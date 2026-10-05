"""A wsd test rig: a fake queue, a fake runtime, a real temp git repository and a real journal.

`restart()` models wsd dying and starting again: a new Journal on the same file and new wsd objects,
while the queue, the repository and the agent sessions (other processes) carry on.
"""

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fakes.checkpoints import Recorder
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime

from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import ActionReconciler, HoldingReconciler, LaunchSpec
from heterodyne.wsd.scheduler import Outcome, Scheduler, Trigger, TriggerKind
from heterodyne.wsd.states import BeadState
from heterodyne.wsd.workstream import Deps, Limits, WorkstreamSettings, place, record

WS = "alpha"
PROFILES = frozenset({"p-one", "p-two"})


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
                         self.reconciler or HoldingReconciler(self.beads), self.cp)
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
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES, limits or Limits())
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
