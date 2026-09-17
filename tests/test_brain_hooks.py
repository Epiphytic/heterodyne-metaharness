import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from harness.brain_hooks import handle, hermes_origin, session_key
from harness.store import Store
from install_brain import plan, apply

ROOT = Path(__file__).resolve().parents[1]


class HooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'hermes'
        self.home.mkdir()
        self.spec = self.root / 'spec'
        self.spec.mkdir()
        (self.spec / 'README.md').write_text('normative')
        (self.home / 'config.yaml').write_text('plugins:\n  enabled: [marmot]\n')
        self.store = Store(self.home)
        self.addCleanup(self.store.db.close)
        with sqlite3.connect(self.home / 'state.db') as db:
            db.execute('CREATE TABLE sessions(id TEXT,parent_session_id TEXT,end_reason TEXT)')
            db.executemany('INSERT INTO sessions VALUES (?,?,?)', [
                ('first', None, 'compression'), ('compact', 'first', None),
                ('fork', 'compact', None)])
        self.env = patch.dict(os.environ, {'HERMES_HOME': str(self.home)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_hermes_exact_turn_receipt_and_compression(self):
        payload = {'session_id': 'first', 'turn_id': 'one', 'hook_event_name': 'pre_llm_call'}
        notice = handle(self.store, 'hermes', payload, self.spec)
        post = dict(payload, hook_event_name='post_llm_call', assistant_response='API failed', conversation_history=[])
        self.assertFalse(handle(self.store, 'hermes', post, self.spec)['acknowledged'])
        post['conversation_history'] = [{'role': 'user', 'api_content': notice['context']},
                                        {'role': 'assistant', 'content': 'response'}]
        self.assertTrue(handle(self.store, 'hermes', post, self.spec)['acknowledged'])
        self.assertIsNone(handle(self.store, 'hermes', dict(payload, session_id='compact'), self.spec))
        self.assertIsNotNone(handle(self.store, 'hermes', dict(payload, session_id='fork'), self.spec))

    def test_coding_native_transcript_receipt(self):
        for provider, env_name, directory in [('codex', 'CODEX_HOME', 'sessions'),
                                               ('claude', 'CLAUDE_CONFIG_DIR', 'projects')]:
            home = self.root / provider
            path = home / directory / 'transcript.jsonl'
            path.parent.mkdir(parents=True)
            path.write_text('')
            with patch.dict(os.environ, {env_name: str(home)}):
                payload = {'session_id': 'native', 'turn_id': 'turn', 'hook_event_name': 'UserPromptSubmit',
                           'transcript_path': str(path)}
                notice = handle(self.store, provider, payload, self.spec)
                stop = dict(payload, hook_event_name='Stop')
                self.assertFalse(handle(self.store, provider, stop, self.spec)['acknowledged'])
                messages = [{'role': 'user', 'content': notice['context']},
                            {'role': 'assistant', 'content': [{'type': 'text', 'text': 'response'}]}]
                if provider == 'codex':
                    messages[0]['role'] = 'developer'
                    messages.insert(1, {'role': 'user', 'content': 'real prompt'})
                    records = [{'type': 'response_item', 'payload': item} for item in messages]
                else:
                    records = [{'type': item['role'], 'message': item} for item in messages]
                path.write_text(''.join(json.dumps(item)+'\n' for item in records))
                self.assertTrue(handle(self.store, provider, stop, self.spec)['acknowledged'])
                self.assertFalse(handle(self.store, provider, stop, self.spec)['acknowledged'])

    def test_install_preserves_existing_and_plugin_runs_from_unrelated_cwd(self):
        codex, claude = self.root / 'codex', self.root / 'claude'
        codex.mkdir(); claude.mkdir()
        (codex / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [{'hooks': [{'command': 'existing'}]}]}}))
        prepared = plan(self.home, codex, claude)
        quarantine = self.root / 'private-backup'
        apply(prepared, quarantine)
        self.assertEqual(apply(prepared, quarantine)['changed'], 0)
        self.assertEqual(plan(self.home, codex, claude)['files'], [])
        hooks = json.loads((codex / 'hooks.json').read_text())
        self.assertEqual(hooks['hooks']['Stop'][0]['hooks'][0]['command'], 'existing')
        # New process, unrelated cwd, no inherited PYTHONPATH; execute actual adapter.
        script = '''import importlib.util,json,sys
p=sys.argv[1]
spec=importlib.util.spec_from_file_location('session_sync_smoke',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
n=m.pre_llm_call(session_id='first',turn_id='real-hook')
assert 'brain-notice:' in n['context']
m.post_llm_call(session_id='first',turn_id='real-hook',conversation_history=[{'role':'user','api_content':n['context']},{'role':'assistant','content':'response'}])
assert m.pre_llm_call(session_id='first',turn_id='next') is None
print('hook invocation passed')
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.home / 'plugins/session-sync/__init__.py')],
                                cwd=self.root, env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('hook invocation passed', result.stdout)

    def test_actual_plugin_manager_invocation_when_native_available(self):
        if importlib.util.find_spec('hermes_cli') is None:
            self.skipTest('run with native Hermes venv for PluginManager smoke')
        codex, claude = self.root / 'codex', self.root / 'claude'
        codex.mkdir(); claude.mkdir()
        apply(plan(self.home, codex, claude), self.root / 'private')
        script = """from hermes_cli.plugins import PluginManager
import os
m=PluginManager(scope_key=os.environ['HERMES_HOME'])
m.discover_and_load()
results=m.invoke_hook('pre_llm_call',session_id='first',turn_id='native-smoke')
notices=[r['context'] for r in results if isinstance(r,dict) and 'context' in r]
assert len(notices)==1, results
m.invoke_hook('post_llm_call',session_id='first',turn_id='native-smoke',conversation_history=[{'role':'user','api_content':notices[0]},{'role':'assistant','content':'response'}])
assert not m.invoke_hook('pre_llm_call',session_id='first',turn_id='next')
print('native PluginManager invocation passed')
"""
        result = subprocess.run([sys.executable, '-c', script], cwd=self.root,
                                env=dict(os.environ), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('native PluginManager invocation passed', result.stdout)
