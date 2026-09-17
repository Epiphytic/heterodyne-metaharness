import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from harness.cli import parser, task_dispatch
from harness.queue_hook import invoke


class QueueHookTest(unittest.TestCase):
    def setUp(self):
        self.run = {'id': str(uuid.uuid4()), 'agent': 'codex', 'workdir': '/tmp/work', 'beads': {}}
        self.store = Mock()
        self.store.get.return_value = self.run
        self.env = {'HERMES_WORKSTREAM_RUN': self.run['id']}
        self.config = {'beads': {'enabled': True, 'workstream': 'test'}}
        self.payload = {'session_id': 'native-id', 'cwd': '/tmp/work', 'source': 'startup'}

    @patch('harness.queue_hook.subprocess.run')
    def test_unmanaged_delegates_payload_unchanged(self, process):
        process.return_value = SimpleNamespace(returncode=0, stdout='{}')
        invoke('SessionStart', 'codex', self.payload, env={})
        self.assertEqual(self.payload, json.loads(process.call_args.kwargs['input']))

    @patch('harness.queue_hook.Beads')
    @patch('harness.queue_hook.subprocess.run')
    def test_allowed_managed_uses_facade_for_both_agents(self, process, beads):
        beads.return_value.enabled = True
        beads.return_value.context.return_value = 'stable facade'
        beads.return_value.ready.return_value = [{'id': 'btq-one'}]
        self.run['beads']['pickup_enabled'] = True
        for agent in ('codex', 'claude'):
            self.run['agent'] = agent
            result = invoke('SessionStart', agent, self.payload, env=self.env, store=self.store, config=self.config)
            self.assertIn('btq-one', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(2, beads.return_value.ready.call_count)
        process.assert_not_called()

    @patch('harness.queue_hook.Beads')
    @patch('harness.queue_hook.subprocess.run')
    def test_direct_work_and_reboot_pause_and_never_pickup(self, process, beads):
        beads.return_value.enabled = True
        beads.return_value.context.return_value = 'durable task'
        for state in ({}, {'pickup_enabled': True, 'recovery_required': True}):
            self.run['beads'] = state
            result = invoke('SessionStart', 'codex', self.payload, env=self.env, store=self.store, config=self.config)
            self.assertIn('hookSpecificOutput', result)
        self.assertEqual(2, beads.return_value.pause.call_count)
        process.assert_not_called()

    @patch('harness.queue_hook.Beads')
    @patch('harness.queue_hook.subprocess.run')
    def test_compact_only_restores_context(self, process, beads):
        beads.return_value.enabled = True
        beads.return_value.context.return_value = 'same claim'
        self.payload['source'] = 'compact'
        invoke('SessionStart', 'codex', self.payload, env=self.env, store=self.store, config=self.config)
        beads.return_value.pause.assert_not_called()
        process.assert_not_called()

    @patch('harness.queue_hook.subprocess.run')
    def test_bad_cwd_rejected_before_hook(self, process):
        self.payload['cwd'] = '/tmp/other'
        with self.assertRaises(ValueError):
            invoke('SessionStart', 'codex', self.payload, env=self.env, store=self.store, config=self.config)
        process.assert_not_called()

    @patch('harness.cli.Beads')
    def test_task_cli_create_forwards_key_and_description(self, facade):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / 'task.txt'
            task.write_text('Acceptance: tested')
            args = parser().parse_args(['task', 'demo', '--workstream', 'test', 'create',
                                       '--title', 'Implement', '--file', str(task), '--key', 'message-42'])
            facade.return_value.config = {}
            supervisor = Mock()
            task_dispatch(args, self.store, supervisor, self.config)
            facade.return_value.create.assert_called_once_with(
                self.run, 'Implement', 'Acceptance: tested', key='message-42', kind='task', metadata={}, approval_id=None)
            supervisor.persist.assert_called_once_with(self.run)

    @patch('harness.cli.Beads')
    def test_task_cli_claim_persists_mutated_identity(self, facade):
        args = parser().parse_args(['task', 'demo', 'claim', 'btq-task'])
        supervisor = Mock()
        task_dispatch(args, self.store, supervisor, self.config)
        facade.return_value.claim.assert_called_once_with(self.run, 'btq-task')
        supervisor.persist.assert_called_once_with(self.run)

    @patch('harness.cli.Beads')
    def test_recovery_clears_guards_without_restarting_task(self, facade):
        import tempfile
        self.run.update(resume_required=True, state='awaiting_resume')
        self.run['beads'].update(recovery_required=True, pickup_enabled=True)
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'recovery.txt'
            evidence.write_text('Verified owned task and interrupted filesystem effects')
            args = parser().parse_args(['task', 'demo', 'recover', '--evidence-file', str(evidence)])
            supervisor = Mock()
            task_dispatch(args, self.store, supervisor, self.config)
        self.assertFalse(self.run['resume_required'])
        self.assertFalse(self.run['beads']['recovery_required'])
        self.assertFalse(self.run['beads']['pickup_enabled'])
        self.assertEqual('awaiting_resume', self.run['state'])
        facade.return_value.pause.assert_called_once_with(self.run)
        facade.return_value.resume.assert_not_called()
        supervisor.submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
