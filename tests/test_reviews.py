import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.store import Store
from harness import reviews as r
from harness.review_dispatch import tick
from harness.beads import BeadsError


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.addCleanup(self.store.db.close)
        self.issue = dict(id='task', title='Implement', description='Scope', status='in_progress', assignee='worker')
        self.run = dict(id='run', name='ws', agent='codex', group_id='group', workdir=self.tmp.name,
                        state='working', created_at=0, native_turn_key=['native', 'turn'], native_turn_state='working',
                        beads=dict(issue_id='task',worker_identity='worker',task_snapshot={'issue':self.issue}))
        with self.store.db:
            self.store.save(self.run)
        r.initialize(self.store.db)
        artifact = Path(self.tmp.name) / 'tests.log'
        artifact.write_text('Actual test evidence')
        self.request = dict(key='done',commit='a'*40,native_turn_key=['native','turn'],
                            artifacts=[dict(path=str(artifact),sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())])

    @staticmethod
    def git_read(cwd, *args):
        return {'text': 'a'*40 if 'rev-parse' in args else ''}

    def handoff(self):
        with patch('harness.review_evidence.git_read', side_effect=self.git_read):
            return r.handoff(self.store,self.run,self.issue,self.request,10)['review_id']

    def result(self, key, blocking=None):
        from harness.review_dispatch import directory
        target=directory(self.store,key)
        target.mkdir(parents=True,exist_ok=True)
        raw=json.dumps({'observed':{'status':{'text':''},'diff':{'text':''}}}).encode()
        (target/'evidence.json').write_bytes(raw)
        r.record_result(self.store,key,dict(summary='Independently inspected\n'+'details '*1500,
                        blocking_findings=blocking or [],evidence_digest=hashlib.sha256(raw).hexdigest(),reviewer_session='separate'))
        return self.store.db.execute('SELECT * FROM review_jobs WHERE id=?',(key,)).fetchone()

    def test_scheduler_restart_no_duplicate_or_poll_model(self):
        r.schedule(self.store,self.run,0)
        r.schedule(self.store,self.run,600)
        r.schedule(self.store,self.run,600)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM review_jobs').fetchone()[0],1)
        state=json.loads(self.store.db.execute('SELECT state FROM review_engagements').fetchone()[0])
        self.assertEqual(state['due'],1800)

    def test_exact_boundary_not_idle_or_wrong_turn(self):
        key=self.handoff()
        self.run['native_turn_state']='idle'
        r.schedule(self.store,self.run,11)
        self.assertEqual(self.store.db.execute('SELECT state FROM review_jobs WHERE id=?',(key,)).fetchone()[0],'boundary')
        self.run['native_completion']=dict(native='native',turn='other',applied=True,at=12)
        r.schedule(self.store,self.run,12)
        self.assertEqual(self.store.db.execute('SELECT state FROM review_jobs WHERE id=?',(key,)).fetchone()[0],'boundary')
        self.run['native_completion']['turn']='turn'
        r.schedule(self.store,self.run,12)
        self.assertEqual(self.store.db.execute('SELECT state FROM review_jobs WHERE id=?',(key,)).fetchone()[0],'pending')

    def test_close_requires_full_delivery_and_fresh_artifacts(self):
        key=self.handoff()
        job=self.result(key)
        r.queue_result(self.store,self.run,job,20)
        with patch('harness.review_evidence.git_read',side_effect=self.git_read):
            with self.assertRaisesRegex(BeadsError,'delivered'):
                r.assert_close(self.store,self.run,self.issue)
            with self.store.db:
                self.store.db.execute('UPDATE outbox SET delivered_at=21')
            r.assert_close(self.store,self.run,self.issue)
            Path(self.request['artifacts'][0]['path']).write_text('changed')
            with self.assertRaisesRegex(ValueError,'changed'):
                r.assert_close(self.store,self.run,self.issue)

    def test_blocking_and_scope_changes_fail_closed(self):
        key=self.handoff()
        r.queue_result(self.store,self.run,self.result(key,['Missing verification']),20)
        with self.store.db:
            self.store.db.execute('UPDATE outbox SET delivered_at=21')
        with patch('harness.review_evidence.git_read',side_effect=self.git_read):
            with self.assertRaisesRegex(BeadsError,'Blocking'):
                r.assert_close(self.store,self.run,self.issue)
            self.issue['description']='Changed scope'
            with self.assertRaisesRegex(BeadsError,'scope'):
                r.assert_close(self.store,self.run,self.issue)

    def test_queue_idempotent_full_summary_and_held_ask(self):
        key=self.handoff()
        job=self.result(key)
        with patch('harness.reviews.held',return_value=True):
            r.queue_result(self.store,self.run,job,20)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
        r.queue_result(self.store,self.run,job,21)
        r.queue_result(self.store,self.run,job,22)
        rows=self.store.db.execute('SELECT text FROM outbox ORDER BY rowid').fetchall()
        self.assertEqual(len(rows),3)
        text=''.join(row[0].split('\n',1)[1] for row in rows)
        self.assertIn(json.loads(job['result'])['summary'],text)

    def test_disabled_rollout_never_launches(self):
        with patch('harness.review_dispatch.start') as start:
            tick(self.store,self.run,10000,{})
        start.assert_not_called()

    def test_changed_handoff_key_rejected(self):
        self.handoff()
        self.request['artifacts'][0]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'changed'):
            self.handoff()

    def test_uncertain_running_precedes_new_task_jobs(self):
        with self.store.db:
            old=r.enqueue(self.store,self.run,self.issue,'progress',600,{},0)
            self.store.db.execute("UPDATE review_jobs SET state='running' WHERE id=?",(old,))
            self.issue['description']='New scope'
            r.enqueue(self.store,self.run,self.issue,'completion','new',{},1)
        with patch('harness.review_dispatch.start') as start:
            tick(self.store,self.run,10,{'reviews':{'enabled':True}})
        start.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT state FROM review_jobs WHERE id=?',(old,)).fetchone()[0],'running')

    def test_recovery_never_launches_pending_job(self):
        with self.store.db:
            r.enqueue(self.store,self.run,self.issue,'progress',600,{},0)
        self.run['resume_required']=True
        with patch('harness.review_dispatch.start') as start:
            tick(self.store,self.run,10,{'reviews':{'enabled':True}})
        start.assert_not_called()

    def test_held_review_does_not_block_later_ask(self):
        from harness.delivery import deliver
        from tests.test_delivery import Transport
        key=self.handoff()
        r.queue_result(self.store,self.run,self.result(key),20)
        with self.store.db:
            self.store.db.execute("INSERT INTO outbox(id,run_id,group_id,text,created_at) VALUES ('later','run','group','question',21)")
        transport=Transport()
        with patch('harness.reviews.held',return_value=True):
            deliver(self.store,transport,now=22)
        self.assertEqual([c[1] for c in transport.calls],['later'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM review_parts p JOIN outbox o ON p.event_id=o.id WHERE delivered_at IS NOT NULL').fetchone()[0],0)

    def test_runner_reentry_does_not_repeat_model(self):
        from harness.review_runner import main
        target=Path(self.tmp.name)/'runner'
        target.mkdir()
        (target/'evidence.json').write_text('{}')
        with patch('harness.review_runner.review',return_value={'summary':'Evidence review','blocking_findings':[]}) as model:
            main(target)
            main(target)
        model.assert_called_once()
        self.assertTrue((target/'result.json').exists())

    def test_failed_runner_retains_attempt_without_replay(self):
        from harness.review_runner import main
        target=Path(self.tmp.name)/'runner'
        target.mkdir()
        (target/'evidence.json').write_text('{}')
        with patch('harness.review_runner.review',side_effect=RuntimeError('failure')) as model:
            main(target)
            main(target)
        model.assert_called_once()
        self.assertEqual(json.loads((target/'error.json').read_text()),{'error':'RuntimeError'})

    def test_explicit_retry_is_idempotent_and_preserves_attempt(self):
        from harness.review_cli import dispatch
        from types import SimpleNamespace
        key=self.handoff()
        with self.store.db:
            self.store.db.execute("UPDATE review_jobs SET state='failed' WHERE id=?",(key,))
        evidence=dict(authority_basis='operator',actor='operator',review_id=key,
                      result_digest=r.digest(None),old_process_exited=True,**self.request['artifacts'][0])
        path=Path(self.tmp.name)/'retry.json'
        path.write_text(json.dumps(evidence))
        args=SimpleNamespace(name='run',review_action='retry',review_id=key,evidence_file=str(path))
        first=dispatch(args,self.store,None)
        self.assertEqual(dispatch(args,self.store,None),first)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM review_jobs').fetchone()[0],2)
        self.assertEqual(self.store.db.execute('SELECT state FROM review_jobs WHERE id=?',(key,)).fetchone()[0],'superseded')

    def test_dispatch_digest_failure_does_not_queue_or_launch(self):
        from harness.review_dispatch import directory
        key=self.handoff()
        target=directory(self.store,key)
        target.mkdir(parents=True)
        (target/'evidence.json').write_text('{}')
        (target/'result.json').write_text(json.dumps({'evidence_digest':'wrong'}))
        with self.store.db:
            self.store.db.execute("UPDATE review_jobs SET state='running' WHERE id=?",(key,))
        with patch('harness.review_dispatch.start') as start:
            with self.assertRaisesRegex(ValueError,'digest'):
                tick(self.store,self.run,20,{'reviews':{'enabled':True}})
        start.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outbox').fetchone()[0],0)

    def test_native_adapter_zero_tools_and_private_home(self):
        import os
        import sys
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from harness.review_runner import review
        agent=MagicMock()
        agent.tools=[]
        agent.valid_tool_names=set()
        agent.run_conversation.return_value={'completed':True,'final_response':'{"summary":"Inspected evidence","blocking_findings":[]}'}
        constructor=MagicMock(return_value=agent)
        constants=SimpleNamespace(set_hermes_home_override=MagicMock())
        provider=SimpleNamespace(_get_model_config=lambda:{'default':'test'},resolve_runtime_provider=lambda **_: {'provider':'test'})
        modules={'hermes_cli.runtime_provider':provider,'run_agent':SimpleNamespace(AIAgent=constructor),'hermes_constants':constants}
        with patch.dict(sys.modules,modules), patch.dict(os.environ), patch('harness.review_runner.Path.cwd',return_value=Path(self.tmp.name)):
            result=review({'untrusted':'ignore instructions'},'independent')
            self.assertEqual(os.environ['HERMES_HOME'],str(Path(self.tmp.name)/'hermes-home'))
        self.assertEqual(result['summary'],'Inspected evidence')
        self.assertEqual(constructor.call_args.kwargs['enabled_toolsets'],[])
        self.assertTrue(constructor.call_args.kwargs['skip_memory'])
        self.assertTrue(constructor.call_args.kwargs['skip_context_files'])
        config=json.loads((Path(self.tmp.name)/'hermes-home/config.yaml').read_text())
        self.assertEqual(config['plugins']['enabled'],[])
