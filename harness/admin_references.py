"""Explicit wn admin lookup; deployment contract: spec/operator-asks.md."""
import json
from pathlib import Path
import re
import subprocess

from .marmot import MarmotError, _hex

# Explicit designated recipients, independently of group membership (user policy).
OPERATORS = (
    '3c9945ed7961a8a5b2ff8a43cb83ce214336fc873b47e770c2d7691fac7925bb',
    '04e4426415f4b047e7668ec08035b12dfadca6e8782fabbf95a34a5d86e92d80',
)
ALPHABET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def npub(public_key):
    """NIP-19 bech32 public-key reference, no signing or key material lookup."""
    raw = bytes.fromhex(_hex(public_key, 'public key'))
    if len(raw) != 32:
        raise MarmotError('Public key must be 32 bytes')
    number = int.from_bytes(raw, 'big') << 4
    data = [(number >> shift) & 31 for shift in range(255, -1, -5)]
    checksum = 1
    for value in [3, 3, 3, 3, 0, 14, 16, 21, 2] + data + [0] * 6:
        top = checksum >> 25
        checksum = ((checksum & 0x1ffffff) << 5) ^ value
        for bit, generator in enumerate((0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)):
            if (top >> bit) & 1:
                checksum ^= generator
    checksum ^= 1
    return 'npub1' + ''.join(ALPHABET[n] for n in data + [(checksum >> shift) & 31 for shift in range(25, -1, -5)])


def lookup(config, account, group, timeout):
    """One bounded CLI call. No retry, live-home fallback or guessed account.

    Explicit configuration is required: the operator must reconcile the known
    side-connection/WAL hazard before enabling this against a running home.
    """
    binary, home = config.get('binary'), config.get('home')
    if not binary or not home:
        raise MarmotError('Admin lookup needs reviewed group_admins binary and home configuration')
    command = [str(Path(binary).expanduser()), '--home', str(Path(home).expanduser()),
               '--secret-store', 'file', '--account', account, 'groups', 'admins', _hex(group, 'group'), '--json']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=True)
        if len(result.stdout) > 65536:
            raise ValueError('response exceeds bound')
        payload = json.loads(result.stdout)
        if payload.get('account_id') != account or payload.get('group_id') != group:
            raise ValueError('account or group mismatch')
        admins = payload['admins']
        if not isinstance(admins, list):
            raise ValueError('admins must be a list')
        references = set()
        for admin in admins:
            reference = admin['npub']
            if not isinstance(reference, str) or not re.fullmatch(r'npub1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{58}', reference):
                raise ValueError('invalid admin npub')
            if reference != npub(admin['admin_id']):
                raise ValueError('admin key/reference mismatch')
            references.add(reference)
        return sorted(references | {npub(key) for key in OPERATORS})
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        # stderr may contain private configuration. Keep it out of chat/outbox errors.
        raise MarmotError('Authoritative admin lookup failed: ' + type(exc).__name__) from exc
