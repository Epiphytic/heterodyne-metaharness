"""Relay plan Task 3: approval asks, and `!approve` / `!deny` through approve-bead, pinned to the shown digest
(relay spec R5-R12, R21-R23, R25, R26; §6, §8).

approve-bead is always the fake (tests/fakes/fake_approve_bead.py) over a bead file under tmp_path; nothing
here runs the real approve-bead or btq, or touches a real bead. Waits are bounded and checked; a child is
checked by its own PID.
"""

import asyncio
import json
import os
import signal
import stat
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import msgspec
import pytest
from admind_asks_fixture import approve_bead_wrapper, bead_db, btq_log, decisions, sent_mid, two_operators
from admind_waits import lock_waiters, stays, wait_until
from fakes.fake_wn_agent import ACCOUNT
from fakes.settings import OPERATOR_HEX, SECOND_HEX, operator
from test_admind_asks import NONE, audited, joined, posted, row, via_main
from test_admind_daemon import Harness, needs_tmux, run_with

from heterodyne.admind import approvals, asks, chunk, cli, commands, reap, summarize
from heterodyne.admind.approvals import ApproveBead, Attempt, BtqError, Busy, Readout
from heterodyne.admind.audit import ref_id
from heterodyne.admind.daemon import (
    ASK_BUSY,
    ASK_LATCHED,
    ASK_NO_APPROVALS,
    ASK_REDACTED,
    ASK_TOO_MANY,
    RECONCILE_RESTARTED,
    RECONCILE_UNVERIFIED,
    RESTARTED_NOTICE,
)
from heterodyne.admind.redact import redact
from heterodyne.admind.store import AskRow, Store

BEAD, OTHER = "btq-ab12c", "btq-cd34e"
D = "1a2b3c4d5e6f" + "0123456789abcdef" * 3 + "0123"
D2 = "9f8e7d6c5b4a" + "fedcba9876543210" * 3 + "fedc"
D12 = D[:12]
TOKEN = "ghp_" + "A" * 36
MID = "ab" * 32
URL = "https://github.com/owner/repo/blob/0123456/docs/adr/0002-sandbox.md"
TITLE = ["title:", "  │ Approve the plan 4 sandbox runtime"]
REF = ["    - ref docs/adr/0002-sandbox.md  [--doc 1]", "        resolved: docs/adr/0002-sandbox.md"]
ASK = ["  effect:", "    - │ wsd intake runs under OpenShell", "  linked beads:", "    - btq-zz9x1 (open)",
       "  refs:", *REF, "    - ref notes/plan.md  [--doc 2]", f"  pinned digest (recomputed): {D}"]
LINKED = ["  effect:", "    - │ wsd intake runs under OpenShell", "  linked beads:", "    - btq-zz9x1 (open)",
          "  refs:", *REF, f"        link: {URL}", "    - ref notes/plan.md  [--doc 2]",
          "        (no forge link; read it on the host with approve-bead --doc 2)",
          f"  pinned digest (recomputed): {D12}"]
DESCRIPTION = ["description:", "  │ Plan 4 needs a sandbox that runs on macOS and Linux.",
               "  │ OpenShell does; bubblewrap does not."]
LINES = {"title": TITLE, "ask": ASK, "description": DESCRIPTION}
LINKS = [{"doc": 1, "url": URL}]
FIELDS: dict[str, Any] = {
    "format": 1, "busy": False, "id": BEAD, "status": "open", "labels": ["kind:approval"],
    "kind_approval": True, "digest": D, "posted_digest": None, "gaps": [], "design_review_valid": True,
    "adr_revision": None, "approvers": ["op"], "decision": None, "approved_by": None, "approved_digest": None,
    "denied_by": None, "denied_digest": None, "via": None, "via_ref": None, "decided": False,
    "gate_valid": None, "gate_reasons": [], "links": LINKS, "readout": LINES}
APPROVE = Attempt("k7m2", MID, "approve", "op", "marmot:" + ref_id(MID), D, None)
DENY = Attempt("k7m2", MID, "deny", "op", "marmot:" + ref_id(MID), D, "too broad")
OPS = {"operators": (operator("op", OPERATOR_HEX), operator("llctest", SECOND_HEX))}


def readout(**kw: Any) -> Readout:
    return msgspec.convert({**FIELDS, **kw}, Readout)


def bead(**kw: Any) -> dict[str, Any]:
    """A fake bead record: an approvable kind:approval bead with the readout above, approver `op`."""
    return {"digest": D, "approvers": ["op"], "readout": LINES, "links": LINKS, "design_review_valid": True,
            **kw}


def card_row(**kw: Any) -> AskRow:
    return row(**{"kind": "approval", "body": "", "bead": BEAD, "digest": D, **kw})


def edit(tmp_path: Path, bead_id: str = BEAD, **fields: Any) -> None:
    """Change the fake bead between steps (the fake reads its file on every run)."""
    db = tmp_path / "btq.json"
    beads = json.loads(db.read_text())
    beads[bead_id].update(fields)
    db.write_text(json.dumps(beads))


def undecide(tmp_path: Path, bead_id: str = BEAD) -> None:
    """Undo a decision on the fake bead: its decision keys removed (their presence is the decision) and the
    bead open again."""
    db = tmp_path / "btq.json"
    beads = json.loads(db.read_text())
    record = beads[bead_id]
    prefixes = ("approved_", "denied_")
    for key in [k for k in record if k in ("decision", "via_ref", "via") or k.startswith(prefixes)]:
        del record[key]
    record["status"] = "open"
    db.write_text(json.dumps(beads))


def bead_state(tmp_path: Path, bead_id: str = BEAD) -> dict[str, Any]:
    return json.loads((tmp_path / "btq.json").read_text())[bead_id]


def pids(tmp_path: Path) -> list[int]:
    path = tmp_path / "pids"
    return [int(p) for p in path.read_text().split()] if path.exists() else []


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def kill_quietly(path: Path) -> None:
    if path.exists():
        try:
            os.kill(int(path.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


def fake(tmp_path: Path, **kw: Any) -> ApproveBead:
    bead_db(tmp_path, **{BEAD: bead(**kw)})
    return ApproveBead(approve_bead_wrapper(tmp_path))


# --- Readout -------------------------------------------------------------------------------------
def test_readout_decoding() -> None:
    whole = {**FIELDS, "digest": None}
    assert msgspec.json.decode(json.dumps(whole), type=Readout).digest is None
    for bad in ({**FIELDS, "surprise": 1}, {k: v for k, v in FIELDS.items() if k != "gate_reasons"},
                {**FIELDS, "format": 2}, {**FIELDS, "links": [{"doc": 1, "url": URL, "x": 1}]}):
        with pytest.raises(msgspec.ValidationError):
            msgspec.json.decode(json.dumps(bad), type=Readout)
    assert msgspec.json.decode(b'{"format": 1, "busy": true}', type=Busy) == Busy(1, True)
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"format": 1, "busy": false}', type=Busy)


def test_read_returns_the_readout_or_busy(tmp_path: Path) -> None:
    async def scenario() -> None:
        ab = fake(tmp_path)
        r = await ab.read(BEAD)
        assert isinstance(r, Readout) and r.digest == D and r.readout == LINES and r.approvers == ["op"]
        edit(tmp_path, busy=True)
        assert await ab.read(BEAD) == Busy(1, True)
        edit(tmp_path, busy=False, readout={"title": [], "ask": []})       # not the three sections
        with pytest.raises(BtqError, match="bad output"):
            await ab.read(BEAD)
        edit(tmp_path, readout=LINES, fail_read=True)
        with pytest.raises(BtqError, match="unavailable"):
            await ab.read(BEAD)
        with pytest.raises(BtqError, match="unavailable"):
            await ab.read("btq-nope1")
        missing = ApproveBead(tmp_path / "no-such-tool")
        with pytest.raises(BtqError, match="unavailable"):
            await missing.read(BEAD)
    asyncio.run(scenario())


def test_decision_argv(tmp_path: Path) -> None:
    async def scenario() -> None:
        ab = fake(tmp_path)
        assert await ab.decide(APPROVE, BEAD) == (0, "")
        assert btq_log(tmp_path)[-1] == [BEAD, "--as=op", "--yes", f"--expect-digest={D}", "--via=marmot",
                                         f"--via-ref={APPROVE.ref}"]
        undecide(tmp_path)
        assert await ab.decide(DENY, BEAD) == (0, "")
        assert btq_log(tmp_path)[-1] == [BEAD, "--as=op", "--yes", f"--expect-digest={D}", "--via=marmot",
                                         f"--via-ref={DENY.ref}", "--deny", "--note=too broad"]
        undecide(tmp_path)
        code, line = await ab.decide(Attempt("k7m2", MID, "approve", "--json", APPROVE.ref, D, None), BEAD)
        assert code == 1 and line.startswith("Approver must be one of")        # one argument, not an option
    asyncio.run(scenario())


def test_decide_quotes_one_redacted_line(tmp_path: Path) -> None:
    async def scenario() -> None:
        ab = fake(tmp_path, decide="fail")
        assert await ab.decide(APPROVE, BEAD) == (1, "refused: something is wrong; nothing written.")
        edit(tmp_path, approvers=[TOKEN + "x" * 400])
        code, line = await ab.decide(APPROVE, BEAD)
        assert code == 1 and TOKEN not in line and "<redacted GitHub token>" in line
        edit(tmp_path, approvers=["y" * 400])
        code, line = await ab.decide(APPROVE, BEAD)
        assert code == 1 and len(line) == approvals.MAX_LINE and line.startswith("Approver must be one of")
    asyncio.run(scenario())


def test_quoted_line_stays_within_the_cap() -> None:
    """r2-NB: a cut can complete a secret's pattern, whose replacement is longer: it is cut and redacted
    again until it fits."""
    line = "x" * 279 + " " + "AKIA" + "Z" * 17
    assert len(redact(redact(line)[:approvals.MAX_LINE])) > approvals.MAX_LINE      # the case itself
    quoted = approvals.quoted(line)
    assert len(quoted) <= approvals.MAX_LINE and redact(quoted) == quoted and "AKIA" + "Z" * 16 not in quoted
    assert quoted.startswith("x" * 279 + " ")


def test_quoted_line_that_never_fits_is_not_quoted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "redact", lambda text: text + "!")     # every pass grows it
    assert approvals.quoted("y" * 400) == approvals.UNQUOTABLE
    assert approvals.quoted("y" * 10) == "y" * 10 + "!"


def test_read_output_cap(tmp_path: Path) -> None:
    """A readout past MAX_OUTPUT is still read (the read cap is MAX_READ_OUTPUT); one past that is not."""
    async def scenario() -> None:
        ab = fake(tmp_path, readout={**LINES, "description": ["x" * 1000] * 3000})
        assert approvals.MAX_OUTPUT < 3_000_000 < approvals.MAX_READ_OUTPUT
        r = await ab.read(BEAD)
        assert isinstance(r, Readout) and len(r.readout["description"]) == 3000
        edit(tmp_path, readout={**LINES, "description": ["x" * 1000] * 9000})
        assert approvals.MAX_READ_OUTPUT < 9_000_000
        with pytest.raises(BtqError, match="bad output"):
            await ab.read(BEAD)
        assert len(pids(tmp_path)) == 2 and all(gone(p) for p in pids(tmp_path))
    asyncio.run(scenario())


# --- reaping (R23) -------------------------------------------------------------------------------
def test_timeout_kills_and_reaps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "READ_SECONDS", 1)
    monkeypatch.setattr(approvals, "DECIDE_SECONDS", 1)

    async def scenario() -> None:
        ab = fake(tmp_path, read="hang", decide="hang")
        with pytest.raises(BtqError, match="timed out"):
            await ab.read(BEAD)
        with pytest.raises(BtqError, match="timed out"):
            await ab.decide(APPROVE, BEAD)
        assert len(pids(tmp_path)) == 2 and all(gone(p) for p in pids(tmp_path))
    asyncio.run(scenario())


