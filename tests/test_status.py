import unittest
from unittest.mock import patch
from tests import test_supervisor as fixture
from harness import status, task_hooks
from harness.delivery import deliver


class StatusTest(unittest.TestCase):
    setUp = fixture.SupervisorTest.setUp
    tearDown = fixture.SupervisorTest.tearDown
    new_supervisor = fixture.SupervisorTest.new_supervisor
    start = fixture.SupervisorTest.start
    events = fixture.SupervisorTest.events

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
        with self.assertLogs('harness.status', level='ERROR'):
            with self.store.db:self.store.event(run, 'working', 'same report', 'third')
        self.assertEqual(len(self.events('duplicate_status_error')), 2)
        with self.store.db:self.store.event(run, 'working', 'same report', 'third')
        self.assertEqual(len(self.events('duplicate_status_error')), 2)
        run['native_turn_state'] = 'idle'
        with self.store.db:self.store.event(run, 'working', 'same report', 'changed')
        self.assertIsNotNone(self.store.db.execute('SELECT id FROM outbox WHERE id="changed"').fetchone())

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

    def test_native_abort_updates_state_without_task_completion(self):
        run = self.start()
        run['beads'] = {'issue_id': 'active-task'}
        with patch('harness.lifecycle.observe_events', side_effect=[[
            {'kind':'turn_aborted','id':'abort','summary':'interrupted','turn_id':'t','at':1000}], []]):
            self.supervisor.lifecycle(run)
        self.assertEqual(run['state'], 'idle')
        self.assertEqual(run['beads']['issue_id'], 'active-task')
        self.assertFalse(self.events('completed'))
