"""approve-bead as admind's only way to read and decide a btq approval (relay spec R5, R6, R12, R23).

admind never writes bead metadata and imports no btq code: it runs `approve-bead <bead> --json
--gate-preview` to read a bead (read only, under btq's per-bead lock; btq-ryfxf's preview is btq's own gate
run on a simulated approval) and `approve-bead <bead> --as=NAME --yes --expect-digest=FULL
--via=marmot --via-ref=REF` to decide it. Each run is a child in its own process group, with its output
capped and its time bounded; on every exit path the group is killed and the child reaped (`reap`) before
anything is settled. The outcome of a decision is never parsed from its output: it is read back with
`--json` and compared with the persisted attempt (`settle`).
"""

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.admind import reap
from heterodyne.admind.redact import redact

READ_SECONDS, DECIDE_SECONDS, MAX_OUTPUT, MAX_READ_OUTPUT = 60, 90, 1 << 20, 8 << 20
REAP_SECONDS = 5.0
BUSY_RETRIES, BUSY_WAIT = 3, 20.0   # a busy read-back is retried 3 times, 20 s apart (spec §7)
MAX_LINE = 300                      # the quoted stderr line (R12)
QUOTE_TRIES = 4
UNQUOTABLE = "(a line that could not be shortened safely)"
BEAD_ID = re.compile(r"[a-z0-9]{1,16}-[a-z0-9.]{1,32}")
DIGEST = re.compile(r"[0-9a-f]{64}")
PINNED_DIGEST_LINE = re.compile(r"^(\s*pinned digest \(recomputed\): )([0-9a-f]{12})[0-9a-f]{52}$")
READOUT_KEYS = frozenset({"title", "description", "ask"})


