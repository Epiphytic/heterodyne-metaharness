import unittest
from unittest.mock import Mock, patch

from harness import pr_watch
from harness.beads import BeadsError
from tests import test_task_blockers as fixtures


@unittest.skipIf(fixtures.PrivateKey is None, 'install requirements-blockers.txt')
class WatchTest(fixtures.BlockerTest):
    def watch(self):
        result = self.setup_blocker('pr-review')
        self.supervisor.config = {'beads': self.config}
        self.supervisor.beads = self.beads
        self.supervisor.store = self.store
        self.config['blockers']['watch_adapters'] = {'radicle': {'argv': ['adapter'], 'reviewers': ['reviewer']}}
        self.source = {'adapter': 'radicle', 'repository': 'rad:repo', 'review_id': 'patch', 'head': 'artifact-v1'}
        pr_watch.attach(self.supervisor, self.run, result['gate']['id'], self.source)
        return result

    def test_hourly_backstop_survives_dead_agent_and_closes_signed_gate(self):
        result = self.watch()
        payload = {'source': self.source, 'state': 'approved', 'evidence': self.receipt(result)}
        with patch.object(pr_watch, 'query', return_value=payload) as query:
            pr_watch.tick(self.supervisor, self.run, 10)
            pr_watch.tick(self.supervisor, self.run, 4000)
            self.assertEqual(query.call_count, 1)
        self.assertEqual(self.issues[result['gate']['id']]['status'], 'closed')

    def test_failed_adapter_retains_hourly_retry_and_wrong_head_rejected(self):
        result = self.watch()
        with patch.object(pr_watch, 'query', side_effect=BeadsError('offline')) as query:
            pr_watch.tick(self.supervisor, self.run, 10)
            pr_watch.tick(self.supervisor, self.run, 100)
            self.assertEqual(query.call_count, 1)
            pr_watch.tick(self.supervisor, self.run, 3610)
            self.assertEqual(query.call_count, 2)
        with self.assertRaises(BeadsError):
            pr_watch.apply(self.supervisor, self.run, result['gate']['id'],
                           {'source': dict(self.source, head='different'), 'state': 'approved'}, 4000)

    def test_authorized_comments_create_one_fix_and_changed_identity_fails(self):
        result = self.watch()
        fix = dict(id='btq-fix', status='open', metadata={}, dependencies=[])
        self.supervisor.beads.create = Mock(return_value=fix)
        self.issues['btq-fix'] = fix
        payload = {'source': self.source, 'state': 'changes-requested',
                   'comments': [{'id': 'c1', 'author': 'reviewer', 'body': 'Repair behavior'}]}
        for now in (10, 3610):
            pr_watch.apply(self.supervisor, self.run, result['gate']['id'], payload, now)
        edges = self.issues['btq-target']['dependencies']
        self.assertEqual(sum(e['id'] == 'btq-fix' for e in edges), 1)
        self.assertEqual(len(self.run['pr_watches'][result['gate']['id']]['events']), 1)
        payload['comments'][0]['body'] = 'Changed under the same event identity'
        with self.assertRaises(BeadsError):
            pr_watch.apply(self.supervisor, self.run, result['gate']['id'], payload, 7210)
