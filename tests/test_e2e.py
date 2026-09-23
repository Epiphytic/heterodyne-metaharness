"""Real process/session recovery, fake model executables and acknowledged transport."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from harness.delivery import deliver
from harness.store import Store
from harness.supervisor import Supervisor
from harness.tmux import Tmux


class AcknowledgingTransport:
    def __init__(self):
        self.groups = []
        self.messages = []

    def create_group(self, name, request_id):
        self.groups.append((name, request_id))
        return 'test-group'

    def group_info(self, group):
        assert group == 'test-group'
        return {'id': group}

    def send(self, group, text, identity):
        assert group == 'test-group'
        self.messages.append((identity, text))
        return {'type': 'send_message_result', 'request_id': identity, 'message_id': identity}


@unittest.skipUnless(shutil.which('tmux'), 'tmux required')
class EndToEndTest(unittest.TestCase):
    def wait_records(self, path, count):
        for _ in range(100):
            rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            if len(rows) >= count:
                return rows
            time.sleep(.02)
        self.fail(f'Expected {count} process starts recorded at {path}')

    def test_real_processes_reboot_restore_without_replaying_task(self):
        socket = 'hws-e2e-' + uuid.uuid4().hex
        tmux = Tmux(socket)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root)
            transport = AcknowledgingTransport()
            executable = root / 'fake model executable'
            executable.write_text('#!' + sys.executable + '''
import json, pathlib, sys, time
path = pathlib.Path(sys.argv[sys.argv.index('--capture') + 1])
with path.open('a') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
print('Awaiting input', flush=True)
time.sleep(60)
''')
            executable.chmod(0o700)
            worker_log, manager_log = root / 'worker.jsonl', root / 'manager.jsonl'
            config = {'manager': {'executable': str(executable), 'extra_args': ['--capture', str(manager_log)]}}
            supervisor = Supervisor(store, tmux, transport, config, boot='before')
            unrelated = {'id': 'unrelated', 'tmux_session': 'unrelated', 'workdir': directory}
            try:
                unrelated['pane_id'] = tmux.launch(unrelated, ['/usr/bin/sleep', '60'])
                prompt = 'Review $(touch SHOULD_NOT_EXIST), `id`, then explain;'
                run = supervisor.start('e2e', None, 'claude', prompt,
                    agent_config={'executable': str(executable), 'extra_args': ['--capture', str(worker_log)]})
                self.assertEqual(run['state'], 'active', run.get('error'))
                worker_initial = self.wait_records(worker_log, 1)[0]
                self.wait_records(manager_log, 1)
                self.assertEqual(worker_initial[-1], prompt)
                self.assertFalse((Path(run['workdir']) / 'SHOULD_NOT_EXIST').exists())
                native_id = run['native_session_id']
                self.assertTrue((store.root / 'runs' / run['id'] / 'checkpoint.json').exists())
                supervisor.start('e2e', None, 'claude', 'must not replace task')
                self.assertEqual(len(self.wait_records(worker_log, 1)), 1)
                self.assertEqual(len(self.wait_records(manager_log, 1)), 1)
                run['observation'] = {'summary': 'Review begun; no implementation performed.'}
                supervisor.persist(run)
                tmux.stop(run)
                tmux.stop(run['manager'])
                store.db.close()
                store = Store(root)
                recovered = Supervisor(store, tmux, transport, config, boot='after')
                recovered.tick()
                saved = store.get('e2e')
                self.assertEqual(saved['state'], 'awaiting_resume', saved.get('error'))
                worker_resume = self.wait_records(worker_log, 2)[1]
                manager_resume = self.wait_records(manager_log, 2)[1]
                self.assertIn('--resume', worker_resume)
                self.assertIn(native_id, worker_resume)
                self.assertNotIn(prompt, worker_resume)
                self.assertIn('--continue', manager_resume)
                self.assertIn('workstream-' + run['name'] + '-manager', manager_resume)
                self.assertNotIn('--query', manager_resume)
                recovered.tick()
                self.assertEqual(len(self.wait_records(worker_log, 2)), 2)
                self.assertEqual(len(self.wait_records(manager_log, 2)), 2)
                for _ in range(20):
                    deliver(store, transport)
                pending = store.db.execute('SELECT COUNT(*) FROM outbox WHERE delivered_at IS NULL').fetchone()[0]
                self.assertEqual(pending, 0)
                self.assertTrue(any('Review begun' in text for _, text in transport.messages))
                recovered.stop(saved, dict(run_id=saved['id'], action='stop', approved_by='operator',
                    response='Stop fixture', evidence_ref='fixture:stop', force=False, open_beads=[]))
                self.assertTrue(tmux.inspect(unrelated)['alive'])
                self.assertTrue(tmux.inspect(saved)['missing'])
                self.assertTrue(tmux.inspect(saved['manager'])['missing'])
            finally:
                store.db.close()
                subprocess.run(['tmux', '-L', socket, 'kill-server'], capture_output=True)
