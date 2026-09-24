"""Exact retained identity repair; no live agents or transport."""
import json
import unittest
from unittest.mock import patch

from harness import task_workspace
from harness.beads import BeadsError
from tests import test_supervisor as fixtures


class NativeRehydrationTest(unittest.TestCase):
    setUp = fixtures.SupervisorTest.setUp
    tearDown = fixtures.SupervisorTest.tearDown
    new_supervisor = fixtures.SupervisorTest.new_supervisor
    start = fixtures.SupervisorTest.start

    def test_restart_live_pane_restores_successor_before_safe_switch(self):
        run = self.start('codex')
        for native in ('original', 'successor'):
            run['native_session_id'] = native
            self.supervisor.persist(run)
        run['native_session_id'] = None
        run['native_turn_state'] = 'idle'
        self.supervisor.persist(run)
        launches = len(self.tmux.launches)
        restarted = self.new_supervisor()
        restarted.tick_run(self.store.get(run['id']))
        saved = self.store.get(run['id'])
        self.assertEqual(saved['native_session_id'], 'successor')
        checkpoint = self.store.root / 'runs' / run['id'] / 'checkpoint.json'
        self.assertEqual(json.loads(checkpoint.read_text())['native_session_id'], 'successor')
        self.assertEqual(len(self.tmux.launches), launches)
        self.assertEqual(self.tmux.sent, [])
        with patch('harness.task_workspace.git', return_value=''), patch.object(restarted, 'launch') as launch:
            task_workspace.switch(restarted, saved, {'path': '/new-owned-checkout'})
        self.assertEqual(launch.call_args.args[1]['native_session_id'], 'successor')
        self.assertTrue(launch.call_args.kwargs['recovering'])

    def test_registry_repair_preserves_existing_identity_and_recovery_hold(self):
        run = self.start('codex')
        run['native_session_id'] = 'registered'
        self.supervisor.persist(run)
        run['native_session_id'] = 'current'
        self.assertFalse(self.store.rehydrate_native_sessions(run))
        self.assertEqual(run['native_session_id'], 'current')
        run.update(native_session_id=None, resume_required=True, native_turn_state='idle')
        self.assertTrue(self.store.rehydrate_native_sessions(run))
        with self.assertRaisesRegex(BeadsError, 'Reconcile recovery'):
            task_workspace.boundary(run)

    def test_registry_scope_and_ambiguous_lineage(self):
        run = self.start('codex')
        with self.store.db:
            self.store.db.executemany('INSERT INTO native_sessions VALUES (?,?,?,?,?)', [
                ('other-run', 'worker', 'foreign', None, 1),
                (run['id'], 'manager', 'manager-id', None, 2),
            ])
        self.store.rehydrate_native_sessions(run)
        self.assertFalse(run.get('native_session_id'))
        self.assertEqual(run['manager']['native_session_id'], 'manager-id')
        for lineage in (
            [('a', None), ('b', None)],
            [('a', 'missing')],
            [('a', 'b'), ('b', 'a')],
            [('a', None), ('b', 'a'), ('c', 'a')],
        ):
            with self.subTest(lineage=lineage), self.store.db:
                self.store.db.execute("DELETE FROM native_sessions WHERE role='worker'")
                self.store.db.executemany('INSERT INTO native_sessions VALUES (?,?,?,?,?)',
                    [(run['id'], 'worker', native, previous, i) for i, (native, previous) in enumerate(lineage)])
                self.assertFalse(self.store.rehydrate_native_sessions(run))
                self.assertFalse(run.get('native_session_id'))
