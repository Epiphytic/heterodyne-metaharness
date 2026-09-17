import json
import os
from unittest.mock import patch
from pathlib import Path
import tempfile
import tomllib
import unittest

from harness.hook_config import install_codex_hook, claude_settings, claude_args, _trust_text, install_queue_hooks

FAKE_CODEX = '''#!/usr/bin/env python3
import json,os,sys,tomllib
from pathlib import Path
home=Path(os.environ['CODEX_HOME'])
for line in sys.stdin:
 r=json.loads(line)
 result={}
 if r['method']=='hooks/list':
  data=json.loads((home/'hooks.json').read_text())
  config=tomllib.loads((home/'config.toml').read_text())
  hooks=[]
  for event,native,key_name in [('SessionStart','sessionStart','session_start'),('Stop','stop','stop')]:
   for i,group in enumerate(data['hooks'].get(event, [])):
    for j,handler in enumerate(group['hooks']):
     key=f'{home}/hooks.json:{key_name}:{i}:{j}'
     import hashlib
     digest='sha256:'+hashlib.sha256(json.dumps(handler,sort_keys=True).encode()).hexdigest()
     trust=config.get('hooks',{}).get('state',{}).get(key,{}).get('trusted_hash')
     hooks.append(dict(command=handler['command'],eventName=native,sourcePath=str(home/'hooks.json'),
       key=key,currentHash=digest,matcher=group.get('matcher'),timeoutSec=handler.get('timeout',30),
       enabled=True,trustStatus='trusted' if trust==digest else 'untrusted'))
  result={'data':[{'hooks':hooks}]}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
'''


