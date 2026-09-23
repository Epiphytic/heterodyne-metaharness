"""One deterministic entry point for models, shell users, gateway and systemd."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid

from .beads import Beads, BeadsError
from .delivery import deliver, health
from .marmot import Marmot
from .store import Store, home_path
from .supervisor import Supervisor, boot_id
from .tmux import Tmux


def load_config(home):
    path = Path(home) / 'workstreams' / 'harness-config.json'
    return json.loads(path.read_text()) if path.exists() else {}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home', default=str(home_path()))
    sub = p.add_subparsers(dest='command', required=True)
    from .approvals import configure_cli
    configure_cli(sub)
    from .operator_asks import configure_cli as configure_asks
    configure_asks(sub)
    from .transition_cli import configure as configure_notices
    configure_notices(sub)
    from .review_cli import configure as configure_reviews
    configure_reviews(sub)
    from .manager_resolution import configure as configure_manager
    configure_manager(sub)
    maintenance = sub.add_parser('maintenance')
    maintenance.add_argument('--file', required=True)
    maintenance.add_argument('--title', required=True)
    maintenance.add_argument('--key', required=True)
    start = sub.add_parser('start')
    start.add_argument('name'); start.add_argument('repo'); start.add_argument('agent')
    start.add_argument('--prompt', default=''); start.add_argument('--file')
    start.add_argument('--group'); start.add_argument('--agent-config', default='{}')
    start.add_argument('--parent-session')
    start.add_argument('--no-persistent', dest='persistent', action='store_false')
    for name in ('status', 'resume', 'identities', 'inbox'):
        cmd = sub.add_parser(name); cmd.add_argument('name')
    for name in ('close', 'stop'):
        cmd = sub.add_parser(name); cmd.add_argument('name')
        cmd.add_argument('--consent-file', required=True)
        cmd.add_argument('--force', action='store_true')
    send = sub.add_parser('send'); send.add_argument('name')
    send.add_argument('--target', choices=['worker', 'manager', 'secondary'], default='manager')
    send.add_argument('--text'); send.add_argument('--file'); send.add_argument('--message-id')
    react = sub.add_parser('react'); react.add_argument('name')
    react.add_argument('--message-id', required=True)
    react.add_argument('--emoji', required=True); react.add_argument('--key', required=True)
    event = sub.add_parser('event'); event.add_argument('name')
    event.add_argument('--state', choices=['working', 'blocked', 'completed', 'failed', 'awaiting_resume'], required=True)
    event.add_argument('--text', required=True); event.add_argument('--event-id')
    event.add_argument('--operator-ask', action='store_true')
    bind = sub.add_parser('bind-group'); bind.add_argument('name'); bind.add_argument('group')
    route = sub.add_parser('route')
    route.add_argument('--group', required=True); route.add_argument('--message-id', required=True)
    route.add_argument('--sender', required=True)
    task = sub.add_parser('task')
    task.add_argument('name')
    task.add_argument('--workstream')
    actions = task.add_subparsers(dest='task_action', required=True)
    from .queue_cli import configure
    configure(actions)
    from .task_gates import configure as configure_gates
    configure_gates(actions)
    from .blocker_cli import configure as configure_blockers
    configure_blockers(actions)
    for name in ('context', 'pause', 'resume'):
        actions.add_parser(name)
    for name in ('show', 'bind', 'claim', 'close', 'worktree'):
        action = actions.add_parser(name)
        action.add_argument('issue_id')
        if name == 'close':
            action.add_argument('--evidence-file', required=True)
        if name == 'worktree':
            action.add_argument('repository')
    update = actions.add_parser('update')
    update.add_argument('issue_id')
    update.add_argument('--file', required=True)
    update.add_argument('--key', required=True)
    update.add_argument('--authorization-file', required=True)
    stage = actions.add_parser('stage')
    stage.add_argument('issue_id')
    stage.add_argument('--stage', required=True, choices=('committed','tested','pr-open','merged','final-tested','deployed','close-ready','live-verified','integrated','investigated','recommended','changed','verified'))
    stage.add_argument('--evidence-file', required=True)
    reconcile = actions.add_parser('reconcile')
    reconcile.add_argument('issue_id', nargs='?')
    create = actions.add_parser('create')
    create.add_argument('--title', required=True)
    create.add_argument('--file', required=True)
    create.add_argument('--key', required=True)
    create.add_argument('--kind', choices=('task', 'research', 'review', 'brainstorm'), default='task')
    create.add_argument('--metadata', default='{}')
    create.add_argument('--approval-id')
    create.add_argument('--at-top', action='store_true')
    delivery = actions.add_parser('delivery')
    delivery.add_argument('--file', required=True)
    formula = actions.add_parser('formula')
    formula.add_argument('--file', required=True)
    recover = actions.add_parser('recover')
    recover.add_argument('--evidence-file', required=True)
    for name in ('list', 'doctor', 'daemon', 'tick', 'deliver', 'import-legacy'):
        sub.add_parser(name)
    return p


def text_input(args):
    if args.file:
        return Path(args.file).read_text()
    return getattr(args, 'text', None) or getattr(args, 'prompt', '')


def import_legacy(store):
    imported = []
    for path in sorted(store.root.glob('*/manifest.json')):
        m = json.loads(path.read_text())
        name = path.parent.name
        if any(r['name'] == name for r in store.runs()):
            continue
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())))
        status = path.parent / 'status.json'
        observation = json.loads(status.read_text()) if status.exists() else {}
        run = dict(id=identity, name=name, agent={'claude-code': 'claude'}.get(m.get('agent'), m.get('agent', 'codex')),
                   repo=m.get('repo'), workdir=m.get('repo') or str(path.parent),
                   group_id=m.get('marmot_group'), state='archived', legacy=True,
                   created_at=time.time(), boot_id=None, recovery_count=0, config={},
                   tmux_session='ws-' + identity, prompt='', prompt_state='not_replayed',
                   resume_required=True, legacy_manifest=str(path), legacy_data=m,
                   observation={'summary': observation.get('detail', 'Legacy state unverified'), 'at': observation.get('ts')})
        with store.db:
            store.save(run)
            if run['repo'] and run['group_id'] and not store.project(run['repo']):
                store.save_project(run['repo'], run['group_id'], 'ready', {'legacy': True})
        store.checkpoint(run)
        imported.append(name)
    return {'imported': imported, 'note': 'Historical records retained, not automatically restarted. Resume explicitly after inspecting.'}


def delivery_loop(home, config, stop):
    store = Store(home)
    active = config
    transport = Marmot(active.get('marmot', {}))
    while not stop.is_set():
        try:
            try:
                fresh = load_config(home)
                if fresh != active:
                    replacement = Marmot(fresh.get('marmot', {}))
                    active, transport = fresh, replacement
            except Exception as exc:
                print(json.dumps({'component': 'delivery-config', 'error': str(exc)}), file=sys.stderr, flush=True)
            deliver(store, transport, ops_group=active.get('ops_group'))
        except Exception as exc:
            print(json.dumps({'component': 'delivery', 'error': str(exc)}), file=sys.stderr, flush=True)
        stop.wait(1)
    store.db.close()


def daemon(store, supervisor, config):
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    thread = threading.Thread(target=delivery_loop, args=(store.root.parent, config, stop), daemon=True)
    thread.start()
    while not stop.is_set():
        try:
            with store.lock(blocking=False):
                try:
                    fresh = load_config(store.root.parent)
                    if fresh != supervisor.config:
                        beads = Beads(fresh.get('beads', {}))
                        transport = Marmot(fresh.get('marmot', {}))
                        supervisor.config, supervisor.beads, supervisor.transport = fresh, beads, transport
                except Exception as exc:
                    print(json.dumps({'component': 'supervisor-config', 'error': str(exc)}), file=sys.stderr, flush=True)
                supervisor.tick()
        except BlockingIOError:
            pass  # Another CLI transaction owns the state; next tick reconciles it.
        except Exception as exc:
            print(json.dumps({'component': 'supervisor', 'error': str(exc)}), file=sys.stderr, flush=True)
        stop.wait(10)
    # Last observations survive a clean shutdown; boot-ID reconciliation also handles power loss.
    with store.lock():
        for run in store.runs():
            store.checkpoint(run)
    thread.join(timeout=2)


def task_dispatch(args, store, supervisor, config):
    run = store.get(args.name)
    beads = Beads(config.get('beads', {}))
    state = run.setdefault('beads', {})
    if args.workstream:
        previous = state.get('workstream') or beads.config.get('workstream')
        if state.get('issue_id') and previous != args.workstream:
            raise BeadsError('Cannot reroute a run with a bound task')
        state['workstream'] = args.workstream
    action = args.task_action
    if action in ('ready', 'prioritize', 'dep', 'drop-everything'):
        from .queue_cli import dispatch as queue_dispatch
        return queue_dispatch(args, store, supervisor, beads, run)
    if action == 'blocker':
        from .blocker_cli import dispatch as blocker_dispatch
        return blocker_dispatch(args, store, beads, run, config, supervisor)
    if action == 'gate':
        from .task_gates import dispatch as gate_dispatch
        return gate_dispatch(args, store, beads, run)
    if action == 'formula':
        from .task_formulas import create
        return create(store, supervisor, beads, run, json.loads(Path(args.file).read_text()))
    if action == 'delivery':
        from .task_delivery import create
        return create(store, supervisor, beads, run, json.loads(Path(args.file).read_text()))
    if action == 'create':
        from .tasks import admit
        return admit(store, supervisor, beads, run, args.title, Path(args.file).read_text(), args.key,
                              kind=args.kind, metadata=json.loads(args.metadata), approval_id=args.approval_id,
                              at_top=args.at_top)
    elif action == 'update':
        from .tasks import addendum
        return addendum(store, beads, run, args.issue_id, Path(args.file).read_text(), args.key, Path(args.authorization_file).read_text())
    elif action == 'reconcile':
        issue_id = args.issue_id or state.get('issue_id')
        if not issue_id:
            raise BeadsError('No bound task to reconcile')
        result = beads.show(run, issue_id)
    elif action == 'context':
        result = {'context': beads.context(run)}
    elif action == 'close':
        from .tasks import assert_close_current
        from .task_stages import close_ready
        issue = beads.show(run, args.issue_id)
        from .task_gates import check
        if issue.get('status') != 'closed':
            from .task_blockers import check_policies
            check_policies(issue, beads.config)
            check(beads._queue(run), issue)
        from .task_review import checkout
        from .task_delivery import contract, close_step
        if contract(issue) and issue.get('status') != 'closed':
            close_step(beads._queue(run), checkout(run, args.issue_id), issue)
        else:
            close_ready(checkout(run, args.issue_id), issue)
        assert_close_current(store, run, issue)
        if supervisor.config.get('reviews', {}).get('enabled') is True:
            from .reviews import assert_close
            assert_close(store, run, issue)
        result = beads.close(run, args.issue_id, args.evidence_file)
    elif action == 'stage':
        from .task_stages import record
        from .task_review import checkout
        result = record(store, beads, checkout(run, args.issue_id), args.issue_id, args.stage, json.loads(Path(args.evidence_file).read_text()))
    elif action == 'worktree':
        result = beads.worktree(run, args.issue_id, args.repository)
    elif action == 'recover':
        evidence = Path(args.evidence_file).read_text().strip()
        if not evidence:
            raise BeadsError('Recovery evidence must describe interrupted effects and verified ownership')
        if state.get('issue_id'):
            queue = beads._queue(run)
            issue = queue.show(state['issue_id'])
            if issue.get('assignee') not in ('', None, queue.worker):
                raise BeadsError('Recovery cannot take another worker claim')
        beads.pause(run)
        run['resume_required'] = False
        state.update(recovery_required=False, pickup_enabled=False,
                     recovery_evidence=evidence[:16000], reconciled_at=time.time())
        result = {'reconciled': True, 'pickup_enabled': False,
                  'note': 'Task ownership retained; resume pickup or send work only after inspecting effects.'}
    elif action in ('pause', 'resume'):
        result = getattr(beads, action)(run)
        state['pickup_enabled'] = action == 'resume'
    elif action in ('show', 'bind', 'claim'):
        from .task_workspace import assign
        result = assign(args, store, supervisor, beads, run)
    else:
        result = beads.ready(run)
    supervisor.persist(run)
    if action in ('bind', 'claim', 'reconcile', 'show', 'stage'):
        from .tasks import observe
        observe(store, result)
        if action == 'claim':
            from .tasks import record_claim_baseline
            record_claim_baseline(store, run, result)
            from .transitions import handoff
            handoff(store, run, result)
    elif action == 'close':
        from .tasks import observe
        observe(store, result['issue'])
    return result


def dispatch(args, store, supervisor, config):
    command = args.command
    if command == 'manager-task':
        from .manager_resolution import dispatch as manager_dispatch
        return manager_dispatch(args, store, supervisor, config)
    if command == 'notice':
        from .transition_cli import dispatch as notice_dispatch
        return notice_dispatch(args, store, Beads(config.get('beads', {})))
    if command == 'approvals':
        from .approvals import handle_cli
        return handle_cli(args, store, Beads(config.get('beads', {})))
    if command == 'ask':
        from .operator_asks import handle_cli
        return handle_cli(args, store)
    if command == 'maintenance':
        from .maintenance import enqueue_task
        return enqueue_task(store, supervisor, config, args.title, Path(args.file).read_text(), args.key)
    if command == 'task':
        return task_dispatch(args, store, supervisor, config)
    if command == 'review':
        from .review_cli import dispatch as review_dispatch
        return review_dispatch(args, store, supervisor.beads, config)
    if command == 'start':
        return supervisor.start(args.name, args.repo, args.agent, text_input(args), args.group, json.loads(args.agent_config), args.parent_session, persistent=args.persistent)
    if command == 'status':
        return store.get(args.name)
    if command == 'identities':
        run = store.get(args.name)
        return {'aliases': [dict(r) for r in store.db.execute('SELECT * FROM identities WHERE run_id=?', (run['id'],))],
                'lineage': [dict(r) for r in store.db.execute('SELECT * FROM native_sessions WHERE run_id=? ORDER BY first_seen', (run['id'],))]}
    if command == 'inbox':
        run = store.get(args.name)
        return [dict(r) for r in store.db.execute('SELECT * FROM inbox WHERE run_id=? ORDER BY created_at', (run['id'],))]
    if command == 'list':
        return [{k:r.get(k) for k in ('name','id','agent','state','group_id','legacy','native_session_id')} for r in store.runs()]
    if command == 'doctor':
        return {'boot_id': boot_id(), 'delivery': health(store),
                'runs': len(store.runs()), 'state_db': str(store.root / 'harness.sqlite3'),
                'inbound_spool': len(list((store.root / 'incoming-spool').glob('*.json')))}
    if command == 'import-legacy':
        return import_legacy(store)
    if command == 'tick':
        supervisor.tick(); return {'ok': True}
    if command == 'deliver':
        deliver(store, supervisor.transport, ops_group=config.get('ops_group')); return health(store)
    run = store.get(args.name)
    if command == 'react':
        from .reactions import enqueue
        return enqueue(store, run, supervisor.transport.account_id, args.message_id, args.emoji, args.key)
    if command in ('close', 'stop'):
        from .run_closure import terminate
        terminate(supervisor, run, command, json.loads(Path(args.consent_file).read_text()), args.force)
    elif command == 'resume':
        if run.get('legacy'):
            run['legacy'] = False
            run['repo'] = str(Path(run['repo']).resolve()) if run.get('repo') and Path(run['repo']).is_dir() else None
        from .workspace import prepare
        prepare(run, store.root)
        supervisor.ensure_group(run)
        supervisor.recover(run, 'explicit conversation recovery')
    elif command == 'send':
        supervisor.submit(run, text_input(args), args.target, args.message_id)
    elif command == 'event':
        from .store import TERMINAL
        if run['state'] not in TERMINAL:
            run['state'] = 'idle' if args.state == 'completed' else args.state
        run['task_state'] = args.state
        run['task_summary'] = args.text
        supervisor.report(run, args.state, args.text, args.event_id,
                          operator_ask=getattr(args, 'operator_ask', False))
        supervisor.persist(run)
    elif command == 'bind-group':
        run['group_id'] = args.group
        supervisor.ensure_group(run)
        supervisor.persist(run)
    return run


def main(argv=None):
    args = parser().parse_args(argv)
    os.environ['HERMES_HOME'] = str(Path(args.home).expanduser())
    if args.command == 'task' and args.task_action == 'progress':
        from .task_progress import read
        print(json.dumps(read(args.home, args.name, load_config(args.home) if args.live else None), indent=2))
        return 0
    if args.command == 'task' and args.task_action in ('show', 'context'):
        from .tasks import readonly
        result = readonly(args.home, args.name, getattr(args, 'issue_id', None))
        if args.task_action == 'context':
            result['context'] = 'Cached task data, not execution authority: ' + json.dumps(result['snapshot'])
        print(json.dumps(result, indent=2))
        return 0
    store = Store(args.home)
    config = load_config(args.home)
    if args.command == 'route':
        from .routing import enqueue
        handled = enqueue(args.group, args.message_id, sys.stdin.read(), args.sender, db_path=store.root / 'harness.sqlite3')
        print(json.dumps({'handled': handled})); return 0
    transport = Marmot(config.get('marmot', {})) if args.command in ('start', 'resume', 'bind-group', 'daemon', 'deliver', 'react') else None
    supervisor = Supervisor(store, Tmux(config.get('tmux_socket', 'hermes-workstreams')), transport, config)
    if args.command == 'daemon':
        daemon(store, supervisor, config); return 0
    with store.lock():
        result = dispatch(args, store, supervisor, config)
    print(json.dumps(result, indent=2))
    if args.command in ('start', 'resume') and result.get('state') == 'blocked':
        return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        raise SystemExit(1)
