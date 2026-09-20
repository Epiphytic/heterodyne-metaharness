import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import task_delivery as delivery, task_formulas as formulas, task_stages as stages, task_handoffs as handoffs
from harness.beads import BeadsError
from tests import test_task_formulas as fixtures


class HandoffTest(unittest.TestCase):
    bd = fixtures.FormulaTest.bd
    git = fixtures.FormulaTest.git
    bind = fixtures.FormulaTest.bind
    create = fixtures.FormulaTest.create

    def setUp(self):
        fixtures.FormulaTest.setUp(self)
        def repo_git(path, *args):
            if '--git-common-dir' in args:
                return '/repository/.git'
            return self.git(path, *args)
        patched = patch.object(handoffs, 'git', side_effect=repo_git)
        patched.start()
        self.addCleanup(patched.stop)

    def evidence(self, stage, history):
        path = Path(self.tmp.name) / (stage + '.log')
        path.write_text('Actual retained ' + stage + ' result\n')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        value = fixtures.FormulaTest.evidence(self)
        value.update(checkout='/owned', repository='/repository/.git', evidence_ref=str(path),
                     artifacts=[{'path': str(path), 'sha256': digest}], log_sha256=digest,
                     approved_revision=self.sha, review_ref=str(path), authority_ref=str(path),
                     restrictions=[], environment='test', running_revision=self.sha,
                     live_verification_ref=str(path), acceptance_checks=[{'check': 'service probe', 'result': 'passed', 'observed': 'expected revision'}])
        if history:
            value['input_digest'] = history[-1]['digest']
        return value

    finish = fixtures.FormulaTest.finish
    stage_evidence = evidence

    def prepared(self):
        group = self.create('deployable-v2')
        self.finish(group, 'implementation')
        self.run['workdir'] = '/unrelated-review-checkout'
        self.finish(group, 'review')
        return group

    def test_complete_independent_owners_retained_artifacts(self):
        for formula in ('deployable-v2', 'library-v2'):
            self.plan['key'] = formula
            self.run['workdir'] = '/owned'
            group = self.create(formula)
            self.assertEqual(group, self.create(formula))
            for role in formulas.PROFILES[formula]:
                with self.assertRaises(BeadsError):
                    delivery.close_step(self.queue, self.run, group['parent'])
                self.finish(group, role)
                self.run['workdir'] = '/other-owner-unrelated'
            delivery.close_step(self.queue, self.run, group['parent'])
            (Path(self.tmp.name) / 'tested.log').write_text('changed evidence')
            with self.assertRaisesRegex(BeadsError, 'digest mismatch'):
                delivery.close_step(self.queue, self.run, group['parent'])

    def test_merged_is_not_deployed_and_deployed_is_not_verified(self):
        group = self.prepared()
        with self.assertRaisesRegex(BeadsError, 'not closed'):
            delivery.close_step(self.queue, self.run, group['parent'])
        self.finish(group, 'deployment')
        with self.assertRaisesRegex(BeadsError, 'not closed'):
            delivery.close_step(self.queue, self.run, group['parent'])
        self.assertEqual([v['id'] for v in self.beads.ready(self.run)], [group['verification']['id']])

    def test_wrong_log_hash_and_wrong_test_revision(self):
        group = self.create('deployable-v2')
        issue = self.issues[group['implementation']['id']]
        self.bind(issue)
        stages.record(self.store, self.beads, self.run, issue['id'], 'committed', self.evidence('committed', []))
        history = issue['metadata']['harness_lifecycle']
        for field, value in [('log_sha256', 'b'*64), ('commit', 'b'*40), ('input_digest', 'b'*64), ('repository', '/wrong'), ('checkout', '/not-owned')]:
            evidence = self.evidence('tested', history)
            evidence[field] = value
            with self.subTest(field=field), self.assertRaises(BeadsError):
                stages.record(self.store, self.beads, self.run, issue['id'], 'tested', evidence)
        self.assertEqual(len(history), 1)

    def test_review_requires_approval_and_retained_authority(self):
        group = self.create('deployable-v2')
        self.finish(group, 'implementation')
        issue = self.issues[group['review']['id']]
        self.bind(issue)
        history = delivery.prior_history(self.queue, issue)
        for field, value in [('approved_revision','b'*40), ('authority_ref', '/missing'), ('restrictions', None)]:
            evidence = self.evidence('merged', history)
            evidence[field] = value
            with self.subTest(field=field), self.assertRaises(BeadsError):
                stages.record(self.store, self.beads, self.run, issue['id'], 'merged', evidence)

    def test_verification_pins_deployment_and_actual_acceptance(self):
        group = self.prepared()
        self.finish(group, 'deployment')
        issue = self.issues[group['verification']['id']]
        self.bind(issue)
        history = delivery.prior_history(self.queue, issue)
        for field, value in [('target', 'wrong-service'), ('environment','wrong-environment'), ('running_revision','b'*40), ('acceptance_checks',[]), ('live_verification_ref','/missing'), ('input_digest','b'*64)]:
            evidence = self.evidence('live-verified', history)
            evidence[field] = value
            with self.subTest(field=field), self.assertRaises(BeadsError):
                stages.record(self.store, self.beads, self.run, issue['id'], 'live-verified', evidence)

    def test_uncertain_write_replay_preserves_history(self):
        group = self.create('deployable-v2')
        issue = self.issues[group['implementation']['id']]
        self.bind(issue)
        evidence = self.evidence('committed', [])
        original = self.queue.bd.side_effect
        def uncertain(*args):
            original(*args)
            raise BeadsError('acknowledgment lost after write')
        self.queue.bd.side_effect = uncertain
        with self.assertRaisesRegex(BeadsError, 'acknowledgment lost'):
            stages.record(self.store, self.beads, self.run, issue['id'], 'committed', evidence)
        snapshot = copy.deepcopy(issue['metadata'])
        self.queue.bd.side_effect = original
        calls = self.queue.bd.call_count
        stages.record(self.store, self.beads, self.run, issue['id'], 'committed', evidence)
        self.assertEqual(self.queue.bd.call_count, calls)
        self.assertEqual(issue['metadata'], snapshot)
        with self.assertRaisesRegex(BeadsError, 'requires tested'):
            stages.record(self.store, self.beads, self.run, issue['id'], 'committed', dict(evidence, result='changed'))

    def test_deploy_authority_restrictions_and_no_waiver(self):
        evidence = dict(commit=self.sha, deployment_authority='operator', applicable=True,
                        deployed_revision=self.sha, restrictions=[])
        retained = self.evidence('deployed', [])
        evidence.update(artifacts=retained['artifacts'], authority_ref=retained['authority_ref'])
        for field, value in [('applicable',False),('deployment_authority','worker'),('deployed_revision','b'*40)]:
            with self.subTest(field=field), self.assertRaises(BeadsError):
                handoffs.deployment(dict(evidence, **{field:value}), {'restrictions':[]})
        evidence['restrictions'] = ['operator must confirm quiet window']
        with self.assertRaises(BeadsError):
            handoffs.deployment(evidence, {'restrictions':evidence['restrictions']})
        evidence['restriction_resolution_ref'] = evidence['authority_ref']
        handoffs.deployment(evidence, {'restrictions':evidence['restrictions']})