def test_cancel_kills_and_reaps(tmp_path: Path) -> None:
    async def scenario() -> None:
        ab = fake(tmp_path, read="hang")
        task = asyncio.create_task(ab.read(BEAD))
        await wait_until(lambda: len(pids(tmp_path)) == 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, approvals.REAP_SECONDS + 1)
        assert gone(pids(tmp_path)[0])
    asyncio.run(scenario())


def test_overflow_kills_and_reaps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "REAP_SECONDS", 60)     # the drain ends the reap, not its bound (r4-1)

    async def scenario() -> None:
        ab = fake(tmp_path, read="big", decide="big")
        for run in (lambda: ab.read(BEAD), lambda: ab.decide(APPROVE, BEAD)):
            started = time.monotonic()
            with pytest.raises(BtqError, match="bad output"):
                await asyncio.wait_for(run(), 30)
            assert time.monotonic() - started < 10
        assert len(pids(tmp_path)) == 2 and all(gone(p) for p in pids(tmp_path))
    asyncio.run(scenario())


def stall_connect(monkeypatch: pytest.MonkeyPatch, loop: asyncio.AbstractEventLoop) -> asyncio.Event:
    """Make connecting a child's pipes stall for good, as a stuck native creation would: the returned event
    is set once one is stalled, and nothing ever releases it."""
    stalled, never = asyncio.Event(), asyncio.Event()

    async def held(*args: Any, **kw: Any) -> Any:
        stalled.set()
        await never.wait()
    monkeypatch.setattr(loop, "connect_read_pipe", held)
    monkeypatch.setattr(loop, "connect_write_pipe", held)
    return stalled


def test_cancel_during_a_stalled_creation_reaps_the_group(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """r2-B3: a cancellation while the pipes are being connected, a stall never released: the read still
    ends within REAP_SECONDS, and the child and a descendant it already started in its group are gone."""
    monkeypatch.setattr(approvals, "REAP_SECONDS", 1)

    async def scenario() -> None:
        ab = fake(tmp_path, read="descendant")
        stalled = stall_connect(monkeypatch, asyncio.get_running_loop())
        task = asyncio.create_task(ab.read(BEAD))
        await wait_until(lambda: stalled.is_set() and (tmp_path / "descendant.pid").exists(), 20)
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1 + 1)
        assert time.monotonic() - started < 1 + 1
        child, grandchild = pids(tmp_path)[0], int((tmp_path / "descendant.pid").read_text())
        await wait_until(lambda: gone(child) and gone(grandchild), 5)
    try:
        asyncio.run(scenario())
    finally:
        kill_quietly(tmp_path / "descendant.pid")


def test_a_stalled_creation_times_out_and_reaps_the_group(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The run's own timeout covers connecting the pipes too."""
    monkeypatch.setattr(approvals, "READ_SECONDS", 1)
    monkeypatch.setattr(approvals, "REAP_SECONDS", 1)

    async def scenario() -> None:
        ab = fake(tmp_path, read="descendant")
        stall_connect(monkeypatch, asyncio.get_running_loop())
        with pytest.raises(BtqError, match="timed out"):
            await asyncio.wait_for(ab.read(BEAD), 1 + 1 + 1)
        await wait_until(lambda: (tmp_path / "descendant.pid").exists(), 20)
        child, grandchild = pids(tmp_path)[0], int((tmp_path / "descendant.pid").read_text())
        await wait_until(lambda: gone(child) and gone(grandchild), 5)
    try:
        asyncio.run(scenario())
    finally:
        kill_quietly(tmp_path / "descendant.pid")


def fail_connect(monkeypatch: pytest.MonkeyPatch, pid_file: Path, decision: bool = False) -> None:
    """Make connecting the pipes of a run (only a decision's, if `decision`) fail with OSError once the
    descendant `pid_file` names exists: a failure after the child, and more of its group, exist."""
    connect = reap.Child.connect

    async def failing(self: reap.Child) -> None:
        args = self.popen.args
        assert isinstance(args, list)
        if decision and "--yes" not in args:
            return await connect(self)
        await wait_until(pid_file.exists, 20)
        raise OSError("injected")
    monkeypatch.setattr(reap.Child, "connect", failing)


def test_failure_after_launch_reaps_the_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r2-B2: connecting the pipes fails with the child and a descendant in its group running: both are
    killed, and the failure is approve-bead's fixed word."""
    pid_file = tmp_path / "descendant.pid"
    fail_connect(monkeypatch, pid_file)

    async def scenario() -> None:
        ab = fake(tmp_path, read="descendant")
        with pytest.raises(BtqError, match="unavailable"):
            await asyncio.wait_for(ab.read(BEAD), approvals.REAP_SECONDS + 20)
        child, grandchild = pids(tmp_path)[0], int(pid_file.read_text())
        await wait_until(lambda: gone(child) and gone(grandchild), 5)
    try:
        asyncio.run(scenario())
    finally:
        kill_quietly(pid_file)


DESCENDANT = """\
import os, sys, time
pid = os.fork()
if pid == 0:
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    time.sleep(60)
    os._exit(0)
with open(sys.argv[1] + ".tmp", "w") as f:
    f.write(str(pid))
os.replace(sys.argv[1] + ".tmp", sys.argv[1])
time.sleep(600)
"""


def test_summarizer_cancel_during_a_stalled_creation_reaps_the_group(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The summarizer starts its child through the same `reap.spawn`: a stall never released, cancelled."""
    monkeypatch.setattr(summarize, "REAP_SECONDS", 1)
    pid_file = tmp_path / "descendant.pid"

    async def scenario() -> None:
        stalled = stall_connect(monkeypatch, asyncio.get_running_loop())
        argv = [sys.executable, "-c", DESCENDANT, str(pid_file)]
        task = asyncio.create_task(summarize.summarize(argv, tmp_path / "w", "r", timeout=30))
        await wait_until(lambda: stalled.is_set() and pid_file.exists(), 20)
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1 + 1)
        assert time.monotonic() - started < 1 + 1
        grandchild = int(pid_file.read_text())
        await wait_until(lambda: gone(grandchild), 5)
    try:
        asyncio.run(scenario())
    finally:
        kill_quietly(pid_file)


def test_summarizer_failure_after_launch_reaps_the_group(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "descendant.pid"
    fail_connect(monkeypatch, pid_file)

    async def scenario() -> None:
        argv = [sys.executable, "-c", DESCENDANT, str(pid_file)]
        with pytest.raises(summarize.SummaryFailed) as info:
            await asyncio.wait_for(summarize.summarize(argv, tmp_path / "w", "r", timeout=30), 30)
        assert info.value.reason == "not-run"
        grandchild = int(pid_file.read_text())
        await wait_until(lambda: gone(grandchild), 5)
    try:
        asyncio.run(scenario())
    finally:
        kill_quietly(pid_file)


def test_summarizer_stalled_creation_times_out_and_reaps_the_group(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The summarizer's own timeout covers connecting the pipes too: a stall never released ends as
    "timeout", and its group is gone."""
    monkeypatch.setattr(summarize, "REAP_SECONDS", 1)
    pid_file = tmp_path / "descendant.pid"

    async def scenario() -> None:
        stalled = stall_connect(monkeypatch, asyncio.get_running_loop())
        argv = [sys.executable, "-c", DESCENDANT, str(pid_file)]
        with pytest.raises(summarize.SummaryFailed) as info:
            await asyncio.wait_for(summarize.summarize(argv, tmp_path / "w", "r", timeout=1), 20 + 1 + 1)
        assert info.value.reason == "timeout" and stalled.is_set()
        await wait_until(pid_file.exists, 20)
        grandchild = int(pid_file.read_text())
        await wait_until(lambda: gone(grandchild), 5)
    try:
        asyncio.run(scenario())
    finally:
        kill_quietly(pid_file)


@pytest.mark.parametrize("released", [True, False])
def test_cancel_waits_for_the_exit_wait(tmp_path: Path, released: bool) -> None:
    """A cancellation while `reap_shielded` waits for the exit propagates only once that wait has finished
    (`released`) or reached its bound (not released)."""
    async def scenario() -> None:
        child = reap.spawn([sys.executable, "-c", "import time; time.sleep(60)"])
        entered, gate = asyncio.Event(), asyncio.Event()
        finished = False

        async def gated(c: reap.Child) -> None:
            nonlocal finished
            entered.set()
            await gate.wait()
            await c.wait()
            finished = True
        try:
            await child.connect()
            task = asyncio.create_task(reap.reap_shielded(child, "test", seconds=1 if not released else 30,
                                                          wait=gated))
            await asyncio.wait_for(entered.wait(), 10)
            task.cancel()
            await stays(lambda: not task.done(), 0.2)
            if released:
                gate.set()
            started = time.monotonic()
            done, _ = await asyncio.wait({task}, timeout=10)    # not wait_for: it would wait out the shield
            gate.set()
            assert done
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished == released and gone(child.pid)
            if not released:
                assert time.monotonic() - started < 1
        finally:
            kill_quietly_pid(child.pid)
    asyncio.run(scenario())


def kill_quietly_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_cleanup_wait_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    """A child that never seems to exit costs REAP_SECONDS, noted by type, and the result stands."""
    monkeypatch.setattr(summarize, "REAP_SECONDS", 0.5)

    async def forever(child: reap.Child) -> None:
        await asyncio.Event().wait()
    monkeypatch.setattr(summarize, "_wait", forever)

    async def scenario() -> None:
        argv = [sys.executable, "-c", "raise SystemExit(3)"]
        with pytest.raises(summarize.SummaryFailed) as info:
            await asyncio.wait_for(summarize.summarize(argv, tmp_path / "w", "r", timeout=30), 0.5 + 10)
        assert info.value.reason == "failed"
    asyncio.run(scenario())
    assert "summarizer cleanup failed (TimeoutError)" in capsys.readouterr().err


def test_reap_bounded_when_pipe_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "READ_SECONDS", 1)
    monkeypatch.setattr(approvals, "REAP_SECONDS", 1)
    closed: list[str] = []
    close = reap.close_pipes

    def recording(child: reap.Child, what: str) -> None:
        close(child, what)
        pipes = (child.popen.stdout, child.popen.stderr)
        shut = all(t.is_closing() for t in child.transports)
        shut = shut and all(p is not None and p.closed for p in pipes)
        closed.append(what if shut and len(child.transports) == 2 else "open")
    monkeypatch.setattr(reap, "close_pipes", recording)

    async def scenario() -> None:
        ab = fake(tmp_path, read="held")
        started = time.monotonic()
        with pytest.raises(BtqError, match="timed out"):
            await ab.read(BEAD)
        assert time.monotonic() - started < 1 + 1
        assert closed == ["approve-bead"] and gone(pids(tmp_path)[0])
        held = int((tmp_path / "held.pid").read_text())
        assert not gone(held)            # it left the group, so the kill missed it: only the pipe was let go
    try:
        asyncio.run(scenario())
    finally:
        kill_quietly(tmp_path / "held.pid")


def test_second_cancel_during_cleanup_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "REAP_SECONDS", 1)
    killed = asyncio.Event()
    kill = reap.kill

    def recording(child: reap.Child) -> None:
        kill(child)
        killed.set()
    monkeypatch.setattr(reap, "kill", recording)

    async def scenario() -> None:
        ab = fake(tmp_path, read="held")
        task = asyncio.create_task(ab.read(BEAD))
        await wait_until(lambda: (tmp_path / "held.pid").exists())
        task.cancel()
        await asyncio.wait_for(killed.wait(), 10)          # the cleanup has started
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1 + 1)
        assert time.monotonic() - started < 1 + 1 and gone(pids(tmp_path)[0])
    try:
        asyncio.run(scenario())
    finally:
        kill_quietly(tmp_path / "held.pid")


