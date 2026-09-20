import unittest
from unittest.mock import patch
from tests import test_supervisor as fixture
from harness import status, task_hooks
from harness.delivery import deliver


class StatusTest(unittest.TestCase):
    def setUp(self):
        fixture.SupervisorTest.setUp(self)
        timer = patch('harness.store.time.time', side_effect=lambda: self.now)
        timer.start()
        self.addCleanup(timer.stop)
    tearDown = fixture.SupervisorTest.tearDown
    new_supervisor = fixture.SupervisorTest.new_supervisor
    start = fixture.SupervisorTest.start
    events = fixture.SupervisorTest.events

    def test_churning_pane_and_turn_do_not_produce_repeated_heartbeats(self):
        from harness.supervisor import REPORT_INTERVAL
        run = self.start()
        run.update(state='active', native_turn_state='working', task_summary='Running tests')
        with patch.object(self.supervisor, 'report', wraps=self.supervisor.report) as report:
            self.supervisor.heartbeat(run)
            for tick in range(10, REPORT_INTERVAL, 10):
                self.now += 10
                run['pane_digest'] = str(tick)
                run['native_turn_key'] = ['native', str(tick)]
                self.supervisor.heartbeat(run)
            self.assertEqual(report.call_count, 1)
        self.assertEqual(len(self.events('progress')), 1)
        self.assertFalse(self.events('duplicate_status_error'))

    def test_alternating_content_is_suppressed_across_recent_window(self):
        run = self.start()
        with self.store.db:
            self.store.event(run, 'working', 'Alpha', 'window-a')
            self.store.event(run, 'working', 'Beta', 'window-b')
        with self.assertLogs('harness.status', level='ERROR'):
            with self.store.db:
                self.store.event(run, 'working', 'Alpha', 'window-a-again')
        self.assertIsNone(self.store.db.execute('SELECT id FROM outbox WHERE id=?',
                                              ('window-a-again',)).fetchone())

    def test_fingerprint_tracks_semantics_not_display_wrappers(self):
        run = self.start()
        before = status.fingerprint(run, 'Status: active; native turn: working. Same')
        run.update(pane_digest='changed', native_turn_key=['new', 'turn'])
        self.assertEqual(before, status.fingerprint(run, 'Same Observed at 2026-09-20T12:00:00Z'))
        run['beads'] = {'issue_id': 'new-task'}
        self.assertNotEqual(before, status.fingerprint(run, 'Same'))

    def test_idle_suppression_changed_text_and_native_transition(self):
        run = self.start()
        run.update(native_turn_state='idle', state='idle', task_summary='Waiting for review')
        self.supervisor.persist(run)
        self.supervisor.heartbeat(run)
        count = len(self.events('progress'))
        self.now += 1000
        self.supervisor.heartbeat(run)
        self.assertEqual(len(self.events('progress')), count)
        self.assertFalse(self.events('duplicate_status_error'))
        self.now += 300
        run['task_summary'] = 'Waiting for approval'
        self.supervisor.heartbeat(run)
        self.assertEqual(len(self.events('progress')), count + 1)
        status.activity(run, 'native', 'new', 'working', self.now)
        self.supervisor.heartbeat(run)
        self.assertEqual(len(self.events('progress')), count + 2)
        self.assertEqual(run['state'], 'active')

    def test_duplicate_production_is_error_pending_and_delivered(self):
        run = self.start()
        with self.store.db:
            self.store.event(run, 'working', 'same report', 'first')
        with self.assertLogs('harness.status', level='ERROR'):
            with self.store.db:self.store.event(run, 'working', 'same report', 'second')
        self.assertIsNone(self.store.db.execute('SELECT id FROM outbox WHERE id="second"').fetchone())
        while self.store.db.execute('SELECT 1 FROM outbox WHERE delivered_at IS NULL').fetchone():
            deliver(self.store, self.transport, now=9999999999)
        with self.assertNoLogs('harness.status', level='ERROR'):
            with self.store.db:self.store.event(run, 'working', 'same report', 'third')
        self.assertEqual(len(self.events('duplicate_status_error')), 2)
        with self.store.db:self.store.event(run, 'working', 'same report', 'third')
        self.assertEqual(len(self.events('duplicate_status_error')), 2)
        run['native_turn_state'] = 'idle'
        with self.store.db:self.store.event(run, 'working', 'same report', 'changed')
        self.assertIsNotNone(self.store.db.execute('SELECT id FROM outbox WHERE id="changed"').fetchone())

    def test_ongoing_timestamp_cannot_defeat_dedup_and_change_is_prompt(self):
        run = self.start()
        run.update(state='active', native_turn_state='working', task_summary='Running tests')
        self.supervisor.heartbeat(run)
        count = len(self.events('progress'))
        for elapsed in (240, 480):
            self.now += elapsed
            with self.assertLogs('harness.status', level='ERROR'):
                self.supervisor.heartbeat(run)
            self.assertEqual(len(self.events('progress')), count)
        self.assertEqual(len(self.events('duplicate_status_error')), 2)
        run['task_summary'] = 'Tests passed; preparing review'
        self.now += 1
        self.supervisor.heartbeat(run)
        self.assertEqual(len(self.events('progress')), count + 1)
        texts = [row[0] for row in self.store.db.execute('SELECT text FROM outbox')]
        self.assertFalse(any('Observed at' in text for text in texts))

    def test_exact_stop_updates_run_and_stale_turn_cannot_overwrite(self):
        run = self.start()
        task_hooks.activity(self.store, run['id']+':worker', 'native', 'new', 'UserPromptSubmit')
        task_hooks.activity(self.store, run['id']+':worker', 'native', 'old', 'Stop')
        self.assertEqual(self.store.get(run['id'])['native_turn_state'], 'working')
        task_hooks.activity(self.store, run['id']+':worker', 'native', 'new', 'Stop')
        run = self.store.get(run['id'])
        self.assertEqual(run['state'], 'idle')
        self.assertFalse(status.activity(run, 'native', 'old', 'working', 1))
        self.assertEqual(run['state'], 'idle')

    def test_static_pane_normalized_persisted_and_changed_output_delivers(self):
        run = self.start()
        run.update(state='active', native_turn_state='working', task_summary='Running')
        pane = self.tmux.panes[run['id']]
        pane['text'] = 'Output\nWorking (1m 2s • esc to interrupt)'
        self.supervisor.observe(run)
        self.supervisor.heartbeat(run)
        count = len(self.events('progress'))
        digest = run['pane_digest']
        pane['text'] = 'Output\nWorking (2m 3s • esc to interrupt)'
        self.supervisor.observe(run)
        self.assertTrue(status.is_idle(run, self.store))
        self.assertEqual(run['native_turn_state'], 'working')  # reporting does not authorize input
        with self.assertLogs('harness.status', level='ERROR'):
            self.supervisor.heartbeat(run)
        self.assertEqual(len(self.events('progress')), count)
        restored = self.store.get(run['id'])
        self.assertEqual(restored['pane_digest'], digest)
        self.assertEqual(restored['pane_unchanged_ticks'], 1)
        self.now += 1000
        self.supervisor.heartbeat(restored)
        self.assertEqual(len(self.events('progress')), count)
        pane['text'] += '\nNew output'
        self.supervisor.observe(restored)
        self.supervisor.heartbeat(restored)
        self.assertEqual(len(self.events('progress')), count)  # pixels alone cannot resend unchanged text
        self.assertEqual(restored['pane_unchanged_ticks'], 0)
        restored['task_summary'] = 'New test result ready'
        self.supervisor.heartbeat(restored)
        self.assertEqual(len(self.events('progress')), count + 1)

    def test_static_pane_never_hides_approval_or_authorizes_recovery(self):
        run = self.start()
        status.observe_pane(run, 'Would you like to run this command?')
        status.observe_pane(run, 'Would you like to run this command?')
        run['observed_state'] = 'awaiting_approval'
        self.assertFalse(status.is_idle(run, self.store))
        run['observed_state'] = 'working'
        run['resume_required'] = True
        self.assertFalse(status.is_idle(run, self.store))

    def test_native_abort_updates_state_without_task_completion(self):
        run = self.start()
        run['beads'] = {'issue_id': 'active-task'}
        with patch('harness.lifecycle.observe_events', side_effect=[[
            {'kind':'turn_aborted','id':'abort','summary':'interrupted','turn_id':'t','at':1000}], []]):
            self.supervisor.lifecycle(run)
        self.assertEqual(run['state'], 'idle')
        self.assertEqual(run['beads']['issue_id'], 'active-task')
        self.assertFalse(self.events('completed'))
