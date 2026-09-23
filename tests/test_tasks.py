import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from harness import tasks, task_hooks, cli
from harness.beads import BeadsError
from harness.store import Store


class TasksTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.store=Store(self.home);self.addCleanup(self.store.db.close)
        self.run=dict(id='test-run',name='test',agent='codex',state='active',workdir=str(self.home),created_at=1,
                      beads={'workstream':'test','pickup_enabled':False})
        with self.store.db:self.store.save(self.run)
        self.issue={'id':'bead','title':'Original','description':'original scope','notes':'prior notes',
                    'status':'open','assignee':'','metadata':{'harness_request_hash':'original'}}
        self.queue=Mock();self.queue.matches.return_value=True
        self.queue.worker=tasks.worker(self.run)
        self.queue.show.side_effect=lambda _:copy.deepcopy(self.issue)
        def bd(*args):
            self.issue['notes']+='\n'+args[-1]
        self.queue.bd.side_effect=bd
        self.beads=Mock();self.beads._queue.return_value=self.queue
        self.beads.create.side_effect=lambda *a,**kw:copy.deepcopy(self.issue)
        self.supervisor=Mock()
        def persist(run):
            with self.store.db:self.store.save(run)
            self.store.checkpoint(run)
        self.supervisor.persist.side_effect=persist

    def claim(self):
        def claim(run,issue_id):
            run['beads']['issue_id']=issue_id
            self.issue.update(status='in_progress',assignee=self.queue.worker)
            return copy.deepcopy(self.issue)
        self.beads.claim.side_effect=claim
        args=cli.parser().parse_args(['task','test','claim','bead'])
        with patch('harness.cli.Beads',return_value=self.beads):
            cli.task_dispatch(args,self.store,self.supervisor,{})
        self.run=self.store.get('test')

    def test_actual_dispatch_claim_update_and_readonly_projection(self):
        self.claim()
        self.assertEqual(tasks.readonly(self.home,'test')['snapshot']['issue']['id'],'bead')
        text=self.home/'addendum';text.write_text('extra authorized detail')
        evidence=self.home/'evidence';evidence.write_text('existing user request reference')
        args=cli.parser().parse_args(['task','test','update','bead','--file',str(text),'--key','revision-one','--authorization-file',str(evidence)])
        with patch('harness.cli.Beads',return_value=self.beads):
            cli.task_dispatch(args,self.store,self.supervisor,{})
            cli.task_dispatch(args,self.store,self.supervisor,{})
        self.assertEqual(self.queue.bd.call_count,1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM inbox WHERE target='worker'").fetchone()[0],2)
        snapshot=tasks.readonly(self.home,'test','bead')['snapshot']
        self.assertIn('extra authorized detail',snapshot['issue']['notes'])
        self.assertEqual(snapshot['issue']['metadata']['harness_request_hash'],'original')
        checkpoint=json.loads((self.store.root/'runs/test-run/checkpoint.json').read_text())
        self.assertEqual(checkpoint['beads']['task_snapshot']['issue'],snapshot['issue'])
        with patch.dict(os.environ), patch('harness.cli.Store',side_effect=AssertionError('write initialization forbidden')):
            with patch('builtins.print'):
                self.assertEqual(cli.main(['--home',str(self.home),'task','test','show','bead']),0)
        self.assertFalse(self.store.get('test')['beads']['pickup_enabled'])

    def test_generic_admission_repeated_preserves_snapshot_and_claim(self):
        self.claim()
        original=self.store.get('test')
        for _ in range(2):
            tasks.admit(self.store,self.supervisor,self.beads,original,'title','text','key')
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM inbox WHERE target='manager'").fetchone()[0],1)
        self.assertEqual(self.store.get('test')['beads']['task_snapshot']['issue']['id'],'bead')
        self.assertEqual(self.store.get('test')['beads']['issue_id'],'bead')

    def test_changed_key_payload_rejected_and_uncertain_write_not_replayed(self):
        self.claim()
        def uncertain(*args):
            raise RuntimeError('write timeout')
        self.queue.bd.side_effect=uncertain
        with self.assertRaises(RuntimeError):
            tasks.addendum(self.store,self.beads,self.run,'bead','detail','key','prior user request')
        with self.assertRaises(BeadsError):
            tasks.addendum(self.store,self.beads,self.run,'bead','detail','key','prior user request')
        with self.assertRaises(BeadsError):
            tasks.addendum(self.store,self.beads,self.run,'bead','changed','key','prior user request')
        self.assertEqual(self.queue.bd.call_count,1)
        body=self.store.db.execute('SELECT body FROM task_updates').fetchone()[0]
        self.issue['notes']+='\n'+body
        tasks.addendum(self.store,self.beads,self.run,'bead','detail','key','prior user request')
        self.assertEqual(self.queue.bd.call_count,1)

    def test_notice_retry_ack_owner_change_and_unclaimed(self):
        tasks.observe(self.store,self.issue)
        self.assertIsNone(task_hooks.offer(self.store,'test-run:worker','native','turn'))
        self.claim()
        first=task_hooks.offer(self.store,'test-run:worker','native','turn')
        self.assertFalse(task_hooks.acknowledge(self.store,'test-run:worker','native','turn',[]))
        self.assertEqual(task_hooks.offer(self.store,'test-run:worker','compact','next'),first)
        messages=[{'role':'user','api_content':first['context']},{'role':'assistant','content':'response'}]
        self.assertTrue(task_hooks.acknowledge(self.store,'test-run:worker','compact','next',messages))
        self.assertIsNone(task_hooks.offer(self.store,'test-run:worker','native','new'))
        tasks.addendum(self.store,self.beads,self.run,'bead','detail','update','existing request')
        second=task_hooks.offer(self.store,'test-run:worker','native','new')
        self.assertNotEqual(first,second)
        self.assertFalse(task_hooks.acknowledge(self.store,'test-run:worker','native','new',messages))
        other=dict(self.run,id='other-run',name='other',beads={'issue_id':'bead'})
        with self.store.db:self.store.save(other)
        self.issue['assignee']=tasks.worker(other)
        tasks.observe(self.store,self.issue)
        self.assertIsNone(task_hooks.offer(self.store,'test-run:worker','native','new'))
        self.assertIsNotNone(task_hooks.offer(self.store,'other-run:worker','other-native','new'))

    def test_unbound_admission_show_preserves_active_claim(self):
        self.claim()
        self.issue=dict(id='queued',title='next',description='next task',status='open',assignee='',
                        labels=['agent:codex','ws:test','session:test-run','kind:task'])
        tasks.admit(self.store,self.supervisor,self.beads,self.run,'next','text','next-key')
        self.assertEqual(tasks.readonly(self.home,'test','queued')['snapshot']['issue']['id'],'queued')
        self.assertEqual(self.store.get('test')['beads']['issue_id'],'bead')
        self.issue['labels']=['agent:claude','ws:other']
        tasks.observe(self.store,self.issue)
        with self.assertRaises(BeadsError):tasks.readonly(self.home,'test','queued')

    def test_task_inbox_waits_for_native_idle_and_preserves_approval(self):
        from harness.supervisor import Supervisor
        self.claim()
        supervisor=object.__new__(Supervisor)
        supervisor.store=self.store;supervisor.submit=Mock();supervisor.beads=self.beads
        run=self.store.get('test')
        for state in ('working','awaiting_approval','unknown'):
            run['native_turn_state']=state
            supervisor.drain_inbox(run)
        supervisor.submit.assert_not_called()
        run['native_turn_state']='idle';run['resume_required']=True
        supervisor.drain_inbox(run);supervisor.submit.assert_not_called()
        run['resume_required']=False
        supervisor.drain_inbox(run);supervisor.submit.assert_called_once()
        self.issue['assignee']='other:owner'
        tasks.observe(self.store,self.issue)
        supervisor.submit.reset_mock()
        supervisor.drain_inbox(run);supervisor.submit.assert_not_called()

    def test_direct_steering_precedes_held_update(self):
        from harness.supervisor import Supervisor
        self.claim()
        supervisor=object.__new__(Supervisor)
        supervisor.store=self.store;supervisor.submit=Mock();supervisor.beads=self.beads
        run=self.store.get('test');run['native_turn_state']='working'
        with self.store.db:
            self.store.db.execute("INSERT INTO inbox(id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)",
                                  ('direct','test-run','stop now',9999999999,'pending','worker'))
        supervisor.drain_inbox(run)
        supervisor.submit.assert_called_once_with(run,'stop now',target='worker',message_id='direct')

    def test_external_scope_close_guard_and_closed_retry(self):
        self.claim()
        tasks.assert_close_current(self.store,self.run,self.issue)
        self.issue['notes']+=' external scope'
        for _ in range(2):
            with self.assertRaises(BeadsError):tasks.assert_close_current(self.store,self.run,self.issue)
        notice=task_hooks.offer(self.store,'test-run:worker','native','turn')
        messages=[{'role':'user','content':notice['context']},{'role':'assistant','content':'received'}]
        self.assertTrue(task_hooks.acknowledge(self.store,'test-run:worker','native','turn',messages))
        tasks.assert_close_current(self.store,self.run,self.issue)
        self.issue.update(status='closed',comment_count=7,notes='closure evidence')
        tasks.assert_close_current(self.store,self.run,self.issue)
        # An acknowledged natural hook must retire the queued duplicate too.
        row=self.store.db.execute("SELECT * FROM inbox ORDER BY created_at DESC LIMIT 1").fetchone()
        self.assertFalse(tasks.current_notification(self.store,self.run,row))

    def test_missing_baseline_cannot_be_seeded_by_failed_close(self):
        self.claim()
        with self.store.db:self.store.db.execute('DELETE FROM task_baselines')
        for _ in range(2):
            with self.assertRaises(BeadsError):tasks.assert_close_current(self.store,self.run,self.issue)

    def test_dispatch_create_retry_preserves_fresh_snapshot(self):
        self.claim()
        self.issue['notes']='new projected revision'
        text=self.home/'task';text.write_text('scope')
        args=cli.parser().parse_args(['task','test','create','--title','Original','--file',str(text),'--key','key'])
        with patch('harness.cli.Beads',return_value=self.beads):
            for _ in range(2):cli.task_dispatch(args,self.store,self.supervisor,{})
        self.assertEqual(self.store.get('test')['beads']['task_snapshot']['issue']['notes'],'new projected revision')

    def test_external_acceptance_design_spec_and_dependency_changes_gate_close(self):
        self.claim()
        original=copy.deepcopy(self.issue)
        for field,value in (('acceptance_criteria','new required check'), ('design','new design'),
                            ('spec_id','new-spec'), ('dependencies',[{'id':'replacement','dependency_type':'blocks'}])):
            self.issue=copy.deepcopy(original)
            self.issue[field]=value
            with self.assertRaises(BeadsError):tasks.assert_close_current(self.store,self.run,self.issue)

    def test_native_provider_hook_task_delivery_receipt_and_idle(self):
        from harness.brain_hooks import handle
        self.claim()
        spec=self.home/'spec';spec.mkdir();(spec/'README.md').write_text('current')
        for provider,directory,env_name in [('codex','sessions','CODEX_HOME'),('claude','projects','CLAUDE_CONFIG_DIR')]:
            run=self.store.get('test');run.update(agent=provider,native_session_id='native')
            self.issue['assignee']=tasks.worker(run)
            with self.store.db:self.store.save(run)
            tasks.observe(self.store,self.issue)
            native_home=self.home/provider;path=native_home/directory/'test.jsonl'
            path.parent.mkdir(parents=True);path.write_text('')
            env={'HERMES_WORKSTREAM_RUN':'test-run','HERMES_WORKSTREAM_ROLE':'worker',env_name:str(native_home)}
            with patch.dict(os.environ,env):
                payload={'session_id':'native','turn_id':provider,'cwd':str(self.home),
                         'hook_event_name':'UserPromptSubmit','transcript_path':str(path)}
                notice=handle(self.store,provider,payload,spec)
                self.assertIn('[task-notice:',notice['context'])
                self.assertEqual(self.store.get('test')['native_turn_state'],'working')
                stop=dict(payload,hook_event_name='Stop')
                self.assertFalse(handle(self.store,provider,stop,spec)['task_acknowledged'])
                messages=[{'role':'user','content':notice['context']},{'role':'assistant','content':'received'}]
                records=([{'type':'response_item','payload':m} for m in messages] if provider=='codex'
                         else [{'type':m['role'],'message':m} for m in messages])
                path.write_text(''.join(json.dumps(r)+'\n' for r in records))
                self.assertTrue(handle(self.store,provider,stop,spec)['task_acknowledged'])
                self.assertEqual(self.store.get('test')['native_turn_state'],'idle')
