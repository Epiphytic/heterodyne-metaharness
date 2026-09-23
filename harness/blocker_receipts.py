"""Bound NIP-01 decision receipts; never native permission or execution proof."""
import hashlib
import json
import re

from .admin_references import npub
from .beads import BeadsError

LIMIT = 65536


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BeadsError('Duplicate JSON key')
        result[key] = value
    return result


def decode(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > LIMIT:
        raise BeadsError('Receipt exceeds JSON bound')
    try:
        return json.loads(raw, object_pairs_hook=unique_pairs)
    except (ValueError, RecursionError) as exc:
        raise BeadsError('Invalid receipt JSON') from exc


def canonical(value):
    """Version 1 evidence profile: JSON objects/arrays, strings, integers, bool/null."""
    def validate(item, depth=0):
        if depth > 20:
            raise BeadsError('Receipt nesting exceeds bound')
        if isinstance(item, dict):
            if not all(isinstance(k, str) for k in item):
                raise BeadsError('Receipt keys must be strings')
            for child in item.values():
                validate(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                validate(child, depth + 1)
        elif type(item) not in (str, int, bool, type(None)):
            raise BeadsError('Unsupported receipt value')
    validate(value)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    if len(raw.encode('utf-8')) > LIMIT:
        raise BeadsError('Receipt exceeds bound')
    return raw


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def event_id(event):
    value = [0, event['pubkey'], event['created_at'], event['kind'], event['tags'], event['content']]
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def verify_event(event):
    """Verify original signed bytes with libsecp256k1, with no signer fallback."""
    canonical(event)
    required = {'id', 'pubkey', 'created_at', 'kind', 'tags', 'content', 'sig'}
    if not isinstance(event, dict) or set(event) != required:
        raise BeadsError('Complete original Nostr event required')
    for field, size in (('id', 64), ('pubkey', 64), ('sig', 128)):
        if not isinstance(event[field], str) or not re.fullmatch('[0-9a-f]{' + str(size) + '}', event[field]):
            raise BeadsError('Invalid Nostr hex field: ' + field)
    if type(event['created_at']) is not int or event['created_at'] < 0:
        raise BeadsError('Invalid event time')
    if type(event['kind']) is not int or not 0 <= event['kind'] <= 65535:
        raise BeadsError('Invalid event kind')
    if not isinstance(event['content'], str) or not isinstance(event['tags'], list):
        raise BeadsError('Invalid event content/tags')
    if any(not isinstance(tag, list) or not all(isinstance(v, str) for v in tag) for tag in event['tags']):
        raise BeadsError('Invalid event tag')
    if event_id(event) != event['id']:
        raise BeadsError('Nostr event digest mismatch')
    try:
        from coincurve import PublicKeyXOnly
    except ImportError as exc:
        raise BeadsError('Signed receipts require requirements-blockers.txt') from exc
    try:
        valid = PublicKeyXOnly(bytes.fromhex(event['pubkey'])).verify(
            bytes.fromhex(event['sig']), bytes.fromhex(event['id']))
    except ValueError as exc:
        raise BeadsError('Invalid Nostr signature/key') from exc
    if not valid:
        raise BeadsError('Invalid Nostr signature')
    return npub(event['pubkey'])


def verify(event, evidence, expected, policy, now):
    """Policy comes from reviewed config, never from receipt or task text."""
    reference = verify_event(event)
    body = decode(event['content'])
    required = set(expected) | {'decision', 'evidence_sha256', 'issued_at', 'expires_at', 'npub'}
    if not isinstance(body, dict) or set(body) != required:
        raise BeadsError('Decision envelope schema mismatch')
    if any(body.get(k) != v for k, v in expected.items()):
        raise BeadsError('Decision belongs to a different scope/request')
    if body['npub'] != reference or body['evidence_sha256'] != digest(evidence):
        raise BeadsError('Decision evidence/npub mismatch')
    if body['decision'] not in ('approve', 'deny', 'answer'):
        raise BeadsError('Unknown decision')
    if (type(body['issued_at']) is not int or type(body['expires_at']) is not int
            or body['issued_at'] != event['created_at']
            or not body['issued_at'] <= now < body['expires_at']):
        raise BeadsError('Decision expired or from future')
    if event['kind'] != policy.get('event_kind') or event['pubkey'] not in policy.get('signers', []):
        raise BeadsError('Signer/event kind not authorized by current policy')
    if event['pubkey'] in policy.get('revoked', []):
        raise BeadsError('Signer revoked')
    return body
