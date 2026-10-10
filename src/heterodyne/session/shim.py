"""ws-hook and ws-request (ADR 0001 §3.1, §10): the agent's side of the per-session socket.

Standard library only, with no heterodyne imports: inside the sandbox this file is run as
`python3 -I /run/hz/shim.py hook|request` from the read-only run directory. The socket is
$HZ_SESSION_SOCKET; the token and `shim.json` sit beside it, never in argv (plan 4 D5).

If wsd doesn't answer within `wait_seconds`, a PreToolUse is allowed only for a locally classifiable
`worktree_edit` (an edit tool on a path inside the worktree, while that class is auto-approved), and
denied otherwise; other events are spooled to $HOME/.hz/spool.jsonl, untrusted (plan 4 D14).

The hook input, the reply and $HOME are all the agent's to shape, so the shim never trusts them to be
well-formed or benign: `wait_seconds` bounds the whole exchange, the reply's length and every JSON text's
nesting are capped, and the spool is written only to a regular file, never through a link or into a FIFO.
Anything unexpected about a tool call denies it.

An event too large for one request line (the server's MAX_LINE) is sent, and spooled, as its projection:
its short top-level scalars, the short path fields of its `tool_input`, and a `{"truncated": true,
"bytes": N}` marker in place of everything else. The server keeps turn state from the event name and
session ID alone, so lifecycle events survive; the local `worktree_edit` check still reads the full
event. A tool call too large even projected is denied; another such event is dropped.
"""

import json
import math
import os
import socket
import stat
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

DENY_REASON = "control plane unavailable; retry shortly"
REFUSED_REASON = "the control plane refused this hook"
TOO_LARGE_REASON = "this tool call is too large for the control plane to check"
TOKEN_FILE = "token"  # noqa: S105 (a file name)
CONFIG_FILE = "shim.json"
EX_TEMPFAIL = 75
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SPOOL_CAP = 1 << 20
RETRY_SECONDS = 0.1
MAX_REPLY = 64 * 1024
MAX_DEPTH = 128                 # JSON nesting: deeper is malformed, before any recursive parse
MAX_LINE = 64 * 1024            # one request line, at most (ADR 0001 §7); the server's limit too
MAX_FIELD = 4096                # a scalar kept in a projection: a path, a name, an ID
PATH_FIELDS = ("file_path", "path", "notebook_path")
DEFAULT_WAIT = 5.0
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


def _wait(cfg: Mapping[str, Any]) -> float:
    wait = cfg.get("wait_seconds", DEFAULT_WAIT)
    if isinstance(wait, int | float) and not isinstance(wait, bool) and math.isfinite(wait) and wait >= 0:
        return float(wait)
    return DEFAULT_WAIT


def _exchange(sock: Path, line: bytes, end: float, clock: Clock) -> Any:
    """One connection: send `line`, read one reply line. Every step gets only what is left of the
    deadline, so a slow or trickling peer can't stretch it; the reply is capped in length and nesting."""
    with socket.socket(socket.AF_UNIX) as c:
        def arm() -> None:
            left = end - clock()
            if left <= 0:
                raise TimeoutError
            c.settimeout(left)

        arm()
        c.connect(str(sock))
        arm()
        c.sendall(line)
        data = b""
        while b"\n" not in data:
            if len(data) > MAX_REPLY:
                raise ValueError("reply too long")
            arm()
            chunk = c.recv(8192)
            if not chunk:
                break
            data += chunk
    reply = data.split(b"\n", 1)[0]
    if len(reply) > MAX_REPLY or json_depth(reply) > MAX_DEPTH:
        raise ValueError("reply too long or too deep")
    return json.loads(reply)


def _ask(sock: Path, message: dict[str, Any], wait: float, clock: Clock,
         sleep: Sleep) -> dict[str, Any] | None:
    """wsd's reply, retried until `wait` seconds have passed in all; None if there is none by then."""
    end = clock() + wait
    line = _encode(message) + b"\n"
    while True:
        try:
            reply = _exchange(sock, line, end, clock)
            return cast(dict[str, Any], reply) if isinstance(reply, dict) else None
        except (OSError, ValueError):
            left = end - clock()
            if left <= 0:
                return None
            sleep(min(RETRY_SECONDS, left))

def _encode(value: Any) -> bytes:
    return json.dumps(value).encode()


def _short(value: Any) -> bool:
    return value is None or isinstance(value, bool | int | float) or (isinstance(value, str)
                                                                    and len(value) <= MAX_FIELD)


def _marker(value: Any) -> dict[str, Any]:
    return {"truncated": True, "bytes": len(_encode(value))}


