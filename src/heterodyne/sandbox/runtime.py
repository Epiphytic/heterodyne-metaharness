"""SandboxRuntime: plan 3's AgentRuntime, each session in its own sandbox with its CLI in a pane of
wsd's tmux server (ADR 0001 §7 Runtime, §4.2, §10).

Every step is described by the session's record before it is taken, so a dead wsd's half-done launch is
found and cleaned up. Everything fails closed: what can't be listed, read or confirmed is
RuntimeUnavailable or LaunchUncertain, never assumed absent."""

import contextlib
import json
import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import msgspec

from heterodyne.agents.base import Adapter, AdapterError, Cli, write_private
from heterodyne.agents.registry import ADAPTERS
from heterodyne.config import ConfigError
from heterodyne.sandbox import sessiongit
from heterodyne.sandbox.backend import Backend, BackendError, BackendUnavailable
from heterodyne.sandbox.selftest import ProbeContext, SelfTest, SelfTestFailed
from heterodyne.sandbox.settings import SandboxSettings, WsSandbox
from heterodyne.sandbox.spec import (
    BRIDGE_INSIDE,
    NAME,
    READ_ONLY_ROLES,
    REQUEST_INSIDE,
    Protected,
    SandboxSpec,
    SessionLayout,
    SpecInput,
    SpecRefused,
    build_spec,
    check_credentials,
    sandbox_name,
    short_id,
)
from heterodyne.session import shim
from heterodyne.session.server import SessionServer
from heterodyne.tmux import Tmux, TmuxError
from heterodyne.wsd import gitwip
from heterodyne.wsd.accounts import Accounts
from heterodyne.wsd.runtime import (
    LaunchFailed,
    LaunchSpec,
    LaunchUncertain,
    Liveness,
    RuntimeUnavailable,
    Session,
    Started,
)

DEFAULT_ACCOUNT = "default"
TRUST_SECONDS = 90
SERVER_SECONDS = 20
POLL_SECONDS = 0.1
TMUX_ERRORS = (TmuxError, OSError, subprocess.TimeoutExpired)
SHIM_SOURCE = Path(shim.__file__)
WIP_FAILED = "the lifetime WIP commit failed; it is retried before the next launch"
RECORD_LOST = "the session record vanished during its launch; inspect the session's git directory"


class Phase(StrEnum):
    CREATING = "creating"
    TESTING = "testing"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ENDED = "ended"


class SessionRecord(msgspec.Struct, frozen=True, kw_only=True):
    key: str
    ws: str
    bead: str
    role: str
    profile: str
    adapter: str
    generation: int
    phase: Phase
    sandbox: str
    tmux_session: str
    ran: bool = False                 # some generation reached `running`; never cleared
    native_id: str | None = None      # the latest running generation's
    worktree: str = ""
    started_at: int = 0
    deadline: int = 0                 # D13: set before the sandbox exists, from the login's exposure
    error: str = ""
    stop_reason: str = ""             # "lifetime" once a lifetime stop has begun (Task 11)
    wip_mark: str = ""                # a lifetime WIP commit still owed; cleared once it lands (Task 11)
    repo: str = ""                    # the bead's repository, as the launch named it (D26)
    unlanded: bool = False            # the session's commits could not be landed: a human looks first
    seeded: bool = False              # this generation's private git directory exists and must be landed
    created: bool = False             # backend.create returned: no create is outstanding (the watcher may go)


def read_record(layout: SessionLayout) -> SessionRecord | None:
    try:
        data = layout.record.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeUnavailable("a session record can't be read") from None
    try:
        return msgspec.json.decode(data, type=SessionRecord)
    except msgspec.MsgspecError:
        raise RuntimeUnavailable("a session record is unreadable") from None


def write_record(layout: SessionLayout, rec: SessionRecord) -> None:
    layout.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(layout.record, msgspec.json.encode(rec).decode())


class _StepFailed(Exception):
    """A launch step failed. Fixed wording."""


KNOWN_FAILURES = (SpecRefused, AdapterError, BackendError, _StepFailed, ConfigError, gitwip.GitFailed)


