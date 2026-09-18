from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from harness.beads import Beads, BeadsError


class BeadsTest(unittest.TestCase):
    def setUp(self):
        self.run = {'id': str(uuid.uuid4()), 'agent': 'codex', 'native_session_id': 'initial',
                    'beads': {'workstream': 'demo'}}
        self.beads = Beads({'enabled': True})
        self.queue = Mock(agent='codex', worker='codex:host:' + self.run['id'])
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.queue.state = Path(directory.name)
        self.queue.bd.return_value = []
        self.queue.matches.return_value = True
        self.queue.design_allowed.return_value = True
        self.beads._queue = Mock(return_value=self.queue)
        self.issue = {'id': 'btq-test', 'status': 'open', 'assignee': '', 'labels': ['kind:task'], 'metadata': {}}
        self.queue.show.return_value = self.issue

    def test_stable_identity_survives_native_compaction(self):
        before = self.beads.command(self.run)
        self.run['native_session_id'] = 'compacted'
        self.assertEqual(before, self.beads.command(self.run))
        self.assertEqual(self.run['id'], self.beads.environment(self.run)['BTQ_SESSION_ID'])

    def test_disabled_and_unenrolled(self):
        self.assertEqual('', Beads({}).context(self.run))
        with self.assertRaises(BeadsError):
            Beads({}).command(self.run)
        del self.run['beads']
        with self.assertRaises(BeadsError):
            self.beads.command(self.run)

    def test_recovery_never_claims_or_resumes(self):
        self.run['beads']['recovery_required'] = True
        self.assertEqual([], self.beads.ready(self.run))
        for method, args in ((self.beads.claim, ('btq-test',)), (self.beads.resume, ())):
            with self.assertRaises(BeadsError):
                method(self.run, *args)
        self.beads._queue.assert_not_called()

    def test_existing_owned_claim_is_idempotent(self):
        self.issue.update(status='in_progress', assignee=self.queue.worker)
        with patch.object(self.beads, '_call') as call:
            self.assertEqual(self.issue, self.beads.claim(self.run, 'btq-test'))
        call.assert_not_called()
        self.assertEqual('btq-test', self.run['beads']['issue_id'])

    def test_uncertain_claim_does_not_bind(self):
        with patch.object(self.beads, '_call', side_effect=BeadsError('uncertain')):
            with self.assertRaises(BeadsError):
                self.beads.claim(self.run, 'btq-test')
        self.assertNotIn('issue_id', self.run['beads'])

    def test_other_workers_task_cannot_bind(self):
        self.issue['assignee'] = 'someone-else'
        with self.assertRaises(BeadsError):
            self.beads.bind(self.run, 'btq-test')

    def test_claude_requires_native_approval_dependency(self):
        self.run['agent'] = self.queue.agent = 'claude'
        self.issue['metadata'] = {'design_approval': 'btq-approval'}
        self.queue.ready.return_value = [self.issue]
        self.queue.bd.return_value = []
        self.assertEqual([], self.beads.ready(self.run))
        with self.assertRaises(BeadsError):
            self.beads.claim(self.run, 'btq-test')
        self.queue.bd.return_value = [{'issue_id': 'btq-test', 'depends_on_id': 'btq-approval', 'type': 'blocks'}]
        self.assertEqual([self.issue], self.beads.ready(self.run))
        self.queue.design_allowed.return_value = False
        self.assertEqual([], self.beads.ready(self.run))

    def test_create_retries_same_id_and_rejects_changed_content(self):
        database = {}
        def bd(*args):
            if args[0] == 'list':
                issue = database.get(args[args.index('--id') + 1])
                return [issue] if issue else []
            if args[0] == 'create':
                import json
                issue_id = args[args.index('--id') + 1]
                database[issue_id] = {'id': issue_id, 'metadata': json.loads(args[args.index('--metadata') + 1])}
                return database[issue_id]
            self.fail(args)
        self.queue.bd.side_effect = bd
        self.queue.show.side_effect = lambda issue_id: database[issue_id]
        first = self.beads.create(self.run, 'Task', 'Acceptance criteria', key='one')
        second = self.beads.create(self.run, 'Task', 'Acceptance criteria', key='one')
        self.assertEqual(first, second)
        self.assertEqual(1, len(database))
        with self.assertRaises(BeadsError):
            self.beads.create(self.run, 'Changed', 'Acceptance criteria', key='one')
        self.queue.claim.assert_not_called()

    def test_cannot_manufacture_approval_metadata(self):
        with self.assertRaises(BeadsError):
            self.beads.create(self.run, 'Task', 'Description', key='one', metadata={'approved_by': 'Liam'})
        self.queue.bd.assert_not_called()

    def test_context_bounds_task_data_and_never_claims(self):
        self.run['beads']['issue_id'] = 'btq-test'
        with patch.object(self.beads, 'show', return_value={'description': 'x' * 50000}):
            context = self.beads.context(self.run)
        self.assertLess(len(context), 4000)
        self.queue.claim.assert_not_called()

    def test_close_reconciles_lost_ack_and_checks_ready_once(self):
        self.issue.update(status='closed', assignee=self.queue.worker)
        self.queue.ready.return_value = []
        with patch.object(self.beads, '_call') as call:
            self.beads.close(self.run, 'btq-test', '/unused')
        call.assert_not_called()
        self.queue.ready.assert_called_once()

    def test_close_consumes_own_stop_trigger_but_preserves_other_marker(self):
        self.issue.update(status='closed', assignee=self.queue.worker)
        self.queue.ready.return_value = []
        current = self.queue.state / 'current'
        current.write_text('btq-test')
        self.beads.close(self.run, 'btq-test', '/unused')
        self.assertFalse(current.exists())
        current.write_text('btq-other')
        self.beads.close(self.run, 'btq-test', '/unused')
        self.assertEqual('btq-other', current.read_text())

    def test_real_executable_boundary_uses_argv_not_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'queue with spaces'
            executable.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            executable.chmod(0o700)
            beads = Beads({'enabled': True, 'executable': str(executable)})
            result = beads.show(self.run, '$(touch /tmp/not-executed)')
            self.assertEqual('$(touch /tmp/not-executed)', result[-1])
            self.assertEqual(self.run['id'], result[5])


