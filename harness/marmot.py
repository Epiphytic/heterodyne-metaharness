"""Bounded synchronous facade over Marmot's installed agent-control v2 protocol.

No retries here: the durable outbox owns retry timing and send idempotency keys.
Group creation is not idempotent in the connector; uncertain results need repair.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import time
import uuid

PROTOCOL = "marmot.agent-control.v2"
MAX_FRAME = 1024 * 1024


class MarmotError(RuntimeError):
    """A configuration, transport, or rejected protocol operation."""


class UncertainOutcome(MarmotError):
    """A creation request may have reached the server; do not recreate it."""


def _hex(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) % 2:
        raise MarmotError(f"{field} must be a nonempty hexadecimal identifier")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise MarmotError(f"{field} must be hexadecimal") from exc
    if any(c not in '0123456789abcdefABCDEF' for c in value):
        raise MarmotError(f"{field} must be hexadecimal")
    return value.lower()


def validate_reaction(emoji):
    if (not isinstance(emoji, str) or not emoji.strip() or emoji != emoji.strip() or len(emoji) > 64
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF for char in emoji)):
        raise MarmotError('reaction must be nonblank, control-free and at most 64 Unicode scalar values without surrounding whitespace')


class Marmot:
    def __init__(self, config: dict):
        bootstrap_path = Path(config.get('bootstrap', '~/.marmot-agents/hermes/bootstrap.json')).expanduser()
        bootstrap = {}
        if bootstrap_path.exists():
            try:
                bootstrap = json.loads(bootstrap_path.read_text())
                if not isinstance(bootstrap, dict):
                    raise ValueError('expected object')
            except (OSError, ValueError) as exc:
                raise MarmotError('cannot read Marmot bootstrap configuration') from exc
        self.socket_path = str(Path(config.get('socket') or config.get('socket_path') or
                                   os.environ.get('MARMOT_AGENT_SOCKET') or bootstrap.get('socket') or
                                   '~/.marmot-agents/hermes/dev/wn-agent.sock').expanduser())
        self.account_id = _hex(config.get('account_id') or config.get('account_id_hex') or
                               os.environ.get('MARMOT_ACCOUNT_ID') or bootstrap.get('account_id_hex'), 'account_id')
        self.members = config.get('members', [])
        self.relays = config.get('relays', bootstrap.get('relays'))
        if not isinstance(self.members, list) or not all(isinstance(x, str) and x for x in self.members):
            raise MarmotError('members must be a list of account/public-key references')
        if self.relays is not None and (not isinstance(self.relays, list) or
                                        not all(isinstance(x, str) and x for x in self.relays)):
            raise MarmotError('relays must be a list of relay URLs')
        self.timeout = float(config.get('timeout', 20))
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise MarmotError('timeout must be finite and positive')
        self.auth_token = config.get('auth_token') or os.environ.get('MARMOT_AGENT_AUTH_TOKEN')
        token_file = config.get('auth_token_file') or os.environ.get('MARMOT_AGENT_AUTH_TOKEN_FILE')
        if not self.auth_token and token_file:
            self.auth_token = Path(token_file).expanduser().read_text().strip()
            if not self.auth_token:
                raise MarmotError('Marmot auth token file is empty')

    def _request(self, payload: dict, expected: str, request_id: str, *, creating=False) -> dict:
        envelope = dict(payload, marmot_agent_control=PROTOCOL, id=request_id)
        if self.auth_token:
            envelope['auth_token'] = self.auth_token
        frame = json.dumps(envelope, separators=(',', ':')).encode() + b'\n'
        if len(frame) > MAX_FRAME:
            raise MarmotError('Marmot request exceeds frame limit')
        attempted = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                deadline = time.monotonic() + self.timeout
                conn.settimeout(self.timeout)
                conn.connect(self.socket_path)
                attempted = True
                conn.sendall(frame)
                raw = bytearray()
                while b'\n' not in raw:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Marmot response deadline exceeded')
                    conn.settimeout(remaining)
                    chunk = conn.recv(min(65536, MAX_FRAME + 1 - len(raw)))
                    if not chunk:
                        raise MarmotError('Marmot socket closed before a response')
                    raw.extend(chunk)
                    if len(raw) > MAX_FRAME:
                        raise MarmotError('Marmot response exceeds frame limit')
            response = json.loads(raw.split(b'\n', 1)[0])
            if not isinstance(response, dict) or response.get('marmot_agent_control') != PROTOCOL:
                raise MarmotError('invalid Marmot protocol response')
            if response.get('id') != request_id:
                raise MarmotError('Marmot response id mismatch')
            # A correlated error confirms rejection, unlike a lost acknowledgement.
            if response.get('type') == 'error':
                attempted = False
                raise MarmotError('Marmot server rejected request: ' + str(response.get('code', 'unknown')))
            if response.get('type') != expected:
                raise MarmotError('unexpected Marmot response type')
            return response
        except (OSError, ValueError, MarmotError) as exc:
            if creating and attempted:
                raise UncertainOutcome('group creation outcome unknown; reconcile before retrying') from exc
            if isinstance(exc, MarmotError):
                raise
            raise MarmotError('Marmot transport or framing failure') from exc

    def send(self, group_id: str, text: str, event_id: str) -> dict:
        if not event_id or not isinstance(event_id, str):
            raise MarmotError('send requires a durable event id')
        response = self._request({'type': 'send_final', 'account_id_hex': self.account_id,
                                  'group_id_hex': _hex(group_id, 'group_id'), 'text': text,
                                  'idempotency_key': event_id}, 'final_sent', event_id)
        ids = response.get('message_ids_hex')
        if not isinstance(ids, list) or not ids:
            raise MarmotError('Marmot final_sent returned no message ids')
        for value in ids:
            _hex(value, 'message_id')
        return response

    def react(self, group_id: str, target_id: str, emoji: str, event_id: str) -> dict:
        """Ensure an active own reaction; connector deduplicates by tuple, not key.

        Contract: spec/reactions.md. The durable key correlates this single attempt.
        """
        if not isinstance(event_id, str) or not event_id:
            raise MarmotError('reaction requires a durable event id')
        validate_reaction(emoji)
        response = self._request({'type': 'send_reaction', 'account_id_hex': self.account_id,
                                  'group_id_hex': _hex(group_id, 'group_id'),
                                  'target_message_id_hex': _hex(target_id, 'target_id'), 'emoji': emoji},
                                 'app_event_sent', event_id)
        ids = response.get('message_ids_hex')
        if not isinstance(ids, list) or not ids:
            raise MarmotError('Marmot app_event_sent returned no message ids')
        for value in ids:
            _hex(value, 'message_id')
        return response

    def group_info(self, group_id: str) -> dict:
        group_id = _hex(group_id, 'group_id')
        response = self._request({'type': 'group_info', 'account_id_hex': self.account_id,
                                  'group_id_hex': group_id}, 'group_info', uuid.uuid4().hex)
        if response.get('group_id_hex') != group_id or response.get('account_id_hex') != self.account_id:
            raise MarmotError('group_info returned a different account or group')
        if type(response.get('member_count')) is not int or response['member_count'] < 1:
            raise MarmotError('group_info returned invalid membership')
        if not isinstance(response.get('is_direct'), bool):
            raise MarmotError('group_info returned invalid direct status')
        return response

    def create_group(self, name: str, request_id: str) -> str:
        if not name or not request_id:
            raise MarmotError('group creation requires a name and durable request id')
        if not self.members:
            raise MarmotError('group creation requires explicit members')
        payload = {'type': 'group_create', 'account_id_hex': self.account_id,
                   'name': name, 'members': self.members,
                   'description': 'Hermes workstream creation intent: ' + request_id}
        if self.relays is not None:
            payload['relays'] = self.relays
        response = self._request(payload, 'group_created', request_id, creating=True)
        try:
            return _hex(response.get('group_id_hex'), 'group_id')
        except MarmotError as exc:
            raise UncertainOutcome('group creation acknowledged without a valid group id') from exc
