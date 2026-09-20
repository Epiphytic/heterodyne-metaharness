"""One eligible-work check per native completion; contract: spec/continuation.md."""
import hashlib
import json
import re
from types import SimpleNamespace

from .beads import BeadsError


def completed(run, turn, summary):
    """Called only for a matched native completion, never pane inactivity/abort."""
    identity = hashlib.sha256(json.dumps([run['native_session_id'], turn]).encode()).hexdigest()
    state = run.setdefault('continuation', {})
    if state.get('completion', state.get('boundary')) == identity:
        return
    if state.get('boundary'):
        state.setdefault('unaccounted', []).append(state['boundary'])
    state.pop('not_before', None)
    state.update(boundary=identity, completion=identity, checked=False,
                 question=bool(re.search(r'\?|awaiting (?:your|operator)|waiting for (?:your|operator)',
                                         summary[-2000:], re.I)))


def native_event(run, event, now):
    from .status import activity
    states = {'working': 'working', 'approval': 'awaiting_approval',
              'turn_completed': 'idle', 'turn_aborted': 'idle'}
    kind = event['kind']
    if kind not in states:
        return
    was_working = run.get('native_turn_state') == 'working'
    matched_idle = (run.get('native_turn_state') == 'idle' and event.get('turn_id')
                    and run.get('native_turn_key') == [run.get('native_session_id'), event['turn_id']])
    applied = activity(run, run.get('native_session_id'), event.get('turn_id'),
                       states[kind], event.get('at') or now)
    if kind == 'turn_completed' and ((applied and was_working) or matched_idle):
        completed(run, event.get('turn_id') or event['id'], event['summary'])


def safe(run):
    beads = run.get('beads', {})
    observation = run.get('observation', {})
    tail = '\n'.join(observation.get('summary', '').splitlines()[-8:])
    return (run.get('native_turn_state') == 'idle'
            and run.get('state') in ('active', 'working', 'idle')
            and beads.get('pickup_enabled') is True
            and not beads.get('recovery_required') and not run.get('resume_required')
            and run.get('observed_state') not in ('awaiting_approval', 'awaiting_question')
            and observation.get('pane_alive') is True
            and not run.get('continuation', {}).get('question')
            and not re.search(r'\?|enter to (?:confirm|select)|esc to (?:cancel|interrupt)', tail, re.I))


def awaiting_start(run):
    pending = run.get('continuation', {}).get('submitted_turn')
    return pending is not None and pending == run.get('native_turn_key')


def started(run, key):
    """A distinct natural turn clears loop suppression; one sent turn does not."""
    state = run.get('continuation', {})
    if state.pop('submitted_turn', None) is not None:
        state['automatic_turn'] = key
    elif state.get('automatic_turn') != key:
        state.pop('last_progress', None)


def _select(supervisor, run, queue):
    """Inspect the actual current owner, then the existing dependency-filtered order."""
    beads = supervisor.beads
    ready = beads.ready(run)
    current = run.get('beads', {}).get('issue_id')
    issue = queue.show(current) if current else None
    if current and run.get('task_parks', {}).get(current, {}).get('state') == 'parked':
        return None  # Interrupted handoff needs explicit reconciliation, never auto-resume.
    if issue and issue.get('status') != 'closed':
        if issue.get('assignee') != queue.worker or issue.get('status') != 'in_progress':
            raise BeadsError('Continuation cannot resume an unverified claim')
        if not beads._allowed(queue, issue):
            raise BeadsError('Continuation requires current routing/design evidence')
        history = issue.get('metadata', {}).get('harness_lifecycle') or []
        if not any(event.get('stage') == 'pr-open' for event in history):
            return issue
        # A review wait is not unfinished implementation. Claim uses the existing
        # validated concurrent-review gate; this hint does not grant handoff.
    if not ready:
        return None
    from .task_workspace import assign
    from .tasks import observe, record_claim_baseline
    previous_workdir = run['workdir']
    result = assign(SimpleNamespace(task_action='claim', issue_id=ready[0]['id']),
                    supervisor.store, supervisor, beads, run)
    if run['workdir'] != previous_workdir:
        run['observation'] = {}  # A restarted pane needs a fresh observation before input.
    supervisor.persist(run)
    observe(supervisor.store, result)
    record_claim_baseline(supervisor.store, run, result)
    # observe updates the stored projection. Never overwrite it with our old dict.
    run.update(supervisor.store.get(run['id']))
    return result


