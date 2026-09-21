"""Launch only durable scheduled jobs; no model in detection. spec/reviews.md."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from . import reviews
from .review_evidence import collect
from .review_runner import atomic
from .task_gates import scope


def directory(store, key):
    return store.root / 'reviews' / hashlib.sha256(key.encode()).hexdigest()


def start(store, run, job, config):
    target = directory(store, job['id'])
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = target / 'evidence.json'
    request = json.loads(job['request'])
    if not path.exists():
        atomic(path, {'task': reviews.issue_for(run), 'kind': job['kind'],
                      'observed': collect(run, request)})
    with store.db:
        store.db.execute("UPDATE review_jobs SET state='running' WHERE id=?", (job['id'],))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_WORKSTREAM_', 'BTQ_', 'HERMES_KANBAN_'))}
    native = Path(config.get('hermes_repo', '~/.hermes/hermes-agent')).expanduser()
    env['PYTHONPATH'] = str(native) + os.pathsep + str(Path(__file__).resolve().parents[1])
    python = str(native / '.venv/bin/python')
    with (target / 'launch.log').open('a') as log:
        subprocess.Popen([python, '-m', 'harness.review_runner', str(target)],
                         cwd=target, env=env, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True)


def tick(store, run, now, config):
    """Serialized by supervisor lock. New jobs require explicit rollout enablement."""
    if config.get('reviews', {}).get('enabled') is not True:
        return
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required'):
        return
    reviews.schedule(store, run, now)
    issue = reviews.issue_for(run)
    if not issue:
        return
    jobs = store.db.execute("SELECT * FROM review_jobs WHERE run_id=? AND state IN ('pending','running','reviewed') ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END, CASE kind WHEN 'completion' THEN 0 ELSE 1 END,created_at,rowid", (run['id'],)).fetchall()
    for job in jobs:
        if job['state'] != 'running' and (job['issue_id'] != issue['id'] or job['scope'] != scope(issue)):
            with store.db:
                store.db.execute("UPDATE review_jobs SET state='stale',error='task scope or binding changed' WHERE id=?", (job['id'],))
            continue
        target = directory(store, job['id'])
        if job['state'] == 'running':
            if (target / 'error.json').exists():
                with store.db:
                    store.db.execute("UPDATE review_jobs SET state='failed',error=? WHERE id=?", ((target / 'error.json').read_text(), job['id']))
            elif (target / 'result.json').exists():
                result = json.loads((target / 'result.json').read_text())
                expected = hashlib.sha256((target / 'evidence.json').read_bytes()).hexdigest()
                if result.get('evidence_digest') != expected:
                    raise ValueError('Reviewer evidence digest mismatch')
                reviews.record_result(store, job['id'], result)
            # A lost spawn or a process that died without writing error.json is
            # inspectable running/uncertain, not permission to duplicate a model call.
            return
        if job['state'] == 'reviewed':
            reviews.queue_result(store, run, job, now)
            continue
        if reviews.held(store, run):
            return
        start(store, run, job, config.get('reviews', {}))
        return  # At most one model per workstream, never one per overdue slot.
