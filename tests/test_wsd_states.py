import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.wsd.states import (
    ACTIVE,
    ALLOWED,
    TERMINAL,
    BeadState,
    IllegalTransition,
    Reason,
    WsState,
    allowed,
    check,
    ws_state,
)

S = BeadState

# The state machine written out independently of `ALLOWED`, from ADR 0001 §4.3/§5.2 and decisions D3, D8,
# D17, D19 and D23: every edge wsd's own operations make, and nothing else. A same-state "transition"
# (a reason update) is always allowed and is not listed. Adding or removing any edge in `ALLOWED` must
# fail `test_transition_matrix`.
_SHELVED = {S.PARKED, S.WAITING_INPUT, S.HELD}
EXPECTED: dict[BeadState | None, set[BeadState]] = {
    None: {S.CLAIMING},                                      # every bead starts with a claim
    S.CLAIMING: {S.STARTING,                                 # read back as ours
                 S.DROPPED,                                  # lost or abandoned
                 S.STUCK, S.CLOSED},
    S.STARTING: {S.RUNNING,                                  # the launch guard launched it
                 S.PARKING, S.STUCK, S.CLOSED,
                 S.DEFERRED}                                 # AU-4: a guard defer
                | _SHELVED,                                  # shelved: not runnable when its launch came
    S.RUNNING: {S.PARKING,
                S.RESUMING,                                  # the sweep: its session ended (D23)
                S.STUCK, S.CLOSED},
    S.PARKING: _SHELVED | {S.STUCK, S.CLOSED,
                           S.DEFERRED},                      # AU-4: the end of every defer tail
    S.PARKED: _SHELVED | {S.STUCK, S.RESUMING, S.CLOSED,
                          S.PARKING},                        # AU-4: a due resume defers
    S.WAITING_INPUT: _SHELVED | {S.STUCK, S.RESUMING, S.CLOSED},
    S.HELD: _SHELVED | {S.STUCK, S.RESUMING, S.CLOSED,       # RESUMING only by release (D19)
                        S.DEFERRED},                         # AU-4: release of a deferred bead
    S.RESUMING: {S.RUNNING, S.PARKING, S.STUCK, S.CLOSED,
                 S.DEFERRED}                                 # AU-4: a guard defer, an abandoned undefer
                | _SHELVED,
    S.STUCK: _SHELVED | {S.RESUMING, S.CLOSED,               # release: back to waiting or a resume (D19)
                         S.DEFERRED},                        # AU-4: release of a deferred bead
    S.DEFERRED: {S.PARKING,                                  # AU-4: a re-gate gives a deadline
                 S.RESUMING,                                 # undefer
                 S.HELD, S.STUCK, S.CLOSED},                 # precedence, escalation, closed by anyone
    S.CLOSED: {S.CLAIMING},                                  # a reopened bead can be claimed again
    S.DROPPED: {S.CLAIMING},
}


def test_transition_matrix() -> None:
    sources: list[BeadState | None] = [None, *BeadState]
    wrong: list[str] = []
    for src in sources:
        for dst in BeadState:
            expected = src == dst or dst in EXPECTED[src]
            if allowed(src, dst) is not expected:
                wrong.append(f"{src} -> {dst}: expected {'allowed' if expected else 'illegal'}")
            try:
                check(src, dst)
            except IllegalTransition:
                if expected:
                    wrong.append(f"check({src}, {dst}) raised")
            else:
                if not expected:
                    wrong.append(f"check({src}, {dst}) did not raise")
    assert not wrong, "\n".join(wrong)


def test_every_state_has_a_row() -> None:
    assert set(ALLOWED) == set(BeadState) | {None}


def test_every_live_state_can_close() -> None:
    for state in set(BeadState) - TERMINAL:
        assert allowed(state, BeadState.CLOSED)


def test_terminal_states_only_reclaim() -> None:
    for state in TERMINAL:
        assert ALLOWED[state] == {BeadState.CLAIMING}


def test_claiming_never_jumps_to_running() -> None:
    # A claim is never executed until it has been read back as ours and a worktree exists.
    with pytest.raises(IllegalTransition):
        check(BeadState.CLAIMING, BeadState.RUNNING)


def test_only_a_claim_starts_a_row() -> None:
    # wsd's own operations start every bead with a claim; recovery rebuilds rows with `adopt` instead.
    assert ALLOWED[None] == {BeadState.CLAIMING}
    with pytest.raises(IllegalTransition):
        check(None, BeadState.CLOSED)


def test_same_state_only_updates_the_reason() -> None:
    # CLAIMING, for one, does not list itself in its row; a reason update is still legal.
    assert BeadState.CLAIMING not in ALLOWED[BeadState.CLAIMING]
    for state in BeadState:
        assert allowed(state, state)
        check(state, state)


def test_held_runs_again_only_through_a_resume() -> None:
    # Only `Parker.release` moves a HELD bead on, and it hands it to a resume (D19).
    assert allowed(BeadState.HELD, BeadState.RESUMING)
    with pytest.raises(IllegalTransition):
        check(BeadState.HELD, BeadState.RUNNING)


