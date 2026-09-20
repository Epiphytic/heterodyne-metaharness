"""Production-path regressions; no live services or model workloads."""
import tempfile
import unittest
from unittest.mock import patch
from harness.store import Store
from harness.status import normalized_text


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(lambda: self.store.db.close())
        self.run = dict(id='run', name='test', group_id='group', state='active', native_turn_state='working')
        self.now = 1000
        timer = patch('harness.store.time.time', side_effect=lambda: self.now)
        timer.start()
        self.addCleanup(timer.stop)

    def emit(self, identity, text, kind='progress', group=None):
        with self.store.db:
            self.store.event(self.run, kind, text, identity, group=group)
        return bool(self.store.db.execute('SELECT 1 FROM outbox WHERE id=?', (identity,)).fetchone())

    def test_alternation_window_survives_restart_pending_and_delivery(self):
        self.assertTrue(self.emit('a', 'Testing variant A'))
        self.now += 300
        self.assertTrue(self.emit('b', 'Testing variant B'))
        self.store.db.execute('UPDATE outbox SET delivered_at=? WHERE id=?', (self.now, 'a'))
        self.store.db.commit()
        self.store.db.close()
        self.store = Store(self.temp.name)
        with patch('harness.status.logging.getLogger') as logger:
            for i in range(30):
                self.now += 10
                self.run['pane_digest'] = str(i)
                self.run['native_turn_key'] = ['native', str(i)]
                self.assertFalse(self.emit(str(i), 'Testing variant ' + ('A' if i % 2 else 'B')))
            self.assertEqual(logger.return_value.error.call_count, 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 2)
        # Failed transport/pending B retains its original outbox identity.
        self.assertIsNone(self.store.db.execute('SELECT delivered_at FROM outbox WHERE id="b"').fetchone()[0])

    def test_rate_limit_boundary_and_no_sliding_suppression(self):
        self.assertTrue(self.emit('a', 'Running test one'))
        with patch('harness.status.logging.getLogger'):
            for i in range(1, 30):
                self.now = 1000 + i * 10
                self.assertFalse(self.emit(str(i), 'Still running test ' + str(i)))
        self.now = 1300
        self.assertTrue(self.emit('next', 'Still running test 30'))

    def test_real_transitions_asks_and_task_identity_are_prompt(self):
        self.assertTrue(self.emit('a', 'Working'))
        self.run['native_turn_state'] = 'idle'
        self.assertTrue(self.emit('b', 'Working'))
        self.run['native_turn_state'] = 'working'
        self.assertTrue(self.emit('c', 'Working'))
        self.assertTrue(self.emit('ask', 'Approve command 1?', 'blocked'))
        self.assertTrue(self.emit('ask2', 'Approve command 2?', 'blocked'))
        self.run['beads'] = {'issue_id': 'new-task'}
        self.assertTrue(self.emit('task', 'Working'))

    def test_scope_is_run_group_kind_and_replay_is_idempotent(self):
        self.assertTrue(self.emit('a', 'Working'))
        self.assertTrue(self.emit('group', 'Working', group='another'))
        self.assertTrue(self.emit('kind', 'Working', kind='working'))
        self.run['id'] = 'another-run'
        self.assertTrue(self.emit('run', 'Working'))
        with patch('harness.status.logging.getLogger') as logger:
            self.assertFalse(self.emit('duplicate', 'Working'))
            self.assertFalse(self.emit('duplicate', 'Working'))
            self.assertEqual(logger.return_value.error.call_count, 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE id='duplicate'").fetchone()[0], 1)

    def test_normalization_preserves_meaningful_numbers(self):
        self.assertEqual(normalized_text('Status: active; native turn: working. Tests running Observed at 2026-09-19T12:00:00Z'), 'Tests running')
        self.assertEqual(normalized_text('Status: working; native turn: unknown. Tests running Observed at 2026-09-19 13:00:00 UTC.'), 'Tests running')
        self.assertNotEqual(normalized_text('Error 123 at 12:00'), normalized_text('Error 456 at 13:00'))
        self.assertTrue(self.emit('a', 'Status: active; native turn: working. Tests running'))
        self.now += 301
        with self.assertLogs('harness.status', level='ERROR'):
            self.assertFalse(self.emit('b', 'Status: working; native turn: unknown. Tests running Observed at 2026-09-19T12:00:00Z'))

    def test_twenty_notice_window_and_atomic_failure(self):
        self.assertTrue(self.emit('old', 'old content', 'working'))
        for i in range(20):
            self.assertTrue(self.emit(str(i), str(i), 'working'))
        self.assertTrue(self.emit('evicted', 'old content', 'working'))
        with self.store.db:
            self.store.db.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.emit('failed', 'Changed content', group='new')
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM status_notices WHERE id='failed'").fetchone())
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM status_limits WHERE recipient='\"new\"'").fetchone())
        with self.store.db:
            self.store.db.execute('DROP TRIGGER reject_outbox')
        self.assertTrue(self.emit('failed', 'Changed content', group='new'))

    def test_log_throttle_survives_restart(self):
        self.assertTrue(self.emit('first', 'repeat', 'supervisor_error'))
        with self.assertLogs('harness.status', level='ERROR'):
            self.assertFalse(self.emit('second', 'repeat', 'supervisor_error'))
        self.store.db.close()
        self.store = Store(self.temp.name)
        self.now += 299
        with self.assertNoLogs('harness.status'):
            self.assertFalse(self.emit('third', 'repeat', 'supervisor_error'))
        self.now += 1
        with self.assertLogs('harness.status', level='ERROR'):
            self.assertFalse(self.emit('fourth', 'repeat', 'supervisor_error'))
