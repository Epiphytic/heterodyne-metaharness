"""Synthetic correlation fixtures, never native consent or effect authority.

Transport credentials/lifetime are qualified separately by actual namespace tests.
These values intentionally model only the independent source/original binding.
"""

import copy
import hashlib
import json
from types import SimpleNamespace

import msgspec
import pytest
from test_deployment_producer_process import inputs, published  # noqa: F401, F811

from heterodyne.actions import deployment_admission_binding as binding
from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment_card import _derive
from heterodyne.actions.deployment_outage import decode_packet


@pytest.fixture
def correlated(inputs, monkeypatch):  # noqa: F811 - imported pytest fixture injection
    original, canonical, producer, _, _, _, _ = inputs
    expected = _derive(original, producer.targets, producer.group_hex, producer.publication_url)
    def h(raw):
        return hashlib.sha256(raw).hexdigest()
    enrollment = {k: 'a' * 64 for k in binding._ENROLLMENT_FIELDS}
    enrollment.update(schema='heterodyne-receiver-enrollment.v3', group=producer.group_hex,
                      operators=['c' * 64], capability_template='admind-receiver-v1')
    process = json.dumps({'schema': 'heterodyne-protected-receiver-process.v3',
                          'enrollment': enrollment}, separators=(',', ':')).encode()
    producer_raw = msgspec.json.encode(producer)
    channel = SimpleNamespace(
        check=lambda: None, nonce=b'n'*32,
        profile=SimpleNamespace(installation_index_sha256='a'*64, installation_source_sha256='a'*64,
                                semantic_factory_source_sha256=decode_packet(
                                    original, producer.targets).semantic_factory_source_sha256,
                                packet_sha256=producer.packet_sha256,
                                producer_profile_sha256=h(producer_raw)),
        handoff=SimpleNamespace(process_profile_sha256=h(process)),
    )
    value = dict(
        schema='heterodyne-owned-consumed.v3', nonce_hex=(b'b'*32).hex(),
        startup_profile_sha256=h(process), enrollment_sha256=h(msgspec.json.encode(enrollment)),
        journal_profile_sha256='a'*64, installation_index_sha256='a'*64,
        installation_source_sha256='a'*64, capability_template='admind-receiver-v1',
        capability_policy_sha256='a'*64,
        original=dict(ask_id=expected.ask_id, expected_group=list(bytes.fromhex(expected.group_hex)),
                      operation=original.hex(), operation_canonical=canonical.hex(),
                      original_request=expected.request_original.hex(), card_chunks=[expected.card]),
        consumption_key=dict(header_sha256='b'*64, card_complete_sequence=3, ask_id=expected.ask_id,
                             original_operation_sha256=h(original)),
        receipt=dict(sequence=4, ask_id=expected.ask_id, operation_sha256=h(original),
                     packet_sha256=producer.packet_sha256, request_sha256=h(expected.request_original),
                     cards_sha256=h(msgspec.json.encode([expected.card])),
                     policy_sha256=h(msgspec.json.encode([list(bytes.fromhex(producer.group_hex)),
                                                         [list(bytes.fromhex('c'*64))]])),
                     operator=list(bytes.fromhex('c'*64)), decision='Approve',
                     authenticated_candidate={'fixture': 'not authenticated consent'}),
    )
    monkeypatch.setattr(binding, '_read', lambda fd, bound: {7: process, 13: producer_raw, 18: original}[fd])
    return channel, value


def test_synthetic_binding_matches_independent_retained_originals(correlated):
    channel, value = correlated
    channel.consumed_raw = msgspec.json.encode(value)
    result = binding._correlate(channel)
    assert result.receipt.ask_id == value['original']['ask_id']


@pytest.mark.parametrize('section,field,replacement', [
    (None, 'nonce_hex', (b'n'*32).hex()), (None, 'enrollment_sha256', '0'*64),
    (None, 'journal_profile_sha256', '0'*64), (None, 'capability_policy_sha256', '0'*64),
    ('original', 'operation', '00'), ('original', 'original_request', '00'),
    ('original', 'card_chunks', ['different']), ('original', 'expected_group', [0]*16),
    ('receipt', 'decision', 'Deny'), ('receipt', 'operator', [0]*32),
    ('receipt', 'sequence', 0), ('receipt', 'cards_sha256', '0'*64),
    ('receipt', 'policy_sha256', '0'*64),
    ('consumption_key', 'card_complete_sequence', 0),
    ('consumption_key', 'original_operation_sha256', '0'*64),
])
def test_synthetic_correlation_rejects_changed_binding(correlated, section, field, replacement):
    channel, original = correlated
    value = copy.deepcopy(original)
    (value[section] if section else value)[field] = replacement
    channel.consumed_raw = msgspec.json.encode(value)
    with pytest.raises(InvalidAction):
        binding._correlate(channel)


def test_live_parent_must_remain_available_during_correlation(correlated):
    channel, value = correlated
    channel.consumed_raw = msgspec.json.encode(value)
    def ceased():
        raise InvalidAction('admission-parent-ceased')
    channel.check = ceased
    with pytest.raises(InvalidAction, match='admission-parent-ceased'):
        binding._correlate(channel)


def test_enrollment_group_is_independently_bound(correlated, monkeypatch):
    channel, value = correlated
    previous = binding._read
    def changed(fd, bound):
        raw = previous(fd, bound)
        if fd == 7:
            obj = json.loads(raw)
            obj['enrollment']['group'] = '0'*32
            raw = msgspec.json.encode(obj)
            channel.handoff.process_profile_sha256 = hashlib.sha256(raw).hexdigest()
        return raw
    monkeypatch.setattr(binding, '_read', changed)
    channel.consumed_raw = msgspec.json.encode(value)
    with pytest.raises(InvalidAction, match='admission-enrollment-installation'):
        binding._correlate(channel)


def test_profile_used_is_exactly_the_single_hashed_read(correlated, monkeypatch):
    channel, value = correlated
    previous = binding._read
    reads = []
    def once(fd, bound):
        if fd == 13:
            reads.append(fd)
            assert len(reads) == 1
        return previous(fd, bound)
    monkeypatch.setattr(binding, '_read', once)
    channel.consumed_raw = msgspec.json.encode(value)
    binding._correlate(channel)
    assert reads == [13]


def test_native_candidate_sequence_is_independent_of_card_journal(correlated):
    channel, value = correlated
    value['receipt']['sequence'] = 1
    value['consumption_key']['card_complete_sequence'] = 6
    channel.consumed_raw = msgspec.json.encode(value)
    assert binding._correlate(channel).receipt.sequence == 1