def test_descendant_killed_after_parent_exit(tmp_path: Path) -> None:
    async def scenario() -> None:
        ab = fake(tmp_path, decide="orphan")
        assert await ab.decide(APPROVE, BEAD) == (1, "bd update failed")
        orphan = int((tmp_path / "orphan.pid").read_text())
        await wait_until(lambda: gone(orphan), 5)
        await stays(lambda: bead_state(tmp_path).get("decision") is None, 2.0)
    try:
        asyncio.run(scenario())
    finally:
        kill_quietly(tmp_path / "orphan.pid")


# --- settle and postable -------------------------------------------------------------------------
CLOSED = {"status": "closed", "decided": True, "via": "marmot", "via_ref": APPROVE.ref}
APPROVED = {**CLOSED, "decision": "approve", "approved_by": "op", "approved_digest": D, "gate_valid": True}
DENIED = {**CLOSED, "decision": "deny", "denied_by": "op", "denied_digest": D}


@pytest.mark.parametrize(("fields", "a", "settled"), [
    (APPROVED, APPROVE, "recorded"),
    ({**APPROVED, "gate_valid": False}, APPROVE, "recorded"),       # the gate is reported, not settled on
    (DENIED, DENY, "recorded"),
    ({}, APPROVE, "untouched"),
    ({}, DENY, "untouched"),
    ({**APPROVED, "via_ref": "marmot:id:000000000000"}, APPROVE, "blocked"),
    ({**APPROVED, "via_ref": None, "via": "cli"}, APPROVE, "blocked"),
    ({**APPROVED, "approved_by": "someone-else"}, APPROVE, "blocked"),
    ({**APPROVED, "approved_digest": D2}, APPROVE, "blocked"),
    ({**APPROVED, "status": "open"}, APPROVE, "blocked"),
    ({"decided": True, "decision": "approve", "approved_by": "op"}, APPROVE, "blocked"),     # partial
    ({"decided": True}, APPROVE, "blocked"),
    (DENIED, APPROVE, "blocked"),
    (APPROVED, DENY, "blocked"),
    ({**APPROVED, "decision": "deny"}, APPROVE, "blocked"),
    ({"status": "closed"}, APPROVE, "blocked"),
])
def test_settle(fields: dict[str, Any], a: Attempt, settled: str) -> None:
    assert approvals.settle(readout(**fields), a) == settled


@pytest.mark.parametrize(("fields", "why"), [
    ({"status": "closed"}, "the bead is closed, not open"),
    ({"kind_approval": False}, "not a kind:approval bead"),
    ({"gaps": ["no effect", "no refs"]}, "send it back for grooming: no effect; no refs"),
    ({"digest": None}, "approve-bead computed no digest"),
    ({"digest": None, "gaps": ["ask: missing"]}, "computed no digest (ask: missing)"),
    ({"digest": D.upper()}, "computed no digest"),
    ({"design_review_valid": False}, "design_review is not in valid two-LLM format"),
    ({"posted_digest": D2}, f"posted {D2[:12]}, now {D12}"),
    ({"decided": True}, "already holds a decision"),
])
def test_postable_refuses(fields: dict[str, Any], why: str) -> None:
    refusal = approvals.postable(readout(**fields))
    assert refusal is not None and why in refusal


def test_postable_accepts() -> None:
    assert approvals.postable(readout()) is None
    assert approvals.postable(readout(posted_digest=D, design_review_valid=None)) is None


# --- the card (R8, R21, R25) ---------------------------------------------------------------------
HEAD = "🛂 Approval ask k7m2 · bead btq-ab12c · posted by controller (a local process; unverified)"
ACTIONS = "👍 approve · 👎 deny — react to any part of this card, or reply approve / deny <reason>"
TOP = [HEAD, ACTIONS, f"digest {D12}"]
END = ["", f"Approving records your approval of {BEAD} in btq, as you, via Marmot. Any other reply is a note "
       "for the poster."]


def test_approval_card_is_the_readout_verbatim() -> None:
    """The R30 wording, pinned: the head, the actions, the digest, the readout, the closing line."""
    card = asks.approval_card(card_row(), readout(), 4000)
    assert isinstance(card, asks.ApprovalCard)
    whole = "\n".join([*TOP, *TITLE, "ask:", *LINKED, *DESCRIPTION, *END])
    assert card.card_chunks == [whole] and card.details_chunks == [whole]
    assert D not in whole and whole.splitlines()[:3] == TOP and "!approve" not in whole
    assert asks.approval_title(readout(), BEAD) == "Approve the plan 4 sandbox runtime"
    assert asks.approval_title(readout(readout={"title": ["title:"], "ask": [], "description": []}),
                               BEAD) == BEAD


def test_approval_card_chunks_are_what_is_queued() -> None:
    card = asks.approval_card(card_row(), readout(), 200)
    assert isinstance(card, asks.ApprovalCard) and len(card.card_chunks) > 1
    text = "\n".join([*TOP, *TITLE, "ask:", *LINKED, *DESCRIPTION, *END])
    assert card.card_chunks == chunk.split(text, 200)


def test_approval_card_is_never_shortened() -> None:
    """R8, R15 (revised): past the question card's 40 lines and 3,500 characters, the whole readout is the
    card, in as many chunks as it needs, and the card is its own `!details`."""
    long = ["description:", *(f"  │ line {i} of the plan" for i in range(60)), *(["  │ " + "w" * 900] * 4)]
    r = readout(readout={**LINES, "description": long})
    card = asks.approval_card(card_row(), r, 4000)
    assert isinstance(card, asks.ApprovalCard) and len(card.card_chunks) > 1
    text = "\n".join([*TOP, *TITLE, "ask:", *LINKED, *long, *END])
    assert card.card_chunks == chunk.split(text, 4000) and card.details_chunks == card.card_chunks
    assert "  │ line 59 of the plan" in "".join(card.card_chunks) and "shortened" not in text
    assert len(text.splitlines()) > asks.CARD_LINES and len(text) > asks.CARD_CHARS


def test_approval_card_cap() -> None:
    """R8: a readout over MAX_APPROVAL_CARD characters is refused at post time, after the R21 check."""
    def sized(n: int, extra: str = "") -> Readout:
        fixed = "\n".join([*TITLE, "ask:", *LINKED, "description:", "  │ "])
        line = "  │ " + "w" * (n - len(fixed)) + extra
        return readout(readout={**LINES, "description": ["description:", line]})
    assert asks.MAX_APPROVAL_CARD == 24_000
    at_cap = asks.approval_card(card_row(), sized(asks.MAX_APPROVAL_CARD), 4000)
    assert isinstance(at_cap, asks.ApprovalCard) and len(at_cap.card_chunks) == 7
    assert asks.approval_card(card_row(), sized(asks.MAX_APPROVAL_CARD + 1), 4000) == asks.TOO_LONG
    assert asks.TOO_LONG == "this bead is too long to decide from the phone; decide it at the terminal"
    over_and_secret = sized(asks.MAX_APPROVAL_CARD + 100, " " + TOKEN)
    assert asks.approval_card(card_row(), over_and_secret, 4000) == asks.REDACTED      # R21 is checked first


@pytest.mark.parametrize("line", [
    f"  │ the deploy key is {TOKEN}",
    "  │ " + "ab" * 32,                                         # a 64-hex run outside a digest line
    f"  pinned digest: {D}",                                    # not a digest line, so not cut
    "  │ -----BEGIN PRIVATE KEY-----",                         # no END
])
def test_approval_card_refuses_what_redaction_would_change(line: str) -> None:
    r = readout(readout={**LINES, "description": [*DESCRIPTION, line]})
    assert asks.approval_card(card_row(), r, 4000) == asks.REDACTED


def test_approval_card_refuses_a_chunk_redaction_would_change() -> None:
    """The R21 fixture found by search (again for the R30 layout): the whole text survives redaction; a
    chunk boundary cuts the key so that one chunk holds a run `SECRET_VALUES` matches."""
    key = "AKIA" + "ABCDEFGHIJKLMNOPQRST"
    r = readout(readout={"title": ["title:", "  │ T"], "ask": ["  effect:", "    - │ e"],
                         "description": ["description:", "  │ " + "w" * 124 + " " + key]}, links=[])
    whole = asks.approval_card(card_row(), r, 4000)
    assert isinstance(whole, asks.ApprovalCard) and key in whole.card_chunks[0]
    assert asks.approval_card(card_row(), r, 200) == asks.REDACTED


def test_approval_card_refuses_a_value_split_across_chunks() -> None:
    """The other half of R21: a token a chunk boundary cuts is in no chunk, but the whole text holds it, so
    the card is refused all the same."""
    def with_line(line: str) -> Readout:
        return readout(readout={"title": ["title:", "  │ T"], "ask": ["  effect:", "    - │ e"],
                                "description": ["description:", line]}, links=[])
    stand_in_token = "Z" * len(TOKEN)
    for pad in range(100, 200):
        stand_in = asks.approval_card(card_row(), with_line("  │ " + "w" * pad + " " + stand_in_token), 4000)
        assert isinstance(stand_in, asks.ApprovalCard)
        text = stand_in.card_chunks[0].replace(stand_in_token, TOKEN)
        if all(redact(part) == part for part in chunk.split(text, 200)):
            break
    else:
        raise AssertionError("no padding splits the token")
    assert redact(text) != text
    assert asks.approval_card(card_row(), with_line("  │ " + "w" * pad + " " + TOKEN), 200) == asks.REDACTED


def test_approval_body_round_trip() -> None:
    card = asks.approval_card(card_row(), readout(), 300)
    assert isinstance(card, asks.ApprovalCard)
    body = asks.approval_body(readout(), card)
    assert msgspec.convert(json.loads(body)["readout"], Readout) == readout()
    assert asks.stored_details(card_row(body=body)) == card.details_chunks


# --- commands ------------------------------------------------------------------------------------
def test_attempt_compare_and_set(tmp_path: Path) -> None:
    """begin_attempt needs the ask `open` and marks the inbound message `executing`; close_attempt needs
    both the status and the attempt it was given, and otherwise changes nothing."""
    store = Store(tmp_path / "admind.db")
    try:
        store.insert_ask(card_row(), None)
        assert store.claim_inbound(MID)
        assert store.begin_attempt(APPROVE)
        stored = store.ask("k7m2")
        assert store.inbound_status(MID) == "executing" and stored is not None and stored.status == "deciding"
        other = "cd" * 32
        assert not store.begin_attempt(Attempt("k7m2", other, "approve", "op", "marmot:x", D, None))
        assert store.current_attempt("k7m2") == APPROVE
        for status, attempt in (("open", MID), ("deciding", other)):
            assert not store.close_attempt("k7m2", attempt, expect_status=status, settled="recorded",
                                           exit_status=0, new_status="approved", outcome="x", decided_by="op")
        stored = store.ask("k7m2")
        assert stored is not None and stored.status == "deciding" and store.current_attempt("k7m2") == APPROVE
        assert store.close_attempt("k7m2", MID, expect_status="deciding", settled="recorded", exit_status=0,
                                   new_status="approved", outcome="x", decided_by="op")
        stored = store.ask("k7m2")
        assert stored is not None and stored.status == "approved" and store.current_attempt("k7m2") is None
    finally:
        store.close()


