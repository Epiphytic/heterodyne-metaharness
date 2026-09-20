import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness.beads import Beads, BeadsError
from harness.task_order import dependency, graph, order_key, prioritize


class OrderTest(unittest.TestCase):
    def setUp(self):
        self.run = {'id': '00000000-0000-0000-0000-000000000001', 'agent': 'codex',
                    'beads': {'workstream': 'demo'}}
        self.data = {key: {'id': key, 'created_at': str(i), 'status': 'open', 'metadata': {},
                           'dependencies': [], 'assignee': '', 'labels': ['kind:task']}
                     for i, key in enumerate(('btq-a', 'btq-b', 'btq-c'))}
        self.queue = Mock(agent='codex', ws='demo', worker='worker')
        self.queue.matches.return_value = True
        self.queue.design_allowed.return_value = True
        self.queue.show.side_effect = lambda key: copy.deepcopy(self.data[key])
        self.queue.bd.side_effect = self.bd
        self.queue.ready.side_effect = lambda: list(reversed(list(self.data.values())))
        self.beads = Beads({'enabled': True})
        self.beads._queue = Mock(return_value=self.queue)

    def bd(self, *args):
        if args[0] == 'list':
            if '--id' in args:
                return [self.data[args[args.index('--id')+1]]] if args[args.index('--id')+1] in self.data else []
            return list(self.data.values())
        if args[0] == 'update':
            field, value = args[3].split('=', 1)
            self.data[args[1]]['metadata'][field] = json.loads(value)
        elif args[0] == 'create':
            key = args[args.index('--id')+1]
            self.data[key] = dict(id=key, status='open', assignee='', created_at='9',
                                 metadata=json.loads(args[args.index('--metadata')+1]))
        elif args[0] == 'dep':
            item = self.data[args[2]]
            if args[1] == 'add':
                item['dependencies'].append(dict(id=args[3], dependency_type='blocks', status='open'))
            else:
                item['dependencies'] = [e for e in item['dependencies'] if e['id'] != args[3]]
        else:
            self.fail(args)

    def ids(self):
        return [i['id'] for i in self.beads.ready(self.run)]

    def test_fifo_and_multi_priority_with_stable_ties(self):
        self.assertEqual(['btq-a', 'btq-b', 'btq-c'], self.ids())
        prioritize(self.queue, self.run, ['btq-c', 'btq-b'], 'manager')
        self.assertEqual(['btq-c', 'btq-b', 'btq-a'], self.ids())
        prioritize(self.queue, self.run, ['btq-a'], 'manager')
        self.assertEqual(['btq-a', 'btq-c', 'btq-b'], self.ids())
        self.data['btq-a']['created_at'] = self.data['btq-b']['created_at']
        self.assertEqual(self.ids(), self.ids())

    def test_create_default_and_at_top_retry_does_not_reorder(self):
        plain = self.beads.create(self.run, 'Task', 'Description', key='plain')
        self.assertEqual(plain['id'], self.ids()[-1])
        top = self.beads.create(self.run, 'Urgent', 'Description', key='top', at_top=True)
        self.assertEqual(top['id'], self.ids()[0])
        prioritize(self.queue, self.run, ['btq-b'], 'manager')
        repeated = self.beads.create(self.run, 'Urgent', 'Description', key='top', at_top=True)
        self.assertEqual(top['metadata'], repeated['metadata'])
        self.assertEqual('btq-b', self.ids()[0])
        with self.assertRaises(BeadsError):
            self.beads.create(self.run, 'Urgent', 'Description', key='top')

    def test_invalid_batch_makes_no_writes_and_failure_not_retried(self):
        self.data['btq-b']['status'] = 'in_progress'
        with self.assertRaises(BeadsError):
            prioritize(self.queue, self.run, ['btq-a', 'btq-b'], 'manager')
        self.queue.bd.assert_not_called()
        self.data['btq-b']['status'] = 'open'
        def fail(*args):
            if args[0] == 'update':
                raise RuntimeError('uncertain')
            return self.bd(*args)
        self.queue.bd.side_effect = fail
        with self.assertRaises(RuntimeError):
            prioritize(self.queue, self.run, ['btq-a'], 'manager')
        self.assertEqual(1, sum(c.args[0] == 'update' for c in self.queue.bd.call_args_list))

    def test_dependency_graph_chain_cycle_and_management(self):
        dependency(self.queue, self.run, 'add', 'btq-a', 'btq-b', 'manager')
        dependency(self.queue, self.run, 'add', 'btq-b', 'btq-c', 'manager')
        self.queue.ready.return_value = [self.data['btq-c']]
        self.queue.ready.side_effect = None
        result = graph(self.beads, self.run)
        self.assertEqual([['btq-a', 'btq-b'], ['btq-a', 'btq-b', 'btq-c']], result[0]['blocked_by_chains'])
        self.assertFalse(result[0]['eligible'])
        self.assertTrue(result[2]['eligible'])
        dependency(self.queue, self.run, 'remove', 'btq-a', 'btq-b', 'manager')
        self.assertEqual([], graph(self.beads, self.run)[0]['blocked_by_chains'])
        self.data['btq-b']['metadata']['design_approval'] = 'btq-c'
        with self.assertRaises(BeadsError):
            dependency(self.queue, self.run, 'remove', 'btq-b', 'btq-c', 'manager')
        dependency(self.queue, self.run, 'add', 'btq-c', 'btq-b', 'manager')
        with self.assertRaises(BeadsError):
            graph(self.beads, self.run)

    def test_key_resolution_and_wrong_route(self):
        issue = self.beads.create(self.run, 'Task', 'Description', key='external-message')
        self.assertEqual(issue['id'], prioritize(self.queue, self.run, ['external-message'], 'manager')[0]['id'])
        self.queue.matches.return_value = False
        with self.assertRaises(BeadsError):
            prioritize(self.queue, self.run, ['btq-a'], 'manager')

    def test_eight_task_dependency_fixture_is_listed_but_not_all_ready(self):
        names = ['259f', '0e771d', '5a9546', 'ff386b', 'da8101', '3f284a', '0c5a5a', 'd290cf']
        self.data = {name: dict(id=name, status='open', assignee='', metadata={},
                               created_at=str(index), dependencies=[])
                     for index, name in enumerate(names)}
        edges = {'0e771d':['d290cf'], 'da8101':['d290cf'], '5a9546':['0e771d'],
                 '0c5a5a':['0e771d','5a9546','da8101'], 'ff386b':['0c5a5a'], '3f284a':['ff386b']}
        for name, blockers in edges.items():
            self.data[name]['dependencies'] = [dict(id=b, dependency_type='blocks', status='open') for b in blockers]
        self.queue.ready.side_effect = lambda: [self.data['259f'], self.data['d290cf']]
        result = graph(self.beads, self.run)
        self.assertEqual(names, [i['id'] for i in result])
        self.assertEqual(['259f','d290cf'], [i['id'] for i in result if i['eligible']])
        self.assertIn(['3f284a','ff386b','0c5a5a','5a9546','0e771d','d290cf'], result[5]['blocked_by_chains'])
