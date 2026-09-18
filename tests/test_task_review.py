import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness.beads import Beads, BeadsError
from harness.task_review import can_handoff, checkout, claim_concurrent


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Mock(worker='worker', state=Path(self.temp.name))
        self.sha = 'a'*40
        self.run = {'id':'run','workdir':'/owned/one','beads':{'issue_id':'one'},
                    'task_worktrees':{'one':{'issue_id':'one','ready':True,'path':'/owned/one','branch':'bead/one'}}}
        self.issue = {'id':'one','status':'in_progress','assignee':'worker', 'metadata':{'harness_lifecycle':[]}}
        for name in ('committed','tested','pr-open'):
            evidence = {'commit':self.sha,'pushed_commit':self.sha,'result':'passed','full_suite':True,
                        'log_sha256':'b'*64,'pr_url':'rad://private/patch','remote':'rad'}
            digest = hashlib.sha256(json.dumps([name,evidence],sort_keys=True).encode()).hexdigest()
            self.issue['metadata']['harness_lifecycle'].append({'stage':name,'evidence':evidence,'digest':digest})

    def test_unreviewed_wrong_owner_and_changed_history_block(self):
        for field, value in [('status','open'),('assignee','other')]:
            issue = copy.deepcopy(self.issue); issue[field] = value
            with self.assertRaises(BeadsError): can_handoff(self.run,self.queue,issue)
        issue = copy.deepcopy(self.issue)
        issue['metadata']['harness_lifecycle'].pop()
        with self.assertRaises(BeadsError): can_handoff(self.run,self.queue,issue)
        self.issue['metadata']['harness_lifecycle'][0]['evidence']['commit'] = 'b'*40
        with self.assertRaises(BeadsError): can_handoff(self.run,self.queue,self.issue)

    def test_handoff_retains_claim_and_validates_checkout(self):
        with patch('harness.task_stages.clean_commit') as clean:
            self.assertIs(can_handoff(self.run,self.queue,self.issue),self.run)
        clean.assert_called_once_with(self.run,self.sha)
        self.queue.bd.assert_not_called()
        run = copy.deepcopy(self.run); run['beads']['issue_id']='two';run['workdir']='/owned/two'
        with patch('harness.task_review.git',side_effect=['/owned/one','bead/one']):
            old = checkout(run,'one')
        self.assertEqual(old['workdir'],'/owned/one')
        self.assertEqual(run['workdir'],'/owned/two')
        self.assertEqual(run['beads']['issue_id'],'two')

    def test_concurrent_claim_checks_all_active_and_native_atomic_claim(self):
        beads = Mock()
        self.queue.bd.return_value = [{'id':'one'}]
        new = {'id':'two','status':'in_progress','assignee':'worker'}
        self.queue.show.side_effect = [self.issue,new]
        self.queue.ready.return_value = [{'id':'two'}]
        beads._allowed.return_value = True
        with patch('harness.task_workspace.boundary') as boundary, patch('harness.task_stages.clean_commit'):
            result = claim_concurrent(beads,self.run,self.queue,'two')
        boundary.assert_called_once_with(self.run)
        self.assertEqual(result,new)
        self.queue.bd.assert_any_call('update','two','--claim')
        self.assertEqual((self.queue.state/'current').read_text(),'two')
        self.assertEqual(self.issue['status'],'in_progress')

    def test_paused_queue_blocks_new_claim(self):
        self.queue.bd.return_value = [{'id':'one'}]
        self.queue.show.return_value = self.issue
        self.queue.ready.return_value = []
        with patch('harness.task_workspace.boundary'), patch('harness.task_stages.clean_commit'):
            with self.assertRaises(BeadsError):claim_concurrent(Mock(),self.run,self.queue,'two')
        self.queue.bd.assert_called_once()

    def test_handoff_rejects_malformed_test_hash_and_review_destination(self):
        for index, field, value in [(1, 'log_sha256', 'not-a-hash'), (2, 'pr_url', 'pending')]:
            issue = copy.deepcopy(self.issue)
            event = issue['metadata']['harness_lifecycle'][index]
            event['evidence'][field] = value
            event['digest'] = hashlib.sha256(json.dumps([event['stage'], event['evidence']], sort_keys=True).encode()).hexdigest()
            with self.assertRaises(BeadsError):
                can_handoff(self.run, self.queue, issue)
        self.queue.bd.assert_not_called()
