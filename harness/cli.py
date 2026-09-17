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
    maintenance = sub.add_parser('maintenance')
    maintenance.add_argument('--file', required=True)
    maintenance.add_argument('--title', required=True)
    maintenance.add_argument('--key', required=True)
    start = sub.add_parser('start')
    start.add_argument('name'); start.add_argument('repo'); start.add_argument('agent')
    start.add_argument('--prompt', default=''); start.add_argument('--file')
    start.add_argument('--group'); start.add_argument('--agent-config', default='{}')
    start.add_argument('--parent-session')
    for name in ('status', 'stop', 'resume', 'identities', 'inbox'):
        cmd = sub.add_parser(name); cmd.add_argument('name')
    send = sub.add_parser('send'); send.add_argument('name')
    send.add_argument('--target', choices=['worker', 'manager'], default='manager')
    send.add_argument('--text'); send.add_argument('--file'); send.add_argument('--message-id')
    event = sub.add_parser('event'); event.add_argument('name')
    event.add_argument('--state', choices=['working', 'blocked', 'completed', 'failed', 'awaiting_resume'], required=True)
    event.add_argument('--text', required=True); event.add_argument('--event-id')
    bind = sub.add_parser('bind-group'); bind.add_argument('name'); bind.add_argument('group')
    route = sub.add_parser('route')
    route.add_argument('--group', required=True); route.add_argument('--message-id', required=True)
    route.add_argument('--sender', required=True)
    task = sub.add_parser('task')
    task.add_argument('name')
    task.add_argument('--workstream')
    actions = task.add_subparsers(dest='task_action', required=True)
    for name in ('ready', 'context', 'pause', 'resume'):
        actions.add_parser(name)
    for name in ('show', 'bind', 'claim', 'close', 'worktree'):
        action = actions.add_parser(name)
        action.add_argument('issue_id')
        if name == 'close':
            action.add_argument('--evidence-file', required=True)
        if name == 'worktree':
            action.add_argument('repository')
    create = actions.add_parser('create')
    create.add_argument('--title', required=True)
    create.add_argument('--file', required=True)
    create.add_argument('--key', required=True)
    create.add_argument('--kind', choices=('task', 'research', 'review', 'brainstorm'), default='task')
    create.add_argument('--metadata', default='{}')
    create.add_argument('--approval-id')
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
    transport = Marmot(config.get('marmot', {}))
    while not stop.is_set():
        try:
            deliver(store, transport, ops_group=config.get('ops_group'))
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
    if action == 'create':
        result = beads.create(run, args.title, Path(args.file).read_text(), key=args.key,
                              kind=args.kind, metadata=json.loads(args.metadata), approval_id=args.approval_id)
    elif action == 'context':
        result = {'context': beads.context(run)}
    elif action == 'close':
        result = beads.close(run, args.issue_id, args.evidence_file)
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
        result = getattr(beads, action)(run, args.issue_id)
    else:
        result = beads.ready(run)
    supervisor.persist(run)
    return result


def dispatch(args, store, supervisor, config):
    command = args.command
    if command == 'maintenance':
        from .maintenance import enqueue_task
        return enqueue_task(store, supervisor, config, args.title, Path(args.file).read_text(), args.key)
    if command == 'task':
        return task_dispatch(args, store, supervisor, config)
    if command == 'start':
        return supervisor.start(args.name, args.repo, args.agent, text_input(args), args.group, json.loads(args.agent_config), args.parent_session)
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
    if command == 'stop':
        supervisor.stop(run)
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
        run['state'] = 'idle' if args.state == 'completed' and run.get('persistent') else args.state
        run['task_state'] = args.state
        run['task_summary'] = args.text
        supervisor.report(run, args.state, args.text, args.event_id)
        supervisor.persist(run)
    elif command == 'bind-group':
        run['group_id'] = args.group
        supervisor.ensure_group(run)
        supervisor.persist(run)
    return run


def main(argv=None):
    args = parser().parse_args(argv)
    os.environ['HERMES_HOME'] = str(Path(args.home).expanduser())
    store = Store(args.home)
    config = load_config(args.home)
    if args.command == 'route':
        from .routing import enqueue
        handled = enqueue(args.group, args.message_id, sys.stdin.read(), args.sender, db_path=store.root / 'harness.sqlite3')
        print(json.dumps({'handled': handled})); return 0
    transport = Marmot(config.get('marmot', {})) if args.command in ('start', 'resume', 'bind-group', 'daemon', 'deliver') else None
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
