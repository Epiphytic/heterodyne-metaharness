"""Detached historical session purge using native deletion. Contract: spec/context.md.

Run in an operator-controlled quiet window. External registry/harness writers must
be quiesced; native DB reference/fingerprint checks run INSIDE deletion transaction.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from .cleanup import apply as apply_files

CANDIDATES = {
    '20260902_182917_59e984': 'Obsolete migration operational advice; current migration facts retained in active context.',
    '20260903_151858_597526': 'Superseded harness instructions and blanket propose-before-spawn guidance.'}


def fingerprint(conn, sid):
    session = conn.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
    messages = conn.execute('SELECT * FROM messages WHERE session_id=? ORDER BY id', (sid,)).fetchall()
    raw = json.dumps([tuple(session) if session else None, [tuple(row) for row in messages]], default=str).encode()
    return hashlib.sha256(raw).hexdigest(), len(messages)


def detached(conn, sid):
    row = conn.execute('SELECT ended_at,parent_session_id FROM sessions WHERE id=?', (sid,)).fetchone()
    if row is None: return False
    if not row[0] or row[1]: raise ValueError('Session is active or has parent: ' + sid)
    if conn.execute('SELECT 1 FROM sessions WHERE parent_session_id=?', (sid,)).fetchone():
        raise ValueError('Session has children: ' + sid)
    check_native_references(conn, sid)
    return True


def references(entry, sid, fields):
    """Match typed identity fields exactly; free-form content is never a binding."""
    return isinstance(entry, dict) and any(entry.get(field) == sid for field in fields)


def check_native_references(conn, sid):
    columns = {name: {row[1] for row in conn.execute('PRAGMA table_info("' + name + '")')}
               for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
               if name in NATIVE_REFERENCES or name in ('sessions', 'gateway_routing')}
    keys = {sid}
    if 'session_key' in columns.get('sessions', set()):
        row = conn.execute('SELECT session_key FROM sessions WHERE id=?', (sid,)).fetchone()
        if row and row[0]: keys.add(row[0])
    for table, fields in NATIVE_REFERENCES.items():
        for field in fields:
            if field not in columns.get(table, set()): continue
            for (value,) in conn.execute(f'SELECT "{field}" FROM "{table}"'):
                if value in keys: raise ValueError('Operational reference in ' + table + '.' + field)
    if 'entry_json' in columns.get('gateway_routing', set()):
        for (encoded,) in conn.execute('SELECT entry_json FROM gateway_routing'):
            if references(json.loads(encoded), sid, ROUTING_FIELDS):
                raise ValueError('Gateway routing reference')


# Installed Hermes native schema: identity/ownership columns only. Descriptive
# content, result_json and task_json deliberately do not participate.
NATIVE_REFERENCES = {
    'async_delegations': ('origin_session', 'origin_ui_session_id', 'parent_session_id', 'origin_session_id'),
    'compression_locks': ('session_id',),
    'session_turn_leases': ('conversation_id',),
    'delivery_obligations': ('session_key',),
    'hosted_room_remote_runs': ('session_id',),
}
ROUTING_FIELDS = ('session_id', 'prev_session_id')


def external_check(home, sid):
    registry = home/'sessions/sessions.json'
    if registry.exists():
        entries = json.loads(registry.read_text())
        if not isinstance(entries, dict): raise ValueError('Unexpected session registry schema')
        if any(references(entry, sid, ROUTING_FIELDS) for entry in entries.values()):
            raise ValueError('Gateway registry reference')
    path = home/'workstreams/harness.sqlite3'
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as conn:
        for (encoded,) in conn.execute('SELECT data FROM runs'):
            run = json.loads(encoded)
            if (references(run, sid, ('native_session_id', 'parent_hermes_session_id'))
                    or references(run.get('manager'), sid, ('native_session_id', 'native_id'))):
                raise ValueError('Harness run identity reference')
        for table, fields in (('native_sessions', ('native_id', 'previous_id')), ('identities', ('native_id',))):
            for field in fields:
                if conn.execute(f'SELECT 1 FROM {table} WHERE {field}=? LIMIT 1', (sid,)).fetchone():
                    raise ValueError('Harness identity reference: ' + table + '.' + field)


def sync(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def purge(home, quarantine, execute=False):
    home=Path(home).resolve(); quarantine=Path(quarantine).resolve()
    if quarantine.is_relative_to(home) or quarantine.is_relative_to(Path('/home/operator/belthanior-hermes')):
        raise ValueError('Quarantine must be outside active Hermes and brain stores')
    quarantine.mkdir(parents=True, exist_ok=True, mode=0o700); quarantine.chmod(0o700)
    database=home/'state.db'; backup=quarantine/'state.before.sqlite3'
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as source:
        if not backup.exists():
            with sqlite3.connect(backup) as dest: source.backup(dest)
            backup.chmod(0o600); sync(backup); sync(quarantine)
        with sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True) as saved:
            if saved.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Bad backup')
            selected=[]
            for sid, reason in CANDIDATES.items():
                if not detached(source,sid): continue
                external_check(home,sid)
                current,count=fingerprint(source,sid)
                if fingerprint(saved,sid)[0]!=current: raise ValueError('Backup differs from current target')
                selected.append({'id':sid,'sha256':current,'messages':count,'reason':reason})
    # Capture exact raw files even on retry after native deletion.
    file_plan = quarantine/'transcript-files.json'
    if not file_plan.exists():
        files=[]
        for sid in CANDIDATES:
            paths=[home/'sessions'/f'{sid}.json',home/'sessions'/f'{sid}.jsonl',*(home/'sessions').glob(f'request_dump_{sid}_*.json')]
            for path in paths:
                if path.is_file(): files.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mode':path.stat().st_mode & 0o777})
        file_plan.write_text(json.dumps({'files':files},indent=2));file_plan.chmod(0o600);sync(file_plan);sync(quarantine)
    manifest=quarantine/'purge-plan.json'
    if not manifest.exists():
        manifest.write_text(json.dumps({'backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),'sessions':selected},indent=2))
    manifest.chmod(0o600);sync(manifest);sync(quarantine)
    if not execute: return {'plan':str(manifest),'sessions':selected,'deleted':0}
    sys.path.insert(0,str(home/'hermes-agent'))
    from hermes_state import SessionDB
    # Preserve external association stores with consistent DB snapshots.
    for name, source_path in (('harness.before.sqlite3',home/'workstreams/harness.sqlite3'),):
        target=quarantine/name
        if not target.exists():
            with sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(target) as dst: src.backup(dst)
            target.chmod(0o600);sync(target);sync(quarantine)
    registry=home/'sessions/sessions.json';target=quarantine/'registry.before.json'
    if registry.exists() and not target.exists():
        target.write_bytes(registry.read_bytes());target.chmod(0o600);sync(target);sync(quarantine)
    native=SessionDB(db_path=database)
    original=native._execute_write
    for entry in selected:
        sid=entry['id'];external_check(home,sid)
        def guarded(callback, *args, **kwargs):
            def checked(conn):
                if not detached(conn,sid) or fingerprint(conn,sid)[0]!=entry['sha256']:
                    raise ValueError('Target changed since backup')
                return callback(conn)
            return original(checked,*args,**kwargs)
        native._execute_write=guarded
        # Keep raw session files: root quarantines them after verified DB removal,
        # avoiding native file deletion before a verified file backup exists.
        if not native.delete_session(sid, expected_delete_ids=[sid]): raise ValueError('Native deletion refused')
    native._execute_write=original
    with sqlite3.connect(database) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Post-purge integrity failure; retain backup and stop')
    # Native DB no longer references these detached sessions. Quarantine raw files
    # with the same verified, restart-safe file manifest protocol.
    file_result=apply_files(json.loads(file_plan.read_text()),quarantine/'raw-files')
    return {'raw_files':file_result,'deleted':len(selected),'messages':sum(row['messages'] for row in selected),'backup':str(backup)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home',type=Path,required=True);p.add_argument('--quarantine',type=Path,required=True)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args();print(json.dumps(purge(a.home,a.quarantine,a.execute)))
