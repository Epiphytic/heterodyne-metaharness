import tempfile
import json
import unittest
from unittest.mock import Mock
from harness.store import Store
from harness.permission_relay import observe, delivered, initialize


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.addCleanup(self.store.db.close)
        self.run={'id':'run','name':'fixture','native_session_id':'native','pane_id':'%1','group_id':'aa'}
        self.text='Would you like to run the following command?\nReason: retain complete text\n$ '+('echo x;'*2000)+'\nYes, proceed\nNo, and tell Codex what to do\nPress enter to confirm or esc to cancel\n'
        self.info={'alive':True,'pane_id':'%1','text':self.text}

    def test_complete_snapshot_bead_intent_without_operator_delivery(self):
        identity=observe(self.store,self.run,self.info)
        self.assertEqual(observe(self.store,self.run,self.info),identity)
        row=self.store.db.execute('SELECT evidence FROM permission_relays').fetchone()
        self.assertEqual(row[0],self.text)
        request=self.store.db.execute('SELECT request FROM manager_tasks').fetchone()
        self.assertEqual(json.loads(request[0])['evidence'], self.text)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)

    def test_old_prompt_or_missing_native_does_not_route(self):
        info=dict(self.info,text=self.text+'Normal final response\n'*5)
        self.assertIsNone(observe(self.store,self.run,info))
        self.assertIsNotNone(observe(self.store,dict(self.run,native_session_id=None),self.info))
        with self.assertRaises(ValueError):observe(self.store,self.run,dict(self.info,pane_id='%2'))

    def test_delivery_identity_binding_and_rejection(self):
        identity=observe(self.store,self.run,self.info)
        # Historical visible relays remain verifiable after new observations
        # stop sending operator messages.
        with self.store.db:
            self.store.db.execute('INSERT INTO outbox(id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)',
                                  ('old-part','run','aa',self.text,1))
            self.store.db.execute('INSERT INTO permission_parts VALUES (?,?,?)',('old-part',identity,0))
        row=self.store.db.execute('SELECT * FROM outbox LIMIT 1').fetchone()
        transport=Mock(account_id='cc')
        with self.store.db:
            delivered(self.store,transport,row,{'message_ids_hex':['dd']})
        with self.store.db:
            delivered(self.store,transport,row,{'message_ids_hex':['dd']})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permission_messages').fetchone()[0],1)
        with self.assertRaises(ValueError):delivered(self.store,transport,row,{})
