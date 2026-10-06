"""A fake `approve-bead` (relay plan Task 1's interface) for admind's approval tests.

Beads live in the JSON file $FAKE_BTQ_DB, keyed by bead ID; each argv is appended to $FAKE_BTQ_LOG as a
JSON list. A bead's record holds its readout fields plus test controls:
- `busy`: every call gets the busy reply (exit 4);
- `fail_read`: `--json` exits 1;
- `read`: `"hang"` sleeps 600 s, `"garbage"` prints non-JSON and exits 0;
- `decide`: `"ok"` (default), `"fail"`, `"hang"`, `"orphan"`, `"partial"`, `"foreign"`, `"edit-before"`;
- `gate`: the `gate_valid` an approval gets (default true).
It touches nothing outside the two files.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

DB = Path(os.environ["FAKE_BTQ_DB"])
FIELDS: dict[str, Any] = {
    "format": 1, "busy": False, "status": "open", "labels": ["kind:approval"], "kind_approval": True,
    "digest": None, "posted_digest": None, "gaps": [], "design_review_valid": None, "adr_revision": None,
    "approvers": [], "decision": None, "approved_by": None, "approved_digest": None, "denied_by": None,
    "denied_digest": None, "via": None, "via_ref": None, "decided": False, "gate_valid": None,
    "gate_reasons": [], "links": [], "readout": {"title": [], "description": [], "ask": []}}


def load() -> dict[str, dict[str, Any]]:
    return json.loads(DB.read_text())


def save(beads: dict[str, dict[str, Any]]) -> None:
    tmp = DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(beads))
    tmp.replace(DB)


def flags(argv: list[str]) -> dict[str, str | bool]:
    out: dict[str, str | bool] = {}
    for arg in argv:
        name, eq, value = arg.partition("=")
        out[name] = value if eq else True
    return out


def readout(bead_id: str, bead: dict[str, Any]) -> dict[str, Any]:
    return {**FIELDS, "id": bead_id, **{k: v for k, v in bead.items() if k in FIELDS}}


def write_decision(bead_id: str, f: dict[str, str | bool], by: str, close: bool) -> None:
    beads = load()
    bead = beads[bead_id]
    deny = bool(f.get("--deny"))
    word = "deny" if deny else "approve"
    bead.update({"decision": word, f"{'denied' if deny else 'approved'}_by": by,
                 f"{'denied' if deny else 'approved'}_digest": f.get("--expect-digest"),
                 "via": f.get("--via", "cli"), "via_ref": f.get("--via-ref"), "decided": True})
    if close:
        bead["status"] = "closed"
        bead["gate_valid"] = None if deny else bead.get("gate", True)
    save(beads)


def main() -> int:
    argv = sys.argv[1:]
    with Path(os.environ["FAKE_BTQ_LOG"]).open("a") as log:
        log.write(json.dumps(argv) + "\n")
    bead_id, f = argv[0], flags(argv[1:])
    bead = load().get(bead_id)
    if bead is None:
        print("no such bead", file=sys.stderr)
        return 1
    if bead.get("busy"):
        if f.get("--json"):
            print(json.dumps({"format": 1, "busy": True}))
        else:
            print("busy: another approve-bead holds the lock", file=sys.stderr)
        return 4
    if f.get("--json"):
        if bead.get("fail_read"):
            print("bd failed", file=sys.stderr)
            return 1
        if bead.get("read") == "hang":
            time.sleep(600)
        if bead.get("read") == "garbage":
            print("this is not JSON")
            return 0
        print(json.dumps(readout(bead_id, bead)))
        return 0
    mode = bead.get("decide", "ok")
    by = str(f.get("--as", ""))
    if mode == "edit-before":
        beads = load()
        beads[bead_id]["digest"] = hashlib.sha256(str(bead.get("digest")).encode()).hexdigest()
        save(beads)
        bead = beads[bead_id]
    if f.get("--expect-digest") != bead.get("digest"):
        print("The ask is not the one you were shown; nothing written.", file=sys.stderr)
        return 3
    if mode == "fail":
        print("refused: something is wrong", file=sys.stderr)
        return 1
    if mode == "hang":
        time.sleep(600)
        return 1
    if mode == "orphan":
        if os.fork() == 0:     # the grandchild stays in the process group and writes late
            time.sleep(1)
            write_decision(bead_id, f, by, close=True)
            os._exit(0)
        print("parent exited early", file=sys.stderr)
        return 1
    if mode == "partial":
        write_decision(bead_id, f, by, close=False)
        print("bd close failed", file=sys.stderr)
        return 1
    write_decision(bead_id, f, "someone-else" if mode == "foreign" else by, close=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
