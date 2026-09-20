import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness.beads import Beads, BeadsError
from harness.cli import parser, task_dispatch
from harness.store import Store
from harness.task_workspace import prepare
from harness.workspace import git
from harness.tasks import readonly


class InterruptTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        repo = self.root / 'repo'; repo.mkdir()
        git(repo, 'init', '-b', 'main')
        git(repo, 'config', 'user.name', 'Fixture')
        git(repo, 'config', 'user.email', 'fixture@example.test')
        (repo/'source').write_text('base')
        git(repo, 'add', '.')
        git(repo, '-c', 'commit.gpgsign=false', 'commit', '-m', 'fixture')
        self.store = Store(self.root/'home'); self.addCleanup(self.store.db.close)
        self.run = {'id':'00000000-0000-0000-0000-000000000001', 'name':'fixture', 'agent':'codex',
                    'repo':str(repo), 'workdir':str(repo), 'state':'idle', 'created_at':1,
                    'native_session_id':'native', 'native_turn_state':'idle', 'config':{},
                    'beads':{'workstream':'demo', 'issue_id':'btq-old', 'pickup_enabled':True}}
        record = prepare(self.run, self.store.root, 'btq-old', 'bootstrap')
        self.run['workdir'] = record['path']
        self.queue = Mock(agent='codex', ws='demo', worker='codex:fixture:'+self.run['id'], state=self.root/'q')
        self.queue.state.mkdir()
        self.data = {key:dict(id=key, status=status, assignee=owner, metadata={}) for key,status,owner in
                     [('btq-old','in_progress',self.queue.worker), ('btq-new','open','')]}
        self.queue.show.side_effect = lambda key: copy.deepcopy(self.data[key])
        self.queue.matches.return_value = self.queue.design_allowed.return_value = True
        self.queue.ready.side_effect = lambda: [copy.deepcopy(i) for i in self.data.values() if i['status']=='open']
        self.queue.bd.side_effect = self.bd
        self.beads = Beads({'enabled':True}); self.beads._queue = Mock(return_value=self.queue)
        self.supervisor = Mock()
        from harness.supervisor import Supervisor
        self.supervisor.store = self.store
        self.supervisor.persist.side_effect = lambda run: Supervisor.persist(self.supervisor, run)
        self.supervisor.persist(self.run)

    def bd(self, *args):
        if args[0]=='list':
            state = args[args.index('--status')+1]
            return [copy.deepcopy(i) for i in self.data.values() if i['status']==state]
        if args[0]=='update':
            issue = self.data[args[1]]
            if '--claim' in args: issue.update(status='in_progress', assignee=self.queue.worker)
            else:
                key,value=args[3].split('=',1);issue['metadata'][key]=json.loads(value)
            return
        self.fail(args)

    def dispatch(self, *args):
        with patch('harness.cli.Beads',return_value=self.beads):
            return task_dispatch(parser().parse_args(['task','fixture',*args]), self.store,self.supervisor,{})

    def test_actual_drop_claim_checkpoint_and_resume_retains_worktree(self):
        old_path=self.run['workdir']
        self.dispatch('drop-everything','btq-new','--issuer','manager','--reason','operator priority')
        run=self.store.get('fixture')
        self.assertEqual('btq-new',run['beads']['issue_id'])
        self.assertEqual('parked',run['task_parks']['btq-old']['state'])
        self.assertEqual('in_progress',self.data['btq-old']['status'])
        self.assertEqual('manager',self.data['btq-old']['metadata']['harness_interruption']['issuer'])
        self.assertEqual('btq-new',readonly(self.store.root.parent,'fixture','btq-new')['snapshot']['issue']['id'])
        self.assertEqual('native',run['native_session_id'])
        self.data['btq-new']['status']='closed'
        self.dispatch('claim','btq-old')
        resumed=self.store.get('fixture')
        self.assertEqual(old_path,resumed['workdir'])
        self.assertEqual('resumed',resumed['task_parks']['btq-old']['state'])
        self.assertEqual(2,self.supervisor.launch.call_count)

    def test_busy_dirty_paused_and_approval_fail_before_mutation(self):
        for key,value in [('native_turn_state','working'),('observed_state','awaiting_approval'),('resume_required',True)]:
            run=copy.deepcopy(self.run);run[key]=value
            self.supervisor.persist(run)
            with self.assertRaises(BeadsError):
                self.dispatch('drop-everything','btq-new','--issuer','manager','--reason','priority')
        self.supervisor.persist(self.run)
        (Path(self.run['workdir'])/'source').write_text('uncommitted')
        with self.assertRaises(BeadsError):
            self.dispatch('drop-everything','btq-new','--issuer','manager','--reason','priority')
        self.queue.bd.assert_not_called()
        self.supervisor.tmux.stop.assert_not_called()

    def test_failed_claim_keeps_durable_park_and_no_automatic_resume(self):
        original=self.queue.bd.side_effect
        def fail(*args):
            if '--claim' in args: raise RuntimeError('uncertain claim')
            return original(*args)
        self.queue.bd.side_effect=fail
        with self.assertRaisesRegex(RuntimeError,'uncertain'):
            self.dispatch('drop-everything','btq-new','--issuer','manager','--reason','priority')
        run=self.store.get('fixture')
        self.assertEqual('parked',run['task_parks']['btq-old']['state'])
        self.assertTrue(run['beads']['recovery_required'])
        self.assertFalse(run['beads']['pickup_enabled'])
        from harness.continuation import _select
        self.supervisor.beads=self.beads
        self.assertIsNone(_select(self.supervisor,run,self.queue))
        self.supervisor.tmux.stop.assert_not_called()
