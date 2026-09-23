"""Launch-policy re-apply edge cases without live worker processes."""
import unittest
from types import SimpleNamespace

from harness import provisioning


class ProvisioningTest(unittest.TestCase):
    def test_reapply_deduplicates_roots_and_tracks_owned_cwd_once(self):
        args = ['-c', 'sandbox_workspace_write.writable_roots=["/tmp/one", "/tmp/one"]',
                '--cd=/old', '-c', 'sandbox_workspace_write.writable_roots=["/tmp/two"]']
        first = provisioning._normalize_args(args, '/new', task_checkout=True)
        self.assertEqual(first, ['-c',
            'sandbox_workspace_write.writable_roots=["/tmp/one", "/tmp/two"]', '-C', '/new'])
        self.assertEqual(provisioning._normalize_args(first, '/new', task_checkout=True), first)

    def test_process_shim_argv_and_secret_env_values_are_not_in_diff(self):
        expected = {'argv': ['codex', 'resume', '--', 'native'],
                    'env': {'API_TOKEN': 'updated'}, 'workdir': '/checkout'}
        actual = {'argv': ['/usr/bin/node', '/opt/bin/codex', 'resume', '--', 'native'],
                  'env': {'API_TOKEN': 'old'}, 'workdir': '/checkout'}
        self.assertTrue(provisioning.changed(actual, expected))
        details = provisioning.diff(actual, expected)
        self.assertFalse(details['argv_changed'])
        self.assertEqual(details['env_keys_changed'], ['API_TOKEN'])
        self.assertNotIn('updated', str(details))

    def test_ended_requires_current_matched_completion_not_only_idle(self):
        run = {'state': 'active', 'native_turn_state': 'idle',
               'native_turn_key': ['native', 'turn-2'], 'observation': {'pane_alive': True}}
        self.assertFalse(provisioning.ended(run))
        run['native_completion'] = {'native': 'native', 'turn': 'turn-1', 'applied': True}
        self.assertFalse(provisioning.ended(run))
        run['native_completion']['turn'] = 'turn-2'
        self.assertTrue(provisioning.ended(run))
        run['observed_state'] = 'awaiting_question'
        self.assertFalse(provisioning.ended(run))

    def test_existing_codex_fork_previews_original_command_without_identity_mutation(self):
        source = '22222222-2222-4222-8222-222222222222'
        run = {'id': 'fork', 'agent': 'codex', 'workdir': '/tmp',
               'config': {'fork_session_id': source}, 'launch_prompt': 'original',
               'native_session_id': '11111111-1111-4111-8111-111111111111',
               'fork_attempted': True, 'launch_recovering': False}
        supervisor = SimpleNamespace(beads=SimpleNamespace(enabled=False), config={})
        spec = provisioning.desired(supervisor, run)
        self.assertEqual(spec['argv'][1], 'fork')
        self.assertIn(source, spec['argv'])
        self.assertEqual(run['native_session_id'], '11111111-1111-4111-8111-111111111111')
