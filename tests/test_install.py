import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.store import Store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTest(unittest.TestCase):
    def test_install_twice_preserves_soul_and_backups(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'HOME': tmp}):
            home = Path(tmp) / '.hermes'; home.mkdir()
            soul = home / 'SOUL.md'; soul.write_text('Original identity\n')
            (home / 'scripts').mkdir()
            old = home / 'scripts/workstream-stop'; old.write_text('old unsafe cleanup\n')
            installer.deploy(home, enable=False)
            before = {p: p.read_bytes() for p in (home/'scripts').iterdir()}
            installer.deploy(home, enable=False)
            self.assertEqual(before, {p:p.read_bytes() for p in (home/'scripts').iterdir()})
            self.assertTrue(soul.read_text().startswith('Original identity'))
            self.assertEqual(soul.read_text().count('## Durable workstream discipline'), 1)
            self.assertEqual((home/'scripts/workstream-stop.pre-durable-harness').read_text(), 'old unsafe cleanup\n')
            self.assertIn('KillMode=process', (Path(tmp)/'.config/systemd/user/hermes-workstreams.service').read_text())

    def test_stable_alias_tracks_successor_without_changing_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            run = dict(id='id', name='alpha', agent='codex', workdir=tmp,
                       state='active', created_at=1, group_id='group', native_session_id='old')
            with store.db: store.save(run)
            run['native_session_id'] = 'new'
            with store.db: store.save(run)
            self.assertEqual(store.get('workstream-alpha-worker')['native_session_id'], 'new')
            history = store.db.execute('SELECT native_id,previous_id FROM native_sessions ORDER BY first_seen').fetchall()
            self.assertEqual([tuple(row) for row in history], [('old', None), ('new', 'old')])
            self.assertEqual(store.get('workstream-alpha-worker')['group_id'], 'group')
            store.db.close()
