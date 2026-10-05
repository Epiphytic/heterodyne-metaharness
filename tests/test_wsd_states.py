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


@pytest.mark.parametrize(("paused", "holds", "beads", "expected"), [
    (False, [], [], WsState.IDLE),
    (False, [], [BeadState.CLOSED, BeadState.DROPPED], WsState.IDLE),
    (False, [], [BeadState.PARKED], WsState.ALL_BLOCKED),
    (False, [], [BeadState.PARKED, BeadState.STUCK], WsState.STUCK),
    (False, [], [BeadState.STUCK, BeadState.RUNNING], WsState.RUNNING),
    (True, [], [BeadState.RUNNING], WsState.PAUSED),
    (True, [Reason.BEADS_UNREACHABLE], [BeadState.RUNNING], WsState.HELD),
])
def test_ws_state_priority(paused: bool, holds: list[Reason], beads: list[BeadState],
                           expected: WsState) -> None:
    assert ws_state(paused, holds, beads) is expected


@given(st.booleans(), st.lists(st.sampled_from(list(BeadState))))
def test_idle_only_when_nothing_is_live(paused: bool, beads: list[BeadState]) -> None:
    state = ws_state(paused, [], beads)
    if state is WsState.IDLE:
        assert not set(beads) - TERMINAL
    if set(beads) & ACTIVE and not paused:
        assert state is WsState.RUNNING
