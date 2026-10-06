"""approve-bead as admind's only way to read and decide a btq approval (relay spec R5, R6, R12, R23).

admind never writes bead metadata and imports no btq code: it runs `approve-bead <bead> --json` to read a
bead (read only, under btq's per-bead lock) and `approve-bead <bead> --as=NAME --yes --expect-digest=FULL
--via=marmot --via-ref=REF` to decide it. Each run is a child in its own process group, with its output
capped and its time bounded; on every exit path the group is killed and the child reaped (`reap`) before
anything is settled. The outcome of a decision is never parsed from its output: it is read back with
`--json` and compared with the persisted attempt (`settle`).
"""

import asyncio
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
BEAD_ID = re.compile(r"[a-z0-9]{1,16}-[a-z0-9.]{1,32}")
DIGEST = re.compile(r"[0-9a-f]{64}")
PINNED_DIGEST_LINE = re.compile(r"^(\s*pinned digest \(recomputed\): )([0-9a-f]{12})[0-9a-f]{52}$")
READOUT_KEYS = frozenset({"title", "description", "ask"})


class Link(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    doc: int
    url: str


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
    links: list[Link]
    readout: dict[str, list[str]]


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


async def _read(stream: asyncio.StreamReader | None, cap: int) -> bytes:
    out = bytearray()
    if stream is not None:
        while chunk := await stream.read(reap.READ_CHUNK):
            out += chunk
            if len(out) > cap:
                raise BtqError("bad output")
    return bytes(out)


async def _collect(proc: asyncio.subprocess.Process, cap: int) -> tuple[bytes, bytes]:
    """Read stdout (up to `cap`) and stderr (up to MAX_OUTPUT) together, so neither pipe can fill and
    stall the child, then wait for it. Either over its cap stops both reads at once."""
    tasks = [asyncio.ensure_future(_read(proc.stdout, cap)),
             asyncio.ensure_future(_read(proc.stderr, MAX_OUTPUT))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:
            if task.done() and task.exception() is not None:
                task.result()
        out, err = tasks[0].result(), tasks[1].result()
        await proc.wait()
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
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True)
        except OSError:
            raise BtqError("unavailable") from None
        try:
            if on_launch is not None:
                on_launch()
            try:
                out, err = await asyncio.wait_for(_collect(proc, cap), timeout)
            except TimeoutError:
                raise BtqError("timed out") from None
        finally:
            await reap.reap_shielded(proc, "approve-bead", seconds=REAP_SECONDS)
        return proc.returncode if proc.returncode is not None else -1, out, err

    async def read(self, bead: str, on_launch: Callable[[], None] | None = None) -> Readout | Busy:
        """`approve-bead <bead> --json`: the readout, or Busy. Anything else is a BtqError."""
        code, out, _ = await self._run([str(self.binary), bead, "--json"], READ_SECONDS, MAX_READ_OUTPUT,
                                       on_launch)
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
        return r

    async def decide(self, a: Attempt, bead: str,
                     on_launch: Callable[[], None] | None = None) -> tuple[int, str]:
        """Run the decision: (exit status, the first stderr line, redacted, at most MAX_LINE characters)."""
        code, _, err = await self._run(decision_argv(self.binary, a, bead), DECIDE_SECONDS, MAX_OUTPUT,
                                       on_launch)
        lines = err.decode("utf-8", "replace").splitlines()
        return code, redact(redact(lines[0].strip())[:MAX_LINE]) if lines else ""


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


def postable(r: Readout) -> str | None:
    """Why the bead can't be posted as an approval ask (spec §8 "Post", step 2), or None. The gaps go
    back to the poster, not to the operator (R4)."""
    gaps = "; ".join(r.gaps)
    if r.digest is None or not DIGEST.fullmatch(r.digest):
        return "the bead's ask is malformed: approve-bead computed no digest" + (f" ({gaps})" if gaps else "")
    if r.status != "open":
        return f"the bead is {r.status}, not open"
    if not r.kind_approval:
        return "the bead is not a kind:approval bead"
    if r.gaps:
        return f"the bead is not approvable as written; send it back for grooming: {gaps}"
    if r.design_review_valid is False:
        return "the bead's design_review is not in valid two-LLM format"
    if r.posted_digest is not None and r.posted_digest != r.digest:
        return (f"the bead's ask changed after it was posted (posted {r.posted_digest[:12]}, now "
                f"{r.digest[:12]}); re-post it")
    if r.decided:
        return "the bead already holds a decision"
    return None