class Link(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    doc: int
    url: str


PURPOSES = (None, "design", "execution")


class GatePreview(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """btq-ryfxf: btq's `approval_valid` on the bead as approved now, and the lines it printed."""
    valid: bool
    reasons: list[str]


class Readout(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Task 1's `--json`, field for field."""
    format: Literal[1]
    busy: bool
    id: str
    status: str
    labels: list[str]
    kind_approval: bool
    digest: str | None
    posted_digest: str | None
    gaps: list[str]
    design_review_valid: bool | None
    adr_revision: str | None
    approvers: list[str]
    decision: str | None
    approved_by: str | None
    approved_digest: str | None
    denied_by: str | None
    denied_digest: str | None
    via: str | None
    via_ref: str | None
    decided: bool
    gate_valid: bool | None
    gate_reasons: list[str]
    # btq-ryfxf: no default, so an approve-bead without `--gate-preview` fails to decode (fail closed)
    gate_preview: GatePreview | None
    links: list[Link]
    readout: dict[str, list[str]]
    # cxmkf K17: the bead's `ask.purpose`, digest-bound; absent from an approve-bead that predates it.
    # admind renders the card label from it and nothing else; any value but these two is a gap.
    purpose: str | None = None


class Busy(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    format: Literal[1]
    busy: Literal[True]


class BtqError(Exception):
    """approve-bead could not be used. `word` is fixed: "unavailable", "timed out" or "bad output"."""

    def __init__(self, word: str) -> None:
        super().__init__(word)
        self.word = word


@dataclass(frozen=True)
class Attempt:
    ask_id: str
    message_id: str
    action: Literal["approve", "deny"]
    operator: str
    ref: str
    digest: str
    note: str | None


Settled = Literal["recorded", "untouched", "blocked"]


@dataclass(frozen=True)
class MaintenanceHold:
    """The enrolled child proved this invocation issued no native mutation."""


def maintenance_hold(code: int, out: bytes) -> bool:
    """Recognize only the closed, bounded pre-effect protocol, never a generic exit 75."""
    if code != 75 or len(out) > 256:
        return False

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    try:
        value = json.loads(out.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError):
        return False
    return value == {"schema": "maintenance-hold.v1", "effect": "not-issued", "reason": "maintenance"}


async def _read(stream: asyncio.StreamReader | None, cap: int) -> bytes:
    out = bytearray()
    if stream is not None:
        while chunk := await stream.read(reap.READ_CHUNK):
            out += chunk
            if len(out) > cap:
                raise BtqError("bad output")
    return bytes(out)


async def _collect(child: reap.Child, cap: int) -> tuple[bytes, bytes]:
    """Connect the pipes, then read stdout (up to `cap`) and stderr (up to MAX_OUTPUT) together, so neither
    pipe can fill and stall the child, then wait for it. Either over its cap stops both reads at once."""
    await child.connect()
    tasks = [asyncio.ensure_future(_read(child.stdout, cap)),
             asyncio.ensure_future(_read(child.stderr, MAX_OUTPUT))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:
            if task.done() and task.exception() is not None:
                task.result()
        out, err = tasks[0].result(), tasks[1].result()
        await child.wait()
        return out, err
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.wait(tasks)


class ApproveBead:
    def __init__(self, binary: Path) -> None:
        self.binary = binary

    async def _run(self, argv: list[str], timeout: float, cap: int,
                   on_launch: Callable[[], None] | None) -> tuple[int, bytes, bytes]:
        """Run approve-bead, launched with no await since the caller's last check (R11). `on_launch` is
        called once a child may exist: from then on every exit reaps its group, and a decision is read back.
        Popen's own OSError means there is none; anything else it raises can't rule one out."""
        try:
            child = reap.spawn(argv)
        except OSError:
            raise BtqError("unavailable") from None
        except BaseException:
            if on_launch is not None:
                on_launch()
            raise
        try:
            if on_launch is not None:
                on_launch()
            try:
                out, err = await asyncio.wait_for(_collect(child, cap), timeout)
            except TimeoutError:
                raise BtqError("timed out") from None
            except OSError:
                raise BtqError("unavailable") from None     # connecting the pipes failed: the child is reaped
        finally:
            await reap.reap_shielded(child, "approve-bead", seconds=REAP_SECONDS)
        return child.returncode if child.returncode is not None else -1, out, err

    async def read(self, bead: str, on_launch: Callable[[], None] | None = None) -> Readout | Busy:
        """`approve-bead <bead> --json --gate-preview`: the readout, or Busy. Anything else is a BtqError,
        a preview that is null for an open, undecided bead, or present for any other, included."""
        code, out, _ = await self._run([str(self.binary), bead, "--json", "--gate-preview"], READ_SECONDS,
                                       MAX_READ_OUTPUT, on_launch)
        try:
            if code == 4:
                return msgspec.json.decode(out, type=Busy)
            if code != 0:
                raise BtqError("unavailable")
            r = msgspec.json.decode(out, type=Readout)
        except msgspec.DecodeError:
            raise BtqError("bad output") from None
        if r.busy or r.id != bead or frozenset(r.readout) != READOUT_KEYS:
            raise BtqError("bad output")
        if (r.gate_preview is None) == (r.status == "open" and not r.decided):
            raise BtqError("bad output")
        return r

    async def decide(self, a: Attempt, bead: str,
                     on_launch: Callable[[], None] | None = None) -> tuple[int, str] | MaintenanceHold:
        """Return a closed pre-effect MaintenanceHold, or (exit status, redacted first stderr line)."""
        code, out, err = await self._run(decision_argv(self.binary, a, bead), DECIDE_SECONDS, MAX_OUTPUT,
                                       on_launch)
        if maintenance_hold(code, out):
            return MaintenanceHold()
        lines = err.decode("utf-8", "replace").splitlines()
        return code, quoted(lines[0].strip()) if lines else ""


def quoted(line: str) -> str:
    """`line` redacted, in at most MAX_LINE characters (R12). Cutting can turn a secret's tail into a match
    whose replacement is longer than what it replaced, so the cut is redacted again, until it fits; a line
    that still doesn't fit after QUOTE_TRIES cuts is not quoted at all."""
    text = redact(line)
    for _ in range(QUOTE_TRIES):
        if len(text) <= MAX_LINE:
            return text
        text = redact(text[:MAX_LINE])
    return UNQUOTABLE


def decision_argv(binary: Path, a: Attempt, bead: str) -> list[str]:
    """Every value as one `--flag=value` argument, so none can become an option (R19)."""
    argv = [str(binary), bead, f"--as={a.operator}", "--yes", f"--expect-digest={a.digest}", "--via=marmot",
            f"--via-ref={a.ref}"]
    if a.action == "deny":
        argv += ["--deny", f"--note={a.note or ''}"]
    return argv


async def read_back(ab: ApproveBead, bead: str) -> Readout | None:
    """The read-back after a decision (R12): a readout, or None when it can't be had (a failure, or still
    busy after BUSY_RETRIES more tries): the outcome is then `uncertain`."""
    for tries in range(BUSY_RETRIES + 1):
        try:
            r = await ab.read(bead)
        except BtqError:
            return None
        if isinstance(r, Readout):
            return r
        if tries < BUSY_RETRIES:
            await asyncio.sleep(BUSY_WAIT)
    return None


def settle(r: Readout, a: Attempt) -> Settled:
    """The read-back against the persisted attempt (R12): the attempt's own decision, nothing at all, or
    anything else."""
    if a.action == "approve":
        by, digest = r.approved_by, r.approved_digest
    else:
        by, digest = r.denied_by, r.denied_digest
    if (r.status == "closed" and r.decision == a.action and by == a.operator and digest == a.digest
            and r.via_ref == a.ref):
        return "recorded"
    if r.status == "open" and not r.decided:
        return "untouched"
    return "blocked"


def postable(r: Readout) -> tuple[str, str] | None:
    """Why the bead can't be posted as an approval ask (spec §8 "Post", step 2), as (the refusal, its
    audit reason), or None. The gaps and btq's gate reasons go back to the poster, not to the operator
    (R4); the caller redacts the refusal."""
    why = _unpostable(r)
    if why is not None:
        return why, "not postable"
    if r.gate_preview is None:
        return "approve-bead gave no gate preview for this bead; try again", "gate"
    if not r.gate_preview.valid:
        return ("btq's gate would reject this approval once approved; fix the bead and post it again: "
                + gate_reasons(r)), "gate"
    return None


def gate_reasons(r: Readout) -> str:
    """The preview's reasons, each without btq's `btq: approval <bead> rejected: ` prefix, unredacted."""
    prefix = f"btq: approval {r.id} rejected: "
    return "; ".join(line.removeprefix(prefix) for line in (r.gate_preview.reasons if r.gate_preview else []))


def _unpostable(r: Readout) -> str | None:
    gaps = "; ".join(r.gaps)
    if r.digest is None or not DIGEST.fullmatch(r.digest):
        return "the bead's ask is malformed: approve-bead computed no digest" + (f" ({gaps})" if gaps else "")
    if r.status != "open":
        return f"the bead is {r.status}, not open"
    if not r.kind_approval:
        return "the bead is not a kind:approval bead"
    if r.gaps:
        return f"the bead is not approvable as written; send it back for grooming: {gaps}"
    if r.purpose not in PURPOSES:       # the value itself is not echoed
        return ("the bead is not approvable as written; send it back for grooming: its purpose must be "
                "design or execution")
    if r.design_review_valid is False:
        return "the bead's design_review is not in valid two-LLM format"
    if r.posted_digest is not None and r.posted_digest != r.digest:
        return (f"the bead's ask changed after it was posted (posted {r.posted_digest[:12]}, now "
                f"{r.digest[:12]}); re-post it")
    if r.decided:
        return "the bead already holds a decision"
    return None