class HookConfigTests(unittest.TestCase):
    def test_preserves_existing_hooks_and_trust_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            fake=home/'codex'
            fake.write_text(FAKE_CODEX)
            fake.chmod(0o700)
            old={'matcher':'startup','hooks':[{'type':'command','command':'bd prime'}]}
            (home/'hooks.json').write_text(json.dumps({'description':'keep', 'hooks':{'SessionStart':[old]}}))
            (home/'config.toml').write_text('model="keep"\n[hooks.state."original"]\ntrusted_hash="original-hash"\n')
            result=install_codex_hook('/bin/workstream-session auto',home,home,str(fake))
            first=(home/'hooks.json').read_text(),(home/'config.toml').read_text()
            install_codex_hook('/bin/workstream-session auto',home,home,str(fake))
            self.assertEqual(first,((home/'hooks.json').read_text(),(home/'config.toml').read_text()))
            hooks=json.loads(first[0])
            self.assertEqual(hooks['hooks']['SessionStart'][0],old)
            self.assertEqual(len(hooks['hooks']['SessionStart']),2)
            config=tomllib.loads(first[1])
            self.assertEqual(config['model'],'keep')
            self.assertEqual(config['hooks']['state']['original']['trusted_hash'],'original-hash')
            self.assertEqual(config['hooks']['state'][result['key']]['trusted_hash'],result['hash'])

    def test_changed_hash_only_updates_selected_trust(self):
        text='[hooks.state."key"]\ntrusted_hash="old"\nenabled=true\n\n[other]\nvalue="keep"\n'
        result=tomllib.loads(_trust_text(text,'key','sha256:'+'b'*64))
        self.assertTrue(result['hooks']['state']['key']['enabled'])
        self.assertEqual(result['other']['value'],'keep')
        self.assertEqual(result['hooks']['state']['key']['trusted_hash'],'sha256:'+'b'*64)

    def test_claude_existing_settings_permissions_hooks_preserved_idempotently(self):
        original = {'permissions': {'deny': ['Bash(rm *)']},
                    'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': 'bd prime'}]}]}}
        args = ['--permission-mode', 'manual', '--settings', json.dumps(original)]
        merged = claude_args(args, '/bin/workstream-session auto')
        self.assertEqual(merged, claude_args(merged, '/bin/workstream-session auto'))
        self.assertEqual(merged[:2], args[:2])
        settings = json.loads(merged[3])
        self.assertEqual(settings['permissions'], original['permissions'])
        self.assertEqual(settings['hooks']['SessionStart'][0], original['hooks']['SessionStart'][0])
        self.assertEqual(len(settings['hooks']['SessionStart']), 2)

    def test_claude_settings_file_is_read_without_modification(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            original = json.dumps({'permissions': {'allow': ['Read']}, 'model': 'keep'})
            path.write_text(original)
            args = claude_args(['--settings=' + str(path)], '/bin/workstream-session auto')
            config = json.loads(args[0].split('=', 1)[1])
            self.assertEqual(config['permissions'], {'allow': ['Read']})
            self.assertEqual(path.read_text(), original)
            self.assertEqual(args, claude_args(args, '/bin/workstream-session auto'))
        with self.assertRaisesRegex(ValueError, 'multiple'):
            claude_args(['--settings', '{}', '--settings={}'], 'hook')

    def test_claude_settings_contains_scoped_command(self):
        config=json.loads(claude_settings('/bin/workstream-session auto'))
        self.assertEqual(config['hooks']['SessionStart'][0]['hooks'][0]['command'], '/bin/workstream-session auto')


class QueueHookInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.codex, self.claude = self.root / 'codex-home', self.root / 'claude-home'
        self.codex.mkdir()
        self.claude.mkdir()
        executable = self.root / 'codex'
        executable.write_text(FAKE_CODEX)
        executable.chmod(0o700)
        env = patch.dict(os.environ, {'PATH': str(self.root) + os.pathsep + os.environ['PATH']})
        env.start()
        self.addCleanup(env.stop)
        self.original = {}
        for agent, home, filename in [('codex', self.codex, 'hooks.json'),
                                      ('claude', self.claude, 'settings.json')]:
            groups = {}
            for event in ('SessionStart', 'Stop'):
                command = '/home/operator/repos/beads-task-queue/bin/pickup-hook ' + event + ' ' + agent
                groups[event] = [
                    {'hooks': [{'type': 'command', 'command': 'keep-before', 'timeout': 11}]},
                    {'matcher': 'startup|resume' if event == 'SessionStart' else None,
                     'hooks': [{'type': 'command', 'command': 'keep-sibling', 'timeout': 12},
                               {'type': 'command', 'command': command, 'timeout': 45}]},
                    {'hooks': [{'type': 'command', 'command': 'keep-after', 'timeout': 13}]}]
            data = {'hooks': groups, 'permissions': {'deny': ['Bash(rm *)'], 'defaultMode': 'dontAsk'},
                    'env': {'PRIVATE': 'preserve'}}
            self.original[agent] = json.loads(json.dumps(data))
            (home / filename).write_text(json.dumps(data))
        (self.codex / 'config.toml').write_text(
            'model="keep"\n[hooks.state."unrelated"]\ntrusted_hash="keep-hash"\nenabled=false\n')

    def test_migration_retains_indices_policy_and_hash_trust_idempotently(self):
        install_queue_hooks('/bin/workstream-queue-hook', self.codex, self.claude)
        paths = [self.codex / 'hooks.json', self.codex / 'config.toml', self.claude / 'settings.json']
        first = [path.read_text() for path in paths]
        for agent, index in [('codex', 0), ('claude', 2)]:
            migrated = json.loads(first[index])
            expected = self.original[agent]
            for event in ('SessionStart', 'Stop'):
                expected['hooks'][event][1]['hooks'][1]['command'] = '/bin/workstream-queue-hook ' + event + ' ' + agent
            self.assertEqual(migrated, expected)
        config = tomllib.loads(first[1])
        self.assertEqual(config['model'], 'keep')
        self.assertEqual(config['hooks']['state']['unrelated'],
                         {'trusted_hash': 'keep-hash', 'enabled': False})
        for event in ('session_start', 'stop'):
            key = f'{self.codex}/hooks.json:{event}:1:1'
            self.assertRegex(config['hooks']['state'][key]['trusted_hash'], r'^sha256:[a-f0-9]{64}$')
        self.assertEqual(len(config['hooks']['state']), 3)
        install_queue_hooks('/bin/workstream-queue-hook', self.codex, self.claude)
        self.assertEqual(first, [path.read_text() for path in paths])

    def test_ambiguous_queue_hook_refuses_before_mutating_provider(self):
        path = self.codex / 'hooks.json'
        data = json.loads(path.read_text())
        data['hooks']['Stop'].append(data['hooks']['Stop'][1])
        path.write_text(json.dumps(data))
        before = path.read_text()
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            install_queue_hooks('/bin/workstream-queue-hook', self.codex, self.claude)
        self.assertEqual(path.read_text(), before)

    def test_missing_claude_hooks_is_reported(self):
        (self.claude / 'settings.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            install_queue_hooks('/bin/workstream-queue-hook', self.codex, self.claude)


if __name__=='__main__':
    unittest.main()
