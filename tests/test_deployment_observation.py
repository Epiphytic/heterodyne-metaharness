"""Deterministic predicate vectors, not actual guard/host authority."""

import hashlib
import json
from pathlib import Path

import msgspec
import pytest

from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment_observation import (
    HostObservation,
    decode_observation,
    observation_bytes,
    observation_digest,
)

BASE = HostObservation('heterodyne-deployment-observation.v1', 'hermes', 'enabled', 'active',
                       '1' * 40, 'online-pass', 0, '2' * 64)


def test_canonical_vector_is_independent_of_input_order_and_whitespace():
    raw = (
        b'{"active":"active","enabled":"enabled","guard_verdict":"online-pass",'
        b'"installed_tree_sha256":"' + b'2' * 64 + b'","quarantine_count":0,'
        b'"schema":"heterodyne-deployment-observation.v1","selected_release":"'
        + b'1' * 40 + b'","target_id":"hermes"}'
    )
    assert observation_bytes(BASE) == raw
    reordered = json.dumps(dict(reversed(list(json.loads(raw).items()))), indent=2).encode()
    assert observation_digest(decode_observation(reordered)) == observation_digest(BASE)
    assert observation_digest(BASE) == hashlib.sha256(
        b'heterodyne-deployment-observation.v1\x00' + raw).hexdigest()
    assert observation_digest(BASE) != hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('field,value', [
    ('target_id', 'other'), ('enabled', 'disabled'), ('active', 'inactive'),
    ('selected_release', '3' * 40), ('guard_verdict', 'unchecked'), ('quarantine_count', 1),
    ('installed_tree_sha256', '4' * 64),
])
def test_each_predicate_changes_transition_hash(field, value):
    assert observation_digest(msgspec.structs.replace(BASE, **{field: value})) != observation_digest(BASE)


@pytest.mark.parametrize('field,value', [
    ('schema', 'other'), ('target_id', '../other'), ('enabled', 'unknown'),
    ('active', 'activating'), ('selected_release', 'main'), ('guard_verdict', 'pass'),
    ('quarantine_count', -1), ('quarantine_count', True), ('quarantine_count', 2**63),
    ('installed_tree_sha256', 'A' * 64), ('pid', 12), ('observed_ns', 100),
])
def test_malformed_unknown_or_volatile_fields_refuse(field, value):
    obj = json.loads(msgspec.json.encode(BASE))
    obj[field] = value
    with pytest.raises(InvalidAction):
        decode_observation(json.dumps(obj).encode())


def test_duplicate_keys_and_direct_invalid_host_record_refuse():
    raw = msgspec.json.encode(BASE).replace(b'"quarantine_count":0',
                                           b'"quarantine_count":0,"quarantine_count":1')
    with pytest.raises(InvalidAction):
        decode_observation(raw)
    with pytest.raises(InvalidAction):
        observation_digest(msgspec.structs.replace(BASE, quarantine_count=-1))


def test_committed_python_rust_golden() -> None:
    fixture = json.loads((Path(__file__).parent / 'fixtures/deployment-observation/v1.json').read_bytes())
    value = decode_observation(json.dumps(fixture['value']).encode())
    assert observation_bytes(value) == fixture['canonical_utf8'].encode()
    assert observation_digest(value) == fixture['sha256']
    assert bytes.fromhex(fixture['domain_hex']) == b'heterodyne-deployment-observation.v1\0'
