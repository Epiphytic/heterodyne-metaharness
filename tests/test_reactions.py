from pathlib import Path
import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from harness import cli
from harness.delivery import deliver
from harness.marmot import MarmotError
from harness.reactions import enqueue
from harness.store import Store


class ReactionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name); self.addCleanup(self.store.db.close)
        self.run = dict(id='run', name='test', group_id='bb', agent='codex', state='active',
                        workdir=self.tmp.name, created_at=1)
        with self.store.db:self.store.save(self.run)
        self.transport = Mock(account_id='aa', timeout=20)

    def test_main_constructs_transport_but_only_enqueues(self):
        with patch.dict(os.environ), patch.object(cli, 'load_config', return_value={'marmot': {'account_id': 'aa'}}), \
                patch.object(cli, 'Marmot', return_value=self.transport) as factory, \
                contextlib.redirect_stdout(io.StringIO()):
            cli.main(['--home', self.tmp.name, 'react', 'test', '--message-id', 'dd',
                      '--emoji', '👀', '--key', 'main'])
        factory.assert_called_once_with({'account_id': 'aa'})
        self.transport.react.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox_reactions').fetchone()[0], 1)

    def test_actual_cli_repeat_key_survives_store_restart_and_rejects_changes(self):
        supervisor = Mock(transport=self.transport)
        args = cli.parser().parse_args(['react','test','--message-id','dd','--emoji','👀','--key','one'])
        first = cli.dispatch(args,self.store,supervisor,{})
        other = Store(self.tmp.name)
        try:
            self.assertEqual(first,cli.dispatch(args,other,supervisor,{}))
            self.assertEqual(other.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
            for target, emoji, account in [('ee','👀','aa'),('dd','✅','aa'),('dd','👀','ff')]:
                with self.assertRaises(MarmotError):enqueue(other,self.run,account,target,emoji,'one')
        finally:other.db.close()
        now = 9999999999
        deliver(self.store,self.transport,now=now)
        self.transport.react.assert_called_once_with('bb','dd','👀',first['event_id'])
        cli.dispatch(args,self.store,supervisor,{})
        deliver(self.store,self.transport,now=now+1)
        self.transport.react.assert_called_once()
        self.transport.send.assert_not_called()

    def test_failed_delivery_retries_same_intent_and_keeps_group_fifo(self):
        event=enqueue(self.store,self.run,'aa','dd','👀','one')['event_id']
        now=9999999999
        self.transport.react.side_effect=RuntimeError('lost acknowledgement')
        deliver(self.store,self.transport,now=now)
        row=self.store.db.execute('SELECT * FROM outbox WHERE id=?',(event,)).fetchone()
        self.assertIsNone(row['delivered_at']);self.assertEqual(row['attempts'],1)
        deliver(self.store,self.transport,now=now+1)
        self.transport.react.assert_called_once()
        self.transport.react.side_effect=None
        deliver(self.store,self.transport,now=now+30)
        self.assertEqual(self.transport.react.call_count,2)
        self.assertEqual(self.transport.react.call_args_list[0],self.transport.react.call_args_list[1])
        self.assertIsNotNone(self.store.db.execute('SELECT delivered_at FROM outbox').fetchone()[0])

    def test_account_drift_and_missing_payload_fail_closed(self):
        enqueue(self.store,self.run,'aa','dd','👀','one')
        self.transport.account_id='ff'
        deliver(self.store,self.transport,now=9999999999)
        self.transport.react.assert_not_called();self.transport.send.assert_not_called()
        with self.store.db:self.store.db.execute('DELETE FROM outbox_reactions')
        deliver(self.store,self.transport,now=10000000030)
        self.transport.send.assert_not_called()

    def test_invalid_intents_never_enqueue(self):
        for emoji in ('', ' 👀', 'x'*65, '\n', '\ud800'):
            with self.assertRaises(MarmotError):enqueue(self.store,self.run,'aa','dd',emoji,'one')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
