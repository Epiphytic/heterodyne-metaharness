import copy
import unittest

from harness import blocker_receipts as receipts
from harness.admin_references import npub
from harness.beads import BeadsError

try:
    from coincurve import PrivateKey
except ImportError:
    PrivateKey = None


def signed(body, key=None):
    key = key or PrivateKey.from_int(7)
    event = dict(pubkey=key.public_key_xonly.format().hex(), created_at=body['issued_at'],
                 kind=1, tags=[], content=receipts.canonical(body))
    event['id'] = receipts.event_id(event)
    event['sig'] = key.sign_schnorr(bytes.fromhex(event['id'])).hex()
    return event


@unittest.skipIf(PrivateKey is None, 'install requirements-blockers.txt for signature fixtures')
class ReceiptTest(unittest.TestCase):
    def setUp(self):
        self.key = PrivateKey.from_int(7)
        self.public = self.key.public_key_xonly.format().hex()
        self.evidence = {'answer': 'approved command digest'}
        self.expected = {'schema': 'test', 'nonce': 'one-request'}
        self.body = dict(self.expected, decision='approve', evidence_sha256=receipts.digest(self.evidence),
                         npub=npub(self.public), issued_at=100, expires_at=200)
        self.policy = {'signers': [self.public], 'event_kind': 1}
        self.event = signed(self.body, self.key)

    def test_real_signature_and_evidence(self):
        self.assertEqual(receipts.verify(self.event, self.evidence, self.expected, self.policy, 150), self.body)

    def test_tamper_signature_event_and_evidence(self):
        for field, value in [('sig', '00' * 64), ('content', '{}'), ('pubkey', '00' * 32)]:
            event = dict(self.event, **{field: value})
            with self.subTest(field=field), self.assertRaises(BeadsError):
                receipts.verify(event, self.evidence, self.expected, self.policy, 150)
        with self.assertRaises(BeadsError):
            receipts.verify(self.event, {'answer': 'different'}, self.expected, self.policy, 150)

    def test_authority_scope_expiry_and_display(self):
        for policy in ({'signers': [], 'event_kind': 1}, dict(self.policy, revoked=[self.public]),
                       dict(self.policy, event_kind=7)):
            with self.assertRaises(BeadsError):
                receipts.verify(self.event, self.evidence, self.expected, policy, 150)
        for now in (99, 200):
            with self.assertRaises(BeadsError):
                receipts.verify(self.event, self.evidence, self.expected, self.policy, now)
        for field in ('npub', 'nonce'):
            with self.assertRaises(BeadsError):
                receipts.verify(signed(dict(self.body, **{field: 'wrong'})), self.evidence,
                                self.expected, self.policy, 150)

    def test_strict_json_and_numeric_types(self):
        for raw in ('{"x":1,"x":2}', 'x' * 65537):
            with self.assertRaises(BeadsError):
                receipts.decode(raw)
        for value in (1.5, float('nan')):
            with self.assertRaises(BeadsError):
                receipts.canonical({'x': value})
        event = dict(self.event, created_at=True)
        with self.assertRaises(BeadsError):
            receipts.verify_event(event)
