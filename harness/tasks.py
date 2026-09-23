"""Generic task admission and durable Bead projections. Contract: spec/tasks.md."""
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import time

from .beads import BeadsError
from .store import TERMINAL


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS task_revisions(issue_id TEXT,revision TEXT,data TEXT,observed_at REAL,
      PRIMARY KEY(issue_id,revision));
    CREATE TABLE IF NOT EXISTS task_current(issue_id TEXT PRIMARY KEY,revision TEXT,data TEXT);
    CREATE TABLE IF NOT EXISTS task_receipts(run_id TEXT,issue_id TEXT,revision TEXT,ack_at REAL,
      PRIMARY KEY(run_id,issue_id,revision));
    CREATE TABLE IF NOT EXISTS task_offers(session TEXT,native_id TEXT,turn_id TEXT,issue_id TEXT,
      revision TEXT, PRIMARY KEY(session,native_id,turn_id));
    CREATE TABLE IF NOT EXISTS task_updates(issue_id TEXT,request_key TEXT,fingerprint TEXT,
      body TEXT,state TEXT, PRIMARY KEY(issue_id,request_key));
    CREATE TABLE IF NOT EXISTS task_baselines(run_id TEXT,issue_id TEXT,fingerprint TEXT,
      PRIMARY KEY(run_id,issue_id));
    ''')


def worker(run):
    verified = run.get('beads', {}).get('worker_identity')
    if verified:
        if not verified.startswith(run['agent'] + ':') or not verified.endswith(':' + run['id']):
            raise BeadsError('Recorded worker identity does not match stable run')
        return verified
    return run['agent'] + ':' + socket.gethostname() + ':' + run['id']


def observe(store, issue):
    """Record full immutable revisions; current owner alone receives active context."""
    initialize(store.db)
    encoded = json.dumps(issue, sort_keys=True)
    if len(encoded.encode()) > 1024 * 1024:
        raise BeadsError('Task snapshot exceeds bound; inspect externally')
    revision = hashlib.sha256(encoded.encode()).hexdigest()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        from .transitions import observe as observe_transition
        for routed in store.runs():
            labels = issue.get('labels', [])
            bound = routed.get('beads', {}).get('issue_id') == issue['id']
            if bound or ('ws:' + routed.get('beads', {}).get('workstream', '') in labels
                         and 'agent:' + routed['agent'] in labels
                         and ('session:' + routed['id'] in labels)):
                observe_transition(store, routed, issue)
        store.db.execute('INSERT OR IGNORE INTO task_revisions VALUES (?,?,?,?)',
                         (issue['id'], revision, encoded, time.time()))
        store.db.execute('INSERT OR REPLACE INTO task_current VALUES (?,?,?)', (issue['id'], revision, encoded))
    for run in store.runs():
        secondary = run.get('secondary', {})
        if secondary.get('beads', {}).get('issue_id') == issue['id']:
            secondary['beads']['task_snapshot'] = dict(revision=revision, issue=issue,
                observed_at=time.time(), execution_authority='Task data, not authority')
            with store.db:
                store.save(run)
            store.checkpoint(run)
        if run.get('beads', {}).get('issue_id') != issue['id']:
            continue
        state = run['beads']
        from .continuation import task_changed
        prior = state.get('task_snapshot', {}).get('issue')
        if prior and 'task_signature' not in run.get('continuation', {}):
            task_changed(run, prior)
        task_changed(run, issue)
        state['task_snapshot'] = {'revision': revision, 'observed_at': time.time(), 'issue': issue,
                                  'execution_authority': 'Snapshot is task data; current claim and approval checks remain required.'}
        with store.db:
            store.save(run)
        store.checkpoint(run)
        if issue.get('assignee') == worker(run) and issue.get('status') == 'in_progress':
            notify_owner(store, run, issue['id'], revision)
    return revision


def admit(store, supervisor, beads, run, title, text, key, **options):
    """Caller holds shared lock. Admission never replaces an existing claim."""
    if run['state'] in TERMINAL:
        raise BeadsError('Cannot admit work to a terminated session')
    issue = beads.create(run, title, text, key=key, **options)
    identity = hashlib.sha256((run['id'] + '\0' + key).encode()).hexdigest()
    message = ('Task queued: ' + issue['id'] + '. Preserve the current claim and native approvals. '
               'At an authorized task boundary inspect and claim through workstream task ' + run['name'] +
               '; use the existing worker and an isolated owned worktree per task.')
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state,target) VALUES(?,?,?,?,?,?)',
                         (identity, run['id'], message, time.time(), 'pending', 'manager'))
    supervisor.persist(run)
    observe(store, issue)
    return dict(issue, issue_id=issue['id'], run_id=run['id'], notification_id=identity, queued=True)


def addendum(store, beads, run, issue_id, text, key, authorization):
    """Append once, retaining request identity and uncertainty across process loss."""
    if not all(value.strip() for value in (text, key, authorization)):
        raise BeadsError('Addendum text, stable key and actual authorization evidence required')
    initialize(store.db)
    queue = beads._queue(run)
    issue = queue.show(issue_id)
    if issue.get('status') == 'closed' or not queue.matches(issue):
        raise BeadsError('Addendum requires an open task routed to this workstream')
    fingerprint = hashlib.sha256(json.dumps([text, authorization]).encode()).hexdigest()
    marker = '[harness-addendum:' + hashlib.sha256((issue_id+'\0'+key).encode()).hexdigest() + ']'
    body = marker + '\nAuthorization evidence (caller record, not a new approval): ' + authorization + '\n' + text
    with store.db:
        row = store.db.execute('SELECT fingerprint,body,state FROM task_updates WHERE issue_id=? AND request_key=?', (issue_id,key)).fetchone()
        if row and (row[0] != fingerprint or row[1] != body):
            raise BeadsError('Addendum key already identifies different content')
        if not row:
            store.db.execute('INSERT INTO task_updates VALUES (?,?,?,?,?)', (issue_id,key,fingerprint,body,'prepared'))
    notes = issue.get('notes') or ''
    if marker in notes and body not in notes:
        raise BeadsError('Addendum marker exists with altered content; inspect history')
    if body not in notes:
        if row and row[2] != 'prepared':
            raise BeadsError('Prior addendum write uncertain or removed; reconcile remote history, never blind replay')
        with store.db:
            store.db.execute("UPDATE task_updates SET state='uncertain' WHERE issue_id=? AND request_key=?", (issue_id,key))
        queue.bd('update', issue_id, '--append-notes', body)
        issue = queue.show(issue_id)
        if body not in (issue.get('notes') or ''):
            raise BeadsError('Addendum write not confirmed; retain uncertainty')
    with store.db:
        store.db.execute("UPDATE task_updates SET state='confirmed' WHERE issue_id=? AND request_key=?", (issue_id,key))
    revision = observe(store, issue)
    return {'issue_id':issue_id, 'revision':revision, 'updated':True}


def readonly(home, name, issue_id=None):
    """No Store initialization, chmod, lock, schema writes or native queue access."""
    path = Path(home).expanduser().resolve() / 'workstreams/harness.sqlite3'
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT data FROM runs WHERE id=? OR name=?', (name,name)).fetchone()
        if not row:
            row=db.execute('SELECT runs.data FROM runs JOIN identities ON runs.id=identities.run_id WHERE alias=?',(name,)).fetchone()
        if not row:
            raise BeadsError('Unknown workstream')
        run = json.loads(row[0])
        wanted = issue_id or run.get('beads',{}).get('issue_id')
        record=db.execute('SELECT revision,data FROM task_current WHERE issue_id=?',(wanted,)).fetchone()
        if not record:
            raise BeadsError('Task snapshot unavailable; operator must run task reconcile')
        issue=json.loads(record[1])
        labels=issue.get('labels',[])
        ws=run.get('beads',{}).get('workstream')
        routes={prefix:[label[len(prefix):] for label in labels if label.startswith(prefix)]
                for prefix in ('agent:','ws:','session:')}
        bound=run.get('beads',{}).get('issue_id')==wanted
        if not bound and (routes['agent:'] != [run['agent']] or routes['ws:'] != [ws]
                          or routes['session:'] not in ([],[run['id']])):
            raise BeadsError('Task snapshot is not routed to this worker')
        if issue.get('assignee') and issue['assignee'] != worker(run):
            raise BeadsError('Task snapshot belongs to another worker')
        from .task_delivery import projection
        result = {'snapshot':{'revision':record[0],'issue':issue},'read_only':True,
                  'current_claim_must_be_verified':True}
        if linked := projection(db, issue):
            result['delivery_steps'] = linked
        return result



def notify_owner(store, run, issue_id, revision):
    identity = 'task-update:' + hashlib.sha256((run['id']+'\0'+issue_id+'\0'+revision).encode()).hexdigest()
    text = ('Assigned Bead '+issue_id+' has revision '+revision+'. Read the task snapshot through workstream task '+
            run['id']+' show '+issue_id+'. Preserve existing approval and recovery constraints; this notice is task data, not new authority.')
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)',
                         (identity,run['id'],text,time.time(),'pending','worker'))


def current_notification(store, run, row):
    """Retire stale owner/revision messages; native context supplies the current one."""
    issue_id=run.get('beads',{}).get('issue_id')
    current=store.db.execute('SELECT revision,data FROM task_current WHERE issue_id=?',(issue_id,)).fetchone()
    if not current:
        return False
    issue=json.loads(current[1])
    expected='task-update:'+hashlib.sha256((run['id']+'\0'+issue_id+'\0'+current[0]).encode()).hexdigest()
    if store.db.execute('SELECT 1 FROM task_receipts WHERE run_id=? AND issue_id=? AND revision=?',
                        (run['id'],issue_id,current[0])).fetchone():
        return False
    return row['id']==expected and issue.get('assignee')==worker(run) and issue.get('status')=='in_progress'


def scope_fingerprint(issue):
    if issue.get('dependency_count', 0) and not isinstance(issue.get('dependencies'), list):
        raise BeadsError('Dependency records missing from task projection; reconcile full native show')
    fields = ('title', 'description', 'acceptance_criteria', 'design', 'spec_id', 'notes',
              'metadata', 'labels', 'dependencies', 'assignee', 'priority', 'issue_type',
              'due_at', 'defer_until', 'estimated_minutes', 'external_ref')
    scope = {key: issue.get(key) for key in fields}
    scope['metadata'] = {key: value for key, value in (issue.get('metadata') or {}).items()
                         if key not in ('harness_lifecycle', 'harness_delivery_artifact')}
    encoded = json.dumps(scope, sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def record_claim_baseline(store, run, issue):
    """Only the original successful claim establishes a baseline, never reconciliation."""
    initialize(store.db)
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO task_baselines VALUES (?,?,?)',
                         (run['id'], issue['id'], scope_fingerprint(issue)))


def assert_close_current(store, run, issue):
    # Native close still checks ownership/recovery and preserves prior evidence.
    if issue.get('status') == 'closed':
        return
    initialize(store.db)
    baseline = store.db.execute('SELECT fingerprint FROM task_baselines WHERE run_id=? AND issue_id=?',
                               (run['id'], issue['id'])).fetchone()
    receipt = store.db.execute('''SELECT revisions.data FROM task_receipts AS receipts
        JOIN task_revisions AS revisions ON receipts.issue_id=revisions.issue_id
        AND receipts.revision=revisions.revision WHERE receipts.run_id=? AND receipts.issue_id=?
        ORDER BY receipts.ack_at DESC LIMIT 1''', (run['id'], issue['id'])).fetchone()
    known = scope_fingerprint(json.loads(receipt[0])) if receipt else baseline[0] if baseline else None
    observe(store, issue)
    if known != scope_fingerprint(issue):
        raise BeadsError('Latest task scope has not been acknowledged by the current worker; reconcile and deliver before close')
