import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness import operator_asks as asks
from harness.admin_references import OPERATORS, lookup, npub
from harness.delivery import deliver
from harness.marmot import MarmotError
from harness.store import Store


class Transport:
    timeout = 20
    account_id = 'ab' * 32

    def __init__(self):
        self.admins = ['npub-one']
        self.calls = []
        self.lookups = 0
        self.fail = False

    def admin_references(self, group):
        self.lookups += 1
        return self.admins

    def send(self, group, text, identity):
        self.calls.append((group, text, identity))
        if self.fail:
            raise RuntimeError('uncertain send')
        return {'message_ids_hex': ['aa']}


class AskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(lambda: self.store.db.close())
        self.run = dict(id='run', name='test', group_id='aa')
        self.transport = Transport()

    def ask(self, key='ask', text='Please inspect the command'):
        return asks.enqueue(self.store, self.run, text, key, 'Inspect command')['event_id']

    def status(self, identity='status', group='aa'):
        with self.store.db:
            self.store.db.execute('INSERT INTO outbox(id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)',
                                  (identity, 'run', group, 'Progress '+identity, 9999999999))

    def test_changed_key_body_rejected_and_duplicate_noop(self):
        self.assertEqual(self.ask(), self.ask())
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.ask(text='New scope')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)

    def test_retry_restart_freezes_payload_and_account(self):
        self.ask()
        self.transport.fail = True
        deliver(self.store, self.transport, now=1)
        self.transport.admins = ['npub-new-admin']
        self.store.db.close()
        self.store = Store(self.temp.name)
        self.transport.fail = False
        deliver(self.store, self.transport, now=31)
        self.assertEqual(self.transport.calls[0], self.transport.calls[1])
        self.assertEqual(self.transport.lookups, 1)
        self.assertIn('@npub-one', self.transport.calls[0][1])

    def test_status_has_no_ask_footer_or_admin_lookup(self):
        self.ask()
        deliver(self.store, self.transport, now=1)
        self.transport.admins = ['npub-new-admin']
        self.status()
        deliver(self.store, self.transport, now=2)
        self.assertEqual(self.transport.calls[-1][1], 'Progress status')
        self.assertEqual(self.transport.lookups, 1)

    def test_resolution_does_not_grant_native_approval(self):
        identity = self.ask()
        deliver(self.store, self.transport, now=1)
        asks.resolve(self.store, self.run, identity, 'Operator answered; native inspection retained')
        self.status()
        deliver(self.store, self.transport, now=2)
        self.assertEqual(self.transport.calls[-1][1], 'Progress status')
        self.assertEqual(self.transport.lookups, 1)
        self.assertTrue(asks.resolve(self.store, self.run, identity, 'Operator answered; native inspection retained')['resolved'])
        with self.assertRaises(ValueError):
            asks.resolve(self.store, self.run, identity, 'Rewritten evidence')

    def test_explicit_blocked_question_vs_status(self):
        with self.store.db:
            self.store.event(self.run, 'blocked', 'Waiting for build', 'pure')
            self.store.event(self.run, 'blocked', 'Choose recovery action', 'question', operator_ask=True)
        self.assertEqual([row[0] for row in self.store.db.execute('SELECT id FROM operator_asks')], ['question'])

    def test_lookup_failure_retains_pending_ask(self):
        self.ask()
        with patch.object(self.transport, 'admin_references', side_effect=MarmotError('unavailable')):
            deliver(self.store, self.transport, now=1)
        self.assertEqual(self.transport.calls, [])
        self.assertIsNone(self.store.db.execute('SELECT delivered_at FROM operator_asks').fetchone()[0])
        self.assertIsNone(self.store.db.execute('SELECT delivered_at FROM outbox').fetchone()[0])

    def test_multiple_asks_remain_separate(self):
        for i in range(10):
            self.ask(key=str(i), text='Question '+str(i))
            deliver(self.store, self.transport, now=i)
        for i, call in enumerate(self.transport.calls):
            self.assertEqual(call[1], f'Question {i}\n\n@npub-one')
        self.status()
        deliver(self.store, self.transport, now=11)
        self.assertEqual(self.transport.calls[-1][1], 'Progress status')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM operator_asks WHERE resolved_at IS NULL').fetchone()[0], 10)

    def test_full_multiline_body_and_summary_persist(self):
        body = 'Long question: ' + 'indivisible漢字 ' * 500 + '\n\nFinal approval condition.'
        identity = asks.enqueue(self.store, self.run, body, 'long', body)['event_id']
        self.assertEqual(self.store.db.execute('SELECT summary FROM operator_asks WHERE id=?', (identity,)).fetchone()[0], body)
        deliver(self.store, self.transport, now=1)
        self.assertEqual(self.transport.calls[-1][1], body + '\n\n@npub-one')

    def test_references_trailing_and_stamps_removed(self):
        body = '[brain-notice:abc]\nApprove btq-dd4 after ask:12345678?\n[worker brain context received]\nKeep this final condition.'
        self.ask(text=body)
        deliver(self.store, self.transport, now=1)
        text = self.transport.calls[-1][1]
        content, footer = text.rsplit('\n\nReferences: ', 1)
        self.assertNotIn('btq-dd4', content)
        self.assertNotIn('ask:12345678', content)
        self.assertNotIn('brain', text)
        self.assertIn('Keep this final condition.', content)
        self.assertEqual(footer, '1: btq-dd4; 2: ask:12345678')
        self.assertEqual(asks.operator_text(text), text)
        self.assertEqual(self.store.db.execute('SELECT text FROM outbox').fetchone()[0], body)

    def test_reminders_separate_hourly_and_use_full_body(self):
        self.ask(text='Original full question ' + 'word ' * 200)
        deliver(self.store, self.transport, now=1)
        self.status()
        deliver(self.store, self.transport, now=3601)
        self.assertEqual(self.transport.calls[-1][1], 'Progress status')
        deliver(self.store, self.transport, now=3602)
        reminder = self.transport.calls[-1]
        self.assertTrue(reminder[2].startswith('ask-reminder:'))
        self.assertIn('word ' * 199, reminder[1])
        self.assertNotIn('Progress status', reminder[1])
        deliver(self.store, self.transport, now=3603)
        self.assertEqual(len(self.transport.calls), 3)

    def test_resolved_unattempted_reminder_cancelled(self):
        identity = self.ask()
        deliver(self.store, self.transport, now=1)
        asks.enqueue_reminder(self.store, 3602)
        asks.resolve(self.store, self.run, identity, 'Answered')
        deliver(self.store, self.transport, now=3603)
        self.assertEqual(len(self.transport.calls), 1)

    def test_failed_reminder_does_not_block_new_ask(self):
        self.ask()
        deliver(self.store, self.transport, now=1)
        self.transport.fail = True
        deliver(self.store, self.transport, now=3602)
        self.ask(key='urgent', text='Urgent new question')
        self.transport.fail = False
        deliver(self.store, self.transport, now=3603)
        self.assertEqual(self.transport.calls[-1][1], 'Urgent new question\n\n@npub-one')

    def test_reminder_contains_one_logical_ask_and_all_parts(self):
        with self.store.db:
            for index, body in enumerate(['First part ' + 'a' * 700, 'Second part with final condition']):
                identity = f'permission:example:{index}'
                self.store.db.execute('INSERT INTO outbox(id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)',
                    (identity, 'run', 'aa', body, index))
                row = self.store.db.execute('SELECT * FROM outbox WHERE id=?', (identity,)).fetchone()
                asks.register(self.store, row, 'Short label', 'multipart')
        # Use direct preparation/delivery bookkeeping: this is not a live native permission relay.
        with self.store.db:
            self.store.db.execute('UPDATE outbox SET delivered_at=1')
            self.store.db.execute('UPDATE operator_asks SET delivered_at=1')
        self.ask(key='other', text='Different question')
        deliver(self.store, self.transport, now=2)
        deliver(self.store, self.transport, now=3601)
        text = self.transport.calls[-1][1]
        self.assertIn('a' * 700, text)
        self.assertIn('Second part with final condition', text)
        self.assertNotIn('Different question', text)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM outbox WHERE id LIKE 'ask-reminder:%'").fetchone()[0], 1)

    def test_existing_reference_footer_stays_after_mentions(self):
        self.ask(text='Approve this?\n\nReferences: btq-dd4')
        deliver(self.store, self.transport, now=1)
        self.assertEqual(self.transport.calls[-1][1],
            'Approve this?\n\n@npub-one\n\nReferences: btq-dd4')

    def test_bare_approval_identity_is_trailing(self):
        identity = 'a' * 64
        text = asks.operator_text(f'Operator question {identity}: approve?')
        self.assertEqual(text, 'Operator question [reference 1]: approve?\n\nReferences: 1: ' + identity)

    def test_legacy_frozen_stacked_payload_requires_reconciliation(self):
        identity = self.ask()
        with self.store.db:
            self.store.db.execute('INSERT INTO outbox_rendered VALUES (?,?,?)',
                (identity, self.transport.account_id, 'Old ask\nStill awaiting your answer: other ask'))
        deliver(self.store, self.transport, now=1)
        self.assertEqual(self.transport.calls, [])
        self.assertIn('reconciliation', self.store.db.execute('SELECT error FROM outbox').fetchone()[0])

    def test_tagged_payload_rejects_changed_sending_account(self):
        self.ask()
        self.transport.fail = True
        deliver(self.store, self.transport, now=1)
        self.transport.account_id = 'cc'*32
        deliver(self.store, self.transport, now=31)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertIn('account changed', self.store.db.execute('SELECT error FROM outbox').fetchone()[0])

    def test_babysitter_routes_durably_and_rejects_stale_group(self):
        self.run.update(agent='codex', workdir=self.temp.name, state='active', created_at=1)
        with self.store.db:
            self.store.save(self.run)
        pinfo = {'run_id': 'run', 'group': 'aa'}
        with patch.dict(os.environ, {'HERMES_HOME': self.temp.name}):
            body = 'Please review ' + 'unbrokenword ' * 100
            first = asks.babysitter_notice(pinfo, body, operator_ask=True)
            self.assertEqual(first, asks.babysitter_notice(pinfo, body, operator_ask=True))
            self.assertEqual(self.store.db.execute('SELECT summary FROM operator_asks').fetchone()[0], body)
            asks.babysitter_notice(pinfo, 'Status', operator_ask=False)
            with self.assertRaisesRegex(ValueError, 'binding changed'):
                asks.babysitter_notice(dict(pinfo, group='bb'), 'Please review', operator_ask=True)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM operator_asks').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 2)

    def test_uncertain_plain_status_retry_cannot_gain_changed_footer(self):
        self.status()
        self.transport.fail = True
        deliver(self.store, self.transport, now=1)
        # A new ask can be recorded while an earlier status is in retry.
        self.ask()
        self.transport.fail = False
        deliver(self.store, self.transport, now=31)
        status_calls = [call for call in self.transport.calls if call[2] == 'status']
        if len(status_calls) == 1:
            deliver(self.store, self.transport, now=32)
            status_calls = [call for call in self.transport.calls if call[2] == 'status']
        self.assertEqual(status_calls[0], status_calls[1])


