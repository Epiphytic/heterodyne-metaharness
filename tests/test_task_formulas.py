import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import cli, task_delivery as delivery, task_formulas as formulas, task_stages as stages
from harness.beads import BeadsError
from tests import test_task_delivery as fixtures


class FormulaTest(unittest.TestCase):
    setUp = fixtures.DeliveryTest.setUp
    bd = fixtures.DeliveryTest.bd
    git = fixtures.DeliveryTest.git
    bind = fixtures.DeliveryTest.bind
    evidence = fixtures.DeliveryTest.evidence

    def create(self, name='research-v1'):
        plan = dict(self.plan, formula=name, routes={role: 'test' for role in formulas.PROFILES[name]})
        path = Path(self.tmp.name) / 'formula-plan.json'
        path.write_text(json.dumps(plan))
        args = cli.parser().parse_args(['task', 'test', 'formula', '--file', str(path)])
        with patch('harness.cli.Beads', return_value=self.beads):
            return cli.task_dispatch(args, self.store, self.supervisor, {})

    def retained(self):
        path = Path(self.tmp.name) / 'sanitized-report.txt'
        path.write_text('Evidence retained for review.\n')
        return [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]

    def finish(self, group, role):
        issue = self.issues[group[role]['id']]
        self.bind(issue)
        prior = delivery.prior_history(self.queue, issue)
        for stage in delivery.steps(delivery.contract(issue))[role]:
            evidence = self.evidence()
            evidence.update(artifacts=self.retained(), summary='Actual findings and limitations')
            if prior:
                evidence['input_digest'] = prior[-1]['digest']
            stages.record(self.store, self.beads, self.run, issue['id'], stage, evidence)
        fresh = self.queue.show(issue['id'])
        delivery.close_step(self.queue, self.run, fresh)
        issue['status'] = 'closed'

    def test_all_graphs_replay_and_parent_closure(self):
        for name, profile in formulas.PROFILES.items():
            with self.subTest(name=name):
                self.plan['key'] = name
                group = self.create(name)
                self.assertEqual(self.create(name), group)
                self.assertEqual(len(group), len(profile) + 1)
                parent = group['parent']
                with self.assertRaises(BeadsError):
                    delivery.close_step(self.queue, self.run, parent)
                for role in profile:
                    self.finish(group, role)
                delivery.close_step(self.queue, self.run, parent)
                binding = delivery.contract(parent)
                self.assertEqual(binding['formula'], name)
                self.assertEqual(binding['formula_sha256'], formulas.load(name)[1])

    def test_research_needs_no_git_pr_or_deployment(self):
        group = self.create()
        with patch('harness.task_stages.git', side_effect=AssertionError('research must not need Git')), \
             patch('harness.task_delivery.git', side_effect=AssertionError('research must not need Git')):
            self.finish(group, 'investigation')
            self.finish(group, 'recommendation')
            delivery.close_step(self.queue, self.run, group['parent'])
        self.assertTrue(all('kind:research' in item['labels'] for item in group.values()))

    def test_same_key_cannot_change_formula_or_content(self):
        self.create()
        before = copy.deepcopy(self.issues)
        for name in ('library-v1', 'configuration-v1'):
            with self.assertRaisesRegex(BeadsError, 'different content'):
                self.create(name)
        self.plan['description'] = 'New unauthorized scope'
        with self.assertRaisesRegex(BeadsError, 'different content'):
            self.create()
        self.assertEqual(self.issues, before)

    def test_partial_admission_can_reconcile_without_eligible_or_duplicate_steps(self):
        original = self.queue.bd.side_effect
        def fail(*args):
            if args[:2] == ('dep', 'add'):
                raise BeadsError('uncertain write')
            return original(*args)
        self.queue.bd.side_effect = fail
        with self.assertRaises(BeadsError):
            self.create()
        self.assertEqual(self.beads.ready(self.run), [])
        self.queue.bd.side_effect = original
        group = self.create()
        self.assertEqual(len(self.issues), 3)
        self.assertEqual([item['id'] for item in self.beads.ready(self.run)], [group['investigation']['id']])

    def test_deployable_cannot_waive_deployment(self):
        group = self.create('deployable-v1')
        self.finish(group, 'implementation')
        self.finish(group, 'review')
        issue = self.issues[group['deployment']['id']]
        self.bind(issue)
        evidence = dict(self.evidence(), applicable=False, reason='skip')
        with self.assertRaisesRegex(BeadsError, 'cannot omit'):
            stages.record(self.store, self.beads, self.run, issue['id'], 'deployed', evidence)

    def test_retained_evidence_drift_blocks_parent(self):
        group = self.create('configuration-v1')
        self.finish(group, 'change')
        self.finish(group, 'verification')
        (Path(self.tmp.name) / 'sanitized-report.txt').write_text('drift')
        with self.assertRaisesRegex(BeadsError, 'digest mismatch'):
            delivery.close_step(self.queue, self.run, group['parent'])

    def test_verification_requires_preceding_revision_and_passing_check(self):
        group = self.create('configuration-v1')
        self.finish(group, 'change')
        issue = self.issues[group['verification']['id']]
        self.bind(issue)
        evidence = dict(self.evidence(), artifacts=self.retained(), summary='Checks')
        with self.assertRaisesRegex(BeadsError, 'preceding'):
            stages.record(self.store, self.beads, self.run, issue['id'], 'verified', evidence)
        evidence['input_digest'] = delivery.prior_history(self.queue, issue)[-1]['digest']
        evidence['result'] = 'failed'
        with self.assertRaisesRegex(BeadsError, 'passing'):
            stages.record(self.store, self.beads, self.run, issue['id'], 'verified', evidence)

    def test_published_formula_digest_and_missing_role_fail_closed(self):
        group = self.create()
        issue = copy.deepcopy(group['investigation'])
        issue['metadata'][delivery.FIELD]['formula_sha256'] = '0' * 64
        with self.assertRaisesRegex(BeadsError, 'digest changed'):
            delivery.contract(issue)
        issue = copy.deepcopy(group['investigation'])
        del issue['metadata'][delivery.FIELD]['members']['recommendation']
        with self.assertRaisesRegex(BeadsError, 'contract'):
            delivery.contract(issue)

    def test_legacy_admission_description_is_byte_compatible(self):
        self.assertEqual(delivery.description(self.plan, {'version': 1}, 'parent'),
            'Approved scope\n\nDelivery responsibility: parent. Verify and close delivery only after all three steps close.\n'
            'Read linked delivery_steps artifacts through task show; preserve operator review/deployment authority.')

    def test_wrong_routes_rejected_before_any_mutation(self):
        plan = dict(self.plan, formula='research-v1')
        with self.assertRaisesRegex(BeadsError, 'role routes'):
            formulas.create(self.store, self.supervisor, self.beads, self.run, plan)
        self.assertEqual(self.issues, {})

    def test_stage_owner_recovery_and_noop_replay(self):
        group = self.create()
        issue = self.issues[group['investigation']['id']]
        self.bind(issue)
        evidence = dict(evidence_ref='retained', summary='Investigation evidence', artifacts=self.retained())
        blocked = dict(self.run, resume_required=True)
        with self.assertRaisesRegex(BeadsError, 'recovery'):
            stages.record(self.store, self.beads, blocked, issue['id'], 'investigated', evidence)
        issue['assignee'] = 'another-worker'
        with self.assertRaisesRegex(BeadsError, 'owner'):
            stages.record(self.store, self.beads, self.run, issue['id'], 'investigated', evidence)
        issue['assignee'] = self.queue.worker
        stages.record(self.store, self.beads, self.run, issue['id'], 'investigated', evidence)
        before = self.queue.bd.call_count
        stages.record(self.store, self.beads, self.run, issue['id'], 'investigated', evidence)
        self.assertEqual(before, self.queue.bd.call_count)