def test_parse_approve_and_deny() -> None:
    """R7 (revised): no arguments are needed, and any given are kept for the card check, which refuses any
    that are not the card's bead (and digest). Only a too-long reason is a usage error."""
    assert commands.parse("!approve") == commands.Command("approve")
    assert commands.parse(f"!approve {BEAD}") == commands.Command("approve", arg=BEAD)
    assert commands.parse(f"!approve {BEAD} {D12.upper()}") == commands.Command("approve", arg=BEAD, rest=D12)
    assert commands.parse(f"!approve {BEAD} {D12} x") == commands.Command("approve", arg=BEAD,
                                                                         rest=f"{D12} x")
    assert commands.parse("!approve BTQ-AB12C") == commands.Command("approve", arg="BTQ-AB12C")
    assert commands.parse("!deny") == commands.Command("deny")
    assert commands.parse("!deny   ") == commands.Command("deny")
    assert commands.parse(f"!deny {BEAD}") == commands.Command("deny", arg=BEAD)
    assert commands.parse(f"!deny {BEAD}  too broad\n for now ") == commands.Command(
        "deny", arg=BEAD, rest="too broad\n for now")
    assert commands.parse(f"!deny {BEAD} leaks {TOKEN}") == commands.Command(
        "deny", arg=BEAD, rest="leaks <redacted GitHub token>")
    assert commands.parse("!deny too broad") == commands.Command("deny", arg="too", rest="broad")
    assert commands.parse(f"!deny {BEAD} " + "x" * 1000) is not None
    with pytest.raises(commands.CommandError, match="at most 1,000"):
        commands.parse(f"!deny {BEAD} " + "x" * 1001)
    assert "!approve · !deny (as a reply to an approval card)" in commands.HELP


# --- integration ---------------------------------------------------------------------------------
APPROVAL = asks.AskPost("approval", bead=BEAD)


def go(tmp_path: Path, scenario: Callable[[Harness], Awaitable[None]],
       beads: dict[str, dict[str, Any]] | None = None, *,
       before_store: Callable[[Harness], Any] | None = None, fresh: bool = True, **settings: Any) -> Harness:
    """A run with the fake approve-bead and operators op and llctest, both confirmed in a three-member
    group (r1-12). `fresh=False` keeps the bead file of an earlier run (a restart)."""
    if fresh:
        bead_db(tmp_path, **(beads or {BEAD: bead()}))
    overrides = {"approve_bead": approve_bead_wrapper(tmp_path), **OPS, **settings}
    h = run_with(tmp_path, scenario, before=two_operators, settings_overrides=overrides,
                 before_store=before_store)
    h.store.close()
    return h


async def card(h: Harness, req: asks.AskPost = APPROVAL, *, join: bool = True) -> tuple[str, str]:
    """Post an approval ask and wait until its whole card is sent: (ask ID, first chunk's message ID)."""
    if join:
        await joined(h)
    ask_id = await posted(h, req)
    await wait_until(lambda: h.store.card_delivered(ask_id))
    mid = sent_mid(h, f"ask:{ask_id}:0")
    assert mid is not None
    return ask_id, mid


async def send(h: Harness, text: str, reply_to: str | None, sender: str = OPERATOR_HEX) -> str:
    h.seq += 1
    mid = f"{h.seq:064x}"
    await h.fake.push_event(h.fake.message_event(text, sender, mid, reply_to=reply_to))
    return mid


def queued(h: Harness, mid: str, tag: str = "ask") -> str:
    rows = h.store.db.execute("SELECT text FROM outbox WHERE key LIKE ? ORDER BY seq",
                              (f"{tag}:{mid}:%",)).fetchall()
    return "".join(r[0] for r in rows)


def row_text(h: Harness, key: str) -> str | None:
    r = h.store.db.execute("SELECT text FROM outbox WHERE key = ?", (key,)).fetchone()
    return None if r is None else str(r[0])


async def settled(h: Harness, mid: str) -> None:
    await wait_until(lambda: h.store.inbound_status(mid) in ("done", "dropped"), 30)


async def say(h: Harness, text: str, reply_to: str | None, sender: str = OPERATOR_HEX,
              tag: str = "ask") -> str:
    """Send a message, wait until it is settled, and return the reply admind queued for it."""
    mid = await send(h, text, reply_to, sender)
    await settled(h, mid)
    return queued(h, mid, tag)


def ask_status(h: Harness, ask_id: str) -> str:
    r = h.store.ask(ask_id)
    assert r is not None
    return r.status


def attempts(h: Harness, ask_id: str) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in h.store.db.execute(
        "SELECT message_id, action, operator, ref, digest, note, exit_status, settled FROM ask_attempts "
        "WHERE ask_id = ? ORDER BY rowid", (ask_id,)).fetchall()]


def current(h: Harness, ask_id: str) -> str | None:
    return h.store.db.execute("SELECT attempt FROM asks WHERE ask_id = ?", (ask_id,)).fetchone()[0]


def reads(h: Harness) -> int:
    return sum("--json" in argv for argv in btq_log(h.settings.workdir))


def approve(ask_digest: str = D12, bead_id: str = BEAD) -> str:
    return f"!approve {bead_id} {ask_digest}"


def go_file(h: Harness, name: str = "gate") -> Path:
    return h.settings.workdir / f"{name}.go"


async def waiting(h: Harness, name: str = "gate") -> None:
    await wait_until(lambda: (h.settings.workdir / f"{name}.waiting").exists(), 20)


