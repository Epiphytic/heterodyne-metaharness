import copy
import json
import unittest
from unittest.mock import patch
from harness import task_progress as progress, tasks, cli
from harness.beads import BeadsError
from tests import test_task_formulas as fixtures


class ProgressTest(unittest.TestCase):
    setUp = fixtures.FormulaTest.setUp
    bd = fixtures.FormulaTest.bd
    git = fixtures.FormulaTest.git
    create = fixtures.FormulaTest.create
    retained = fixtures.FormulaTest.retained
    evidence = fixtures.FormulaTest.evidence
    stage_evidence = fixtures.FormulaTest.stage_evidence
    finish = fixtures.FormulaTest.finish
    bind = fixtures.FormulaTest.bind

    def test_readonly_projection_no_writes_and_closed_step_releases_next(self):
        group = self.create()
        before = self.store.db.total_changes
        result = progress.read(self.tmp.name, 'test')
        self.assertEqual('cached-projection', result['source'])
        self.assertEqual({'ready', 'blocked'}, {r['view'] for r in result['tasks']})
        self.assertTrue(all(r['eligible'] is None for r in result['tasks']))
        self.assertEqual(before, self.store.db.total_changes)
        self.finish(group, 'investigation')
        tasks.observe(self.store, self.issues[group['investigation']['id']])
        result = progress.read(self.tmp.name, 'test')
        self.assertEqual(2, len(result['tasks']))
        self.assertEqual([group['recommendation']['id']], [i['id'] for i in self.beads.ready(self.run)])
        self.assertEqual('open', self.issues[group['parent']['id']]['status'])

    def test_wrong_route_excluded_and_missing_dependency_explicit(self):
        group = self.create()
        item = self.issues[group['recommendation']['id']]
        item['dependencies'].append({'id': 'missing', 'dependency_type': 'blocks'})
        tasks.observe(self.store, item)
        rows = progress.read(self.tmp.name, 'test')['tasks']
        row = next(r for r in rows if r['id'] == item['id'])
        self.assertFalse(row['eligible'])
        self.assertIn('Missing cached dependency', row['reason'])
        run = copy.deepcopy(self.run)
        run['id'] = 'other-session'
        self.assertEqual([], progress.build(run, self.issues.values(), self.queue.show)['tasks'])

    def test_six_views_and_limits(self):
        group = self.create('deployable-v2')
        for role, expected in [('implementation', 'ready'), ('review', 'awaiting-review'),
                               ('deployment', 'awaiting-deployment'), ('verification', 'awaiting-deployment')]:
            issue = copy.deepcopy(group[role])
            issue['dependencies'] = []
            self.assertEqual(expected, progress.category(issue, self.queue.show))
        item = copy.deepcopy(group['implementation'])
        item['status'] = 'in_progress'
        self.assertEqual('executing', progress.category(item, self.queue.show))
        item['metadata']['design_approval'] = 'gate'
        self.assertEqual('awaiting-approval', progress.category(item, lambda key: {'status': 'open'}))
        with patch.object(progress, 'LIMIT', 1), self.assertRaises(BeadsError):
            progress.build(self.run, self.issues.values(), self.queue.show)

    def test_resumed_step_requires_predecessor_evidence_and_pause_wins(self):
        group = self.create()
        item = self.issues[group['recommendation']['id']]
        item.update(status='in_progress', assignee=self.queue.worker)
        with self.assertRaises(BeadsError):
            self.beads.claim(self.run, item['id'])
        self.finish(group, 'investigation')
        self.assertEqual(item['id'], self.beads.claim(self.run, item['id'])['id'])
        item['dependencies'].append({'id': group['parent']['id'], 'dependency_type': 'blocks'})
        with self.assertRaises(BeadsError):
            self.beads.claim(self.run, item['id'])
        item['dependencies'].pop()
        (self.queue.state / 'paused').touch()
        with self.assertRaisesRegex(BeadsError, 'paused'):
            self.beads.claim(self.run, item['id'])
        self.run['beads']['pickup_enabled'] = False
        self.assertEqual([], self.beads.ready(self.run))

    def test_live_read_uses_bounded_native_queries_without_mutations(self):
        group = self.create()
        original = self.queue.bd.side_effect
        calls = []
        def native(*args):
            calls.append(args)
            if args[0] == 'list':
                return list(self.issues.values())
            if args[0] == 'show':
                return [self.issues[args[1]]]
            if args[0] == 'ready':
                return [group['investigation']]
            return original(*args)
        self.queue.ws = 'test'
        self.queue.bd.side_effect = native
        view = progress.live(self.run, self.beads)
        self.assertEqual('native-beads', view['source'])
        self.assertEqual([group['investigation']['id']], [r['id'] for r in view['tasks'] if r['eligible']])
        self.assertTrue(all(c[0] in ('show', 'list', 'ready') for c in calls))
        self.assertTrue(all('--readonly' in c for c in calls))

    def test_cli_progress_does_not_construct_store(self):
        self.create()
        with patch('harness.cli.Store', side_effect=AssertionError('write path')), patch('builtins.print') as out:
            self.assertEqual(0, cli.main(['--home', self.tmp.name, 'task', 'test', 'progress']))
        self.assertEqual('cached-projection', json.loads(out.call_args.args[0])['source'])

    def test_prerequisite_change_during_native_claim_retains_unbound_hold(self):
        group = self.create()
        issue = self.issues[group['investigation']['id']]
        self.queue.bd.side_effect = lambda *args: [] if args[0] == 'list' else self.bd(*args)
        def claim(run, action, identity):
            issue.update(status='in_progress', assignee=self.queue.worker)
            issue['dependencies'].append({'id': group['parent']['id'], 'dependency_type': 'blocks'})
            return copy.deepcopy(issue)
        with patch.object(self.beads, '_call', side_effect=claim):
            with self.assertRaisesRegex(BeadsError, 'prerequisites'):
                self.beads.claim(self.run, issue['id'])
        self.assertNotIn('issue_id', self.run['beads'])
        self.assertEqual(self.queue.worker, issue['assignee'])
