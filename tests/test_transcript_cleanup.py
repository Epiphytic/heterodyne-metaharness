import sqlite3
import unittest
from harness.transcript_cleanup import detached, fingerprint


class TranscriptGuards(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('CREATE TABLE sessions(id TEXT, ended_at REAL, parent_session_id TEXT); CREATE TABLE messages(id INTEGER,session_id TEXT,content TEXT); CREATE TABLE gateway_routing(entry_json TEXT);')
        self.db.execute("INSERT INTO sessions VALUES('old',1,NULL)")

    def test_child_and_operational_references_block(self):
        self.assertTrue(detached(self.db,'old'))
        self.db.execute("INSERT INTO sessions VALUES('child',1,'old')")
        with self.assertRaises(ValueError): detached(self.db,'old')
        self.db.execute("DELETE FROM sessions WHERE id='child'")
        self.db.execute('INSERT INTO gateway_routing VALUES(?)', ('{"session_id":"old"}',))
        with self.assertRaises(ValueError): detached(self.db,'old')

    def test_fingerprint_changes_with_new_message(self):
        before=fingerprint(self.db,'old')
        self.db.execute("INSERT INTO messages VALUES(1,'old','new message')")
        self.assertNotEqual(before,fingerprint(self.db,'old'))
        self.db.execute("UPDATE sessions SET ended_at=NULL")
        with self.assertRaises(ValueError): detached(self.db,'old')

    def test_native_audit_text_is_not_a_reference(self):
        self.db.execute('INSERT INTO gateway_routing VALUES(?)', ('{"session_id":"current","metadata":{"audit":"old"}}',))
        self.db.executescript('CREATE TABLE async_delegations(origin_session_id TEXT,task_json TEXT); CREATE TABLE delivery_obligations(session_key TEXT,content TEXT);')
        self.db.execute('INSERT INTO async_delegations VALUES(?,?)', ('current','{"audit":"old"}'))
        self.db.execute('INSERT INTO delivery_obligations VALUES(?,?)', ('current','Review old'))
        self.assertTrue(detached(self.db,'old'))
        self.db.execute("UPDATE async_delegations SET origin_session_id='old'")
        with self.assertRaises(ValueError): detached(self.db,'old')

    def test_harness_audit_text_allowed_and_native_bindings_block(self):
        import json
        import tempfile
        from pathlib import Path
        from harness.store import Store
        from harness.transcript_cleanup import external_check
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory); store=Store(home)
            run=dict(id='run',name='maintenance',repo=directory,agent='codex',workdir=directory,group_id='group',state='active',created_at=1,
                     native_session_id='current',prompt='Audit historical old',manager={'native_session_id':'manager-current'})
            with store.db:
                store.save(run)
                store.db.execute("INSERT INTO inbox(id,run_id,text) VALUES('audit','run','delete old after review')")
                store.db.execute("INSERT INTO outbox(id,run_id,text) VALUES('status','run','candidate old')")
            (home/'sessions').mkdir()
            registry=home/'sessions/sessions.json'
            registry.write_text(json.dumps({'route':{'session_id':'current','metadata':{'audit':'old'}}}))
            external_check(home,'old')
            for field in ('native_session_id','parent_hermes_session_id'):
                changed=dict(run); changed[field]='old'
                with store.db: store.save(changed)
                with self.assertRaises(ValueError): external_check(home,'old')
                with store.db: store.save(run)
            # Native lineage history remains protective even when current run moves.
            with store.db:
                store.db.execute("INSERT INTO native_sessions VALUES('run','worker','new','old',1)")
            with self.assertRaises(ValueError): external_check(home,'old')
            with store.db: store.db.execute('DELETE FROM native_sessions')
            registry.write_text(json.dumps({'route':{'session_id':'current','prev_session_id':'old'}}))
            with self.assertRaises(ValueError): external_check(home,'old')
