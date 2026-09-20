import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from harness.babysitter_queue import check
from install_babysitter_queue import plan


class BabysitterQueueTest(unittest.TestCase):
    def test_nudge_then_escalate_once_retry_failed_enqueue(self):
        calls = []
        ready = Mock(return_value=[{'id':'one'}])
        nudge = Mock(side_effect=lambda: calls.append('nudge'))
        escalate = Mock(side_effect=lambda *args: calls.append('ask') or len(calls) > 2)
        state = {}
        info = {'name':'fixture'}
        check(info, state, ready, nudge, escalate, lambda: 1000)
        check(info, state, ready, nudge, escalate, lambda: 1001)
        self.assertEqual(calls, ['nudge','ask'])
        check(info, state, ready, nudge, escalate, lambda: 1061)
        check(info, state, ready, nudge, escalate, lambda: 1122)
        self.assertEqual(calls, ['nudge','ask','ask'])
        nudge.assert_called_once()

    def test_uncertain_nudge_escalates_without_retry(self):
        ready = Mock(return_value=[{'id':'one'}])
        nudge = Mock(side_effect=RuntimeError('uncertain'))
        escalate = Mock(return_value=True)
        state = {}
        for at in (1000,1061):
            check({'name':'fixture'}, state, ready, nudge, escalate, lambda:at)
        nudge.assert_called_once()
        self.assertIn('uncertain', escalate.call_args.args[1])

    def test_empty_queue_is_quiet_and_new_episode_can_notify(self):
        ready = Mock(return_value=[])
        nudge, escalate = Mock(), Mock(return_value=True)
        state = {}
        check({'name':'fixture'},state,ready,nudge,escalate,lambda:1000)
        nudge.assert_not_called()
        ready.return_value=[{'id':'one'}]
        check({'name':'fixture'},state,ready,nudge,escalate,lambda:1061)
        self.assertEqual(escalate.call_count,1)

    def test_installer_is_bounded_and_idempotent(self):
        source = '''def nudge_idle_worker(pane, pinfo, pstate):
    pass

def poll_pane(pane, pinfo, state):
    pstate = state
    if pane:
        pstate["idle_polls"] = 0
    else:
        pstate["idle_polls"] += 1

def unrelated():
    return "preserved"
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'babysitter.py'
            path.write_text(source)
            result = plan(path)
            path.write_text(result['files'][0]['replacement'])
            self.assertIn('return "preserved"',path.read_text())
            self.assertEqual(plan(path)['files'],[])


class DurableBabysitterTest(unittest.TestCase):
    def setUp(self):
        from harness.store import Store
        from harness.supervisor import Supervisor
        from tests.test_supervisor import FakeTmux, FakeTransport
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.addCleanup(self.store.db.close)
        self.supervisor = Supervisor(self.store, FakeTmux(), FakeTransport(), boot='fixture')
        self.owner = self.supervisor.start('fixture', None, 'codex')
        self.owner.update(native_turn_state='idle', native_turn_key=['native','turn'], state='idle',
                          observation={'pane_alive':True, 'summary':'idle'}, observed_state='idle',
                          beads={'pickup_enabled':True})
        self.supervisor.persist(self.owner)
        self.info = {'name':'fixture', 'pane_id':self.owner['pane_id']}
        self.supervisor.beads = Mock(enabled=True)
        self.supervisor.beads._queue.return_value.state = Path(self.tmp.name)

    def test_restart_dedup_and_native_pause_then_delivery(self):
        from harness.babysitter_queue import enqueue
        enqueue(self.store, self.info)
        enqueue(self.store, self.info)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox').fetchone()[0],1)
        paused = Path(self.tmp.name)/'paused'
        paused.touch()
        self.supervisor.drain_inbox(self.owner)
        self.assertEqual(self.supervisor.tmux.sent,[])
        paused.unlink()
        self.supervisor.drain_inbox(self.owner)
        enqueue(self.store, self.info)
        self.supervisor.drain_inbox(self.owner)
        self.assertEqual(len(self.supervisor.tmux.sent),1)

    def test_native_turn_change_retires_old_nudge(self):
        from harness.babysitter_queue import enqueue
        enqueue(self.store, self.info)
        self.owner['native_turn_key']=['native','next']
        self.supervisor.drain_inbox(self.owner)
        self.assertEqual(self.supervisor.tmux.sent,[])
        self.assertEqual(self.store.db.execute('SELECT state FROM inbox').fetchone()[0],'superseded')

    def test_approval_and_recovery_hold_never_send(self):
        from harness.babysitter_queue import enqueue
        enqueue(self.store, self.info)
        for change in ({'observed_state':'awaiting_approval'}, {'resume_required':True}):
            current = dict(self.owner, **change)
            self.supervisor.drain_inbox(current)
        self.assertEqual(self.supervisor.tmux.sent,[])

    def test_queue_and_escalation_do_not_nest_store_lock(self):
        from unittest.mock import patch
        from harness.babysitter_queue import run
        from harness.store import Store
        def read(*args):
            with self.store.lock(blocking=False):
                return [{'id':'ready'}]
        def ask(*args):
            with self.store.lock(blocking=False):
                return True
        adapter_store = Store(self.tmp.name)
        with patch('harness.store.Store', return_value=adapter_store):
            run(self.info, {}, {'queue_open_items':read, 'escalate':ask})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox').fetchone()[0],1)

    def test_automatic_turn_cannot_start_another_nudge_loop(self):
        from harness.babysitter_queue import enqueue
        self.owner['continuation']={'automatic_turn':self.owner['native_turn_key']}
        self.supervisor.persist(self.owner)
        with self.assertRaises(ValueError):
            enqueue(self.store, self.info)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM inbox').fetchone()[0],0)

    def test_direct_steering_precedes_held_nudge(self):
        from harness.babysitter_queue import enqueue
        enqueue(self.store, self.info)
        self.owner['continuation']={'sent_at':self.supervisor.clock()}
        with self.store.db:
            self.store.db.execute("INSERT INTO inbox(id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)",
                                  ('direct',self.owner['id'],'Stop after saving',self.supervisor.clock()+1,'pending','worker'))
        self.supervisor.drain_inbox(self.owner)
        row=self.store.db.execute("SELECT state FROM inbox WHERE id='direct'").fetchone()
        self.assertEqual(row[0],'submitted')
        self.assertEqual(len(self.supervisor.tmux.sent),1)
