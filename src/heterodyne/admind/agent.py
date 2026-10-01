"""The persistent admin agent session (ADR 0001 §8; plan decision D8).

One interactive `claude` runs in tmux session `admin` on admind's private tmux server. Its session ID is
recorded in the store:
- `ensure_running` adopts a live session;
- it resumes the recorded ID (with `--resume`) once that ID has been seen to start;
- otherwise it launches with a fresh ID;
- three launches in a row without a SessionStart hook raise AgentStuck until `new()`.
It runs as the service user, unsandboxed, with permission prompts bypassed: a deliberate operator
decision (§8).
"""

import os
import uuid
from pathlib import Path
from typing import Protocol

from heterodyne.admind.hook import hook_command, settings_json
from heterodyne.admind.settings import AdmindSettings
from heterodyne.admind.store import Store
from heterodyne.agents.claude_code import interactive_argv

SESSION = "admin"
TMUX_SOCKET = "heterodyne-admind"
MAX_LAUNCHES_WITHOUT_START = 3


class AgentStuck(RuntimeError):
    pass


class TmuxLike(Protocol):
    def has_session(self, name: str) -> bool: ...
    def new_session(self, name: str, cwd: Path, argv: list[str]) -> None: ...
    def pane_dead(self, name: str) -> bool: ...
    def paste(self, name: str, text: str) -> None: ...
    def send_key(self, name: str, key: str) -> None: ...
    def capture(self, name: str, lines: int) -> str: ...
    def kill(self, name: str) -> None: ...


class AdminAgent:
    def __init__(self, tmux: TmuxLike, store: Store, settings: AdmindSettings, hook_socket: Path) -> None:
        self.tmux = tmux
        self.store = store
        self.settings = settings
        self.hook_socket = hook_socket
        self.settings_file = settings.state_dir / "claude-settings.json"

    @property
    def session_id(self) -> str | None:
        return self.store.get("agent_session")

    def alive(self) -> bool:
        return self.tmux.has_session(SESSION) and not self.tmux.pane_dead(SESSION)

    def _write_settings(self) -> None:
        tmp = self.settings_file.with_name(f".{self.settings_file.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(settings_json(hook_command(self.hook_socket)))
        tmp.replace(self.settings_file)

    def ensure_running(self) -> str:
        if self.alive() and self.session_id is not None:
            return "adopted"
        self.tmux.kill(SESSION)  # a dead pane kept for !tail, or a session admind has no record of
        launches = int(self.store.get("launches_without_start") or "0")
        if launches >= MAX_LAUNCHES_WITHOUT_START:
            raise AgentStuck(f"the admin agent did not start after {launches} launches; use !tail, then !new")
        sid = self.session_id
        resume = sid is not None and self.store.get("session_started") == sid
        if sid is None or not resume:
            sid = str(uuid.uuid4())
            self.store.set("agent_session", sid)
        self.store.set("launches_without_start", str(launches + 1))
        self._write_settings()
        argv = interactive_argv(self.settings.adapter_binary, self.settings.profile, session_id=sid,
                                resume=resume, settings_file=self.settings_file, name=SESSION)
        self.tmux.new_session(SESSION, self.settings.workdir, argv)
        return "resumed" if resume else "launched"

    def started(self, session_id: str) -> None:
        self.store.set("session_started", session_id)
        self.store.set("launches_without_start", "0")

    def new(self) -> str:
        self.tmux.kill(SESSION)
        self.store.delete("agent_session")
        self.store.set("launches_without_start", "0")
        return self.ensure_running()

    def send(self, text: str) -> None:
        self.tmux.paste(SESSION, text)

    def interrupt(self) -> None:
        self.tmux.send_key(SESSION, "Escape")

    def tail(self, lines: int) -> str:
        if not self.tmux.has_session(SESSION):
            return "(no admin agent session)"
        return self.tmux.capture(SESSION, lines)
