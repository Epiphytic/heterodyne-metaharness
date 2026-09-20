"""Native-boundary continuation, with real durable inbox and no model workloads."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from harness.continuation import advance, completed
from harness.store import Store
from harness.supervisor import Supervisor
from harness.status import activity
from tests.test_supervisor import FakeTmux, FakeTransport


class ContinuationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.db.close)
        self.tmux = FakeTmux()
        self.now = 1000
        self.supervisor = Supervisor(self.store, self.tmux, FakeTransport(), boot='boot', clock=lambda: self.now)
        self.run = self.supervisor.start('demo', None, 'codex')
        self.run.update(native_session_id='native', native_turn_key=['native', 'turn'],
                        native_turn_state='idle', state='idle', observed_state='unknown',
                        observation={'pane_alive': True, 'summary': '› Ask Codex to do anything'})
        self.run['beads'] = {'pickup_enabled': True, 'issue_id': 'current', 'worker_identity': 'codex:host:' + self.run['id']}
        self.issue = {'id': 'current', 'title': 'Current task', 'status': 'in_progress', 'assignee': self.run['beads']['worker_identity'], 'description': 'Do the assigned work'}
        self.queue = Mock(state=Path(self.temp.name), worker=self.issue['assignee'])
        self.queue.show.return_value = self.issue
        self.beads = Mock(enabled=True)
        self.beads._queue.return_value = self.queue
        self.beads.ready.return_value = []
        self.beads._allowed.return_value = True
        self.supervisor.beads = self.beads
        self.boundary('turn')

    def boundary(self, turn, summary='Implementation remains.'):
        self.run.update(native_turn_key=['native', turn], native_turn_state='idle', state='idle')
        completed(self.run, turn, summary)
        self.supervisor.persist(self.run)

    def advance(self):
        advance(self.supervisor, self.run)
        self.supervisor.drain_inbox(self.run)

    def test_one_send_and_one_check_survive_restart_and_duplicate_completion(self):
        self.advance()
        self.run = self.store.get(self.run['id'])
        completed(self.run, 'turn', 'duplicate')
        self.advance()
        self.assertEqual(len(self.tmux.sent), 1)
        self.beads.ready.assert_called_once()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='queue_continuation_sent'").fetchone()[0], 1)

    def test_empty_queue_is_checked_once(self):
        self.queue.show.return_value = dict(self.issue, status='closed')
        self.advance()
        self.advance()
        self.beads.ready.assert_called_once()
        self.assertEqual(self.tmux.sent, [])

    def test_manager_working_report_does_not_block_native_idle(self):
        self.run['state'] = 'working'
        self.advance()
        self.advance()
        self.beads.ready.assert_called_once()
        self.assertEqual(len(self.tmux.sent), 1)

    def test_completion_after_newer_idle_hook_still_marks_exact_boundary(self):
        from harness.continuation import native_event
        self.run.pop('continuation')
        self.run['native_turn_at'] = 1001
        event = {'kind': 'turn_completed', 'summary': 'More work remains',
                 'id': 'complete', 'turn_id': 'turn', 'at': 1000}
        native_event(self.run, event, 1002)
        self.advance()
        native_event(self.run, event, 1003)
        self.advance()
        self.beads.ready.assert_called_once()
        self.assertEqual(len(self.tmux.sent), 1)

    def test_approval_question_pause_recovery_and_missing_pane_hold(self):
        cases = [({'observed_state': 'awaiting_approval'}, {}),
                 ({'observed_state': 'awaiting_question'}, {}),
                 ({'resume_required': True}, {}), ({}, {'recovery_required': True}),
                 ({}, {'pickup_enabled': False}), ({'observation': {}}, {})]
        for index, (run_changes, bead_changes) in enumerate(cases):
            with self.subTest(index=index):
                original = dict(self.run)
                original_beads = dict(self.run['beads'])
                self.boundary(str(index))
                self.run.update(run_changes)
                self.run['beads'].update(bead_changes)
                self.advance()
                self.run.update(original)
                self.run['beads'] = original_beads
        self.beads.ready.assert_not_called()
        self.assertEqual(self.tmux.sent, [])
        self.boundary('question', 'Which branch should I use?')
        self.advance()
        self.beads.ready.assert_not_called()

    def test_native_btq_pause_is_not_overridden(self):
        (Path(self.temp.name) / 'paused').touch()
        self.advance()
        self.beads.ready.assert_not_called()

    def test_cooldown_and_unchanged_work_do_not_loop(self):
        self.advance()
        self.now += 1
        self.boundary('fast')
        self.advance()
        self.beads.ready.assert_called_once()
        self.now += 100
        self.boundary('unchanged')
        self.advance()
        self.assertEqual(len(self.tmux.sent), 1)
        self.issue['description'] = 'New authorized scope'
        self.now += 100
        self.boundary('changed')
        self.advance()
        self.assertEqual(len(self.tmux.sent), 2)

    def test_direct_steering_preempts_queue_check(self):
        with self.store.db:
            self.store.db.execute('INSERT INTO inbox(id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)',
                                  ('user', self.run['id'], 'Stop', self.now, 'pending', 'worker'))
        self.advance()
        self.beads.ready.assert_not_called()
        self.assertEqual(self.tmux.sent[0][1], 'Stop')

    def test_claim_uses_assign_and_preserves_projection_without_double_send(self):
        from harness.workspace import git
        repo = Path(self.temp.name) / 'repo'
        repo.mkdir()
        git(repo, 'init', '-b', 'main')
        git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
            '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'base')
        self.run.update(repo=str(repo), workdir=str(repo))
        self.queue.show.return_value = dict(self.issue, status='closed')
        new = dict(self.issue, id='next')
        self.beads.ready.return_value = [new]
        def claim(run, identity):
            self.assertEqual(run['native_turn_state'], 'idle')
            run['beads']['issue_id'] = identity
            return new
        self.beads.claim.side_effect = claim
        with patch.object(self.supervisor, 'launch', side_effect=lambda run, target, recovering: self.tmux.launch(target, [])) as launch:
            self.advance()
            self.assertTrue(launch.call_args.kwargs['recovering'])
        self.assertEqual(self.run['native_session_id'], 'native')
        self.assertNotEqual(self.run['workdir'], str(repo))
        self.assertEqual(self.tmux.sent, [])  # Fresh pane observation required.
        self.run['observation'] = {'pane_alive': True, 'summary': '› Ask Codex to do anything'}
        self.supervisor.drain_inbox(self.run)
        saved = self.store.get(self.run['id'])
        self.assertEqual(saved['beads']['task_snapshot']['issue']['id'], 'next')
        self.assertEqual(saved['beads']['issue_id'], 'next')
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM transitions WHERE kind='handoff'").fetchone()[0], 1)
        self.supervisor.drain_inbox(self.run)
        self.assertEqual(len(self.tmux.sent), 1)
        self.beads.claim.assert_called_once()
        # Native working start releases only the start latch; task updates still
        # wait for idle/context receipt through the existing hook contract.
        activity(self.run, 'native', 'next-turn', 'working', 2000)
        self.supervisor.drain_inbox(self.run)
        self.assertEqual(len(self.tmux.sent), 1)

    def test_uncertain_send_never_replays(self):
        self.tmux.send = Mock(side_effect=RuntimeError('socket failure'))
        with self.assertRaises(RuntimeError):
            self.advance()
        self.advance()
        self.tmux.send.assert_called_once()
        self.assertEqual(self.store.db.execute("SELECT state FROM inbox WHERE id LIKE 'continuation:%'").fetchone()[0], 'uncertain')

    def test_external_read_failure_consumes_boundary_without_poll(self):
        self.beads.ready.side_effect = RuntimeError('queue offline')
        with self.assertRaises(RuntimeError):
            self.advance()
        self.run = self.store.get(self.run['id'])
        self.advance()
        self.beads.ready.assert_called_once()

    def test_wrong_owner_never_resumed(self):
        self.queue.show.return_value = dict(self.issue, assignee='another-worker')
        with self.assertRaisesRegex(RuntimeError, 'unverified claim'):
            self.advance()
        self.assertEqual(self.tmux.sent, [])

    def test_aborted_turn_does_not_create_boundary(self):
        self.run.pop('continuation')
        self.run['native_turn_state'] = 'working'
        event = {'kind': 'turn_aborted', 'summary': 'Interrupted', 'id': 'abort', 'turn_id': 'turn'}
        with patch('harness.lifecycle.observe_events', side_effect=[[event], []]):
            self.supervisor.lifecycle(self.run)
        self.assertNotIn('continuation', self.run)

    def test_native_completion_marks_boundary_and_stale_turn_does_not(self):
        self.run.pop('continuation')
        self.run['native_turn_state'] = 'working'
        event = {'kind': 'turn_completed', 'summary': 'More work remains', 'id': 'complete', 'turn_id': 'old'}
        with patch('harness.lifecycle.observe_events', side_effect=[[event], []]):
            self.supervisor.lifecycle(self.run)
        self.assertNotIn('continuation', self.run)
        event.update(id='current-complete', turn_id='turn')
        with patch('harness.lifecycle.observe_events', side_effect=[[event], []]):
            self.supervisor.lifecycle(self.run)
        self.assertFalse(self.run['continuation']['checked'])

    def test_natural_turn_releases_loop_guard(self):
        self.advance()
        activity(self.run, 'native', 'automatic', 'working', 1001)
        self.now = 1100
        self.boundary('automatic')
        self.advance()
        self.assertEqual(len(self.tmux.sent), 1)
        activity(self.run, 'native', 'operator-answer', 'working', 1200)
        self.now = 1300
        self.boundary('operator-answer')
        self.advance()
        self.assertEqual(len(self.tmux.sent), 2)

    def test_late_prompt_holds_continuation_but_not_direct_stop(self):
        advance(self.supervisor, self.run)
        self.run['continuation']['question'] = True
        self.supervisor.drain_inbox(self.run)
        self.assertEqual(self.tmux.sent, [])
        with self.store.db:
            self.store.db.execute('INSERT INTO inbox(id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)',
                                  ('direct-stop', self.run['id'], 'Stop', self.now + 1, 'pending', 'worker'))
        self.supervisor.drain_inbox(self.run)
        self.assertEqual(self.tmux.sent[0][1], 'Stop')

    def test_restart_after_enqueue_sends_once(self):
        advance(self.supervisor, self.run)
        self.run = self.store.get(self.run['id'])
        self.supervisor.drain_inbox(self.run)
        self.supervisor.drain_inbox(self.run)
        self.assertEqual(len(self.tmux.sent), 1)
        self.beads.ready.assert_called_once()

    def test_completion_before_working_footer_clears_does_not_lose_boundary(self):
        self.run['observation']['summary'] = 'Working (3s • esc to interrupt)'
        self.advance()
        self.advance()
        self.beads.ready.assert_not_called()
        self.assertFalse(self.run['continuation']['checked'])
        self.run['observation']['summary'] = '› Ask Codex to do anything'
        self.advance()
        self.beads.ready.assert_called_once()
        self.assertEqual(len(self.tmux.sent), 1)

    def test_failed_check_has_exactly_one_accounting_event(self):
        self.beads.ready.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            self.advance()
        self.run = self.store.get(self.run['id'])
        self.advance()
        rows = self.store.db.execute("SELECT text FROM events WHERE kind='queue_boundary'").fetchall()
        self.assertEqual([r[0] for r in rows], ['error:RuntimeError'])
        self.beads.ready.assert_called_once()

    def test_consumed_boundary_after_crash_accounts_without_retry(self):
        self.run['continuation']['checked'] = True
        self.supervisor.persist(self.run)
        self.run = self.store.get(self.run['id'])
        self.advance()
        self.advance()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='queue_boundary'").fetchone()[0], 1)
        self.beads.ready.assert_not_called()

    def test_close_projection_schedules_once_without_duplicate_native_boundary(self):
        from harness.tasks import observe
        observe(self.store, self.issue)
        self.run = self.store.get(self.run['id'])
        self.run['beads']['pickup_enabled'] = False
        self.advance()
        closed = dict(self.issue, status='closed')
        observe(self.store, closed)
        self.run = self.store.get(self.run['id'])
        self.run['beads']['pickup_enabled'] = True
        self.queue.show.return_value = closed
        completed(self.run, 'turn', 'duplicate receipt')
        self.advance()
        observe(self.store, closed)
        self.run = self.store.get(self.run['id'])
        self.advance()
        self.beads.ready.assert_called_once()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='queue_boundary'").fetchone()[0], 2)

    def test_multiple_completions_are_all_accounted(self):
        self.boundary('second')
        self.run['beads']['pickup_enabled'] = False
        self.advance()
        self.advance()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='queue_boundary'").fetchone()[0], 2)

    def test_task_transition_waits_out_cooldown_without_queue_poll(self):
        from harness.continuation import task_changed
        task_changed(self.run, self.issue)
        self.advance()
        task_changed(self.run, dict(self.issue, status='closed'))
        self.now += 1
        self.advance()
        self.beads.ready.assert_called_once()
        self.now += 60
        self.queue.show.return_value = dict(self.issue, status='closed')
        self.advance()
        self.assertEqual(self.beads.ready.call_count, 2)
