import copy
import time
import unittest
from unittest.mock import patch

from harness import task_blockers as blockers, task_gates as gates
from harness import blocker_receipts as receipts
from harness.admin_references import npub
from harness.beads import BeadsError
from tests.test_blocker_receipts import PrivateKey, signed
from tests import test_task_gates as fixtures


@unittest.skipIf(PrivateKey is None, 'install requirements-blockers.txt')
class BlockerTest(unittest.TestCase):
    setUp = fixtures.GateTest.setUp
    git = fixtures.GateTest.git
    bd = fixtures.GateTest.bd
    evidence = fixtures.GateTest.evidence

    def setup_blocker(self, category='approval'):
        self.issues['btq-target'] = dict(id='btq-target', title='Target', description='Scope', metadata={},
            status='in_progress', assignee=self.queue.worker, labels=[], dependencies=[])
        self.run['group_id'] = 'group'
        self.run['beads']['issue_id'] = 'btq-target'
        with self.store.db:
            self.store.save(self.run)
        public = PrivateKey.from_int(7).public_key_xonly.format().hex()
        self.config = {'blockers': {'enabled': True, 'policies': {'operator': {
            'signers': [public], 'revision': 'v1', 'event_kind': 1, 'kinds': ['external'],
            'authority_basis': 'operator'}}, 'assignees': {'reviewer': {'type': 'human'}}}}
        self.beads.config.update(self.config)
        self.plan = dict(key='request', issue_id='btq-target', kind='external', revision='artifact-v1',
                         reason='Approve exact change', issuer='operator', category=category,
                         policy='operator', assignee={'id': 'reviewer', 'type': 'human'})
        return blockers.create(self.store, self.beads, self.run, self.plan, self.config)

    def receipt(self, result):
        evidence = self.evidence(result)
        binding = result['gate']['metadata']['harness_gate']
        public = PrivateKey.from_int(7).public_key_xonly.format().hex()
        evidence['decision_evidence'] = {'question': binding['reason'], 'artifact': binding['revision']}
        body = dict(blockers.expected(binding), decision='approve', evidence_sha256=receipts.digest(evidence['decision_evidence']),
                    npub=npub(public), issued_at=int(time.time()), expires_at=int(time.time()) + 3600)
        evidence['signed_receipt'] = signed(body)
        return evidence

    def test_human_block_signed_receipt_unblock_and_durable_ask(self):
        result = self.setup_blocker()
        gate_id = result['gate']['id']
        self.assertEqual(blockers.expected(result['gate']['metadata']['harness_gate'])['blocker_id'], gate_id)
        with self.assertRaises(BeadsError):
            blockers.assert_worker_unblocked(self.beads, self.run)
        blockers.create(self.store, self.beads, self.run, self.plan, self.config)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM operator_asks').fetchone()[0], 1)
        from harness.delivery import deliver
        from tests.test_operator_asks import Transport
        transport = Transport()
        deliver(self.store, transport, now=time.time())
        self.assertTrue(any(gate_id in call[1] and '@npub-one' in call[1] for call in transport.calls))
        evidence = self.receipt(result)
        blockers.resolve(self.store, self.beads, self.run, gate_id, evidence, self.config)
        blockers.assert_worker_unblocked(self.beads, self.run)
        blockers.resolve(self.store, self.beads, self.run, gate_id, evidence, self.config)
        self.assertIsNotNone(self.store.db.execute('SELECT resolved_at FROM operator_asks').fetchone()[0])
        self.assertEqual(self.issues[gate_id]['metadata']['harness_gate_resolution']['evidence'], evidence)

    def test_unsigned_generic_gate_resolution_cannot_bypass(self):
        result = self.setup_blocker()
        unsigned_plan = {k: self.plan[k] for k in ('key', 'issue_id', 'kind', 'revision', 'reason', 'issuer')}
        unsigned_plan.update(key='unsigned-replacement', supersedes=result['gate']['id'])
        with self.assertRaisesRegex(BeadsError, 'signed replacement'):
            gates.create(self.queue, self.run, unsigned_plan)
        with self.assertRaises(BeadsError):
            gates.resolve_gate(self.queue, self.run, result['gate']['id'], self.evidence(result))
        self.issues[result['gate']['id']]['status'] = 'closed'
        with self.assertRaises(BeadsError):
            blockers.assert_worker_unblocked(self.beads, self.run)

    def test_policy_change_and_approval_without_effect_fail(self):
        result = self.setup_blocker('access-validation')
        evidence = self.receipt(result)
        with self.assertRaisesRegex(BeadsError, 'native effect'):
            blockers.resolve(self.store, self.beads, self.run, result['gate']['id'], evidence, self.config)
        self.config['blockers']['policies']['operator']['revision'] = 'v2'
        with self.assertRaisesRegex(BeadsError, 'policy changed'):
            blockers.resolve(self.store, self.beads, self.run, result['gate']['id'], evidence, self.config)

    def test_agent_dispatch_is_registered_and_deduplicated(self):
        self.setup_blocker()
        self.plan['key'] = 'agent'
        self.plan['assignee'] = {'type': 'agent', 'id': 'agent-reviewer'}
        self.config['blockers']['assignees']['agent-reviewer'] = {'type': 'agent', 'run_id': self.run['id']}
        for _ in range(2):
            blockers.create(self.store, self.beads, self.run, self.plan, self.config)
        rows = self.store.db.execute("SELECT target FROM inbox WHERE id LIKE 'blocker-dispatch:%'").fetchall()
        self.assertEqual([row[0] for row in rows], ['manager'])

    def test_consent_retained_before_native_effect_and_metadata_roundtrip(self):
        import json
        result = self.setup_blocker('access-validation')
        gate_id = result['gate']['id']
        evidence = self.receipt(result)
        decision = {k: evidence[k] for k in ('signed_receipt', 'decision_evidence')}
        blockers.receive(self.store, self.beads, self.run, gate_id, decision, self.config)
        self.assertEqual(self.issues[gate_id]['status'], 'open')
        self.assertEqual(self.issues[gate_id]['metadata']['harness_blocker_decision']['phase'], 'approved')
        with self.assertRaises(BeadsError):
            blockers.assert_worker_unblocked(self.beads, self.run)
        evidence['native_effect_ref'] = evidence['artifacts'][0]['path']
        for issue in (self.issues[gate_id], self.issues['btq-target']):
            issue['metadata'] = {k: json.dumps(v) for k, v in issue['metadata'].items()}
        blockers.resolve(self.store, self.beads, self.run, gate_id, evidence, self.config)
        blockers.assert_worker_unblocked(self.beads, self.run)
