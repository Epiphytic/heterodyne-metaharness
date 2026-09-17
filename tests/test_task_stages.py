import copy
import json
import unittest
from unittest.mock import Mock, patch
from harness import task_stages as stages
from harness.beads import BeadsError


class StageTest(unittest.TestCase):
    def setUp(self):
        self.run={'id':'run','workdir':'/owned','beads':{'issue_id':'bead'}}
        self.issue={'id':'bead','status':'in_progress','assignee':'worker','metadata':{'keep':'value'}}
        self.queue=Mock(worker='worker')
        self.queue.show.side_effect=lambda _:copy.deepcopy(self.issue)
        def update(*args):self.issue['metadata']=json.loads(args[-1])
        self.queue.bd.side_effect=update
        self.beads=Mock();self.beads._queue.return_value=self.queue
        self.sha='a'*40
        self.patch=patch.object(stages,'git',side_effect=self.git)
        self.patch.start();self.addCleanup(self.patch.stop)

    def git(self, repo, *args):
        if args[0]=='status':return ''
        if args[0]=='log':return 'G'
        if args[0]=='merge-base':return ''
        return self.sha

    def evidence(self):
        return {'commit':self.sha,'evidence_ref':'retained-evidence', 'result':'passed','full_suite':True,
                'command':['python3','-m','unittest','discover'], 'log_sha256':'b'*64,
                'pr_url':'https://private.example/pull/1','remote':'origin','pushed_commit':self.sha,
                'merge_authority':'operator','review_ref':'operator-review'}

    def test_order_repeat_metadata_and_close(self):
        with self.assertRaises(BeadsError):stages.close_ready(self.run,self.issue)
        for stage in stages.STAGES:
            evidence=self.evidence()
            stages.record(Mock(),self.beads,self.run,'bead',stage,evidence)
            before=self.queue.bd.call_count
            stages.record(Mock(),self.beads,self.run,'bead',stage,evidence)
            self.assertEqual(self.queue.bd.call_count,before)
        stages.close_ready(self.run,self.issue)
        self.assertEqual(self.issue['metadata']['keep'],'value')
        self.issue['metadata']['harness_lifecycle'][-2]['evidence']['result']='failed'
        with self.assertRaises(BeadsError):stages.close_ready(self.run,self.issue)

    def test_skipped_stage_and_dirty_boundary_fail(self):
        with self.assertRaises(BeadsError):stages.record(Mock(),self.beads,self.run,'bead','pr-open',self.evidence())
        with patch.object(stages,'git',return_value=' M file'):
            with self.assertRaisesRegex(BeadsError,'Uncommitted'):stages.clean_commit(self.run,self.sha)
        self.queue.bd.assert_not_called()

    def test_wrong_owner_recovery_and_closed_idempotency(self):
        self.issue['assignee']='someone-else'
        with self.assertRaises(BeadsError):stages.record(Mock(),self.beads,self.run,'bead','committed',self.evidence())
        self.issue['status']='closed'
        stages.close_ready(self.run,self.issue)
        self.assertFalse(self.queue.bd.called)