@needs_tmux
def test_approve_happy_path(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        stored = h.store.ask(ask_id)
        assert stored is not None and stored.digest == D and not stored.truncated
        r = msgspec.convert(json.loads(stored.body)["readout"], Readout)
        expected = asks.approval_card(stored, r, h.settings.chunk_chars)
        rows = h.store.db.execute("SELECT text FROM outbox WHERE key LIKE ? ORDER BY seq",
                                  (f"ask:{ask_id}:%",)).fetchall()
        assert not isinstance(expected, str) and [x[0] for x in rows] == expected.card_chunks
        mid = await send(h, approve(), first)
        await settled(h, mid)
        ref = "marmot:" + ref_id(mid)
        assert ref.startswith("marmot:id:") and len(ref) == len("marmot:id:") + 12
        assert decisions(h) == [[BEAD, "--as=op", "--yes", f"--expect-digest={D}", "--via=marmot",
                                 f"--via-ref={ref}"]]
        assert attempts(h, ask_id) == [(mid, "approve", "op", ref, D, None, 0, "recorded")]
        assert ask_status(h, ask_id) == "approved" and current(h, ask_id) is None
        text = queued(h, mid)
        assert text == (f"Approved {BEAD} as op (digest {D12}, via Marmot). btq's design gate accepts it.")
        assert audited(h, kind="ask", action="deciding", ask_id=ask_id, decision="approve", operator="op")
        assert audited(h, kind="ask", action="decided", ask_id=ask_id, outcome="recorded", status="approved",
                       exit_status=0, gate_valid=True, latched_during=False)
        assert bead_state(tmp_path)["via_ref"] == ref
    go(tmp_path, scenario)


@needs_tmux
def test_gate_rejects_is_reported(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        text = await say(h, approve(), first)
        assert text == (f'Approved {BEAD} as op (digest {D12}, via Marmot), but btq\'s design gate rejects '
                        'it: "design_review is stale". Check it on the host.\napprove-bead said: "Closed '
                        f'{BEAD}, but btq approval_valid() rejects it: design_review is stale; second"')
        assert ask_status(h, ask_id) == "approved"
        assert audited(h, kind="ask", action="decided", ask_id=ask_id, gate_valid=False, exit_status=1)
        other, other_first = await card(h, asks.AskPost("approval", bead=OTHER), join=False)
        text = await say(h, approve(D2[:12], OTHER), other_first)
        assert (f"rejects it: (btq gave no reason; run approve-bead {OTHER} on the host). Check it on the "
                "host.\n") in text
        assert ask_status(h, other) == "approved"
    go(tmp_path, scenario, {BEAD: bead(gate_ok=False, gate_reasons=["design_review is stale", "second"]),
                            OTHER: bead(digest=D2, gate_ok=False)})


@needs_tmux
def test_unthreaded_approve_refused(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        text = await say(h, approve(), None)
        assert text == "To decide, react to the approval card or reply to it. Nothing recorded."
        assert (await say(h, "!approve", None)) == text and (await say(h, "!deny", None)) == text
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
    go(tmp_path, scenario)


@needs_tmux
def test_approve_as_reply_to_agent_message_refused(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        echo = next(r["_message_id"] for r in h.fake.sent if r["text"] == "echo: hello")
        text = await say(h, approve(), echo)
        assert text == "To decide, react to the approval card or reply to it. Nothing recorded."
        notice = next(r["_message_id"] for r in h.fake.sent if r["idempotency_key"] == "ready")
        assert (await say(h, approve(), notice)) == text
        assert (await say(h, "!approve", notice)) == text
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
    go(tmp_path, scenario)


@needs_tmux
def test_wrong_digest_refused(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        text = await say(h, approve(D2[:12]), first)
        assert text == (f"This card is for {BEAD}. React 👍 to approve or 👎 to deny, or reply approve / "
                        "deny <reason>. Nothing recorded.")
        assert decisions(h) == [] and ask_status(h, ask_id) == "open" and attempts(h, ask_id) == []
        assert (await say(h, approve(D12.upper()), first)).startswith(f"Approved {BEAD}")   # case-insensitive
    go(tmp_path, scenario)


@needs_tmux
def test_wrong_bead_refused(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert (await say(h, approve(D12, OTHER), first)).startswith(f"This card is for {BEAD}. ")
        assert (await say(h, f"!deny {OTHER} no", first)).startswith(f"This card is for {BEAD}. ")
        assert (await say(h, f"!approve {OTHER}", first)).startswith(f"This card is for {BEAD}. ")
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
    go(tmp_path, scenario)


@needs_tmux
def test_card_not_fully_delivered(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        gate = asyncio.Event()

        def hold_after_first(req: dict[str, Any]) -> None:
            if req["idempotency_key"].startswith("ask:") and req["idempotency_key"].endswith(":0"):
                h.fake.send_gate = gate
        h.fake.on_send = hold_after_first
        ask_id = await posted(h, APPROVAL)
        stored = h.store.ask(ask_id)
        assert stored is not None and stored.card_parts > 1
        await wait_until(lambda: sent_mid(h, f"ask:{ask_id}:0") is not None)
        first = sent_mid(h, f"ask:{ask_id}:0")
        text = await say(h, approve(), first)
        assert text == (f"Ask {ask_id} has not been fully delivered yet. Wait for every part, then react or "
                        "reply again. Nothing recorded.")
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
        h.fake.on_send = None
        h.fake.send_gate = None
        gate.set()
        await wait_until(lambda: h.store.card_delivered(ask_id))
        assert (await say(h, approve(), first)).startswith(f"Approved {BEAD}")
    go(tmp_path, scenario, chunk_chars=200)


async def refused_before_launch(h: Harness, first: str, word: str) -> str:
    """An `!approve` that stops in preflight: the §6 reply, the attempt `refused`, the ask open again."""
    ask_id = h.store.ask_for_message(first)
    assert ask_id is not None
    mid = await send(h, approve(), first)
    await settled(h, mid)
    assert queued(h, mid) == f"admind could not check {BEAD} ({word}); nothing was recorded. Try again."
    assert h.store.inbound_status(mid) == "done" and ask_status(h, ask_id) == "open"
    assert current(h, ask_id) is None and attempts(h, ask_id)[-1][0] == mid
    assert attempts(h, ask_id)[-1][-1] == "refused" and decisions(h) == []
    assert audited(h, kind="ask", action="refused", message_id=ref_id(mid), reason=word, status="open")
    return mid


@needs_tmux
def test_preflight_timeout_settles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        monkeypatch.setattr(approvals, "READ_SECONDS", 1)
        edit(tmp_path, read="hang")
        await refused_before_launch(h, first, "timed out")
        edit(tmp_path, read=None)
        assert (await say(h, approve(), first)).startswith(f"Approved {BEAD}")
        assert ask_status(h, ask_id) == "approved"
    go(tmp_path, scenario)


@needs_tmux
def test_preflight_bad_output_settles(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, read="garbage")
        await refused_before_launch(h, first, "bad output")
        edit(tmp_path, read="big")
        await refused_before_launch(h, first, "bad output")
        edit(tmp_path, read=None)
        assert (await say(h, approve(), first)).startswith(f"Approved {BEAD}")
        assert [a[-1] for a in attempts(h, ask_id)] == ["refused", "refused", "recorded"]
    go(tmp_path, scenario)


@needs_tmux
def test_preflight_spawn_failure_settles(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        tool = tmp_path / "approve-bead"
        mode = tool.stat().st_mode
        tool.chmod(mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        await refused_before_launch(h, first, "unavailable")
        tool.chmod(mode)
        assert (await say(h, approve(), first)).startswith(f"Approved {BEAD}")
        assert ask_status(h, ask_id) == "approved"
    go(tmp_path, scenario)


@needs_tmux
def test_preflight_audit_failure_settles(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        write = h.daemon.audit.write

        def failing(kind: str, **fields: Any) -> None:
            if kind == "ask" and fields.get("action") == "deciding":
                raise OSError("disk full")
            write(kind, **fields)
        h.daemon.audit.write = failing  # type: ignore[method-assign]
        before = len(btq_log(tmp_path))
        await refused_before_launch(h, first, "error")
        assert len(btq_log(tmp_path)) == before            # nothing launched at all
        h.daemon.audit.write = write  # type: ignore[method-assign]
        assert (await say(h, approve(), first)).startswith(f"Approved {BEAD}")
        assert ask_status(h, ask_id) == "approved"
    go(tmp_path, scenario)


@needs_tmux
def test_asks_backstop_reconciles_stranded(tmp_path: Path) -> None:
    stranded: list[str] = []

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, approvers=[])                         # a preflight refusal, nothing written
        close = h.store.close_attempt
        calls = 0

        def once(*args: Any, **kw: Any) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("store failure")
            return close(*args, **kw)
        h.store.close_attempt = once  # type: ignore[method-assign]
        mid = await send(h, approve(), first)
        await wait_until(lambda: audited(h, kind="handler", action="failed", error="RuntimeError"))
        assert ask_status(h, ask_id) == "deciding" and h.store.inbound_status(mid) == "executing"
        assert queued(h, mid) == ""
        stranded.append(mid)
        listing = await say(h, "!asks", None, tag="cmd")
        assert ask_status(h, ask_id) == "open" and current(h, ask_id) is None
        assert listing.startswith(f"{ask_id} approval")
        assert h.store.inbound_status(mid) == "done"           # r6: settled with the ask, no restart notice
        assert attempts(h, ask_id)[0][-1] == "untouched"
        await wait_until(lambda: sent_mid(h, f"asknote:{ask_id}:reconciled:1:0") is not None)
        note = next(r for r in h.fake.sent if r["idempotency_key"] == f"asknote:{ask_id}:reconciled:1:0")
        assert note["text"] == (f"admind could not finish settling the last decision on ask {ask_id}; "
                                "nothing was recorded. Decide again.")
        assert note["reply_to_message_id_hex"] == first
        assert audited(h, kind="ask", action="reconciled", ask_id=ask_id, outcome="untouched", was="deciding",
                       restarted=False)
        edit(tmp_path, approvers=["op"])
        assert (await say(h, approve(), first)).startswith(f"Approved {BEAD}")
    go(tmp_path, scenario)

    async def restarted(h: Harness) -> None:
        assert row_text(h, f"restarted:{stranded[0]}") is None       # recover() ran before this
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE text = ?",
                                  (RESTARTED_NOTICE,)).fetchone()[0] == 0
    go(tmp_path, restarted, fresh=False)


@needs_tmux
def test_cancel_during_preflight_left_for_reconcile(tmp_path: Path) -> None:
    ids: list[str] = []

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, read="wait")
        mid = await send(h, approve(), first)
        await waiting(h)
        assert ask_status(h, ask_id) == "deciding"
        ids.extend([ask_id, mid])              # the run ends here: the worker is cancelled mid-read
    go(tmp_path, scenario)

    async def restarted(h: Harness) -> None:
        ask_id, mid = ids
        await wait_until(lambda: ask_status(h, ask_id) == "open")
        assert attempts(h, ask_id)[0][-1] == "untouched" and current(h, ask_id) is None
        assert row_text(h, f"restarted:{mid}") is None          # the reconcile's notice only, not two
        assert h.store.inbound_status(mid) == "done"
        notes = h.store.db.execute("SELECT text FROM outbox WHERE key LIKE ?",
                                   (f"asknote:{ask_id}:reconciled:%",))
        assert [r[0] for r in notes] == [RECONCILE_RESTARTED]
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE text = ?",
                                  (RESTARTED_NOTICE,)).fetchone()[0] == 0
        assert decisions(h) == []
    edit(tmp_path, read=None)
    go(tmp_path, restarted, fresh=False)


@needs_tmux
def test_digest_changed_before_read(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2)
        text = await say(h, approve(), first)
        new = h.store.newer_ask(ask_id)
        assert new is not None and new.refreshed_from == ask_id
        assert text == (f"{BEAD} changed after this card was posted (shown {D12}, now {D2[:12]}). Nothing "
                        f"recorded. A fresh card follows: ask {new.ask_id}.")
        assert ask_status(h, ask_id) == "stale" and decisions(h) == []
        assert (await say(h, approve(), first)) == (f"Ask {ask_id} is already stale; see ask {new.ask_id}. "
                                                    "Nothing recorded.")
    go(tmp_path, scenario)


@needs_tmux
def test_digest_changed_inside_approve_bead(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="edit-before")
        text = await say(h, approve(), first)
        new = h.store.newer_ask(ask_id)
        assert new is not None and new.refreshed_from == ask_id and new.digest != D
        now = (new.digest or "")[:12]
        assert text == (f"{BEAD} changed after this card was posted (shown {D12}, now {now}). Nothing "
                        f"recorded. A fresh card follows: ask {new.ask_id}.\napprove-bead said: \"The ask is "
                        f"not the one you were shown (expected {D12}, now changed); nothing written.\"")
        assert ask_status(h, ask_id) == "stale" and len(decisions(h)) == 1
        assert attempts(h, ask_id)[0][-2:] == (3, "untouched")
    go(tmp_path, scenario)


@needs_tmux
def test_race_exit_5_settles_from_the_read_back(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="race", gate_ok=False, gate_reasons=["the ask changed after the decision"])
        text = await say(h, approve(), first)
        assert text.startswith(f"Approved {BEAD} as op") and "rejects it" in text
        assert text.endswith(f'\napprove-bead said: "{BEAD} written, but the bead changed during the write; '
                             'btq will reject this approval."')        # R12: exit 5's warning is quoted
        assert attempts(h, ask_id)[0][-2:] == (5, "recorded") and ask_status(h, ask_id) == "approved"
    go(tmp_path, scenario)


@needs_tmux
def test_non_approver_operator_refused(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        text = await say(h, approve(), first, SECOND_HEX)
        assert text == "llctest is not a btq approver. Nothing recorded."
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
        assert attempts(h, ask_id)[0][2:] == ("llctest", attempts(h, ask_id)[0][3], D, None, None, "refused")
    go(tmp_path, scenario)


@needs_tmux
def test_latched_drops_approve(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        h.daemon.latch("test latch")
        mid = await send(h, approve(), first)
        await wait_until(lambda: audited(h, kind="drop", message_id=ref_id(mid)))
        await stays(lambda: decisions(h) == [] and attempts(h, ask_id) == [])
        assert h.store.inbound_status(mid) is None and ask_status(h, ask_id) == "open"
    go(tmp_path, scenario)


@needs_tmux
def test_removed_operator(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert (await h.daemon.change_membership("remove", "llctest"))[0] == "committed"
        mid = await send(h, approve(), first, SECOND_HEX)
        await wait_until(lambda: audited(h, kind="drop", sender_prefix=SECOND_HEX[:8]))
        await stays(lambda: decisions(h) == [] and attempts(h, ask_id) == [])
        assert h.store.inbound_status(mid) is None
    go(tmp_path, scenario, {BEAD: bead(approvers=["op", "llctest"])})


@needs_tmux
def test_removal_waits_for_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "DECIDE_SECONDS", 1)

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="hang")
        mid = await send(h, approve(), first)
        await wait_until(lambda: len(decisions(h)) == 1)
        removal = asyncio.create_task(h.daemon.change_membership("remove", "llctest"))
        await wait_until(lambda: lock_waiters(h.daemon.work_lock) >= 1)
        assert not removal.done() and ask_status(h, ask_id) == "deciding"
        assert (await asyncio.wait_for(removal, 30))[0] == "committed"
        assert h.store.inbound_status(mid) == "done" and ask_status(h, ask_id) == "open"
        assert queued(h, mid) == f"Not recorded. {BEAD} is unchanged; you can decide again."
    go(tmp_path, scenario)


@needs_tmux
def test_latch_during_decision_completes(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="wait")
        mid = await send(h, approve(), first)
        await waiting(h)
        await h.fake.push_event({"type": "group_state_changed", "account_id_hex": ACCOUNT,
                                 "group_id_hex": "b2" * 32, "event_id_hex": "f6" * 32,
                                 "change": "member_added"})
        await wait_until(lambda: h.daemon.latched())
        go_file(h).touch()
        await settled(h, mid)
        assert ask_status(h, ask_id) == "approved"
        assert audited(h, kind="ask", action="decided", ask_id=ask_id, latched_during=True)
        assert queued(h, mid).startswith(f"Approved {BEAD}")
        await stays(lambda: sent_mid(h, f"ask:{mid}:0") is None)
    go(tmp_path, scenario)


LONG = {"description": ["description:", *(f"  │ line {i} of the plan" for i in range(60))]}


def legacy(h: Harness, ask_id: str) -> None:
    """Make the ask one stored before the reply/reaction delta, whose card was shortened (`asks.truncated
    = 1`): R8's old rule still holds for it."""
    h.store.db.execute("UPDATE asks SET truncated = 1 WHERE ask_id = ?", (ask_id,))


@needs_tmux
def test_legacy_truncated_needs_details(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert not (h.store.ask(ask_id) or card_row()).truncated     # a new ask is never shortened
        legacy(h, ask_id)
        stored = h.store.ask(ask_id)
        assert stored is not None and stored.truncated
        text = await say(h, approve(), first)
        assert text == (f"Ask {ask_id} was shortened. Reply !details to it and read it first. Nothing "
                        "recorded.")
        assert decisions(h) == []
        request = await send(h, "!details", first)
        await wait_until(lambda: h.store.details_delivered(ask_id, "op"))
        rows = h.store.db.execute("SELECT text FROM outbox WHERE key LIKE ? ORDER BY seq",
                                  (f"askd:{ask_id}:{request}:%",)).fetchall()
        assert [r[0] for r in rows] == asks.stored_details(stored)
        assert "  │ line 59 of the plan" in "".join(r[0] for r in rows)
        details = sent_mid(h, f"askd:{ask_id}:{request}:0")
        assert (await say(h, approve(), details)).startswith(f"Approved {BEAD}")
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **LONG})})


@needs_tmux
def test_legacy_details_by_other_operator_does_not_count(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        legacy(h, ask_id)
        await send(h, "!details", first, SECOND_HEX)
        await wait_until(lambda: h.store.details_delivered(ask_id, "llctest"))
        assert (await say(h, approve(), first)).startswith(f"Ask {ask_id} was shortened.")
        assert decisions(h) == [] and not h.store.details_delivered(ask_id, "op")
        assert (await say(h, f"!deny {BEAD} not now", first)).startswith(f"Denied {BEAD}")   # deny needs none
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **LONG})})


@needs_tmux
def test_redacted_content_refused_at_post(tmp_path: Path) -> None:
    bad = {"btq-tok1": f"  │ the deploy key is {TOKEN}", "btq-hex1": "  │ " + "ab" * 32,
           "btq-pem1": "  │ -----BEGIN PRIVATE KEY-----"}

    async def scenario(h: Harness) -> None:
        await joined(h)
        for bead_id in bad:
            reply = await h.daemon.on_ask(asks.AskPost("approval", bead=bead_id), NONE)
            assert reply == asks.refused(ASK_REDACTED)
            assert audited(h, kind="ask", action="refused", bead=bead_id, reason="redaction")
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE 'ask:%'").fetchone()[0] == 0
        assert h.store.asks_with_status(*asks.ACTIVE) == []
    go(tmp_path, scenario, {b: bead(readout={**LINES, "description": [*DESCRIPTION, line]})
                            for b, line in bad.items()})


@needs_tmux
def test_plain_yes_is_a_note(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        mid = await send(h, "yes but", first)       # "yes" alone approves now (R28)
        await settled(h, mid)
        assert queued(h, mid) == (f"Noted on ask {ask_id}; this is not a decision. React 👍 to approve or "
                                  "👎 to deny, or reply approve / deny <reason>.")
        assert ask_status(h, ask_id) == "open" and decisions(h) == []
        assert [(a.kind, a.operator, a.text) for a in h.store.answers(ask_id)] == [("note", "op", "yes but")]
    go(tmp_path, scenario)


@needs_tmux
def test_replayed_message_id(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        event = h.fake.message_event(approve(), OPERATOR_HEX, "cd" * 32, reply_to=first)
        await h.fake.push_event(event)
        await h.fake.push_event(event)
        await wait_until(lambda: audited(h, kind="drop", message_id=ref_id("cd" * 32),
                                         reason="replayed message id"))
        await settled(h, "cd" * 32)
        assert len(decisions(h)) == 1 and ask_status(h, ask_id) == "approved"
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize(("fields", "why"), [
    ({"status": "closed"}, "the bead is closed, not open"),
    ({"gaps": ["no effect"]}, "send it back for grooming: no effect"),
    ({"digest": None}, "approve-bead computed no digest"),
])
def test_unpostable_bead_refused_at_post(tmp_path: Path, fields: dict[str, Any], why: str) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        reply = await h.daemon.on_ask(APPROVAL, NONE)
        assert reply.result == "refused" and why in reply.message
        assert audited(h, kind="ask", action="refused", bead=BEAD, reason="not postable")
        assert h.store.asks_with_status(*asks.ACTIVE) == []
    go(tmp_path, scenario, {BEAD: bead(**fields)})


@needs_tmux
def test_deny_with_reason(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        mid = await send(h, f"!deny {BEAD} too broad; it leaks {TOKEN}", first)
        await settled(h, mid)
        assert decisions(h) == [[BEAD, "--as=op", "--yes", f"--expect-digest={D}", "--via=marmot",
                                 f"--via-ref=marmot:{ref_id(mid)}", "--deny",
                                 "--note=too broad; it leaks <redacted GitHub token>"]]
        assert queued(h, mid) == f"Denied {BEAD} as op (via Marmot)."
        assert ask_status(h, ask_id) == "denied" and TOKEN not in json.dumps(btq_log(tmp_path))
        assert bead_state(tmp_path)["denied_by"] == "op"
    go(tmp_path, scenario)


@needs_tmux
def test_approve_bead_failure_reported(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="fail")
        text = await say(h, approve(), first)
        assert text == (f'Not recorded: "refused: something is wrong; nothing written.". {BEAD} is '
                        "unchanged; you can decide again.")
        assert ask_status(h, ask_id) == "open" and attempts(h, ask_id)[0][-2:] == (1, "untouched")
    go(tmp_path, scenario)


@needs_tmux
def test_partial_write_blocks(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="partial")
        text = await say(h, approve(), first)
        assert text == (f'{BEAD} holds a decision admind cannot confirm as yours ("bd close failed"). '
                        f"Nothing more will be done from Marmot. Resolve it on the host with approve-bead "
                        f"{BEAD}.")
        assert ask_status(h, ask_id) == "blocked" and attempts(h, ask_id)[0][-1] == "blocked"
        assert (await say(h, approve(), first)) == f"Ask {ask_id} is already blocked. Nothing recorded."
        assert len(decisions(h)) == 1
        refused = await h.daemon.on_ask(APPROVAL, NONE)
        assert refused.result == "refused" and "already holds a decision" in refused.message
    go(tmp_path, scenario)


@needs_tmux
def test_foreign_decision_blocks(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="foreign")
        text = await say(h, approve(), first)
        assert text.startswith(f"{BEAD} holds a decision admind cannot confirm as yours")
        assert ask_status(h, ask_id) == "blocked"
        r = h.store.ask(ask_id)
        assert r is not None and r.decided_by is None
    go(tmp_path, scenario)


@needs_tmux
def test_read_failure_uncertain_then_retried(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, after_decide={"fail_read": True})
        mid = await send(h, approve(), first)
        await settled(h, mid)
        assert queued(h, mid) == (f"admind could not read {BEAD} back. No further decision is taken from "
                                  f"Marmot until it can; check it on the host with approve-bead {BEAD}.")
        assert ask_status(h, ask_id) == "uncertain" and current(h, ask_id) == mid
        assert attempts(h, ask_id)[0][-2:] == (0, "uncertain")
        assert (await h.daemon.on_ask(APPROVAL, NONE)).message.startswith(f"ask {ask_id} for {BEAD} is being")
        assert (await say(h, "!asks", None, tag="cmd")).startswith(f"{ask_id} approval")   # still unreadable
        assert ask_status(h, ask_id) == "uncertain" and current(h, ask_id) == mid
        edit(tmp_path, fail_read=False)
        listing = await say(h, "!asks", None, tag="cmd")
        assert ask_status(h, ask_id) == "approved" and current(h, ask_id) is None
        assert ask_id not in listing and attempts(h, ask_id)[0][-2:] == (0, "recorded")
        assert audited(h, kind="ask", action="reconciled", ask_id=ask_id, outcome="recorded", was="uncertain")
        r = h.store.ask(ask_id)
        assert r is not None and r.decided_by == "op"
    go(tmp_path, scenario)


@needs_tmux
def test_busy_read_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "BUSY_WAIT", 0.0)

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, after_decide={"busy": True})
        before = reads(h)
        await say(h, approve(), first)
        assert reads(h) - before == 1 + 1 + approvals.BUSY_RETRIES        # preflight, then 1 + 3 read-backs
        assert ask_status(h, ask_id) == "uncertain"
    go(tmp_path, scenario)


@needs_tmux
def test_refused_then_retried_then_restart(tmp_path: Path) -> None:
    ids: list[str] = []

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        refused = await say(h, approve(), first, SECOND_HEX)
        assert refused.endswith("is not a btq approver. Nothing recorded.")
        assert (await say(h, approve(), first)).startswith(f"Approved {BEAD}")
        assert [a[-1] for a in attempts(h, ask_id)] == ["refused", "recorded"]
        ids.append(ask_id)
    go(tmp_path, scenario)

    async def restarted(h: Harness) -> None:
        assert h.store.recovery_snapshot() == [] and ask_status(h, ids[0]) == "approved"
        await stays(lambda: not audited(h, kind="ask", action="reconciled"))
    go(tmp_path, restarted, fresh=False)


@needs_tmux
def test_uncertain_then_reconciled_at_restart(tmp_path: Path) -> None:
    ids: list[str] = []

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, after_decide={"fail_read": True})
        await say(h, approve(), first)
        assert ask_status(h, ask_id) == "uncertain"
        ids.append(ask_id)
    go(tmp_path, scenario)
    edit(tmp_path, fail_read=False)

    async def restarted(h: Harness) -> None:
        await wait_until(lambda: ask_status(h, ids[0]) == "approved")
        note = h.store.db.execute("SELECT text FROM outbox WHERE key LIKE ?",
                                  (f"asknote:{ids[0]}:reconciled:%",)).fetchone()
        assert note[0].startswith(f"Approved {BEAD} as op")
        assert audited(h, kind="ask", action="reconciled", ask_id=ids[0], was="uncertain", restarted=True)
    go(tmp_path, restarted, fresh=False)


def seed(tmp_path: Path, *asks_: tuple[str, str, str, str]) -> Callable[[Harness], None]:
    """Before the store opens: for each (ask ID, bead, attempt message ID, status), an approval ask with
    that attempt, left `deciding` or `uncertain` as a crash would leave it."""
    def before_store(h: Harness) -> None:
        store = Store(h.settings.state_dir / "admind.db")
        for ask_id, bead_id, mid, status in asks_:
            store.insert_ask(row(ask_id, kind="approval", body="", bead=bead_id, digest=D), None)
            a = Attempt(ask_id, mid, "approve", "op", "marmot:" + ref_id(mid), D, None)
            assert store.begin_attempt(a)
            if status == "uncertain":
                assert store.close_attempt(ask_id, mid, expect_status="deciding", settled="uncertain",
                                           exit_status=0, new_status="uncertain", outcome=None,
                                           decided_by=None)
        assert store.recovery_snapshot() == [(a, m, s) for a, _, m, s in asks_]
        store.close()
    return before_store


def closed_by(mid: str, **kw: Any) -> dict[str, Any]:
    """A fake bead that attempt `mid` (by op) closed as approved."""
    return bead(**{**APPROVED, "via_ref": "marmot:" + ref_id(mid), **kw})


@needs_tmux
def test_reconcile_snapshot_statuses(tmp_path: Path) -> None:
    a, b, c = "aa" * 32, "bb" * 32, "cc" * 32

    async def scenario(h: Harness) -> None:
        await wait_until(lambda: ask_status(h, "a222") == "approved" and ask_status(h, "b222") == "approved")
        assert audited(h, kind="ask", action="reconciled", ask_id="a222", was="deciding", outcome="recorded")
        assert audited(h, kind="ask", action="reconciled", ask_id="b222", was="uncertain", outcome="recorded")
        await wait_until(lambda: audited(h, kind="ask", action="reconciled", ask_id="c222", was="uncertain",
                                         outcome="uncertain"))
        assert ask_status(h, "c222") == "uncertain" and current(h, "c222") == c
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE 'asknote:c222:%'"
                                  ).fetchone()[0] == 0                    # unchanged: no notice
        edit(tmp_path, "btq-c1", fail_read=False)
        await h.daemon.reconcile_asks([("c222", c, "deciding")])          # a stale status: no compare-and-set
        assert audited(h, kind="ask", action="conflict", ask_id="c222", expected="deciding")
        assert ask_status(h, "c222") == "uncertain"
        await h.daemon.reconcile_asks([("c222", c, "uncertain")])
        assert ask_status(h, "c222") == "approved"
    go(tmp_path, scenario, {"btq-a1": closed_by(a), "btq-b1": closed_by(b),
                            "btq-c1": closed_by(c, fail_read=True)},
       before_store=seed(tmp_path, ("a222", "btq-a1", a, "deciding"), ("b222", "btq-b1", b, "uncertain"),
                         ("c222", "btq-c1", c, "uncertain")))


