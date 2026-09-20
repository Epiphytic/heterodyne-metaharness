"""Queue-management facade wiring; spec/queue-order.md."""
from . import task_order
from .tasks import observe, record_claim_baseline


def configure(actions):
    ready = actions.add_parser('ready')
    ready.add_argument('--all', action='store_true')
    priority = actions.add_parser('prioritize')
    priority.add_argument('issues', nargs='+')
    priority.add_argument('--issuer', required=True)
    interrupt = actions.add_parser('drop-everything')
    interrupt.add_argument('issue_id')
    interrupt.add_argument('--issuer', required=True)
    interrupt.add_argument('--reason', required=True)
    dep = actions.add_parser('dep')
    dep.add_argument('operation', choices=('add', 'remove'))
    dep.add_argument('issue_id')
    dep.add_argument('blocker')
    dep.add_argument('--issuer', required=True)


def dispatch(args, store, supervisor, beads, run):
    supervisor.persist(run)  # Retain explicit enrollment before observing fresh task projections.
    action = args.task_action
    if action == 'ready':
        return task_order.graph(beads, run) if args.all else beads.ready(run)
    if action == 'prioritize':
        issues = task_order.prioritize(beads._queue(run), run, args.issues, args.issuer)
        for issue in issues:
            observe(store, issue)
        return issues
    if action == 'dep':
        issue = task_order.dependency(beads._queue(run), run, args.operation,
                                      args.issue_id, args.blocker, args.issuer)
    else:
        from .task_interrupt import drop
        issue = drop(store, supervisor, beads, run, args.issue_id, args.issuer, args.reason)
    supervisor.persist(run)
    observe(store, issue)
    if action == 'drop-everything':
        record_claim_baseline(store, run, issue)
    return issue
