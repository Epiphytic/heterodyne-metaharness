"""Native hook adapters for spec/brain.md; no tool interruption or task pickup."""
import json
import os
from pathlib import Path
import sqlite3
import sys

from . import brain, task_hooks
from .store import Store


def hermes_origin(home, native):
    """Follow only documented compression edges, never arbitrary forks."""
    database = home / 'state.db'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        seen = set()
        for _ in range(100):
            if native in seen:
                raise ValueError('Cyclic Hermes compression lineage')
            seen.add(native)
            row = db.execute('''SELECT p.id FROM sessions c JOIN sessions p ON c.parent_session_id=p.id
                                WHERE c.id=? AND p.end_reason='compression' ''', (native,)).fetchone()
            if not row:
                return native
            native = row[0]
    raise ValueError('Hermes compression lineage exceeds bound')


def session_key(store, provider, payload):
    native = payload.get('session_id')
    if not isinstance(native, str) or not native:
        raise ValueError('Native identity required')
    owner = os.environ.get('HERMES_WORKSTREAM_RUN')
    if owner:
        role = os.environ.get('HERMES_WORKSTREAM_ROLE', 'worker')
        if owner.endswith('-manager'):
            owner, role = owner[:-8], 'manager'
        if role not in ('worker', 'manager', 'secondary'):
            raise ValueError('Invalid managed role')
        run = store.get(owner)
        target = run if role == 'worker' else run[role]
        if target['agent'] != provider:
            raise ValueError('Provider does not match managed owner')
        if provider != 'hermes' and Path(payload.get('cwd', '')).resolve() != Path(target['workdir']).resolve():
            raise ValueError('Hook cwd differs from owned worktree')
        bound = store.db.execute('SELECT native_id FROM native_sessions WHERE run_id=? AND role=?',
                                 (run['id'], role)).fetchall()
        identities = {row[0] for row in bound} | {target.get('native_session_id')}
        origin = hermes_origin(store.root.parent, native) if provider == 'hermes' else native
        if native not in identities and origin not in identities:
            # Delegates and independent forks can inherit process environment.
            return provider + ':' + origin
        return run['id'] + ':' + role
    if provider == 'hermes':
        origin = hermes_origin(store.root.parent, native)
        row = store.db.execute('SELECT run_id,role FROM native_sessions WHERE native_id IN (?,?)',
                               (native, origin)).fetchall()
        keys = {r[0] + ':' + r[1] for r in row}
        if len(keys) > 1:
            raise ValueError('Ambiguous Hermes owner')
        return next(iter(keys)) if keys else 'hermes:' + origin
    # Unmanaged coding sessions remain independent. Enrolled sessions use stable run keys.
    return provider + ':' + native


def transcript_messages(path, provider, offset):
    """Read only a bounded suffix from the exact native-supplied transcript."""
    allowed = (Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions'
               if provider == 'codex' else Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))) / 'projects')
    path = Path(path).resolve()
    if not path.is_relative_to(allowed.resolve()) or path.is_symlink():
        raise ValueError('Transcript outside provider store')
    with path.open('rb') as stream:
        stream.seek(offset)
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        return []  # Uncertain evidence stays pending; no silent acknowledgment.
    messages = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if provider == 'codex' and item.get('type') == 'response_item':
            message = item.get('payload', {})
        elif provider == 'claude' and item.get('type') in ('user', 'assistant'):
            message = item.get('message', {})
        else:
            continue
        if message.get('role') in ('developer', 'user', 'assistant'):
            messages.append(message)
    return messages


def handle(store, provider, payload, spec):
    brain.initialize(store.db)
    session = session_key(store, provider, payload)
    native = payload['session_id']
    event = payload.get('hook_event_name')
    turn = str(payload.get('turn_id') or 'native-prompt')
    with store.lock():
        task_hooks.activity(store, session, native, turn, event)
        if event in ('Stop', 'post_llm_call'):
            if provider == 'hermes':
                messages = payload.get('conversation_history') or []
            else:
                saved = store.db.execute('SELECT path,offset FROM brain_transcripts WHERE session=? AND native_id=? AND turn_id=?',
                                         (session, native, turn)).fetchone()
                messages = transcript_messages(saved[0], provider, saved[1]) if saved else []
            task_ack = task_hooks.acknowledge(store, session, native, turn, messages, exact_turn=provider != 'hermes')
            return {'acknowledged': brain.acknowledge(store.db, session, native, turn, messages, exact_turn=provider != 'hermes'),
                    'task_acknowledged': task_ack}
        if event not in ('SessionStart', 'UserPromptSubmit', 'pre_llm_call'):
            return None
        task_notice = task_hooks.offer(store, session, native, turn)
        notice = brain.offer(store.db, session, native, turn, brain.scan(store.root.parent, spec))
        if task_notice:
            notice = dict(notice) if notice else {'context': ''}
            notice['context'] += '\n' + task_notice['context']
        if notice and provider != 'hermes' and payload.get('transcript_path'):
            path = Path(payload['transcript_path'])
            offset = path.stat().st_size if path.exists() else 0
            with store.db:
                store.db.execute('INSERT OR REPLACE INTO brain_transcripts VALUES (?,?,?,?,?)',
                                 (session, native, turn, str(path), offset))
        return notice


def main():
    provider = sys.argv[1]
    if provider not in ('codex', 'claude'):
        raise ValueError('Unsupported native provider')
    store = None
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Oversized native hook payload')
        payload = json.loads(raw)
        store = Store()
        result = handle(store, provider, payload, Path(__file__).resolve().parents[1] / 'spec')
        output = {}
        if result and result.get('context'):
            output = {'hookSpecificOutput': {'hookEventName': payload['hook_event_name'],
                                             'additionalContext': result['context']}}
        print(json.dumps(output))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print('Brain notice deferred: ' + type(exc).__name__, file=sys.stderr)
        print('{}')
    finally:
        if store:
            store.db.close()


if __name__ == '__main__':
    main()
