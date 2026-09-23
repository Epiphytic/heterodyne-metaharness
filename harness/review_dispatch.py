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
        if job['kind'] == 'artifact':
            from .review_gate import artifacts
            evidence = {'task': reviews.issue_for(run), 'kind': request['artifact_kind'],
                        'artifacts': artifacts(request['artifacts'])}
            if request['artifact_kind'] == 'pr':
                evidence['observed'] = collect(run, request)
                if any(evidence['observed'][key]['truncated'] for key in ('committed_diff', 'diff', 'status')):
                    raise ValueError('PR evidence truncated; explicit complete artifact review required')
            atomic(path, evidence)
        else:
            atomic(path, {'task': reviews.issue_for(run), 'kind': job['kind'],
                          'observed': collect(run, request)})
    if job['kind'] == 'artifact':
        atomic(target / 'model.json', request['policy'])
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


def tick(store, run, now, config, beads=None):
    """Serialized by supervisor lock. New jobs require explicit rollout enablement."""
    legacy = config.get('reviews', {}).get('enabled') is True
    enabled_gate = config.get('review_gate', {}).get('enabled') is True
    if not legacy and not enabled_gate:
        return
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required'):
        return
    reviews.initialize(store.db)
    if legacy:
        reviews.schedule(store, run, now)
    issue = reviews.issue_for(run)
    if not issue:
        return
    jobs = store.db.execute("SELECT * FROM review_jobs WHERE run_id=? AND state IN ('pending','running','reviewed') ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END, CASE kind WHEN 'completion' THEN 0 ELSE 1 END,created_at,rowid", (run['id'],)).fetchall()
    for job in jobs:
        if job['kind'] == 'artifact':
            if beads is None or not enabled_gate:
                if job['state'] == 'running':
                    return
                continue
            if artifact_tick(store, beads, run, job, config):
                return
            continue
        if not legacy:
            if job['state'] == 'running':
                return
            continue
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


def artifact_tick(store, beads, run, job, config):
    """Process one durable gate attempt; errors never become an implicit pass."""
    from . import review_gate
    target = directory(store, job['id'])
    try:
        review_gate.current(store, beads, run, job, config)
        if job['state'] == 'running':
            if (target / 'error.json').exists():
                review_gate.escalate(store, beads, run, job, config,
                                     'Independent reviewer failed; inspect retained runner error.')
            elif (target / 'result.json').exists():
                try:
                    review_gate.process_result(store, beads, run, job,
                        json.loads((target / 'result.json').read_text()), config)
                except (ValueError, review_gate.BeadsError):
                    review_gate.escalate(store, beads, run, job, config,
                                         'Invalid independent reviewer result; inspect retained evidence.')
            return True  # Uncertain processes block all further model launches.
        if job['state'] == 'reviewed':
            review_gate.process_result(store, beads, run, job, json.loads(job['result']), config)
            return True
        if reviews.held(store, run):
            return True
        start(store, run, job, config.get('reviews', {}))
        return True
    except (ValueError, KeyError, OSError, review_gate.BeadsError) as exc:
        # Only a running/uncertain process retains the concurrency slot.
        state = 'running' if job['state'] == 'running' else 'stale'
        with store.db:
            store.db.execute('UPDATE review_jobs SET state=?,error=? WHERE id=?', (state, str(exc), job['id']))
        store.event(run, 'blocked', 'Artifact review needs reconciliation: ' + str(exc),
                    job['id'] + ':reconcile')
        return state == 'running'
