import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from harness.permission_consent import receive
from harness.permission_relay import observe, delivered
from harness.store import Store
from install_permission_relay import plan, ANCHOR, HOOK
from install_brain import apply


class ConsentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name); self.addCleanup(self.store.db.close)
        self.run = dict(id='run', name='fixture', agent='codex', state='working',
                        workdir=self.temp.name, created_at=1, native_session_id='native',
                        pane_id='%1', group_id='aa')
        with self.store.db:
            self.store.save(self.run)
        self.text = 'Would you like to run the following command?\n$ echo safe\nPress enter to confirm or esc to cancel'
        identity = observe(self.store,self.run,dict(alive=True,pane_id='%1',text=self.text))
        # Historical delivered parts can still carry consent receipts. New
        # observations create manager Beads and do not send visible parts.
        with self.store.db:
            self.store.db.execute('INSERT INTO outbox(id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)',
                                  ('old-part','run','aa',self.text,1))
            self.store.db.execute('INSERT INTO permission_parts VALUES (?,?,?)',('old-part',identity,0))
        row = self.store.db.execute('SELECT * FROM outbox LIMIT 1').fetchone()
        with self.store.db:
            delivered(self.store,Mock(account_id='bb'),row,dict(message_ids_hex=['cc']))
        self.event = dict(type='reaction_added',account_id_hex='bb',group_id_hex='aa',
                          target_message_id_hex='cc',event_id_hex='dd',emoji='👍',
                          actor=dict(is_self=False,account_id_hex='ee'),
                          original_nostr_event={'id':'original','tags':[['e','cc']]})

    def intake(self, event=None):
        return receive(self.store,event or self.event,account_id='bb',allowed_senders={'ee'})

    def test_preserves_event_target_and_retries_once(self):
        self.assertTrue(self.intake()); self.assertTrue(self.intake())
        rows = self.store.db.execute('SELECT evidence FROM permission_consents').fetchall()
        self.assertEqual(len(rows),1)
        record = json.loads(rows[0][0])
        self.assertEqual(record['reaction_event'],self.event)
        self.assertIn(self.text,record['target_message']['text'])
        self.assertEqual(record['state'],'requires-native-inspection')
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM inbox WHERE id LIKE 'permission-consent:%'").fetchone()[0],1)
        changed = dict(self.event,emoji='👎')
        with self.assertRaises(ValueError): self.intake(changed)

    def test_unknown_disallowed_and_stale_never_handoff(self):
        self.assertFalse(self.intake(dict(self.event,target_message_id_hex='ff')))
        self.assertTrue(self.intake(dict(self.event,actor=dict(is_self=False,account_id_hex='ff'))))
        self.assertFalse(self.intake(dict(self.event,emoji='❤️')))
        with self.store.db:
            self.store.save(dict(self.run,native_session_id='new-native'))
        self.assertTrue(self.intake())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permission_consents').fetchone()[0],0)
        with self.store.db:
            self.store.save(dict(self.run,state='stopped'))
        self.assertTrue(self.intake())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permission_consents').fetchone()[0],0)

    def test_exact_manager_binding_routes_but_changed_manager_does_not(self):
        self.run['manager'] = {'native_session_id': 'native', 'pane_id': '%1'}
        self.run['native_session_id'] = 'worker-native'
        self.run['pane_id'] = '%2'
        with self.store.db:
            self.store.save(self.run)
        self.assertTrue(self.intake())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permission_consents').fetchone()[0], 1)
        self.run['manager']['native_session_id'] = 'replacement'
        with self.store.db:
            self.store.save(self.run)
        self.assertTrue(self.intake(dict(self.event, event_id_hex='abcd')))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permission_consents').fetchone()[0], 1)

    def test_failed_inbox_insert_retries_atomically(self):
        self.store.db.execute("CREATE TRIGGER fail_consent BEFORE INSERT ON inbox WHEN NEW.id LIKE 'permission-consent:%' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(Exception): self.intake()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permission_consents').fetchone()[0],0)
        self.store.db.execute('DROP TRIGGER fail_consent')
        self.assertTrue(self.intake())


class ConsentInstallerTest(unittest.TestCase):
    def test_exact_anchor_private_backup_and_repeat_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plugin = root/'plugin'; plugin.mkdir()
            adapter = plugin/'adapter.py'
            adapter.write_text('class Adapter:\n'+ANCHOR+'        pass\n')
            manifest = plan(adapter)
            apply(manifest,root/'quarantine')
            self.assertIn(HOOK,adapter.read_text())
            self.assertEqual(plan(adapter),dict(files=[],creates=[]))
            self.assertTrue((plugin/'_native_consent.py').exists())
            adapter.write_text(adapter.read_text().replace('self.approval_reactions','False'))
            with self.assertRaises(ValueError): plan(adapter)
