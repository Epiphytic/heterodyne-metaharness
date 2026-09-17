import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from harness.tmux import Tmux


@unittest.skipUnless(shutil.which('tmux'), 'tmux required')
class TmuxTest(unittest.TestCase):
    def setUp(self):
        self.socket = 'hws-test-' + uuid.uuid4().hex
        self.tmux = Tmux(self.socket)
        self.directory = tempfile.TemporaryDirectory()
        self.run = {'id': 'one', 'tmux_session': 'test-one', 'workdir': self.directory.name}

    def tearDown(self):
        subprocess.run(['tmux', '-L', self.socket, 'kill-server'], capture_output=True)
        self.directory.cleanup()

    def test_exit_status_retained_and_no_shell_interpretation(self):
        self.run['pane_id'] = self.tmux.launch(self.run, ['/usr/bin/printf', '%s', '$(touch injected); `id`;'])
        for _ in range(30):
            info = self.tmux.inspect(self.run)
            if info['dead']:
                break
            time.sleep(.02)
        self.assertTrue(info['dead'])
        self.assertEqual(info['exit_code'], 0)
        self.assertIn('$(touch injected); `id`;', info['text'])

    def test_idempotent_launch_and_owner_check(self):
        pane = self.tmux.launch(self.run, ['/usr/bin/sleep', '30'])
        self.run['pane_id'] = pane
        self.assertEqual(self.tmux.launch(self.run, ['/usr/bin/sleep', '30']), pane)
        other = dict(self.run, id='intruder')
        with self.assertRaises(RuntimeError):
            self.tmux.stop(other)
        self.assertTrue(self.tmux.inspect(self.run)['alive'])
        self.tmux.stop(self.run)
        self.tmux.stop(self.run)
        self.assertTrue(self.tmux.inspect(self.run)['missing'])

    def test_replaced_pane_not_accepted(self):
        self.run['pane_id'] = self.tmux.launch(self.run, ['/usr/bin/sleep', '30'])
        self.run['pane_id'] = '%9999'
        with self.assertRaises(RuntimeError):
            self.tmux.inspect(self.run)

    def test_paste_is_literal_and_submit_is_explicit(self):
        self.run['pane_id'] = self.tmux.launch(self.run, ['/usr/bin/python3', '-u', '-c',
            "print('READY'); print('GOT:' + input()); import time; time.sleep(30)"])
        time.sleep(.1)
        self.tmux.send(self.run, '$(echo injection)')
        time.sleep(.1)
        self.assertNotIn('GOT:', self.tmux.inspect(self.run)['text'])
        self.tmux.send(self.run, '', submit=True)
        time.sleep(.1)
        self.assertIn('GOT:$(echo injection)', self.tmux.inspect(self.run)['text'])

    def test_signal_exit_status_is_preserved(self):
        self.run['pane_id'] = self.tmux.launch(self.run, ['/usr/bin/python3', '-c',
            'import os,signal; os.kill(os.getpid(),signal.SIGTERM)'])
        for _ in range(40):
            info = self.tmux.inspect(self.run)
            if info['exit_code'] is not None:
                break
            time.sleep(.02)
        self.assertEqual(info['exit_code'], 143)

    def test_optional_logfile_uses_literal_path_and_private_mode(self):
        from pathlib import Path
        logfile = Path(self.directory.name) / "logs" / "quoted ' file; log"
        self.run['config'] = {'logfile': str(logfile)}
        self.run['pane_id'] = self.tmux.launch(self.run, ['/usr/bin/python3', '-u', '-c',
            "import time; time.sleep(.15); print('LOGGED'); time.sleep(.2)"])
        for _ in range(40):
            if logfile.exists() and 'LOGGED' in logfile.read_text():
                break
            time.sleep(.02)
        self.assertIn('LOGGED', logfile.read_text())
        self.assertEqual(logfile.stat().st_mode & 0o777, 0o600)
