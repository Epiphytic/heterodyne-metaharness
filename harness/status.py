"""Status production and native activity contracts: spec/status.md."""
import hashlib
import json
import logging
import re
import time

KINDS = {'progress', 'working', 'blocked', 'completed', 'failed', 'awaiting_resume',
         'idle', 'interrupted', 'supervisor_error', 'native_state'}


def fingerprint(run, text):
    state = {key: run.get(key) for key in ('state', 'native_turn_state', 'native_turn_key',
                                         'resume_required', 'manager_missing', 'pane_digest')}
    return hashlib.sha256(json.dumps([state, text], sort_keys=True).encode()).hexdigest()


def observe_pane(run, text):
    """Reporting fallback only: static pixels never authorize terminal input."""
    normalized = re.sub(r'(Working \()[\d hms.]+(?=\s*[•·].*esc to interrupt)',
                        r'\1<elapsed>', text)
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    same = digest == run.get('pane_digest')
    run['pane_unchanged_ticks'] = run.get('pane_unchanged_ticks', 0) + 1 if same else 0
    run['pane_digest'] = digest
    run['pane_stopped'] = bool(normalized.strip()) and run['pane_unchanged_ticks'] >= 1
    return same


def normalized_text(text):
    """Remove harness display wrappers, not arbitrary numbers or command content."""
    text = re.sub(r'^Status: [\w-]+; native turn: [\w-]+\.\s*', '', text)
    text = re.sub(r'\s+Observed at \d{4}-\d{2}-\d{2}[T ][\d:.]+(?:Z|[+-][\d:]+| UTC)?\.?$', '', text)
    return ' '.join(text.split())


def semantic_state(run):
    state = {key: run.get(key) for key in
             ('state', 'native_turn_state', 'resume_required', 'manager_missing', 'observed_state')}
    state['issue_id'] = run.get('beads', {}).get('issue_id')
    return json.dumps(state, sort_keys=True)


def suppressed(store, run, identity, scope, now):
    """Keep event idempotency durable; throttle journal output across restarts."""
    error = f'Duplicate status suppressed: workstream={run["id"]} event={identity}'
    store.db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                     (identity, run['id'], 'duplicate_status_error', error, now))
    logged = store.db.execute('SELECT logged_at FROM status_limits WHERE run_id=? AND recipient=? AND kind=?', scope).fetchone()
    if logged[0] is None or now - logged[0] >= 300:
        store.db.execute('UPDATE status_limits SET logged_at=? WHERE run_id=? AND recipient=? AND kind=?', (now, *scope))
        logging.getLogger(__name__).error(error)
    return False


def record(store, run, identity, kind, text, group, now):
    """Caller holds a write transaction; pending and delivered notices share gates."""
    scope = (run['id'], json.dumps(group), kind)
    semantic = semantic_state(run)
    content = normalized_text(text) if kind == 'progress' else text
    digest = hashlib.sha256(json.dumps([semantic, content]).encode()).hexdigest()
    store.db.execute('INSERT OR IGNORE INTO status_limits(run_id,recipient,kind) VALUES (?,?,?)', scope)
    limit = store.db.execute('SELECT semantic,accepted_at FROM status_limits WHERE run_id=? AND recipient=? AND kind=?', scope).fetchone()
    same_state = limit['semantic'] == semantic
    prior = store.db.execute('''SELECT digest FROM status_notices
        WHERE run_id=? AND group_id IS ? AND kind=? ORDER BY rowid DESC LIMIT 20''',
        (run['id'], group, kind)).fetchall()
    duplicate = same_state and any(row['digest'] == digest for row in prior)
    limited = kind == 'progress' and same_state and now - limit['accepted_at'] < 300
    static = (kind == 'progress' and run.get('pane_stopped') and
              run.get('reported_pane_digest') == run.get('pane_digest') and
              run.get('reported_pane_group') == group)
    if static or duplicate or limited:
        return suppressed(store, run, identity, scope, now)
    store.db.execute('INSERT INTO status_notices VALUES (?,?,?,?,?)',
                     (identity, run['id'], digest, group, kind))
    store.db.execute('UPDATE status_limits SET semantic=?,accepted_at=? WHERE run_id=? AND recipient=? AND kind=?',
                     (semantic, now, *scope))
    if kind == 'progress':
        run['reported_pane_digest'] = run.get('pane_digest')
        run['reported_pane_group'] = group
    return True


def activity(target, native, turn, state, at=None):
    """Ignore stale native turns without treating a finished turn as task closure."""
    at = time.time() if at is None else at
    key = [native, turn] if turn else None
    current = target.get('native_turn_key')
    if at < target.get('native_turn_at', 0):
        return False
    if state != 'working' and key and current and key != current:
        return False
    if state == 'working' and key and key != current:
        from .continuation import started
        started(target, key)
    target['native_turn_state'] = state
    target['native_turn_at'] = max(at, target.get('native_turn_at', 0))
    if key:
        target['native_turn_key'] = key
    if target.get('state') in ('active', 'working', 'idle') and not target.get('resume_required'):
        target['state'] = 'idle' if state == 'idle' else 'active'
    return True


def is_idle(run, store):
    if (run.get('resume_required') or run.get('manager_missing') or
            run.get('observed_state') == 'awaiting_approval'):
        return False
    idle = run.get('pane_stopped') or run.get('native_turn_state') == 'idle' or run.get('state') == 'idle'
    if not idle or run.get('manager', {}).get('native_turn_state') == 'working':
        return False
    return not store.db.execute("SELECT 1 FROM inbox WHERE run_id=? AND state IN ('pending','sending') LIMIT 1",
                                (run['id'],)).fetchone()
