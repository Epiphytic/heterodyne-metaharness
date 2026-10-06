"""A fake `approve-bead` with Task 1's real interface (beads-task-queue `bin/approve-bead`), for admind.

Beads live in the JSON file $FAKE_BTQ_DB, keyed by bead ID; each argv is appended to $FAKE_BTQ_LOG as a
JSON list. As the real tool: `--json` takes no decision or paging flag (exit 2) and prints one line of the
readout fields; a busy bead prints `{"format": 1, "busy": true}` for `--json` and exits 4; a decision exits
0 (decided; for an approval, btq's gate accepts it), 1 (a refusal, a bd failure, or written but the gate
rejects it), 2 (usage, or gaps), 3 (not the expected content), 4 (busy) or 5 (written, but the content
changed during the write). Every non-zero exit prints exactly one stderr line.

A bead's record holds its readout fields, the decision metadata, and test controls. As the real tool, a
bead holds a decision once any key `decision` or `via_ref`, or starting `approved_` or `denied_`, is present,
whatever its value (so a test removes such a key to undo a decision, rather than setting it to None). The
controls:
- `busy`: every call is busy (exit 4);
- `fail_read`: `--json` fails like bd (exit 1);
- `read`: `"hang"` (sleeps; the caller's timeout must end it), `"garbage"` (non-JSON, exit 0), `"big"` (past
  the read cap on stdout and past the pipe buffer on stderr, still writing), `"held"` (a grandchild in its
  own session keeps stdout open; its PID goes to `<db dir>/held.pid`), `"descendant"` (a grandchild in the
  process group, off the pipes, sleeps; its PID goes to `<db dir>/descendant.pid`; then it hangs), `"wait"`
  (see `wait`);
- `decide`: `"ok"` (default), `"fail"` (refused, exit 1), `"hang"`, `"orphan"` (a grandchild in the
  process group writes the decision 1 s later; its PID goes to `<db dir>/orphan.pid`; exit 1 at once),
  `"partial"` (fields written, close failed), `"foreign"` (closed by someone else), `"edit-before"` (the ask
  changes before the pre-write re-check: exit 3), `"race"` (written, then the ask changes: exit 5), `"big"`
  (past the decision cap on stderr), `"wait"`;
- `wait`: a name. A `"wait"` mode creates `<db dir>/<name>.waiting`, then waits (bounded) for
  `<db dir>/<name>.go` before going on as normal;
- `gate_ok` (default true) and `gate_reasons`: what btq's `approval_valid` says of a closed approval;
- `after_decide`: fields merged into the bead once any decision run ends (to fail a read-back).
Each run appends its PID to `<db dir>/pids`. It touches nothing outside the files named above.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DB = Path(os.environ["FAKE_BTQ_DB"])
DIGEST = re.compile(r"[0-9a-f]{64}")
VIA_REF = re.compile(r"[A-Za-z0-9:._-]{1,80}")
WAIT_SECONDS = 60.0
VALUE_FLAGS = {"--as", "--note", "--expect-digest", "--via", "--via-ref", "--tree", "--doc"}
BOOL_FLAGS = {"--json", "--yes", "--deny", "--detail", "--dry-run"}
READ_ONLY_REFUSED = ("--tree", "--detail", "--doc", "--deny", "--note", "--yes", "--dry-run",
                     "--expect-digest", "--via", "--via-ref")
FIELDS: dict[str, Any] = {
    "format": 1, "busy": False, "status": "open", "labels": ["kind:approval"], "kind_approval": True,
    "digest": None, "posted_digest": None, "gaps": [], "design_review_valid": None, "adr_revision": None,
    "approvers": [], "decision": None, "approved_by": None, "approved_digest": None, "denied_by": None,
    "denied_digest": None, "via": None, "via_ref": None, "decided": False, "gate_valid": None,
    "gate_reasons": [], "links": [], "readout": {"title": [], "description": [], "ask": []}}


def fail(code: int, message: str) -> int:
    print(" ".join(message.split()) or "failed", file=sys.stderr)
    return code


def load() -> dict[str, dict[str, Any]]:
    return json.loads(DB.read_text())


def save(beads: dict[str, dict[str, Any]]) -> None:
    tmp = DB.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(beads))
    tmp.replace(DB)


def update(bead_id: str, **fields: Any) -> dict[str, Any]:
    beads = load()
    beads[bead_id].update(fields)
    save(beads)
    return beads[bead_id]


def parse(argv: list[str]) -> tuple[str, dict[str, str | bool]] | str:
    """(bead, flags), or a usage error. Values only in the `--flag=value` form admind uses."""
    bead_id: str | None = None
    flags: dict[str, str | bool] = {}
    for arg in argv:
        name, eq, value = arg.partition("=")
        if not arg.startswith("-"):
            if bead_id is not None:
                return "unrecognized arguments"
            bead_id = arg
        elif name in VALUE_FLAGS and eq:
            flags[name] = value
        elif name in BOOL_FLAGS and not eq:
            flags[name] = True
        else:
            return "unrecognized arguments"
    if bead_id is None:
        return "the following arguments are required: id"
    return bead_id, flags


def decided(bead: dict[str, Any]) -> bool:
    """The real tool's `decision_fields(meta)` is not empty: key presence, not the values."""
    return any(key in ("decision", "via_ref") or key.startswith(("approved_", "denied_")) for key in bead)