@needs_tmux
def test_reconcile_does_not_touch_live_attempt(tmp_path: Path) -> None:
    stranded = "ee" * 32

    async def scenario(h: Harness) -> None:
        live, first = await card(h)
        other, _ = await card(h, asks.AskPost("approval", bead=OTHER), join=False)
        assert h.store.begin_attempt(Attempt(other, stranded, "approve", "op", "marmot:" + ref_id(stranded),
                                             D2, None))
        edit(tmp_path, read="wait")
        mid = await send(h, approve(), first)
        await waiting(h)
        snapshot = h.store.recovery_snapshot()
        assert {s[0] for s in snapshot} == {live, other}
        reconcile = asyncio.create_task(h.daemon.reconcile_asks(snapshot))
        await wait_until(lambda: lock_waiters(h.daemon.work_lock) >= 1)
        assert ask_status(h, other) == "deciding" and ask_status(h, live) == "deciding"
        edit(tmp_path, read=None)
        go_file(h).touch()
        await asyncio.wait_for(reconcile, 30)
        assert ask_status(h, live) == "approved" and queued(h, mid).startswith(f"Approved {BEAD}")
        assert ask_status(h, other) == "open" and attempts(h, other)[0][-1] == "untouched"
        assert audited(h, kind="ask", action="reconcile-skipped", ask_id=live)
        assert not audited(h, kind="ask", action="reconciled", ask_id=live)
    go(tmp_path, scenario, {BEAD: bead(), OTHER: bead(digest=D2)})


