import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from harness import brain
from harness.brain_visible import enqueue_receipts
from harness.delivery import deliver
from harness.store import Store


class VisibleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home)
        self.addCleanup(self.store.db.close)
        brain.initialize(self.store.db)

    def receipt(self, session, native='native'):
        notice = brain.offer(self.store.db, session, native, 'turn', {'SOUL.md':'a'})
        self.assertEqual(enqueue_receipts(self.store), 0)
        self.assertTrue(brain.acknowledge(self.store.db, session, native, 'turn', [
            {'role':'user','api_content':notice['context']}, {'role':'assistant','content':'response'}]))
        return notice

    def test_managed_receipt_retry_and_dedup(self):
        run = dict(id='run', name='test', agent='codex', workdir=str(self.home), state='active', created_at=1)
        with self.store.db:
            self.store.save(run)
        notice = self.receipt('run:worker')
        self.assertEqual(enqueue_receipts(self.store), 0)
        self.assertIsNotNone(self.store.db.execute('SELECT snapshot FROM brain_known').fetchone())
        run['group_id']='abcdef'
        with self.store.db:
            self.store.save(run)
        self.assertEqual(enqueue_receipts(self.store), 1)
        self.assertEqual(enqueue_receipts(self.store), 0)
        row = self.store.db.execute('SELECT * FROM outbox').fetchone()
        self.assertIn('worker brain context received', row['text'])
        self.assertIn(notice['token'], row['text'])
        transport=Mock(account_id='cc')
        transport.timeout=10
        transport.send.side_effect=RuntimeError('offline')
        deliver(self.store, transport, now=1)
        self.assertIsNone(self.store.db.execute('SELECT delivered_at FROM outbox').fetchone()[0])
        transport.send.side_effect=None
        deliver(self.store, transport, now=32)
        self.assertEqual(self.store.db.execute('SELECT delivered_at FROM outbox').fetchone()[0],32)
        self.assertEqual(transport.send.call_count,2)
        self.assertEqual(transport.send.call_args_list[0],transport.send.call_args_list[1])

    def test_exact_standalone_mapping_ambiguity_and_compression(self):
        self.receipt('hermes:parent', 'parent')
        with sqlite3.connect(self.home/'state.db') as db:
            db.execute('CREATE TABLE sessions(id TEXT,parent_session_id TEXT,end_reason TEXT)')
            db.executemany('INSERT INTO sessions VALUES (?,?,?)', [('parent',None,'compression'),('child','parent',None)])
        sessions=self.home/'sessions';sessions.mkdir()
        key='agent:main:marmot:group:abcdef:sender'
        entry={'session_key':key,'session_id':'child','platform':'marmot','metadata':{}}
        registry={'_README':'not a session',key:entry}
        other='agent:main:marmot:group:fedcba:sender'
        registry[other]=dict(entry,session_key=other)
        path=sessions/'sessions.json';path.write_text(json.dumps(registry))
        self.assertEqual(enqueue_receipts(self.store),0)
        del registry[other];path.write_text(json.dumps(registry))
        self.assertEqual(enqueue_receipts(self.store),1)
        row=self.store.db.execute('SELECT group_id,text FROM outbox').fetchone()
        self.assertEqual(row[0],'abcdef')
        self.assertIn('hermes brain context received',row[1])

    def test_more_than_100_unroutable_receipts_do_not_starve_known_group(self):
        with self.store.db:
            self.store.db.executemany('INSERT INTO brain_visible(token,session,native_id,notice) VALUES (?,?,?,?)',
                                     [(str(i), 'codex:unmanaged'+str(i), 'n', 'inventory') for i in range(105)])
            run = dict(id='run', name='test', agent='codex', workdir=str(self.home), state='active', created_at=1, group_id='abcdef')
            self.store.save(run)
            self.store.db.execute('INSERT INTO brain_visible(token,session,native_id,notice) VALUES (?,?,?,?)',
                                  ('known', 'run:worker', 'n', 'inventory'))
        self.assertEqual(enqueue_receipts(self.store),0)
        self.assertEqual(enqueue_receipts(self.store),1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM brain_visible WHERE queued_at IS NULL').fetchone()[0],105)

    def test_visible_db_failure_does_not_prevent_progress_delivery(self):
        with self.store.db:
            self.store.db.execute('INSERT INTO outbox(id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)',
                                  ('progress','run','abcdef','existing progress',1))
        transport=Mock(account_id='cc');transport.timeout=10
        with patch('harness.brain_visible.enqueue_receipts', side_effect=sqlite3.OperationalError('locked')):
            with self.assertLogs('harness.delivery', level='WARNING'):
                deliver(self.store,transport,now=2)
        transport.send.assert_called_once_with('abcdef','existing progress','progress')
