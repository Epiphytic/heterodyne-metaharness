"""Explicit blocker commands; operator configuration supplies authority."""
from pathlib import Path

from .beads import BeadsError
from .blocker_receipts import decode
from . import task_blockers


def configure(actions):
    parser = actions.add_parser('blocker')
    commands = parser.add_subparsers(dest='blocker_action', required=True)
    commands.add_parser('create').add_argument('--file', required=True)
    resolver = commands.add_parser('resolve')
    resolver.add_argument('gate_id')
    resolver.add_argument('--evidence-file', required=True)
    receiver = commands.add_parser('receive')
    receiver.add_argument('gate_id')
    receiver.add_argument('--evidence-file', required=True)
    second = commands.add_parser('secondary-start')
    second.add_argument('issue_id')
    commands.add_parser('secondary-finish')
    watch = commands.add_parser('watch')
    watch.add_argument('gate_id')
    watch.add_argument('--file', required=True)
    request = commands.add_parser('request')
    request.add_argument('gate_id')


def dispatch(args, store, beads, run, config, supervisor):
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required'):
        raise BeadsError('Reconcile recovery before blocker operations')
    if args.blocker_action == 'secondary-start':
        from .secondary_worker import start
        return start(supervisor, run, args.issue_id)
    if args.blocker_action == 'secondary-finish':
        from .secondary_worker import finish
        return finish(supervisor, run)
    if args.blocker_action == 'watch':
        from .pr_watch import attach
        return attach(supervisor, run, args.gate_id, decode(Path(args.file).read_text()))
    if args.blocker_action == 'create':
        return task_blockers.create(store, beads, run, decode(Path(args.file).read_text()), config)
    if args.blocker_action in ('resolve', 'receive'):
        handler = getattr(task_blockers, args.blocker_action)
        return handler(store, beads, run, args.gate_id,
                                     decode(Path(args.evidence_file).read_text()), config)
    gate = beads._queue(run).show(args.gate_id)
    binding = task_blockers.gates.metadata(gate)['harness_gate']
    if binding.get('blocker', {}).get('run_id') != run['id']:
        raise BeadsError('Blocker belongs to another run')
    return {'request': task_blockers.expected(binding), 'binding': binding}