class AdminLookupTests(unittest.TestCase):
    account = '205b78b7770de52aab06874fea2e516123118165d4f672d7bf222971f318bfa1'
    config = {'binary': '/reviewed/wn', 'home': '/reviewed/home'}

    def payload(self):
        return {'account_id': self.account, 'group_id': 'aa',
                'admins': [{'admin_id': self.account, 'npub': npub(self.account)}]}

    def test_known_reference_and_union(self):
        self.assertEqual(npub(self.account), 'npub1ypdh3dmhphjj42cxsa875tj3vy33rqt96nm894alyg5hrucch7ssjmla23')
        with patch('harness.admin_references.subprocess.run') as command:
            command.return_value.stdout = json.dumps(self.payload())
            refs = lookup(self.config, self.account, 'aa', 3)
            self.assertEqual(set(refs), {npub(self.account)} | {npub(key) for key in OPERATORS})
            self.assertEqual(command.call_args.kwargs['timeout'], 3)
            self.assertEqual(command.call_args.args[0][-4:], ['groups', 'admins', 'aa', '--json'])

    def test_wrapped_result_envelope_accepted(self):
        """wn 0.10.x wraps payloads as {"ok":true,"result":{...}} (regression:
        the envelope shape previously failed every lookup and wedged the
        delivery outbox FIFO for hours)."""
        wrapped = {'ok': True, 'result': self.payload()}
        with patch('harness.admin_references.subprocess.run') as command:
            command.return_value.stdout = json.dumps(wrapped)
            refs = lookup(self.config, self.account, 'aa', 3)
            self.assertEqual(set(refs), {npub(self.account)} | {npub(key) for key in OPERATORS})

    def test_ok_false_envelope_fails_closed(self):
        with patch('harness.admin_references.subprocess.run') as command:
            command.return_value.stdout = json.dumps({'ok': False, 'error': {'code': 'x'}})
            with self.assertRaises(MarmotError):
                lookup(self.config, self.account, 'aa', 3)

    def test_wrong_group_bad_key_and_unavailable_fail_closed(self):
        for field, value in [('group_id', 'bb'), ('admins', [{'admin_id': self.account, 'npub': 'npub1bad'}])]:
            payload = self.payload()
            payload[field] = value
            with patch('harness.admin_references.subprocess.run') as command:
                command.return_value.stdout = json.dumps(payload)
                with self.assertRaises(MarmotError):
                    lookup(self.config, self.account, 'aa', 3)
        with patch('harness.admin_references.subprocess.run', side_effect=subprocess.TimeoutExpired('wn', 3)):
            with self.assertRaises(MarmotError):
                lookup(self.config, self.account, 'aa', 3)
        with self.assertRaises(MarmotError):
            lookup({}, self.account, 'aa', 3)
