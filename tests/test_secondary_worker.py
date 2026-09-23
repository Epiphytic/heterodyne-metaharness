import copy
import unittest
from unittest.mock import Mock, patch

from harness import secondary_worker as secondary
from harness.beads import BeadsError
from tests import test_task_blockers as fixtures


@unittest.skipIf(fixtures.PrivateKey is None, 'install requirements-blockers.txt')
class SecondaryTest(fixtures.BlockerTest):
    def secondary_setup(self):
        self.setup_blocker()
        self.run.update(native_session_id='primary-native', native_turn_state='idle', tmux_session='test')
        self.config['blockers']['secondary_enabled'] = True
        self.supervisor.config = {'beads': self.config}
        self.supervisor.beads = self.beads
        self.supervisor.store = self.store
        self.supervisor.clock.return_value = 100
        self.issues['btq-next'] = dict(id='btq-next', title='Independent task', metadata={}, status='open',
                                      assignee='', labels=[], dependencies=[])
        self.beads.ready = Mock(return_value=[self.issues['btq-next']])
        original = self.queue.bd.side_effect
        def bd(*args):
            if args[:2] == ('list', '--assignee'):
                return [i for i in self.issues.values() if i.get('assignee') == self.queue.worker
                        and i.get('status') == 'in_progress']
            if args == ('update', 'btq-next', '--claim'):
                self.issues['btq-next'].update(status='in_progress', assignee=self.queue.worker)
            else:
                return original(*args)
        self.queue.bd.side_effect = bd

    def test_secondary_claim_isolated_launch_and_retained_primary(self):
        self.secondary_setup()
        original = copy.deepcopy(self.run)
        with patch.object(secondary, 'git', return_value=''), patch.object(secondary, 'prepare',
                return_value={'path': '/owned-next'}) as prepare:
            slot = secondary.start(self.supervisor, self.run, 'btq-next')
        self.assertEqual(slot['beads']['issue_id'], 'btq-next')
        self.assertEqual(slot['workdir'], '/owned-next')
        self.assertEqual(self.run['beads']['issue_id'], original['beads']['issue_id'])
        self.assertEqual(self.run['native_session_id'], 'primary-native')
        self.supervisor.launch.assert_called_once_with(self.run, slot)
        self.assertEqual(slot['launch_prompt'], '')
        self.assertEqual(self.issues['btq-target']['status'], 'in_progress')
        with self.assertRaises(BeadsError):
            secondary.start(self.supervisor, self.run, 'btq-next')

    def test_recovery_busy_dirty_and_approval_prevent_launch(self):
        self.secondary_setup()
        for changed in ({'resume_required': True}, {'native_turn_state': 'working'},
                        {'observed_state': 'awaiting_approval'}):
            with self.subTest(changed=changed), self.assertRaises(BeadsError):
                secondary.start(self.supervisor, dict(self.run, **changed), 'btq-next')
        with patch.object(secondary, 'git', return_value=' M unfinished'), self.assertRaises(BeadsError):
            secondary.start(self.supervisor, self.run, 'btq-next')
        self.supervisor.launch.assert_not_called()

    def test_secondary_completion_then_parent_signed_unblock_and_resume(self):
        self.secondary_setup()
        with patch.object(secondary, 'git', return_value=''), patch.object(secondary, 'prepare',
                return_value={'path': '/owned-next'}):
            slot = secondary.start(self.supervisor, self.run, 'btq-next')
        slot.update(native_session_id='secondary-native', native_turn_state='idle')
        self.assertIs(secondary.submission_target(self.supervisor, self.run, 'secondary'), slot)
        with self.assertRaises(BeadsError):
            secondary.submission_target(self.supervisor, self.run, 'worker')
        gate_id = slot['blocker_ids'][0]
        result = {'gate': self.issues[gate_id]}
        fixtures.blockers.resolve(self.store, self.beads, self.run, gate_id, self.receipt(result), self.config)
        self.issues['btq-next']['status'] = 'closed'
        with patch.object(secondary, 'git', return_value=''):
            secondary.finish(self.supervisor, self.run)
        self.assertIs(secondary.submission_target(self.supervisor, self.run, 'worker'), self.run)
        self.assertEqual(self.run['native_session_id'], 'primary-native')
        self.supervisor.tmux.stop.assert_called_once_with(slot)
