#!/usr/bin/env python3
"""Deterministic watchdog; contract: spec/marmot-wedge-watch.md.

Activation records provide before/after evidence for connector fixes. No LLM,
no direct database access, and no direct gateway restart. Socket liveness uses account_list; never run wn against the live home.
"""
import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

TIMER = 'hermes-gw-deploy.timer'
NOTICE = 'Armed hermes-gw-deploy.timer; gateway restart + replay in ~90s.'


def timestamp(value):
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            value = dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid timestamp')
    return float(value)


def allowed_users(path):
    values = []
    for line in path.read_text().splitlines():
        match = re.match(r'^\s*(?:export\s+)?MARMOT_ALLOWED_USERS\s*=\s*(.*?)\s*$', line)
        if match:
            values.append(match[1].strip('"\''))
    if len(values) != 1:
        raise ValueError('expected one MARMOT_ALLOWED_USERS assignment')
    users = {x.strip().lower() for x in values[0].split(',')}
    if not users or any(not re.fullmatch('[0-9a-f]{64}', x) for x in users):
        raise ValueError('MARMOT_ALLOWED_USERS must contain 64-hex keys')
    return users


def last_inbound(path):
    newest = None
    with path.open() as source:
        for line in source:
            if 'INFO gateway.run: inbound message: platform=marmot' not in line:
                continue
            match = re.search(r'\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:\d\d)?', line)
            if not match:
                raise ValueError('inbound log line lacks a timestamp')
            value = timestamp(match[0].replace(',', '.'))
            newest = value if newest is None else max(newest, value)
    if newest is None:
        raise ValueError('no Marmot inbound timestamp in gateway log')
    return newest


def probe_socket(path, timeout=5):
    """Bounded documented account_list probe; no arbitrary text operations."""
    request_id = uuid.uuid4().hex
    request = {'marmot_agent_control': 'marmot.agent-control.v2',
               'id': request_id, 'type': 'account_list'}
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(json.dumps(request).encode() + b'\n')
        response = bytearray()
        while b'\n' not in response:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('socket probe timed out')
            connection.settimeout(remaining)
            chunk = connection.recv(4096)
            if not chunk:
                raise ValueError('socket EOF before response')
            response.extend(chunk)
            if len(response) > 65536:
                raise ValueError('socket response exceeds bound')
    data = json.loads(response.split(b'\n', 1)[0])
    if (not isinstance(data, dict) or data.get('id') != request_id
            or data.get('marmot_agent_control') != request['marmot_agent_control']
            or data.get('type') != 'account_list' or not isinstance(data.get('accounts'), list)
            or not data['accounts']):
        raise ValueError('invalid account_list response')


def append_record(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as target:
        target.write(json.dumps(record, sort_keys=True) + '\n')
        target.flush()
        os.fsync(target.fileno())
    sync_directory(path.parent)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_state(path, state):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as target:
        json.dump(state, target, sort_keys=True)
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def assess(args, now, probe=probe_socket):
    heartbeat = json.loads(args.heartbeat.read_text())
    started = timestamp(heartbeat['start_time'])
    if started > now:
        raise ValueError('gateway start time is in the future')
    if now - started < 300:
        return {'reason': 'startup_grace'}
    mtime = args.heartbeat.stat().st_mtime
    if mtime > now + 60:
        raise ValueError('heartbeat mtime is in the future')
    # A dead connector is escalated, not "fixed" by restarting its gateway.
    try:
        probe(args.socket)
    except (OSError, ValueError) as exc:
        raise ValueError('socket_unavailable: connector probe failed; manager intervention required') from exc
    if now - mtime > 180:
        return dict(reason='stale_heartbeat', gw_last_inbound=None, gw_start_time=started)
    allowed_users(args.env_file)  # Native inbound log is already sender-filtered.
    gw_last = last_inbound(args.gw_log)
    if gw_last > now + 60:
        raise ValueError('gateway inbound timestamp is in the future')
    if now - gw_last > args.inbound_max_age:
        return dict(reason='inbound_inactivity', gw_last_inbound=gw_last, gw_start_time=started,
                    threshold_seconds=args.inbound_max_age)
    return None


def run(args, now=None, execute=subprocess.run, probe=probe_socket):
    now = time.time() if now is None else now
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(args.state.read_text()) if args.state.exists() else {}
        # A crash after intent has uncertain external effects. Do not arm again
        # automatically; operator reconciles timer and journal against intent ID.
        if state.get('pending'):
            raise ValueError('unresolved activation intent; reconcile timer before retry')
        reason = assess(args, now, probe)
        if reason and reason['reason'] == 'startup_grace':
            return ''
        if reason is None:
            if state.get('last_trigger'):
                save_state(args.state, {'last_trigger': 0})
            return ''
        last = timestamp(state.get('last_trigger', 0))
        if last and now - last < 900:
            return ''
        activation = dict(reason, ts=now, activation_id=uuid.uuid4().hex)
        append_record(args.activation_log, dict(activation, phase='intent', armed_timer=False))
        save_state(args.state, {'last_trigger': now, 'pending': activation})
        result = execute(['/usr/bin/systemctl', '--user', 'start', TIMER],
                         capture_output=True, text=True, timeout=20, check=False)
        armed = result.returncode == 0
        append_record(args.activation_log, dict(activation, phase='result', armed_timer=armed,
                                               returncode=result.returncode))
        save_state(args.state, {'last_trigger': now})
        if not armed:
            raise ValueError('timer arming command failed (exit ' + str(result.returncode) + ')')
        return reason['reason'] + ': ' + NOTICE


def main():
    home = Path.home() / '.hermes'
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in [('gw-log', home / 'logs/gateway.log'),
                          ('heartbeat', home / 'state/gateway.heartbeat'),
                          ('env-file', home / '.env'),
                          ('socket', Path.home() / '.marmot-agents/hermes/dev/wn-agent.sock'),
                          ('state', home / 'state/marmot-wedge-watch.json'),
                          ('activation-log', home / 'logs/marmot-wedge-watch.log')]:
        parser.add_argument('--' + name, type=Path, default=default)
    parser.add_argument('--check', action='store_true', help='Report assessment without state writes or timer activation')
    parser.add_argument('--inbound-max-age', type=int, default=300)
    try:
        args = parser.parse_args()
        if args.inbound_max_age < 300:
            raise ValueError('inbound max age must be at least 300 seconds')
        if args.check:
            print(json.dumps({'assessment': assess(args, time.time())}, sort_keys=True))
            return 0
        notice = run(args)
        if notice:
            print(notice)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print('marmot-wedge-watch ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
