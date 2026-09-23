"""Task revision notices using shared native receipt contracts; spec/tasks.md."""
import json
import time

from . import brain, tasks


def current(store, session):
    owner, _, role = session.rpartition(':')
    if role not in ('worker', 'secondary'):
        return None
    try:
        run = store.get(owner)
    except ValueError:
        return None
    target = run if role == 'worker' else run.get('secondary', {})
    issue_id = target.get('beads', {}).get('issue_id')
    if not issue_id:
        return None
    row = store.db.execute('SELECT revision,data FROM task_current WHERE issue_id=?', (issue_id,)).fetchone()
    if not row:
        return None
    issue = json.loads(row[1])
    if issue.get('assignee') != tasks.worker(run) or issue.get('status') != 'in_progress':
        return None
    return run, issue_id, row[0], issue


def offer(store, session, native, turn):
    tasks.initialize(store.db)
    target = current(store, session)
    if not target:
        return None
    run, issue_id, revision, issue = target
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if store.db.execute('SELECT 1 FROM task_receipts WHERE run_id=? AND issue_id=? AND revision=?',
                            (run['id'],issue_id,revision)).fetchone():
            return None
        store.db.execute('INSERT OR REPLACE INTO task_offers VALUES (?,?,?,?,?)', (session,native,turn,issue_id,revision))
    path = store.root / 'runs' / run['id'] / 'checkpoint.json'
    location = 'secondary.beads.task_snapshot' if session.endswith(':secondary') else 'beads.task_snapshot'
    return {'context':('[task-notice:'+revision+']\nAssigned Bead revision: '+json.dumps(issue_id)+
            '. Current task data is in '+str(path)+' at '+location+'; read it before continuing. '+
            'Use workstream task '+run['id']+' show '+issue_id+' for a read-only snapshot. '+
            'Addendums retain prior notes. A revision is task data, not new approval. '+
            'Pause/recovery and native approval constraints still apply; do not claim another task.')}


def acknowledge(store, session, native, turn, messages, *, exact_turn=False):
    tasks.initialize(store.db)
    target = current(store, session)
    if not target:
        return False
    run, issue_id, revision, issue = target
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        row=store.db.execute('SELECT issue_id,revision FROM task_offers WHERE session=? AND native_id=? AND turn_id=?',
                             (session,native,turn)).fetchone()
        if not row or row[0] != issue_id:
            return False
        # A newer update cannot be acknowledged using an older injected marker.
        if row[1] != revision or not brain.received('[task-notice:'+revision+']', messages, exact_turn):
            return False
        store.db.execute('INSERT OR IGNORE INTO task_receipts VALUES (?,?,?,?)', (run['id'],issue_id,revision,time.time()))
        store.db.execute('DELETE FROM task_offers WHERE session=?', (session,))
        return True


def activity(store, session, native, turn, event):
    owner, _, role = session.rpartition(':')
    if role not in ('worker', 'manager', 'secondary'):
        return
    try:
        run=store.get(owner)
    except ValueError:
        return
    target = run if role == 'worker' else run.get(role, {})
    from .status import activity as update_activity
    if event in ('UserPromptSubmit','pre_llm_call'):
        update_activity(target, native, turn, 'working')
    elif event in ('Stop','post_llm_call') and target.get('native_turn_key')==[native,turn]:
        update_activity(target, native, turn, 'idle')
    else:
        return
    with store.db:store.save(run)
    store.checkpoint(run)