def test_stuck_runs_again_only_through_a_resume() -> None:
    # Release hands a stuck bead back to waiting or to a resume through the launch guard (D19).
    assert allowed(BeadState.STUCK, BeadState.RESUMING)
    with pytest.raises(IllegalTransition):
        check(BeadState.STUCK, BeadState.RUNNING)


@pytest.mark.parametrize(("paused", "holds", "beads", "expected"), [
    (False, [], [], WsState.IDLE),
    (False, [], [BeadState.CLOSED, BeadState.DROPPED], WsState.IDLE),
    (False, [], [BeadState.PARKED], WsState.ALL_BLOCKED),
    (False, [], [BeadState.PARKED, BeadState.STUCK], WsState.STUCK),
    (False, [], [BeadState.STUCK, BeadState.RUNNING], WsState.RUNNING),
    (True, [], [BeadState.RUNNING], WsState.PAUSED),
    (True, [Reason.BEADS_UNREACHABLE], [BeadState.RUNNING], WsState.HELD),
    # A hold applies whether or not the workstream is paused, and with no live bead at all.
    (False, [Reason.BEADS_UNREACHABLE], [BeadState.RUNNING], WsState.HELD),
    (False, [Reason.RUNTIME_UNAVAILABLE], [], WsState.HELD),
    (False, [Reason.ACTIONS_UNRECONCILED], [BeadState.STUCK], WsState.HELD),
    # Pause applies whatever the beads are doing.
    (True, [], [], WsState.PAUSED),
    (True, [], [BeadState.PARKED], WsState.PAUSED),
    (True, [], [BeadState.STUCK], WsState.PAUSED),
    # Waiting on input or on the operator is blocked, not stuck.
    (False, [], [BeadState.WAITING_INPUT], WsState.ALL_BLOCKED),
    (False, [], [BeadState.HELD], WsState.ALL_BLOCKED),
    (False, [], [BeadState.PARKED, BeadState.WAITING_INPUT, BeadState.HELD], WsState.ALL_BLOCKED),
    (False, [], [BeadState.HELD, BeadState.STUCK], WsState.STUCK),
    # AU-4: every non-terminal row DEFERRED, whatever the reason, is deferred; mixed with a parked one it
    # is all-blocked, unless a wake is armed (below).
    (False, [], [BeadState.DEFERRED], WsState.DEFERRED),
    (False, [], [BeadState.DEFERRED, BeadState.CLOSED], WsState.DEFERRED),
    (False, [], [BeadState.DEFERRED, BeadState.PARKED], WsState.ALL_BLOCKED),
    (False, [], [BeadState.DEFERRED, BeadState.STUCK], WsState.STUCK),
    (False, [], [BeadState.PARKED], WsState.ALL_BLOCKED),
])
def test_ws_state_priority(paused: bool, holds: list[Reason], beads: list[BeadState],
                           expected: WsState) -> None:
    assert ws_state(paused, holds, beads) is expected


def test_an_armed_wake_defers_an_idle_or_all_blocked_workstream() -> None:
    # AU-4 §2.2: the wake applies where the workstream would otherwise be idle or all-blocked; the states
    # above those (held, paused, running, stuck) still win.
    assert ws_state(False, [], [BeadState.PARKED], wake=True) is WsState.DEFERRED
    assert ws_state(False, [], [BeadState.PARKED, BeadState.DEFERRED], wake=True) is WsState.DEFERRED
    assert ws_state(False, [], [BeadState.WAITING_INPUT, BeadState.HELD], wake=True) is WsState.DEFERRED
    assert ws_state(False, [], [], wake=True) is WsState.DEFERRED
    assert ws_state(False, [], [BeadState.STUCK], wake=True) is WsState.STUCK
    assert ws_state(False, [], [BeadState.RUNNING, BeadState.PARKED], wake=True) is WsState.RUNNING
    assert ws_state(True, [], [BeadState.PARKED], wake=True) is WsState.PAUSED
    assert ws_state(False, [Reason.JOURNAL_LOST], [BeadState.PARKED], wake=True) is WsState.HELD


@given(st.booleans(), st.lists(st.sampled_from(list(BeadState))))
def test_idle_only_when_nothing_is_live(paused: bool, beads: list[BeadState]) -> None:
    state = ws_state(paused, [], beads)
    if state is WsState.IDLE:
        assert not set(beads) - TERMINAL
    if set(beads) & ACTIVE and not paused:
        assert state is WsState.RUNNING


@given(st.booleans(), st.lists(st.sampled_from(list(Reason)), max_size=3),
       st.lists(st.sampled_from(list(BeadState))))
def test_holds_then_pause_take_priority(paused: bool, holds: list[Reason], beads: list[BeadState]) -> None:
    state = ws_state(paused, holds, beads)
    if holds:
        assert state is WsState.HELD
    elif paused:
        assert state is WsState.PAUSED
    else:
        assert state not in (WsState.HELD, WsState.PAUSED)