def readout(bead_id: str, bead: dict[str, Any]) -> dict[str, Any]:
    out = {**FIELDS, "id": bead_id, **{k: v for k, v in bead.items() if k in FIELDS}}
    out["decided"] = decided(bead)
    return out


def wait(bead: dict[str, Any]) -> None:
    name = str(bead.get("wait", "gate"))
    (DB.parent / f"{name}.waiting").touch()
    end = time.monotonic() + WAIT_SECONDS
    while not (DB.parent / f"{name}.go").exists() and time.monotonic() < end:
        time.sleep(0.01)


def spew(stdout_bytes: int) -> None:
    """Past `stdout_bytes` on stdout and past any pipe buffer on stderr, then keep writing until killed."""
    block = b"x" * 65536
    with os.fdopen(1, "wb", closefd=False) as out, os.fdopen(2, "wb", closefd=False) as err:
        written = 0
        while True:
            if written <= stdout_bytes + 65536:
                out.write(block)
                out.flush()
            err.write(block)
            err.flush()
            written += len(block)


def hold_stdout() -> None:
    """A grandchild in its own session (os.setsid, as no `setsid` binary is assumed) keeps stdout open. It
    has left the process group before this returns: it says so on a pipe of its own."""
    ready_r, ready_w = os.pipe()
    code = f"import os, time\nos.setsid()\nos.write({ready_w}, b'1')\ntime.sleep(30)\n"
    child = subprocess.Popen([sys.executable, "-c", code], stdout=sys.stdout, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, pass_fds=(ready_w,))
    os.close(ready_w)
    os.read(ready_r, 1)
    (DB.parent / "held.pid").write_text(str(child.pid))


def descendant() -> None:
    """A grandchild that stays in the process group, with its output off the pipes, and sleeps. Its PID is
    written (atomically) once it exists."""
    pid = os.fork()
    if pid == 0:
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.dup2(null, 2)
        time.sleep(60)
        os._exit(0)
    tmp = DB.parent / "descendant.pid.tmp"
    tmp.write_text(str(pid))
    tmp.replace(DB.parent / "descendant.pid")


def write_decision(bead_id: str, f: dict[str, str | bool], by: str, close: bool) -> dict[str, Any]:
    deny = bool(f.get("--deny"))
    prefix = "denied" if deny else "approved"
    fields: dict[str, Any] = {"decision": "deny" if deny else "approve", f"{prefix}_by": by,
                              f"{prefix}_digest": f.get("--expect-digest"), "via": f.get("--via", "cli"),
                              "via_ref": f.get("--via-ref")}
    if close:
        bead = load()[bead_id]
        fields["status"] = "closed"
        fields["gate_valid"] = None if deny else bool(bead.get("gate_ok", True))
    return update(bead_id, **fields)


def json_read(bead_id: str, bead: dict[str, Any]) -> int:
    mode = bead.get("read")
    if mode == "wait":
        wait(bead)
        bead = load()[bead_id]
    if bead.get("busy"):
        print(json.dumps({"format": 1, "busy": True}))
        return fail(4, f"{bead_id} is busy: another approve-bead run holds its lock. Try again.")
    if bead.get("fail_read"):
        return fail(1, "bd show failed")
    if mode == "hang":
        time.sleep(600)
    if mode == "garbage":
        print("this is not JSON")
        return 0
    if mode == "big":
        spew(8 << 20)
    if mode == "held":
        hold_stdout()
        return 0
    if mode == "descendant":
        descendant()
        time.sleep(600)
    print(json.dumps(readout(bead_id, bead), ensure_ascii=True))
    return 0


