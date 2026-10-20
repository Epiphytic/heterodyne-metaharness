"""The source-owned persistent admin agent.

A matching startup acknowledgement establishes current readiness, never future
resumability. Automatic replacement/resume is default-denied because a supported
positive eligibility producer is absent. Existing authenticated !new requests one
fresh launch and resets the outer three-launch ceiling. All automatic callers use
ensure_running; its refusal occurs before kill, new identity or another attempt.
"""

import os
import shutil
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from heterodyne.admind.hook import hook_command, new_launch_nonce, settings_json
from heterodyne.admind.settings import AdmindSettings
from heterodyne.admind.store import Store
from heterodyne.agents.claude_code import interactive_argv

SESSION = "admin"
TMUX_SOCKET = "heterodyne-admind"
TMUX_SCOPE = "heterodyne-admind-tmux"  # prefix of the transient systemd scopes that hold the tmux server
MAX_LAUNCHES_WITHOUT_START = 3


def tmux_launcher(settings: AdmindSettings) -> Callable[[], tuple[str, ...]] | None:
    """A factory for the command prefix that starts the private tmux server outside admind's cgroup.

    On systemd the unit uses KillMode=control-group, so a server forked from admind would die with every
    stop or restart, taking the agent with it. A transient scope puts the server in a cgroup of its own;
    each start gets a unique scope name, so a predecessor still being collected cannot collide.
    On launchd nothing is needed (None): tmux daemonises with setsid and launchd does not kill by cgroup."""
    if settings.service_manager != "systemd":
        return None
    return lambda: (
        shutil.which("systemd-run") or "systemd-run",
        "--user",
        "--scope",
        "--collect",
        "--quiet",
        f"--unit={TMUX_SCOPE}-{uuid.uuid4().hex[:12]}",
        "--description=heterodyne admind private tmux server (admin agent)",
    )


class AgentStuck(RuntimeError):
    pass


