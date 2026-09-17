import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from harness.marmot import Marmot, MarmotError, PROTOCOL, UncertainOutcome


class MarmotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'agent.sock')
        self.requests = []

    def client(self, response, **overrides):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.path)
        server.listen(1)
        self.addCleanup(server.close)

        def serve():
            with server.accept()[0] as conn:
                raw = b''
                while b'\n' not in raw:
                    raw += conn.recv(4096)
                request = json.loads(raw)
                self.requests.append(request)
                result = response(request)
                if result is None:
                    return
                data = result if isinstance(result, bytes) else json.dumps(
                    dict(marmot_agent_control=PROTOCOL, id=request['id'], **result)).encode() + b'\n'
                try:
                    conn.sendall(data)
                except BrokenPipeError:
                    pass
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 1)
        return Marmot(dict(socket=self.path, account_id='aa', members=['bb'],
                           bootstrap=str(Path(self.temp.name) / 'absent'), timeout=.2, **overrides))

    def test_send_carries_durable_idempotency_key_and_validates_ack(self):
        client = self.client(lambda _: {'type': 'final_sent', 'message_ids_hex': ['cc']})
        self.assertEqual(client.send('bb', 'status', 'event-1')['message_ids_hex'], ['cc'])
        self.assertEqual(self.requests[0]['idempotency_key'], 'event-1')

    def test_server_error_is_not_success(self):
        client = self.client(lambda _: {'type': 'error', 'code': 'send_in_progress'})
        with self.assertRaises(MarmotError):
            client.send('bb', 'status', 'e')

    def test_missing_message_ids_is_not_success(self):
        client = self.client(lambda _: {'type': 'final_sent', 'message_ids_hex': []})
        with self.assertRaises(MarmotError):
            client.send('bb', 'status', 'e')

    def test_wrong_id_is_not_success(self):
        client = self.client(lambda _: json.dumps(dict(marmot_agent_control=PROTOCOL,
                         id='wrong', type='final_sent', message_ids_hex=['cc'])).encode() + b'\n')
        with self.assertRaisesRegex(MarmotError, 'id mismatch'):
            client.send('bb', 'status', 'e')

    def test_empty_response_is_failure(self):
        client = self.client(lambda _: None)
        with self.assertRaises(MarmotError):
            client.send('bb', 'status', 'e')

    def test_create_group_success(self):
        client = self.client(lambda _: {'type': 'group_created', 'group_id_hex': 'dd'})
        self.assertEqual(client.create_group('project', 'intent-1'), 'dd')
        self.assertIn('intent-1', self.requests[0]['description'])

    def test_lost_creation_ack_is_uncertain(self):
        client = self.client(lambda _: None)
        with self.assertRaises(UncertainOutcome):
            client.create_group('project', 'intent-1')
        self.assertEqual(len(self.requests), 1)

    def test_creation_timeout_is_uncertain(self):
        def delayed(_):
            time.sleep(.3)
            return None
        client = self.client(delayed)
        with self.assertRaises(UncertainOutcome):
            client.create_group('project', 'intent-1')

    def test_correlated_creation_rejection_is_definite(self):
        client = self.client(lambda _: {'type': 'error', 'code': 'invalid_members'})
        with self.assertRaises(MarmotError) as result:
            client.create_group('project', 'intent-1')
        self.assertNotIsInstance(result.exception, UncertainOutcome)

    def test_invalid_creation_ack_is_uncertain(self):
        client = self.client(lambda _: {'type': 'group_created'})
        with self.assertRaises(UncertainOutcome):
            client.create_group('project', 'intent-1')

    def test_group_info_verifies_account_group_and_membership(self):
        client = self.client(lambda _: dict(type='group_info', account_id_hex='aa',
                         group_id_hex='bb', member_count=2, is_direct=True))
        self.assertEqual(client.group_info('bb')['member_count'], 2)

    def test_group_info_rejects_different_group(self):
        client = self.client(lambda _: dict(type='group_info', account_id_hex='aa',
                         group_id_hex='cc', member_count=2, is_direct=True))
        with self.assertRaises(MarmotError):
            client.group_info('bb')

    def test_wrong_protocol_fails(self):
        client = self.client(lambda req: json.dumps(dict(marmot_agent_control='old',
                         id=req['id'], type='final_sent', message_ids_hex=['cc'])).encode() + b'\n')
        with self.assertRaises(MarmotError):
            client.send('bb', 'status', 'e')

    def test_partial_unframed_ack_fails(self):
        client = self.client(lambda req: json.dumps(dict(marmot_agent_control=PROTOCOL,
                         id=req['id'], type='final_sent', message_ids_hex=['cc'])).encode())
        with self.assertRaises(MarmotError):
            client.send('bb', 'status', 'e')


if __name__ == '__main__':
    unittest.main()