def decide(bead_id: str, bead: dict[str, Any], f: dict[str, str | bool]) -> int:
    by = str(f.get("--as", ""))
    if by not in bead.get("approvers", []):
        return fail(1, f"Approver must be one of {bead.get('approvers', [])} (use --as)")
    if bead.get("busy"):
        return fail(4, f"{bead_id} is busy: another approve-bead run holds its lock. Try again.")
    mode = bead.get("decide", "ok")
    if mode == "wait":
        wait(bead)
        bead = load()[bead_id]
    expect = str(f.get("--expect-digest", ""))
    if expect and bead.get("digest") != expect:
        return fail(3, f"The ask is not the one you were shown (expected {expect[:12]}, now "
                       f"{str(bead.get('digest') or 'none')[:12]}); nothing written.")
    if bead.get("status") == "closed":
        return fail(1, "Already closed; nothing to do.")
    if not bead.get("kind_approval", True):
        return fail(1, "Not a kind:approval bead; refusing.")
    if decided(bead):
        return fail(1, "Already holds a decision; refusing.")
    if bead.get("gaps"):
        return fail(2, f"Not approvable as written: {len(bead['gaps'])} gap(s); nothing written.")
    if mode == "fail":
        return fail(1, "refused: something is wrong; nothing written.")
    if mode == "hang":
        time.sleep(600)
        return fail(1, "hung")
    if mode == "big":
        spew(0)
    if mode == "edit-before":
        update(bead_id, digest=hashlib.sha256(str(bead.get("digest")).encode()).hexdigest())
        return fail(3, f"The ask is not the one you were shown (expected {expect[:12]}, now changed); "
                       "nothing written.")
    if mode == "orphan":
        pid = os.fork()
        if pid == 0:      # the grandchild stays in the process group and writes late, off the pipes
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, 1)
            os.dup2(null, 2)
            time.sleep(1)
            write_decision(bead_id, f, by, close=True)
            os._exit(0)
        (DB.parent / "orphan.pid").write_text(str(pid))
        return fail(1, "bd update failed")
    if mode == "partial":
        write_decision(bead_id, f, by, close=False)
        return fail(1, "bd close failed")
    after = write_decision(bead_id, f, "someone-else" if mode == "foreign" else by, close=True)
    if mode == "race":
        update(bead_id, digest=hashlib.sha256(str(bead.get("digest")).encode()).hexdigest())
        return fail(5, f"{bead_id} written, but the bead changed during the write; btq will reject this "
                       "approval.")
    if f.get("--deny") or after.get("gate_valid"):
        return 0
    reasons = "; ".join(after.get("gate_reasons", [])) or "no reason given"
    return fail(1, f"Closed {bead_id}, but btq approval_valid() rejects it: {reasons}")


def main() -> int:
    argv = sys.argv[1:]
    with Path(os.environ["FAKE_BTQ_LOG"]).open("a") as log:
        log.write(json.dumps(argv) + "\n")
    with (DB.parent / "pids").open("a") as pids:      # each run's PID, so a test can check it was reaped
        pids.write(f"{os.getpid()}\n")
    parsed = parse(argv)
    if isinstance(parsed, str):
        return fail(2, f"approve-bead: error: {parsed}")
    bead_id, f = parsed
    expect = f.get("--expect-digest")
    if expect is not None and not (isinstance(expect, str) and DIGEST.fullmatch(expect)):
        return fail(2, "approve-bead: error: --expect-digest must be 64 lowercase hex characters")
    if f.get("--via") not in (None, "cli", "marmot"):
        return fail(2, "approve-bead: error: argument --via: invalid choice")
    via_ref = f.get("--via-ref")
    if via_ref is not None:
        if f.get("--via") != "marmot":
            return fail(2, "approve-bead: error: --via-ref needs --via=marmot")
        if not (isinstance(via_ref, str) and VIA_REF.fullmatch(via_ref)):
            return fail(2, "approve-bead: error: --via-ref must match [A-Za-z0-9:._-]{1,80}")
    if f.get("--json"):
        used = [flag for flag in READ_ONLY_REFUSED if f.get(flag)]
        if used:
            return fail(2, f"approve-bead: error: --json is read only; it takes none of {', '.join(used)}")
    bead = load().get(bead_id)
    if bead is None:
        return fail(1, f"bd show {bead_id} returned no bead")
    if f.get("--json"):
        return json_read(bead_id, bead)
    try:
        return decide(bead_id, bead, f)
    finally:
        extra = load()[bead_id].get("after_decide")
        if extra:
            update(bead_id, **extra, after_decide=None)


if __name__ == "__main__":
    sys.exit(main())
