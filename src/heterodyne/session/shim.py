"""ws-hook and ws-request (ADR 0001 §3.1, §10): the agent's side of the per-session socket.

Standard library only, with no heterodyne imports: inside the sandbox this file is run as
`python3 -I /run/hz/shim.py hook <Event>|request|status` from the read-only run directory. The socket is
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

The hook's input is bounded too (btq-rcose): at most MAX_HOOK_INPUT bytes, ending within HOOK_INPUT_WAIT
seconds, after which a hook the CLI cancels at its timeout would let the tool run. Input past the bound is
drained to its end, or to the deadline, so the CLI's write completes. The event comes from the installed
command's argument, which is as trusted as the hook itself, so bad input is answered by event without
parsing anything: a PreToolUse is denied (too large, or unreadable: late, empty, no stdin, not JSON, or a
payload whose tool-call status disagrees with the argument) and a lifecycle event gets empty output. No
argument, an unknown one or extra words fail closed as PreToolUse on bad input; a readable payload is
then taken by its own `hook_event_name`, as before. Nothing bad is sent to wsd or spooled.
"""

import hashlib
import json
import math
import os
import select
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
MAX_HOOK_INPUT = 16 << 20       # ~45x the largest payload seen, 4x a 1M-token context: cuts only a runaway
HOOK_INPUT_WAIT = 5.0           # the CLI writes from memory at spawn: only a stalled writer is this late
DRAIN_CHUNK = 64 * 1024         # past the bound, input is read into a fixed scratch buffer and dropped
KNOWN_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "Stop", "StopFailure", "PreToolUse"})
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


def _ask(sock: Path, message: dict[str, Any] | bytes, wait: float, clock: Clock,
         sleep: Sleep) -> dict[str, Any] | None:
    """wsd's reply, retried until `wait` seconds have passed in all; None if there is none by then."""
    end = clock() + wait
    line = (message if isinstance(message, bytes) else _encode(message)) + b"\n"
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

def run_hook(raw: bytes, env: Mapping[str, str], *, event: str | None = None, clock: Clock = time.monotonic,
             sleep: Sleep = time.sleep) -> tuple[int, str]:
    """Exit 0 always; a denied tool call is the JSON decision on stdout. `event` is the command's argument
    (None: none, or not one we know). Input over MAX_HOOK_INPUT is answered unparsed, and input that can't
    be read as a hook event (empty, not JSON, nested too deeply, or a tool call where `event` says
    otherwise, or the reverse) takes the unreadable path: denied for a tool call, silent for a lifecycle
    event. So is a tool call the shim fails on."""
    tool_event = event in (None, "PreToolUse")      # no argument: fail closed
    if len(raw) > MAX_HOOK_INPUT:
        return 0, _deny(TOO_LARGE_REASON) if tool_event else ""
    try:
        payload: Any = json.loads(raw) if json_depth(raw) <= MAX_DEPTH else None
    except (ValueError, RecursionError):
        payload = None
    fields = cast(dict[str, Any], payload) if isinstance(payload, dict) else None
    tool_call = fields is not None and fields.get("hook_event_name") == "PreToolUse"
    if fields is None or (event is not None and (event == "PreToolUse") != tool_call):
        return 0, _deny(DENY_REASON) if tool_event or tool_call else ""
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


# The installed shim is a standalone stdlib file; the host uses this same codec.
CONTROL_SCHEMA = "hermes.manager-control.v1"
CONTROL_BYTES = 16 * 1024
CONTROL_REPLY_BYTES = 32 * 1024
CONTROL_DEPTH = 16
CONTROL_FIELDS = {
    "Status": frozenset(), "Boundary": frozenset(),
    "ListManagerRequests": frozenset({"cursor"}),
    "Evidence": frozenset({"issue_id"}),
    "Ask": frozenset({"key", "summary", "text"}),
    "Escalate": frozenset({"issue_id", "reason"}),
    "ResolveSigned": frozenset({"issue_id", "event"}),
    "ReadOutcome": frozenset({"origin_generation", "request_id"}),
}


def strict_json(raw: bytes) -> Any:
    """No replacement UTF-8, duplicate object keys, nonfinite values or surrogate text."""
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError("nonfinite JSON")

    text = raw.decode("utf-8", "strict")
    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    # ensure_ascii=False exposes escaped lone surrogates without rewriting valid input.
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    return value


def control_uuid(value: Any) -> bool:
    import uuid
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _control_text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value.encode("utf-8")) <= limit


def _control_identifier(value: Any) -> bool:
    import re
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is not None


def _control_event(event: Any) -> bool:
    import re
    fields = {"id", "pubkey", "created_at", "kind", "tags", "content", "sig"}
    if not isinstance(event, dict) or set(event) != fields:
        return False
    for key, size in (("id", 64), ("pubkey", 64), ("sig", 128)):
        if not isinstance(event[key], str) or re.fullmatch("[0-9a-f]{" + str(size) + "}", event[key]) is None:
            return False
    return (type(event["created_at"]) is int and event["created_at"] >= 0
            and type(event["kind"]) is int and 0 <= event["kind"] <= 65535
            and isinstance(event["content"], str) and isinstance(event["tags"], list)
            and all(isinstance(tag, list) and all(isinstance(x, str) for x in tag) for tag in event["tags"]))


