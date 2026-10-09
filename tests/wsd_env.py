"""A wsd test rig: a fake queue, a fake runtime, a real temp git repository and a real journal.

`restart()` models wsd dying and starting again: a new Journal on the same file and new wsd objects,
while the queue, the repository and the agent sessions (other processes) carry on.
"""

import sqlite3
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from fakes.checkpoints import Recorder
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime

from heterodyne.config.accounts import Account
from heterodyne.wsd.accounts import ConfiguredAccounts, Failover, ProfileAccounts
from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.headroom import Mark
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import ActionReconciler, HoldingReconciler, LaunchSpec
from heterodyne.wsd.scheduler import Outcome, Scheduler, Trigger, TriggerKind
from heterodyne.wsd.states import BeadState
from heterodyne.wsd.workstream import Deps, Limits, WorkstreamSettings, place, record

WS = "alpha"
PROFILES = frozenset({"p-one", "p-two"})
ADAPTERS = {"p-one": "claude-code", "p-two": "codex"}


def login_home(path: Path) -> Path:
    """A scratch HOME with a default login for each adapter: `~/.claude/.credentials.json` and
    `~/.codex/auth.json` (fake contents)."""
    for login in (path / ".claude" / ".credentials.json", path / ".codex" / "auth.json"):
        login.parent.mkdir(parents=True, exist_ok=True)
        login.write_text("{}")
    return path


def accounts_at(home: Path) -> ConfiguredAccounts:
    return ConfiguredAccounts(ADAPTERS, {"HOME": str(home)})


class Clock:
    """wsd's injected clock (UTC epoch seconds), moved only by the test."""

    def __init__(self, now: int = 1_800_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@example.org",
                                                   "commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


@dataclass
class Rig:
    root: Path
    world: World
    runtime: FakeRuntime
    repo: Path
    ws: WorkstreamSettings
    cp: Checkpoint = field(default_factory=Recorder)
    reconciler: ActionReconciler | None = None
    clock: Clock = field(default_factory=Clock)
    journal: Journal = field(init=False)
    beads: BeadsAdapter = field(init=False)
    gate: ClaimGate = field(init=False)
    deps: Deps = field(init=False)
    parker: Parker = field(init=False)
    sched: Scheduler = field(init=False)

    def __post_init__(self) -> None:
        self.restart(self.cp)

    def restart(self, cp: Checkpoint | None = None) -> None:
        if hasattr(self, "journal"):
            self.journal.close()
        self.cp = cp if cp is not None else Recorder()
        self.journal = Journal(self.root / "state" / "wsd.db")
        self.beads = BeadsAdapter(factory(self.world))
        self.gate = ClaimGate(self.root / "state" / "claims", self.beads, self.cp)
        self.deps = Deps(self.journal, self.beads, self.gate, self.runtime,
                         self.reconciler or HoldingReconciler(self.beads), self.cp, self.clock)
        self.parker = Parker(self.ws, self.deps)
        self.sched = Scheduler(self.ws, self.deps, self.parker)

    def start(self, bead: str) -> None:
        """What a pickup does for one bead, without the pickup op: claim, worktree, record, launch."""
        self.gate.claim(WS, bead)
        spot = place(self.ws, self.beads.show(WS, bead))
        self.beads.worktree(WS, bead, spot.repo)
        self.beads.ensure_record(WS, bead, record(self.ws, spot))
        self.runtime.launch(LaunchSpec(WS, bead, self.ws.coder_role, spot.profile, spot.session_key,
                                       spot.label, spot.worktree, resume=False))
        for step in (BeadState.CLAIMING, BeadState.STARTING, BeadState.RUNNING):
            self.journal.set_state(WS, bead, step)

    def key(self, bead: str) -> str:
        """The coder session key of a started bead, from its launched-session record."""
        found = self.beads.show(WS, bead).record()
        assert found is not None
        return found.session_key

    def replay_open(self) -> None:
        for op in self.journal.ops_open(WS):
            self.parker.replay(op)

    def pickup(self, kind: TriggerKind = TriggerKind.BACKSTOP, ref: str | None = None) -> Outcome:
        return self.sched.pickup(Trigger(kind, ref))

    def worktree(self, bead: str) -> Path:
        return self.repo.parent / f"{self.repo.name}-btq-{bead}"

    def state(self, bead: str) -> str | None:
        row = self.journal.state(WS, bead)
        return None if row is None else row.state.value


def make_rig(tmp_path: Path, cp: Checkpoint | None = None, limits: Limits | None = None) -> Rig:
    world = World(tmp_path / "btq-state")
    repo = git_repo(tmp_path / "repos" / "proj")
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES, limits or Limits(),
                            accounts=accounts_at(login_home(tmp_path / "home")))
    return Rig(tmp_path, world, FakeRuntime(), repo, ws, cp if cp is not None else Recorder())