@needs_tmux
def test_blocked_asks_do_not_exhaust_quota(tmp_path: Path) -> None:
    def before_store(h: Harness) -> None:
        store = Store(h.settings.state_dir / "admind.db")
        for i in range(asks.MAX_OPEN):
            store.insert_ask(row(f"b{asks.ID_ALPHABET[i]}22", kind="approval", body="", bead=f"btq-x{i}",
                                 digest=D, status="blocked", created_at="2026-10-01T10:00:00+00:00"), None)
        store.close()

    async def scenario(h: Harness) -> None:
        reply = await h.daemon.on_ask(asks.AskPost("question", "Which relay?", "y" * 80, poster="controller"),
                                      NONE)
        assert reply.result == "posted"
        assert (await h.daemon.on_ask(APPROVAL, NONE)).result == "posted"
    go(tmp_path, scenario, before_store=before_store)


@needs_tmux
@pytest.mark.parametrize(("via_ref", "status"), [(None, "approved"), ("marmot:id:000000000000", "blocked")])
def test_restart_while_deciding(tmp_path: Path, via_ref: str | None, status: str) -> None:
    mid = "dd" * 32

    def before_store(h: Harness) -> None:
        seed(tmp_path, ("d222", BEAD, mid, "deciding"))(h)
        store = Store(h.settings.state_dir / "admind.db")
        assert store.claim_inbound(mid)
        store.set_inbound(mid, "executing")
        store.close()

    async def scenario(h: Harness) -> None:
        await wait_until(lambda: ask_status(h, "d222") == status)
        assert row_text(h, f"restarted:{mid}") is None          # recorded or blocked: no "resend it"
        assert h.store.inbound_status(mid) == "done"
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE text = ?",
                                  (RESTARTED_NOTICE,)).fetchone()[0] == 0
        note = h.store.db.execute("SELECT text, reply_to FROM outbox "
                                  "WHERE key LIKE 'asknote:d222:reconciled:%'").fetchone()
        assert note is not None and note[1] is None
        assert note[0].startswith(f"Approved {BEAD} as op" if status == "approved" else
                                  f"{BEAD} holds a decision admind cannot confirm as yours")
        assert attempts(h, "d222")[0][-1] == ("recorded" if status == "approved" else "blocked")
    fields = closed_by(mid) if via_ref is None else closed_by(mid, via_ref=via_ref)
    go(tmp_path, scenario, {BEAD: fields}, before_store=before_store)


def interrupted(tmp_path: Path, mid: str, inbound: str = "executing") -> Callable[[Harness], None]:
    """Before the store opens: ask d222 left `deciding` by attempt `mid`, whose message is `inbound`."""
    def before_store(h: Harness) -> None:
        seed(tmp_path, ("d222", BEAD, mid, "deciding"))(h)
        store = Store(h.settings.state_dir / "admind.db")
        assert store.claim_inbound(mid)
        if inbound != "received":
            store.set_inbound(mid, inbound)
        store.close()
    return before_store


def restart_notices(h: Harness) -> int:
    return h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE text = ?", (RESTARTED_NOTICE,)).fetchone()[0]


@needs_tmux
def test_reconcile_without_approve_bead(tmp_path: Path) -> None:
    """approve_bead removed after an interrupted decision (a rollback, say): the message is answered once
    with the uncertainty notice, never "resend it", and the ask is kept for a later read-back."""
    mid = "dd" * 32
    unverified = f"asknote:d222:unverified:{mid}:%"

    def notes(h: Harness) -> list[str]:
        return [r[0] for r in h.store.db.execute("SELECT text FROM outbox WHERE key LIKE ?", (unverified,))]

    async def first(h: Harness) -> None:
        await wait_until(lambda: audited(h, kind="ask", action="reconcile-unverified", ask_id="d222"))
        assert notes(h) == [RECONCILE_UNVERIFIED.format(bead=BEAD)]
        assert "Decide again" not in notes(h)[0]
        assert h.store.inbound_status(mid) == "done"
        assert row_text(h, f"restarted:{mid}") is None and restart_notices(h) == 0
        assert ask_status(h, "d222") == "deciding" and current(h, "d222") == mid
        assert attempts(h, "d222")[0][-1] is None
    go(tmp_path, first, {BEAD: closed_by(mid)}, before_store=interrupted(tmp_path, mid), approve_bead=None)

    async def again(h: Harness) -> None:
        await wait_until(lambda: audited(h, kind="ask", action="reconcile-skipped", ask_id="d222"))
        assert len(notes(h)) == 1 and restart_notices(h) == 0
        assert ask_status(h, "d222") == "deciding" and current(h, "d222") == mid
    go(tmp_path, again, fresh=False, approve_bead=None)

    async def enabled(h: Harness) -> None:
        await wait_until(lambda: ask_status(h, "d222") == "approved")
        assert attempts(h, "d222")[0][-1] == "recorded"
        assert (row_text(h, "asknote:d222:reconciled:1:0") or "").startswith(f"Approved {BEAD} as op")
        assert len(notes(h)) == 1 and restart_notices(h) == 0
    go(tmp_path, enabled, fresh=False)


@needs_tmux
def test_reconcile_settles_received_message(tmp_path: Path) -> None:
    """Defence in depth: an attempt whose message is still `received` (begin_attempt makes it `executing`,
    so this is an anomaly) is settled done too, so the next restart does not say "resend it"."""
    mid = "dd" * 32

    async def scenario(h: Harness) -> None:
        await wait_until(lambda: ask_status(h, "d222") == "approved")
        assert h.store.inbound_status(mid) == "done"
    go(tmp_path, scenario, {BEAD: closed_by(mid)}, before_store=interrupted(tmp_path, mid, "received"))

    async def restarted(h: Harness) -> None:
        await joined(h)
        assert row_text(h, f"restarted:{mid}") is None and restart_notices(h) == 0
    go(tmp_path, restarted, fresh=False)


