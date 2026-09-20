import json
import tempfile
import unittest

from harness.store import Store
from harness import transitions as t


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(lambda: self.store.db.close())
        self.run = dict(id='run', name='ws', agent='codex', group_id='group',
                        workdir=self.temp.name, state='working', created_at=1,
                        beads={'issue_id': 'task', 'workstream': 'ws'})
        self.issue = dict(id='task', title='Task title', status='open', description='whole text')
        with self.store.db:
            self.store.save(self.run)

    def observe(self, status, now):
        self.issue['status'] = status
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            t.observe(self.store, self.run, self.issue, now)

    def test_fixed_window_preserves_returns_and_noop(self):
        self.observe('open', 0)
        self.observe('in_progress', 10)
        self.observe('open', 20)
        self.observe('in_progress', 30)
        self.observe('in_progress', 120)
        t.flush(self.store, 129)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 0)
        t.flush(self.store, 130)
        text = self.store.db.execute('SELECT text FROM outbox').fetchone()[0]
        self.assertEqual(text.splitlines(), ['Task title, in_progress, task',
                                           'Task title, open, task', 'Task title, in_progress, task'])
        t.flush(self.store, 999)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 1)

    def test_restart_and_late_flush_do_not_merge_windows(self):
        self.observe('open', 0)
        self.observe('in_progress', 1)
        self.observe('blocked', 121)
        self.store.db.close()
        self.store = Store(self.temp.name)
        t.flush(self.store, 300)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 2)

    def test_first_handoff_complete_multipart_once(self):
        self.issue['description'] = 'x' * 19000
        t.handoff(self.store, self.run, self.issue, 0)
        self.issue['notes'] = 'later'
        t.handoff(self.store, self.run, self.issue, 10)
        t.flush(self.store, 120)
        chunks = [r[0].split('\n', 1)[1] for r in self.store.db.execute('SELECT text FROM outbox ORDER BY rowid')]
        payload = json.loads(''.join(chunks).split('\n', 1)[1])
        self.assertEqual(payload['description'], 'x' * 19000)
        self.assertNotIn('notes', payload)

    def test_immutable_event_identity(self):
        with self.store.db:
            t.append(self.store, self.run, self.issue, 'status', 'open', 'key', now=0)
            t.append(self.store, self.run, self.issue, 'status', 'open', 'key', now=1)
            with self.assertRaises(ValueError):
                t.append(self.store, self.run, self.issue, 'status', 'closed', 'key', now=2)

    def test_lifecycle_history_preserves_each_new_stage(self):
        self.observe('in_progress', 0)
        self.issue['metadata'] = {'harness_lifecycle': [
            {'stage': 'committed', 'digest': 'a'}, {'stage': 'tested', 'digest': 'b'},
            {'stage': 'pr-open', 'digest': 'c'}]}
        self.observe('in_progress', 1)
        self.observe('in_progress', 2)
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT status FROM transitions')],
                         ['committed', 'tested', 'review-requested'])

    def test_retirement_preserves_attempted_unrelated_and_audit(self):
        with self.store.db:
            for key, kind, body, attempts in [('old', 'progress', 'Status: working; native turn: idle. x', 0),
                      ('uncertain', 'progress', 'Status: working; native turn: idle. x', 1),
                      ('custom', 'progress', 'human update', 0)]:
                self.store.db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (key, 'run', kind, body, 0))
                self.store.db.execute('INSERT INTO outbox(id,run_id,group_id,text,created_at,attempts) VALUES(?,?,?,?,?,?)',
                                      (key, 'run', 'group', body, 0, attempts))
        self.assertEqual(t.retire_polls(self.store, 20), 1)
        self.assertEqual(t.retire_polls(self.store, 21), 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events').fetchone()[0], 3)
        self.assertEqual(self.store.db.execute('SELECT event_id FROM transition_retirements').fetchone()[0], 'old')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 2)

    def test_failed_send_retry_and_same_text_return_are_not_damped(self):
        from harness.delivery import deliver
        from tests.test_delivery import Transport
        transport = Transport()
        self.observe('open', 0)
        self.observe('in_progress', 1)
        transport.fail = True
        deliver(self.store, transport, now=121)
        transport.fail = False
        deliver(self.store, transport, now=151)
        self.assertEqual(transport.calls[0][1], transport.calls[1][1])
        self.observe('open', 160)
        self.observe('in_progress', 290)
        deliver(self.store, transport, now=410)
        deliver(self.store, transport, now=411)
        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox WHERE delivered_at IS NULL').fetchone()[0], 0)

    def test_unknown_groups_do_not_starve_known_batches(self):
        with self.store.db:
            for n in range(101):
                t.append(self.store, dict(self.run, id=str(n), group_id=None), self.issue, 'status', 'open', 'key', now=0)
            t.append(self.store, self.run, self.issue, 'status', 'open', 'key', now=0)
        t.flush(self.store, 120)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 1)

    def test_poll_and_turn_prose_audit_only_with_bound_task(self):
        from harness.tasks import observe
        observe(self.store, self.issue)
        with self.store.db:
            for kind in ('progress', 'manager_reply', 'turn_completed', 'working'):
                self.store.event(self.run, kind, 'pane prose changed', kind)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events').fetchone()[0], 4)

    def test_native_stop_repetition_and_state_return(self):
        from harness.tasks import observe
        observe(self.store, self.issue)
        with self.store.db:
            for key, kind in enumerate(('agent-crashed', 'agent-crashed', 'recovered', 'agent-crashed')):
                self.store.event(self.run, kind, 'timestamp changes ' + str(key), str(key))
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT status FROM transitions ORDER BY seq')],
                         ['agent-crashed', 'agent-recovered', 'agent-crashed'])
        self.assertEqual(self.store.get('run')['beads']['task_snapshot']['issue']['status'], 'open')

    def test_real_observe_scope_routed_owner_and_no_timestamp_notice(self):
        from harness.tasks import observe, worker
        self.issue.update(assignee=worker(self.run), status='in_progress')
        observe(self.store, self.issue)
        self.issue['updated_at'] = 'later'
        observe(self.store, self.issue)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM transitions').fetchone()[0], 0)
        self.issue['acceptance_criteria'] = 'new scope'
        revision = observe(self.store, self.issue)
        self.assertEqual(self.store.db.execute('SELECT status FROM transitions').fetchone()[0], 'scope-changed')
        self.assertIn(revision, self.store.db.execute('SELECT text FROM inbox ORDER BY rowid DESC LIMIT 1').fetchone()[0])
        self.issue['assignee'] = 'other-worker'
        count = self.store.db.execute('SELECT count(*) FROM inbox').fetchone()[0]
        observe(self.store, self.issue)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox').fetchone()[0], count)

    def test_steering_retains_full_question_and_gate_actor(self):
        from harness.tasks import observe
        from harness.operator_asks import enqueue
        observe(self.store, self.issue)
        enqueue(self.store, self.run, 'full question', 'ask-key', 'question')
        with self.store.db:
            t.steering(self.store, self.run, 'exact steering', 'message-id', 0)
        row = self.store.db.execute("SELECT payload FROM transitions WHERE kind='steering'").fetchone()
        payload = json.loads(row[0])
        self.assertEqual(payload['outstanding_questions'][0]['text'], 'full question')
        self.assertEqual(payload['steering'], 'exact steering')
        gate = {'id':'gate', 'metadata':{'harness_gate':{'kind':'deployment'},
            'harness_gate_resolution':{'digest':'receipt', 'evidence':{
                'issuer':'actual-admin', 'authority_basis':'operator-reaction', 'evidence_ref':'retained'}}}}
        t.gate(self.store, self.run, self.issue, gate, resolved=True)
        t.gate(self.store, self.run, self.issue, gate, resolved=True)
        payload = json.loads(self.store.db.execute("SELECT payload FROM transitions WHERE kind='gate'").fetchone()[0])
        self.assertEqual(payload['actor'], 'actual-admin')
        self.assertEqual(payload['authority_basis'], 'operator-reaction')

    def test_rendered_unacknowledged_poll_is_never_retired(self):
        from harness.operator_asks import initialize
        with self.store.db:
            initialize(self.store.db)
            self.store.db.execute('INSERT INTO events VALUES (?,?,?,?,?)', ('old','run','progress','Status: working; native turn: idle.',0))
            self.store.db.execute('INSERT INTO outbox(id,run_id,text,created_at) VALUES(?,?,?,?)', ('old','run','x',0))
            self.store.db.execute('INSERT INTO outbox_rendered VALUES (?,?,?)', ('old','account','x'))
        self.assertEqual(t.retire_polls(self.store, 10), 0)

    def test_notice_cli_requires_applied_evidence_and_bound_route(self):
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock
        from harness.transition_cli import dispatch
        from harness.beads import BeadsError
        evidence = dict(issue_id='task', kind='approval-applied', key='native-resolution-1',
                        subject='exact command', actor='admin-key', authority_basis='operator-reaction',
                        evidence_ref='retained:resolution', result='consented')
        path = Path(self.temp.name) / 'evidence.json'
        args = SimpleNamespace(name='run', notice_action='record', file=str(path))
        beads = Mock()
        beads._queue.return_value.show.return_value = self.issue
        beads._queue.return_value.matches.return_value = True
        path.write_text(json.dumps(evidence))
        with self.assertRaises(BeadsError):
            dispatch(args, self.store, beads)
        evidence['result'] = 'applied'
        path.write_text(json.dumps(evidence))
        first = dispatch(args, self.store, beads)
        self.assertEqual(first, dispatch(args, self.store, beads))
        self.assertFalse(first['native_approval_changed'])
        evidence['subject'] = 'different command'
        path.write_text(json.dumps(evidence))
        with self.assertRaises(ValueError):
            dispatch(args, self.store, beads)
        beads._queue.return_value.matches.return_value = False
        with self.assertRaises(BeadsError):
            dispatch(args, self.store, beads)

    def test_bound_turn_retains_boundary_without_model_assessment(self):
        from unittest.mock import Mock, patch
        from harness.supervisor import Supervisor
        self.run['beads']['task_snapshot'] = {'issue': self.issue}
        supervisor = Mock(store=self.store, clock=lambda: 100)
        event = {'kind': 'turn_completed', 'summary': 'done', 'id': 'turn-1'}
        with patch('harness.lifecycle.observe_events', return_value=[event]), \
             patch('harness.continuation.native_event') as boundary:
            Supervisor.lifecycle(supervisor, self.run)
        boundary.assert_called_once_with(self.run, event, 100)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
