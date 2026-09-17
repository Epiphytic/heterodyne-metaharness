import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.capabilities import apply_capabilities


class CapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'CODEX_HOME': str(self.home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.run = {'agent': 'codex', 'workdir': str(self.home)}

    def catalog(self, window=272000):
        (self.home / 'config.toml').write_text('model="selected"\n')
        (self.home / 'models_cache.json').write_text(json.dumps({'models': [
            {'slug': 'selected', 'context_window': window},
            {'slug': 'small', 'context_window': 64000}]}))

    def test_actual_native_window_half_and_idempotency(self):
        self.catalog()
        config = {'extra_args': ['-c', 'sandbox_mode="workspace-write"']}
        result = apply_capabilities(self.run, config)
        self.assertEqual(result['compact_token_limit'], 136000)
        self.assertEqual(result['model'], 'selected')
        first = list(config['extra_args'])
        apply_capabilities(self.run, config)
        self.assertEqual(first, config['extra_args'])
        self.assertIn('sandbox_mode="workspace-write"', first)
        self.assertFalse(any(arg.startswith('model_context_window=') for arg in first))

    def test_large_window_is_capped_at_500k(self):
        self.catalog(1050000)
        self.assertEqual(apply_capabilities(self.run, {})['compact_token_limit'], 500000)

    def test_model_arg_and_project_model_override_base(self):
        self.catalog()
        project = self.home / '.codex'
        project.mkdir()
        (project / 'config.toml').write_text('model="small"')
        self.assertEqual(apply_capabilities(self.run, {})['compact_token_limit'], 32000)
        self.assertEqual(apply_capabilities(self.run, {'args': ['--model=selected']})[
            'compact_token_limit'], 136000)

    def test_profile_and_explicit_window_can_only_reduce_known_window(self):
        self.catalog()
        (self.home / 'compact.config.toml').write_text('model="small"')
        self.assertEqual(apply_capabilities(self.run, {'args': ['--profile', 'compact']})[
            'compact_token_limit'], 32000)
        for window, expected in ((100000, 50000), (1000000, 136000)):
            result = apply_capabilities(self.run, {'args': ['-c', f'model_context_window={window}']})
            self.assertEqual(result['compact_token_limit'], expected)

    def test_unknown_model_does_not_invent_native_window(self):
        result = apply_capabilities(self.run, {})
        self.assertIsNone(result['native_context_window'])
        self.assertEqual(result['compact_token_limit'], 32000)
        self.assertIn('unknown', result['context_source'])

    def test_existing_policy_is_replaced_without_duplicates(self):
        self.catalog()
        config = {'args': ['--config=web_search="disabled"', '-c',
                           'model_auto_compact_token_limit=900000']}
        apply_capabilities(self.run, config)
        self.assertEqual(config['extra_args'], ['-c', 'model_auto_compact_token_limit=136000',
                                                '-c', 'web_search="live"'])

    def test_claude_window_percent_and_existing_grants_preserved(self):
        run = {'agent': 'claude'}
        config = {'env': {'KEEP': 'yes'}, 'args': ['--allowedTools', 'Edit,Bash(git status)',
                                                 '--permission-mode', 'dontAsk',
                                                 '--disallowedTools', 'Bash(curl *)']}
        result = apply_capabilities(run, config)
        self.assertEqual(config['env']['CLAUDE_AUTOCOMPACT_PCT_OVERRIDE'], '50')
        self.assertEqual(config['env']['CLAUDE_CODE_AUTO_COMPACT_WINDOW'], '1000000')
        self.assertEqual(result['compact_token_ceiling'], 500000)
        self.assertIn('Edit', config['extra_args'][-1])
        self.assertIn('mcp__semble__search', config['extra_args'][-1])
        self.assertIn('Bash(curl *)', config['extra_args'])
        first = list(config['extra_args'])
        apply_capabilities(run, config)
        self.assertEqual(first, config['extra_args'])
        self.assertEqual(config['env']['KEEP'], 'yes')

    def test_other_adapters_untouched(self):
        config = {'args': ['hello']}
        self.assertFalse(apply_capabilities({'agent': 'hermes'}, config)['policy_applied'])
        self.assertEqual(config, {'args': ['hello']})


class InstallCapabilitiesTest(unittest.TestCase):
    def test_global_codex_edit_retains_tables_and_is_idempotent(self):
        import tomllib
        from install_capabilities import codex_defaults
        original = 'model="example"\nweb_search="cached"\n[features]\nhooks=true\n[projects."/safe"]\ntrust_level="trusted"\n'
        modified = codex_defaults(original, 136000)
        self.assertEqual(codex_defaults(modified, 136000), modified)
        data = tomllib.loads(modified)
        self.assertEqual(data['features'], {'hooks': True})
        self.assertEqual(data['projects']['/safe']['trust_level'], 'trusted')
        self.assertEqual(data['web_search'], 'live')

    def test_global_claude_and_mcp_install_preserves_deny_and_secrets(self):
        import install_capabilities
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / '.codex').mkdir()
            (home / '.claude').mkdir()
            (home / '.codex/config.toml').write_text('model="x"\n')
            original = {'env': {'PRIVATE_KEY': 'secret'}, 'permissions': {
                'deny': ['Bash(curl *)'], 'defaultMode': 'dontAsk', 'allow': ['Edit']}}
            (home / '.claude/settings.json').write_text(json.dumps(original))
            (home / '.claude.json').write_text(json.dumps({'secret': 'kept'}))
            def installer(*args, **kwargs):
                path = home / '.codex/config.toml'
                if 'mcp_servers' not in path.read_text():
                    path.write_text(path.read_text() + '\n[mcp_servers.semble]\ncommand="uvx"\n')
                path = home / '.claude.json'
                obj = json.loads(path.read_text())
                obj['mcpServers'] = {'semble': {'command': 'uvx'}}
                path.write_text(json.dumps(obj))
            with patch('install_capabilities.Path.home', return_value=home), \
                    patch.dict(os.environ, {'CODEX_HOME': str(home / '.codex')}), \
                    patch('install_capabilities.subprocess.run', side_effect=installer):
                install_capabilities.install()
                before = (home / '.claude/settings.json').read_text()
                install_capabilities.install()
                self.assertEqual((home / '.claude/settings.json').read_text(), before)
            settings = json.loads(before)
            self.assertEqual(settings['env']['PRIVATE_KEY'], 'secret')
            self.assertEqual(settings['permissions']['deny'], ['Bash(curl *)'])
            self.assertEqual(settings['permissions']['defaultMode'], 'dontAsk')
            self.assertEqual(json.loads((home / '.claude.json').read_text())['secret'], 'kept')
