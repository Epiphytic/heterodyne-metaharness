"""Explicit completion handoff and operator reconciliation. spec/reviews.md."""
import json
from pathlib import Path
import time

from .beads import BeadsError
from . import reviews


def configure(sub):
    parser = sub.add_parser('review')
    parser.add_argument('name')
    actions = parser.add_subparsers(dest='review_action', required=True)
    actions.add_parser('inspect')
    handoff = actions.add_parser('handoff')
    handoff.add_argument('--file', required=True)
    for verb in ('accept', 'retry'):
        action = actions.add_parser(verb)
        action.add_argument('review_id')
        action.add_argument('--evidence-file', required=True)


def dispatch(args, store, beads):
    run = store.get(args.name)
    reviews.initialize(store.db)
    if args.review_action == 'inspect':
        return [dict(r) for r in store.db.execute('SELECT * FROM review_jobs WHERE run_id=? ORDER BY created_at DESC LIMIT 50', (run['id'],))]
    if args.review_action == 'handoff':
        issue = beads.show(run, run['beads']['issue_id'])
        if issue.get('assignee') != beads._queue(run).worker or issue.get('status') != 'in_progress':
            raise BeadsError('Completion handoff requires current ownership')
        from .tasks import assert_close_current
        assert_close_current(store, run, issue)
        return reviews.handoff(store, run, issue, json.loads(Path(args.file).read_text()), time.time())
    job = store.db.execute('SELECT * FROM review_jobs WHERE id=? AND run_id=?', (args.review_id, run['id'])).fetchone()
    evidence = json.loads(Path(args.evidence_file).read_text())
    if job is None or evidence.get('authority_basis') != 'operator' or not evidence.get('actor'):
        raise BeadsError('Explicit operator reconciliation evidence required')
    from .review_evidence import retained
    retained(evidence['path'], evidence['sha256'])
    if evidence.get('review_id') != job['id'] or evidence.get('result_digest') != reviews.digest(job['result']):
        raise BeadsError('Reconciliation must bind exact review result')
    with store.db:
        if args.review_action == 'accept':
            if job['state'] != 'queued' or not job['result']:
                raise BeadsError('Only a completed review can have findings accepted')
            encoded = json.dumps(evidence, sort_keys=True)
            prior = store.db.execute('SELECT evidence FROM review_acceptances WHERE job_id=?', (job['id'],)).fetchone()
            if prior and prior[0] != encoded:
                raise BeadsError('Acceptance evidence is immutable')
            store.db.execute('INSERT OR IGNORE INTO review_acceptances VALUES (?,?)', (job['id'], encoded))
        else:
            # Explicit new attempt keeps the old model attempt and all its evidence.
            retry_id = job['id'] + ':retry:' + reviews.digest(evidence)
            if job['state'] == 'superseded' and store.db.execute('SELECT 1 FROM review_jobs WHERE id=?', (retry_id,)).fetchone():
                return {'review_id': job['id'], 'recorded': 'retry'}
            if job['state'] not in ('failed', 'running'):
                raise BeadsError('Retry only failed/uncertain jobs after operator process inspection')
            if evidence.get('old_process_exited') is not True:
                raise BeadsError('Verify prior runner exited before retry')
            retry_id = job['id'] + ':retry:' + reviews.digest(evidence)
            store.db.execute('''INSERT OR IGNORE INTO review_jobs
                (id,run_id,issue_id,kind,scope,request,created_at,state) VALUES (?,?,?,?,?,?,?,'pending')''',
                (retry_id, job['run_id'], job['issue_id'], job['kind'], job['scope'], job['request'], time.time()))
            store.db.execute("UPDATE review_jobs SET state='superseded' WHERE id=?", (job['id'],))
    return {'review_id': job['id'], 'recorded': args.review_action}
