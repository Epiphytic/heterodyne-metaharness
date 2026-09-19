import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from harness import status
from harness.notify import receive
from harness.store import Store
from install_brain import apply
from install_notify import configured, plan


class NotifyTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root)
        self.addCleanup(self.store.db.close)
        self.env = patch.dict(os.environ, {'HERMES_WORKSTREAM_RUN': 'run',
                                          'HERMES_WORKSTREAM_ROLE': 'worker'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.run = {'id': 'run', 'name': 'test', 'agent': 'codex', 'workdir': str(self.root),
                    'state': 'active', 'task_state': 'working', 'created_at': 1,
                    'native_session_id': 'native', 'native_turn_key': ['native', 'turn'],
                    'native_turn_state': 'working', 'native_turn_at': 1}
        with self.store.db:
            self.store.save(self.run)
        self.payload = {'type': 'agent-turn-complete', 'thread-id': 'native', 'turn-id': 'turn',
                        'cwd': str(self.root), 'last-assistant-message': 'private result'}

    def test_exact_completion_duplicate_and_stale_lifecycle(self):
        self.assertTrue(receive(self.store, self.payload))
        self.assertFalse(receive(self.store, self.payload))
        run = self.store.get('run')
        self.assertEqual((run['state'], run['task_state'], run['native_turn_state']), ('idle',)*3)
        self.assertFalse(run['continuation']['checked'])
        self.assertFalse(status.activity(run, 'native', 'turn', 'working', 2))
        receipt = self.store.db.execute("SELECT text FROM events WHERE kind='native_completion'").fetchall()
        self.assertEqual(len(receipt), 1)
        self.assertNotIn('private result', receipt[0][0])
        self.assertTrue((self.store.root / 'runs/run/checkpoint.json').exists())

    def test_old_turn_fork_wrong_cwd_and_recovery(self):
        self.assertFalse(receive(self.store, dict(self.payload, **{'turn-id': 'old'})))
        self.assertFalse(receive(self.store, dict(self.payload, **{'thread-id': 'fork'})))
        with self.assertRaises(ValueError):
            receive(self.store, dict(self.payload, cwd='/unowned'))
        self.assertEqual(self.store.get('run')['native_turn_state'], 'working')
        self.run.update(resume_required=True, state='awaiting_resume')
        with self.store.db:
            self.store.save(self.run)
        self.assertTrue(receive(self.store, self.payload))
        run = self.store.get('run')
        self.assertEqual(run['state'], 'awaiting_resume')
        self.assertTrue(run['resume_required'])

    def test_install_multiline_preserves_policy_and_backup(self):
        original = '# config\nnotify = [\n"old", "--arg",\n]\napproval_policy = "on-request"\n[tui]\nnotifications = true\n'
        home = self.root / 'codex'
        home.mkdir()
        path = home / 'config.toml'
        path.write_text(original)
        prepared = plan(path)
        apply(prepared, self.root / 'backup')
        self.assertEqual(apply(prepared, self.root / 'backup')['changed'], 0)
        data = tomllib.loads(path.read_text())
        self.assertEqual(json.loads(data['notify'][3]), ['old', '--arg'])
        self.assertEqual(data['approval_policy'], 'on-request')
        self.assertEqual(plan(path)['files'], [])
        self.assertEqual(tomllib.loads(configured('[tui]\nnotifications=false\n'))['tui'], {'notifications': False})
        with self.assertRaises(ValueError):
            configured('"notify" = ["old"]\n')

    def test_entrypoint_failure_still_forwards_exact_payload(self):
        result = self.root / 'forwarded'
        code = 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])'
        command = [sys.executable, '-c', code, str(result)]
        script = Path(__file__).resolve().parents[1] / 'bin/workstream-notify'
        proc = subprocess.run([sys.executable, str(script), '--forward', json.dumps(command), 'invalid-json'],
                              cwd=self.root, env=dict(os.environ, HERMES_HOME=str(self.root)),
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(result.read_text(), 'invalid-json')
        self.assertIn('deferred', proc.stderr)


if __name__ == '__main__':
    unittest.main()
