"""Shared lifecycle state machine; agent-specific behavior lives in adapters."""
import json
import os
from pathlib import Path
import re
import time
import uuid

from .agents import Adapter
from .store import TERMINAL
from .workspace import prepare
from .marmot import MarmotError, UncertainOutcome
from .beads import Beads
from .capabilities import apply_capabilities

REPORT_INTERVAL = 240


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


class Supervisor:
    def __init__(self, store, tmux, transport, config=None, boot=None, clock=time.time):
        self.store, self.tmux, self.transport = store, tmux, transport
        self.config = config or {}
        self.boot = boot or boot_id()
        self.clock = clock
        self.beads = Beads(self.config.get('beads', {}))

    def persist(self, run):
        with self.store.db:
            self.store.save(run)
        self.store.checkpoint(run)

    def report(self, run, kind, text, key=None, operator_ask=False):
        with self.store.db:
            self.store.event(run, kind, text, key, group=run.get('group_id') or self.config.get('ops_group'),
                             operator_ask=operator_ask)
            self.store.save(run)

    def start(self, name, repo, agent, prompt='', group=None, agent_config=None, parent_session=None):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name):
            raise ValueError('name must be 1-64 letters, numbers, underscores or hyphens')
        agent = Adapter(agent).name
        repo = str(Path(repo).expanduser().resolve()) if repo and repo != '-' else None
        existing = [r for r in self.store.runs() if r['name'] == name]
        if existing:
            run = existing[0]
            if run.get('repo') != repo or run['agent'] != agent:
                raise ValueError('name already belongs to a different repo/agent')
            if run['state'] == 'blocked' and run.get('prompt_state') == 'pending':
                return self.activate(run)
            return run  # Retrying start never resubmits a task or replaces identity.
        for run in self.store.runs():
            if repo and run.get('repo') == repo and run['state'] not in TERMINAL:
                raise ValueError(f"Repo already owned by {run['name']}; steer that workstream")
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, str(self.store.root.resolve()) + ':' + name))
        run = dict(id=identity, name=name, repo=repo, agent=agent, state='starting',
                   workdir=str(self.store.root / 'runs' / identity / 'checkout'),
                   tmux_session='ws-' + identity, created_at=self.clock(),
                   boot_id=self.boot, prompt=prompt, prompt_state='pending',
                   config=agent_config or {}, group_id=group, last_report_at=self.clock(),
                   resume_required=False, recovery_count=0)
        run['session_names'] = {'worker': f'workstream-{name}-worker',
                                'manager': f'workstream-{name}-manager'}
        run['parent_hermes_session_id'] = parent_session or os.environ.get('HERMES_SESSION_ID')
        if self.beads.enabled and agent in ('codex', 'claude'):
            run['beads'] = {'workstream': self.config['beads'].get('workstream') or name.lower(),
                            'pickup_enabled': False}
        if agent in ('claude', 'hermes'):
            run['config']['session_name'] = run['session_names']['worker']
        self.persist(run)
        return self.activate(run)

    def ensure_group(self, run):
        project_key = run.get('repo') or 'workstream:' + run['name']
        project = self.store.project(project_key)
        group = run.get('group_id') or (project or {}).get('group_id')
        if group:
            owners = [r['name'] for r in self.store.runs()
                      if r['id'] != run['id'] and r.get('group_id') == group
                      and r['state'] not in TERMINAL and not r.get('legacy')]
            if owners:
                raise ValueError(f'Channel already owned by {owners[0]}')
            self.transport.group_info(group)
        else:
            if project and project['status'] in ('creating', 'uncertain'):
                raise RuntimeError('group creation outcome uncertain; bind the existing group before retrying')
            intent = str(uuid.uuid4())
            with self.store.db:
                self.store.save_project(project_key, None, 'creating', {'request_id': intent})
            # The server has no group-create idempotency: leave intent on ANY uncertain exit.
            try:
                group = self.transport.create_group(run['name'] + ' workstream', intent)
            except UncertainOutcome:
                with self.store.db:
                    self.store.save_project(project_key, None, 'uncertain', {'request_id': intent})
                raise
            except MarmotError:
                with self.store.db:
                    self.store.save_project(project_key, None, 'rejected', {'request_id': intent})
                raise
        run['group_id'] = group
        with self.store.db:
            self.store.save_project(project_key, group, 'ready', {'name': run['name']})
            self.store.save(run)

    def manager_run(self, run):
        if 'manager' not in run:
            path = self.store.root / 'runs' / run['id'] / 'manager'
            path.mkdir(parents=True, exist_ok=True)
            run['manager'] = dict(id=run['id'] + '-manager', agent='hermes',
                tmux_session=run['tmux_session'] + '-manager', workdir=str(path),
                created_at=self.clock(), config=dict(self.config.get('manager', {}),
                    session_name='workstream-' + run['name'] + '-manager'))
            (path / 'AGENTS.md').write_text(
                f"You manage workstream {run['name']} (stable ID {run['id']}).\n"
                f"Before steering, read {self.store.root / 'runs' / run['id'] / 'checkpoint.json'}.\n"
                'The checkpoint is authoritative across compaction and reboot. '
                'If resume_required is true, report the interruption and wait for explicit steering. '
                'Never automatically replay commands or start another worker. '
                'Use workstream send NAME --target worker --file PATH to steer and '
                'workstream event NAME --state completed --text EVIDENCE to record verified completion.\n')
        return run['manager']

    def manager_prompt(self, run):
        checkpoint = self.store.checkpoint(run)
        command = self.config.get('cli', str(Path(__file__).resolve().parents[1] / 'bin/workstream'))
        return (
            f'You are the dedicated manager for workstream {run["name"]} ({run["id"]}). '
            f'The coding agent is {run["agent"]}; it owns {run["workdir"]}. '
            f'Read the durable checkpoint {checkpoint} before acting and after compaction. '
            f'Use {command} status {run["name"]} to inspect state; '
            f'{command} send {run["name"]} --target worker --file PATH to steer the coder; '
            f'{command} event {run["name"]} --state working|blocked|completed --text TEXT '
            'to publish verified progress/results. Preserve native approvals; do not type approval keys automatically. '
            'Do not edit the coding worktree yourself, start another worker, or poll/sleep. '
            'The deterministic supervisor handles five-minute reporting and will deliver new channel messages. '
            'All workstream discussion belongs in its Marmot group; event sends there durably. '
            'A completed turn is not necessarily a completed task. Verify evidence before reporting completed. '
            'Answer workflow sequencing questions yourself when the active spec already authorizes the answer; '
            'do not invent approvals or defer routine in-policy decisions to the operator. '
            'Follow spec/changes.md and spec/worktrees.md for task boundaries. '
            f'Classify command asks with {command} approvals {run["id"]} ask --file JSON '
            '(key, issue_id, argv, cwd, evidence_ref). This durably routes uncertain or dangerous asks '
            'to the manager inbox and bound Marmot outbox; answer each with an actionable decision. '
            'A harness allow result never authorizes typing native approval keys or changing native permission rules. '
            'On recovery, describe the interruption using the checkpoint and await explicit steering; '
            'do not replay commands, restart tests or resume implementation automatically. '
            f'Use {command} task {run["name"]} for durable Beads task passing and ownership; '
            'bind and claim explicitly before queued execution, close only with evidence. '
            'Use workstream-recall and semble search to find prior work before implementation. '
            f'Initial task, for context: {run.get("prompt", "")}'
        )

    def launch(self, run, target, recovering=False):
        adapter = Adapter(target['agent'])
        info = self.tmux.inspect(target)
        if info['alive']:
            target['pane_id'] = info['pane_id']
            return
        if not info.get('missing'):
            self.tmux.stop(target)
        target.pop('pane_id', None)
        target['launched_at'] = self.clock()
        config = target.setdefault('config', {})
        target['capabilities'] = apply_capabilities(target, config)
        if target is run and self.beads.enabled and run['agent'] in ('codex', 'claude'):
            config.setdefault('env', {}).update(self.beads.environment(run))
        if target['agent'] == 'claude' and self.config.get('native_hook_command'):
            from .hook_config import claude_args
            config = target.setdefault('config', {})
            config['extra_args'] = claude_args(config.get('extra_args', config.get('args', [])), self.config['native_hook_command'])
        argv = adapter.argv(target, recover=recovering)
        # Commit identity and launch intent before the externally visible side effect.
        target['launch_intent'] = self.boot
        self.persist(run)
        target['pane_id'] = self.tmux.launch(target, argv)
        target['launch_intent'] = None
        self.persist(run)
        if target['agent'] != 'command' and not self.tmux.inspect(target)['alive']:
            raise RuntimeError(f"{target['agent']} exited during launch; inspect retained pane")

    def activate(self, run):
        try:
            self.ensure_group(run)
            prepare(run, self.store.root)
            if run['agent'] == 'command' and run['config'].get('working_directory'):
                run['workdir'] = str(Path(run['config']['working_directory']).resolve(strict=True))
            self.persist(run)
            manager = self.manager_run(run)
            manager['launch_prompt'] = self.manager_prompt(run)
            self.launch(run, manager)
            run['launch_prompt'] = run['prompt'] if run['prompt_state'] == 'pending' else ''
            run['prompt_state'] = 'launching'
            self.launch(run, run)
            run['prompt_state'] = 'submitted'
            run['state'] = 'active'
            self.report(run, 'started', 'Manager and coding session launched. Progress reports at most five minutes apart.')
        except Exception as exc:
            run['state'] = 'blocked'
            run['error'] = str(exc)
            self.report(run, 'blocked', f'Startup blocked: {exc}')
        self.persist(run)
        return run

    def recover(self, run, reason='host reboot'):
        if (run.get('boot_id') == self.boot and run['state'] == 'awaiting_resume'
                and self.tmux.inspect(run)['alive']
                and run.get('manager') and self.tmux.inspect(run['manager'])['alive']):
            return
        previous = run.get('observation', {}).get('summary', 'No terminal checkpoint captured yet.')
        old_boot = run.get('boot_id')
        run.update(boot_id=self.boot, state='interrupted', resume_required=True,
                   interrupted_at=self.clock(), interruption_reason=reason)
        if self.beads.enabled and run['agent'] in ('codex', 'claude'):
            run.setdefault('beads', {}).update(recovery_required=True, pickup_enabled=False)
        with self.store.db:
            self.store.db.execute("UPDATE inbox SET state='held' WHERE run_id=? AND state='pending'", (run['id'],))
        run['recovery_count'] = run.get('recovery_count', 0) + 1
        run['launch_prompt'] = ''
        run['prompt_state'] = 'not_replayed'
        self.report(run, 'interrupted',
            f'Session was killed/interrupted ({reason}). '
            f"Task: {run.get('task_summary') or run.get('prompt', '')[:240]}\n"
            f"Native conversation: {run.get('native_session_id') or 'not recorded'}. "
            f'Last observed: {previous}\n'
            'Restoring the conversation only; interrupted commands will not be rerun.',
            f"{run['id']}:interrupted:{old_boot}:{run['recovery_count']}")
        try:
            if run['agent'] == 'command':
                # Arbitrary commands have no safe conversation to restore.
                manager = self.manager_run(run)
                manager['launch_prompt'] = ''
                self.launch(run, manager, recovering=bool(manager.get('launched_at')))
                run['state'] = 'interrupted'
                self.persist(run)
                return
            native = run.get('native_session_id')
            self.launch(run, run, recovering=bool(native))
            manager = self.manager_run(run)
            manager['launch_prompt'] = ''
            try:
                self.launch(run, manager, recovering=bool(manager.get('launched_at')))
            except Exception:
                # A missing named manager is not replayed; make the failure visible.
                raise RuntimeError('Could not restore manager; saved checkpoint retained')
            run['state'] = 'awaiting_resume'
            self.report(run, 'recovered',
                ('Saved conversation restored' if native else 'Native session ID unavailable; blank coding session restored')
                + '. Awaiting steering; no task or command was replayed.')
        except Exception as exc:
            run['state'] = 'blocked'
            run['error'] = str(exc)
            self.report(run, 'blocked', f'Recovery blocked: {exc}')
        self.persist(run)

    def observe(self, run):
        from .status import observe_pane
        info = self.tmux.inspect(run)
        if not info['alive']:
            run['pane_stopped'] = False
            if run['state'] in ('starting', 'blocked', 'interrupted', 'failed'):
                return
            if run['agent'] == 'command' and not info.get('missing'):
                if info.get('exit_code') is None:
                    return  # tmux can publish pane_dead before the wait status.
                run['state'] = 'completed' if info.get('exit_code') == 0 else 'failed'
                self.report(run, run['state'], f"Command exited with status {info.get('exit_code')}. {info['text'][-1000:]}")
            else:
                run['state'] = 'interrupted'
                self.report(run, 'interrupted', 'Coding agent exited. Checkpoint preserved; explicit resume is available.')
            return
        if run['agent'] != 'command':
            observe_pane(run, info['text'])
        from .terminal_observation import retain
        retain(run, info, self.clock())
        observation = Adapter(run['agent']).observe(run, info['text'])
        run['pane_id'] = info['pane_id']
        if observation.get('native_session_id'):
            run['native_session_id'] = observation['native_session_id']
        run['observation'] = dict(observation, at=self.clock(), pane_alive=True)
        from .permission_relay import observe as relay_permission
        approval_relay = relay_permission(self.store, run, info)
        if observation['state'] == 'awaiting_approval' and run.get('observed_state') != 'awaiting_approval':
            self.report(run, 'blocked', 'Coding agent appears to need approval. Native permission prompt preserved.',
                        operator_ask=approval_relay is None)
        run['observed_state'] = observation['state']
        self.observe_manager(run)

    def observe_manager(self, run):
        manager = run.get('manager')
        if manager:
            manager_info = self.tmux.inspect(manager)
            if manager_info['alive']:
                from .terminal_observation import retain
                retain(manager, manager_info, self.clock())
                observation = Adapter('hermes').observe(manager, manager_info['text'])
                native = observation.get('native_session_id')
                if native:
                    manager['native_session_id'] = native
                manager['observation'] = observation
                manager['pane_id'] = manager_info['pane_id']
                from .permission_relay import observe as relay_permission
                approval_relay = relay_permission(self.store, dict(
                    run, native_session_id=manager.get('native_session_id'),
                    pane_id=manager['pane_id']), manager_info)
                if observation['state'] == 'awaiting_approval' and manager.get('observed_state') != 'awaiting_approval':
                    self.report(run, 'blocked', 'Manager needs native approval through Hermes.\n' + observation['summary'],
                                operator_ask=approval_relay is None)
                manager['observed_state'] = observation['state']
            elif not run.get('manager_missing'):
                run['state'] = 'blocked'
                self.report(run, 'blocked', 'Manager exited; coding session remains owned and reporting. Use resume to restore manager.', operator_ask=True)
            run['manager_missing'] = not manager_info['alive']

    def submit(self, run, text, target='manager', message_id=None):
        destination = run if target == 'worker' else self.manager_run(run)
        key = message_id or str(uuid.uuid4())
        with self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)',
                                 (key, run['id'], text, self.clock(), 'pending', target))
        row = self.store.db.execute('SELECT * FROM inbox WHERE id=?', (key,)).fetchone()
        if row['run_id'] != run['id'] or row['text'] != text or row['target'] != target:
            raise ValueError('message identity already belongs to different input')
        if row['state'] != 'pending':
            return
        if destination.get('observed_state') == 'awaiting_approval':
            raise ValueError('Native approval pending; resolve through Hermes before steering')
        with self.store.db:
            self.store.db.execute("UPDATE inbox SET state='sending' WHERE id=?", (key,))
        try:
            self.tmux.send(destination, row['text'], submit=True)
        except Exception as exc:
            with self.store.db:
                self.store.db.execute("UPDATE inbox SET state='uncertain' WHERE id=?", (key,))
            self.report(run, 'blocked', f'Input submission uncertain ({key}); inspect before retrying: {exc}')
            raise
        with self.store.db:
            self.store.db.execute("UPDATE inbox SET state='submitted' WHERE id=?", (key,))
            if key.startswith(('continuation:', 'babysitter-idle:')):
                run.setdefault('continuation', {})['submitted_turn'] = run.get('native_turn_key')
                run['continuation']['sent_at'] = self.clock()
                self.store.save(run)
                self.store.event(run, 'queue_continuation_sent', key, key + ':sent')
        if target == 'worker':
            run.update(state='active', resume_required=False)
        self.persist(run)

    def drain_inbox(self, run):
        rows = self.store.db.execute("SELECT * FROM inbox WHERE run_id=? AND state='pending' ORDER BY CASE WHEN id LIKE 'task-update:%' THEN 2 WHEN id LIKE 'continuation:%' OR id LIKE 'babysitter-idle:%' THEN 1 ELSE 0 END, created_at LIMIT 1", (run['id'],)).fetchall()
        for row in rows:
            if not self.inbox_ready(run, row):
                continue
            self.submit(run, row['text'], target=row['target'], message_id=row['id'])

    def inbox_ready(self, run, row):
        from .continuation import safe, awaiting_start
        if row['id'].startswith('babysitter-idle:'):
            from .babysitter_queue import nudge_key
            if row['id'] != nudge_key(run):
                with self.store.db:
                    self.store.db.execute("UPDATE inbox SET state='superseded' WHERE id=?", (row['id'],))
                return False
            return (safe(run) and self.beads.enabled
                    and self.clock() - run.get('continuation', {}).get('sent_at', 0) >= 60
                    and not (self.beads._queue(run).state / 'paused').exists())
        if row['id'].startswith('continuation:'):
            state = run.get('continuation', {})
            return (row['id'] == 'continuation:' + state.get('boundary', '')
                    and state.get('selected_issue') == run.get('beads', {}).get('issue_id')
                    and safe(run))
        if not row['id'].startswith('task-update:'):
            return True
        from .tasks import current_notification
        if not current_notification(self.store, run, row):
            with self.store.db:
                self.store.db.execute("UPDATE inbox SET state='superseded' WHERE id=?", (row['id'],))
            return False
        return (not awaiting_start(run) and run.get('native_turn_state') == 'idle'
                and not run.get('resume_required') and not run.get('beads', {}).get('recovery_required')
                and run.get('observed_state') != 'awaiting_approval')

    def recover_intents(self, run):
        targets = [run] + ([run['manager']] if run.get('manager') else [])
        uncertain = [target for target in targets if target.get('launch_intent')]
        if uncertain:
            for target in uncertain:
                info = self.tmux.inspect(target)
                target['launch_intent'] = None
                if info['alive']:
                    target['pane_id'] = info['pane_id']
            run['state'] = 'interrupted'
            run['resume_required'] = True
            self.report(run, 'interrupted', 'Supervisor stopped during launch. Existing owned panes retained; original prompt will not be replayed. Inspect and resume explicitly.')
        rows = self.store.db.execute("SELECT id FROM inbox WHERE run_id=? AND state='sending'", (run['id'],)).fetchall()
        for row in rows:
            with self.store.db:
                self.store.db.execute("UPDATE inbox SET state='uncertain' WHERE id=?", (row['id'],))
            self.report(run, 'blocked', f"Submission {row['id']} interrupted; inspect before retrying. Input was not replayed.")

    def tick_run(self, run):
        if run['state'] in TERMINAL or run.get('legacy'):
            return
        self.recover_intents(run)
        if run.get('boot_id') != self.boot:
            self.recover(run)
        else:
            self.observe(run)
            self.recover_dead_pane(run)
        self.lifecycle(run)
        from .continuation import advance
        advance(self, run)
        self.drain_inbox(run)
        self.persist(run)

    def recover_dead_pane(self, run):
        """Restore a known conversation once; existing recovery holds own failures."""
        if (run['agent'] not in ('codex', 'claude', 'hermes')
                or run['state'] not in ('interrupted', 'failed')
                or run.get('resume_required') or run.get('beads', {}).get('recovery_required')
                or not run.get('native_session_id')
                or run.get('observed_state') in ('awaiting_approval', 'awaiting_question')):
            return
        info = self.tmux.inspect(run)
        if info.get('alive') or info.get('missing') or not info.get('dead'):
            return
        now = self.clock()
        retry = run.setdefault('dead_pane_recovery', {'attempts': 0, 'next_at': now + 10})
        if now < retry['next_at']:
            self.persist(run)
            return
        retry['attempts'] += 1
        retry['next_at'] = now + min(300, 10 * 2 ** min(retry['attempts'], 5))
        self.persist(run)
        self.recover(run, reason='owned worker pane exited')

    def heartbeat(self, run):
        from .status import fingerprint, is_idle
        if run['state'] in TERMINAL or run.get('legacy'):
            return
        now = self.clock()
        idle = is_idle(run, self.store)
        summary = run.get('task_summary') or run.get('observation', {}).get('summary', run.get('error', 'Awaiting first observation.'))
        text = f"Status: {'idle' if idle else run['state']}; native turn: {run.get('native_turn_state', 'unknown')}. {summary}"
        digest = fingerprint(run, text)
        changed = run.get('heartbeat_digest') != digest
        # Idle ticks do not produce notices. This is distinct from detecting
        # faulty duplicate production in Store.event; unchanged idle is normal.
        transition = run.get('heartbeat_idle') is not None and run['heartbeat_idle'] != idle
        if idle and not changed and not transition:
            return
        if changed or transition or now - run.get('last_report_at', 0) >= REPORT_INTERVAL:
            run['last_report_at'] = now
            run['heartbeat_digest'] = digest
            run['heartbeat_idle'] = idle
            # Observation timestamps must never manufacture a content change.
            # Store.event error-logs and suppresses unchanged active reports.
            self.report(run, 'progress', text)
        self.persist(run)

    def lifecycle(self, run):
        from .lifecycle import observe_events
        from .continuation import native_event
        events = observe_events(run)
        manager_events = observe_events(run['manager']) if run.get('manager') else []
        with self.store.db:
            for event in manager_events:
                if event['kind'] == 'turn_completed':
                    self.store.event(run, 'manager_reply', event['summary'], event['id'])
                elif event['kind'] == 'compacted':
                    self.store.event(run, 'manager_compacted', 'Manager compacted; stable workstream and channel mapping retained.', event['id'])
            for event in events:
                kind, summary, key = event['kind'], event['summary'], event['id']
                native_event(run, event, self.clock())
                if kind == 'turn_aborted':
                    self.store.event(run, kind, summary, key)
                if kind == 'turn_completed':
                    self.store.event(run, kind, 'Coding agent finished a turn:\n' + summary, key)
                    if run.get('resume_required'):
                        continue  # Recovery reports history; it never asks a manager to continue it.
                    text = ('Coding agent finished a turn. Inspect this result and the checkpoint; '
                            'verify whether the task is complete, needs steering, or needs human input. '
                            'Use workstream event to publish your assessment.\n' + summary)
                    self.store.db.execute('''INSERT OR IGNORE INTO inbox
                      (id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)''',
                      (key + ':manager', run['id'], text, self.clock(), 'pending', 'manager'))
                elif kind == 'approval':
                    self.store.event(run, kind, 'Native approval required: ' + summary, key)
                elif kind == 'compacted':
                    run['last_compacted_at'] = self.clock()
                    self.store.event(run, kind, 'Coding context compacted; session ownership and reporting retained.', key)
            self.store.save(run)

    def tick(self):
        from .routing import drain_spool
        counts = drain_spool(self.store.root / 'harness.sqlite3')
        if counts['errors'] or counts['unowned']:
            with self.store.db:
                self.store.event({'id': 'supervisor', 'name': 'supervisor'}, 'routing_recovery',
                    f"Inbound recovery needs attention: {counts['errors']} errors, {counts['unowned']} unowned messages retained.",
                    f'spool:{int(self.clock() // REPORT_INTERVAL)}', group=self.config.get('ops_group'))
        for run in self.store.runs():
            try:
                self.tick_run(run)
            except Exception as exc:
                run['error'] = str(exc)
                self.report(run, 'supervisor_error', f'Supervision error: {exc}',
                            f"{run['id']}:error:{int(self.clock() // REPORT_INTERVAL)}")
            finally:
                self.heartbeat(run)

    def stop(self, run):
        # State is durable before killing anything: restart will not resurrect this run.
        run['state'] = 'stopped'
        self.report(run, 'stopped', 'Stopped. Worktree, branch, group and conversation history preserved.', run['id'] + ':stopped')
        self.tmux.stop(run)
        if run.get('manager'):
            self.tmux.stop(run['manager'])
        self.persist(run)