def _control_arguments(operation: str, args: dict[str, Any]) -> bool:
    import re
    if "issue_id" in args and not _control_identifier(args["issue_id"]):
        return False
    if operation == "Ask":
        key = args["key"]
        return (isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key) is not None
                and _control_text(args["summary"], 256) and _control_text(args["text"], 4096))
    if operation == "Escalate":
        return _control_text(args["reason"], 4096)
    if operation == "ResolveSigned":
        return _control_event(args["event"])
    if operation == "ReadOutcome":
        return (type(args["origin_generation"]) is int and 0 < args["origin_generation"] < 2**63
                and control_uuid(args["request_id"]))
    if operation == "ListManagerRequests":
        return args["cursor"] is None or _control_text(args["cursor"], 256)
    return True


def parse_control(raw: bytes) -> dict[str, Any]:
    """Validate untrusted intent; this conveys no operator or native authority."""
    if len(raw) > MAX_LINE or json_depth(raw) > CONTROL_DEPTH:
        raise ValueError("control request exceeds bounds")
    request = strict_json(raw)
    if (not isinstance(request, dict) or set(request) != {"schema", "request_id", "operation", "arguments"}
            or request["schema"] != CONTROL_SCHEMA or not control_uuid(request["request_id"])):
        raise ValueError("invalid control request")
    op, args = request["operation"], request["arguments"]
    if (not isinstance(op, str) or op not in CONTROL_FIELDS or not isinstance(args, dict)
            or set(args) != CONTROL_FIELDS[op] or not _control_arguments(op, args)):
        raise ValueError("invalid control operation or arguments")
    if op != "ResolveSigned" and len(raw) > CONTROL_BYTES:
        raise ValueError("control request exceeds ordinary bound")
    return request


def run_control(raw: bytes, env: Mapping[str, str], *, clock: Clock = time.monotonic,
                sleep: Sleep = time.sleep) -> tuple[int, str]:
    """Structured fixed-endpoint client: no text argv, socket override or truncation."""
    try:
        parse_control(raw)
        # One exact JSON value in the existing newline-framed transport. Refuse
        # unsupported framing instead of changing the manager's retained bytes.
        if raw != raw.strip() or b"\n" in raw or b"\r" in raw:
            raise ValueError("control input must be one unpadded JSON object line")
    except (ValueError, UnicodeError, RecursionError):
        return 2, json.dumps({"ok": False, "error": "operator-route-required"})
    found = _config(env)
    if found is None:
        return EX_TEMPFAIL, DENY_REASON
    sock, token, cfg = found
    message = b'{"token":' + _encode(token) + b',"type":"ws_request","payload":' + raw + b'}'
    if len(message) + 1 > MAX_LINE:
        return 2, json.dumps({"ok": False, "error": "operator-route-required"})
    reply = _ask(sock, message, _wait(cfg), clock, sleep)
    if reply is None:
        return EX_TEMPFAIL, DENY_REASON
    encoded = json.dumps(reply)
    if len(encoded.encode()) + 1 > CONTROL_REPLY_BYTES:
        return 2, json.dumps({"ok": False, "error": "operator-route-required"})
    return (0 if reply.get("ok") is True else 1), encoded


def control_main() -> int:
    raw = read_bounded(sys.stdin.fileno(), MAX_LINE, DEFAULT_WAIT)
    code, output = run_control(raw, os.environ)
    print(output)
    return code


STATUS_FILE = "status.json"     # in $HOME/.hz: what wsd last accepted, from which launch, and when
STATUS_WAIT = 2.0               # the status line redraws often: never hold it longer than this
STATUS_RESEND = 30.0            # an unchanged value is sent again this often (wall seconds): well inside
                                # the smallest stale window wsd allows (usage.stale_minutes >= 1)


