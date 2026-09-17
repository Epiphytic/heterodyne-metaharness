import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from harness.hook_config import install_codex_hook, claude_settings, claude_args, _trust_text

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
  for i,group in enumerate(data['hooks']['SessionStart']):
   for j,handler in enumerate(group['hooks']):
    key=f'{home}/hooks.json:session_start:{i}:{j}'
    digest='sha256:'+'a'*64
    trust=config.get('hooks',{}).get('state',{}).get(key,{}).get('trusted_hash')
    hooks.append(dict(command=handler['command'],eventName='sessionStart',sourcePath=str(home/'hooks.json'),
      key=key,currentHash=digest,matcher=group.get('matcher'),timeoutSec=handler.get('timeout'),
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


if __name__=='__main__':
    unittest.main()
