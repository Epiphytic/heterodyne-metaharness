import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('wedge_watch', Path(__file__).resolve().parents[1] / 'scripts/marmot-wedge-watch.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class WedgeWatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.args = SimpleNamespace(**{name: root / name for name in
            ('heartbeat', 'gw_log', 'env_file', 'socket', 'state', 'activation_log')})
        self.now = 1800000000
        self.args.inbound_max_age = 300
        self.probe = Mock()
        self.args.heartbeat.write_text(json.dumps({'start_time': self.now - 1000}))
        os.utime(self.args.heartbeat, (self.now, self.now))
        self.args.env_file.write_text('MARMOT_ALLOWED_USERS=' + 'a' * 64 + '\n')
        self.args.gw_log.write_text('2027-01-15T08:00:00+00:00 INFO gateway.run: inbound message: platform=marmot\n')
        self.gw_last = watch.last_inbound(self.args.gw_log)
        self.execute = Mock(return_value=SimpleNamespace(returncode=0))

    def stale_log(self):
        self.args.gw_log.write_text('2027-01-15T07:50:00+00:00 INFO gateway.run: inbound message: platform=marmot\n')

    def run_watch(self):
        return watch.run(self.args, self.now, self.execute, self.probe)

    def test_healthy_silent_no_activation(self):
        self.assertEqual(self.run_watch(), '')
        self.execute.assert_not_called()
        self.assertFalse(self.args.activation_log.exists())

    def test_wedge_intent_durable_before_arm_and_cooldown(self):
        self.stale_log()
        def arm(argv, **kwargs):
            self.assertEqual(argv, ['/usr/bin/systemctl', '--user', 'start', watch.TIMER])
            intent = json.loads(self.args.activation_log.read_text())
            self.assertEqual(intent['phase'], 'intent')
            self.assertFalse(intent['armed_timer'])
            self.assertTrue(json.loads(self.args.state.read_text())['pending'])
            return SimpleNamespace(returncode=0)
        self.execute.side_effect = arm
        self.assertIn(watch.NOTICE, self.run_watch())
        self.assertEqual(self.run_watch(), '')
        self.assertEqual(self.execute.call_count, 1)
        records = [json.loads(x) for x in self.args.activation_log.read_text().splitlines()]
        self.assertTrue(records[1]['armed_timer'])

    def test_stale_heartbeat_without_message_source(self):
        os.utime(self.args.heartbeat, (self.now - 181, self.now - 181))
        self.assertTrue(self.run_watch().startswith('stale_heartbeat:'))

    def test_startup_grace(self):
        self.args.heartbeat.write_text(json.dumps({'start_time': self.now - 299}))
        self.assertEqual(self.run_watch(), '')
        self.execute.assert_not_called()

    def test_startup_preserves_activation_cooldown(self):
        self.stale_log()
        self.run_watch()
        self.args.heartbeat.write_text(json.dumps({'start_time': self.now - 1}))
        self.assertEqual(self.run_watch(), '')
        self.assertEqual(json.loads(self.args.state.read_text())['last_trigger'], self.now)
        self.now += 301
        os.utime(self.args.heartbeat, (self.now, self.now))
        self.assertEqual(self.run_watch(), '')
        self.assertEqual(self.execute.call_count, 1)

    def test_quiet_chat_is_inactivity_not_proven_wedge(self):
        self.stale_log()
        self.assertTrue(self.run_watch().startswith('inbound_inactivity:'))

    def test_invalid_allowlist_is_error(self):
        self.args.env_file.write_text('MARMOT_ALLOWED_USERS=*\n')
        with self.assertRaises(ValueError):
            self.run_watch()
        self.execute.assert_not_called()

    def test_failure_does_not_claim_arm_success(self):
        self.stale_log()
        self.execute.return_value.returncode = 1
        with self.assertRaisesRegex(ValueError, 'arming command failed'):
            self.run_watch()
        result = json.loads(self.args.activation_log.read_text().splitlines()[-1])
        self.assertFalse(result['armed_timer'])

    def test_uncertain_arm_blocks_retry(self):
        self.stale_log()
        self.execute.side_effect = TimeoutError('uncertain')
        with self.assertRaises(TimeoutError):
            self.run_watch()
        with self.assertRaisesRegex(ValueError, 'unresolved activation'):
            self.run_watch()
        self.assertEqual(self.execute.call_count, 1)

    def test_socket_failure_escalates_without_gateway_restart(self):
        self.probe.side_effect = OSError('unavailable')
        with self.assertRaisesRegex(ValueError, 'socket_unavailable'):
            self.run_watch()
        self.execute.assert_not_called()

    def test_healthy_resets_cooldown(self):
        self.args.state.write_text(json.dumps({'last_trigger': self.now-10}))
        self.assertEqual(self.run_watch(), '')
        self.assertEqual(json.loads(self.args.state.read_text())['last_trigger'], 0)

    def test_log_missing_is_error(self):
        self.args.gw_log.write_text('unrelated log\n')
        with self.assertRaisesRegex(ValueError, 'no Marmot inbound'):
            self.run_watch()
        self.execute.assert_not_called()

class SocketProbeTest(unittest.TestCase):
    def connection(self, response):
        from unittest.mock import patch
        connection = Mock()
        request = {}
        connection.sendall.side_effect = lambda raw: request.update(json.loads(raw))
        def receive(_size):
            data = {'marmot_agent_control': 'marmot.agent-control.v2', 'id': request['id'],
                    'type': 'account_list', 'accounts': [{'account_id_hex': 'a'*64}]}
            data.update(response)
            return json.dumps(data).encode() + b'\n'
        connection.recv.side_effect = receive
        factory = patch.object(watch.socket, 'socket')
        factory.start().return_value.__enter__.return_value = connection
        self.addCleanup(factory.stop)
        return connection, request

    def test_documented_operation_only(self):
        connection, request = self.connection({})
        watch.probe_socket('/fake/socket')
        self.assertEqual(request['type'], 'account_list')
        self.assertNotIn('text', request)
        connection.connect.assert_called_once_with('/fake/socket')

    def test_rejection_and_mismatched_reply(self):
        for response in ({'type': 'error'}, {'id': 'different'}, {'accounts': []}):
            with self.subTest(response=response):
                self.connection(response)
                with self.assertRaises(ValueError):
                    watch.probe_socket('/fake/socket')

    def test_eof_fails(self):
        connection, _ = self.connection({})
        connection.recv.side_effect = None
        connection.recv.return_value = b''
        with self.assertRaisesRegex(ValueError, 'EOF'):
            watch.probe_socket('/fake/socket')
