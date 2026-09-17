import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from harness.native_hook import payload_registration, register
from harness.store import Store


class NativeHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.db.close)
        self.run = dict(id='owner', name='project', agent='claude', workdir='/tmp/work',
                        state='active', created_at=1, group_id='aa',
                        manager=dict(id='owner-manager', agent='hermes'))
        with self.store.db:
            self.store.save(self.run)

    def test_compaction_updates_identity_and_retains_alias_group_history(self):
        register(self.store, 'owner', 'worker', 'first')
        register(self.store, 'owner', 'worker', 'compacted', 'first')
        self.assertEqual(self.store.get('workstream-project-worker')['native_session_id'], 'compacted')
        row = self.store.db.execute('SELECT * FROM native_sessions WHERE native_id=?', ('compacted',)).fetchone()
        self.assertEqual(row['previous_id'], 'first')
        self.assertEqual(self.store.get('owner')['group_id'], 'aa')
        checkpoint = json.loads((self.store.root / 'runs/owner/checkpoint.json').read_text())
        self.assertEqual(checkpoint['native_session_id'], 'compacted')

    def test_duplicate_registration_idempotent(self):
        register(self.store, 'owner', 'worker', 'first')
        register(self.store, 'owner', 'worker', 'next', 'first')
        register(self.store, 'owner', 'worker', 'next', 'first')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM native_sessions').fetchone()[0], 2)

    def test_mismatched_previous_is_rejected(self):
        register(self.store, 'owner', 'worker', 'first')
        with self.assertRaises(ValueError):
            register(self.store, 'owner', 'worker', 'next', 'wrong')
        self.assertEqual(self.store.get('owner')['native_session_id'], 'first')

    def test_delayed_old_hook_does_not_roll_identity_back(self):
        register(self.store, 'owner', 'worker', 'first')
        register(self.store, 'owner', 'worker', 'next')
        with self.assertRaises(ValueError):
            register(self.store, 'owner', 'worker', 'first')
        self.assertEqual(self.store.get('owner')['native_session_id'], 'next')

    def test_cross_run_and_cross_role_collision_rejected(self):
        register(self.store, 'owner', 'worker', 'first')
        other = dict(self.run, id='other', name='other', manager=dict(agent='hermes'))
        with self.store.db:
            self.store.save(other)
        with self.assertRaises(ValueError):
            register(self.store, 'other', 'worker', 'first')
        with self.assertRaises(ValueError):
            register(self.store, 'owner', 'manager', 'first')

    def test_manager_updates_only_nested_identity(self):
        register(self.store, 'owner', 'manager', 'manager-session')
        result = self.store.get('owner')
        self.assertNotIn('native_session_id', result)
        self.assertEqual(result['manager']['native_session_id'], 'manager-session')

    def test_payload_rejects_nested_session_startup_and_wrong_cwd(self):
        register(self.store, 'owner', 'worker', 'first')
        for source, cwd in [('startup', '/tmp/work'), ('resume', '/tmp/work'), ('compact', '/elsewhere')]:
            with self.assertRaises(ValueError):
                payload_registration(self.store, 'owner', 'worker',
                    dict(hook_event_name='SessionStart', source=source, cwd=cwd, session_id='nested'))
        self.assertEqual(self.store.get('owner')['native_session_id'], 'first')

    def test_payload_rejects_unrelated_hook(self):
        with self.assertRaises(ValueError):
            payload_registration(self.store, 'owner', 'worker',
                                 dict(hook_event_name='SubagentStart', source='startup', session_id='wrong'))

    def test_auto_without_owner_is_noop(self):
        env = dict(os.environ, HERMES_HOME=self.temp.name)
        env.pop('HERMES_WORKSTREAM_RUN', None)
        result = subprocess.run([sys.executable, '-m', 'harness.native_hook', 'auto'],
            input='not json', text=True, capture_output=True, env=env,
            cwd=Path(__file__).resolve().parents[1], timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('native_session_id', self.store.get('owner'))

    def test_auto_uses_owned_manager_suffix(self):
        run = self.store.get('owner')
        run['manager']['workdir'] = '/tmp/manager'
        with self.store.db:
            self.store.save(run)
        result = subprocess.run([sys.executable, '-m', 'harness.native_hook', 'auto'],
            input=json.dumps(dict(hook_event_name='SessionStart', source='startup',
                                  session_id='mgr', cwd='/tmp/manager')),
            text=True, capture_output=True,
            env=dict(os.environ, HERMES_HOME=self.temp.name, HERMES_WORKSTREAM_RUN='owner-manager'),
            cwd=Path(__file__).resolve().parents[1], timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.store.get('owner')['manager']['native_session_id'], 'mgr')
        self.assertNotIn('native_session_id', self.store.get('owner'))

    def test_cli_registers_sessionstart_compact_and_injects_durable_context(self):
        result = subprocess.run([sys.executable, '-m', 'harness.native_hook', 'owner', 'worker'],
             input=json.dumps(dict(hook_event_name='SessionStart', source='compact', session_id='new-id', cwd='/tmp/work')),
             env=dict(os.environ, HERMES_HOME=self.temp.name), text=True, capture_output=True,
             cwd=Path(__file__).resolve().parents[1], timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('project', context)
        self.assertIn('aa', context)
        self.assertEqual(self.store.get('owner')['native_session_id'], 'new-id')


if __name__ == '__main__':
    unittest.main()
