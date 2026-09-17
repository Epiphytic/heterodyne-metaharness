import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from harness import task_workspace as work
from harness.workspace import git
from harness.beads import BeadsError


class TaskWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.repo=self.root/'repo';self.repo.mkdir()
        git(self.repo,'init','-b','main')
        git(self.repo,'config','user.name','Fixture');git(self.repo,'config','user.email','fixture@example.test')
        (self.repo/'source').write_text('base')
        (self.repo/'.gitignore').write_text('.venv/\n')
        git(self.repo,'add','.');git(self.repo,'-c','commit.gpgsign=false','commit','-m','fixture')
        self.run={'id':'run','repo':str(self.repo),'workdir':str(self.repo),'agent':'codex',
                  'state':'idle','native_session_id':'native','native_turn_state':'idle',
                  'beads':{'issue_id':'old'},'config':{'extra_args':['-C',str(self.repo)]}}

    def test_isolation_reuse_no_network_and_idempotent_base(self):
        (self.repo/'.venv').mkdir();(self.repo/'.venv'/'package').write_text('installed')
        one=work.prepare(self.run,self.root,'issue-one','old')
        two=work.prepare(self.run,self.root,'issue-two','old')
        self.assertNotEqual(one['path'],two['path'])
        (Path(one['path'])/'source').write_text('changed')
        (Path(one['path'])/'.venv'/'package').write_text('changed dependency')
        self.assertEqual((Path(two['path'])/'source').read_text(),'base')
        self.assertEqual((self.repo/'.venv'/'package').read_text(),'installed')
        self.assertEqual(work.prepare(self.run,self.root,'issue-one','issue-one'),one)
        self.assertEqual(git(one['path'],'rev-parse','--git-common-dir'),git(two['path'],'rev-parse','--git-common-dir'))

    def test_migration_no_mutation_and_dirty_boundary_rejected(self):
        (self.repo/'source').write_text('valuable')
        self.assertIsNone(work.prepare(self.run,self.root,'old','old'))
        with self.assertRaisesRegex(BeadsError,'Uncommitted'):work.prepare(self.run,self.root,'new','old')
        self.assertEqual((self.repo/'source').read_text(),'valuable')

    def test_safe_switch_retains_native_and_manager_and_no_prompt(self):
        record=work.prepare(self.run,self.root,'new','old')
        supervisor=Mock()
        work.switch(supervisor,self.run,record)
        self.assertEqual(self.run['native_session_id'],'native')
        self.assertEqual(self.run['workdir'],record['path'])
        self.assertEqual(self.run['config']['extra_args'],['-C',record['path']])
        self.assertEqual(self.run['launch_prompt'],'')
        supervisor.tmux.stop.assert_called_once_with(self.run)
        supervisor.launch.assert_called_once_with(self.run,self.run,recovering=True)
        work.switch(supervisor,self.run,record)
        supervisor.launch.assert_called_once()

    def test_busy_approval_and_recovery_do_not_stop_pane(self):
        for key,value in [('native_turn_state','working'),('observed_state','awaiting_approval'),('resume_required',True)]:
            run=dict(self.run);run[key]=value
            with self.assertRaises(BeadsError):work.prepare(run,self.root,'new','old')

    def test_actual_claim_moves_checkpoint_and_hook_cwd_together(self):
        from harness import cli, tasks, brain_hooks
        from harness.store import Store
        store=Store(self.root/'home');self.addCleanup(store.db.close)
        self.run.update(name='fixture',created_at=1)
        with store.db:store.save(self.run)
        facade=Mock(config={})
        def claim(run,issue_id):
            run['beads']['issue_id']=issue_id
            return {'id':issue_id,'title':'New task','status':'in_progress','assignee':tasks.worker(run)}
        facade.claim.side_effect=claim
        supervisor=Mock()
        def persist(run):
            with store.db:store.save(run)
            store.checkpoint(run)
        supervisor.persist.side_effect=persist
        args=cli.parser().parse_args(['task','run','claim','new'])
        with patch.object(cli,'Beads',return_value=facade):cli.task_dispatch(args,store,supervisor,{})
        current=store.get('run')
        self.assertEqual(current['beads']['task_snapshot']['issue']['id'],'new')
        self.assertEqual(tasks.readonly(store.root.parent,'run','new')['snapshot']['issue']['id'],'new')
        with patch.dict('os.environ',{'HERMES_WORKSTREAM_RUN':'run','HERMES_WORKSTREAM_ROLE':'worker'}):
            self.assertEqual(brain_hooks.session_key(store,'codex',{'session_id':'native','cwd':current['workdir']}),'run:worker')
            with self.assertRaises(ValueError):brain_hooks.session_key(store,'codex',{'session_id':'native','cwd':str(self.repo)})
        self.assertEqual(current['native_session_id'],'native')
