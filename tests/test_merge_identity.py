"""Real Git topology, isolated provider responses; no live PR mutations."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from harness.beads import BeadsError
from harness import task_stages as stages
from harness import task_delivery as delivery
from harness.workspace import git


class MergeIdentityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        git(self.repo, 'init', '-b', 'main')
        git(self.repo, 'config', 'user.name', 'Fixture')
        git(self.repo, 'config', 'user.email', 'fixture@example.test')
        git(self.repo, 'config', 'commit.gpgsign', 'false')
        self.commit('base')
        git(self.repo, 'checkout', '-b', 'feature')
        self.head = self.commit('submitted')
        git(self.repo, 'checkout', 'main')
        self.merged = self.commit('review fixes in squash')
        self.url = 'https://github.com/example/repo/pull/9'
        self.submitted = dict(commit=self.head, pr_url=self.url)
        self.evidence = dict(commit=self.merged, review_ref=self.url,
                             merge_authority='operator', evidence_ref='retained')
        self.run = dict(workdir=str(self.repo))
        self.record = dict(url=self.url, state='MERGED', headRefOid=self.head,
                           mergeCommit=dict(oid=self.merged))

    def commit(self, text):
        (self.repo / 'file').write_text(text)
        git(self.repo, 'add', 'file')
        git(self.repo, 'commit', '-m', text)
        return git(self.repo, 'rev-parse', 'HEAD')

    def verify(self, record=None):
        response = Mock(stdout=json.dumps(self.record if record is None else record))
        with patch('harness.merge_identity.subprocess.run', return_value=response) as fetch:
            # workspace uses the same subprocess module: pin just its Git call to
            # the already-observed non-ancestor result, retaining real topology test below.
            with patch.object(stages, 'git', side_effect=subprocess.CalledProcessError(1, ['git'])):
                stages.validate_merge(self.run, self.evidence, self.submitted)
            return fetch

    def test_real_nonancestor_and_different_tree_verified_by_provider(self):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            git(self.repo, 'merge-base', '--is-ancestor', self.head, self.merged)
        self.assertEqual(caught.exception.returncode, 1)
        self.assertNotEqual(git(self.repo, 'rev-parse', self.head+':file'),
                            git(self.repo, 'rev-parse', self.merged+':file'))
        fetch = self.verify()
        self.assertEqual(fetch.call_args.args[0], ['gh', 'pr', 'view', self.url,
                         '--json', 'url,state,headRefOid,mergeCommit,commits'])
        self.assertEqual(fetch.call_args.kwargs['timeout'], 30)

    def test_review_fix_head_retains_recorded_commit_membership(self):
        self.verify(dict(self.record, headRefOid='a'*40,
                         commits=[{'oid': self.head}, {'oid': 'a'*40}]))
        for commits in [[], None, {}, [{'oid': 'b'*40}], [self.head]]:
            with self.subTest(commits=commits), self.assertRaises(BeadsError):
                self.verify(dict(self.record, headRefOid='a'*40, commits=commits))

    def test_ancestor_offline_and_fatal_git_error_not_fallback(self):
        with patch('harness.merge_identity.verify_github') as fetch:
            stages.validate_merge(self.run, dict(self.evidence, commit=self.head), self.submitted)
            fetch.assert_not_called()
            with patch.object(stages, 'git', side_effect=subprocess.CalledProcessError(128, ['git'])):
                with self.assertRaisesRegex(BeadsError, 'local Git objects'):
                    stages.validate_merge(self.run, self.evidence, self.submitted)
            fetch.assert_not_called()

    def test_wrong_head_merge_state_url_and_malformed_records_rejected(self):
        for field, value in [('headRefOid', 'a'*40), ('mergeCommit', {'oid': 'b'*40}),
                             ('state', 'OPEN'), ('url', self.url+'0'), ('mergeCommit', None)]:
            with self.subTest(field=field, value=value), self.assertRaises(BeadsError):
                self.verify(dict(self.record, **{field: value}))
        with self.assertRaises(BeadsError):
            self.verify([])

    def test_wrong_review_reference_and_unsupported_host_never_fetch(self):
        for url in ['https://evil.example/repo/pull/9', self.url+'?x=1', 'rad://repo/patch']:
            self.submitted['pr_url'] = url
            self.evidence['review_ref'] = url
            with self.assertRaisesRegex(BeadsError, 'matching GitHub'):
                self.verify()
        self.submitted['pr_url'] = self.url
        self.evidence['review_ref'] = 'unrelated-review'
        with self.assertRaisesRegex(BeadsError, 'matching GitHub'):
            self.verify()

    def test_provider_errors_fail_closed_without_diagnostics(self):
        for error in [FileNotFoundError(), subprocess.TimeoutExpired('gh', 30),
                      subprocess.CalledProcessError(1, 'gh', stderr='secret'), ValueError('bad JSON')]:
            with self.subTest(error=type(error).__name__), patch.object(stages, 'git',
                    side_effect=subprocess.CalledProcessError(1, ['git'])), patch(
                    'harness.merge_identity.subprocess.run', side_effect=error):
                with self.assertRaisesRegex(BeadsError, 'verification unavailable') as caught:
                    stages.validate_merge(self.run, self.evidence, self.submitted)
                self.assertNotIn('secret', str(caught.exception))

    def test_deployment_prior_history_uses_rewritten_merge_validation(self):
        group = {'implementation': {'status':'closed'}, 'review': {'status':'closed'}, 'deployment': {}}
        events = {'implementation': [{'stage':'pr-open','evidence':self.submitted}],
                  'review': [{'stage':'merged','evidence':self.evidence}]}
        def validate(run, binding, stage, evidence, history, require_head):
            if stage == 'merged':
                stages.validate_evidence(run, stage, evidence, history, require_head=require_head)
        with patch.object(delivery, 'members', return_value=group), patch.object(delivery, 'contract',
                return_value={'role':'deployment'}), patch.object(delivery, 'artifact',
                return_value={'checkout':str(self.repo)}), patch.object(delivery, 'history',
                side_effect=lambda issue:events[next(k for k,v in group.items() if v is issue)]), patch(
                'harness.task_gates.check'), patch('harness.task_formulas.validate', side_effect=validate), patch.object(
                stages,'clean_commit',return_value=self.merged), patch.object(stages,'git',
                side_effect=subprocess.CalledProcessError(1,['git'])), patch(
                'harness.merge_identity.subprocess.run', return_value=Mock(stdout=json.dumps(self.record))):
            self.assertEqual(len(delivery.prior_history(Mock(),{})),2)
