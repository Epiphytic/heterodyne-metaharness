"""Durable independent review projection. Authority: spec/reviews.md."""
import json

from .beads import BeadsError
from .task_gates import digest, scope
from .review_schedule import advance, suspended


def initialize(db):
    for sql in (
        '''CREATE TABLE IF NOT EXISTS review_engagements(
        run_id TEXT,issue_id TEXT,state TEXT NOT NULL, PRIMARY KEY(run_id,issue_id))''',
        '''CREATE TABLE IF NOT EXISTS review_jobs(
        id TEXT PRIMARY KEY,run_id TEXT NOT NULL,issue_id TEXT NOT NULL,
        kind TEXT NOT NULL,scope TEXT NOT NULL,request TEXT NOT NULL,
        created_at REAL NOT NULL,state TEXT NOT NULL,result TEXT,error TEXT)''',
        '''CREATE TABLE IF NOT EXISTS review_parts(
        job_id TEXT NOT NULL,event_id TEXT PRIMARY KEY)''',
        '''CREATE TABLE IF NOT EXISTS review_acceptances(
        job_id TEXT PRIMARY KEY,evidence TEXT NOT NULL)''',
    ):
        db.execute(sql)


def issue_for(run):
    issue = run.get('beads', {}).get('task_snapshot', {}).get('issue')
    if not issue or issue.get('id') != run.get('beads', {}).get('issue_id'):
        return None
    if issue.get('status') != 'in_progress' or issue.get('assignee') != run.get('beads', {}).get('worker_identity'):
        return None
    return issue


def held(store, run):
    from .operator_asks import initialize as asks_init
    asks_init(store.db)
    opened = store.db.execute('SELECT 1 FROM operator_asks WHERE group_id=? AND resolved_at IS NULL',
                              (run.get('group_id'),)).fetchone()
    other = any(r['id'] != run['id'] and r.get('group_id') == run.get('group_id')
                and r.get('native_turn_state') == 'working' for r in store.runs())
    return suspended(run, bool(opened), other)


def enqueue(store, run, issue, kind, identity, request, now):
    key = 'review:' + digest([run['id'], issue['id'], kind, identity])
    encoded = json.dumps(request, sort_keys=True)
    old = store.db.execute('SELECT request,scope FROM review_jobs WHERE id=?', (key,)).fetchone()
    if old and tuple(old) != (encoded, scope(issue)):
        raise BeadsError('Review identity already binds different evidence or task scope')
    store.db.execute('''INSERT OR IGNORE INTO review_jobs
        (id,run_id,issue_id,kind,scope,request,created_at,state) VALUES (?,?,?,?,?,?,?,'pending')''',
                     (key, run['id'], issue['id'], kind, scope(issue), encoded, now))
    return key


def schedule(store, run, now):
    """Caller holds supervisor lock; no native queue reads and no model calls."""
    initialize(store.db)
    issue = issue_for(run)
    if not issue or run.get('resume_required') or run.get('beads', {}).get('recovery_required'):
        return
    with store.db:
        row = store.db.execute('SELECT state FROM review_engagements WHERE run_id=? AND issue_id=?',
                               (run['id'], issue['id'])).fetchone()
        state = json.loads(row[0]) if row else {}
        due = advance(state, now, held(store, run))
        outstanding = store.db.execute("SELECT 1 FROM review_jobs WHERE run_id=? AND issue_id=? AND kind='progress' AND state IN ('pending','running','reviewed')", (run['id'], issue['id'])).fetchone()
        if due is not None and not outstanding:
            enqueue(store, run, issue, 'progress', due, {'due': due, 'workdir': run['workdir']}, now)
        store.db.execute('INSERT OR REPLACE INTO review_engagements VALUES (?,?,?)',
                         (run['id'], issue['id'], json.dumps(state)))
        # The completion boundary must be the same native turn named by the handoff.
        receipt = run.get('native_completion', {})
        rows = store.db.execute("SELECT * FROM review_jobs WHERE run_id=? AND state='boundary'",
                                (run['id'],)).fetchall()
        for job in rows:
            request = json.loads(job['request'])
            if (job['issue_id'] == issue['id'] and job['scope'] == scope(issue)
                    and receipt.get('applied') is True
                    and [receipt.get('native'), receipt.get('turn')] == request['native_turn_key']
                    and receipt.get('at', 0) >= job['created_at']):
                store.db.execute("UPDATE review_jobs SET state='pending' WHERE id=?", (job['id'],))


def handoff(store, run, issue, request, now):
    from .review_evidence import retained, git_read
    initialize(store.db)
    if issue_for(run) is None or scope(issue) != scope(issue_for(run)) or issue['id'] != issue_for(run)['id']:
        raise BeadsError('Completion review requires the current verified worker claim')
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required') or run.get('observed_state') == 'awaiting_approval':
        raise BeadsError('Resolve recovery or pending native approval before handoff')
    required = {'key', 'commit', 'artifacts', 'native_turn_key'}
    if set(request) != required or not request['key'] or not request['artifacts']:
        raise BeadsError('Handoff requires key, commit, retained artifacts and native_turn_key')
    if (not isinstance(request['native_turn_key'], list) or len(request['native_turn_key']) != 2
            or not all(isinstance(v, str) and v for v in request['native_turn_key'])):
        raise BeadsError('Handoff requires exact native session and turn identity')
    if request['native_turn_key'] != run.get('native_turn_key') or run.get('native_turn_state') != 'working':
        raise BeadsError('Handoff must name the current active native turn')
    if request['commit'] != git_read(run['workdir'], 'rev-parse', 'HEAD')['text'].strip():
        raise BeadsError('Handoff commit must match owned checkout')
    if git_read(run['workdir'], 'status', '--porcelain=v1', '--untracked-files=normal')['text'].strip():
        raise BeadsError('Completion handoff requires a clean committed worktree')
    for artifact in request['artifacts']:
        retained(artifact['path'], artifact['sha256'])
    with store.db:
        key_expected = 'review:' + digest([run['id'], issue['id'], 'completion', request['key']])
        existing = store.db.execute('SELECT 1 FROM review_jobs WHERE id=?', (key_expected,)).fetchone()
        key = enqueue(store, run, issue, 'completion', request['key'],
                      {**request, 'workdir': run['workdir']}, now)
        if not existing:
            store.db.execute("UPDATE review_jobs SET state='boundary' WHERE id=?", (key,))
    return {'review_id': key, 'waiting_for_native_boundary': True}


