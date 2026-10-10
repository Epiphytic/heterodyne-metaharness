"""AU-4 §8: the defer tail, undefer and release end to end, on the crash-point harness.

Every crash point is checked by one of two oracles. The completed-run oracle replays a crashed run (a new
wsd over the same journal, its recovery, then two pickups) and requires the end state of the same scenario
run without a crash: the deferral records, labels, `wsd-defer` comments, row, alerts, launches and WIP
commits. The uncertainty oracle covers the guard's UNRECEIPTED points, where the replay escalates instead.
"""

import gc
import json
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fakes.checkpoints import Recorder, SimulatedCrash
from fakes.fake_runtime import FakeRuntime
from wsd_env import WS, Rig, block, make_rig, on_profile, profile_key, repoint_codex

from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import DEFERRED, HELD, NEEDS_HUMAN, PARKED
from heterodyne.wsd.journal import DeferralRow, Journal
from heterodyne.wsd.park import (
    ACCOUNT_CHANGED,
    ACCOUNT_CHANGED_EVENT,
    DEFER_MARK,
    DEFER_STEPS,
    GUARD_STEPS,
    QUOTA,
    RELEASE_REGATE_POINTS,
    UNDEFER_POINTS,
    UNRECEIPTED,
    UNTRUSTED,
    NotReleasable,
)
from heterodyne.wsd.recovery import recover
from heterodyne.wsd.runtime import LaunchSpec, Started
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.states import BeadState, Reason
from heterodyne.wsd.sweep import sweep

B = "btq-1"
HOUR = 3600
DIRTY = "wip.txt"


@pytest.fixture(autouse=True)
def _close_journals(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every journal a test opened (a rig keeps its last one open), so their descriptors don't
    close later inside another test's count of open descriptors (test_wsd_gate checks that a refusal
    leaks none)."""
    opened: list[Journal] = []
    real = Journal.__init__

    def tracked(self: Journal, *args: Any, **kwargs: Any) -> None:
        opened.append(self)
        real(self, *args, **kwargs)

    monkeypatch.setattr(Journal, "__init__", tracked)
    yield
    for each in opened:
        if hasattr(each, "db"):
            each.close()
    gc.collect()


class PeakRuntime(FakeRuntime):
    """The fake runtime, also counting the most coder sessions it ever listed at once."""

    def __init__(self) -> None:
        super().__init__()
        self.peak = 0

    def launch(self, spec: LaunchSpec) -> Started:
        try:
            return super().launch(spec)
        finally:
            self.peak = max(self.peak, len(self.coders(spec.ws)))


class Script(Recorder):
    """Run each action the first time its point is reached (another process acting then), and crash
    once at `crash`, after that point's action."""

    def __init__(self, actions: dict[str, Callable[[], object]], crash: str | None = None) -> None:
        super().__init__()
        self.actions = dict(actions)
        self.crash = crash
        self.fired = False

    def __call__(self, name: str) -> None:
        super().__call__(name)
        action = self.actions.pop(name, None)
        if action is not None:
            action()
        if name == self.crash and not self.fired:
            self.fired = True
            raise SimulatedCrash(name)


type Actions = Callable[[Rig], dict[str, Callable[[], object]]]


def no_actions(_rig: Rig) -> dict[str, Callable[[], object]]:
    return {}


@dataclass(frozen=True)
class Scenario:
    setup: Callable[[Rig], object]          # uncrashed, before the act
    act: Callable[[Rig], object]            # the operation whose points are crashed
    actions: Actions = no_actions           # other processes during the act
    then: Callable[[Rig], object] = lambda _rig: None     # checked on the replayed and the uncrashed run


@dataclass
class Run:
    rig: Rig
    seen: list[str]
    before: int = 0          # deferral records before the act
    at_crash: list[DeferralRow] = field(default_factory=list[DeferralRow])
    texts: dict[str, str] = field(default_factory=dict[str, str])


