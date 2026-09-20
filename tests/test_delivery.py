import fcntl
import tempfile
import unittest

from harness.delivery import deliver, health
from harness.store import Store


class Transport:
    timeout = 20

    def __init__(self):
        self.calls = []
        self.fail = False

    def send(self, group, text, event):
        self.calls.append((group, event, self.timeout))
        if self.fail:
            raise RuntimeError('offline')
        return {'type': 'final_sent', 'message_ids_hex': ['aa']}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.db.close)
        self.transport = Transport()

    def enqueue(self, identity, group='aa', created=0):
        with self.store.db:
            self.store.db.execute('''INSERT INTO outbox
              (id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)''',
              (identity, 'run', group, 'status', created))

    def test_one_bounded_attempt_and_restored_timeout(self):
        for i in range(20):
            self.enqueue(str(i))
        deliver(self.store, self.transport, now=10)
        self.assertEqual(self.transport.calls, [('aa', '0', 15)])
        self.assertEqual(self.transport.timeout, 20)
        self.assertEqual(health(self.store)['pending'], 19)

    def test_failure_durable_retry_uses_same_id(self):
        self.enqueue('event')
        self.transport.fail = True
        deliver(self.store, self.transport, now=10)
        deliver(self.store, self.transport, now=20)
        self.assertEqual(len(self.transport.calls), 1)
        self.transport.fail = False
        deliver(self.store, self.transport, now=40)
        self.assertEqual([c[1] for c in self.transport.calls], ['event', 'event'])
        self.assertEqual(health(self.store)['pending'], 0)

    def test_failed_group_preserves_order_without_blocking_other_group(self):
        self.enqueue('first')
        self.enqueue('second', created=1)
        self.store.db.execute("UPDATE outbox SET text='second-status' WHERE id='second'")
        self.store.db.commit()
        self.enqueue('other', group='bb', created=2)
        self.transport.fail = True
        deliver(self.store, self.transport, now=10)
        self.transport.fail = False
        deliver(self.store, self.transport, now=11)
        self.assertEqual([c[1] for c in self.transport.calls], ['first', 'other'])
        deliver(self.store, self.transport, now=12)
        self.assertEqual(len(self.transport.calls), 2)
        deliver(self.store, self.transport, now=40)
        deliver(self.store, self.transport, now=41)
        self.assertEqual([c[1] for c in self.transport.calls], ['first', 'other', 'first', 'second'])

    def test_identical_text_not_resent_within_one_hour(self):
        # Operator contract (2026-09-20): dedup all sent-message hashes for a
        # full hour; identical rendered text to a group is dropped, not resent.
        self.enqueue('event-a')
        deliver(self.store, self.transport, now=10)
        self.enqueue('event-b', created=1)  # distinct row, same text
        deliver(self.store, self.transport, now=20)
        deliver(self.store, self.transport, now=3500)  # 58 min later: still suppressed
        self.assertEqual([c[1] for c in self.transport.calls], ['event-a'])
        self.assertEqual(health(self.store)['pending'], 1)
        deliver(self.store, self.transport, now=3610)  # past the hour window: sends
        self.assertEqual([c[1] for c in self.transport.calls], ['event-a', 'event-b'])

    def test_identical_text_to_different_groups_both_send(self):
        self.enqueue('event-a', group='aa')
        self.enqueue('event-b', group='bb', created=1)
        deliver(self.store, self.transport, now=10)
        deliver(self.store, self.transport, now=11)
        self.assertEqual([c[1] for c in self.transport.calls], ['event-a', 'event-b'])

    def test_third_failure_queues_single_escalation_without_recursive_alerts(self):
        self.enqueue('event')
        self.transport.fail = True
        for now in (0, 30, 60):
            deliver(self.store, self.transport, now=now, ops_group='cc')
        rows = self.store.db.execute('SELECT * FROM outbox').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['group_id'], 'cc')
        for now in (61, 91, 121, 151, 181, 211):
            deliver(self.store, self.transport, now=now, ops_group='cc')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 2)

    def test_simultaneous_dispatcher_does_not_send(self):
        self.enqueue('event')
        with open(self.store.root / '.delivery.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            deliver(self.store, self.transport, now=0)
        self.assertEqual(self.transport.calls, [])


if __name__ == '__main__':
    unittest.main()
