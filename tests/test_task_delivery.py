import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness import cli, task_delivery as delivery, task_stages as stages, tasks
from harness.beads import Beads, BeadsError
from harness.store import Store


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.addCleanup(self.store.db.close)
        self.run = dict(id='b61d651b-8206-52d9-8965-d30d937dd103', name='test', agent='codex',
                        state='active', created_at=1, workdir='/owned', beads={'workstream': 'test'})
        self.store.save(self.run)
        self.issues = {}
        self.queue = Mock(agent='codex', worker=tasks.worker(self.run), state=Path(self.tmp.name))
        self.queue.show.side_effect = lambda key: copy.deepcopy(self.issues[key])
        self.queue.matches.return_value = True
        self.queue.design_allowed.return_value = True
        self.queue.bd.side_effect = self.bd
        self.queue.ready.side_effect = lambda: [copy.deepcopy(i) for i in self.issues.values()
            if i['status'] == 'open' and not any(e['dependency_type'] == 'blocks' and
                self.issues[e['id']]['status'] != 'closed' for e in i['dependencies'])]
        self.beads = Beads({'enabled': True})
        self.beads._queue = Mock(return_value=self.queue)
        self.beads.show = lambda run, key: self.queue.show(key)
        self.supervisor = Mock()
        self.supervisor.persist.side_effect = self.store.save
        self.plan = dict(key='request', title='Deliver change', description='Approved scope',
                         routes={role: 'test' for role in delivery.ROLES})
        self.sha = 'a' * 40
        for module in (delivery, stages):
            patcher = patch.object(module, 'git', side_effect=self.git)
            patcher.start()
            self.addCleanup(patcher.stop)

    def git(self, repo, *args):
        if args[0] in ('status', 'merge-base'):
            return ''
        if args[0] == 'log':
            return 'G'
        if args[0] == 'branch':
            return 'bead/test'
        if args == ('rev-parse', '--show-toplevel'):
            return repo
        return self.sha

    def bd(self, *args):
        if args[0] == 'list':
            key = args[args.index('--id') + 1]
            return [self.issues[key]] if key in self.issues else []
        if args[0] == 'create':
            value = lambda flag: args[args.index(flag) + 1]
            key = value('--id')
            self.assertNotIn(key, self.issues)
            self.issues[key] = dict(id=key, title=value('--title'), description=value('--description'),
                metadata=json.loads(value('--metadata')), labels=value('--labels').split(','),
                status='open', assignee='', dependencies=[])
        elif args[:2] == ('dep', 'add'):
            self.issues[args[2]]['dependencies'].append({'id': args[3], 'dependency_type': args[-1]})
        elif args[0] == 'update':
            self.issues[args[1]]['metadata'] = json.loads(args[-1])
        else:
            raise AssertionError(args)

    def create(self):
        plan = Path(self.tmp.name) / 'plan.json'
        plan.write_text(json.dumps(self.plan))
        args = cli.parser().parse_args(['task', 'test', 'delivery', '--file', str(plan)])
        with patch('harness.cli.Beads', return_value=self.beads):
            return cli.task_dispatch(args, self.store, self.supervisor, {})

    def evidence(self):
        return dict(commit=self.sha, evidence_ref='retained', result='passed', full_suite=True,
                    command='test-suite', log_sha256='b'*64, pr_url='rad://private/patch', remote='rad',
                    pushed_commit=self.sha, merge_authority='operator', review_ref='actual-review',
                    deployment_authority='operator', applicable=True, target='service',
                    deployed_revision=self.sha, live_verification_ref='actual-live-probe')

    def bind(self, issue):
        issue.update(status='in_progress', assignee=self.queue.worker)
        self.run['beads']['issue_id'] = issue['id']
        self.store.save(self.run)
        tasks.record_claim_baseline(self.store, self.run, issue)

    def finish(self, role, group):
        issue = self.issues[group[role]['id']]
        self.bind(issue)
        for stage in delivery.STEPS[role]:
            stages.record(self.store, self.beads, self.run, issue['id'], stage, self.evidence())
        delivery.close_step(self.queue, self.run, issue)
        tasks.assert_close_current(self.store, self.run, issue)
        issue['status'] = 'closed'
        tasks.observe(self.store, issue)

    def test_admission_replay_edges_projection_no_claim(self):
        group = self.create()
        calls = self.queue.bd.call_args_list[:]
        self.create()
        self.assertEqual(len(self.issues), 4)
        self.assertEqual(sum(c.args[0] == 'create' for c in self.queue.bd.call_args_list), 4)
        self.assertEqual(sum(c.args[:2] == ('dep', 'add') for c in self.queue.bd.call_args_list), 5)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0], 4)
        self.assertNotIn('issue_id', self.store.get('test')['beads'])
        self.assertEqual([i['id'] for i in self.beads.ready(self.run)], [group['implementation']['id']])
        shown = tasks.readonly(Path(self.tmp.name), 'test', group['review']['id'])
        self.assertEqual(set(shown['delivery_steps']), {'parent', *delivery.ROLES})
        self.plan['description'] = 'changed request'
        with self.assertRaisesRegex(BeadsError, 'different content'):
            self.create()

    def test_independent_close_parent_gating_and_pinned_artifact(self):
        group = self.create()
        parent = self.issues[group['parent']['id']]
        with self.assertRaisesRegex(BeadsError, 'not closed'):
            delivery.close_step(self.queue, self.run, parent)
        self.finish('implementation', group)
        self.assertEqual(parent['status'], 'open')
        self.assertEqual([i['id'] for i in self.beads.ready(self.run)], [group['review']['id']])
        # Later owner's checkout differs; implementation attribution must remain /owned.
        self.run['workdir'] = '/review-owned'
        self.finish('review', group)
        with self.assertRaisesRegex(BeadsError, 'not closed'):
            delivery.close_step(self.queue, self.run, parent)
        self.run['workdir'] = '/deploy-owned'
        self.finish('deployment', group)
        self.assertEqual([i['id'] for i in self.beads.ready(self.run)], [parent['id']])
        delivery.close_step(self.queue, dict(self.run, workdir='/unrelated'), parent)
        self.assertEqual(delivery.artifact(self.issues[group['implementation']['id']])['checkout'], '/owned')
        self.issues[group['deployment']['id']]['metadata']['harness_lifecycle'][-2]['evidence']['result'] = 'failed'
        with self.assertRaises(BeadsError):
            delivery.close_step(self.queue, self.run, parent)

    def test_partial_creation_retry_and_missing_edge_fail_closed(self):
        original = self.queue.bd.side_effect
        def fail(*args):
            if args[:2] == ('dep', 'add'):
                raise BeadsError('uncertain connection')
            return original(*args)
        self.queue.bd.side_effect = fail
        with self.assertRaises(BeadsError):
            self.create()
        self.assertEqual(self.beads.ready(self.run), [])
        self.queue.bd.side_effect = original
        group = self.create()
        self.assertEqual(len(self.issues), 4)
        self.assertEqual(len(self.beads.ready(self.run)), 1)
        self.issues[group['review']['id']]['dependencies'] = []
        self.assertEqual(self.beads.ready(self.run), [])

    def test_stage_retry_wrong_owner_and_no_implicit_approval(self):
        group = self.create()
        issue = self.issues[group['implementation']['id']]
        self.bind(issue)
        stages.record(self.store, self.beads, self.run, issue['id'], 'committed', self.evidence())
        count = self.queue.bd.call_count
        stages.record(self.store, self.beads, self.run, issue['id'], 'committed', self.evidence())
        self.assertEqual(self.queue.bd.call_count, count)
        issue['assignee'] = 'another-worker'
        with self.assertRaises(BeadsError):
            stages.record(self.store, self.beads, self.run, issue['id'], 'tested', self.evidence())
        self.queue.design_allowed.return_value = False
        self.assertFalse(self.beads._allowed(self.queue, issue))

    def test_contract_is_scope_but_recorded_artifact_is_evidence(self):
        group = self.create()
        issue = group['implementation']
        before = tasks.scope_fingerprint(issue)
        issue['metadata']['harness_delivery_artifact'] = {'checkout': '/owned', 'commit': self.sha}
        self.assertEqual(before, tasks.scope_fingerprint(issue))
        issue['metadata'][delivery.FIELD]['role'] = 'review'
        self.assertNotEqual(before, tasks.scope_fingerprint(issue))

    def test_separate_role_routes_and_readonly_artifacts(self):
        for number, role in enumerate(('review', 'deployment'), 1):
            run = copy.deepcopy(self.run)
            run.update(id=f'00000000-0000-4000-8000-{number:012d}', name=role)
            self.store.save(run)
            self.plan['routes'][role] = role
        group = self.create()
        self.finish('implementation', group)
        shown = tasks.readonly(Path(self.tmp.name), 'review', group['review']['id'])
        self.assertEqual(shown['delivery_steps']['implementation']['artifact']['commit'], self.sha)
        self.assertNotIn('issue_id', self.store.get('review')['beads'])
        with self.assertRaisesRegex(BeadsError, 'not routed'):
            tasks.readonly(Path(self.tmp.name), 'review', group['deployment']['id'])

    def test_cli_close_step_frees_implementation_only_and_repeat_noop(self):
        group = self.create()
        issue = self.issues[group['implementation']['id']]
        self.bind(issue)
        for stage in delivery.STEPS['implementation']:
            stages.record(self.store, self.beads, self.run, issue['id'], stage, self.evidence())
        calls = []
        def native(run, action, key, *args):
            calls.append((action, key))
            self.issues[key]['status'] = 'closed'
        self.beads._call = native
        args = cli.parser().parse_args(['task', 'test', 'close', issue['id'], '--evidence-file', '/retained'])
        with patch('harness.cli.Beads', return_value=self.beads):
            cli.task_dispatch(args, self.store, self.supervisor, {})
            cli.task_dispatch(args, self.store, self.supervisor, {})
        self.assertEqual(calls, [('close', issue['id'])])
        self.assertEqual(self.issues[group['parent']['id']]['status'], 'open')
        self.assertEqual(self.issues[group['review']['id']]['status'], 'open')
