"""Idle-ready safety-net detection; contract: spec/continuation.md."""
import hashlib
import json
import time


def check(pinfo, pstate, ready, nudge, escalate, clock=time.time):
    """One ready observation per nudge interval; failures stay retryable."""
    now = clock()
    if now < pstate.get('queue_check_after', 0):
        return
    pstate['queue_check_after'] = now + 60
    items = ready(pinfo['name'])
    if not items:
        pstate.pop('idle_ready_episode', None)
        return
    identity = hashlib.sha256(json.dumps(sorted(item['id'] for item in items)).encode()).hexdigest()
    episode = pstate.setdefault('idle_ready_episode', {})
    if episode.get('identity') != identity:
        episode.clear()
        episode['identity'] = identity
    if episode.get('escalated'):
        return
    if not episode.get('nudged'):
        # Retain uncertainty for subsequent polls; caller persists pane state.
        episode['nudged'] = True
        try:
            nudge()
        except Exception:
            episode['nudge_failed'] = True
    text = (f"[babysitter:{pinfo['name']}] Ready work is stranded at an idle worker: "
            f"{len(items)} eligible task(s), next {items[0]['id']}. "
            + ('Nudge delivery was uncertain; inspect before retrying. '
               if episode.get('nudge_failed') else 'Standard resume nudge attempted. ')
            + 'Please inspect the continuation boundary and task ownership.')
    if escalate(pinfo, text):
        episode['escalated'] = True


def nudge_key(owner):
    identity = [owner['id'], owner.get('native_turn_key'), owner.get('pane_id')]
    return 'babysitter-idle:' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def enqueue(store, pinfo):
    """Revalidate ownership under lock; only the supervisor may touch the pane."""
    from .continuation import safe
    with store.lock(blocking=False):
        owner = store.get(pinfo['name'])
        if (owner.get('pane_id') != pinfo['pane_id'] or not safe(owner)
                or owner.get('continuation', {}).get('automatic_turn') == owner.get('native_turn_key')
                and owner.get('native_turn_key') is not None):
            raise ValueError('Worker no longer eligible for a nudge')
        key = nudge_key(owner)
        pending = store.db.execute(
            "SELECT 1 FROM inbox WHERE run_id=? AND target='worker' AND state IN ('pending','sending','uncertain','held') AND id!=? LIMIT 1",
            (owner['id'], key)).fetchone()
        if pending:
            raise ValueError('Existing worker input takes precedence')
        text = ('Ready work remains. Resume current scope before claiming another task '
                'through the guarded workstream facade. Preserve native approvals and recovery holds.')
        with store.db:
            store.db.execute("INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)",
                             (key, owner['id'], text, time.time(), 'pending', 'worker'))


def run(pinfo, pstate, namespace):
    """Queue reads and escalation acquire their own locks, never nest ours."""
    from .store import Store
    from .continuation import safe
    store = Store()
    try:
        with store.lock(blocking=False):
            owner = store.get(pinfo['name'])
            if owner.get('pane_id') != pinfo['pane_id'] or not safe(owner):
                return
        check(pinfo, pstate, namespace['queue_open_items'],
              lambda: enqueue(store, pinfo), namespace['escalate'])
    finally:
        store.db.close()
