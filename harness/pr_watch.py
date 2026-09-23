"""Bounded PR/patch reconciliation; the hourly backstop never polls a model."""
import json
import subprocess
import tempfile
import time

from .beads import BeadsError
from .blocker_receipts import decode, digest
from . import task_blockers, task_gates


def attach(supervisor, run, gate_id, source):
    gate = supervisor.beads._queue(run).show(gate_id)
    binding = task_gates.metadata(gate).get('harness_gate', {})
    blocker = binding.get('blocker', {})
    if blocker.get('run_id') != run['id'] or blocker.get('category') != 'pr-review':
        raise BeadsError('Watch must bind this run\'s PR-review blocker')
    if set(source) != {'adapter', 'repository', 'review_id', 'head'} or source['head'] != binding['revision']:
        raise BeadsError('Watch requires exact repository, review and gated head')
    adapters = supervisor.config.get('beads', {}).get('blockers', {}).get('watch_adapters', {})
    if source['adapter'] not in adapters:
        raise BeadsError('Unconfigured review adapter')
    watches = run.setdefault('pr_watches', {})
    previous = watches.get(gate_id)
    if previous and previous['source'] != source:
        raise BeadsError('Watch source changed; use a fresh revision-bound blocker')
    watches.setdefault(gate_id, dict(source=source, next_check=0, phase='pending', events={}))
    supervisor.persist(run)
    return watches[gate_id]


def query(config, source):
    argv = config.get('argv')
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise BeadsError('Watch adapter requires explicit executable argv')
    # Adapter output is untrusted data; no command or path from it is executed.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(argv, input=json.dumps(source).encode(), stdout=output,
                                stderr=subprocess.DEVNULL, timeout=30, check=False)
        output.seek(0)
        raw = output.read(65537)
    if result.returncode or len(raw) > 65536:
        raise BeadsError('Review adapter failed or exceeded output bound')
    return decode(raw.decode('utf-8'))


def apply(supervisor, run, gate_id, payload, now):
    watch = run['pr_watches'][gate_id]
    source = watch['source']
    if not isinstance(payload, dict) or payload.get('source') != source:
        raise BeadsError('Review response belongs to a different source/head')
    queue = supervisor.beads._queue(run)
    gate = queue.show(gate_id)
    binding = task_gates.metadata(gate)['harness_gate']
    if payload.get('state') == 'approved':
        task_blockers.resolve(supervisor.store, supervisor.beads, run, gate_id,
                              payload['evidence'], supervisor.config)
        watch['phase'] = 'resolved'
    elif payload.get('state') == 'changes-requested':
        comments = payload.get('comments')
        if not isinstance(comments, list) or not 1 <= len(comments) <= 100:
            raise BeadsError('Bounded review comments required')
        adapter = supervisor.config['beads']['blockers']['watch_adapters'][source['adapter']]
        for comment in comments:
            fix(supervisor, run, binding, watch, adapter, comment)
    elif payload.get('state') != 'pending':
        raise BeadsError('Unknown review state; do not infer approval')
    watch['last_checked'] = now
    watch['next_check'] = now + 3600
    supervisor.persist(run)
    return watch


def fix(supervisor, run, binding, watch, adapter, comment):
    if not isinstance(comment, dict) or set(comment) != {'id', 'author', 'body'}:
        raise BeadsError('Review comment schema mismatch')
    if comment['author'] not in adapter.get('reviewers', []) or not all(
            isinstance(v, str) and v.strip() for v in comment.values()):
        raise BeadsError('Review comment is not from an authorized reviewer')
    key = digest([watch['source'], comment['id']])
    fingerprint = digest(comment)
    prior = watch['events'].get(key)
    if prior and prior['digest'] != fingerprint:
        raise BeadsError('Review event changed under its identity')
    issue = supervisor.beads.create(run, 'Review fix: ' + comment['id'], comment['body'],
        key='review-fix:' + key, metadata={'harness_review_source': watch['source'],
                                         'review_comment_digest': fingerprint})
    queue = supervisor.beads._queue(run)
    parent = queue.show(binding['issue_id'])
    if not any(e.get('id') == issue['id'] and e.get('dependency_type') == 'blocks'
               for e in parent.get('dependencies', [])):
        queue.bd('dep', 'add', parent['id'], issue['id'], '--type', 'blocks')
    watch['events'][key] = {'digest': fingerprint, 'issue_id': issue['id']}
    supervisor.persist(run)


def tick(supervisor, run, now=None):
    now = time.time() if now is None else now
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required'):
        return
    settings = supervisor.config.get('beads', {}).get('blockers', {})
    if settings.get('enabled') is not True or run.get('state') == 'stopped':
        return
    adapters = settings.get('watch_adapters', {})
    for gate_id, watch in list(run.get('pr_watches', {}).items())[:100]:
        if watch['phase'] != 'pending' or now < watch['next_check']:
            continue
        # Daemon/CLI shared store lock serializes attempts. Persist before I/O;
        # process loss leaves one bounded hourly retry, not another agent claim.
        watch['next_check'] = now + 3600
        supervisor.persist(run)
        try:
            apply(supervisor, run, gate_id, query(adapters[watch['source']['adapter']], watch['source']), now)
            watch.pop('error', None)
        except (BeadsError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            watch['error'] = str(exc)[:1000]
        supervisor.persist(run)
