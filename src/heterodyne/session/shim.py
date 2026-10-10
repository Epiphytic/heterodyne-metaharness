"""ws-hook and ws-request (ADR 0001 §3.1, §10): the agent's side of the per-session socket.

Standard library only, with no heterodyne imports: inside the sandbox this file is run as
`python3 -I /run/hz/shim.py hook|request` from the read-only run directory. The socket is
$HZ_SESSION_SOCKET; the token and `shim.json` sit beside it, never in argv (plan 4 D5).

If wsd doesn't answer within `wait_seconds`, a PreToolUse is allowed only for a locally classifiable
`worktree_edit` (an edit tool on a path inside the worktree, while that class is auto-approved), and
denied otherwise; other events are spooled to $HOME/.hz/spool.jsonl, untrusted (plan 4 D14).
"""

import json
import os
import socket
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

DENY_REASON = "control plane unavailable; retry shortly"
REFUSED_REASON = "the control plane refused this hook"
TOKEN_FILE = "token"  # noqa: S105 (a file name)
CONFIG_FILE = "shim.json"
EX_TEMPFAIL = 75
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SPOOL_CAP = 1 << 20
RETRY_SECONDS = 0.1
REQUEST_WRAPPER = '#!/bin/sh\nexec python3 -I /run/hz/shim.py request "$@"\n'

Clock = Callable[[], float]
Sleep = Callable[[float], None]


def _deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}})


def _config(env: Mapping[str, str]) -> tuple[Path, str, dict[str, Any]] | None:
    sock = env.get("HZ_SESSION_SOCKET")
    if not sock:
        return None
    try:
        token = (Path(sock).parent / TOKEN_FILE).read_text().strip()
        cfg: Any = json.loads((Path(sock).parent / CONFIG_FILE).read_text())
    except (OSError, ValueError):
        return None
    return (Path(sock), token, cast(dict[str, Any], cfg)) if isinstance(cfg, dict) else None


def _ask(sock: Path, message: dict[str, Any], wait: float, clock: Clock,
         sleep: Sleep) -> dict[str, Any] | None:
    end = clock() + wait
    line = (json.dumps(message) + "\n").encode()
    while True:
        try:
            with socket.socket(socket.AF_UNIX) as c:
                c.settimeout(max(0.5, wait))
                c.connect(str(sock))
                c.sendall(line)
                reply: Any = json.loads(c.makefile("rb").readline())
            return cast(dict[str, Any], reply) if isinstance(reply, dict) else None
        except (OSError, ValueError):
            if clock() >= end:
                return None
            sleep(RETRY_SECONDS)


def _local_edit(payload: Mapping[str, Any], cfg: Mapping[str, Any]) -> bool:
    if payload.get("tool_name") not in EDIT_TOOLS or "worktree_edit" not in cfg.get("local_classes", []):
        return False
    worktree = cfg.get("worktree")
    tool_input = payload.get("tool_input")
    if not isinstance(worktree, str) or not isinstance(tool_input, dict):
        return False
    fields = cast(dict[str, Any], tool_input)
    target = fields.get("file_path", fields.get("notebook_path"))
    if not isinstance(target, str) or not target:
        return False
    root = os.path.realpath(worktree)
    real = os.path.realpath(Path(root) / target)
    return os.path.commonpath([real, root]) == root


def _spool(env: Mapping[str, str], payload: Any) -> None:
    home = env.get("HOME")
    if not home:
        return
    try:
        folder = Path(home) / ".hz"
        folder.mkdir(mode=0o700, exist_ok=True)
        spool = folder / "spool.jsonl"
        line = (json.dumps({"spooled": True, "payload": payload}) + "\n").encode()
        if (spool.stat().st_size if spool.exists() else 0) + len(line) <= SPOOL_CAP:
            with spool.open("ab") as fh:
                fh.write(line)
    except OSError:
        pass


def run_hook(raw: bytes, env: Mapping[str, str], *, clock: Clock = time.monotonic,
             sleep: Sleep = time.sleep) -> tuple[int, str]:
    try:
        payload: Any = json.loads(raw)
    except ValueError:
        payload = {}
    fields = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
    tool_call = fields.get("hook_event_name") == "PreToolUse"
    found = _config(env)
    if found is None:
        return 0, _deny(DENY_REASON) if tool_call else ""
    sock, token, cfg = found
    wait = cfg.get("wait_seconds", 5)
    wait = float(wait) if isinstance(wait, int | float) and not isinstance(wait, bool) else 5.0
    reply = _ask(sock, {"token": token, "type": "hook_event", "payload": fields}, wait, clock, sleep)
    if reply is None:
        if tool_call:
            return 0, "" if _local_edit(fields, cfg) else _deny(DENY_REASON)
        _spool(env, fields)
        return 0, ""
    if not tool_call:
        return 0, ""
    if reply.get("ok") is not True:
        return 0, _deny(REFUSED_REASON)
    if reply.get("decision") == "deny":
        reason = reply.get("reason")
        return 0, _deny(reason if isinstance(reason, str) and reason else REFUSED_REASON)
    return 0, ""


def run_request(words: Sequence[str], env: Mapping[str, str], *, clock: Clock = time.monotonic,
                sleep: Sleep = time.sleep) -> tuple[int, str]:
    found = _config(env)
    if found is None:
        return EX_TEMPFAIL, DENY_REASON
    sock, token, cfg = found
    wait = cfg.get("wait_seconds", 5)
    wait = float(wait) if isinstance(wait, int | float) and not isinstance(wait, bool) else 5.0
    reply = _ask(sock, {"token": token, "type": "ws_request", "payload": {"text": " ".join(words)}},
                 wait, clock, sleep)
    if reply is None:
        return EX_TEMPFAIL, DENY_REASON
    return (0 if reply.get("ok") is True else 1), json.dumps(reply)


def hook_main() -> int:
    rc, out = run_hook(sys.stdin.buffer.read(), os.environ)
    if out:
        print(out)
    return rc


def request_main(argv: Sequence[str] | None = None) -> int:
    rc, out = run_request(list(sys.argv[1:] if argv is None else argv), os.environ)
    print(out)
    return rc


def main(argv: Sequence[str]) -> int:
    if argv[:1] == ["hook"]:
        return hook_main()
    if argv[:1] == ["request"]:
        return request_main(argv[1:])
    print("usage: shim.py hook|request [text...]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
