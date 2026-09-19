"""Codex completion receipts; contract: spec/status.md."""
import hashlib
import json
import os
import subprocess
import sys
import time

from . import status
from .brain_hooks import session_key
from .store import Store


def receive(store, payload):
    if payload.get('type') != 'agent-turn-complete':
        return False
    native, turn, cwd = (payload.get(key) for key in ('thread-id', 'turn-id', 'cwd'))
    if not all(isinstance(value, str) and value for value in (native, turn, cwd)):
        raise ValueError('Completion requires native identity, turn and cwd')
    with store.lock(blocking=False):
        session = session_key(store, 'codex', {'session_id': native, 'cwd': cwd})
        owner, _, role = session.rpartition(':')
        if role != 'worker':
            return False
        run = store.get(owner)
        if run.get('native_session_id') != native:
            return False
        identity = 'native-notify:' + hashlib.sha256(json.dumps([owner, native, turn]).encode()).hexdigest()
        receipt = {'native': native, 'turn': turn, 'at': time.time(),
                   'message_sha256': hashlib.sha256(str(payload.get('last-assistant-message', '')).encode()).hexdigest()}
        with store.db:
            if store.db.execute('SELECT 1 FROM events WHERE id=?', (identity,)).fetchone():
                return False
            matched = run.get('native_turn_key') == [native, turn]
            receipt['applied'] = matched
            if matched:
                if run.get('native_turn_state') == 'working':
                    from .continuation import completed
                    completed(run, turn, str(payload.get('last-assistant-message', '')))
                status.activity(run, native, turn, 'idle', receipt['at'])
                if not run.get('resume_required') and run.get('task_state') == 'working':
                    run['task_state'] = 'idle'
                run['native_completion'] = receipt
                store.save(run)
            store.db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                             (identity, owner, 'native_completion', json.dumps(receipt), receipt['at']))
        if matched:
            store.checkpoint(run)
        return matched


def main():
    """Fail open without printing payloads; preserve an existing notifier independently."""
    raw = sys.argv[-1]
    store = None
    try:
        if len(raw.encode()) > 1024 * 1024:
            raise ValueError('Oversized completion payload')
        if os.environ.get('HERMES_WORKSTREAM_RUN'):
            store = Store()
            receive(store, json.loads(raw))
    except Exception as exc:
        print('Native completion deferred: ' + type(exc).__name__, file=sys.stderr)
    finally:
        if store:
            store.db.close()
    try:
        if len(sys.argv) == 4 and sys.argv[1] == '--forward':
            prior = json.loads(sys.argv[2])
            if prior:
                subprocess.run(prior + [raw], timeout=15, check=False)
    except Exception as exc:
        print('Prior notifier failed: ' + type(exc).__name__, file=sys.stderr)


if __name__ == '__main__':
    main()