def advance(supervisor, run):
    """Supervisor lock held. Consume before external reads/writes; failures do not poll."""
    state = run.get('continuation', {})
    with supervisor.store.db:
        for identity in state.pop('unaccounted', []):
            supervisor.store.event(run, 'queue_boundary', 'superseded-before-check',
                                   'queue-boundary:' + identity)
    if not state.get('boundary'):
        return
    if state.get('checked'):
        state['checking'] = False
        with supervisor.store.db:
            supervisor.store.event(run, 'queue_boundary', 'interrupted-check',
                                   'queue-boundary:' + state['boundary'])
        return
    if supervisor.clock() < state.get('not_before', 0):
        return  # One scheduled task transition, no external queue polling.
    # Completion can reach the notifier just before the terminal clears its
    # working footer. Wait for the ordinary pane observation, without querying
    # Beads or losing the one boundary to this rendering race.
    tail = '\n'.join(run.get('observation', {}).get('summary', '').splitlines()[-8:])
    if run.get('native_turn_state') == 'idle' and re.search(r'Working \([^\n]*esc to interrupt', tail):
        return
    state['checked'] = True
    supervisor.persist(run)
    with supervisor.store.db:
        supervisor.store.db.execute("UPDATE inbox SET state='superseded' WHERE run_id=? AND state='pending' AND id LIKE 'continuation:%' AND id!=?",
                                    (run['id'], 'continuation:' + state['boundary']))
    reason = 'interrupted-check'
    state['checking'] = True
    try:
        reason = _check(supervisor, run, state)
    except Exception as exc:
        reason = 'error:' + type(exc).__name__
        raise
    finally:
        run['continuation']['checking'] = False
        with supervisor.store.db:
            supervisor.store.event(run, 'queue_boundary', reason,
                                   'queue-boundary:' + state['boundary'])
        supervisor.persist(run)


def _check(supervisor, run, state):
    now = supervisor.clock()
    if not supervisor.beads.enabled:
        return 'disabled'
    if not safe(run):
        return 'guarded'
    if now - state.get('sent_at', 0) < 60:
        return 'cooldown'
    pending = supervisor.store.db.execute(
        "SELECT 1 FROM inbox WHERE run_id=? AND target='worker' AND state IN ('pending','sending','uncertain','held') LIMIT 1",
        (run['id'],)).fetchone()
    if pending:
        return 'pending-input'
    queue = supervisor.beads._queue(run)
    if (queue.state / 'paused').exists():
        return 'pickup-paused'
    issue = _select(supervisor, run, queue)
    return 'empty' if issue is None else _send(supervisor, run, issue, now)


def task_changed(run, issue):
    """A reconciled ownership/status transition, never a timer or revision bump."""
    state = run.get('continuation', {})
    signature = [issue['id'], issue.get('status'), issue.get('assignee')]
    previous = state.get('task_signature')
    state['task_signature'] = signature
    if previous is None or previous == signature or not state.get('checked') or state.get('checking'):
        return
    # Preserve all guards and loop/cooldown history. Duplicate completions must
    # still match their original completion ID, not this task-transition token.
    state.setdefault('unaccounted', []).append(state['boundary'])
    state.setdefault('completion', state.get('boundary'))
    state['boundary'] = hashlib.sha256(json.dumps([state['boundary'], signature]).encode()).hexdigest()
    state['checked'] = False
    state['not_before'] = state.get('sent_at', 0) + 60


def _send(supervisor, run, issue, now):
    state = run['continuation']
    from .tasks import scope_fingerprint
    from .workspace import git
    head = git(run['workdir'], 'rev-parse', 'HEAD') if run.get('repo') else ''
    progress = [issue['id'], scope_fingerprint(issue), head]
    if state.get('last_progress') == progress:
        return 'unchanged-work: automatic continuation suppressed'
    state.update(last_progress=progress, sent_at=now, selected_issue=issue['id'])
    text = (f'Authorized queue boundary: resume assigned Bead {issue["id"]}. '
            f'Read workstream task {run["id"]} show {issue["id"]}. '
            'Finish in-flight scope through its review/deployment lifecycle. '
            'Preserve approvals and recovery constraints. Pause pickup before yielding '
            'for operator input. The harness performs the next eligible-work check at '
            'a confirmed idle boundary; do not poll or bypass worktree guards.')
    with supervisor.store.db:
        supervisor.store.db.execute('''INSERT OR IGNORE INTO inbox
            (id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)''',
            ('continuation:' + state['boundary'], run['id'], text, now, 'pending', 'worker'))
        supervisor.store.save(run)
    supervisor.store.checkpoint(run)
    return 'queued:' + issue['id']
