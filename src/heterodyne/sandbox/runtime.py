"""SandboxRuntime: plan 3's AgentRuntime, each session in its own sandbox with its CLI in a pane of
wsd's tmux server (ADR 0001 §7 Runtime, §4.2, §10).

Every step is described by the session's record before it is taken, so a dead wsd's half-done launch is
found and cleaned up. Everything fails closed: what can't be listed, read or confirmed is
RuntimeUnavailable or LaunchUncertain, never assumed absent."""

import contextlib
import dataclasses
import errno
import json
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import msgspec

from heterodyne.agents.base import Adapter, AdapterError, Cli, UsageReport, write_private
from heterodyne.agents.registry import ADAPTERS
from heterodyne.config import ConfigError, profile_args
from heterodyne.config.accounts import credential_key
from heterodyne.config.capabilities import Capabilities
from heterodyne.fsutil import private_dir
from heterodyne.harness import selection
from heterodyne.harness import verify as hv
from heterodyne.harness.selection import InstallPin
from heterodyne.harness.store import Store
from heterodyne.sandbox import creates, sessiongit
from heterodyne.sandbox.backend import Backend, BackendError, BackendUnavailable, ExecResult, Watch
from heterodyne.sandbox.reaper import LINGER_SECONDS
from heterodyne.sandbox.selftest import ProbeContext, SelfTest, SelfTestFailed
from heterodyne.sandbox.settings import SandboxSettings, WsSandbox
from heterodyne.sandbox.spec import (
    BRIDGE_INSIDE,
    INSTALL_RECORD_INSIDE,
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
from heterodyne.wsd import gitwip, ids
from heterodyne.wsd.accounts import Accounts
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.credlock import CredentialBusy, credential_lock
from heterodyne.wsd.runtime import (
    INSTALL_TAINTED,
    INSTALL_UNPINNED,
    KEY_CHANGED,
    LOGIN_STALE,
    NOT_SELECTED,
    Action,
    EndCause,
    Ended,
    ExecDirCleared,
    InstallTainted,
    LaunchFailed,
    LaunchSpec,
    LaunchUncertain,
    LimitSeen,
    Liveness,
    Resolution,
    ResolveFailed,
    ResolveRefused,
    RuntimeUnavailable,
    Session,
    Signal,
    Started,
    UnlandedView,
    UsageSeen,
)

DEFAULT_ACCOUNT = "default"
TRUST_SECONDS = 90
SERVER_SECONDS = 20
POLL_SECONDS = 0.1
TMUX_ERRORS = (TmuxError, OSError, subprocess.TimeoutExpired)
SHIM_SOURCE = Path(shim.__file__)
WIP_FAILED = "the lifetime WIP commit failed; it is retried before the next launch"
RECORD_LOST = "the session record vanished during its launch; inspect the session's git directory"
MAX_QUEUED = 256        # reports a workstream keeps between drains: a flooding session keeps only its newest
TAINT_SECONDS = 600     # plan 4c T9: how old an install in use's last walk may get before `expire` walks it
USAGE_SECONDS = 45      # the in-sandbox usage read's exec bound (codex_usage.py's own limit is 30)
DIAG_USAGE_READ = "an in-sandbox usage read gave no reading"
READ_REASON = re.compile(rb"codex_usage: ([A-Za-z]+)\n")       # the script's one stderr line (Task 5)
# btq-xb56t: the operator's resolve of an unlanded session (docs/wsd.md §5).
SHORT_ID = re.compile(r"hz[0-9a-f]{12}")
ZERO = "0" * 40
SUBJECT_CHARS = 72
CONFIRMED, IMPORTED, MOVED, REMOVED = "confirmed", "imported", "moved", "removed"
INCONSISTENT = "the resolve's state is inconsistent; see docs/wsd.md §5"
NO_RECORD = "the session has no record; see docs/wsd.md §5"
KEEP_REFUSED = "the keep ref can't be created"
NO_FAST_FORWARD = "the bead branch can't be fast-forwarded to the session's tip; nothing was changed"
STEP_FAILED = "a resolve step failed; run the same wsctl resolve again"
RESOLVE_POINTS = ("imported", "imported.recorded", "moved", "moved.recorded", "removed", "removed.recorded")
MARKER_BYTES = 4096
BRANCH_TAKEN = "another session of this bead still holds its branch; a branch holds one session at a time"

log = logging.getLogger(__name__)


def _same_repo(recorded: str, repo: Path | None) -> bool:
    """Whether a record's repository is the launch's: the same path once symlinks are resolved."""
    if not recorded or repo is None:
        return False
    return os.path.realpath(recorded) == os.path.realpath(repo)


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
    create_started: bool = False      # a create may be submitted: written before the watcher starts (r2)
    end_cause: str = ""               # g08sd: how this generation ended (EndCause); written once
    ended_at: int = 0                 # the `ended` write's clock
    install_name: str = ""            # plan 4c T8: the generation's verified install and manifest digest
    install_digest: str = ""
    create_ledger: bool = False       # migration was completed; cleanup must not recreate an old entry


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


class Intent(msgspec.Struct, frozen=True, kw_only=True):
    """A resolve's durable intent (btq-xb56t), in `<state>/resolutions/`: written before its first side
    effect, checkpointed after each, removed only once its journal entry is committed."""
    ws: str
    bead: str
    key: str
    generation: int
    tip: str | None
    action: Action
    ref: str = ""                     # the keep ref
    step: str                         # CONFIRMED, IMPORTED, MOVED or REMOVED
    dir_id: tuple[int, int]           # the session directory's (st_dev, st_ino) when it was confirmed
    end_cause: str = ""               # wpyv5: the record's end, as `ended()` maps it; "" is no evidence
    reached_running: bool = False
    ran_seconds: int | None = None    # the operator's detail only, as in g08sd


def _end_of(rec: SessionRecord) -> Ended:
    """g08sd: an ended record's end. Whether it ran is this generation's `started_at`, never `ran` (which
    persists across generations); the duration is detail only. `ended()` and a resolve both read it here."""
    if rec.end_cause:
        try:
            cause = EndCause(rec.end_cause)
        except ValueError:
            cause = EndCause.DIED
    else:                                    # ended before g08sd
        cause = EndCause.LEGACY_LIFETIME if rec.stop_reason == "lifetime" else EndCause.DIED
    ran = rec.ended_at - rec.started_at if rec.started_at > 0 and rec.ended_at >= rec.started_at else None
    return Ended(rec.generation, cause, rec.started_at > 0, ran)


def _intent_end(intent: Intent) -> Ended | None:
    """wpyv5: the end an intent carries, or None for one written before wpyv5."""
    if not intent.end_cause:
        return None
    try:
        cause = EndCause(intent.end_cause)
    except ValueError:
        cause = EndCause.DIED
    return Ended(intent.generation, cause, intent.reached_running, intent.ran_seconds)


def printable(raw: bytes) -> str:
    """An untrusted commit subject for a terminal: UTF-8 with replacement, every character outside
    printable ASCII as `?`, at most SUBJECT_CHARS."""
    text = raw.decode("utf-8", "replace")
    return "".join(c if " " <= c <= "~" else "?" for c in text)[:SUBJECT_CHARS]


def keep_ref(bead: str, sid: str, generation: int) -> str:
    return f"refs/heads/unlanded/{bead}/{sid}-g{generation}"


def _ident(path: Path) -> tuple[int, int] | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    return (st.st_dev, st.st_ino)


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class _StepFailed(Exception):
    """A launch step failed. Fixed wording."""


KNOWN_FAILURES = (SpecRefused, AdapterError, BackendError, _StepFailed, ConfigError, gitwip.GitFailed)
LOGIN_UNRESOLVED = "the chosen account's login file is missing or unresolvable"


@dataclass(frozen=True)
class _Checked:
    """What the locked check read (E1, D8): the canonical login files, each one's (st_dev, st_ino), and
    the expiry the freshness gate passed. The bind is made from exactly these files, and once the sandbox
    exists they are checked again (`_still_checked`), so what was bound is what was checked."""
    files: tuple[Path, ...]
    ids: tuple[tuple[int, int], ...]
    expiry: int


def _canonical(login_files: tuple[Path, ...]) -> tuple[tuple[Path, ...], tuple[tuple[int, int], ...]]:
    """The login files resolved once, and each one's identity: a regular file, by lstat of the resolved
    path. OSError or RuntimeError (a loop) if any can't be resolved or isn't a regular file."""
    files = tuple(f.resolve(strict=True) for f in login_files)
    ids: list[tuple[int, int]] = []
    for f in files:
        st = os.lstat(f)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        ids.append((st.st_dev, st.st_ino))
    return files, tuple(ids)


@dataclass(frozen=True)
class RuntimeConfig:
    sessions: Path                    # <wsd state>/sessions
    creates: Path                     # <state>/creates, outside every sandbox bind
    alerts: Path                      # <state>/alerts, shared with same-user admind
    credlocks: Path                   # <wsd state>/credlocks: the D8 lock directory (wsd.credlock)
    settings: SandboxSettings
    accounts: Accounts
    real_home: Path
    real_home_canary: Path
    wsd_socket: Path                  # wsd's control socket: the probes check it is unreachable
    uid: int
    gid: int
    tmux: Tmux
    clock: Callable[[], int]          # UTC epoch seconds: the scheduler's clock
    # Plan 4c T8: the managed install store, the taint markers (`<state>/harness-taint`), the config directory
    # holding harnesses.toml (re-read on every verification), and the store's location problems.
    store: Store
    taint_dir: Path
    config_dir: Path
    store_problems: Callable[[], Sequence[str]]
    wait_clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    adapters: Mapping[str, Adapter] = field(default_factory=lambda: ADAPTERS)


class SandboxRuntime:
    def __init__(self, config: RuntimeConfig, backend: Backend, selftest: SelfTest) -> None:
        self.c = config
        self.backend = backend
        self.selftest = selftest
        self.servers: dict[str, SessionServer] = {}      # key -> its running generation's socket
        self.cp: Checkpoint = nothing                       # a test's crash points in a resolve
        self._inspect_lock = threading.Lock()
        self._inspect_cleared = False
        self.queued: dict[str, list[Signal]] = {}         # ws -> reports since the last drain
        self.queue_lock = threading.Lock()                # the servers' threads add, signals() drains
        # Codex T8 r1 B1: evidence wsd must see (a taint, an exec dir cleared), by workstream, kept until
        # drained and never capped: one per (kind, session key, generation[, path]), so its size is bounded
        # by the live sessions, and no flood of usage reports can evict one.
        self.notices: dict[str, dict[tuple[str | int, ...], Signal]] = {}
        self.polled: dict[tuple[str, int], int] = {}      # (key, generation) -> when its read last ran
        self.walked: dict[tuple[str, str], int] = {}      # (adapter, install) -> when its last walk passed
        self.walk_lock = threading.Lock()                  # one taint walk at a time: expire's and the ends'

    # --- names ---

    def layout(self, key: str) -> SessionLayout:
        return SessionLayout.at(self.c.sessions, key)

    def tmux_name(self, key: str) -> str:
        return f"wsd-{short_id(key)}"

    def reaper_name(self, key: str, generation: int) -> str:
        """Per generation, like the sandbox it watches (`sandbox_name`): no later launch replaces it."""
        return f"wsd-{short_id(key)}-r{generation}"

    def _ledger(self) -> creates.Ledger:
        try:
            return creates.Ledger(self.c.creates, self.c.alerts)
        except OSError:
            raise RuntimeUnavailable("the create ledger can't be opened") from None

    def _submission(self, ledger: creates.Ledger, rec: SessionRecord) -> bool:
        return ledger.put(rec.sandbox, "", key=rec.key, ws=rec.ws, bead=rec.bead,
                          generation=rec.generation, deadline=rec.deadline, pane=rec.tmux_session,
                          at=self.c.clock()) and ledger.submission(rec.sandbox) is not None

    def _recover_create(self, ledger: creates.Ledger, rec: SessionRecord) -> None:
        if ledger.submission(rec.sandbox) is None:
            return
        if rec.created:
            ledger.put(rec.sandbox, "settled", how="returned", at=self.c.clock())
        elif not rec.create_started:
            ledger.put(rec.sandbox, "settled", how="never_submitted", at=self.c.clock())

    def _watcher_live(self, name: str) -> bool:
        return self.c.tmux.has_session(name) and not self.c.tmux.pane_dead(name)

    def _arm(self, entry: dict[str, Any]) -> None:
        """Confirm startup with a unique ready receipt and a live pane, never a retained dead pane."""
        ledger, tmux = self._ledger(), self.c.tmux
        key, generation, sandbox = entry["key"], entry["generation"], entry["sandbox"]
        name = self.reaper_name(key, generation)
        ready = ledger.directory / f".ready-{sandbox}-{secrets.token_hex(8)}"
        attempted = False
        try:
            if tmux.has_session(name):
                if not tmux.pane_dead(name):
                    return
                tmux.kill(name)
                if not tmux.session_absent(name):
                    raise RuntimeUnavailable("a dead watcher can't be removed")
            layout = self.layout(key)
            watch = Watch(generation, layout.reaped(generation), entry["pane"], tmux.binary,
                          tmux.socket_name, tmux.socket_path, ledger.directory, ledger.alerts, ready)
            argv = self.backend.reaper_argv(sandbox, entry["deadline"], layout.record, watch)
            private_dir(self.c.sessions)
            attempted = True
            tmux.new_session(name, self.c.sessions, argv)
            until = self.c.wait_clock() + 10
            while True:
                live = self._watcher_live(name)
                if ready.is_file():
                    if not live:
                        break
                    ready.unlink()
                    creates.sync(ledger.directory)
                    return
                if not live or self.c.wait_clock() >= until:
                    break
                self.c.sleep(0.2)
        except (*TMUX_ERRORS, OSError):
            pass
        finally:
            with contextlib.suppress(OSError):
                ready.unlink(missing_ok=True)
        if attempted:
            with contextlib.suppress(*TMUX_ERRORS):
                tmux.kill(name)
        raise RuntimeUnavailable(f"the watcher of {sandbox} did not start")

    def _reaper(self, rec: SessionRecord) -> None:
        entry = self._ledger().submission(rec.sandbox)
        if entry is None:
            raise RuntimeUnavailable("the create's submission can't be read")
        self._arm(entry)

    def _cleanup_create(self, ledger: creates.Ledger, name: str) -> None:
        watcher = f"wsd-{name.split('g')[0]}-r{name.rsplit('g', 1)[1]}"
        if self.c.tmux.has_session(watcher):
            if not self.c.tmux.pane_dead(watcher):
                return
            self.c.tmux.kill(watcher)
        if not self.c.tmux.session_absent(watcher):
            raise RuntimeUnavailable("a finished create's watcher can't be confirmed absent")
        if not ledger.close_notice(name, self.c.clock()):
            raise RuntimeUnavailable("a finished create's close-out notice can't be written")
        ledger.cleanup(name)

    def _restore_creates(self, *, ws: str | None = None, key: str | None = None) -> None:
        """Migrate once, restore protection, then sweep late/finished creates and crash cleanup."""
        ledger = self._ledger()
        try:
            records = {r.sandbox: r for r in self._records()}
            for rec in records.values():
                if (ws is not None and rec.ws != ws) or (key is not None and rec.key != key):
                    continue
                if rec.create_ledger:
                    continue
                needed = ((rec.create_started and not rec.created) or
                          (rec.created and rec.phase != Phase.ENDED))
                if needed:
                    if not self._submission(ledger, rec):
                        raise RuntimeUnavailable("a legacy create can't be recorded")
                    self._recover_create(ledger, rec)
                    if rec.created and not creates.settled(ledger.directory, rec.sandbox):
                        raise RuntimeUnavailable("a returned legacy create can't be settled")
                self._phase(self.layout(rec.key), rec, create_ledger=True)
            names = ledger.names()       # Enumeration failures never mean an empty ledger.
            for name in names:
                entry = ledger.submission(name)
                if entry is not None:
                    if (ws is not None and entry["ws"] != ws) or (key is not None and entry["key"] != key):
                        continue
                    rec = records.get(name)
                    if rec is not None:
                        self._recover_create(ledger, rec)
                elif not creates.settled(ledger.directory, name):
                    raise RuntimeUnavailable("a create ledger entry is unreadable")
                # Orphan settlements are interrupted cleanup, not a reason to arm a new watcher.
                if creates.gone(ledger.directory, name):
                    self._cleanup_create(ledger, name)
                    continue
                if entry is not None:
                    self._arm(entry)
                before = creates.settled(ledger.directory, name)
                rec = records.get(name)
                stale = rec is None or rec.phase == Phase.ENDED
                if stale:
                    listed = self.backend.names()
                    if name in listed:
                        ledger.observe(name, "sweep", self.c.clock())
                    if not self.backend.delete(name):
                        raise RuntimeUnavailable("a late sandbox of an earlier generation can't be deleted")
                    if before:
                        ledger.put(name, "gone", by="sweep", at=self.c.clock())
                    if creates.gone(ledger.directory, name):
                        self._cleanup_create(ledger, name)
        except (*TMUX_ERRORS, OSError, BackendError, BackendUnavailable):
            raise RuntimeUnavailable("create protection can't be confirmed") from None

    def settle_sandbox(self, ws: str, sid: str, generation: int) -> str:
        """Host operator accepts the remaining late-create risk after linger and an absent listing."""
        if not SHORT_ID.fullmatch(sid) or generation <= 0:
            raise ResolveRefused("invalid session or generation")
        ledger, name = self._ledger(), f"{sid}g{generation}"
        entry = ledger.submission(name)
        if entry is None or entry["ws"] != ws:
            raise ResolveRefused("the create is unknown")
        evidence = creates.settlement(ledger.directory, name)
        if evidence is not None and evidence["how"] != "operator":
            raise ResolveRefused("the create is already settled by another kind")
        if evidence is None:
            if self.c.clock() < entry["deadline"] + LINGER_SECONDS:
                raise ResolveRefused("its watcher is still within its linger")
            try:
                if name in self.backend.names():
                    raise ResolveRefused("the sandbox may exist; its watcher will delete it first")
            except (BackendUnavailable, BackendError):
                raise ResolveRefused("the sandbox may exist; its watcher will delete it first") from None
            if not ledger.put(name, "settled", how="operator", by="wsctl", at=self.c.clock()):
                raise RuntimeUnavailable("the operator settlement can't be written")
            evidence = creates.settlement(ledger.directory, name)
            if evidence is None or evidence["how"] != "operator":
                raise ResolveRefused("the create is already settled by another kind")
        observed = creates.read(ledger.directory / f"{name}.observed.json", name)
        return ("never observed" if observed is None else
                f"observed at least once by {observed.get('by')} at {observed.get('at')}")

    # --- AgentRuntime ---

    def available(self) -> bool:
        return self.backend.available()

    def sessions(self, ws: str) -> list[Session]:
        self._restore_creates(ws=ws)
        try:
            names = self.backend.names()
        except BackendUnavailable:
            raise RuntimeUnavailable("the sandbox backend can't list its sandboxes") from None
        records = self._records()
        known = {short_id(r.key) for r in records}
        try:
            known.update(n.split("g")[0] for n in self._ledger().names())
        except OSError:
            raise RuntimeUnavailable("the create ledger can't be read") from None
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
        # T10 r3: commits that were not landed hold the whole workstream, so neither a park nor a defer
        # makes a host WIP over them. Read again: an end above may have just recorded one.
        self._held(ws=ws)
        for rec in self._records():
            if rec.ws == ws and rec.unlanded:
                raise self._unlanded(rec)
        return listed

    def launch(self, spec: LaunchSpec) -> Started:
        adapter = self.c.adapters.get(self.c.accounts.adapter(spec.profile) or "")
        if adapter is None:
            raise LaunchFailed("the profile's adapter can't be run in a sandbox")
        if spec.repo is None:
            raise LaunchFailed("the launch names no repository")
        try:
            pinned = gitwip.pin(spec.repo, spec.worktree, f"btq/{spec.bead}")
        except gitwip.GitFailed as exc:
            raise LaunchFailed(str(exc)) from None
        if pinned.git_dir == pinned.common:
            raise LaunchFailed("a sandbox can't hold the main worktree: its git directory is inside it")
        self._restore_creates(key=spec.session_key)
        layout = self.layout(spec.session_key)
        prev = read_record(layout)
        if prev is None and os.path.lexists(layout.git):
            # D26: a record lost after seeding; the directory may hold commits, and seeding would remove them.
            raise LaunchFailed("the session's git directory exists but its record is gone; inspect it")
        if prev is not None and prev.phase is Phase.RUNNING and self._alive(prev, self._names()):
            return self._started(prev)                  # idempotent: a live session is left alone
        self._check_reasoning_resume(adapter, layout, prev, spec)
        if prev is not None and prev.phase is not Phase.ENDED:
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
        self._claim_branch(spec)
        resume, native = self._resume(adapter, layout, prev, spec)
        installed = self._cli(adapter, spec)
        protected, others = self._logins(adapter, spec.account)
        checked = self._pinned(adapter, spec, protected.chosen_files)
        try:
            name = sandbox_name(spec.session_key, spec.generation)
        except SpecRefused as exc:
            raise LaunchFailed(str(exc)) from None
        try:
            self._drop_markers(layout)              # g08sd: an earlier generation's, left by a crash
        except OSError:
            raise RuntimeUnavailable("the session directory can't be cleaned") from None
        rec = SessionRecord(key=spec.session_key, ws=spec.ws, bead=spec.bead, role=spec.role,
                            profile=spec.profile, adapter=adapter.name, generation=spec.generation,
                            phase=Phase.CREATING, sandbox=name, tmux_session=self.tmux_name(spec.session_key),
                            ran=prev.ran if prev else False, native_id=prev.native_id if prev else None,
                            worktree=str(spec.worktree), repo=str(spec.repo), install_name=installed.name,
                            install_digest=installed.digest, create_ledger=True)
        write_record(layout, rec)
        try:
            self._registered(adapter, installed)
            return self._start(spec, adapter, installed, layout, rec, resume, native, protected, others,
                               checked, pinned)
        except Exception as exc:  # noqa: BLE001 - every failure ends the sandbox, then is reported
            raise self._fail(layout, rec, exc) from None

    def _check_reasoning_resume(self, adapter: Adapter, layout: SessionLayout,
                                prev: SessionRecord | None, spec: LaunchSpec) -> None:
        """Read-only qualification gate; explicit stop/reconciliation owns retained cleanup."""
        profile = self.c.settings.profiles.get(spec.profile, {})
        try:
            effort = profile_args.reasoning_effort(profile, adapter.name, "profile")
        except ConfigError as exc:
            raise LaunchFailed(str(exc)) from None
        if effort is not None:
            if prev is not None and prev.unlanded:
                raise self._unlanded(prev)
            if prev is not None and prev.wip_mark:
                raise LaunchFailed("the last generation's lifetime WIP commit is still owed")
        if effort is not None and self._resume(adapter, layout, prev, spec)[0]:
            # Actual 0.160 app-server/TUI qualification retains the same native UUID across
            # server restart and adopts medium from the new home even after a high turn.
            # Keep the CLI capability scoped to the tested version/effort. Verify the
            # journal-pinned install before any retained-generation cleanup.
            if effort == "medium" and prev is not None:
                installed = self._cli(adapter, spec)
                if (installed.manifest.version == "0.160.0"
                        and (prev.install_name, prev.install_digest) == (installed.name, installed.digest)):
                    return
            raise LaunchFailed("typed Codex reasoning on native resume is not qualified")

    def stop(self, session_key: str) -> None:
        layout = self.layout(session_key)
        rec = read_record(layout)
        if rec is None:
            self._held(key=session_key)         # its directory may have moved for a resolve
            return
        if rec.phase is not Phase.ENDED:
            if not self._end(rec, interrupt=True, cause=EndCause.STOPPED):
                raise RuntimeUnavailable("the session's end could not be confirmed")
            rec = read_record(layout)
        self._held(ws=rec.ws if rec is not None else None, key=session_key)
        if rec is not None and rec.unlanded:
            raise self._unlanded(rec)            # ended, but its caller must not commit over it

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
            try:
                stopped = self._end(rec, interrupt=hard, lifetime=True, cause=EndCause.LIFETIME)
            except RuntimeUnavailable as exc:
                failed.append(str(exc))
                continue
            if not stopped:
                failed.append("a session at its maximum lifetime could not be stopped")
        self._watch(now, due_only=True)          # after the lifetime pass, which a walk never holds up
        if failed:
            raise RuntimeUnavailable(failed[0])

    def signals(self, ws: str) -> list[Signal]:
        """E8: what the workstream's sessions reported since the last call, oldest first, then its notices
        (B1: taints, cleared exec dirs), each once. Each running session's usage read is run first when it
        is due (E5). Never raises: a report that can't be had is no report."""
        try:
            running = [r for r in self._records() if r.ws == ws and r.phase is Phase.RUNNING]
        except RuntimeUnavailable:
            running = []
        for rec in running:
            if rec.key in self.servers:
                self._poll(rec)
        with self.queue_lock:
            return [*self.queued.pop(ws, []), *self.notices.pop(ws, {}).values()]

    def ended(self, session_key: str) -> Ended | None:
        """g08sd: the latest generation's end, from its record (`_end_of`)."""
        rec = read_record(self.layout(session_key))
        if rec is None or rec.phase is not Phase.ENDED:
            return None
        return _end_of(rec)

    # --- the launch ---

    def _resume(self, adapter: Adapter, layout: SessionLayout, prev: SessionRecord | None,
                spec: LaunchSpec) -> tuple[bool, str | None]:
        """D9: (resume, native ID). Resume held state; start fresh only when the record shows no generation
        ever ran; otherwise RuntimeUnavailable. Missing state is never evidence that none existed. D7: a
        handoff starts fresh whatever is held: a server-assigned ID is learned from the session, and any
        other is the handoff's own (`ids.handoff_session`)."""
        if spec.handoff:
            fresh = None if adapter.assigns_id else ids.handoff_session(spec.session_key, spec.generation)
            return False, fresh
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

    def verified_cli(self, adapter: Adapter, pin: InstallPin | None = None) -> Cli:
        """Plan 4c T8: the CLI of the install `pin` names, or with no pin of the adapter's current selection
        (`NOT_SELECTED` if it has none or it can't be read), verified now. Raises LaunchFailed with one of
        `verify.REFUSALS`. No lock is held: the taint marker and the selection are read again on every call,
        and the store is read-only to the service user."""
        return self._verified(adapter, pin).cli

    def _verified(self, adapter: Adapter, pin: InstallPin | None) -> hv.VerifiedInstall:
        if pin is None:
            try:
                chosen = selection.load(self.c.config_dir).get(adapter.name)
            except ConfigError:
                chosen = None           # an unreadable selection is not one; its text may quote the file
            if chosen is None or chosen.current is None:
                raise LaunchFailed(NOT_SELECTED)
            pin = chosen.current
        try:
            return hv.verify_pin(self.c.store, adapter, pin, self.c.taint_dir, problems=self.c.store_problems)
        except hv.VerifyRefused as exc:
            if exc.constant in hv.TAINTING:
                self._tainted(adapter.name, pin.install)
            raise LaunchFailed(exc.constant) from None

    def _tainted(self, adapter: str, install: str) -> None:
        """E17: one InstallTainted per live session on the install, queued before the refusal is raised. The
        marker is already written; a listing that fails reports nothing, and the marker still refuses."""
        try:
            live = [r for r in self._records()
                    if r.phase is not Phase.ENDED and (r.adapter, r.install_name) == (adapter, install)]
        except RuntimeUnavailable:
            return
        for rec in live:
            self._notice(rec.ws, ("taint", rec.key, rec.generation),
                         InstallTainted(rec.key, rec.generation, adapter, install))

    def _notice(self, ws: str, ident: tuple[str | int, ...], signal: Signal) -> None:
        """Keep `signal` for the workstream's next drain, once per `ident` (B1)."""
        with self.queue_lock:
            self.notices.setdefault(ws, {}).setdefault(ident, signal)

    def _watch(self, now: int, *, due_only: bool) -> None:
        """Plan 4c T9 (E17's second path): walk every install a live session runs, or with `due_only` each
        one whose last walk is `TAINT_SECONDS` old. A mismatch writes the marker (in `verify_pin`, before it
        raises) and queues one InstallTainted per live session on the install, into its own workstream's
        queue; so does a marker already there, since sessions may have started on the install after it was
        written. Anything else (an unsafe store, a missing install, an undeclared version, an I/O failure)
        is no evidence: it is logged, and the install stays due. Never raises."""
        try:
            live = [r for r in self._records() if r.phase is not Phase.ENDED and r.install_name]
        except RuntimeUnavailable:
            log.warning("taint watch: the session records can't be listed")
            return
        pins = sorted({(r.adapter, r.install_name, r.install_digest) for r in live})
        with self.walk_lock:
            for name, install, digest in pins:
                last = self.walked.get((name, install))
                if due_only and last is not None and now - last < TAINT_SECONDS:
                    continue
                adapter = self.c.adapters.get(name)
                if adapter is None:
                    log.warning("taint watch: no adapter %s for install %s", name, install)
                    continue
                try:
                    hv.verify_pin(self.c.store, adapter, InstallPin(install, digest), self.c.taint_dir,
                                  problems=self.c.store_problems, mark=hv.taint, io_taints=False)
                except hv.VerifyRefused as exc:
                    if exc.constant not in (*hv.TAINTING, INSTALL_TAINTED):
                        log.warning("taint watch: %s %s can't be walked: %s", name, install, exc.constant)
                        continue
                    self._tainted(name, install)
                except Exception:  # noqa: BLE001 - a walk that can't run is logged, never raised
                    log.warning("taint watch: %s %s can't be walked", name, install, exc_info=True)
                    continue
                self.walked[(name, install)] = now

    def _cli(self, adapter: Adapter, spec: LaunchSpec) -> hv.VerifiedInstall:
        """The launch entry's pinned install, verified (D2): never the selection, which may have moved since
        the entry was journaled. An entry without a pin (journaled before schema 3) refuses."""
        if not spec.install or not spec.install_digest:
            raise LaunchFailed(INSTALL_UNPINNED)
        return self._verified(adapter, InstallPin(spec.install, spec.install_digest))

    def _registered(self, adapter: Adapter, installed: hv.VerifiedInstall) -> None:
        """Codex T9 r1 B1: the launch's record is written, so every watch from here on lists and signals it;
        one that ran between the verify and the record wrote its marker first. So the marker is read again,
        under the watch's lock, and a marker (or one that can't be read) refuses. Otherwise the verify
        counts as the install's walk."""
        with self.walk_lock:
            try:
                marked = hv.tainted(self.c.taint_dir, adapter.name, installed.name)
            except OSError:
                marked = True
            if marked:
                raise _StepFailed(INSTALL_TAINTED)      # the record is written: `_fail` ends it
            self.walked[(adapter.name, installed.name)] = self.c.clock()

    def _logins(self, adapter: Adapter, account: str) -> tuple[Protected, tuple[tuple[str, Path], ...]]:
        """The chosen account's login, and every other account's as configured: `default` is another
        account whenever a named one is chosen (§7)."""
        try:
            chosen_dir, chosen = self.c.accounts.login_paths(adapter.name, account)
            others: list[tuple[str, Path]] = []
            dirs: list[Path] = []
            for name in (DEFAULT_ACCOUNT, *self.c.accounts.configured(adapter.name)):
                if name == account:
                    continue
                folder, files = self.c.accounts.login_paths(adapter.name, name)
                dirs.append(folder)
                others += [(name, f) for f in files]
        except ConfigError as exc:
            raise LaunchFailed(str(exc)) from None
        return Protected(chosen_dir, chosen, tuple(dirs), tuple(f for _, f in others)), tuple(others)

    @contextlib.contextmanager
    def _credlock(self, key: str, failure: type[Exception]) -> Generator[None]:
        """The account's D8 lock for the body; a lock not taken raises `failure` with its exact text."""
        try:
            with credential_lock(self.c.credlocks, key, self.c.settings.lock_wait_seconds,
                                 clock=self.c.wait_clock, sleep=self.c.sleep):
                yield
        except CredentialBusy as exc:
            raise failure(str(exc)) from None

    def _pinned(self, adapter: Adapter, spec: LaunchSpec, login_files: tuple[Path, ...]) -> _Checked:
        """E1, D8: under the account's lock, the key the entry pinned must still be the account's, and
        then the login is read for the freshness gate. The files are resolved once, and the key is checked
        against those very files, so the files read are the files the key names."""
        if not spec.credential_key:
            raise LaunchFailed(KEY_CHANGED)
        with self._credlock(spec.credential_key, LaunchFailed):
            try:
                current = self.c.accounts.current_key(adapter.name, spec.account)
            except ConfigError as exc:
                raise LaunchFailed(str(exc)) from None
            if current != spec.credential_key:
                raise LaunchFailed(KEY_CHANGED)
            try:
                files, ids = _canonical(login_files)
            except (OSError, RuntimeError):
                raise LaunchFailed(LOGIN_UNRESOLVED) from None
            if credential_key(adapter.name, files) != spec.credential_key:
                raise LaunchFailed(KEY_CHANGED)
            return _Checked(files, ids, self._fresh(adapter, files))

    def _still_checked(self, adapter: Adapter, spec: LaunchSpec, login_files: tuple[Path, ...],
                       checked: _Checked) -> None:
        """Codex r1 (PR #74): the lock is let go before the bind is made, so once the sandbox exists the
        login is checked again under the lock. The configured files must still resolve to the checked
        ones (else KEY_CHANGED), each still the same file, its expiry no earlier than the one the gate
        passed (else LOGIN_STALE). Anything swapped or rewritten in between was bound unchecked."""
        with self._credlock(spec.credential_key, _StepFailed):
            try:
                files, ids = _canonical(login_files)
            except (OSError, RuntimeError):
                raise _StepFailed(KEY_CHANGED) from None
            if files != checked.files:
                raise _StepFailed(KEY_CHANGED)
            try:
                expiry = adapter.access_expiry(files)
            except AdapterError:
                raise _StepFailed(LOGIN_STALE) from None
            if ids != checked.ids or expiry < checked.expiry:
                raise _StepFailed(LOGIN_STALE)

    def _fresh(self, adapter: Adapter, login_files: tuple[Path, ...]) -> int:
        """§7 freshness gate (D12, E3): plan 4b never refreshes, so a login that would expire within the
        session's maximum lifetime and stop margin refuses the launch with LOGIN_STALE, which AU-8 reads
        as "try the next account". Returns the expiry it checked."""
        try:
            expiry = adapter.access_expiry(login_files)
        except AdapterError as exc:
            raise LaunchFailed(str(exc)) from None
        s = self.c.settings
        if expiry - self.c.clock() <= s.max_lifetime_seconds + s.stop_margin_seconds:
            raise LaunchFailed(LOGIN_STALE)
        return int(expiry)

    def _start(self, spec: LaunchSpec, adapter: Adapter, installed: hv.VerifiedInstall, layout: SessionLayout,
               rec: SessionRecord, resume: bool, native: str | None, protected: Protected,
               others: tuple[tuple[str, Path], ...], checked: _Checked, pinned: gitwip.Pinned) -> Started:
        c, gen, cli = self.c, spec.generation, installed.cli
        run, scratch = layout.run(gen), layout.scratch(gen)
        for folder in (run, scratch):
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(mode=0o700, parents=True)
        layout.empty(gen).mkdir(mode=0o700)
        layout.empty(gen).chmod(0o555)          # E25: empty and read-only, remade before every create
        layout.home.mkdir(mode=0o700, exist_ok=True)
        facts = adapter.facts(layout, cli, c.uid)
        profile: dict[str, Any] = dict(c.settings.profiles.get(spec.profile, {}))
        if spec.model:
            profile["model"] = spec.model
        for cleared in adapter.prepare_install_home(layout, spec.worktree, installed.root, profile=profile):
            log.warning("exec_dir_cleared %s gen=%d adapter=%s path=%s files=%d links=%d dirs=%d bytes=%d",
                        spec.session_key, gen, adapter.name, cleared.path, cleared.files, cleared.links,
                        cleared.dirs, cleared.bytes)
            self._notice(spec.ws, ("cleared", spec.session_key, gen, str(cleared.path)), ExecDirCleared(
                spec.session_key, gen, adapter.name, str(cleared.path), cleared.files, cleared.links,
                cleared.dirs, cleared.bytes, cleared.names))         # B2: wsd journals it
        ws = c.settings.workstreams.get(spec.ws, WsSandbox())
        token = secrets.token_hex(16)
        shim_config = {"wait_seconds": c.settings.hook_wait_seconds,
                       "local_classes": sorted(ws.local_classes), "worktree": str(spec.worktree)}
        caps = self._caps(spec.profile)
        files = {shim.TOKEN_FILE: token, SHIM_SOURCE.name: SHIM_SOURCE.read_text(),
                 shim.CONFIG_FILE: json.dumps(shim_config), REQUEST_INSIDE.name: shim.REQUEST_WRAPPER,
                 **adapter.run_files(usage=caps.in_session_usage, limits=caps.limit_signal),
                 **self.selftest.files(),
                 INSTALL_RECORD_INSIDE.name: hv.install_record(
                     installed, [str(layout.home / p) for p in facts.empty_read_only])}
        for fname, text in files.items():
            write_private(run / fname, text)
        (run / REQUEST_INSIDE.name).chmod(0o700)
        server = self._server(rec, adapter, token, caps)
        self.servers[spec.session_key] = server     # before start: an end closes even a half-started one
        server.start()
        git_binds = sessiongit.seed(pinned, layout.git, read_only=spec.role in READ_ONLY_ROLES)
        sp = build_spec(SpecInput(spec.session_key, gen, spec.role, layout, spec.worktree,
                                  facts, checked.files, ws.extra_egress,
                                  ws.extra_ro_mounts, c.settings.probe_allowed_host, c.uid, c.gid,
                                  git_binds))
        if tuple(b.source for b in sp.logins) != checked.files:
            raise _StepFailed(KEY_CHANGED)          # a file swapped since the locked check: never bound
        check_credentials(sp, protected, c.real_home)
        # D13: the lifetime runs from the moment the login can be reached, never from the end of a slow
        # start, and the hard stop is always a full margin before the checked expiry.
        exposed = c.clock()
        # D26: from here on the private git directory may hold the agent's commits, so every end lands it.
        # Codex T12 r1 #2, r2: a create is possible from here, and the record says so before the watcher
        # starts, so the watcher never reads it as settled while this launch could still submit one, however
        # long it stalls (`reaper.settled`).
        deadline = min(exposed + c.settings.max_lifetime_seconds,
                       checked.expiry - c.settings.stop_margin_seconds)
        ledger = self._ledger()
        planned = msgspec.structs.replace(rec, deadline=deadline)
        if not self._submission(ledger, planned):
            raise _StepFailed("the create's submission can't be written")
        rec = self._phase(layout, rec, seeded=True, create_started=True, deadline=deadline)
        try:
            self._reaper(rec)
        except RuntimeUnavailable as exc:
            raise _StepFailed(str(exc)) from None
        if c.clock() >= rec.deadline:
            if not ledger.put(rec.sandbox, "settled", how="never_submitted", at=c.clock()):
                raise _StepFailed("the unsubmitted create can't be settled")
            rec = self._phase(layout, rec, create_started=False)
            raise _StepFailed("the launch stalled past its deadline before the sandbox was created")
        self.backend.create(sp, scratch)
        # Even if this write fails, persist created=True: R2 repairs it on recovery.
        ledger.put(rec.sandbox, "settled", how="returned", at=c.clock())
        rec = self._phase(layout, rec, created=True)
        self._still_checked(adapter, spec, protected.chosen_files, checked)
        if c.clock() >= rec.deadline:
            # T11 r3: the check above leaves a window up to the create's submission, so a sandbox that
            # exists only at or past its deadline is ended at once, by this launch, not left to the watcher.
            raise _StepFailed("the sandbox was created past its deadline; it was destroyed")
        rec = self._phase(layout, rec, phase=Phase.TESTING)
        ctx = ProbeContext(self.backend, c.tmux, rec.tmux_session, sp, layout, gen, adapter, cli, token,
                           others, c.real_home_canary, c.wsd_socket, c.settings, server.turns, installed)
        # D23 (Amendment 1): the exec path is the first thing run in the new sandbox, so no process but
        # OpenShell's own exists to reach the pipes `sandbox exec` gives the probe.
        self.selftest.exec_path(ctx)
        self._trust(adapter, cli, sp, layout)
        self._app_server(adapter, cli, sp, layout)
        rec = self._phase(layout, rec, phase=Phase.STARTING)
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

    def _caps(self, profile: str) -> Capabilities:
        """E15: the profile's capabilities now; none if the profile is gone. No login is read: only
        `_pinned` reads one, under its lock."""
        return self.c.accounts.capabilities_of(profile)

    def _server(self, rec: SessionRecord, adapter: Adapter, token: str, caps: Capabilities) -> SessionServer:
        """The generation's socket: it counts the adapter's turn ends and hands each hook payload to the
        adapter's parser, attributed to this generation (E8, E14)."""
        layout, key, gen = self.layout(rec.key), rec.key, rec.generation

        def heard(payload: dict[str, Any]) -> None:
            self._report(rec.ws, key, gen, caps, adapter.usage_from_event(payload))

        return SessionServer(layout.socket(gen), token, layout.events(gen), turn_ends=adapter.turn_end_events,
                             on_event=heard)

    def _report(self, ws: str, key: str, gen: int, caps: Capabilities, report: UsageReport | None) -> None:
        if report is None:
            return
        found: list[Signal] = []
        if caps.in_session_usage and report.windows:
            found.append(UsageSeen(key, gen, report.windows))
        if caps.limit_signal and report.limited:
            found.append(LimitSeen(key, gen, report.reset_hint))
        if found:
            with self.queue_lock:
                queue = self.queued.setdefault(ws, [])
                queue.extend(found)
                del queue[:-MAX_QUEUED]

    def _poll(self, rec: SessionRecord) -> None:
        """E5: run the session's in-sandbox usage read if (c) or (e) is on and it is due; `_report`
        emits each signal only under its own capability. A read that fails reports nothing and is not
        retried before the next interval."""
        adapter = self.c.adapters.get(rec.adapter)
        caps = self._caps(rec.profile)
        if adapter is None or not (caps.in_session_usage or caps.limit_signal):
            return
        now, ident = self.c.clock(), (rec.key, rec.generation)
        last = self.polled.get(ident)
        if last is not None and now - last < self.c.settings.usage_poll_seconds:
            return
        self.polled[ident] = now
        try:
            pin = InstallPin(rec.install_name, rec.install_digest)
            argv = adapter.usage_argv(self.verified_cli(adapter, pin))
            if argv is None:
                return
            r = self.backend.exec(rec.sandbox, Path(rec.worktree), argv, timeout=USAGE_SECONDS)
        except (LaunchFailed, BackendError, BackendUnavailable) as exc:
            log.warning("%s: %s", DIAG_USAGE_READ, type(exc).__name__)
            return
        report = adapter.usage_from_read(r.stdout) if r.returncode == 0 else None
        if report is None:
            log.warning("%s: %s", DIAG_USAGE_READ, _read_reason(r))
            return
        self._report(rec.ws, rec.key, rec.generation, caps, report)

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
                    rec, phase=Phase.ENDED if gone else Phase.STOPPING, unlanded=True, error=RECORD_LOST,
                    end_cause=EndCause.REFUSED))
            return LaunchUncertain(reason)
        rec = msgspec.structs.replace(current, error=reason)
        if self._end(rec, interrupt=False, cause=EndCause.REFUSED):
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
            adapter = self.c.adapters[rec.adapter]
            server = self._server(rec, adapter, token, self._caps(rec.profile))
        except (OSError, KeyError):
            raise RuntimeUnavailable("a live session's socket can't be served") from None
        try:
            server.start()
        except OSError:
            server.close()
            raise RuntimeUnavailable("a live session's socket can't be served") from None
        self.servers[rec.key] = server

    def _end(self, rec: SessionRecord, *, interrupt: bool, lifetime: bool = False,
             cause: EndCause = EndCause.DIED) -> bool:
        """End the session: interrupt it if asked, kill its pane, delete every sandbox of its key. True
        (recorded `ended`) only once the pane is gone and the backend no longer lists any of them.

        A lifetime end (D13) records its reason and the WIP commit it owes in the same write that records
        `stopping`, before anything is stopped, so a crash on either side of any later step replays it.
        That write also records the end's cause (g08sd, `_cause`), once: a replay never changes it. A
        healthy cause is evidence only this write keeps, so if it fails nothing is stopped: the end is
        unconfirmed and the next pass retries it (the reaper still bounds a lifetime). Any other end
        still stops, since a restart would find the same cause."""
        layout = self.layout(rec.key)
        ledger = self._ledger()
        self._recover_create(ledger, rec)
        was_settled = creates.settled(ledger.directory, rec.sandbox)
        owed: dict[str, str] = {}
        if lifetime or rec.stop_reason == "lifetime":
            mark = rec.wip_mark or f"lifetime:{rec.key}:{rec.generation}"
            owed = {"stop_reason": "lifetime", "wip_mark": mark}
        if not rec.end_cause:
            owed["end_cause"] = self._cause(rec, cause)
        try:
            rec = self._phase(layout, rec, phase=Phase.STOPPING, **owed)
            journaled = True
        except OSError:
            if owed.get("end_cause") in (EndCause.STOPPED, EndCause.LIFETIME):
                return False                 # code r1: never end a healthy session without its cause
            journaled = False                # stop it anyway; landing and `ended` wait for a durable record
        if not self._teardown(rec, interrupt=interrupt) or not journaled:
            return False                     # the reaper stays: it is the backstop until the end is confirmed
        self._watch(self.c.clock(), due_only=False)    # T9: the tree as the generation left it, `stopping`
        if was_settled:
            ledger.put(rec.sandbox, "gone", by="teardown", at=self.c.clock())
        if creates.gone(ledger.directory, rec.sandbox) and ledger.close_notice(rec.sandbox, self.c.clock()):
            with contextlib.suppress(*TMUX_ERRORS):
                self.c.tmux.kill(self.reaper_name(rec.key, rec.generation))
        try:
            rec = self._land(layout, rec)    # D26: before any host git (the lifetime WIP, a park) runs
            rec = self._phase(layout, rec, phase=Phase.ENDED, ended_at=self.c.clock())
        except OSError:
            return False
        self.polled = {k: v for k, v in self.polled.items() if k[0] != rec.key}
        with contextlib.suppress(OSError):
            layout.reaped(rec.generation).unlink(missing_ok=True)   # the cause is in the record now
        with contextlib.suppress(OSError):
            self._settle_wip(layout, rec)    # a lost record write keeps the mark; the replay is idempotent
        return True

    def _cause(self, rec: SessionRecord, asked: EndCause) -> EndCause:
        """g08sd: a stop proves health only if the runtime begins it on a generation it confirms live, here,
        before the stop, or if the reaper recorded it live at its deadline. Anything else, absence found
        late included, is a death. The marker is read after liveness: the reaper makes it durable before it
        kills, so a pane found dead because the reaper just killed it still finds its marker."""
        if asked is EndCause.REFUSED:
            return asked
        if rec.phase is Phase.RUNNING:
            if asked in (EndCause.STOPPED, EndCause.LIFETIME) and self._alive(rec, self._names()):
                return asked
            if self._reaped_live(rec):
                return EndCause.LIFETIME
        if rec.phase is Phase.STOPPING:          # a stop begun before g08sd, with no cause recorded
            return EndCause.LEGACY_LIFETIME if rec.stop_reason == "lifetime" else EndCause.DIED
        return EndCause.DIED

    def _reaped_live(self, rec: SessionRecord) -> bool:
        """Whether the reaper's marker names this generation's sandbox. A link, a special file or a
        malformed marker is none; one that can't be read is unavailable, never assumed absent."""
        path = self.layout(rec.key).reaped(rec.generation)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return False
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return False
            raise RuntimeUnavailable("the reaper's record can't be read") from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                return False
            with os.fdopen(fd, "rb") as f:
                data = f.read(MARKER_BYTES)
        except OSError:
            raise RuntimeUnavailable("the reaper's record can't be read") from None
        try:
            fields = json.loads(data)
        except ValueError:
            return False
        if not isinstance(fields, dict):
            return False
        marker: dict[str, object] = fields  # pyright: ignore[reportUnknownVariableType]
        generation = marker.get("generation")
        return (marker.get("sandbox") == rec.sandbox and isinstance(generation, int)
                and not isinstance(generation, bool) and generation == rec.generation)

    def _drop_markers(self, layout: SessionLayout) -> None:
        """Remove every reaper marker (and temporary file) of the session: called before a new generation's
        record is written, when every earlier generation has ended and been accounted."""
        if not layout.root.is_dir():
            return
        for path in layout.root.glob("reaped-r*"):
            path.unlink(missing_ok=True)

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
            ledger = self._ledger()
            confirmed: list[bool] = []
            for name in mine:
                # Unknown pre-upgrade/reappearing siblings have no create to recover.
                # Delete them without inventing an unreadable observed-only entry.
                if ledger.submission(name) is not None or creates.settled(ledger.directory, name):
                    ledger.observe(name, "teardown", self.c.clock())
                confirmed.append(self.backend.delete(name))
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

    def _unlanded(self, rec: SessionRecord) -> RuntimeUnavailable:
        """The hold's detail names the session and the command that shows it: never a path."""
        return RuntimeUnavailable(f"session {short_id(rec.key)} generation {rec.generation}: its commits "
                                  f"were not landed; see `wsctl unlanded {rec.ws}` (docs/wsd.md §5)")

    def _overdue(self, rec: SessionRecord) -> bool:
        """A session that ran is past its deadline: whatever ends it now ends it for its lifetime."""
        return rec.started_at > 0 and self.c.clock() >= rec.deadline

    def _claim_branch(self, spec: LaunchSpec) -> None:
        """Refuse a launch while another key's record of this bead holds the branch ``btq/<bead>``.

        A record holds it while it is not ENDED (it still owes its land or lifetime WIP, D13), is unlanded,
        or owes a WIP that can't be settled now. Every record is checked before anything is settled, so a
        refusal moves nothing. This relies on wsd serializing launches per workstream (the Parker's entry
        lock); it is not a cross-process lock, and two concurrent direct callers could both pass.
        """
        others = [r for r in self._records() if r.key != spec.session_key and r.bead == spec.bead
                  and _same_repo(r.repo, spec.repo)]
        if any(r.phase is not Phase.ENDED or r.unlanded for r in others):
            raise LaunchFailed(BRANCH_TAKEN)
        for other in others:
            if other.wip_mark:
                with contextlib.suppress(OSError):          # D13: the WIP it owes lands before anyone else's
                    other = self._settle_wip(self.layout(other.key), other)
                if other.wip_mark:
                    raise LaunchFailed(BRANCH_TAKEN)

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

    # --- unlanded sessions: the operator's view and resolve (btq-xb56t, docs/wsd.md §5) ---

    def _resolutions(self) -> Path:
        return self.c.sessions.parent / "resolutions"

    def _intent_path(self, sid: str, generation: int) -> Path:
        return self._resolutions() / f"{sid}-g{generation}.json"

    def _trash(self, sid: str, generation: int) -> Path:
        """Beside the intents, on the same filesystem as `sessions/` and outside it: one rename takes the
        whole session directory out of `_records`' sight."""
        return self._resolutions() / f"{sid}-g{generation}.trash"

    def _read_intent(self, sid: str, generation: int) -> Intent | None:
        return self._intent_at(self._intent_path(sid, generation))

    def _intent_at(self, path: Path) -> Intent | None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise RuntimeUnavailable("a resolve's intent can't be read") from None
        try:
            return msgspec.json.decode(data, type=Intent)
        except msgspec.MsgspecError:
            raise RuntimeUnavailable("a resolve's intent is unreadable") from None

    def _intents(self) -> list[Intent]:
        folder = self._resolutions()
        if not os.path.lexists(folder):
            return []
        try:
            paths = sorted(p for p in folder.iterdir() if p.name.endswith(".json"))
        except OSError:
            raise RuntimeUnavailable("the resolves' intents can't be listed") from None
        found = [self._intent_at(p) for p in paths]
        return [i for i in found if i is not None]

    def _held(self, *, ws: str | None = None, key: str | None = None) -> None:
        """A resolve's intent holds its workstream from its first write until it is removed."""
        for intent in self._intents():
            if intent.ws == ws or intent.key == key:
                raise RuntimeUnavailable(f"an unlanded resolve of session {short_id(intent.key)} is "
                                         "unfinished; run `wsctl resolve` for it again")

    def _write_intent(self, intent: Intent) -> Intent:
        """Atomic and durable: a temporary file, an fsync, the rename, an fsync of `resolutions/`. `<state>`
        is synced first, so the entry of a `resolutions/` just created is on disk before anything rests on
        an intent in it."""
        private_dir(self._resolutions())
        _sync_dir(self._resolutions().parent)
        write_private(self._intent_path(short_id(intent.key), intent.generation),
                      msgspec.json.encode(intent).decode())
        return intent

    @contextlib.contextmanager
    def _inspection(self) -> Generator[Path]:
        """A fresh path for one snapshot under `<state>/inspect/` (wsd-owned, outside every bind), removed
        when done. The first in this process first clears what an earlier wsd left: wsd holds its
        instance lock for as long as it runs, and every snapshot is this process's."""
        root = self.c.sessions.parent / "inspect"
        with self._inspect_lock:
            # Cleared here, once per process, not when the runtime is built: build_runtime runs before wsd
            # takes its instance lock, and clearing then could remove a live wsd's snapshot. Every snapshot
            # runs in the process that holds the lock, so anything here at the first one is a dead wsd's.
            if not self._inspect_cleared:
                if root.is_symlink():
                    root.unlink()
                elif os.path.lexists(root):
                    shutil.rmtree(root)
                self._inspect_cleared = True
            private_dir(root)
        dest = root / secrets.token_hex(8)
        try:
            yield dest
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    def _running(self, sid: str) -> str:
        """Why something may still write the session's directory, or "": its pane or a reaper of any
        generation on wsd's tmux server, or a sandbox of any generation. A listing that fails is
        RuntimeUnavailable, never taken as absent."""
        ledger = self._ledger()
        try:
            entries = ledger.names()
        except OSError:
            raise RuntimeUnavailable("the create ledger can't be listed") from None
        for name in entries:
            if name.startswith(f"{sid}g") and not creates.gone(ledger.directory, name):
                return f"an earlier create of generation {name.rsplit('g', 1)[1]} is not settled and deleted"
        try:
            panes = self.c.tmux.session_names()
        except TMUX_ERRORS:
            raise RuntimeUnavailable("wsd's tmux sessions can't be listed") from None
        reaper = re.compile(rf"wsd-{sid}-r[0-9]+")
        if f"wsd-{sid}" in panes or any(reaper.fullmatch(n) for n in panes):
            return "its pane or a reaper is on wsd's tmux server"
        if any(re.fullmatch(rf"{sid}g[0-9]+", n) for n in self._names()):
            return "the backend lists a sandbox of it"
        return ""

    def _snap(self, rec: SessionRecord, dest: Path) -> tuple[gitwip.Pinned | None, sessiongit.Snapshot]:
        """The session's objects, snapshotted. A repository that can't be pinned leaves the tip unread."""
        if not rec.repo or not rec.worktree:
            return None, sessiongit.Snapshot(dest, None, "the session record names no repository")
        try:
            p = gitwip.pin(Path(rec.repo), Path(rec.worktree), f"btq/{rec.bead}")
        except gitwip.GitFailed as exc:
            return None, sessiongit.Snapshot(dest, None, str(exc))
        try:
            return p, sessiongit.snapshot(p, self.layout(rec.key).git, dest)
        except (gitwip.GitFailed, OSError):
            raise ResolveFailed("the snapshot store can't be made") from None

    def unlanded(self, ws: str) -> list[UnlandedView]:
        views: list[UnlandedView] = []
        for rec in self._records():
            if rec.ws != ws or not rec.unlanded:
                continue
            sid = short_id(rec.key)
            view = UnlandedView(sid, rec.generation, rec.bead, f"btq/{rec.bead}")
            if rec.phase is not Phase.ENDED or self._running(sid):
                views.append(dataclasses.replace(view, running=True))      # no snapshot while it may write
                continue
            with self._inspection() as dest:
                views.append(self._view(rec, view, dest))
        return views

    def _view(self, rec: SessionRecord, view: UnlandedView, dest: Path) -> UnlandedView:
        try:
            p, snap = self._snap(rec, dest)
        except ResolveFailed as exc:
            return dataclasses.replace(view, error=str(exc))
        if p is None or snap.tip is None:
            return dataclasses.replace(view, error=snap.error)
        try:
            landed = sessiongit.landed(p)
            cmp = sessiongit.compare(p, snap, landed)
        except gitwip.GitFailed:
            return dataclasses.replace(view, tip=snap.tip,
                                       error="the session's tip can't be compared with the landed one")
        return dataclasses.replace(view, landed=landed, tip=snap.tip, ahead=cmp.ahead, behind=cmp.behind,
                                   fast_forward=cmp.behind == 0,
                                   subjects=tuple(printable(raw) for raw in cmp.subjects))

    def resolve_unlanded(self, ws: str, sid: str, generation: int, action: Action,
                         tip: str | None) -> Resolution:
        if not SHORT_ID.fullmatch(sid):
            raise ResolveRefused("that is not a session's short ID")
        intent = self._read_intent(sid, generation)
        if intent is not None:
            # A replay: the whole identity first, before anything is read or changed. From here on the
            # intent, not the request, supplies every value.
            if (intent.ws, intent.generation, intent.action, intent.tip) != (ws, generation, action, tip):
                raise ResolveRefused("an unfinished resolve of this session asked for something else; "
                                     "repeat that request")
            return self._advance(intent, None)
        for other in self._intents():
            if other.ws == ws or short_id(other.key) == sid:     # name the one to finish first
                raise ResolveRefused(f"an unfinished resolve of session {short_id(other.key)} generation "
                                     f"{other.generation} comes first; repeat that request")
        rec = self._resolvable(ws, sid, generation, None)
        if why := self._running(sid):
            raise ResolveRefused(f"the session may still run: {why}")
        with self._inspection() as dest:
            p, snap = self._snap(rec, dest)
            if tip != snap.tip:
                raise ResolveRefused(_tip_refusal(action, snap, tip))
            ref = keep_ref(rec.bead, sid, generation) if action == "keep" else ""
            if action == "land":
                self._can_land(p, snap)
            elif action == "keep":
                self._can_keep(p, snap, rec.bead, ref)
            end = _end_of(rec)                  # wpyv5: in the intent before the record can go
            try:
                st = os.lstat(self.c.sessions / sid)
                intent = self._write_intent(Intent(ws=ws, bead=rec.bead, key=rec.key, generation=generation,
                                                   tip=tip, action=action, ref=ref, step=CONFIRMED,
                                                   dir_id=(st.st_dev, st.st_ino), end_cause=end.cause.value,
                                                   reached_running=end.reached_running,
                                                   ran_seconds=end.ran_seconds))
            except OSError:
                raise ResolveFailed("the resolve's intent can't be written; nothing was changed") from None
            return self._advance(intent, (p, snap))

    def _resolvable(self, ws: str, sid: str, generation: int, key: str | None) -> SessionRecord:
        """The record a resolve needs before its directory moves: this workstream's, of this generation,
        ended and unlanded (and, on a replay, the intent's own)."""
        rec = read_record(SessionLayout(self.c.sessions / sid))
        if rec is None:
            raise ResolveRefused(NO_RECORD)
        if short_id(rec.key) != sid or rec.ws != ws or key is not None and rec.key != key:
            raise ResolveRefused("the session is not one of this workstream's")
        if rec.generation != generation:
            raise ResolveRefused("the session's generation is not the one given")
        if rec.phase is not Phase.ENDED:
            raise ResolveRefused("the session has not ended")
        if not rec.unlanded:
            raise ResolveRefused("the session's commits are not unlanded")
        return rec

    def _can_land(self, p: gitwip.Pinned | None, snap: sessiongit.Snapshot) -> None:
        if p is None or snap.tip is None:
            raise ResolveRefused("the session's tip can't be read")
        try:
            landed = sessiongit.landed(p)
        except gitwip.GitFailed:
            raise ResolveRefused("the bead branch can't be read") from None
        if landed != snap.tip and not sessiongit.fast_forward(p, snap, landed):
            raise ResolveRefused(NO_FAST_FORWARD)

    def _can_keep(self, p: gitwip.Pinned | None, snap: sessiongit.Snapshot, bead: str, ref: str) -> None:
        """The keep ref is free, or a replay's (already at the tip), and no ref takes its parents' names."""
        if p is None or snap.tip is None:
            raise ResolveRefused("the session's tip can't be read")
        try:
            at = sessiongit.ref_at(p, ref)
            parents = ("refs/heads/unlanded", f"refs/heads/unlanded/{bead}")
            taken = [sessiongit.ref_at(p, name) for name in parents]
        except gitwip.GitFailed:
            raise ResolveRefused(KEEP_REFUSED) from None
        if at not in (None, snap.tip) or any(taken):
            raise ResolveRefused(KEEP_REFUSED)

    def _act(self, intent: Intent, p: gitwip.Pinned | None, snap: sessiongit.Snapshot) -> None:
        """The action, idempotent: a land already at the tip, or a keep ref already there, is done. Either
        way the repository's packs and the ref are made durable before it returns, since the caller then
        records the import and deletes the session's own copy."""
        if intent.action == "discard":
            return
        tip = intent.tip
        if p is None or tip is None or snap.tip != tip:
            raise ResolveRefused("the session's tip is no longer the confirmed one")
        ref = f"refs/heads/{p.branch}" if intent.action == "land" else intent.ref
        try:
            if intent.action == "land":
                landed = sessiongit.landed(p)
                if landed != tip:
                    if not sessiongit.fast_forward(p, snap, landed):
                        raise ResolveRefused(NO_FAST_FORWARD)
                    sessiongit.import_snapshot(p, snap, tip, ref, landed)
            else:
                at = sessiongit.ref_at(p, ref)
                if at is not None and at != tip:
                    raise ResolveRefused(KEEP_REFUSED)
                if at is None:
                    try:
                        sessiongit.import_snapshot(p, snap, tip, ref, ZERO)
                    except gitwip.GitFailed:
                        raise ResolveFailed(KEEP_REFUSED) from None
            sessiongit.make_durable(p, ref)
        except gitwip.GitFailed:
            raise ResolveFailed("the session's commits could not be imported") from None

    def _step(self, intent: Intent, step: str) -> Intent:
        return self._write_intent(msgspec.structs.replace(intent, step=step))

    def _advance(self, intent: Intent, live: tuple[gitwip.Pinned | None, sessiongit.Snapshot] | None
                 ) -> Resolution:
        """Run the resolve on from its last checkpoint, reconciled against the filesystem, since a crash can
        come between a side effect and its checkpoint (docs/wsd.md §5). `live` is a first run's snapshot."""
        sid, gen = short_id(intent.key), intent.generation
        here, trash = self.c.sessions / sid, self._trash(sid, gen)
        if live is None and (why := self._running(sid)):
            raise ResolveRefused(f"the session may still run: {why}")
        try:
            at_here, at_trash, want = _ident(here), _ident(trash), tuple(intent.dir_id)
            step = intent.step
            if step in (CONFIRMED, IMPORTED) and at_here == want and at_trash is None:
                if live is None:
                    rec = self._resolvable(intent.ws, sid, gen, intent.key)
                    with self._inspection() as dest:
                        self._act(intent, *self._snap(rec, dest))
                else:
                    self._act(intent, *live)
                self.cp("imported")
                intent = self._step(intent, IMPORTED)
                self.cp("imported.recorded")
                here.rename(trash)
                _sync_dir(self.c.sessions)
                _sync_dir(self._resolutions())
                self.cp("moved")
                intent = self._step(intent, MOVED)
                self.cp("moved.recorded")
            elif step == IMPORTED and at_here is None and at_trash == want:
                intent = self._step(intent, MOVED)            # the rename happened before its checkpoint
            elif not (step == MOVED and at_here is None and at_trash in (None, want)
                      or step == REMOVED and at_here is None and at_trash is None):
                raise ResolveRefused(INCONSISTENT)
            if intent.step == MOVED:
                if os.path.lexists(trash):
                    shutil.rmtree(trash)
                if os.path.lexists(trash):
                    raise ResolveFailed(STEP_FAILED)
                _sync_dir(self._resolutions())
                self.cp("removed")
                intent = self._step(intent, REMOVED)
                self.cp("removed.recorded")
        except OSError:
            raise ResolveFailed(STEP_FAILED) from None
        return Resolution(intent.ws, intent.bead, sid, gen, intent.action, intent.tip, intent.ref, intent.key,
                          _intent_end(intent))

    def finish_resolution(self, ws: str, sid: str, generation: int) -> None:
        intent = self._read_intent(sid, generation)
        if intent is None:
            return
        if intent.ws != ws or intent.step != REMOVED:
            raise ResolveFailed("the resolve is not finished; run the same wsctl resolve again")
        try:
            self._intent_path(sid, generation).unlink()
            _sync_dir(self._resolutions())
        except OSError:
            raise ResolveFailed("the resolve's intent can't be removed; run the same wsctl resolve again") \
                from None


def _read_reason(r: ExecResult) -> str:
    """Why a read gave no reading, from its status and the script's own diagnostic only: never its
    output. Only exit 0 is a reading, so 127 (nothing ran) is a failure like any other."""
    if r.returncode == 0:
        return "unparsed"
    found = READ_REASON.fullmatch(r.stderr)
    return found.group(1).decode() if found and r.returncode == 1 else f"exit {r.returncode}"


def _tip_refusal(action: Action, snap: sessiongit.Snapshot, tip: str | None) -> str:
    """Why the confirmation doesn't match what the snapshot read."""
    if snap.tip is None:
        if action != "discard":
            return f"the session's tip can't be read ({snap.error}); it can only be discarded"
        return f"the session's tip can't be read ({snap.error}); discard it without --tip"
    if tip is None:
        return "the session's tip is readable: confirm the discard with --tip, as wsctl unlanded shows it"
    return "the session's tip is not the one given; see wsctl unlanded"
