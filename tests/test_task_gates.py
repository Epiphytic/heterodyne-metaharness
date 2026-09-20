import copy
import hashlib
import json
import unittest
from pathlib import Path
from harness import task_gates as gates
from harness.beads import BeadsError
from tests import test_task_delivery as fixtures


class GateTest(unittest.TestCase):
    setUp = fixtures.DeliveryTest.setUp
    git = fixtures.DeliveryTest.git

    def bd(self, *args):
        if args[0] == 'update' and '--set-metadata' in args:
            key, value = args[-1].split('=', 1)
            self.issues[args[1]]['metadata'][key] = json.loads(value)
        elif args[0] == 'create' and args[args.index('--type') + 1] == 'gate':
            value = lambda flag: args[args.index(flag) + 1]
            self.issues[value('--id')] = dict(id=value('--id'), issue_type=value('--type'),
                metadata=json.loads(value('--metadata')), status='open', dependencies=[])
        elif args[:2] == ('gate', 'resolve'):
            self.issues[args[2]]['status'] = 'closed'
        elif args[:2] == ('dep', 'remove'):
            self.issues[args[2]]['dependencies'] = [e for e in self.issues[args[2]]['dependencies'] if e['id'] != args[3]]
        else:
            return fixtures.DeliveryTest.bd(self, *args)

    def setup_gate(self, kind='external'):
        self.issues['btq-target'] = dict(id='btq-target', title='Target', description='Scope', metadata={},
            status='open', labels=[], dependencies=[])
        self.plan = dict(key='request', issue_id='btq-target', kind=kind, revision='artifact-v1',
                         reason='Required prerequisite', issuer='operator')
        return gates.create(self.queue, self.run, self.plan)

    def evidence(self, result):
        binding = result['gate']['metadata']['harness_gate']
        path = Path(self.tmp.name) / 'evidence.txt'
        path.write_text('Actual reviewed evidence fixture')
        return dict(issue_id=binding['issue_id'], kind=binding['kind'], revision=binding['revision'],
            scope=binding['scope'], authority_basis='operator', issuer='Liam', result='passed',
            evidence_ref=str(path), artifacts=[{'path':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}])

    def test_native_gate_graph_resolution_duplicate_and_unrelated_task(self):
        result = self.setup_gate()
        key = result['gate']['id']
        self.issues['btq-other'] = dict(id='btq-other', metadata={}, status='open', dependencies=[])
        self.assertEqual([i['id'] for i in self.beads.ready(self.run)], ['btq-other'])
        self.assertEqual(gates.create(self.queue,self.run,self.plan), result)
        evidence = self.evidence(result)
        gates.resolve_gate(self.queue,self.run,key,evidence)
        before = self.queue.bd.call_count
        gates.resolve_gate(self.queue,self.run,key,evidence)
        self.assertEqual(before,self.queue.bd.call_count)
        gates.check(self.queue,self.queue.show('btq-target'))
        self.assertEqual({i['id'] for i in self.beads.ready(self.run)}, {'btq-target','btq-other'})

    def test_wrong_scope_revision_and_artifact_drift(self):
        result=self.setup_gate()
        evidence=self.evidence(result)
        for field in ('revision','issue_id','kind','scope'):
            with self.subTest(field=field), self.assertRaises(BeadsError):
                gates.resolve_gate(self.queue,self.run,result['gate']['id'],dict(evidence,**{field:'wrong'}))
        self.issues['btq-target']['acceptance_criteria']='New requirement'
        with self.assertRaisesRegex(BeadsError,'scope changed'):
            gates.resolve_gate(self.queue,self.run,result['gate']['id'],evidence)

    def test_raw_close_does_not_authorize(self):
        result=self.setup_gate()
        self.issues[result['gate']['id']]['status']='closed'
        self.assertFalse(self.beads._allowed(self.queue,self.queue.show('btq-target')))

    def test_missing_edge_and_partial_creation_fail_closed(self):
        result=self.setup_gate()
        self.issues['btq-target']['dependencies']=[]
        self.assertFalse(self.beads._allowed(self.queue,self.queue.show('btq-target')))
        gates.create(self.queue,self.run,self.plan)
        self.assertEqual(len(self.issues['btq-target']['dependencies']),1)

    def test_changed_key_rejected_and_explicit_supersession(self):
        result=self.setup_gate()
        evidence=self.evidence(result)
        gates.resolve_gate(self.queue,self.run,result['gate']['id'],evidence)
        self.issues['btq-target']['description']='Changed scope'
        with self.assertRaises(BeadsError):
            gates.create(self.queue,self.run,self.plan)
        newer=gates.create(self.queue,self.run,dict(self.plan,key='revalidated',supersedes=result['gate']['id']))
        self.assertFalse(self.beads._allowed(self.queue,self.queue.show('btq-target')))
        gates.resolve_gate(self.queue,self.run,newer['gate']['id'],self.evidence(newer))
        gates.check(self.queue,self.queue.show('btq-target'))
        self.assertEqual(self.issues[result['gate']['id']]['metadata']['harness_gate_resolution']['evidence'],evidence)

    def test_design_requires_real_existing_approval_contract(self):
        result=self.setup_gate('design')
        with self.assertRaisesRegex(BeadsError,'existing design approval'):
            gates.resolve_gate(self.queue,self.run,result['gate']['id'],self.evidence(result))

    def test_merge_and_deployment_not_external_watcher_authority(self):
        for kind in ('merge','deployment'):
            result=self.setup_gate(kind)
            with self.assertRaisesRegex(BeadsError,'operator authority'):
                gates.resolve_gate(self.queue,self.run,result['gate']['id'],dict(self.evidence(result),authority_basis='verified-external'))
            self.issues.clear()

    def test_design_valid_then_approval_revision_drift_blocks(self):
        self.setup_gate('design')
        self.issues.clear()
        self.issues['btq-target'] = dict(id='btq-target', title='Target', metadata={
            'design_approval':'btq-approval','adr_revision':'artifact-v1'}, status='open',
            dependencies=[{'id':'btq-approval','dependency_type':'blocks'}])
        self.issues['btq-approval'] = dict(id='btq-approval',status='closed',metadata={
            'approved_by':'Liam','approved_at':'retained-timestamp','adr_revision':'artifact-v1',
            'brainstorm_models':['GLM 5.3 Fast','GPT-6 Astra']})
        result=gates.create(self.queue,self.run,self.plan)
        gates.resolve_gate(self.queue,self.run,result['gate']['id'],self.evidence(result))
        gates.check(self.queue,self.queue.show('btq-target'))
        self.issues['btq-approval']['metadata']['adr_revision']='different'
        with self.assertRaises(BeadsError):
            gates.check(self.queue,self.queue.show('btq-target'))

    def test_uncertain_resolution_replay_and_retained_artifact_loss(self):
        result=self.setup_gate()
        evidence=self.evidence(result)
        native=self.queue.bd.side_effect
        def fail(*args):
            if args[:2] == ('gate','resolve'):
                raise BeadsError('uncertain socket')
            return native(*args)
        self.queue.bd.side_effect=fail
        with self.assertRaises(BeadsError):
            gates.resolve_gate(self.queue,self.run,result['gate']['id'],evidence)
        self.queue.bd.side_effect=native
        gates.resolve_gate(self.queue,self.run,result['gate']['id'],evidence)
        Path(evidence['evidence_ref']).write_text('changed')
        with self.assertRaises(BeadsError):
            gates.check(self.queue,self.queue.show('btq-target'))

    def test_cli_creation_resolution_and_recovery(self):
        from harness import cli
        from unittest.mock import patch
        self.setup_gate()
        self.issues.clear()
        self.issues['btq-target']=dict(id='btq-target',metadata={},status='open',dependencies=[])
        path=Path(self.tmp.name)/'plan.json'
        path.write_text(json.dumps(self.plan))
        args=cli.parser().parse_args(['task','test','gate','create','--file',str(path)])
        with patch('harness.cli.Beads',return_value=self.beads):
            result=cli.task_dispatch(args,self.store,self.supervisor,{})
            evidence=self.evidence(result)
            path.write_text(json.dumps(evidence))
            args=cli.parser().parse_args(['task','test','gate','resolve',result['gate']['id'],'--evidence-file',str(path)])
            self.assertEqual(cli.task_dispatch(args,self.store,self.supervisor,{})['status'],'closed')
            self.run['resume_required']=True
            self.store.save(self.run)
            with self.assertRaisesRegex(BeadsError,'recovery'):
                cli.task_dispatch(args,self.store,self.supervisor,{})

    def test_formula_role_gate_preserves_graph_and_blocks_claim(self):
        from harness import task_formulas
        plan=dict(self.plan,formula='deployable-v1')
        group=task_formulas.create(self.store,self.supervisor,self.beads,self.run,plan)
        role=group['implementation']['id']
        plan=dict(key='design',issue_id=role,kind='external',revision='dependency-commit',
                  reason='Cross repository delivery',issuer='operator')
        result=gates.create(self.queue,self.run,plan)
        self.assertEqual(self.beads.ready(self.run),[])
        with self.assertRaises(BeadsError):
            self.beads.claim(self.run,role)
        gates.resolve_gate(self.queue,self.run,result['gate']['id'],self.evidence(result))
        self.assertEqual([i['id'] for i in self.beads.ready(self.run)],[role])
        self.assertNotIn('issue_id',self.run['beads'])

    def test_reserved_gate_after_failed_create_is_not_eligible(self):
        self.setup_gate()
        self.issues.clear()
        self.issues['btq-target']=dict(id='btq-target',metadata={},status='open',dependencies=[])
        native=self.queue.bd.side_effect
        def fail(*args):
            if args[0]=='create':
                raise BeadsError('connection lost before create')
            return native(*args)
        self.queue.bd.side_effect=fail
        with self.assertRaises(BeadsError):
            gates.create(self.queue,self.run,self.plan)
        self.assertEqual(self.beads.ready(self.run),[])
        self.queue.bd.side_effect=native
        gates.create(self.queue,self.run,self.plan)
        self.assertEqual(len(self.issues),2)

    def test_parent_rechecks_completed_role_gate_evidence(self):
        from harness import task_formulas, task_delivery
        group=task_formulas.create(self.store,self.supervisor,self.beads,self.run,dict(self.plan,formula='deployable-v1'))
        role=group['implementation']['id']
        result=gates.create(self.queue,self.run,dict(key='prerequisite',issue_id=role,kind='external',
            revision='external-sha',reason='Deliverable',issuer='operator'))
        gates.resolve_gate(self.queue,self.run,result['gate']['id'],self.evidence(result))
        self.issues[role]['status']='closed'
        self.issues[role]['description']='Unreviewed replacement scope'
        with self.assertRaisesRegex(BeadsError,'scope changed'):
            task_delivery.prior_history(self.queue,group['parent'])