def rig_for(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.runtime = PeakRuntime()
    rig.restart()
    return rig


def dirty(rig: Rig, text: str, bead: str = B) -> None:
    (rig.worktree(bead) / DIRTY).write_text(text)


def settle(rig: Rig) -> None:
    """A new wsd over the journal: recovery, then two pickups."""
    rig.restart()
    assert recover(rig.sched).ok
    rig.pickup()
    rig.pickup()


def run(tmp_path: Path, scenario: Scenario, crash: str | None = None) -> Run:
    rig = rig_for(tmp_path)
    scenario.setup(rig)
    before = len(records(rig))
    if rig.worktree(B).is_dir():
        dirty(rig, "dirty before the act")
    script = Script(scenario.actions(rig), crash)
    rig.restart(script)
    if crash is None:
        scenario.act(rig)
    else:
        with pytest.raises(SimulatedCrash):
            scenario.act(rig)
        assert script.fired
    seen, at_crash = list(script.seen), records(rig)
    settle(rig)
    scenario.then(rig)
    return Run(rig, seen, before, at_crash)


# --- the oracles ---

def records(rig: Rig) -> list[DeferralRow]:
    rows = rig.journal.db.execute("SELECT session_key, number, bead, role, profile, reason, defer_until, "
                                  "trust FROM deferrals ORDER BY session_key, number").fetchall()
    return [DeferralRow(*r) for r in rows]


def ops(rig: Rig) -> list[tuple[str, str, str, dict[str, str]]]:
    rows = rig.journal.db.execute("SELECT kind, step, status, data FROM ops ORDER BY rowid")
    return [(k, s, st, json.loads(d)) for k, s, st, d in rows.fetchall()]


def alerts(rig: Rig) -> list[str]:
    rows = rig.journal.db.execute("SELECT ref FROM events WHERE kind = ? ORDER BY seq",
                                  (ACCOUNT_CHANGED_EVENT,)).fetchall()
    return [r[0] for r in rows]


def defer_comments(rig: Rig, bead: str = B) -> list[str]:
    return [c for c in rig.world.beads[bead].comments if c.startswith(DEFER_MARK)]


def overs(rig: Rig) -> list[str]:
    return [r[0] for r in rig.journal.db.execute(
        "SELECT key FROM meta WHERE key LIKE 'deferral_over:%' ORDER BY key").fetchall()]


def end_state(rig: Rig, bead: str = B) -> dict[str, Any]:
    row = rig.journal.state(WS, bead)
    runtime = rig.runtime
    assert isinstance(runtime, PeakRuntime)
    gens = [(e.generation, e.outcome, rig.journal.receipt(*e.ident) is not None)
            for e in rig.journal.launches_of(WS, bead)]
    return {"records": records(rig), "labels": sorted(rig.world.beads[bead].labels),
            "comments": defer_comments(rig, bead), "alerts": alerts(rig), "overs": overs(rig),
            "row": None if row is None else (row.state, row.reason, row.detail),
            "dispatches": runtime.calls, "generations": gens, "open": rig.journal.ops_open(WS)}


def completed(rig: Rig, bead: str = B) -> None:
    """What every completed run holds on its own: one comment per number, one alert per `account_changed`
    number, never two coder sessions, one receipt per launched generation, the claim kept, and the WIP
    commit of every tail."""
    recs = records(rig)
    comments = defer_comments(rig, bead)
    assert sorted({c.split(" role=")[0] for c in comments}) == sorted(c.split(" role=")[0] for c in comments)
    assert len(comments) == len({r.number for r in recs if wip_of(rig, r) is not None})
    assert alerts(rig) == [f"{r.session_key}:{r.number}" for r in recs
                           if r.reason == ACCOUNT_CHANGED and wip_of(rig, r) is not None]
    runtime = rig.runtime
    assert isinstance(runtime, PeakRuntime) and runtime.peak <= 1
    for e in rig.journal.launches_of(WS, bead):
        if e.outcome == "launched":
            assert rig.journal.receipt(*e.ident) is not None
    claimed(rig, bead)


def claimed(rig: Rig, bead: str = B) -> None:
    """The bead stays claimed throughout: claimed once and never handed back (the fake has no unclaim)."""
    shown = rig.world.beads[bead]
    assert shown.status == "in_progress" and shown.assignee is not None
    assert rig.world.claims.count(bead) == 1


def wip_of(rig: Rig, rec: DeferralRow) -> str | None:
    """The SHA a tail journaled for `rec`, checked against git: the mark for (key, n) finds it."""
    shas = [d["sha"] for _, _, _, d in ops(rig)
            if d.get("defer_key") == rec.session_key and d.get("defer") == str(rec.number) and "sha" in d]
    if not shas:
        return None
    [sha] = shas
    assert gitwip.find_wip(gitwip.pin(rig.repo, rig.worktree(rec.bead), f"btq/{rec.bead}"),
                           f"defer:{rec.session_key}:{rec.number}") == sha
    return sha


def wip_holds(run: Run, rec: DeferralRow, text: str) -> None:
    sha = wip_of(run.rig, rec)
    assert sha is not None
    shown = subprocess.run(["git", "-C", str(run.rig.worktree(rec.bead)), "show", f"{sha}:{DIRTY}"],
                           capture_output=True, text=True, check=True).stdout
    assert shown == text


def same_end(tmp_path: Path, scenario: Scenario, points: tuple[str, ...]) -> Run:
    """The completed-run oracle at each of `points`, all of which the uncrashed run must reach."""
    clean = run(tmp_path / "clean", scenario)
    missing = [p for p in points if p not in clean.seen]
    assert missing == []
    completed(clean.rig)
    want = end_state(clean.rig)
    for i, point in enumerate(points):
        crashed = run(tmp_path / f"crash-{i}", scenario, point)
        assert end_state(crashed.rig) == want, point
        completed(crashed.rig)
        for rec in records(crashed.rig)[crashed.before:]:
            if wip_of(crashed.rig, rec) is not None:
                wip_holds(crashed, rec, "dirty before the act")
    return clean


def uncertain_end(tmp_path: Path, scenario: Scenario, point: str) -> None:
    """The uncertainty oracle at one of the guard's UNRECEIPTED points."""
    rig_point = point.rsplit(".", 1)[1]
    crashed = run(tmp_path / f"uncertain-{rig_point}", scenario, point)
    rig = crashed.rig
    row = rig.journal.state(WS, B)
    assert row is not None and (row.state, row.reason) == (BeadState.STUCK, Reason.UNEXPECTED_STATE)
    assert NEEDS_HUMAN in rig.world.beads[B].labels and rig.journal.ops_open(WS) == []
    assert any(k == "escalate" and st == "done" for k, _, st, _ in ops(rig))
    entries = rig.journal.launches_of(WS, B)
    last = entries[-1]
    assert last.dispatched_at is not None and last.outcome is None
    assert rig.journal.receipt(*last.ident) is None
    runtime = rig.runtime
    assert isinstance(runtime, PeakRuntime)
    base = sum(1 for e in entries[:-1] if e.dispatched_at is not None)
    assert runtime.calls == base + (1 if rig_point == "launched!" else 0)
    rig.pickup()
    assert rig.journal.launches_of(WS, B) == entries and runtime.calls == base + (rig_point == "launched!")
    assert records(rig) == crashed.at_crash
    claimed(rig)


# --- scenarios ---

def launched(rig: Rig, profile: str = "p-two", bead: str = B) -> None:
    rig.world.add(bead)
    on_profile(rig, bead, profile)
    assert rig.pickup() is Outcome.STARTED


def two_key(rig: Rig) -> str:
    return profile_key(rig, "p-two")


def deferred(rig: Rig, reason: str = QUOTA, labels: tuple[str, ...] = ()) -> None:
    """btq-1 launched on p-two, then deferred by the standalone defer (quota: an hour)."""
    launched(rig)
    dirty(rig, "dirty before the setup")
    rig.world.beads[B].labels.extend(labels)
    until = rig.clock() + HOUR if reason == QUOTA else None
    rig.parker.defer(B, reason, until)


def parked_on_open(rig: Rig) -> None:
    launched(rig)
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.parker.park(B, ("btq-2",))


def resumable(rig: Rig) -> None:
    parked_on_open(rig)
    rig.world.close("btq-2")


ENTRIES: dict[str, tuple[str, Scenario]] = {
    # 1: the standalone defer
    "standalone": ("park", Scenario(launched, lambda r: r.parker.defer(B, QUOTA, r.clock() + HOUR))),
    # 2: pickup's due resume, gated to a deadline: through public pickup, from a PARKED row
    "due_resume": ("park", Scenario(lambda r: (resumable(r), block(r, two_key(r), HOUR)),
                                    lambda r: r.pickup())),
    # 3: the guard of a new bead's pickup, blocked between pickup's gate and step 2
    "guard_new": ("pickup", Scenario(
        lambda r: (r.world.add(B), on_profile(r, B, "p-two")), lambda r: r.pickup(),
        lambda r: {"gate.checked": lambda: block(r, two_key(r), HOUR),
                   "pickup.recorded!": lambda: dirty(r, "dirty before the act")})),
    # 3: the guard of a resume, blocked after pickup's gate
    "guard_resume": ("resume", Scenario(resumable, lambda r: r.pickup(),
                                        lambda r: {"resume.intent": lambda: block(r, two_key(r), HOUR)})),
    # 5: the re-gate of an `account_changed` wait gives a deadline
    "regate": ("park", Scenario(lambda r: (deferred(r, ACCOUNT_CHANGED), block(r, two_key(r), HOUR)),
                                lambda r: r.sched.regate_all())),
}


@pytest.mark.parametrize("entry", list(ENTRIES))
def test_a_crash_at_every_defer_point_replays_to_the_deferral(tmp_path: Path, entry: str) -> None:
    kind, scenario = ENTRIES[entry]
    clean = same_end(tmp_path, scenario, tuple(f"{kind}.{s}" for s in DEFER_STEPS))
    rig = clean.rig
    [*_, last] = records(rig)
    assert last.reason == QUOTA and rig.state(B) == "deferred" and DEFERRED in rig.world.beads[B].labels
    assert rig.runtime.calls == (0 if entry == "guard_new" else 1)
    wip_holds(clean, last, "dirty before the act")


# --- undefer (§3.3) ---

def due(rig: Rig, reason: str = QUOTA) -> None:
    """btq-1 deferred, then due: quota past its hour, or `account_changed` marked over by a re-gate."""
    deferred(rig, reason)
    if reason == QUOTA:
        rig.clock.advance(HOUR)
    else:
        assert rig.sched.regate_all().over == 1


def superseding(rig: Rig) -> Callable[[], object]:
    def insert() -> None:
        key = rig.key(B)
        rig.journal.deferral_insert(DeferralRow(key, 2, B, "coder", "p-two", QUOTA,
                                                str(rig.clock() + HOUR), "trusted"))
    return insert


def labelled(rig: Rig, label: str) -> Callable[[], object]:
    return lambda: rig.world.beads[B].labels.append(label)


def blocker(rig: Rig) -> Callable[[], object]:
    def add() -> None:
        rig.world.add("btq-3")
        rig.world.beads["btq-3"].labels.remove("agent:wsd")
        rig.world.beads[B].deps.append(("btq-3", "blocks"))
    return add


def parked_and_deferred(rig: Rig) -> None:
    parked_on_open(rig)
    dirty(rig, "dirty before the setup")
    rig.parker.defer(B, QUOTA, rig.clock() + HOUR)
    rig.clock.advance(HOUR)


def launches_once_its_blockers_close(rig: Rig) -> None:
    assert rig.state(B) == "parked" and rig.runtime.calls == 1
    assert PARKED in rig.world.beads[B].labels and DEFERRED not in rig.world.beads[B].labels
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.RESUMED and rig.runtime.calls == 2


def pickup(rig: Rig) -> object:
    return rig.pickup()


GUARD = tuple(f"resume.{s}" for s in GUARD_STEPS)
TAIL = tuple(f"resume.{s}" for s in DEFER_STEPS)
UNDEFERS: dict[str, tuple[tuple[str, ...], Scenario]] = {
    "account_then_launch": ((*UNDEFER_POINTS, *GUARD), Scenario(due, pickup)),
    "both_labels_end_parked": (UNDEFER_POINTS, Scenario(parked_and_deferred, pickup,
                                                        then=launches_once_its_blockers_close)),
    "deadline_is_n_plus_1": (("undefer.intent", *TAIL),
                             Scenario(lambda r: (due(r), block(r, two_key(r), HOUR)), pickup)),
    "quota_to_account_changed": (("undefer.intent", *TAIL),
                                 Scenario(lambda r: (due(r), repoint_codex(r, "other")), pickup)),
    "superseded": (("undefer.intent",), Scenario(due, pickup, lambda r: {"undefer.intent": superseding(r)})),
    "held": (("undefer.intent",), Scenario(due, pickup, lambda r: {"undefer.intent": labelled(r, HELD)})),
    "needs_human": (("undefer.intent",),
                    Scenario(due, pickup, lambda r: {"undefer.intent": labelled(r, NEEDS_HUMAN)})),
    "blocked": (("undefer.intent",), Scenario(due, pickup, lambda r: {"undefer.intent": blocker(r)})),
}
ENDS = {"account_then_launch": ("running", None),
        "both_labels_end_parked": ("running", None),        # after `then` closed its blocker
        "deadline_is_n_plus_1": ("deferred", Reason.QUOTA),
        "quota_to_account_changed": ("deferred", Reason.ACCOUNT_CHANGED),
        "superseded": ("deferred", Reason.QUOTA), "held": ("held", Reason.HELD_BY_OPERATOR),
        "needs_human": ("stuck", Reason.NEEDS_HUMAN), "blocked": ("deferred", Reason.QUOTA)}


@pytest.mark.parametrize("name", list(UNDEFERS))
def test_a_crash_at_every_undefer_point_replays(tmp_path: Path, name: str) -> None:
    """Every undefer point the scenario reaches, and the guard's and the tail's after it: the
    completed-run oracle, except the guard's UNRECEIPTED points, which take the uncertainty oracle."""
    expected, scenario = UNDEFERS[name]
    clean = run(tmp_path / "probe", scenario)
    reached = tuple(p for p in dict.fromkeys(clean.seen) if p.startswith(("undefer.", "resume.")))
    assert set(expected) <= set(reached)
    uncertain = tuple(f"resume.{s}" for s in UNRECEIPTED)
    same_end(tmp_path, scenario, tuple(p for p in reached if p not in uncertain))
    for point in (p for p in reached if p in uncertain):
        uncertain_end(tmp_path, scenario, point)
    row = clean.rig.journal.state(WS, B)
    assert row is not None and (row.state.value, row.reason) == ENDS[name]
    recs = records(clean.rig)
    if name in ("deadline_is_n_plus_1", "quota_to_account_changed"):
        assert [r.number for r in recs] == [1, 2] and DEFERRED in clean.rig.world.beads[B].labels
        assert len(defer_comments(clean.rig)) == 2
        wip_holds(clean, recs[1], "dirty before the act")
    if name in ("superseded", "held", "needs_human", "blocked"):    # stopped by the intent's checks
        assert "undefer.gated" not in clean.seen and DEFERRED in clean.rig.world.beads[B].labels
    if name != "account_then_launch":
        assert clean.rig.runtime.calls == 1 + (name == "both_labels_end_parked")
    else:
        assert clean.rig.runtime.calls == 2 and DEFERRED not in clean.rig.world.beads[B].labels


# --- replay routes (§3.6) ---

@pytest.mark.parametrize(("entry", "point"), [("guard_new", "pickup.defer.stopped"),
                                              ("guard_resume", "resume.defer.labelled"),
                                              ("standalone", "park.defer.committed")])
def test_recovery_finishes_a_defer_before_any_pickup(tmp_path: Path, entry: str, point: str) -> None:
    rig = rig_for(tmp_path)
    _, scenario = ENTRIES[entry]
    scenario.setup(rig)
    calls = rig.runtime.calls
    rig.restart(Script(scenario.actions(rig), point))
    with pytest.raises(SimulatedCrash):
        scenario.act(rig)
    rig.restart()
    assert recover(rig.sched).ok
    assert rig.journal.ops_open(WS) == [] and rig.state(B) == "deferred"
    assert rig.runtime.calls == calls and [r.number for r in records(rig)] == [1]


def test_an_undefer_at_a_defer_step_never_launches(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    due(rig)
    block(rig, two_key(rig), HOUR)
    rig.restart(Script({}, "resume.defer.committed"))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    [op] = rig.journal.ops_open(WS)
    assert "undefer" in op.data and op.step == "defer.committed"
    rig.parker.replay(op)
    assert rig.runtime.calls == 1 and rig.state(B) == "deferred"
    assert [r.number for r in records(rig)] == [1, 2]


# --- when an undefer may run (§4) ---

def test_a_quota_deferral_never_undefers_before_its_until(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    deferred(rig)
    [rec] = records(rig)
    until = int(rec.defer_until or 0)
    rig.clock.now = until - 1
    assert rig.pickup() is Outcome.DEFERRED and rig.sched.wake_at == until
    assert rig.runtime.calls == 1 and rig.state(B) == "deferred" and rig.journal.ops_open(WS) == []
    rig.clock.now = until
    assert rig.pickup() is Outcome.RESUMED and rig.runtime.calls == 2
    assert DEFERRED not in rig.world.beads[B].labels and rig.state(B) == "running"


@pytest.mark.parametrize("reason", [QUOTA, ACCOUNT_CHANGED])
@pytest.mark.parametrize("stop", ["held", "needs_human", "blocked"])
def test_no_deferral_undefers_while_held_stuck_or_blocked(tmp_path: Path, reason: str, stop: str) -> None:
    rig = rig_for(tmp_path)
    due(rig, reason)
    {"held": labelled(rig, HELD), "needs_human": labelled(rig, NEEDS_HUMAN), "blocked": blocker(rig)}[stop]()
    count = len(ops(rig))
    for _ in range(2):
        rig.pickup()
    assert rig.runtime.calls == 1 and DEFERRED in rig.world.beads[B].labels
    assert all(k != "resume" for k, *_ in ops(rig)[count:])
    if stop == "needs_human":
        assert rig.state(B) == "stuck"


@pytest.mark.parametrize("deferred_first", [True, False])
def test_the_lower_id_starts_first_and_the_other_once_the_role_frees(tmp_path: Path,
                                                                     deferred_first: bool) -> None:
    """An over DEFERRED bead and an eligible PARKED bead compete: Source 1 is ordered by bead id."""
    rig = rig_for(tmp_path)
    low, high = "btq-1", "btq-2"
    late, parked = (low, high) if deferred_first else (high, low)
    launched(rig, bead=late)
    rig.parker.defer(late, QUOTA, rig.clock() + HOUR)
    launched(rig, "p-one", parked)
    rig.world.add("btq-9")
    rig.world.beads["btq-9"].labels.remove("agent:wsd")
    rig.parker.park(parked, ("btq-9",))
    rig.world.close("btq-9")
    rig.clock.advance(HOUR)
    assert rig.pickup() is Outcome.RESUMED and rig.runtime.coders() == [low]
    assert rig.pickup() is Outcome.BUSY and rig.runtime.coders() == [low]
    rig.runtime.end(rig.key(low))
    rig.world.close(low)
    assert rig.pickup() is Outcome.RESUMED and rig.runtime.coders() == [high]
    assert rig.runtime.calls == 4
    claimed(rig, high)


def test_a_deferred_bead_with_no_row_is_journal_lost_and_never_launched(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    deferred(rig)
    rig.journal.db.execute("DELETE FROM beads WHERE bead = ?", (B,))
    rig.pickup()
    row = rig.journal.state(WS, B)
    assert row is not None and (row.state, row.reason) == (BeadState.STUCK, Reason.JOURNAL_LOST)
    rig.clock.advance(2 * HOUR)
    rig.pickup()
    rig.pickup()
    assert rig.runtime.calls == 1 and rig.state(B) == "stuck"


@pytest.mark.parametrize(("hint", "after"), [(None, 30 * 60), (1, 60), (10**6, 60 * 60), (600, 600)])
def test_an_untrusted_until_is_clamped(tmp_path: Path, hint: int | None, after: int) -> None:
    rig = rig_for(tmp_path)
    launched(rig)
    now = rig.clock()
    rig.parker.defer(B, QUOTA, None if hint is None else now + hint, trust=UNTRUSTED)
    [rec] = records(rig)
    assert (rec.defer_until, rec.trust) == (str(now + after), UNTRUSTED)


# --- the sweep table (§7) ---

def deferred_parking(rig: Rig) -> None:
    rig.world.add(B)
    on_profile(rig, B, "p-two")
    assert rig.pickup() is Outcome.STARTED
    rig.restart(Script({}, "park.defer.labelled"))
    with pytest.raises(SimulatedCrash):
        rig.parker.defer(B, QUOTA, rig.clock() + HOUR)
    rig.restart()


SWEEP_ROWS: dict[str, tuple[Callable[[Rig], object], tuple[BeadState, Reason | None, str]]] = {
    "session_listed": (lambda r: (deferred(r), r.runtime.adopt(LaunchSpec(
        WS, B, "coder", "p-two", r.key(B), "x", r.worktree(B), resume=True))),
        (BeadState.STUCK, Reason.UNEXPECTED_STATE, "deferred, but a session is listed")),
    "kept": (deferred, (BeadState.DEFERRED, Reason.QUOTA, "")),
    "held_kept": (lambda r: deferred(r, labels=(HELD,)), (BeadState.HELD, Reason.HELD_BY_OPERATOR, "")),
    "stuck_kept": (lambda r: deferred(r, labels=(NEEDS_HUMAN,)), (BeadState.STUCK, Reason.NEEDS_HUMAN, "")),
    "open_op": (deferred_parking, (BeadState.PARKING, None, "")),
    "other_row": (lambda r: (deferred(r), r.journal.adopt(WS, B, BeadState.PARKED, Reason.BLOCKED_ON_BEAD)),
                  (BeadState.STUCK, Reason.UNEXPECTED_STATE, "deferred, but the journal says parked")),
    "unlabelled": (lambda r: (deferred(r), r.world.beads[B].labels.remove(DEFERRED)),
                   (BeadState.STUCK, Reason.UNEXPECTED_STATE,
                    "the journal says deferred, but the bead isn't labelled")),
    "no_record": (lambda r: (deferred(r), r.journal.db.execute("DELETE FROM deferrals")),
                  (BeadState.STUCK, Reason.UNEXPECTED_STATE, "deferred without a deferral record")),
}


@pytest.mark.parametrize("case", list(SWEEP_ROWS))
def test_the_sweep_table(tmp_path: Path, case: str) -> None:
    rig = rig_for(tmp_path)
    setup, (state, reason, detail) = SWEEP_ROWS[case]
    setup(rig)
    before = rig.journal.state(WS, B)
    with rig.parker.entry():
        sweep(rig.parker)
    row = rig.journal.state(WS, B)
    assert row is not None and (row.state, row.reason) == (state, reason)
    assert detail in row.detail
    if case in ("kept", "held_kept", "stuck_kept", "open_op"):
        assert row == before


# --- release (§3.5) ---

GATES: dict[str, Callable[[Rig], object]] = {
    "account": lambda _r: None,
    "deadline": lambda r: block(r, two_key(r), HOUR),
    "account_changed": lambda r: repoint_codex(r, "other"),
}


def release_scenario(reason: str, labels: tuple[str, ...], gate: str) -> Scenario:
    return Scenario(lambda r: (deferred(r, reason, labels), GATES[gate](r)), lambda r: r.parker.release(B))


def released_keeps_the_label(tmp_path: Path, scenario: Scenario) -> Rig:
    """The release alone, uncrashed: `v2:deferred` stays, and nothing launches."""
    rig = rig_for(tmp_path)
    scenario.setup(rig)
    scenario.act(rig)
    assert DEFERRED in rig.world.beads[B].labels and rig.runtime.calls == 1
    assert rig.journal.ops_open(WS) == [] and rig.state(B) == "deferred"
    return rig


@pytest.mark.parametrize("gate", list(GATES))
def test_releasing_an_account_changed_wait_regates_it(tmp_path: Path, gate: str) -> None:
    scenario = release_scenario(ACCOUNT_CHANGED, (), gate)
    rig = released_keeps_the_label(tmp_path / "plain", scenario)
    recs = records(rig)
    if gate == "account":
        assert overs(rig) == [f"deferral_over:{rig.key(B)}:1"]
    elif gate == "deadline":
        assert [(r.number, r.reason) for r in recs] == [(1, ACCOUNT_CHANGED), (2, QUOTA)]
        assert len(defer_comments(rig)) == 2
    else:
        assert [r.number for r in recs] == [1] and overs(rig) == [] and len(alerts(rig)) == 1
        assert [(k, st) for k, _, st, _ in ops(rig)][-1] == ("release", "done")
    clean = same_end(tmp_path, scenario, ("release.regate", *(
        ("release.regate.over!",) if gate == "account" else
        tuple(f"release.{s}" for s in DEFER_STEPS) if gate == "deadline" else ())))
    expected = 2 if gate == "account" else 1          # the next pickups undefer an over-marked wait, once
    assert clean.rig.runtime.calls == expected


@pytest.mark.parametrize("gate", list(GATES))
@pytest.mark.parametrize("reason", [QUOTA, ACCOUNT_CHANGED])
@pytest.mark.parametrize("label", [HELD, NEEDS_HUMAN])
def test_releasing_a_held_or_stuck_deferred_bead(tmp_path: Path, label: str, reason: str, gate: str) -> None:
    """A crash at every release point, through the real routes (`check` enforced, no `adopt`)."""
    scenario = release_scenario(reason, (label,), gate)
    rig = rig_for(tmp_path / "before")
    scenario.setup(rig)
    assert rig.state(B) == ("held" if label == HELD else "stuck")
    released_keeps_the_label(tmp_path / "plain", scenario)
    probe = run(tmp_path / "probe", scenario)
    reached = tuple(p for p in dict.fromkeys(probe.seen) if p.startswith("release."))
    assert {"release.intent", "release.unlabelled!", "release.unlabelled", "release.regate"} <= set(reached)
    if reason == ACCOUNT_CHANGED and gate == "account":
        assert "release.regate.over!" in reached
    if reason == ACCOUNT_CHANGED and gate == "deadline":
        assert set(RELEASE_REGATE_POINTS) - {"release.regate.over!"} <= set(reached)
    clean = same_end(tmp_path, scenario, reached)
    assert HELD not in clean.rig.world.beads[B].labels and NEEDS_HUMAN not in clean.rig.world.beads[B].labels


def test_a_regated_release_never_launches_beside_another_coder(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    deferred(rig, ACCOUNT_CHANGED)
    launched(rig, "p-one", "btq-2")
    rig.parker.release(B)
    assert overs(rig) == [f"deferral_over:{rig.key(B)}:1"]
    assert rig.pickup() is Outcome.BUSY
    assert rig.runtime.calls == 2 and rig.state(B) == "deferred" and rig.journal.ops_open(WS) == []
    rig.runtime.end(rig.key("btq-2"))
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.RESUMED and rig.runtime.calls == 3 and rig.runtime.coders() == [B]
    assert rig.pickup() is Outcome.BUSY and rig.runtime.calls == 3


def test_a_quota_wait_is_not_releasable(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    deferred(rig)
    count = len(ops(rig))
    with pytest.raises(NotReleasable):
        rig.parker.release(B)
    assert len(ops(rig)) == count and rig.state(B) == "deferred"


# --- the AU-5 §8 amendment tests 1 and 4 ---

def resume_to_account_changed(rig: Rig) -> None:
    resumable(rig)
    repoint_codex(rig, "other")


@pytest.mark.parametrize("path", ["due_quota", "resume"])
def test_quota_to_account_changed_writes_one_comment(tmp_path: Path, path: str) -> None:
    if path == "due_quota":
        _, scenario = UNDEFERS["quota_to_account_changed"]
        kind = "resume"
    else:
        scenario, kind = Scenario(resume_to_account_changed, pickup), "park"
    clean = same_end(tmp_path, scenario, tuple(f"{kind}.{s}" for s in DEFER_STEPS))
    rig = clean.rig
    *_, last = records(rig)
    assert last.reason == ACCOUNT_CHANGED and last.defer_until is None
    assert last.number == (2 if path == "due_quota" else 1)
    new = [c for c in defer_comments(rig) if f" n={last.number} " in c]
    assert len(new) == 1 and new[0].endswith("until=none reason=account_changed")
    assert alerts(rig) == [f"{last.session_key}:{last.number}"]


REGATES = {"defer": Scenario(launched, lambda r: r.parker.defer(B, ACCOUNT_CHANGED, None)),
           "regate": ENTRIES["regate"][1],
           "release_regate": release_scenario(ACCOUNT_CHANGED, (), "deadline")}


@pytest.mark.parametrize("what", list(REGATES))
def test_defer_and_regate_replay(tmp_path: Path, what: str) -> None:
    kind = {"defer": "park", "regate": "park", "release_regate": "release"}[what]
    clean = same_end(tmp_path, REGATES[what], tuple(f"{kind}.{s}" for s in DEFER_STEPS))
    rig = clean.rig
    recs = records(rig)
    assert len(defer_comments(rig)) == len(recs) == (1 if what == "defer" else 2)
    assert recs[-1].reason == (ACCOUNT_CHANGED if what == "defer" else QUOTA)


# --- retirement (§6) ---

def at_quota(rig: Rig, op_id: str, until: int) -> None:
    """A pre-AU-4 journal: the op left at AU-5's `quota` step, mid-shelve."""
    rig.journal.db.execute("UPDATE ops SET step = 'quota', data = json_set(data, '$.until', ?) "
                           "WHERE op_id = ?", (str(until), op_id))


@pytest.mark.parametrize("kind", ["pickup", "resume"])
def test_an_op_at_the_quota_step_replays_into_a_deferral(tmp_path: Path, kind: str) -> None:
    rig = rig_for(tmp_path)
    if kind == "pickup":
        rig.world.add(B)
        on_profile(rig, B, "p-two")
        point = "pickup.recorded!"
    else:
        resumable(rig)
        point = "resume.unlabelled"
    calls = rig.runtime.calls
    rig.restart(Script({}, point))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    [op] = rig.journal.ops_open(WS)
    until = rig.clock() + HOUR
    at_quota(rig, op.op_id, until)
    assert recover(rig.sched).ok
    [rec] = records(rig)
    assert (rec.number, rec.reason, rec.defer_until) == (1, QUOTA, str(until))
    assert rig.journal.ops_open(WS) == [] and rig.state(B) == "deferred" and rig.runtime.calls == calls
    assert DEFERRED in rig.world.beads[B].labels and len(defer_comments(rig)) == 1
    claimed(rig)


FIXTURE = Path(__file__).parent / "data" / "au5_quota_shelve_25c7b2a.json"


def repoint_claude(rig: Rig) -> None:
    """Repoint the default claude-code login (`~/.claude/.credentials.json`) to another file."""
    home = rig.root / "home"
    link = home / ".claude" / ".credentials.json"
    other = home / ".claude-other" / ".credentials.json"
    other.parent.mkdir()
    other.write_text("{}")
    link.unlink()
    link.symlink_to(other)


def shelved(tmp_path: Path, launched_before: bool) -> Rig:
    """The AU-5 shelve's end state, recorded from main 25c7b2a: its row, its abandoned op, its labels and
    its comments, laid over the claim, worktree and launched-session record today's code makes for the
    same bead (a pickup that stops just after its record), or over a bead that launched once before."""
    data = json.loads(FIXTURE.read_text())
    rig = rig_for(tmp_path)
    assert rig.clock() == data["clock"]
    rig.world.add(B)
    if launched_before:
        assert rig.pickup() is Outcome.STARTED
        rig.runtime.end(rig.key(B))
    else:
        rig.restart(Script({}, "pickup.recorded!"))
        with pytest.raises(SimulatedCrash):
            rig.pickup()
        rig.restart()
    [op] = data["ops"]
    assert op["data"]["session_key"] == "{session_key}"
    fields = {k: v.replace("{root}", str(tmp_path)).replace("{session_key}", rig.key(B))
              for k, v in op["data"].items()}
    assert fields["worktree"] == str(rig.worktree(B))
    rig.journal.db.execute("DELETE FROM ops WHERE status = 'open'")
    rig.journal.db.execute("INSERT INTO ops VALUES ('au5-shelve', ?, ?, ?, ?, ?, 0, ?, '', '')",
                           (op["kind"], WS, B, op["step"], json.dumps(fields), op["status"]))
    row = data["row"]
    rig.journal.adopt(WS, B, BeadState(row["state"]), Reason(row["reason"]), row["detail"])
    bead = rig.world.beads[B]
    bead.labels[:] = data["labels"]
    bead.comments[:] = data["comments"]
    assert bead.status == data["status"] and len(records(rig)) == data["deferrals"]
    return rig


def swept_row(rig: Rig) -> None:
    """A sweep never changes the row, before the conversion or after it."""
    before = rig.journal.state(WS, B)
    with rig.parker.entry():
        sweep(rig.parker)
    assert rig.journal.state(WS, B) == before


@pytest.mark.parametrize("outcome", ["account", "deadline", "account_changed"])
def test_recovery_converts_the_au5_shelve_fixture(tmp_path: Path, outcome: str) -> None:
    rig = shelved(tmp_path, launched_before=outcome == "account_changed")
    calls, until = rig.runtime.calls, None
    if outcome == "deadline":
        until = block(rig, profile_key(rig, "p-one"), 2 * HOUR)
    elif outcome == "account_changed":
        repoint_claude(rig)
    swept_row(rig)
    rig.restart()
    assert recover(rig.sched).ok
    swept_row(rig)
    row = rig.journal.state(WS, B)
    assert row is not None and rig.runtime.calls == calls and rig.journal.ops_open(WS) == []
    labels = rig.world.beads[B].labels
    if outcome == "account":
        assert (row.state, row.reason, row.detail) == (BeadState.PARKED, Reason.BLOCKED_ON_BEAD, "")
        assert records(rig) == [] and DEFERRED not in labels
        assert rig.pickup() is Outcome.RESUMED and rig.runtime.calls == calls + 1
    else:
        [rec] = records(rig)
        assert row.state is BeadState.DEFERRED and PARKED in labels and DEFERRED in labels
        if outcome == "deadline":
            assert (row.reason, rec.reason, rec.defer_until) == (Reason.QUOTA, QUOTA, str(until))
            assert alerts(rig) == []
        else:
            assert row.reason is Reason.ACCOUNT_CHANGED
            assert (rec.reason, rec.defer_until) == (ACCOUNT_CHANGED, None)
            assert alerts(rig) == [f"{rec.session_key}:1"]
        assert len(defer_comments(rig)) == 1
        rig.pickup()
        assert rig.runtime.calls == calls
    claimed(rig)
