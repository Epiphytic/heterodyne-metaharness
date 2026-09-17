import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

from harness.agents import Adapter
from harness.cleanup import apply
from harness.governance import validate
from harness.maintenance import enqueue_task
from harness.store import Store


class MaintenanceTests(unittest.TestCase):
    def test_fork_and_exact_resume(self):
        source = '01a0ac8d-fce3-7a61-aadf-b1ead3b4ad81'
        run = {'config': {'fork_session_id': source}, 'prompt': 'initial'}
        self.assertEqual(Adapter('codex').argv(run), ['codex', 'fork', '--', source, 'initial'])
        with self.assertRaises(ValueError): Adapter('codex').argv(run)
        run['native_session_id'] = 'successor'
        self.assertEqual(Adapter('codex').argv(run, True), ['codex', 'resume', '--', 'successor'])
        self.assertNotIn('fork', Adapter('codex').argv(run))
        run['config']['fork_session_id'] = '--last'; del run['native_session_id']
        with self.assertRaises(ValueError): Adapter('codex').argv(run)

    def test_cleanup_repeat_and_drift(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'active'/'old.md'; source.parent.mkdir(); source.write_text('obsolete')
            manifest = {'files': [{'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}
            self.assertEqual(apply(manifest, root/'offline')['changed'], 1)
            self.assertEqual(apply(manifest, root/'offline')['changed'], 0)
            source.write_text('new facts')
            with self.assertRaises(ValueError): apply(manifest, root/'offline')
            self.assertEqual(source.read_text(), 'new facts')

    def test_admission_is_idempotent_and_does_not_send(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d)
            run = dict(id='uuid', name='hermes-maintenance', repo=d, workdir=d, agent='codex', state='idle', persistent=True, group_id='group', created_at=1)
            with store.db: store.save(run)
            config = {'maintenance': {'run_id':'uuid','group_id':'group'}}
            supervisor = Mock()
            with patch('harness.maintenance.Beads') as beads:
                beads.return_value.create.return_value = {'id':'task'}
                enqueue_task(store,supervisor,config,'Title','body','key')
                enqueue_task(store,supervisor,config,'Title','body','key')
            self.assertEqual(store.db.execute('select count(*) from inbox').fetchone()[0],1)
            supervisor.submit.assert_not_called()

    def test_completed_history_survives_spec_revision(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'spec').mkdir(); (root/'governance/decisions').mkdir(parents=True)
            (root/'spec/README.md').write_text('# Active\n')
            (root/'evidence.txt').write_text('Deployment and dependencies verified in isolated fixture')
            def git(*args):
                return subprocess.check_output(['git','-C',d,*args],stderr=subprocess.DEVNULL).decode().strip()
            git('init');git('add','.');git('-c','user.name=Test','-c','user.email=test@example.com','commit','-m','fixture')
            revision=git('rev-parse','HEAD')
            def artifact(path):
                return dict(commit=revision,path=path,sha256=hashlib.sha256((root/path).read_bytes()).hexdigest(),kind='execution-evidence')
            event=dict(state='completed',at='3', **{field:[artifact('spec/README.md' if field=='spec' else 'evidence.txt')] for field in ('spec','implementation','deployment','dependencies','verification')})
            record={'events':[{'state':'proposed','at':'1'},{'state':'approved','at':'2','by':'fixture','evidence':'test','proposal_sha256':hashlib.sha256(b'').hexdigest()},event,{'state':'superseded','at':'4','evidence':'D2'}]}
            (root/'governance/decisions/D1.json').write_text(json.dumps(record))
            (root/'spec/README.md').write_text('# Updated by D2\n')
            (root/'governance/decisions/D2.json').write_text(json.dumps({'events':[{'state':'proposed','at':'4'}]}))
            self.assertEqual(validate(root),[])
            git('add','.');git('-c','user.name=Test','-c','user.email=test@example.com','commit','-m','approved history')
            record['events'][1]['by']='altered approval'
            (root/'governance/decisions/D1.json').write_text(json.dumps(record))
            self.assertTrue(any('append-only' in e for e in validate(root)))
            (root/'spec/README.md').unlink()
            self.assertTrue(validate(root))

    def test_nested_links_and_runtime_direction(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'spec/nested').mkdir(parents=True);(root/'bin').mkdir()
            (root/'spec/README.md').write_text('[Nested](nested/contract.md#rules)')
            (root/'spec/nested/contract.md').write_text('# Rules\n')
            self.assertEqual(validate(root),[])
            (root/'spec/nested/contract.md').write_text('# Changed\n')
            (root/'bin/command').write_text('read governance/decisions/example.json')
            errors=validate(root)
            self.assertTrue(any('anchor' in error for error in errors))
            self.assertTrue(any('runtime historical' in error for error in errors))

    def test_brain_backup_stays_outside_publishable_tree(self):
        from install_brain_pointer import install
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);brain=root/'brain';brain.mkdir();backup=root/'private'
            script=brain/'sync-brain.sh'
            original='# Abort the sync if a present memory store cannot be backed up safely.\n'
            script.write_text(original);script.chmod(0o755)
            with self.assertRaises(ValueError): install(brain,brain/'backups')
            self.assertTrue(install(brain,backup))
            self.assertFalse(install(brain,backup))
            self.assertEqual([p.name for p in brain.iterdir()],['sync-brain.sh'])
            self.assertEqual((backup/hashlib.sha256(original.encode()).hexdigest()).read_text(),original)
            self.assertEqual(script.stat().st_mode & 0o777,0o755)

    def test_persistent_idle_suppresses_heartbeat(self):
        from harness.supervisor import Supervisor
        supervisor=object.__new__(Supervisor)
        supervisor.report=Mock(); supervisor.persist=Mock()
        supervisor.clock=Mock(return_value=1000)
        supervisor.store=Mock()
        supervisor.store.db.execute.return_value.fetchone.return_value=None
        run={'id':'idle-run','persistent':True,'state':'idle'}
        supervisor.heartbeat(run)
        supervisor.report.assert_called_once()  # Initial state announcement.
        supervisor.clock.return_value=2000
        supervisor.heartbeat(run)
        supervisor.report.assert_called_once()  # Unchanged idle produces nothing.

    def test_guard_blocks_source_keeps_memory(self):
        path=Path(__file__).resolve().parents[1]/'plugins/hermes-maintenance/__init__.py'
        spec=importlib.util.spec_from_file_location('guard',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        self.assertEqual(mod.pre_tool_call('write_file',{'path':'/home/operator/.hermes/plugins/new.py'})['action'],'block')
        self.assertIsNone(mod.pre_tool_call('write_file',{'path':'/home/operator/.hermes/memories/MEMORY.md'}))
        self.assertIsNone(mod.pre_tool_call('terminal',{'command':'git status','cwd':'/home/operator/.hermes/plugins'}))
        self.assertIsNone(mod.pre_tool_call('terminal',{'command':"sed -n '1,20p' foo.py",'cwd':'/home/operator/.hermes/plugins'}))
        self.assertEqual(mod.pre_tool_call('patch',{'path':'/home/operator/.hermes/plugins/a.py','old_string':'x','new_string':'y'})['action'],'block')
        self.assertIsNone(mod.pre_tool_call('read_file',{'path':'/home/operator/.hermes/plugins/new.py'}))
