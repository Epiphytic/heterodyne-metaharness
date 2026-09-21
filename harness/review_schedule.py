"""Pure review deadline and activity decisions. Authority: spec/reviews.md."""


def next_deadline(elapsed):
    """Absolute active-elapsed deadlines, skipping missed slots without a burst."""
    if elapsed < 600:
        return 600
    if elapsed < 1800:
        return 1800
    return 5400 + max(0, int((elapsed - 1800) // 3600)) * 3600


def advance(state, now, suspended):
    """Persisted wall anchor; never count backwards or charge a suspended interval.

    Suspension is observed, not guessed retrospectively. A restart charges elapsed
    wall time according to the last durable suspension state. One overdue review
    replaces all missed slots. Caller atomically stores state plus scheduled job.
    """
    previous = state.get('observed_at', now)
    elapsed = state.get('elapsed', 0)
    if not state.get('suspended', False):
        elapsed += max(0, now - previous)
    state.update(observed_at=max(now, previous), elapsed=elapsed, suspended=suspended)
    state.setdefault('due', 600)
    if suspended or elapsed < state['due']:
        return None
    due = state['due']
    state['due'] = next_deadline(elapsed)
    return due


def suspended(run, open_ask, other_work=False):
    """Only explicit native coding/testing activity releases an outstanding ask.

    A live PID, pane timers, or task status alone do not establish work proceeding.
    Callers may pass verified activity on another task in the same channel.
    """
    waiting = open_ask or run.get('native_turn_state') in ('awaiting_question', 'awaiting_approval')
    working = run.get('native_turn_state') == 'working'
    return bool(waiting and not (working or other_work))