class InstalledBTQIntegrationTest(unittest.TestCase):
    """Exercise real installed BTQ policy/CLI against an isolated fake bd transport."""
    @unittest.skipUnless(Path('/home/operator/repos/beads-task-queue/bin/btq').exists(), 'installed BTQ not available')
    def test_both_agents_create_bind_claim_close_without_live_writes(self):
        import json
        import os
        import subprocess
        import sys
        fake_bd = '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
path=Path(os.environ['FAKE_BD_STORE'])
data=json.loads(path.read_text())
actor=args[args.index('--actor')+1]
args=args[args.index('--actor')+2:]
def opt(name, default=None):
 return args[args.index(name)+1] if name in args else default
command=args[0]
result=None
if command=='create':
 id=opt('--id')
 if id in data: raise SystemExit(3)
 result={'id':id,'title':opt('--title'),'description':opt('--description'),'labels':opt('--labels').split(','),'metadata':json.loads(opt('--metadata')),'status':'open','assignee':''}
 if opt('--deps'): result['dep']=opt('--deps').split(':',1)[1]
 data[id]=result
elif command=='show': result=[data[args[1]]]
elif command=='list':
 result=[v for v in data.values() if (not opt('--id') or v['id']==opt('--id')) and (not opt('--assignee') or v.get('assignee')==opt('--assignee')) and (not opt('--status') or v.get('status')==opt('--status'))]
elif command=='ready': result=[v for v in data.values() if v.get('status')=='open' and not v.get('assignee')]
elif command=='update':
 issue=data[args[1]]
 if issue.get('assignee'): raise SystemExit(4)
 issue.update(assignee=actor,status='in_progress')
 result=issue
elif command=='dep':
 dep=data[args[2]].get('dep')
 result=[{'id':dep,'dependency_type':'blocks'}] if dep else []
elif command=='comments':
 issue=data[args[2]]
 issue['comments']=issue.get('comments',0)+1
 result={}
elif command=='close':
 data[args[1]]['status']='closed'
 result=[data[args[1]]]
else: raise SystemExit(5)
path.write_text(json.dumps(data))
print(json.dumps(result))
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / '.config/beads-task-queue'
            config.mkdir(parents=True)
            (config / 'credentials.json').write_text(json.dumps({'codex': 'fixture', 'claude': 'fixture'}))
            executable = root / 'bd'
            executable.write_text(fake_bd)
            executable.chmod(0o700)
            database = root / 'fixture.json'
            database.write_text(json.dumps({'btq-approval': {
                'id': 'btq-approval', 'status': 'closed', 'metadata': {'approved_by': 'Liam',
                'approved_at': 'test-fixture', 'adr_revision': 'fixture-r1',
                'brainstorm_models': ['GLM 5.3 Fast', 'GPT-6 Astra']}}}))
            evidence = root / 'evidence'
            evidence.write_text('Disposable fixture acceptance complete')
            driver = '''
import os, uuid
from harness.beads import Beads
beads=Beads({'enabled':True,'workstream':'isolated-test'})
for agent in ('codex','claude'):
 run={'id':str(uuid.uuid4()),'agent':agent,'beads':{}}
 issue=beads.create(run,'Fixture','Acceptance fixture',key='one',metadata={'adr_revision':'fixture-r1'},approval_id='btq-approval')
 assert beads.create(run,'Fixture','Acceptance fixture',key='one',metadata={'adr_revision':'fixture-r1'},approval_id='btq-approval')['id']==issue['id']
 beads.bind(run,issue['id'])
 beads.resume(run)
 assert issue['id'] in [x['id'] for x in beads.ready(run)]
 claimed=beads.claim(run,issue['id'])
 assert claimed['status']=='in_progress'
 assert beads.claim(run,issue['id'])['assignee']==claimed['assignee']
 assert beads.close(run,issue['id'],os.environ['FAKE_EVIDENCE'])['issue']['status']=='closed'
 assert beads.close(run,issue['id'],os.environ['FAKE_EVIDENCE'])['issue']['comments']==1
print('codex and claude lifecycle passed')
'''
            result = subprocess.run([sys.executable, '-c', driver], cwd=Path(__file__).resolve().parents[1],
                                    env=dict(os.environ, HOME=str(root), PATH=str(root)+os.pathsep+os.environ['PATH'],
                                             FAKE_BD_STORE=str(database), FAKE_EVIDENCE=str(evidence)),
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('codex and claude lifecycle passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
