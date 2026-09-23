"""Lifecycle contract tests with durable SQLite and no live agents/messages."""
from pathlib import Path
import tempfile
import shutil
import subprocess
import sys
import time
import uuid
import unittest

from harness.delivery import deliver
from harness.store import Store
from harness.tmux import Tmux
from harness.supervisor import REPORT_INTERVAL, Supervisor


class FakeTmux:
    def __init__(self):
        self.panes = {}
        self.launches = []
        self.stops = []
        self.sent = []
        self.fail_manager = False

    def inspect(self, run):
        return dict(self.panes.get(run['id'], dict(alive=False, dead=True, missing=True,
                                                  exit_code=None, text='')))

    def launch(self, run, argv):
        if self.fail_manager and run['id'].endswith('-manager'):
            raise RuntimeError('manager unavailable')
        self.launches.append((run['id'], argv[:]))
        pane = '%' + str(len(self.launches))
        self.panes[run['id']] = dict(alive=True, dead=False, missing=False,
                                    pane_id=pane, exit_code=None, text='>')
        return pane

    def stop(self, run):
        self.stops.append(run['id'])
        self.panes.pop(run['id'], None)

    def send(self, run, text, submit=False):
        self.sent.append((run['id'], text, submit))


class FakeTransport:
    def __init__(self):
        self.created = []
        self.messages = []
        self.fail_send = False

    def group_info(self, group):
        return {'id': group}

    def create_group(self, name, intent):
        self.created.append((name, intent))
        return 'group-1'

    def send(self, group, text, identity):
        self.messages.append((group, text, identity))
        if self.fail_send:
            raise RuntimeError('Marmot error response')
        return {'type': 'send_message_result'}


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.tmux = FakeTmux()
        self.transport = FakeTransport()
        self.now = 1000.0
        self.supervisor = self.new_supervisor()

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def new_supervisor(self, boot='boot-a'):
        return Supervisor(self.store, self.tmux, self.transport, boot=boot, clock=lambda: self.now)

    def start(self, agent='claude', prompt='implement original task'):
        config = {'argv': ['/bin/false']} if agent == 'command' else None
        return self.supervisor.start('demo', None, agent, prompt, agent_config=config)

    def events(self, kind):
        return self.store.db.execute('SELECT * FROM events WHERE kind=?', (kind,)).fetchall()

    def test_terminal_buffers_and_long_approval_survive_supervisor_restart(self):
        run = self.start('codex')
        run['native_session_id'] = 'worker-native'
        text = ('Would you like to run the following command?\n'
                + 'command reason\n' * 300 + 'Press enter to confirm or esc to cancel')
        self.tmux.panes[run['id']]['text'] = text
        self.supervisor.observe(run)
        self.supervisor.persist(run)
        restored = self.store.get(run['id'])
        self.new_supervisor().observe(restored)
        self.assertFalse(restored['terminal_buffer']['changed'])
        self.assertEqual(restored['observation']['summary'], text)
        self.assertEqual(self.store.db.execute('SELECT evidence FROM permission_relays').fetchone()[0], text)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM manager_tasks').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM outbox WHERE id LIKE 'permission:%'").fetchone()[0], 0)
        self.assertEqual(self.tmux.sent, [])

    def test_manager_modal_surfaces_complete_evidence(self):
        run = self.start('codex')
        manager = run['manager']
        manager['native_session_id'] = 'manager-native'
        text = ('Dangerous Command\n' + 'full command argument\n' * 60
                + 'Allow once\nDeny\n↑/↓ to select, Enter to confirm')
        self.tmux.panes[manager['id']]['text'] = text
        self.supervisor.observe_manager(run)
        evidence = self.store.db.execute('SELECT native_id,evidence FROM permission_relays').fetchone()
        self.assertEqual(evidence['native_id'], 'manager-native')
        self.assertEqual(evidence['evidence'], text)
        self.assertEqual(manager['terminal_buffer']['text'], text)
        self.assertEqual(self.tmux.sent, [])

    def test_same_boot_dead_pane_recovers_exact_session_after_restart(self):
        run = self.start('codex')
        run['native_session_id'] = '11111111-1111-4111-8111-111111111111'
        native = run['native_session_id']
        self.tmux.panes[run['id']].update(alive=False, dead=True, exit_code=0)
        self.supervisor.tick_run(run)
        self.assertEqual(len(self.tmux.launches), 2)
        self.now += 11
        self.new_supervisor().tick_run(self.store.get(run['id']))
        saved = self.store.get(run['id'])
        self.assertEqual(saved['state'], 'awaiting_resume')
        self.assertTrue(saved['resume_required'])
        self.assertEqual(saved['native_session_id'], native)
        self.assertIn(native, self.tmux.launches[-1][1])
        self.assertEqual(saved['prompt_state'], 'not_replayed')
        self.assertEqual(self.tmux.sent, [])
        self.new_supervisor().tick_run(saved)
        self.assertEqual(len(self.tmux.launches), 3)

    def test_dead_pane_failed_restore_holds_without_launch_retry(self):
        from unittest.mock import patch
        run = self.start('codex')
        run.update(native_session_id='11111111-1111-4111-8111-111111111111', state='failed')
        self.tmux.panes[run['id']].update(alive=False, dead=True)
        self.supervisor.recover_dead_pane(run)
        self.now += 11
        with patch.object(self.supervisor, 'launch', side_effect=RuntimeError('failed')) as launch:
            self.supervisor.recover_dead_pane(run)
            self.now += 1000
            self.supervisor.recover_dead_pane(run)
            launch.assert_called_once()
        self.assertTrue(run['resume_required'])
        self.assertEqual(run['state'], 'blocked')
        self.assertEqual(run['dead_pane_recovery']['attempts'], 1)

    def test_dead_pane_recovery_preserves_holds_and_missing_panes(self):
        run = self.start('codex')
        run['state'] = 'interrupted'
        self.tmux.panes[run['id']].update(alive=False, dead=True)
        for changes in ({'resume_required': True}, {'beads': {'recovery_required': True}},
                        {'observed_state': 'awaiting_approval'}, {'native_session_id': None}):
            candidate = dict(run, **changes)
            self.supervisor.recover_dead_pane(candidate)
            self.assertNotIn('dead_pane_recovery', candidate)
        self.tmux.panes[run['id']]['missing'] = True
        self.supervisor.recover_dead_pane(run)
        self.assertNotIn('dead_pane_recovery', run)
        self.assertEqual(len(self.tmux.launches), 2)

    def test_start_retry_preserves_identity_prompt_and_group(self):
        first = self.start()
        second = self.start(prompt='replacement must not overwrite')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(second['prompt'], 'implement original task')
        self.assertEqual(len(self.tmux.launches), 2)
        self.assertEqual(len(self.transport.created), 1)
        with self.assertRaises(ValueError):
            self.start(agent='codex')

    def test_beads_identity_and_context_policy_survive_reboot(self):
        config = {'beads': {'enabled': True}}
        self.supervisor = Supervisor(self.store, self.tmux, self.transport, config, boot='boot-a')
        run = self.start()
        self.assertEqual(run['state'], 'active')
        self.assertEqual(run['config']['env']['BTQ_SESSION_ID'], run['id'])
        self.assertEqual(run['config']['env']['BTQ_WS'], 'demo')
        self.assertEqual(run['config']['env']['CLAUDE_AUTOCOMPACT_PCT_OVERRIDE'], '50')
        self.assertFalse(run['beads']['pickup_enabled'])
        self.tmux.panes.clear()
        reboot = Supervisor(self.store, self.tmux, self.transport, config, boot='boot-b')
        reboot.recover(run)
        self.assertTrue(run['beads']['recovery_required'])
        self.assertFalse(run['beads']['pickup_enabled'])
        self.assertEqual(run['config']['env']['BTQ_SESSION_ID'], run['id'])
        self.assertEqual(run['state'], 'awaiting_resume')

    def test_reboot_reports_checkpoint_resumes_exact_id_without_task(self):
        run = self.start()
        original_id = run['native_session_id']
        run['observation'] = {'summary': 'Editing parser, tests not yet run.'}
        self.supervisor.persist(run)
        self.tmux.panes.clear()
        self.new_supervisor('boot-b').tick()
        saved = self.store.get('demo')
        self.assertEqual(saved['state'], 'awaiting_resume')
        self.assertEqual(saved['prompt_state'], 'not_replayed')
        self.assertTrue(saved['resume_required'])
        resumed = self.tmux.launches[2:]
        self.assertEqual(len(resumed), 2)
        self.assertIn(original_id, resumed[0][1])
        for _, argv in resumed:
            self.assertNotIn('implement original task', argv)
            self.assertNotIn('--last', argv)
        self.assertIn('Editing parser', self.events('interrupted')[0]['text'])
        self.new_supervisor('boot-b').tick()
        self.assertEqual(len(self.tmux.launches), 4)

    def test_unknown_and_approval_reports_do_not_repeat_at_deadline(self):
        run = self.start()
        for text in ('>', 'Do you want to proceed?'):
            self.tmux.panes[run['id']]['text'] = text
            self.now += REPORT_INTERVAL
            self.new_supervisor().tick()
        self.assertEqual(len(self.events('progress')), 1)
        self.assertEqual(self.store.get('demo')['last_report_at'], self.now - REPORT_INTERVAL)
        self.now += REPORT_INTERVAL - 1
        self.new_supervisor().tick()
        self.assertEqual(len(self.events('progress')), 1)
        self.now += 1
        self.new_supervisor().tick()
        self.assertEqual(len(self.events('progress')), 1)
        self.assertEqual(len(self.events('duplicate_status_error')), 0)
        self.assertLessEqual(REPORT_INTERVAL, 300)

    def test_command_reboot_does_not_replay_workload(self):
        run = self.start(agent='command')
        before = len([entry for entry in self.tmux.launches if entry[0] == run['id']])
        self.tmux.panes.clear()
        self.new_supervisor('boot-b').tick()
        after = len([entry for entry in self.tmux.launches if entry[0] == run['id']])
        self.assertEqual(before, after)
        self.assertTrue(self.events('interrupted'))

    def test_stop_preserves_owned_and_unrelated_files(self):
        run = self.start()
        owned = Path(run['workdir']) / 'uncommitted.txt'
        owned.write_text('valuable edits')
        unrelated = Path(self.temp.name) / 'unrelated-worktree'
        unrelated.mkdir()
        (unrelated / 'keep').write_text('unrelated')
        self.supervisor.stop(run, dict(run_id=run['id'], action='stop', approved_by='operator',
            response='Stop now', evidence_ref='fixture:operator-stop', force=False, open_beads=[]))
        self.assertEqual(owned.read_text(), 'valuable edits')
        self.assertEqual((unrelated / 'keep').read_text(), 'unrelated')
        self.assertEqual(set(self.tmux.stops), {run['id'], run['manager']['id']})
        self.new_supervisor('boot-b').tick()
        self.assertEqual(len(self.tmux.launches), 2)

    def test_outbox_error_retries_same_message_identity(self):
        self.start()
        self.transport.fail_send = True
        deliver(self.store, self.transport, now=2000)
        row = self.store.db.execute('SELECT * FROM outbox').fetchone()
        self.assertIsNone(row['delivered_at'])
        self.assertEqual(row['attempts'], 1)
        identity = row['id']
        self.transport.fail_send = False
        deliver(self.store, self.transport, now=2029)
        self.assertEqual(len(self.transport.messages), 1)
        deliver(self.store, self.transport, now=2030)
        self.assertEqual([m[2] for m in self.transport.messages], [identity, identity])
        self.assertIsNotNone(self.store.db.execute('SELECT delivered_at FROM outbox').fetchone()[0])

    def test_missing_manager_reports_once_and_keeps_ownership(self):
        run = self.start()
        self.tmux.panes.pop(run['manager']['id'])
        self.new_supervisor().tick()
        self.new_supervisor().tick()
        reports = [r for r in self.events('blocked') if 'Manager exited' in r['text']]
        self.assertEqual(len(reports), 1)
        self.assertTrue(self.store.get('demo')['manager_missing'])
        self.assertTrue(self.tmux.inspect(run)['alive'])

    def test_manager_restore_failure_is_visible(self):
        self.start()
        self.tmux.panes.clear()
        self.tmux.fail_manager = True
        self.new_supervisor('boot-b').tick()
        self.assertEqual(self.store.get('demo')['state'], 'blocked')
        self.assertTrue(any('manager' in row['text'] for row in self.events('blocked')))

    def test_legacy_manifest_is_never_automatically_replayed(self):
        run = self.start()
        run['legacy'] = True
        run['boot_id'] = 'old-boot'
        self.supervisor.persist(run)
        self.tmux.panes.clear()
        self.new_supervisor('boot-b').tick()
        self.assertEqual(len(self.tmux.launches), 2)

    def test_uncertain_launch_after_supervisor_crash_does_not_silently_stall(self):
        run = self.start()
        self.tmux.panes.clear()
        run.update(state='starting', prompt_state='launching', launch_intent='boot-a')
        self.supervisor.persist(run)
        self.new_supervisor().tick()
        saved = self.store.get('demo')
        self.assertNotEqual(saved['state'], 'starting')
        self.assertTrue(any(self.events(kind) for kind in ('interrupted', 'blocked')))
        for _, argv in self.tmux.launches[2:]:
            self.assertNotIn('implement original task', argv)

    def test_failed_command_emits_terminal_event_once(self):
        run = self.start(agent='command')
        self.tmux.panes[run['id']].update(alive=False, dead=True, exit_code=3)
        self.new_supervisor().tick()
        self.new_supervisor().tick()
        self.assertEqual(len(self.events('failed')), 1)

    def test_inbox_id_cannot_redirect_another_runs_pending_message(self):
        first = self.start()
        second = self.supervisor.start('other', None, 'claude', 'other task')
        with self.store.db:
            self.store.db.execute('INSERT INTO inbox (id,run_id,text,created_at,state) VALUES (?,?,?,?,?)',
                ('message-1', first['id'], 'original persisted text', self.now, 'pending'))
        try:
            self.supervisor.submit(second, 'injected replacement', message_id='message-1')
        except ValueError:
            pass
        self.assertFalse(self.tmux.sent)

    def test_crash_after_launch_adopts_existing_owned_pane_without_replay(self):
        run = self.start()
        run.update(state='starting', prompt_state='launching', launch_intent='boot-a')
        run.pop('pane_id', None)
        self.supervisor.persist(run)
        count = len(self.tmux.launches)
        self.new_supervisor().tick()
        saved = self.store.get('demo')
        self.assertEqual(len(self.tmux.launches), count)
        self.assertIsNotNone(saved.get('pane_id'))
        self.assertFalse(saved.get('launch_intent'))
        self.assertNotEqual(saved['state'], 'starting')

    def test_pending_worker_target_survives_database_and_supervisor_restart(self):
        run = self.start()
        with self.store.db:
            self.store.db.execute('''INSERT INTO inbox
                (id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)''',
                ('worker-msg', run['id'], 'review only', self.now, 'pending', 'worker'))
        self.store.db.close()
        self.store = Store(self.temp.name)
        self.new_supervisor().tick()
        self.assertEqual(self.tmux.sent, [(run['id'], 'review only', True)])
        self.assertEqual(self.store.db.execute('SELECT state FROM inbox WHERE id=?',
                                              ('worker-msg',)).fetchone()[0], 'submitted')
        self.new_supervisor().tick()
        self.assertEqual(len(self.tmux.sent), 1)

    def test_crash_during_input_becomes_visible_uncertainty_without_replay(self):
        run = self.start()
        with self.store.db:
            self.store.db.execute('''INSERT INTO inbox
                (id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)''',
                ('uncertain-msg', run['id'], 'possibly submitted', self.now, 'sending', 'worker'))
        self.new_supervisor().tick()
        self.assertFalse(self.tmux.sent)
        self.assertEqual(self.store.db.execute('SELECT state FROM inbox WHERE id=?',
                                              ('uncertain-msg',)).fetchone()[0], 'uncertain')
        self.assertTrue(any('uncertain-msg' in row['text'] for row in self.events('blocked')))

    def test_approval_wait_preserves_pending_input_and_reporting(self):
        run = self.start()
        self.tmux.panes[run['id']]['text'] = 'Do you want to proceed?'
        with self.store.db:
            self.store.db.execute('''INSERT INTO inbox
                (id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)''',
                ('approval-msg', run['id'], 'continue review', self.now, 'pending', 'worker'))
        self.now += REPORT_INTERVAL
        self.new_supervisor().tick()
        self.assertFalse(self.tmux.sent)
        self.assertEqual(len(self.events('progress')), 0)

    @unittest.skipUnless(shutil.which('tmux'), 'tmux required')
    def test_real_tmux_command_exit_keeps_manager_and_reports_once(self):
        executable = Path(self.temp.name) / 'fake-manager'
        executable.write_text('#!' + sys.executable + '\nimport time\nprint("manager ready", flush=True)\ntime.sleep(30)\n')
        executable.chmod(0o700)
        socket = 'hws-supervisor-test-' + uuid.uuid4().hex
        self.tmux = Tmux(socket)
        self.supervisor = Supervisor(self.store, self.tmux, self.transport,
            config={'manager': {'executable': str(executable)}},
            boot='boot-a', clock=lambda: self.now)
        try:
            run = self.supervisor.start('real-command', None, 'command',
                agent_config={'argv': [sys.executable, '-c', 'print("command complete")']})
            self.assertEqual(run['state'], 'active')
            for _ in range(50):
                self.supervisor.tick()
                state = self.store.get('real-command')['state']
                self.assertNotEqual(state, 'failed', self.tmux.inspect(run))
                if state == 'idle':
                    break
                time.sleep(.02)
            self.assertEqual(self.store.get('real-command')['state'], 'idle', self.tmux.inspect(run))
            self.assertTrue(self.tmux.inspect(run['manager'])['alive'])
            self.assertEqual(len(self.events('completed')), 1)
            self.supervisor.tick()
            self.assertEqual(len(self.events('completed')), 1)
            self.supervisor.stop(self.store.get('real-command'), dict(run_id=run['id'], action='stop',
                approved_by='operator', response='Stop now', evidence_ref='fixture:stop', force=False, open_beads=[]))
            self.assertTrue(self.tmux.inspect(run)['missing'])
            self.assertTrue(self.tmux.inspect(run['manager'])['missing'])
        finally:
            subprocess.run(['tmux', '-L', socket, 'kill-server'], capture_output=True)

    def test_reboot_does_not_execute_input_queued_before_interruption(self):
        run = self.start()
        with self.store.db:
            self.store.db.execute('''INSERT INTO inbox
                (id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)''',
                ('pre-reboot', run['id'], 'run the deployment now', self.now, 'pending', 'worker'))
        self.tmux.panes.clear()
        self.new_supervisor('boot-b').tick()
        self.assertFalse(self.tmux.sent)
        self.assertTrue(self.store.get('demo')['resume_required'])
        self.assertIn(self.store.db.execute('SELECT state FROM inbox WHERE id=?',
                                           ('pre-reboot',)).fetchone()[0], ('pending', 'held'))