def record_result(store, key, result):
    """Only independent runner results enter here; worker text is never a review."""
    if (set(result) != {'summary', 'blocking_findings', 'evidence_digest', 'reviewer_session'}
            or not isinstance(result['summary'], str) or not result['summary'].strip()
            or not isinstance(result['blocking_findings'], list)
            or not all(isinstance(f, str) and f.strip() for f in result['blocking_findings'])
            or not result['reviewer_session'] or not result['evidence_digest']):
        raise ValueError('Invalid independent reviewer result')
    encoded = json.dumps(result, sort_keys=True)
    with store.db:
        old = store.db.execute('SELECT result FROM review_jobs WHERE id=?', (key,)).fetchone()
        if old is None or old[0] and old[0] != encoded:
            raise ValueError('Missing job or changed immutable result')
        store.db.execute("UPDATE review_jobs SET result=?,state='reviewed',error=NULL WHERE id=?", (encoded, key))


def queue_result(store, run, job, now):
    """Full multipart summary through shared outbox; delivery acknowledgment is separate."""
    from .transitions import parts
    if held(store, run) or not run.get('group_id'):
        return
    result = json.loads(job['result'])
    text = f"{job['kind'].capitalize()} review: {job['issue_id']}\n" + result['summary']
    text += '\nBlocking findings:\n' + ('\n'.join(result['blocking_findings']) or 'None reported.')
    with store.db:
        for index, part in enumerate(parts(text)):
            key = job['id'] + ':' + str(index)
            store.db.execute('INSERT OR IGNORE INTO outbox(id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)',
                             (key, run['id'], run['group_id'], part, now))
            store.db.execute('INSERT OR IGNORE INTO review_parts VALUES (?,?)', (job['id'], key))
        store.db.execute("UPDATE review_jobs SET state='queued' WHERE id=?", (job['id'],))


def assert_close(store, run, issue):
    from .review_evidence import retained, git_read
    initialize(store.db)
    if issue.get('status') == 'closed':
        return
    jobs = store.db.execute("SELECT * FROM review_jobs WHERE run_id=? AND issue_id=? AND kind='completion' ORDER BY created_at DESC,rowid DESC",
                            (run['id'], issue['id'])).fetchall()
    if not jobs:
        raise BeadsError('Completion handoff and delivered independent review required')
    job = jobs[0]
    if job['scope'] != scope(issue):
        raise BeadsError('Task scope changed after completion review')
    request = json.loads(job['request'])
    if git_read(request['workdir'], 'rev-parse', 'HEAD')['text'].strip() != request['commit']:
        raise BeadsError('Completion review commit is stale')
    for artifact in request['artifacts']:
        retained(artifact['path'], artifact['sha256'])
    rows = store.db.execute('SELECT o.delivered_at FROM review_parts p JOIN outbox o ON o.id=p.event_id WHERE p.job_id=?', (job['id'],)).fetchall()
    if job['state'] != 'queued' or not rows or any(r[0] is None for r in rows):
        raise BeadsError('Full completion review must be delivered before closure')
    from .review_dispatch import directory
    evidence_path = directory(store, job['id']) / 'evidence.json'
    if evidence_path.exists():
        observed = json.loads(evidence_path.read_text())['observed']
        current_status = git_read(request['workdir'], 'status', '--porcelain=v1', '--untracked-files=normal')
        current_diff = git_read(request['workdir'], 'diff', '--no-ext-diff', '--no-textconv', 'HEAD', '--')
        if observed['status'] != current_status or observed['diff'] != current_diff:
            raise BeadsError('Working tree changed after completion review')
    else:
        raise BeadsError('Retained independent evidence snapshot missing')
    result = json.loads(job['result'])
    if hashlib_sha(evidence_path.read_bytes()) != result['evidence_digest']:
        raise BeadsError('Independent evidence snapshot changed')
    accepted = store.db.execute('SELECT evidence FROM review_acceptances WHERE job_id=?', (job['id'],)).fetchone()
    if accepted:
        acceptance = json.loads(accepted[0])
        retained(acceptance['path'], acceptance['sha256'])
        if acceptance.get('review_id') != job['id'] or acceptance.get('result_digest') != digest(job['result']):
            raise BeadsError('Acceptance no longer binds this review')
    if result['blocking_findings'] and not accepted:
        raise BeadsError('Blocking review findings require fresh review or explicit operator acceptance')


def hashlib_sha(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def held_events(store):
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='review_parts'").fetchone():
        return []
    held_runs = {run['id'] for run in store.runs() if held(store, run)}
    return [row['event_id'] for row in store.db.execute(
        'SELECT p.event_id,o.run_id FROM review_parts p JOIN outbox o ON o.id=p.event_id WHERE o.delivered_at IS NULL')
        if row['run_id'] in held_runs]
