import sqlite3
import tempfile
import unittest
from pathlib import Path
from harness.agents import Adapter


class AgentsTest(unittest.TestCase):
    def test_recovery_never_replays_prompt(self):
        for name in ('codex', 'claude', 'hermes'):
            run = dict(id='test', native_session_id='exact-id', prompt='DO NOT REPLAY')
            argv = Adapter(name).argv(run, recover=True)
            self.assertIn('exact-id', argv)
            self.assertNotIn('DO NOT REPLAY', argv)
            self.assertNotIn('--last', argv)

    def test_missing_identity_refuses_recovery(self):
        for name in ('codex', 'claude', 'hermes', 'command'):
            with self.assertRaises(ValueError):
                Adapter(name).argv({'id': 'x'}, recover=True)

    def test_claude_identity_is_deterministic(self):
        first, second = {'id': 'x'}, {'id': 'x'}
        self.assertEqual(Adapter('claude-code').argv(first), Adapter('claude').argv(second))
        self.assertEqual(first['native_session_id'], second['native_session_id'])

    def test_named_manager_recovery_never_creates_missing_session(self):
        run = {'id': 'x', 'config': {'session_name': 'harness-manager-x'}}
        self.assertIn('--create-if-missing', Adapter('hermes').argv(run))
        argv = Adapter('hermes').argv(run, recover=True)
        self.assertIn('harness-manager-x', argv)
        self.assertNotIn('--create-if-missing', argv)

    def test_discovery_rejects_ambiguous_cwd_time(self):
        with tempfile.TemporaryDirectory() as directory:
            with sqlite3.connect(Path(directory) / 'state_5.sqlite') as db:
                db.execute('create table threads(id text,cwd text,created_at integer)')
                db.executemany('insert into threads values(?,?,?)',
                               [('one', directory, 100), ('outside', directory, 10)])
            run = {'workdir': directory, 'launched_at': 100, 'config': {'home': directory}}
            self.assertEqual(Adapter('codex').discover(run), 'one')
            with sqlite3.connect(Path(directory) / 'state_5.sqlite') as db:
                db.execute('insert into threads values(?,?,?)', ('two', directory, 101))
            self.assertIsNone(Adapter('codex').discover(run))

    def test_old_spinner_never_proves_activity_or_completion(self):
        result = Adapter('command').observe({}, 'esc to interrupt\nDone\n>')
        self.assertEqual(result['state'], 'unknown')

    def test_hermes_missing_cwd_exact_title_and_launch_window(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            name = 'workstream-hermes-maintenance-manager'
            run = {'workdir': directory, 'launched_at': 100.8,
                   'config': {'home': directory, 'session_name': name}}
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE sessions(id TEXT,cwd TEXT,started_at REAL,title TEXT,source TEXT,parent_session_id TEXT)')
                db.executemany('INSERT INTO sessions VALUES(?,?,?,?,?,?)', [
                    ('manager',None,101.1,name,'cli',None),
                    ('old',None,10,name,'cli',None),
                    ('late',None,222,name,'cli',None),
                    ('gateway',None,101,name,'marmot',None),
                    ('different-title',None,101,name+'-other','cli',None),
                    ('different-cwd','/other',101,name,'cli',None),
                    ('fork',None,101,name,'cli','parent'),
                ])
            self.assertEqual(Adapter('hermes').discover(run), 'manager')
            with sqlite3.connect(path) as db:
                db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', ('ambiguous','',102,name,'cli',None))
            self.assertIsNone(Adapter('hermes').discover(run))
            run['native_session_id'] = 'verified-manager'
            self.assertEqual(Adapter('hermes').discover(run), 'verified-manager')
            self.assertEqual(Adapter('hermes').argv(run, recover=True),
                             ['hermes','chat','--resume','verified-manager','--no-restore-cwd'])

    def test_hermes_fork_only_and_unnamed_do_not_discover(self):
        with tempfile.TemporaryDirectory() as directory:
            name = 'workstream-manager'
            with sqlite3.connect(Path(directory)/'state.db') as db:
                db.execute('CREATE TABLE sessions(id TEXT,cwd TEXT,started_at REAL,title TEXT,source TEXT,parent_session_id TEXT)')
                db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', ('fork',None,101,name,'cli','parent'))
            run = {'workdir':directory,'launched_at':100,'config':{'home':directory,'session_name':name}}
            self.assertIsNone(Adapter('hermes').discover(run))
            del run['config']['session_name']
            self.assertIsNone(Adapter('hermes').discover(run))