def _project(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The event's short top-level scalars and `tool_input` paths; a marker for everything else."""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key == "tool_input" and isinstance(value, dict):
            tool_input = cast(dict[str, Any], value)
            paths = {name: tool_input[name] for name in PATH_FIELDS
                     if isinstance(tool_input.get(name), str) and _short(tool_input[name])}
            out[key] = {**paths, **_marker(value)}
        else:
            out[key] = value if _short(value) else _marker(value)
    return out


def _bounded(wrap: Callable[[dict[str, Any]], dict[str, Any]],
             fields: dict[str, Any]) -> dict[str, Any] | None:
    """The message `wrap` makes of the event, or of its projection if the event's is too long for one
    line; None if even that is."""
    for payload in (fields, _project(fields)):
        message = wrap(payload)
        if len(_encode(message)) <= MAX_LINE:
            return message
    return None


def _local_edit(payload: Mapping[str, Any], cfg: Mapping[str, Any]) -> bool:
    """A `worktree_edit`: an edit tool on a path whose real path is inside the worktree, while that class
    is in `local_classes`. A relative path counts only against the hook's own absolute `cwd`."""
    tool = payload.get("tool_name")
    classes = cfg.get("local_classes")
    if not isinstance(tool, str) or tool not in EDIT_TOOLS:
        return False
    if not isinstance(classes, list) or "worktree_edit" not in classes:
        return False
    worktree = cfg.get("worktree")
    tool_input = payload.get("tool_input")
    if not isinstance(worktree, str) or not Path(worktree).is_absolute() or not isinstance(tool_input, dict):
        return False
    fields = cast(dict[str, Any], tool_input)
    target = fields.get("file_path", fields.get("notebook_path"))
    if not isinstance(target, str) or not target:
        return False
    if not Path(target).is_absolute():
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            return False
        target = str(Path(cwd) / target)
    try:
        root = os.path.realpath(worktree)
        real = os.path.realpath(target)
        return os.path.commonpath([real, root]) == root
    except (OSError, ValueError):       # a NUL, an unresolvable path
        return False

def _spool(env: Mapping[str, str], payload: dict[str, Any]) -> None:
    """Append the event (or its projection) to $HOME/.hz/spool.jsonl, capped, best effort. $HOME is the
    agent's: the file is opened without following a link and without blocking, and written only if it is a
    regular file."""
    home = env.get("HOME")
    if not home or not Path(home).is_absolute():
        return
    folder = Path(home) / ".hz"
    try:
        message = _bounded(lambda p: {"spooled": True, "payload": p}, payload)
        if message is None:
            return
        line = _encode(message) + b"\n"
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fd = os.open("spool.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        try:
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode) and st.st_size + len(line) <= SPOOL_CAP:
                os.write(fd, line)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        pass

def run_hook(raw: bytes, env: Mapping[str, str], *, clock: Clock = time.monotonic,
             sleep: Sleep = time.sleep) -> tuple[int, str]:
    """Exit 0 always; a denied tool call is the JSON decision on stdout. Input that can't be read as a
    hook event (not JSON, nested too deeply) is denied, as is a tool call the shim fails on."""
    try:
        payload: Any = json.loads(raw) if json_depth(raw) <= MAX_DEPTH else None
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        return 0, _deny(DENY_REASON)
    fields = cast(dict[str, Any], payload)
    tool_call = fields.get("hook_event_name") == "PreToolUse"
    try:
        return _decide(fields, tool_call, env, clock, sleep)
    except Exception:  # noqa: BLE001 - fail closed on anything unforeseen
        return 0, _deny(DENY_REASON) if tool_call else ""


def _decide(fields: dict[str, Any], tool_call: bool, env: Mapping[str, str], clock: Clock,
            sleep: Sleep) -> tuple[int, str]:
    found = _config(env)
    if found is None:
        if tool_call:
            return 0, _deny(DENY_REASON)
        _spool(env, fields)
        return 0, ""
    sock, token, cfg = found
    message = _bounded(lambda p: {"token": token, "type": "hook_event", "payload": p}, fields)
    if message is None:
        return 0, _deny(TOO_LARGE_REASON) if tool_call else ""
    reply = _ask(sock, message, _wait(cfg), clock, sleep)
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
    reply = _ask(sock, {"token": token, "type": "ws_request", "payload": {"text": " ".join(words)}},
                 _wait(cfg), clock, sleep)
    if reply is None:
        return EX_TEMPFAIL, DENY_REASON
    return (0 if reply.get("ok") is True else 1), json.dumps(reply)


def json_depth(line: bytes) -> int:
    """The deepest array or object nesting in a JSON text, counted outside strings. Parsing and spooling
    recurse, so a deep text is refused before either sees it (a thread's stack may be small)."""
    depth = deepest = 0
    in_string = escaped = False
    for byte in line:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:              # backslash
                escaped = True
            elif byte == 0x22:              # quote
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):          # [ {
            depth += 1
            deepest = max(deepest, depth)
        elif byte in (0x5D, 0x7D):          # ] }
            depth -= 1
    return deepest


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
