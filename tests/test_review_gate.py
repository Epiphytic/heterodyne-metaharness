import copy
import hashlib
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import review_gate as gate, reviews, review_dispatch, task_gates
from harness.beads import BeadsError
from tests import test_task_blockers as fixtures
from tests.test_task_blockers import PrivateKey
from tests.test_blocker_receipts import signed
from harness.admin_references import npub
from harness import blocker_receipts, task_blockers


@unittest.skipIf(PrivateKey is None, 'install requirements-blockers.txt')
class ReviewGateTest(unittest.TestCase):
    setUp = fixtures.BlockerTest.setUp
    git = fixtures.BlockerTest.git
    bd = fixtures.BlockerTest.bd
    evidence = fixtures.BlockerTest.evidence
    setup_blocker = fixtures.BlockerTest.setup_blocker
    def setup_review(self, auto=True, maximum=3):
        self.setup_blocker()
        self.store.db.execute('DELETE FROM operator_asks')
        # Start a fresh parent without the unrelated approval prerequisite.
        self.issues['btq-target']['metadata'] = {}
        self.issues['btq-target']['dependencies'] = []
        self.config['blockers']['assignees']['manager'] = {'type': 'agent', 'run_id': self.run['id'], 'target': 'manager'}
        self.config['blockers']['assignees']['operator'] = {'type': 'human'}
        self.config['blockers']['policies']['manager'] = copy.deepcopy(self.config['blockers']['policies']['operator'])
        self.config['review_gate'] = {'enabled': True, 'auto_approve': auto, 'max_attempts': maximum}
        path = Path(self.tmp.name) / 'design.md'
        path.write_text('Design with acceptance evidence')
        self.request = dict(key='design-1', issue_id='btq-target', artifact_kind='design',
                            artifacts=[{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}])
        self.run['beads']['worker_identity'] = self.queue.worker
        self.run['beads']['task_snapshot'] = {'issue':self.issues['btq-target']}
        return gate.submit(self.store, self.beads, self.run, self.request, self.config)

    def result(self, submitted, verdict='pass'):
        key = submitted['review_id']
        target = review_dispatch.directory(self.store, key)
        target.mkdir(parents=True, exist_ok=True)
        raw = b'{"independent":"evidence"}'
        (target/'evidence.json').write_bytes(raw)
        result = dict(summary='Independent appraisal', verdict=verdict,
                      blocking_findings=['Fix missing coverage'] if verdict=='changes' else [],
                      model='fuelix/claude-opus-5-5', resolved_model='claude-opus-5-5',
                      resolved_provider='custom', resolved_api_mode='anthropic_messages', reviewer_session='independent-1',
                      evidence_digest=hashlib.sha256(raw).hexdigest())
        (target/'result.json').write_text(json.dumps(result))
        return result

    def apply(self, submitted, verdict='pass'):
        result = self.result(submitted, verdict)
        gate.process_result(self.store,self.beads,self.run,gate.job_for(self.store,self.run,submitted['review_id']),result,self.config)
        return gate.job_for(self.store,self.run,submitted['review_id'])

    def sign_review(self, job):
        request=json.loads(job['request'])
        binding=task_gates.metadata(self.issues[request['gate_id']])['harness_gate']
        decision=gate.appraisal(job,json.loads(job['result']))
        public=PrivateKey.from_int(7).public_key_xonly.format().hex()
        body=dict(task_blockers.expected(binding),decision='approve',evidence_sha256=blocker_receipts.digest(decision),
                  npub=npub(public),issued_at=int(time.time()),expires_at=int(time.time())+3600)
        return {'signed_receipt':signed(body),'decision_evidence':decision}

    def test_pass_waits_for_manager_signature_then_auto_approves(self):
        submitted=self.setup_review()
        job=self.apply(submitted)
        self.assertEqual(job['state'],'receipt-pending')
        with self.assertRaises(BeadsError):
            task_gates.check(self.queue,self.issues['btq-target'])
        evidence=self.sign_review(job)
        gate.receive(self.store,self.beads,self.run,job['id'],evidence,self.config)
        gate.receive(self.store,self.beads,self.run,job['id'],evidence,self.config)
        self.assertEqual(gate.job_for(self.store,self.run,job['id'])['state'],'approved')
        task_gates.check(self.queue,self.issues['btq-target'])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM inbox WHERE id LIKE '%:sign'").fetchone()[0],1)
        rows=self.store.db.execute("SELECT text FROM outbox WHERE id LIKE '%:approval:%'").fetchall()
        self.assertTrue(any('signed_receipt' in row[0] for row in rows))

    def test_findings_revision_pass_and_worker_input(self):
        first=self.setup_review()
        job=self.apply(first,'changes')
        self.assertEqual(job['state'],'changes-requested')
        task_blockers.assert_worker_unblocked(self.beads,self.run)
        with self.assertRaises(BeadsError):
            task_gates.check(self.queue,self.issues['btq-target'])
        revised=dict(self.request,key='design-2',previous=job['id'])
        next_review=gate.submit(self.store,self.beads,self.run,revised,self.config)
        self.assertEqual(next_review['attempt'],2)
        self.assertEqual(gate.job_for(self.store,self.run,job['id'])['state'],'superseded')
        self.assertEqual(self.apply(next_review)['state'],'receipt-pending')
        self.assertEqual(gate.submit(self.store,self.beads,self.run,revised,self.config),next_review)

    def test_attempt_limit_and_policy_false_escalate(self):
        for auto,maximum,verdict in [(True,1,'changes'),(False,3,'pass')]:
            with self.subTest(auto=auto):
                self.setUp()
                submitted=self.setup_review(auto,maximum)
                job=self.apply(submitted,verdict)
                self.assertEqual(job['state'],'escalated')
                self.assertTrue(any(b.get('supersedes')==submitted['gate_id'] and b['blocker']['category']=='approval'
                    for b in task_gates.metadata(self.issues['btq-target'])[task_gates.FIELD].values()))

    def test_invalid_results_and_forged_receipts_fail_closed(self):
        submitted=self.setup_review()
        result=self.result(submitted)
        result['blocking_findings']=['unresolved']
        with self.assertRaises(BeadsError):
            gate.process_result(self.store,self.beads,self.run,gate.job_for(self.store,self.run,submitted['review_id']),result,self.config)
        job=self.apply(submitted)
        receipt=self.sign_review(job)
        receipt['decision_evidence']['model']='other-model'
        with self.assertRaises(BeadsError):
            gate.receive(self.store,self.beads,self.run,job['id'],receipt,self.config)
        self.assertEqual(self.issues[submitted['gate_id']]['status'],'open')

    def test_scope_policy_and_artifact_drift(self):
        submitted=self.setup_review()
        job=self.apply(submitted)
        receipt=self.sign_review(job)
        self.config['review_gate']['auto_approve']=False
        with self.assertRaisesRegex(BeadsError,'policy changed'):
            gate.receive(self.store,self.beads,self.run,job['id'],receipt,self.config)
        self.config['review_gate']['auto_approve']=True
        Path(self.request['artifacts'][0]['path']).write_text('changed')
        with self.assertRaisesRegex(ValueError,'artifact changed'):
            gate.receive(self.store,self.beads,self.run,job['id'],receipt,self.config)

    def test_runner_dispatch_restart_dedup_and_model_configuration(self):
        submitted=self.setup_review()
        job=gate.job_for(self.store,self.run,submitted['review_id'])
        with patch('harness.review_dispatch.subprocess.Popen') as launch:
            review_dispatch.tick(self.store,self.run,time.time(),self.config,self.beads)
            review_dispatch.tick(self.store,self.run,time.time(),self.config,self.beads)
            self.assertEqual(launch.call_count,1)
        target=review_dispatch.directory(self.store,job['id'])
        self.assertEqual(json.loads((target/'model.json').read_text())['model'],'fuelix/claude-opus-5-5')
        self.result(submitted)
        review_dispatch.tick(self.store,self.run,time.time(),self.config,self.beads)
        self.assertEqual(gate.job_for(self.store,self.run,job['id'])['state'],'receipt-pending')

    def test_complete_artifacts_required(self):
        self.setup_review()
        self.request['artifacts'][0]['sha256']='wrong'
        with self.assertRaises(ValueError):
            gate.submit(self.store,self.beads,self.run,self.request,self.config)

    def test_config_defaults_and_cli(self):
        self.assertEqual(gate.settings({'review_gate':{'enabled':True}})['signer'],'manager')
        with self.assertRaises(BeadsError):
            gate.settings({'review_gate':{'enabled':True,'auto_approve':'true'}})
        from harness.cli import parser
        args=parser().parse_args(['review','test','submit','--file','request.json'])
        self.assertEqual(args.review_action,'submit')

    def test_retained_result_and_evidence_cannot_change_before_signing(self):
        submitted = self.setup_review()
        job = self.apply(submitted)
        receipt = self.sign_review(job)
        target = review_dispatch.directory(self.store, job['id'])
        original = (target / 'result.json').read_text()
        (target / 'result.json').write_text('{}')
        with self.assertRaisesRegex(BeadsError, 'result changed'):
            gate.receive(self.store, self.beads, self.run, job['id'], receipt, self.config)
        (target / 'result.json').write_text(original)
        (target / 'evidence.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            gate.receive(self.store, self.beads, self.run, job['id'], receipt, self.config)

    def test_known_runner_failure_escalates_without_relaunch(self):
        submitted = self.setup_review()
        with patch('harness.review_dispatch.subprocess.Popen') as launch:
            review_dispatch.tick(self.store, self.run, time.time(), self.config, self.beads)
            target = review_dispatch.directory(self.store, submitted['review_id'])
            (target / 'error.json').write_text('{"error":"TimeoutError"}')
            review_dispatch.tick(self.store, self.run, time.time(), self.config, self.beads)
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(gate.job_for(self.store, self.run, submitted['review_id'])['state'], 'escalated')

    def test_malformed_completed_result_escalates(self):
        submitted = self.setup_review()
        with patch('harness.review_dispatch.subprocess.Popen'):
            review_dispatch.tick(self.store, self.run, time.time(), self.config, self.beads)
        target = review_dispatch.directory(self.store, submitted['review_id'])
        (target / 'result.json').write_text('[]')
        review_dispatch.tick(self.store, self.run, time.time(), self.config, self.beads)
        self.assertEqual(gate.job_for(self.store, self.run, submitted['review_id'])['state'], 'escalated')

    def test_recovery_and_cross_run_do_not_admit_or_approve(self):
        submitted = self.setup_review()
        job = self.apply(submitted)
        self.run['resume_required'] = True
        with self.assertRaisesRegex(BeadsError, 'recovery'):
            gate.receive(self.store, self.beads, self.run, job['id'], self.sign_review(job), self.config)
        other = dict(self.run, id='other-run')
        with self.assertRaisesRegex(BeadsError, 'Unknown'):
            gate.job_for(self.store, other, job['id'])

    def test_unsigned_verdict_cannot_resolve_gate(self):
        submitted = self.setup_review()
        job = self.apply(submitted)
        receipt = self.sign_review(job)
        receipt['signed_receipt']['sig'] = '00' * 64
        with self.assertRaises((BeadsError, ValueError)):
            gate.receive(self.store, self.beads, self.run, job['id'], receipt, self.config)
        self.assertEqual(gate.job_for(self.store, self.run, job['id'])['state'], 'receipt-pending')

    def test_stale_pending_job_releases_concurrency_slot(self):
        submitted = self.setup_review()
        job = gate.job_for(self.store, self.run, submitted['review_id'])
        Path(self.request['artifacts'][0]['path']).write_text('New revision')
        with patch('harness.review_dispatch.subprocess.Popen') as launch:
            busy = review_dispatch.artifact_tick(self.store, self.beads, self.run, job, self.config)
            self.assertFalse(busy)
            launch.assert_not_called()
        self.assertEqual(gate.job_for(self.store, self.run, job['id'])['state'], 'stale')
        self.assertEqual(self.issues[submitted['gate_id']]['status'], 'open')

    def test_pending_review_respects_existing_hold(self):
        submitted = self.setup_review()
        with patch('harness.reviews.held', return_value=True), patch('harness.review_dispatch.subprocess.Popen') as launch:
            review_dispatch.tick(self.store, self.run, time.time(), self.config, self.beads)
            launch.assert_not_called()
        self.assertEqual(gate.job_for(self.store, self.run, submitted['review_id'])['state'], 'pending')

    def test_configured_runner_records_resolved_model_without_credentials(self):
        import os
        import sys
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from harness.review_runner import review
        agent = MagicMock(tools=[], valid_tool_names=set())
        agent.run_conversation.return_value = {'completed': True, 'final_response':
            '{"verdict":"pass","summary":"Reviewed","blocking_findings":[]}'}
        constructor = MagicMock(return_value=agent)
        resolver = MagicMock(return_value={'provider': 'custom', 'api_key': 'fixture-secret',
            'base_url': 'https://example.invalid', 'api_mode': 'chat_completions'})
        provider = SimpleNamespace(_get_model_config=lambda: {}, _getenv=lambda name: 'fixture-secret',
                                   resolve_runtime_provider=resolver)
        modules = {'hermes_cli.runtime_provider': provider, 'run_agent': SimpleNamespace(AIAgent=constructor),
                   'hermes_constants': SimpleNamespace(set_hermes_home_override=lambda path: None)}
        config = {'model': 'fuelix/claude-opus-5-5', 'provider': 'custom',
                  'api_mode': 'anthropic_messages', 'api_key_env': 'TEST_KEY', 'base_url': 'https://example.invalid'}
        with patch.dict(sys.modules, modules), patch.dict(os.environ), patch('harness.review_runner.Path.cwd', return_value=Path(self.tmp.name)):
            result = review({'task': 'acceptance criteria'}, 'independent-test', config)
        self.assertEqual(result['resolved_model'], 'claude-opus-5-5')
        self.assertEqual(result['resolved_api_mode'], 'anthropic_messages')
        self.assertEqual(constructor.call_args.kwargs['api_mode'], 'anthropic_messages')
        self.assertEqual(constructor.call_args.kwargs['enabled_toolsets'], [])
        self.assertNotIn('fixture-secret', json.dumps(result))
        self.assertNotIn('fixture-secret', (Path(self.tmp.name) / 'hermes-home/config.yaml').read_text())

    def test_review_category_cannot_bypass_native_execution_gates(self):
        submitted = self.setup_review()
        plan = dict(key='not-a-review', issue_id='btq-target', kind='merge', revision='abc',
                    reason='merge', issuer='review-gate', category='review',
                    assignee={'type':'agent', 'id':'manager'}, policy='manager')
        self.config['blockers']['policies']['manager']['kinds'].append('merge')
        with self.assertRaisesRegex(BeadsError, 'native execution'):
            task_blockers.create(self.store, self.beads, self.run, plan, self.config)