class TmuxLike(Protocol):
    def session_absent(self, name: str) -> bool: ...
    def has_session(self, name: str) -> bool: ...
    def new_session(
        self,
        name: str,
        cwd: Path,
        argv: list[str],
        set: Mapping[str, str] | None = None,
        unset: Sequence[str] = (),
    ) -> None: ...
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

    @property
    def launch_nonce(self) -> str | None:
        """The nonce of the current launch, persisted so an adopted pane keeps its own."""
        return self.store.get("launch_nonce")

    def invalidate_launch(self) -> None:
        """Forget the departing launch's nonce, synchronously, before a replacement is started in a
        thread: until the replacement has its own nonce, no event validates as current."""
        self.store.delete("launch_nonce")

    def alive(self) -> bool:
        return not self.tmux.session_absent(SESSION) and not self.tmux.pane_dead(SESSION)

    def _write_settings(self) -> None:
        tmp = self.settings_file.with_name(f".{self.settings_file.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(settings_json(hook_command(self.hook_socket, self.launch_nonce or "")))
        tmp.replace(self.settings_file)

    def login_matches(self) -> bool:
        """Whether the current pane's launch ran on the configured login. A store from before AU-11 has no
        key: that is unknown, never assumed to be the default."""
        return self.store.get("agent_account") == self.settings.agent_account.key

    def replace_pending(self) -> str | None:
        """Why an earlier run began replacing the pane without finishing (a fixed word), or None."""
        return self.store.get("replace_pending")

    def startup_acknowledged(self) -> bool:
        """Current launch acknowledgement only; never a persistence/resume capability."""
        nonce = self.launch_nonce
        return (
            nonce is not None
            and self.login_matches()
            and self.store.get("launch_kind") == "startup"
            and self.store.get("launch_session") == self.session_id
            and self.store.get("launch_ack_nonce") == nonce
            and self.store.get("launch_ack_session") == self.session_id
            and self.store.get("launch_hold") is None
        )

    def recovery_status(self) -> str:
        """Public fixed vocabulary; never disclose persisted identities or account paths."""
        origin = self.store.get("conversation_origin")
        conversation = (
            "explicitly replaced"
            if origin == "explicitly-replaced"
            else ("original" if origin == "original" else "origin unknown")
        )
        if self.store.get("launch_hold") is not None:
            return (
                f"HOLD; conversation {conversation}; future resume unqualified; authenticated !new required"
            )
        state = "startup-ready" if self.startup_acknowledged() else "startup acknowledgement pending"
        return state + f"; conversation {conversation}; future resume unqualified"

    def hold_launch(self, why: str) -> None:
        """A source-owned terminal launch hold; only authenticated !new clears it."""
        if self.store.get("launch_hold") is None:
            self.store.set("launch_hold", why)

    def _refuse_launch(self, why: str) -> None:
        self.hold_launch(why)
        raise AgentStuck(f"admin agent recovery held ({why}); use !tail, then !new")

    def ensure_running(self, relaunch: bool = False) -> str:
        """Adopt a current pane or launch a first/explicitly requested fresh startup.

        No supported positive resume-eligibility producer exists. A recorded session,
        SessionStart or caller-written eligibility flag cannot authorize automatic
        resume/replacement. Refuse BEFORE kill, identity mutation or attempt spending.
        An issued fresh launch is never replayed after a crash, including pending !new.
        """
        if self.store.get("launch_hold") is not None:
            raise AgentStuck("admin agent recovery held; use !tail, then !new")
        pending = self.replace_pending()
        if (
            not relaunch
            and pending is None
            and self.store.get("launch_issued") is None
            and self.alive()
            and self.login_matches()
            and self.session_id is not None
            and self.launch_nonce is not None
        ):
            return "adopted"
        intent = self.store.get("launch_new_intent")
        if pending == "new" and intent is not None:
            self._refuse_launch("launch-unacknowledged")
        recorded = any(
            self.store.get(key) is not None
            for key in (
                "agent_session",
                "session_started",
                "launch_nonce",
                "launch_kind",
                "launch_session",
                "agent_account",
                "launches_without_start",
                "launch_issued",
                "launch_new_intent",
                "launch_attempt_intent",
                "launch_ack_nonce",
                "launch_ack_session",
                "launch_resume_eligibility",
            )
        )
        if relaunch or pending is not None or recorded:
            self._refuse_launch("resume-eligibility-unqualified")
        return self._request_startup(fresh=False)

    def _request_startup(self, *, fresh: bool) -> str:
        """Issue durably once; only the explicit new() path may request replacement.

        The new intent and its consumption commit together with the launch binding,
        before any external effect. Automatic entrypoints never infer replacement
        permission from a persisted pending intent.
        """
        launches = int(self.store.get("launches_without_start") or "0")
        if fresh:
            launches = 0
        if launches >= MAX_LAUNCHES_WITHOUT_START:
            self._refuse_launch("launch-budget-exhausted")
        sid = str(uuid.uuid4())
        account = self.settings.agent_account
        with self.store.transaction():
            if fresh:
                intent = new_launch_nonce()
                self.store.set("replace_pending", "new")
                self.store.set("launch_new_intent", intent)
                self.store.set("launch_attempt_intent", intent)
                self.store.set("conversation_origin", "explicitly-replaced")
                self.store.delete("launch_hold")
            else:
                self.store.set("conversation_origin", "original")
            self.store.set("agent_session", sid)
            self.store.set("launches_without_start", str(launches + 1))
            self.store.set("launch_nonce", new_launch_nonce())
            self.store.set("agent_account", account.key)
            self.store.set("launch_kind", "startup")
            self.store.set("launch_issued", self.launch_nonce or "")
            self.store.set("launch_session", sid)
            self.store.set("launch_resume_eligibility", "unqualified")
            self.store.delete("launch_ack_nonce")
            self.store.delete("launch_ack_session")
        # Issued state precedes every external operation. Any uncertainty is held.
        try:
            self.tmux.kill(SESSION)
            self._write_settings()
            argv = interactive_argv(
                self.settings.adapter_binary,
                self.settings.profile,
                session_id=sid,
                resume=False,
                settings_file=self.settings_file,
                name=SESSION,
            )
            self.tmux.new_session(SESSION, self.settings.workdir, argv, set=account.set, unset=account.unset)
        except Exception:
            self.hold_launch("launch-unacknowledged")
            raise
        with self.store.transaction():
            self.store.delete("replace_pending")
            self.store.delete("launch_issued")
        return "new-launch-requested" if fresh else "startup-launch-requested"

    def started(self, session_id: str, source: str = "startup", nonce: str | None = None) -> bool:
        """Accept only this source-owned startup's exact current acknowledgement.

        Hook authentication remains the daemon/socket boundary. A startup ack is
        current readiness only, never durable resume eligibility. No positive resume
        branch exists until a separately approved capability producer is implemented.
        """
        if (
            source != "startup"
            or nonce is None
            or nonce != self.launch_nonce
            or session_id != self.session_id
            or not self.login_matches()
            or self.store.get("launch_kind") != "startup"
            or self.store.get("launch_session") != session_id
            or self.store.get("launch_hold") is not None
        ):
            return False
        with self.store.transaction():
            self.store.set("session_started", session_id)
            self.store.set("launch_ack_session", session_id)
            self.store.set("launch_ack_nonce", nonce)
        return True

    def new(self) -> str:
        """Existing authenticated owner operation: accept one explicit fresh launch.

        A persisted intent is consumed before kill/spawn; restart cannot replay it.
        Only this operation clears sticky recovery hold and the outer launch budget.
        """
        return self._request_startup(fresh=True)

    def send(self, text: str) -> None:
        self.tmux.paste(SESSION, text)

    def interrupt(self) -> None:
        self.tmux.send_key(SESSION, "Escape")

    def tail(self, lines: int) -> str:
        if not self.tmux.has_session(SESSION):
            return "(no admin agent session)"
        return self.tmux.capture(SESSION, lines)
