import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from harness import runtime_repair as repair


class RuntimeRepairTest(unittest.TestCase):
    def test_missing_version_and_origin_changes_block(self):
        before = {'plugin': {'version': '1', 'origin': {'url': 'file:///local'}}}
        self.assertTrue(repair.differences(before, {}))
        self.assertTrue(repair.differences(before, {'plugin': {'version': '1', 'origin': None}}))
        self.assertFalse(repair.differences(before, dict(before, extra={'version': '2'})))

    def test_sqlite_backup_includes_uncheckpointed_wal(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.db'
            with sqlite3.connect(source) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY)')
                db.execute("INSERT INTO sessions VALUES ('preserved')")
                db.commit()
                destination = Path(temp) / 'backup.db'
                record = repair.database_backup(source, destination)
                with sqlite3.connect(destination) as saved:
                    self.assertEqual(saved.execute('SELECT id FROM sessions').fetchall(), [('preserved',)])
                self.assertEqual(record['sha256'], repair.sha(destination))
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_apply_requires_quiet_window(self):
        with self.assertRaisesRegex(ValueError, 'quiet-window'):
            repair.apply(Mock(), Path('/unused'), Path('/unused'), [], False)

    def test_interrupted_cutover_never_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = root / 'repair.json'
            repair.save(record, {'root': str(root), 'state': 'cutover-intent'})
            native = Mock()
            with self.assertRaisesRegex(ValueError, 'Interrupted'):
                repair.apply(native, root, record, [root / 'db'], True)
            native._cut_over_candidate.assert_not_called()

    def test_applied_repeat_does_not_stage_or_cut_over(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = root / 'repair.json'
            repair.save(record, {'root': str(root), 'state': 'applied'})
            native = Mock()
            native.probe_sqlite_runtime.return_value = SimpleNamespace(wal_reset_vulnerable=False)
            self.assertEqual(repair.apply(native, root, record, [root / 'db'], True)['state'], 'applied')
            native._cut_over_candidate.assert_not_called()
            native.probe_sqlite_runtime.return_value = None
            with self.assertRaisesRegex(ValueError, 'drifted'):
                repair.apply(native, root, record, [root / 'db'], True)

    def test_staged_repeat_does_not_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = root / 'repair.json'
            repair.save(record, {'state': 'staged'})
            native = Mock()
            self.assertEqual(repair.stage(native, root, 'uv', record), {'state': 'staged'})
            native._install_safe_python_generation.assert_not_called()

    def test_runtime_archive_restorable_and_failure_prevents_return(self):
        import tarfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'root'
            (root / 'venv').mkdir(parents=True)
            (root / 'venv' / 'pyvenv.cfg').write_text('original')
            base = Path(temp) / 'base'
            base.mkdir()
            (base / 'python').write_text('runtime')
            quarantine = Path(temp) / 'backup'
            quarantine.mkdir()
            info = SimpleNamespace(base_prefix=base)
            result = repair.backup_runtime(root, quarantine, info, [])
            with tarfile.open(result['runtime']['path']) as saved:
                self.assertEqual(saved.extractfile('venv/pyvenv.cfg').read(), b'original')
                self.assertEqual(saved.extractfile('base-python/python').read(), b'runtime')
            self.assertEqual(result['runtime']['sha256'], repair.sha(quarantine / 'runtime.tar'))
            with self.assertRaises(FileExistsError):
                repair.backup_runtime(root, quarantine, info, [])

    def test_packaging_patch_preserves_content_and_repeats(self):
        from prepare_runtime_packaging import manifest, MODULES
        import tomllib
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            text = '# retained\n[tool.setuptools]\npy-modules = [\n  "hermes_state",\n]\n'
            (root / 'pyproject.toml').write_text(text)
            for name in MODULES:
                (root / (name + '.py')).write_text('# existing source')
            result = manifest(root)
            self.assertEqual(len(result['files']), 1)
            updated = result['files'][0]['replacement']
            self.assertTrue(updated.startswith('# retained\n'))
            self.assertEqual(len(tomllib.loads(updated)['tool']['setuptools']['py-modules']), 8)
            (root / 'pyproject.toml').write_text(updated)
            self.assertEqual(manifest(root), {'files': []})

    def test_failed_candidate_records_generation_for_retry(self):
        from dataclasses import dataclass
        @dataclass
        class Info:
            wal_reset_vulnerable: bool = True
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / '.hermes-runtime/python'
            python = store / 'generation/cpython/bin/python'
            python.parent.mkdir(parents=True)
            python.write_text('fixture')
            record = root / 'repair.json'
            native = Mock()
            native.probe_sqlite_runtime.side_effect = [Info(), Info(), Info(False)]
            native.managed_python_install_dir.return_value = store
            native._install_safe_python_generation.return_value = (store / 'generation', python, Info(False))
            native._stage_candidate_venv.return_value = None
            with patch('prepare_runtime_packaging.manifest', return_value={'files': []}), patch.object(repair, 'inventory', return_value={}):
                for attempt in range(2):
                    with self.assertRaisesRegex(ValueError, 'retained for retry'):
                        repair.stage(native, root, 'uv', record)
            self.assertEqual(native._install_safe_python_generation.call_count, 1)
            self.assertEqual(native._stage_candidate_venv.call_count, 2)
            self.assertEqual(json.loads(record.read_text())['state'], 'provisioned')