class Worker(threading.Thread):
    """A test thread that keeps what its body raised, so `finish` can report it."""

    def __init__(self, body: Callable[[], object], name: str) -> None:
        super().__init__(name=name, daemon=True)
        self.body = body
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.body()
        except BaseException as exc:  # noqa: BLE001 - re-raised by finish() in the test's thread
            self.error = exc


def finish(*threads: Worker) -> None:
    """Join every thread that started, then require each to have ended without raising."""
    started = [t for t in threads if t.ident is not None]
    for t in started:
        t.join(10)
    assert [t.name for t in started if t.is_alive()] == []
    errors = [t.error for t in started if t.error is not None]
    if errors:
        raise errors[0]


class ProbedLock:
    """The operation lock, instrumented: before the thread named `who` waits on it, that thread probes it
    without blocking and records the answer, so a test knows the caller asked for the lock and was
    refused (not merely that it reached the door)."""

    def __init__(self, real: threading.RLock, who: str) -> None:
        self.real = real
        self.who = who
        self.probes: list[str] = []
        self.asked = threading.Event()

    def __enter__(self) -> None:
        if threading.current_thread().name == self.who and not self.asked.is_set():
            got = self.real.acquire(blocking=False)
            self.probes.append("acquired" if got else "blocked")
            self.asked.set()
            if got:
                return
        self.real.acquire()

    def __exit__(self, *_exc: object) -> None:
        self.real.release()


def probe_lock(rig: Rig, who: str) -> ProbedLock:
    """Install a ProbedLock as the rig's operation lock (after any restart that rebuilds the Parker)."""
    lock = ProbedLock(rig.parker.lock, who)
    rig.parker.lock = lock  # pyright: ignore[reportAttributeAccessIssue] - a test double for the RLock
    return lock


class At(Recorder):
    """Run `fn` once, the first time `point` is reached (another process acting at that moment)."""

    def __init__(self, point: str, fn: Callable[[], object]) -> None:
        super().__init__()
        self.point = point
        self.fn = fn
        self.fired = False

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point and not self.fired:
            self.fired = True
            self.fn()


def own_profile(rig: Rig, profile: str, failover: Failover = "none") -> str:
    """Add `profile` (codex) with its own named account, whose login is its own file under the rig's
    HOME, so it has a credential key no other profile shares. Returns that key. Rebuilds the rig."""
    accounts = rig.ws.accounts
    assert isinstance(accounts, ConfiguredAccounts)
    login = Path(accounts.env["HOME"]) / f".codex-{profile}"
    login.mkdir(parents=True, exist_ok=True)
    (login / "auth.json").write_text("{}")
    accounts.named[("codex", profile)] = Account(profile, "codex", str(login), login, (), "")
    accounts.profiles[profile] = ProfileAccounts("codex", (profile,), failover)
    rig.ws = replace(rig.ws, profiles=rig.ws.profiles | {profile})
    rig.restart(rig.cp)
    return accounts.current_key("codex", profile)


def profile_key(rig: Rig, profile: str) -> str:
    accounts = rig.ws.accounts
    assert accounts is not None
    [candidate] = accounts.view(profile).accounts
    return candidate.key


def block(rig: Rig, key: str, seconds: int) -> int:
    """A trusted exhaustion mark on `key`, observed now, until `seconds` from now. Returns its until."""
    until = rig.clock() + seconds
    rig.journal.exhausted_put(key, Mark(until, rig.clock(), 0), rig.journal.usage_seq_next())
    return until


def on_profile(rig: Rig, bead: str, profile: str) -> None:
    """The bead's `role:coder=<profile>` override."""
    rig.world.beads[bead].labels.append(f"role:coder={profile}")


class LockAt(Recorder):
    """Another process takes the journal's write lock at `point`, and wsd's connection does not wait."""

    def __init__(self, point: str, db: Path) -> None:
        super().__init__()
        self.point = point
        self.db = db
        self.other: sqlite3.Connection | None = None

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point and self.other is None:
            self.other = sqlite3.connect(self.db, isolation_level=None)
            self.other.execute("BEGIN IMMEDIATE")


def repoint_codex(rig: Rig, to: str) -> None:
    """Repoint the default codex login (`~/.codex/auth.json`) to another file, or back (`to` = "")."""
    accounts = rig.ws.accounts
    assert isinstance(accounts, ConfiguredAccounts)
    repoint_codex_at(Path(accounts.env["HOME"]), to)


def repoint_codex_at(home: Path, to: str) -> None:
    link = home / ".codex" / "auth.json"
    link.unlink()
    if to:
        other = home / f".codex-{to}" / "auth.json"
        other.parent.mkdir(exist_ok=True)
        other.write_text("{}")
        link.symlink_to(other)
    else:
        link.write_text("{}")
