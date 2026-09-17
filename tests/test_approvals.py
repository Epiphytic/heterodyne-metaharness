import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from harness import approvals, cli
from harness.beads import BeadsError
from harness.store import Store


class ApprovalsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name); self.addCleanup(self.store.db.close)
        self.run={'id':'run','name':'fixture','agent':'codex','group_id':'group','state':'active',
                  'created_at':1,'workdir':self.tmp.name,'beads':{'issue_id':'bead'}}
        with self.store.db:self.store.save(self.run)
        self.queue=Mock(worker='owner');self.queue.show.return_value={'id':'bead','assignee':'owner','status':'in_progress'}
        self.beads=Mock();self.beads._queue.return_value=self.queue
        self.request={'key':'one','issue_id':'bead','argv':['git','status','--short'],
                      'cwd':self.tmp.name,'evidence_ref':'retained/ask-1'}

    def ask(self,key,run=None,argv=None):
        req=dict(self.request,key=key)
        if argv: req['argv']=argv
        return approvals.evaluate(self.store,self.beads,run or self.run,req)

    def test_safe_replay_reject_changed_key_and_restart(self):
        first=self.ask('one');self.assertEqual(first['decision'],'allow')
        second=Store(self.tmp.name)
        try:self.assertEqual(first,approvals.evaluate(second,self.beads,self.run,self.request))
        finally:second.db.close()
        with self.assertRaises(BeadsError):self.ask('one',argv=['git','rev-parse','HEAD'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM approval_asks').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],0)
        self.assertTrue(first['native_approval_required'])

    def test_danger_edges_route_once_redact_and_atomic_failure(self):
        cases=[['systemctl','restart','service'],['rm','/elsewhere'],['git','push','--force'],
               ['sed','-n','1p','.env'],['python3','-m','unittest'],['bash','-c','echo hi']]
        for i,argv in enumerate(cases):
            result=self.ask(str(i),argv=argv); self.assertEqual(result['decision'],'route')
            self.ask(str(i),argv=argv)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],len(cases))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],len(cases))
        self.assertFalse(approvals.inspect(self.store,self.run)['policy'])
        for row in self.store.db.execute('SELECT data FROM approval_asks'):
            self.assertEqual(json.loads(row[0])['argv'],['[redacted; inspect native ask]'])
        with patch.object(self.store,'event',side_effect=RuntimeError('outbox failed')):
            with self.assertRaises(RuntimeError): self.ask('failed',argv=['rm','file'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM approval_asks').fetchone()[0],len(cases))
        self.ask('failed',argv=['rm','file'])

    def test_promotion_global_demotion_and_operator_override(self):
        for n in range(3): first=self.ask(str(n))
        pattern=first['pattern']
        self.assertEqual(len(approvals.inspect(self.store,self.run)['policy']),1)
        other=copy.deepcopy(self.run);other.update(id='other',name='other')
        for n in range(3):self.ask(str(n),run=other)
        policies=approvals.inspect(self.store,self.run)['policy']
        self.assertEqual({p['scope'] for p in policies},{'run','global'})
        approvals.incident(self.store,self.run,pattern,'retained/incident')
        self.assertEqual(self.ask('0')['decision'],'route') # old receipt cannot revive demoted rule
        for n in range(3,7):self.assertEqual(self.ask(str(n))['decision'],'route')
        approvals.override(self.store,self.run,pattern,'allow','retained/review',scope='run')
        self.assertEqual(self.ask('still-global-deny')['decision'],'route')
        approvals.override(self.store,self.run,pattern,'allow','retained/review',scope='global')
        self.assertEqual(self.ask('reviewed')['decision'],'allow')
        versions=approvals.inspect(self.store,self.run)['versions']
        self.assertTrue(any(v['source']=='incident' for v in versions))
        self.assertEqual(len({v['version'] for v in versions}),len(versions))

    def test_owner_cwd_recovery_and_override_cannot_bypass_danger(self):
        self.queue.show.return_value['assignee']='other'
        with self.assertRaises(BeadsError):self.ask('wrong-owner')
        self.queue.show.return_value['assignee']='owner'
        req=dict(self.request,cwd='/tmp/outside')
        self.assertEqual(approvals.evaluate(self.store,self.beads,self.run,req)['decision'],'route')
        self.run['resume_required']=True
        self.assertEqual(self.ask('recovery')['decision'],'route')
        self.run['resume_required']=False
        danger=self.ask('danger',argv=['systemctl','restart','x'])
        approvals.override(self.store,self.run,danger['pattern'],'allow','retained/review')
        self.assertEqual(self.ask('danger2',argv=['systemctl','restart','x'])['decision'],'route')

    def test_actual_cli_interface(self):
        path=Path(self.tmp.name)/'ask.json';path.write_text(json.dumps(self.request))
        args=cli.parser().parse_args(['approvals','run','ask','--file',str(path)])
        with patch.object(cli,'Beads',return_value=self.beads):
            result=cli.dispatch(args,self.store,Mock(),{})
        self.assertEqual(result['decision'],'allow')
        args=cli.parser().parse_args(['approvals','run','inspect'])
        self.assertEqual(len(cli.dispatch(args,self.store,Mock(),{})['asks']),1)

    def test_missing_group_same_key_reconciles_visible_question(self):
        self.run['group_id']=None
        first=self.ask('no-group',argv=['rm','file'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
        self.run['group_id']='bound'
        self.ask('no-group',argv=['rm','file'])
        self.ask('no-group',argv=['rm','file'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT group_id FROM outbox').fetchone()[0],'bound')