@needs_tmux
def test_supersede(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        old, old_first = await card(h)
        new, _ = await card(h, join=False)
        assert ask_status(h, old) == "superseded" and ask_status(h, new) == "open"
        await wait_until(lambda: sent_mid(h, f"asknote:{old}:superseded:{new}:0") is not None)
        note = next(r for r in h.fake.sent if r["idempotency_key"] == f"asknote:{old}:superseded:{new}:0")
        assert note["reply_to_message_id_hex"] == old_first and f"superseded by ask {new}" in note["text"]
        assert audited(h, kind="ask", action="posted", ask_id=new, superseded=[old])
        assert (await say(h, approve(), old_first)) == (f"Ask {old} is already superseded; see ask {new}. "
                                                        "Nothing recorded.")
        assert decisions(h) == []
    go(tmp_path, scenario)


@needs_tmux
def test_post_refused_while_bead_deciding(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="wait")
        mid = await send(h, approve(), first)
        await waiting(h)
        reply = await h.daemon.on_ask(APPROVAL, NONE)
        assert reply == asks.refused(f"ask {ask_id} for {BEAD} is being decided or awaits a read-back; post "
                                     "again once it settles.")
        go_file(h).touch()
        await settled(h, mid)
        assert ask_status(h, ask_id) == "approved"
    go(tmp_path, scenario)


@needs_tmux
def test_concurrent_posts_respect_limit(tmp_path: Path) -> None:
    beads = {f"btq-p{i}": bead(read="wait") for i in range(5)}

    def before_store(h: Harness) -> None:
        store = Store(h.settings.state_dir / "admind.db")
        for i in range(asks.MAX_OPEN - 2):
            store.insert_ask(row(f"q{asks.ID_ALPHABET[i]}22", created_at="2026-10-01T10:00:00+00:00"), None)
        store.close()

    async def scenario(h: Harness) -> None:
        posts = asyncio.gather(*(h.daemon.on_ask(asks.AskPost("approval", bead=b), NONE) for b in beads))
        await waiting(h)
        go_file(h).touch()
        replies = await asyncio.wait_for(posts, 30)
        assert [r.result for r in replies] == ["posted", "posted", "refused", "refused", "refused"]
        assert all(r == asks.refused(ASK_BUSY) for r in replies[2:])
        full = await h.daemon.on_ask(asks.AskPost("approval", bead="btq-p4"), NONE)
        assert full == asks.refused(ASK_TOO_MANY)
    go(tmp_path, scenario, beads, before_store=before_store)


@needs_tmux
def test_cli_post_budget_covers_queued_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    """Two `admind ask post` clients: one held in its approve-bead read, one queued behind it (R22). Both
    get their reply: the post budget covers the reads, not an ordinary request's margin, which is cut to
    almost nothing here so that a client on that margin alone would give up (exit 69)."""
    beads = {"btq-p0": bead(read="wait"), "btq-p1": bead(read="wait")}

    async def scenario(h: Harness) -> None:
        await joined(h)
        via_main(monkeypatch, h.settings)
        monkeypatch.setattr(cli, "ASK_READ_SECONDS", 0.01)
        codes: dict[str, int] = {}

        def client(b: str) -> None:
            codes[b] = cli.main(["ask", "post", "--kind", "approval", "--bead", b])
        threads = [threading.Thread(target=client, args=(b,), daemon=True) for b in beads]
        for t in threads:
            t.start()
        await waiting(h)
        await wait_until(lambda: h.daemon.posts_in_flight == 2, 20)
        await stays(lambda: all(t.is_alive() for t in threads), 0.2)  # well past the cut margin
        go_file(h).touch()
        for t in threads:
            await asyncio.to_thread(t.join, 30)
            assert not t.is_alive()
        assert codes == {b: 0 for b in beads}
        out = capsys.readouterr()
        assert out.out.count(" posted") == 2 and out.err == ""
        assert len(h.store.asks_with_status(*asks.ACTIVE)) == 2
    go(tmp_path, scenario, beads)


@needs_tmux
def test_latch_during_post_read_refused(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        post = asyncio.create_task(h.daemon.on_ask(APPROVAL, NONE))
        await waiting(h)
        h.daemon.latch("test latch")
        go_file(h).touch()
        assert await asyncio.wait_for(post, 30) == asks.refused(ASK_LATCHED)
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE 'ask:%'").fetchone()[0] == 0
        assert h.store.asks_with_status(*asks.ACTIVE) == []
    go(tmp_path, scenario, {BEAD: bead(read="wait")})


@needs_tmux
def test_not_configured(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        assert await h.daemon.on_ask(APPROVAL, NONE) == asks.refused(ASK_NO_APPROVALS)
        reply = await h.daemon.on_ask(asks.AskPost("question", "Which relay?", "y" * 80, poster="controller"),
                                      NONE)
        assert reply.result == "posted"
    run_with(tmp_path, scenario, before=two_operators, settings_overrides=OPS)


@needs_tmux
def test_answer_command_refused_for_approval(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        text = await say(h, f"!answer {ask_id} yes", None, tag="cmd")
        assert text == (f"Ask {ask_id} is an approval ask. To decide, react to its card or reply to it. "
                        "Nothing recorded.")
        assert h.store.answers(ask_id) == [] and decisions(h) == []
    go(tmp_path, scenario)


@needs_tmux
def test_near_miss_digest_refused(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        near = D12[:11] + ("0" if D12[-1] != "0" else "1")
        assert (await say(h, approve(near), first)).startswith(f"This card is for {BEAD}. ")
        assert decisions(h) == [] and attempts(h, ask_id) == []
    go(tmp_path, scenario)


@needs_tmux
def test_decide_rechecks_authorisation(tmp_path: Path) -> None:
    """Step 3, called as the worker would after `handle`'s check, but with admind latched since."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        h.daemon.latch("test latch")
        mid = "de" * 32
        cmd = commands.parse(approve())
        assert cmd is not None
        await h.daemon.decide(mid, cmd, first)
        assert audited(h, kind="drop", message_id=ref_id(mid), what="decision")
        assert attempts(h, ask_id) == [] and reads(h) == 1 and ask_status(h, ask_id) == "open"
    go(tmp_path, scenario)


@needs_tmux
def test_latch_during_preflight_read_drops_decision(tmp_path: Path) -> None:
    """Step 6, the commit point (R11): the preflight read awaited, so authorisation is checked again."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, read="wait")
        mid = await send(h, approve(), first)
        await waiting(h)
        h.daemon.latch("test latch")
        go_file(h).touch()
        await settled(h, mid)
        assert h.store.inbound_status(mid) == "dropped"
        assert audited(h, kind="drop", message_id=ref_id(mid), what="decision")
        assert decisions(h) == [] and ask_status(h, ask_id) == "open" and current(h, ask_id) is None
        assert attempts(h, ask_id)[0][-1] == "refused" and queued(h, mid) == ""
    go(tmp_path, scenario)


@needs_tmux
def test_latch_during_asks_backstop_drops_listing(tmp_path: Path) -> None:
    """`!asks` checks authorisation again after the backstop's read-backs."""
    stale = "ee" * 32

    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        assert h.store.begin_attempt(Attempt(ask_id, stale, "approve", "op", "marmot:" + ref_id(stale), D,
                                             None))
        edit(tmp_path, read="wait")
        mid = await send(h, "!asks", None)
        await waiting(h)
        h.daemon.latch("test latch")
        go_file(h).touch()
        await settled(h, mid)
        assert audited(h, kind="drop", message_id=ref_id(mid), what="command")
        assert queued(h, mid, "cmd") == ""
        assert ask_status(h, ask_id) == "open" and attempts(h, ask_id)[0][-1] == "untouched"
    go(tmp_path, scenario)


@needs_tmux
def test_legacy_details_not_fully_delivered_does_not_count(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        legacy(h, ask_id)
        gate = asyncio.Event()

        def hold_after_first(req: dict[str, Any]) -> None:
            if req["idempotency_key"].startswith("askd:") and req["idempotency_key"].endswith(":0"):
                h.fake.send_gate = gate
        h.fake.on_send = hold_after_first
        request = await send(h, "!details", first)
        await wait_until(lambda: sent_mid(h, f"askd:{ask_id}:{request}:0") is not None)
        stored = h.store.ask(ask_id)
        assert stored is not None and len(asks.stored_details(stored)) > 1
        details = sent_mid(h, f"askd:{ask_id}:{request}:0")
        assert (await say(h, approve(), details)).startswith(f"Ask {ask_id} was shortened.")
        assert decisions(h) == [] and not h.store.details_delivered(ask_id, "op")
        h.fake.on_send = None
        h.fake.send_gate = None
        gate.set()
        await wait_until(lambda: h.store.details_delivered(ask_id, "op"))
        assert (await say(h, approve(), details)).startswith(f"Approved {BEAD}")
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **LONG})}, chunk_chars=200)


@needs_tmux
def test_decided_open_bead_blocks_before_launch(tmp_path: Path) -> None:
    """A bead that holds a decision but is still open (a partial write elsewhere) stops in preflight."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decision="approve", approved_by="someone-else")
        text = await say(h, approve(), first)
        assert text.startswith(f"{BEAD} holds a decision admind cannot confirm as yours. Nothing more")
        assert decisions(h) == [] and ask_status(h, ask_id) == "blocked"
        assert attempts(h, ask_id)[0][-1] == "refused"
    go(tmp_path, scenario)


@needs_tmux
def test_reconcile_skips_a_replaced_attempt(tmp_path: Path) -> None:
    """A reconcile of an attempt that is no longer the ask's current one reads nothing and settles
    nothing."""
    old, new = "ee" * 32, "ef" * 32

    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        assert h.store.begin_attempt(Attempt(ask_id, new, "approve", "op", "marmot:" + ref_id(new), D, None))
        before = reads(h)
        await h.daemon.reconcile_one(ask_id, old, "deciding", restarted=False)
        assert audited(h, kind="ask", action="reconcile-skipped", ask_id=ask_id)
        assert reads(h) == before and ask_status(h, ask_id) == "deciding" and current(h, ask_id) == new
    go(tmp_path, scenario)


THIRD = "btq-ef56g"
PARTIAL = [{"approved_at": "2026-10-06T10:00:00Z"}, {"approved_by": ""}, {"via_ref": None}]


@pytest.mark.parametrize("partial", PARTIAL)
def test_fake_partial_record_is_decided(tmp_path: Path, partial: dict[str, Any]) -> None:
    """The fake follows the real tool: a decision key present, whatever its value, is a decision."""
    async def scenario() -> None:
        ab = fake(tmp_path, **partial)
        r = await ab.read(BEAD)
        assert isinstance(r, Readout) and r.decided
        assert await ab.decide(APPROVE, BEAD) == (1, "Already holds a decision; refusing.")
    asyncio.run(scenario())


@needs_tmux
@pytest.mark.parametrize("partial", PARTIAL)
def test_partial_record_at_post_preflight_and_read_back(tmp_path: Path, partial: dict[str, Any]) -> None:
    """A partial decision record (any decision key, even empty or null) is refused at post, stops the
    attempt in preflight, and makes a read-back `blocked`."""
    async def scenario(h: Harness) -> None:
        await joined(h)
        refused = await h.daemon.on_ask(asks.AskPost("approval", bead=OTHER), NONE)           # post
        assert refused.result == "refused" and "already holds a decision" in refused.message
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE 'ask:%'").fetchone()[0] == 0
        ask_id, first = await card(h, join=False)                                            # preflight
        edit(tmp_path, **partial)
        text = await say(h, approve(), first)
        assert text.startswith(f"{BEAD} holds a decision admind cannot confirm as yours. Nothing more")
        assert decisions(h) == [] and ask_status(h, ask_id) == "blocked"
        assert attempts(h, ask_id)[0][-1] == "refused"
        third, third_first = await card(h, asks.AskPost("approval", bead=THIRD), join=False)  # read-back
        edit(tmp_path, THIRD, decide="fail", after_decide=partial)
        text = await say(h, approve(D12, THIRD), third_first)
        assert text == (f'{THIRD} holds a decision admind cannot confirm as yours ("refused: something is '
                        'wrong; nothing written."). Nothing more will be done from Marmot. Resolve it on '
                        f"the host with approve-bead {THIRD}.")
        assert len(decisions(h)) == 1 and ask_status(h, third) == "blocked"
        assert attempts(h, third)[0][-2:] == (1, "blocked")
    go(tmp_path, scenario, {BEAD: bead(), OTHER: bead(digest=D2, **partial), THIRD: bead()})


@needs_tmux
def test_uncertain_quotes_the_stderr_line(tmp_path: Path) -> None:
    """R12: the stderr line is quoted after the uncertain wording too."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="fail", after_decide={"fail_read": True})
        text = await say(h, approve(), first)
        assert text == (f"admind could not read {BEAD} back. No further decision is taken from Marmot until "
                        f"it can; check it on the host with approve-bead {BEAD}.\napprove-bead said: "
                        '"refused: something is wrong; nothing written."')
        assert ask_status(h, ask_id) == "uncertain"
    go(tmp_path, scenario)


@needs_tmux
def test_launch_follows_the_last_check_with_no_await(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r2-B1 (R11): a latch callback queued the moment step 6's check passes runs only once the decision's
    child exists: nothing awaits between the check and the launch."""
    launches: list[tuple[bool, bool]] = []
    init = reap.Child.__init__
    latched = lambda: False  # noqa: E731 - replaced once the daemon exists

    def recording(self: reap.Child, popen: Any) -> None:
        launches.append(("--yes" in popen.args, latched()))
        init(self, popen)
    monkeypatch.setattr(reap.Child, "__init__", recording)

    async def scenario(h: Harness) -> None:
        nonlocal latched
        latched = h.daemon.latched
        ask_id, first = await card(h)
        ab, check = h.daemon.approve_bead, h.daemon.authorised
        assert ab is not None
        read = ab.read
        armed = queued_latch = False

        async def reading(bead_id: str, on_launch: Any = None) -> Any:
            nonlocal armed
            r = await read(bead_id, on_launch)
            armed = True        # the preflight read is back: the next check is step 6's
            return r

        def authorised(mid: str | None = None) -> bool:
            nonlocal queued_latch
            ok = check(mid)
            if armed and ok and not queued_latch:
                asyncio.get_running_loop().call_soon(h.daemon.latch, "test latch")
                queued_latch = True
            return ok
        monkeypatch.setattr(ab, "read", reading)
        monkeypatch.setattr(h.daemon, "authorised", authorised)
        mid = await send(h, approve(), first)
        await settled(h, mid)
        assert queued_latch and h.daemon.latched()
        assert [latched_then for decision, latched_then in launches if decision] == [False]
        assert ask_status(h, ask_id) == "approved" and len(decisions(h)) == 1
        assert audited(h, kind="ask", action="decided", ask_id=ask_id, latched_during=True)
    go(tmp_path, scenario)


@needs_tmux
def test_failure_after_the_decision_launch_reads_back(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """r2-B2: the decision's child exists (it wrote the decision and started a descendant in its group), then
    connecting its pipes fails: the group is reaped, and the outcome comes from the read-back, not "nothing
    was recorded"."""
    pid_file = tmp_path / "descendant.pid"
    fail_connect(monkeypatch, pid_file, decision=True)

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="descendant")
        text = await say(h, approve(), first)
        assert text == f"Approved {BEAD} as op (digest {D12}, via Marmot). btq's design gate accepts it."
        assert ask_status(h, ask_id) == "approved" and attempts(h, ask_id)[0][-2:] == (None, "recorded")
        assert reads(h) == 3 and len(decisions(h)) == 1
        child, grandchild = pids(tmp_path)[2], int(pid_file.read_text())
        await wait_until(lambda: gone(child) and gone(grandchild), 5)
    try:
        go(tmp_path, scenario)
    finally:
        kill_quietly(pid_file)


@needs_tmux
def test_a_launch_that_may_have_happened_reads_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Popen raising anything but OSError can't rule out a child: the outcome is read back."""
    spawn = reap.spawn

    def failing(argv: list[str], **kw: Any) -> reap.Child:
        if "--yes" in argv:
            raise RuntimeError("injected")
        return spawn(argv, **kw)
    monkeypatch.setattr(reap, "spawn", failing)

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        text = await say(h, approve(), first)
        assert text == f"Not recorded. {BEAD} is unchanged; you can decide again."
        assert attempts(h, ask_id)[0][-2:] == (None, "untouched") and reads(h) == 3 and decisions(h) == []
    go(tmp_path, scenario)
