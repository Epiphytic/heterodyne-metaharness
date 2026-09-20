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
            ('heartbeat', 'gw_log', 'env_file', 'inventory', 'state', 'activation_log')})
        self.now = 1800003600
        self.args.heartbeat.write_text(json.dumps({'start_time': self.now - 1000}))
        os.utime(self.args.heartbeat, (self.now, self.now))
        self.args.env_file.write_text('MARMOT_ALLOWED_USERS=' + 'a' * 64 + '\n')
        self.args.gw_log.write_text('2027-01-15T08:00:00+00:00 INFO gateway.run: inbound message: platform=marmot\n')
        self.gw_last = watch.last_inbound(self.args.gw_log)
        self.inventory(self.gw_last)
        self.execute = Mock(return_value=SimpleNamespace(returncode=0))

    def inventory(self, received, sender='a' * 64, direction='received'):
        self.args.inventory.write_text(json.dumps({'complete': True, 'source': 'agent-control',
            'captured_at': self.now, 'chats': [{'activity_sort_at': self.now, 'messages': [
                {'direction': direction, 'from': sender, 'received_at': received}]}]}))

    def run_watch(self):
        return watch.run(self.args, self.now, self.execute)

    def test_healthy_silent_no_activation(self):
        self.assertEqual(self.run_watch(), '')
        self.execute.assert_not_called()
        self.assertFalse(self.args.activation_log.exists())

    def test_wedge_intent_durable_before_arm_and_cooldown(self):
        self.inventory(self.gw_last + 301)
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
        self.args.inventory.unlink()
        self.assertTrue(self.run_watch().startswith('stale_heartbeat:'))

    def test_startup_grace(self):
        self.args.heartbeat.write_text(json.dumps({'start_time': self.now - 299}))
        self.args.inventory.unlink()
        self.assertEqual(self.run_watch(), '')
        self.execute.assert_not_called()

    def test_sender_direction_and_strict_margin(self):
        for received, sender, direction in [(self.gw_last + 300, 'a'*64, 'received'),
                (self.gw_last + 301, 'b'*64, 'received'),
                (self.gw_last + 301, 'a'*64, 'sent')]:
            with self.subTest(sender=sender, direction=direction, received=received):
                self.inventory(received, sender, direction)
                self.assertEqual(self.run_watch(), '')
        self.execute.assert_not_called()

    def test_failure_does_not_claim_arm_success(self):
        self.inventory(self.gw_last + 301)
        self.execute.return_value.returncode = 1
        with self.assertRaisesRegex(ValueError, 'arming command failed'):
            self.run_watch()
        result = json.loads(self.args.activation_log.read_text().splitlines()[-1])
        self.assertFalse(result['armed_timer'])

    def test_uncertain_arm_blocks_retry(self):
        self.inventory(self.gw_last + 301)
        self.execute.side_effect = TimeoutError('uncertain')
        with self.assertRaises(TimeoutError):
            self.run_watch()
        with self.assertRaisesRegex(ValueError, 'unresolved activation'):
            self.run_watch()
        self.assertEqual(self.execute.call_count, 1)

    def test_invalid_inventory_never_restarts(self):
        for changes in ({'complete': False}, {'source': 'direct-db'}, {'captured_at': self.now-61}):
            with self.subTest(changes=changes):
                self.inventory(self.gw_last + 301)
                data = json.loads(self.args.inventory.read_text())
                data.update(changes)
                self.args.inventory.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
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
