"""Register native session identities using agent lifecycle hook payloads.

Run as ``python -m harness.native_hook RUN worker|manager``. Never infers an
identity from whichever session happens to have the newest modification time.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import subprocess

from .store import Store

MAX_PAYLOAD = 1024 * 1024


def enrichment(home, run):
    """Optional context cannot hold native identity restoration past its deadline."""
    try:
        result = subprocess.run([sys.executable, '-m', 'harness.hook_context', str(home)],
                                input=json.dumps(run), capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1],
                                timeout=10, check=True)
        return result.stdout[:12000]
    except (OSError, subprocess.SubprocessError):
        return 'Optional memory/Beads context unavailable; use the checkpoint and explicit task/search commands.'


def register(store, run_name_or_id, role, native_id, previous_id=None, *, source=None, cwd=None):
    """Atomically update one owned conversation and its durable identity history."""
    if role not in ('worker', 'manager'):
        raise ValueError('role must be worker or manager')
    if not isinstance(native_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', native_id):
        raise ValueError('invalid native session id')
    with store.lock():
        run = store.get(run_name_or_id)
        target = run if role == 'worker' else run.get('manager')
        if target is None:
            raise ValueError('manager has not been created for this run')
        current = target.get('native_session_id')
        if source is not None:
            if not isinstance(cwd, str) or Path(cwd).resolve() != Path(target['workdir']).resolve():
                raise ValueError('hook cwd does not match the owned session workdir')
            if current and current != native_id and source in ('startup', 'resume'):
                raise ValueError('startup/resume cannot replace an existing native identity')
        history = store.db.execute('''SELECT run_id,role,previous_id FROM native_sessions
          WHERE native_id=?''', (native_id,)).fetchall()
        if any(row['run_id'] != run['id'] or row['role'] != role for row in history):
            raise ValueError('native session id already belongs to another run or role')
        if current == native_id:
            if previous_id is not None and previous_id != current:
                if not any(row['previous_id'] == previous_id for row in history):
                    raise ValueError('previous native session id does not match registered history')
            return run
        if previous_id is not None and previous_id != current:
            raise ValueError('previous native session id does not match current session')
        if history:
            raise ValueError('refusing to roll back to a historical native session id')
        target['previous_native_session_id'] = current
        target['native_session_id'] = native_id
        with store.db:
            store.save(run)
        store.checkpoint(run)
        return run


def payload_registration(store, owner, role, payload):
    if not isinstance(payload, dict):
        raise ValueError('hook input must be an object')
    if payload.get('hook_event_name') != 'SessionStart':
        raise ValueError('only SessionStart identity events are accepted')
    if payload.get('source') not in ('startup', 'resume', 'compact', 'clear'):
        raise ValueError('unsupported SessionStart source')
    return register(store, owner, role, payload.get('session_id'), payload.get('previous_session_id'),
                    source=payload['source'], cwd=payload.get('cwd'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run')
    parser.add_argument('role', choices=('worker', 'manager'), nargs='?')
    args = parser.parse_args(argv)
    if args.run == 'auto':
        owner = os.environ.get('HERMES_WORKSTREAM_RUN')
        if not owner:
            return 0
        args.role = os.environ.get('HERMES_WORKSTREAM_ROLE', 'worker')
        if owner.endswith('-manager'):
            owner = owner[:-len('-manager')]
            args.role = 'manager'
        args.run = owner
    if args.role not in ('worker', 'manager'):
        parser.error('explicit run requires worker or manager role')
    store = None
    try:
        raw = sys.stdin.buffer.read(MAX_PAYLOAD + 1)
        if len(raw) > MAX_PAYLOAD:
            raise ValueError('hook payload exceeds size limit')
        payload = json.loads(raw)
        store = Store()
        run = payload_registration(store, args.run, args.role, payload)
        checkpoint = store.root / 'runs' / run['id'] / 'checkpoint.json'
        context = (f"Durable workstream: {run['name']} ({run['id']}); role: {args.role}. "
                   f"Marmot group: {run.get('group_id') or 'not bound'}. "
                   f"Read {checkpoint} after compaction to recover ownership and task state. "
                   'Use the existing manager and coding session; do not create replacement workstreams.')
        if args.role == 'worker':
            context += '\n' + enrichment(store.root.parent, run)
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart',
                                                'additionalContext': context}}))
        return 0
    except (ValueError, OSError) as exc:
        print(f'native session registration failed: {exc}', file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