def _launch(token: str) -> str:
    """Which launch the status was sent to, without keeping its token: each generation has its own."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _recent(sent: object, now: float) -> bool:
    if isinstance(sent, bool) or not isinstance(sent, int | float):
        return False
    return 0 <= now - sent < STATUS_RESEND          # a clock that went back is never recent


def _status_dir(env: Mapping[str, str]) -> int | None:
    """$HOME/.hz, opened without following a link; None if $HOME isn't absolute."""
    home = env.get("HOME")
    if not home or not Path(home).is_absolute():
        return None
    folder = Path(home) / ".hz"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _last_status(env: Mapping[str, str]) -> dict[str, Any] | None:
    try:
        dir_fd = _status_dir(env)
        if dir_fd is None:
            return None
        try:
            fd = os.open(STATUS_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        try:
            raw = os.read(fd, MAX_LINE + 1) if stat.S_ISREG(os.fstat(fd).st_mode) else b""
        finally:
            os.close(fd)
        data: Any = json.loads(raw) if raw and len(raw) <= MAX_LINE and json_depth(raw) <= MAX_DEPTH else None
    except (OSError, ValueError):
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


def _save_status(env: Mapping[str, str], text: str) -> None:
    try:
        dir_fd = _status_dir(env)
        if dir_fd is None:
            return
        try:
            fd = os.open(STATUS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                os.write(fd, text.encode())
        finally:
            os.close(fd)
    except (OSError, ValueError):
        pass


def run_status(raw: bytes, env: Mapping[str, str], *, clock: Clock = time.monotonic,
               sleep: Sleep = time.sleep, wall: Clock = time.time) -> int:
    """Claude's status line (plan 4b E7): send the input's `rate_limits` as a StatusLine hook event and
    print nothing. A value is sent when it differs from what this launch's wsd last accepted, when it
    went to another launch (a relaunch shares $HOME), or when STATUS_RESEND has passed since, so an
    unchanged reading never goes stale in wsd's cache. A value wsd didn't accept is sent again on the
    next redraw. Always exits 0: the status line decides nothing."""
    try:
        payload: Any = json.loads(raw) if len(raw) <= MAX_LINE and json_depth(raw) <= MAX_DEPTH else None
        if not isinstance(payload, dict):
            return 0
        fields = cast(dict[str, Any], payload)
        limits: Any = fields.get("rate_limits")
        if not isinstance(limits, dict):
            return 0                    # the first call, before any API response, has none (S7)
        text = json.dumps(limits, sort_keys=True)
        found = _config(env)
        if found is None:
            return 0
        sock, token, cfg = found
        launch, now = _launch(token), wall()
        last = _last_status(env)
        if (last is not None and last.get("launch") == launch and last.get("limits") == text
                and _recent(last.get("sent"), now)):
            return 0
        event: dict[str, Any] = {"hook_event_name": "StatusLine", "session_id": fields.get("session_id"),
                                 "rate_limits": limits}
        message = _bounded(lambda p: {"token": token, "type": "hook_event", "payload": p}, event)
        if message is None:
            return 0
        reply = _ask(sock, message, min(_wait(cfg), STATUS_WAIT), clock, sleep)
        if reply is not None and reply.get("ok") is True:
            _save_status(env, json.dumps({"launch": launch, "limits": text, "sent": now}))
    except Exception:  # noqa: BLE001, S110 - a status line never fails the session
        pass
    return 0


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


def hook_main(args: Sequence[str] | None = None) -> int:
    """`ws-hook` (pyproject) calls this with no arguments: its event, if any, is then on its command line."""
    args = list(sys.argv[1:] if args is None else args)
    event = args[0] if len(args) == 1 and args[0] in KNOWN_EVENTS else None
    try:
        raw = read_bounded(0, MAX_HOOK_INPUT, HOOK_INPUT_WAIT, drain=True)
    except OSError:                     # no stdin: closed (sys.stdin is None under `exec 0<&-`) or invalid
        raw = b""
    rc, out = run_hook(raw, os.environ, event=event)
    if out:
        print(out)
    return rc


def request_main(argv: Sequence[str] | None = None) -> int:
    rc, out = run_request(list(sys.argv[1:] if argv is None else argv), os.environ)
    print(out)
    return rc


def read_bounded(fd: int, limit: int, seconds: float, clock: Clock = time.monotonic, *,
                 drain: bool = False) -> bytes:
    """`fd`'s input up to its end, cut at `limit` + 1 bytes (too long to be read as one line), or b"" if
    it hasn't ended within `seconds`: neither an endless nor a stalled stream can hold the caller. With
    `drain`, input past the cut is read and dropped until its end or the deadline, so its writer isn't
    left on a closed pipe; the cut is returned either way. The deadline is checked before every poll and
    again before every read, so input the poll reports only after the deadline is late however whole."""
    end = clock() + seconds
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    data = bytearray()
    while True:
        left = end - clock()
        # a poll that returns past the deadline (or a process resumed past it) is late: never read on
        if left <= 0 or not poller.poll(max(1, int(left * 1000))) or clock() >= end:
            return bytes(data) if len(data) > limit else b""
        if len(data) > limit:
            if not os.read(fd, DRAIN_CHUNK):
                break
            continue
        chunk = os.read(fd, limit + 1 - len(data))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > limit and not drain:
            break
    return bytes(data)


def status_main() -> int:
    return run_status(read_bounded(sys.stdin.fileno(), MAX_LINE, STATUS_WAIT), os.environ)


def main(argv: Sequence[str]) -> int:
    if argv[:1] == ["hook"]:
        return hook_main(argv[1:])
    if argv[:1] == ["request"]:
        return request_main(argv[1:])
    if argv[:1] == ["status"]:
        return status_main()
    if list(argv) == ["control"]:
        return control_main()
    print("usage: shim.py hook <Event>|request|status [text...]|control", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
