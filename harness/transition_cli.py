"""Explicit evidence ingress, never an approval executor. See spec/transitions.md."""
import json
from pathlib import Path

from . import transitions
from .beads import BeadsError


def configure(sub):
    command = sub.add_parser('notice')
    command.add_argument('name')
    commands = command.add_subparsers(dest='notice_action', required=True)
    applied = commands.add_parser('record')
    applied.add_argument('--file', required=True)
    commands.add_parser('inspect')


def dispatch(args, store, beads):
    run = store.get(args.name)
    if args.notice_action == 'inspect':
        return {'batches': [dict(row) for row in store.db.execute('''SELECT b.*,
          (SELECT count(*) FROM transitions t WHERE t.batch_id=b.id) AS transition_count
          FROM transition_batches b WHERE run_id=? ORDER BY id DESC LIMIT 100''', (run['id'],))],
                'retirements': [dict(row) for row in store.db.execute('''SELECT r.* FROM transition_retirements r
          JOIN events e ON e.id=r.event_id WHERE e.run_id=? ORDER BY retired_at DESC LIMIT 100''', (run['id'],))]}
    evidence = json.loads(Path(args.file).read_text())
    required = ('issue_id', 'kind', 'key', 'subject', 'actor', 'authority_basis', 'evidence_ref')
    if any(not isinstance(evidence.get(key), str) or not evidence[key].strip() for key in required):
        raise BeadsError('Notice requires issue, kind, key, subject, actor, authority_basis and evidence_ref')
    if evidence['kind'] not in ('approval-applied', 'agent-usage-limited', 'agent-crashed'):
        raise BeadsError('Unsupported evidenced notice kind')
    if evidence['kind'] == 'approval-applied' and evidence.get('result') != 'applied':
        raise BeadsError('Consent or a recommendation is not an applied approval')
    queue = beads._queue(run)
    issue = queue.show(evidence['issue_id'])
    if not queue.matches(issue) or issue['id'] != run.get('beads', {}).get('issue_id'):
        raise BeadsError('Notice must identify the current routed task')
    # Attribution is retained operator evidence, not authentication of arbitrary text.
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        identity = transitions.append(store, run, issue, evidence['kind'], evidence['kind'],
                                      evidence['key'], evidence)
    return {'transition_id': identity, 'queued': True, 'native_approval_changed': False}
