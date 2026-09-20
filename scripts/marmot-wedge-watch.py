#!/usr/bin/env python3
"""Deterministic watchdog; contract: spec/marmot-wedge-watch.md.

Activation records provide before/after evidence for connector fixes. No LLM,
no direct database access, and no direct gateway restart. Inventory comes from
an operator-reviewed socket-backed exporter; never run wn against the live home.
"""
import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
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


def newest_allowed(inventory, users, gw_last, now):
    """Validate a complete local-receipt inventory, never sender event time."""
    if inventory.get('complete') is not True or inventory.get('source') != 'agent-control':
        raise ValueError('requires complete agent-control inventory')
    captured = timestamp(inventory['captured_at'])
    if not 0 <= now - captured <= 60:
        raise ValueError('stale or future inventory')
    newest = 0.0
    for chat in inventory['chats']:
        if timestamp(chat['activity_sort_at']) < gw_last - 60:
            continue
        for message in chat['messages']:
            if message['direction'] != 'received' or message['from'].lower() not in users:
                continue
            received = timestamp(message['received_at'])
            if received > now + 60:
                raise ValueError('future local receipt timestamp')
            newest = max(newest, received)
    return newest


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


def assess(args, now):
    heartbeat = json.loads(args.heartbeat.read_text())
    started = timestamp(heartbeat['start_time'])
    if started > now:
        raise ValueError('gateway start time is in the future')
    if now - started < 300:
        return None
    mtime = args.heartbeat.stat().st_mtime
    if mtime > now + 60:
        raise ValueError('heartbeat mtime is in the future')
    # Stale heartbeat is independently sufficient; inventory outage must not
    # prevent recovery of a dead gateway. Unavailable message evidence is null.
    if now - mtime > 180:
        return dict(reason='stale_heartbeat', newest_allowed=None,
                    gw_last_inbound=None, gw_start_time=started)
    gw_last = last_inbound(args.gw_log)
    inventory = json.loads(args.inventory.read_text())
    newest = newest_allowed(inventory, allowed_users(args.env_file), gw_last, now)
    if newest > gw_last + 300:
        return dict(reason='wedge', newest_allowed=newest,
                    gw_last_inbound=gw_last, gw_start_time=started)
    return None


def run(args, now=None, execute=subprocess.run):
    now = time.time() if now is None else now
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(args.state.read_text()) if args.state.exists() else {}
        # A crash after intent has uncertain external effects. Do not arm again
        # automatically; operator reconciles timer and journal against intent ID.
        if state.get('pending'):
            raise ValueError('unresolved activation intent; reconcile timer before retry')
        reason = assess(args, now)
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
                          ('inventory', home / 'state/marmot-inbound-inventory.json'),
                          ('state', home / 'state/marmot-wedge-watch.json'),
                          ('activation-log', home / 'logs/marmot-wedge-watch.log')]:
        parser.add_argument('--' + name, type=Path, default=default)
    try:
        notice = run(parser.parse_args())
        if notice:
            print(notice)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print('marmot-wedge-watch ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
