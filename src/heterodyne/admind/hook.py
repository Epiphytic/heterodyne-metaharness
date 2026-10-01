"""The admin agent's hooks reach admind over a private Unix socket (ADR 0001 §2, §8).

Claude Code runs `python -m heterodyne.admind hook --socket <path>` on SessionStart, UserPromptSubmit
and Stop. The hook forwards its stdin JSON as one line, waits (up to 2s) for admind's `ok`, and **always
exits 0** with nothing on stdout: a Stop hook that exits 2 would block the agent from stopping, a
UserPromptSubmit hook's stdout would be added to the prompt, and admind being down must never wedge the
admin agent (it is the recovery path). Waiting for the `ok` means admind has queued each event before
Claude Code moves on, so events reach admind in the order the agent produced them.
The agent's reply is the Stop hook's `last_assistant_message`. If a Claude Code build omits that
optional field, the fallback is the last assistant text in the session's own transcript file
(plan decision D2). The screen is never scraped.

Trust boundary: the socket is 0600 in a 0700 directory, so only the service user can write to it, and
events for any session but the current one are dropped. A process already running as the service user
can still forge a reply event; that is the same-user residual risk ADR §3.4 accepts.
"""

import argparse
import asyncio
import contextlib
import json
import os
import shlex
import socket
import sys
from pathlib import Path
from typing import Any, cast

import msgspec

from heterodyne.admind.audit import Audit

MAX_HOOK_FRAME = 1024 * 1024
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")


class HookEvent(msgspec.Struct, frozen=True):
    hook_event_name: str
    session_id: str
    transcript_path: str | None = None
    last_assistant_message: str | None = None
    prompt: str | None = None


def same_prompt(a: str, b: str) -> bool:
    """Whether a UserPromptSubmit `prompt` is the text admind pasted (whitespace-insensitive)."""
    return a.split() == b.split()


def hook_command(sock: Path) -> str:
    return f"{shlex.quote(sys.executable)} -m heterodyne.admind hook --socket {shlex.quote(str(sock))}"


def settings_json(command: str) -> str:
    entry = [{"hooks": [{"type": "command", "command": command}]}]
    return json.dumps({"hooks": {event: entry for event in HOOK_EVENTS}}, indent=2)


def hook_main(argv: list[str], stdin: bytes) -> int:
    parser = argparse.ArgumentParser(prog="admind hook", add_help=False)
    parser.add_argument("--socket")
    try:
        args, _ = parser.parse_known_args(argv)
    except SystemExit:      # argparse exits 2 on a malformed flag; a hook never fails the agent
        args = argparse.Namespace(socket=None)
    if not args.socket:
        print("admind hook: --socket is required", file=sys.stderr)
        return 0
    try:
        line = json.dumps(json.loads(stdin), separators=(",", ":")).encode() + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(2)
            conn.connect(args.socket)
            conn.sendall(line)
            conn.recv(16)   # admind's "ok": the event is queued (or a timeout; either way, exit 0)
    except (OSError, ValueError) as exc:
        print(f"admind hook: not delivered ({type(exc).__name__})", file=sys.stderr)
    return 0


def _blocks(record: dict[str, Any]) -> list[dict[str, Any]] | str | None:
    message: Any = record.get("message")
    content: Any = cast(dict[str, Any], message).get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [cast(dict[str, Any], b) for b in cast(list[Any], content) if isinstance(b, dict)]
    return None


def last_assistant_text(path: Path) -> str:
    """The last assistant text of the **current turn**: text before the latest real user prompt belongs
    to an earlier turn and is never returned (a silent turn must not resend an old reply)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    text = ""
    for line in lines:
        try:
            record: Any = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        kind = cast(dict[str, Any], record).get("type")
        blocks = _blocks(cast(dict[str, Any], record))
        if kind == "user":
            # A tool result is also a "user" record; only a prompt (text) starts a new turn.
            if isinstance(blocks, str) or (blocks and any(b.get("type") == "text" for b in blocks)):
                text = ""
        elif kind == "assistant" and isinstance(blocks, list):
            parts = [b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)]
            if parts:
                text = "\n".join(parts)
    return text


def reply_text(ev: HookEvent) -> str:
    if ev.last_assistant_message:
        return ev.last_assistant_message
    if ev.transcript_path:
        path = Path(ev.transcript_path)
        if path.name == f"{ev.session_id}.jsonl":
            return last_assistant_text(path)
    return ""


class HookServer:
    def __init__(self, path: Path, queue: asyncio.Queue[HookEvent], audit: Audit) -> None:
        self.path = path
        self.queue = queue
        self.audit = audit
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        old = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._handle, path=str(self.path),
                                                           limit=MAX_HOOK_FRAME + 1)
        finally:
            os.umask(old)
        self.path.chmod(0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)
        self.path.unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            await self.queue.put(msgspec.json.decode(line, type=HookEvent))
            writer.write(b"ok\n")
            await writer.drain()
        except (TimeoutError, ValueError, OSError, msgspec.DecodeError) as exc:
            self.audit.write("hook", result="dropped", error=type(exc).__name__)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