@dataclass(frozen=True)
class RuntimeConfig:
    sessions: Path                    # <wsd state>/sessions
    settings: SandboxSettings
    accounts: Accounts
    real_home: Path
    real_home_canary: Path
    wsd_socket: Path                  # wsd's control socket: the probes check it is unreachable
    uid: int
    gid: int
    tmux: Tmux
    path: str                         # where CLI binaries are looked up
    clock: Callable[[], int]          # UTC epoch seconds: the scheduler's clock
    wait_clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    adapters: Mapping[str, Adapter] = field(default_factory=lambda: ADAPTERS)


class SandboxRuntime:
    def __init__(self, config: RuntimeConfig, backend: Backend, selftest: SelfTest) -> None:
        self.c = config
        self.backend = backend
        self.selftest = selftest
        self.servers: dict[str, SessionServer] = {}      # key -> its running generation's socket

    # --- names ---

    def layout(self, key: str) -> SessionLayout:
        return SessionLayout.at(self.c.sessions, key)

    def tmux_name(self, key: str) -> str:
        return f"wsd-{short_id(key)}"

    def reaper_name(self, key: str, generation: int) -> str:
        """Per generation, like the sandbox it watches (`sandbox_name`): no later launch replaces it."""
        return f"wsd-{short_id(key)}-r{generation}"

    def _reaper(self, rec: SessionRecord) -> None:
        """D13's backstop: a process in wsd's tmux server (its own systemd scope, so it outlives wsd) that
        deletes the sandbox at the deadline, whether or not wsd, its queue or its reconciliation works."""
        name = self.reaper_name(rec.key, rec.generation)
        self.c.tmux.kill(name)
        self.c.tmux.new_session(name, self.c.sessions, self.backend.reaper_argv(rec.sandbox, rec.deadline))

    # --- AgentRuntime ---

    def available(self) -> bool:
        return self.backend.available()

    def sessions(self, ws: str) -> list[Session]:
        try:
            names = self.backend.names()
        except BackendUnavailable:
            raise RuntimeUnavailable("the sandbox backend can't list its sandboxes") from None
        records = self._records()
        known = {short_id(r.key) for r in records}
        if any(NAME.fullmatch(n) and n.rsplit("g", 1)[0] not in known for n in names):
            raise RuntimeUnavailable("a sandbox has no session record")
        listed: list[Session] = []
        for rec in records:
            if rec.ws != ws or rec.phase is Phase.ENDED:
                continue
            if rec.phase is Phase.RUNNING and self._alive(rec, names):
                self._rebind(rec)
                listed.append(Session(rec.key, rec.ws, rec.bead, rec.role, Liveness.LIVE))
            elif not self._end(rec, interrupt=False, lifetime=self._overdue(rec)):
                listed.append(Session(rec.key, rec.ws, rec.bead, rec.role, Liveness.UNKNOWN))
        return listed

    def launch(self, spec: LaunchSpec) -> Started:
        adapter = self.c.adapters.get(self.c.accounts.adapter(spec.profile) or "")
        if adapter is None:
            raise LaunchFailed("the profile's adapter can't be run in a sandbox")
        if spec.account != DEFAULT_ACCOUNT:
            raise LaunchFailed("named accounts are bound by AU-6")
        if spec.repo is None:
            raise LaunchFailed("the launch names no repository")
        try:
            pinned = gitwip.pin(spec.repo, spec.worktree, f"btq/{spec.bead}")
        except gitwip.GitFailed as exc:
            raise LaunchFailed(str(exc)) from None
        if pinned.git_dir == pinned.common:
            raise LaunchFailed("a sandbox can't hold the main worktree: its git directory is inside it")
        layout = self.layout(spec.session_key)
        prev = read_record(layout)
        if prev is None and os.path.lexists(layout.git):
            # D26: a record lost after seeding; the directory may hold commits, and seeding would remove them.
            raise LaunchFailed("the session's git directory exists but its record is gone; inspect it")
        if prev is not None and prev.phase is not Phase.ENDED:
            if prev.phase is Phase.RUNNING and self._alive(prev, self._names()):
                return self._started(prev)              # idempotent: a live session is left alone
            if not self._end(prev, interrupt=False, lifetime=self._overdue(prev)):
                raise RuntimeUnavailable("an earlier launch of this session could not be cleaned up")
            prev = read_record(layout)
        if prev is not None and prev.unlanded:
            raise LaunchFailed("the last generation's commits were not landed; inspect the session's git "
                               "directory")
        if prev is not None and prev.wip_mark:
            try:
                prev = self._settle_wip(layout, prev)   # D13: a lifetime commit a crash left owed lands first
            except OSError:
                raise RuntimeUnavailable("the session record can't be written") from None
            if prev.wip_mark:
                raise LaunchFailed("the last generation's lifetime WIP commit is still owed")
        resume, native = self._resume(adapter, layout, prev, spec)
        cli = self._cli(adapter)
        protected, others = self._logins(adapter)
        expiry = self._fresh(adapter, protected.chosen_files)
        try:
            name = sandbox_name(spec.session_key, spec.generation)
        except SpecRefused as exc:
            raise LaunchFailed(str(exc)) from None
        rec = SessionRecord(key=spec.session_key, ws=spec.ws, bead=spec.bead, role=spec.role,
                            profile=spec.profile, adapter=adapter.name, generation=spec.generation,
                            phase=Phase.CREATING, sandbox=name, tmux_session=self.tmux_name(spec.session_key),
                            ran=prev.ran if prev else False, native_id=prev.native_id if prev else None,
                            worktree=str(spec.worktree), repo=str(spec.repo))
        write_record(layout, rec)
        try:
            return self._start(spec, adapter, cli, layout, rec, resume, native, protected, others, expiry,
                               pinned)
        except Exception as exc:  # noqa: BLE001 - every failure ends the sandbox, then is reported
            raise self._fail(layout, rec, exc) from None

    def stop(self, session_key: str) -> None:
        rec = read_record(self.layout(session_key))
        if rec is None or rec.phase is Phase.ENDED:
            return
        if not self._end(rec, interrupt=True):
            raise RuntimeUnavailable("the session's end could not be confirmed")

    def expire(self, ws: str, now: int) -> None:
        """D13: `_end(..., lifetime=True)` makes the stop durable, lands the generation's commits and then
        the WIP; this only decides when, and replays what a crash left owed. An unlanded session's mark
        stays owed and is never committed here (`_settle_wip`)."""
        margin = self.c.settings.stop_margin_seconds
        failed: list[str] = []                   # every session is tried; a failure is raised after the pass
        for rec in self._records():
            if rec.ws != ws:
                continue
            if rec.phase is Phase.ENDED:
                if rec.wip_mark:
                    try:
                        self._settle_wip(self.layout(rec.key), rec)    # a crash came between end and commit
                    except OSError:
                        failed.append("the session record can't be written")
                continue
            if rec.stop_reason == "lifetime":
                hard = True                      # a lifetime stop already begun: finish it
            elif rec.phase is not Phase.RUNNING or now < rec.deadline - margin:
                continue
            else:
                hard = now >= rec.deadline
                server = self.servers.get(rec.key)
                if not hard and (server is None or not server.turns().idle):
                    continue                     # not at a turn boundary: wait for one, or the deadline
            if not self._end(rec, interrupt=hard, lifetime=True):
                failed.append("a session at its maximum lifetime could not be stopped")
        if failed:
            raise RuntimeUnavailable(failed[0])

    # --- the launch ---

    def _resume(self, adapter: Adapter, layout: SessionLayout, prev: SessionRecord | None,
                spec: LaunchSpec) -> tuple[bool, str | None]:
        """D9: (resume, native ID). Resume held state; start fresh only when the record shows no generation
        ever ran; otherwise RuntimeUnavailable. Missing state is never evidence that none existed."""
        wanted = spec.native_id or (prev.native_id if prev is not None and prev.ran else None)
        try:
            # A home that was never made holds nothing; one that exists is searched anchored (a link refuses).
            if os.path.lexists(layout.home) and adapter.has_state(layout.home, wanted):
                return True, wanted
            if prev is None or not prev.ran:
                return False, None if adapter.assigns_id else spec.native_id
        except AdapterError as exc:
            raise LaunchFailed(str(exc)) from None
        except OSError:
            raise RuntimeUnavailable("the session's home can't be read") from None
        raise RuntimeUnavailable("the session ran before, but its resumable state can't be found")

    def _cli(self, adapter: Adapter) -> Cli:
        binary = self.c.settings.binaries.get(adapter.name)
        if not binary:
            raise LaunchFailed(f"no binary is configured for {adapter.name}")
        try:
            cli = adapter.locate(binary, self.c.path)
        except AdapterError as exc:
            raise LaunchFailed(str(exc)) from None
        if cli.version != adapter.version_pin:
            raise LaunchFailed(f"the {adapter.name} CLI is not the pinned version {adapter.version_pin}")
        return cli

    def _logins(self, adapter: Adapter) -> tuple[Protected, tuple[tuple[str, Path], ...]]:
        try:
            chosen_dir, chosen = self.c.accounts.login_paths(adapter.name, DEFAULT_ACCOUNT)
            others: list[tuple[str, Path]] = []
            dirs: list[Path] = []
            for account in self.c.accounts.configured(adapter.name):
                folder, files = self.c.accounts.login_paths(adapter.name, account)
                dirs.append(folder)
                others += [(account, f) for f in files]
        except ConfigError as exc:
            raise LaunchFailed(str(exc)) from None
        return Protected(chosen_dir, chosen, tuple(dirs), tuple(f for _, f in others)), tuple(others)

    def _fresh(self, adapter: Adapter, login_files: tuple[Path, ...]) -> int:
        """§7 freshness gate (D12): plan 4 never refreshes, so a login that would expire within the
        session's maximum lifetime and stop margin refuses the launch. Returns the expiry it checked."""
        try:
            expiry = adapter.access_expiry(login_files)
        except AdapterError as exc:
            raise LaunchFailed(str(exc)) from None
        s = self.c.settings
        if expiry - self.c.clock() <= s.max_lifetime_seconds + s.stop_margin_seconds:
            raise LaunchFailed("the login expires within the session's maximum lifetime; refresh it on "
                               "the host")
        return int(expiry)

    def _start(self, spec: LaunchSpec, adapter: Adapter, cli: Cli, layout: SessionLayout, rec: SessionRecord,
               resume: bool, native: str | None, protected: Protected,
               others: tuple[tuple[str, Path], ...], expiry: int, pinned: gitwip.Pinned) -> Started:
        c, gen = self.c, spec.generation
        run, scratch = layout.run(gen), layout.scratch(gen)
        for folder in (run, scratch):
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(mode=0o700, parents=True)
        layout.home.mkdir(mode=0o700, exist_ok=True)
        adapter.prepare_home(layout, spec.worktree)
        ws = c.settings.workstreams.get(spec.ws, WsSandbox())
        token = secrets.token_hex(16)
        shim_config = {"wait_seconds": c.settings.hook_wait_seconds,
                       "local_classes": sorted(ws.local_classes), "worktree": str(spec.worktree)}
        files = {shim.TOKEN_FILE: token, SHIM_SOURCE.name: SHIM_SOURCE.read_text(),
                 shim.CONFIG_FILE: json.dumps(shim_config), REQUEST_INSIDE.name: shim.REQUEST_WRAPPER,
                 **adapter.run_files(), **self.selftest.files()}
        for fname, text in files.items():
            write_private(run / fname, text)
        (run / REQUEST_INSIDE.name).chmod(0o700)
        server = SessionServer(layout.socket(gen), token, layout.events(gen))
        self.servers[spec.session_key] = server     # before start: an end closes even a half-started one
        server.start()
        git_binds = sessiongit.seed(pinned, layout.git, read_only=spec.role in READ_ONLY_ROLES)
        sp = build_spec(SpecInput(spec.session_key, gen, spec.role, layout, spec.worktree,
                                  adapter.facts(layout, cli, c.uid), protected.chosen_files, ws.extra_egress,
                                  ws.extra_ro_mounts, c.settings.probe_allowed_host, c.uid, c.gid,
                                  git_binds))
        check_credentials(sp, protected, c.real_home)
        # D13: the lifetime runs from the moment the login can be reached, never from the end of a slow
        # start, and the hard stop is always a full margin before the checked expiry.
        exposed = c.clock()
        # D26: from here on the private git directory may hold the agent's commits, so every end lands it.
        rec = self._phase(layout, rec, seeded=True,
                          deadline=min(exposed + c.settings.max_lifetime_seconds,
                                       expiry - c.settings.stop_margin_seconds))
        self._reaper(rec)                    # the backstop exists before the sandbox does
        if c.clock() >= rec.deadline:
            # T11 r3: a stall here could outlast the watcher's linger, and a create submitted after it would
            # land unwatched. Past the deadline nothing is created; only a stall between this read and the
            # create's submission remains, and it would have to last LINGER_SECONDS.
            raise _StepFailed("the launch stalled past its deadline before the sandbox was created")
        self.backend.create(sp, scratch)
        rec = self._phase(layout, rec, created=True)
        if c.clock() >= rec.deadline:
            # T11 r3: the check above leaves a window up to the create's submission, so a sandbox that
            # exists only at or past its deadline is ended at once, by this launch, not left to the watcher.
            raise _StepFailed("the sandbox was created past its deadline; it was destroyed")
        self._trust(adapter, cli, sp, layout)
        rec = self._phase(layout, rec, phase=Phase.TESTING)
        ctx = ProbeContext(self.backend, c.tmux, rec.tmux_session, sp, layout, gen, adapter, cli, token,
                           others, c.real_home_canary, c.wsd_socket, c.settings, server.turns)
        self.selftest.exec_path(ctx)
        self._app_server(adapter, cli, sp, layout)
        rec = self._phase(layout, rec, phase=Phase.STARTING)
        profile: dict[str, Any] = dict(c.settings.profiles.get(spec.profile, {}))
        if spec.model:
            profile["model"] = spec.model
        tui = adapter.tui_argv(cli, profile, native_id=native, resume=resume, label=spec.label)
        c.tmux.kill(rec.tmux_session)
        c.tmux.new_session(rec.tmux_session, spec.worktree, self.backend.tty_argv(sp.name, sp.workdir, tui))
        pane, pid = c.tmux.pane_info(rec.tmux_session)
        self.selftest.agent_path(ctx)
        native = self._native(server, native)
        now = c.clock()
        if now >= rec.deadline - c.settings.stop_margin_seconds:
            raise _StepFailed("the launch outlasted its login's lifetime window; "
                              "refresh the login on the host")
        rec = self._phase(layout, rec, phase=Phase.RUNNING, ran=True, native_id=native, started_at=now,
                          error="")
        return Started(rec.tmux_session, pane, pid, native)

    def _phase(self, layout: SessionLayout, rec: SessionRecord, **changes: Any) -> SessionRecord:
        rec = msgspec.structs.replace(rec, **changes)
        write_record(layout, rec)
        return rec

    def _trust(self, adapter: Adapter, cli: Cli, sp: SandboxSpec, layout: SessionLayout) -> None:
        """D6: seed the hook trust, then require that nothing is left untrusted."""
        argv = adapter.trust_argv(cli, sp.workdir)
        if argv is None:
            return
        for attempt in range(2):
            r = self.backend.exec(sp.name, sp.workdir, argv, timeout=TRUST_SECONDS)
            if r.returncode != 0:
                raise _StepFailed("the hook trust listing failed")
            if adapter.apply_trust(layout, r.stdout) and attempt:
                raise _StepFailed("hooks are still untrusted after seeding")

    def _app_server(self, adapter: Adapter, cli: Cli, sp: SandboxSpec, layout: SessionLayout) -> None:
        argv = adapter.server_argv(cli)
        if argv is None:
            return
        detach = f'setsid "$0" "$@" </dev/null >{BRIDGE_INSIDE}/app-server.log 2>&1 &'
        r = self.backend.exec(sp.name, sp.workdir, ["sh", "-c", detach, *argv], timeout=30)
        if r.returncode != 0 or not self._until(lambda: adapter.server_ready(layout), SERVER_SECONDS):
            raise _StepFailed("the agent's server did not start")

    def _native(self, server: SessionServer, native: str | None) -> str:
        """D8, D24: the native ID this generation's SessionStart reported, checked against the one it was
        started with. A resume must report it too: nothing else says which session the CLI resumed."""
        thread = server.turns().thread_id
        if thread is None:
            raise _StepFailed("the agent reported no session ID")
        if native is not None and thread != native:
            raise _StepFailed("the agent reported a different session ID")
        return thread

    def _until(self, pred: Callable[[], bool], seconds: float) -> bool:
        end = self.c.wait_clock() + seconds
        while not pred():
            if self.c.wait_clock() >= end:
                return False
            self.c.sleep(POLL_SECONDS)
        return True

    def _fail(self, layout: SessionLayout, rec: SessionRecord, exc: Exception) -> Exception:
        if isinstance(exc, SelfTestFailed):
            reason = f"launch self-test failed: {exc}"
        elif isinstance(exc, KNOWN_FAILURES):
            reason = str(exc)
        else:
            reason = f"launch step failed ({type(exc).__name__})"
        try:
            current = read_record(layout)
        except RuntimeUnavailable:
            # The record can't say what this launch reached, so nothing is landed or recorded: the sandbox
            # and pane still go, by the identity this launch holds, and the listing stays held.
            self._teardown(rec, interrupt=False)
            return LaunchUncertain(reason)
        if current is None:
            # The record this launch wrote is gone, so nothing says what it reached: the sandbox and pane
            # still go, nothing is landed, and a hold takes the record's place until a human looks.
            gone = self._teardown(rec, interrupt=False)
            with contextlib.suppress(OSError):
                write_record(layout, msgspec.structs.replace(
                    rec, phase=Phase.ENDED if gone else Phase.STOPPING, unlanded=True, error=RECORD_LOST))
            return LaunchUncertain(reason)
        rec = msgspec.structs.replace(current, error=reason)
        if self._end(rec, interrupt=False):
            return LaunchFailed(reason)
        return LaunchUncertain(reason)

    # --- the end of a session ---

    def _names(self) -> set[str]:
        try:
            return self.backend.names()
        except BackendUnavailable:
            raise RuntimeUnavailable("the sandbox backend can't list its sandboxes") from None

    def _alive(self, rec: SessionRecord, names: set[str]) -> bool:
        try:
            return (rec.sandbox in names and self.c.tmux.has_session(rec.tmux_session)
                    and not self.c.tmux.pane_dead(rec.tmux_session))
        except TMUX_ERRORS:
            raise RuntimeUnavailable("the session's pane can't be checked") from None

    def _started(self, rec: SessionRecord) -> Started:
        try:
            pane, pid = self.c.tmux.pane_info(rec.tmux_session)
        except TMUX_ERRORS:
            raise RuntimeUnavailable("the session's pane can't be read") from None
        return Started(rec.tmux_session, pane, pid, rec.native_id)

    def _rebind(self, rec: SessionRecord) -> None:
        """D19: a restarted wsd serves a live session's socket again, with an empty turn state."""
        if rec.key in self.servers:
            return
        layout = self.layout(rec.key)
        try:
            token = (layout.run(rec.generation) / shim.TOKEN_FILE).read_text().strip()
            server = SessionServer(layout.socket(rec.generation), token, layout.events(rec.generation))
        except OSError:
            raise RuntimeUnavailable("a live session's socket can't be served") from None
        try:
            server.start()
        except OSError:
            server.close()
            raise RuntimeUnavailable("a live session's socket can't be served") from None
        self.servers[rec.key] = server

    def _end(self, rec: SessionRecord, *, interrupt: bool, lifetime: bool = False) -> bool:
        """End the session: interrupt it if asked, kill its pane, delete every sandbox of its key. True
        (recorded `ended`) only once the pane is gone and the backend no longer lists any of them.

        A lifetime end (D13) records its reason and the WIP commit it owes in the same write that records
        `stopping`, before anything is stopped, so a crash on either side of any later step replays it."""
        layout = self.layout(rec.key)
        owed: dict[str, str] = {}
        if lifetime or rec.stop_reason == "lifetime":
            mark = rec.wip_mark or f"lifetime:{rec.key}:{rec.generation}"
            owed = {"stop_reason": "lifetime", "wip_mark": mark}
        try:
            rec = self._phase(layout, rec, phase=Phase.STOPPING, **owed)
            journaled = True
        except OSError:
            journaled = False                # stop it anyway; landing and `ended` wait for a durable record
        if not self._teardown(rec, interrupt=interrupt) or not journaled:
            return False                     # the reaper stays: it is the backstop until the end is confirmed
        if rec.created:              # a create that failed or timed out may still land: its watcher stays
            with contextlib.suppress(*TMUX_ERRORS):
                self.c.tmux.kill(self.reaper_name(rec.key, rec.generation))
        try:
            rec = self._land(layout, rec)    # D26: before any host git (the lifetime WIP, a park) runs
            rec = self._phase(layout, rec, phase=Phase.ENDED)
        except OSError:
            return False
        with contextlib.suppress(OSError):
            self._settle_wip(layout, rec)    # a lost record write keeps the mark; the replay is idempotent
        return True

    def _teardown(self, rec: SessionRecord, *, interrupt: bool) -> bool:
        """Close the generation's socket, interrupt (if asked) and kill the pane, delete every sandbox of
        the key. True only once the socket path, the pane and every listed sandbox are confirmed gone."""
        server = self.servers.get(rec.key)
        socket_gone = True
        if server is not None:
            try:
                server.close()
            except OSError:
                socket_gone = False             # kept, so the next end closes it again
            else:
                del self.servers[rec.key]
        for path in (self.layout(rec.key).socket(rec.generation),
                     self.layout(rec.key).probe_socket(rec.generation)):
            try:
                path.unlink(missing_ok=True)     # a crashed wsd's socket outlives its server object
            except OSError:
                socket_gone = False
        tmux = self.c.tmux
        try:
            if interrupt and tmux.has_session(rec.tmux_session) and not tmux.pane_dead(rec.tmux_session):
                tmux.send_key(rec.tmux_session, "Escape")
            tmux.kill(rec.tmux_session)
            pane_gone = tmux.session_absent(rec.tmux_session)   # an unreachable server confirms nothing
        except TMUX_ERRORS:
            pane_gone = False
        prefix = f"{short_id(rec.key)}g"
        try:
            mine = [n for n in self.backend.names() if n.startswith(prefix)]
            confirmed = [self.backend.delete(n) for n in mine]
            deleted = all(confirmed) and not any(n.startswith(prefix) for n in self.backend.names())
        except (BackendUnavailable, BackendError):
            deleted = False
        return socket_gone and pane_gone and deleted

    def _land(self, layout: SessionLayout, rec: SessionRecord) -> SessionRecord:
        """Land the generation's commits on the bead branch (Task 7A). Idempotent; a failure keeps the
        private git directory and is recorded, and the next launch refuses until a human looks. Once the
        record says the directory was seeded, a missing or unreadable one is such a failure: it may have
        held commits."""
        if rec.unlanded or not rec.seeded:
            return rec
        try:
            sessiongit.land(gitwip.pin(Path(rec.repo), Path(rec.worktree), f"btq/{rec.bead}"), layout.git)
        except (gitwip.GitFailed, OSError):
            return self._phase(layout, rec, unlanded=True, error="the session's commits could not be landed")
        return rec

    def _overdue(self, rec: SessionRecord) -> bool:
        """A session that ran is past its deadline: whatever ends it now ends it for its lifetime."""
        return rec.started_at > 0 and self.c.clock() >= rec.deadline

    def _settle_wip(self, layout: SessionLayout, rec: SessionRecord) -> SessionRecord:
        """Land the lifetime WIP commit an ended session still owes (D13), then clear the mark. The commit is
        idempotent by its mark, so a replay after a crash before or after it lands it exactly once. The
        mark stays until the commit is made: a failure is retried by the next end or launch. Commits that
        were not landed come first: a WIP on the host branch would diverge from them."""
        if not rec.wip_mark or rec.phase is not Phase.ENDED or rec.unlanded:
            return rec
        try:
            pinned = gitwip.pin(Path(rec.repo), Path(rec.worktree), f"btq/{rec.bead}")
            gitwip.wip_commit(pinned, rec.wip_mark, "maximum lifetime reached")
        except gitwip.GitFailed:
            return self._phase(layout, rec, error=WIP_FAILED)
        return self._phase(layout, rec, wip_mark="", error="" if rec.error == WIP_FAILED else rec.error)

    def _records(self) -> list[SessionRecord]:
        if not self.c.sessions.exists():
            return []
        try:
            folders = sorted(p for p in self.c.sessions.iterdir() if p.is_dir())
        except OSError:
            raise RuntimeUnavailable("the session records can't be listed") from None
        found = [read_record(SessionLayout(p)) for p in folders]
        return [r for r in found if r is not None]
