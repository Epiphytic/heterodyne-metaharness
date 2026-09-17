"""Staged native Hermes runtime repair; operational contract: spec/runtime.md."""
import argparse
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

from harness.transcript_cleanup import sync


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, record):
    temp = path.with_suffix('.new')
    with temp.open('w') as stream:
        os.chmod(temp, 0o600)
        json.dump(record, stream, indent=2, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    sync(path.parent)


def inventory(python):
    script = '''import importlib.metadata as m,json,re
result={}
for d in m.distributions():
 name=re.sub(r"[-_.]+","-",d.metadata["Name"]).lower()
 result[name]={"version":d.version,"origin":json.loads(d.read_text("direct_url.json") or "null")}
print(json.dumps(result))'''
    result = subprocess.run([str(python), '-I', '-c', script], check=True,
                            capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


def differences(before, after):
    return {name: {'before': value, 'after': after.get(name)}
            for name, value in before.items() if after.get(name) != value}


def database_backup(source, destination):
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as db:
        with sqlite3.connect(destination) as backup:
            db.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Database backup integrity failure')
            if backup.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Database backup foreign key failure')
    destination.chmod(0o600)
    sync(destination)
    return {'source': str(source), 'path': str(destination), 'sha256': sha(destination)}


def backup_runtime(root, quarantine, info, databases):
    """A failed backup never permits cutover; archive retains symlinks and modes."""
    archive = quarantine / 'runtime.tar'
    with archive.open('xb') as stream:
        os.chmod(archive, 0o600)
        with tarfile.open(fileobj=stream, mode='w') as output:
            output.add(root / 'venv', arcname='venv')
            output.add(info.base_prefix, arcname='base-python')
    archive.chmod(0o600)
    sync(archive)
    # Read every regular member, not only the tar directory, before trusting it.
    with tarfile.open(archive) as saved:
        for member in saved:
            if member.isfile():
                stream = saved.extractfile(member)
                while stream.read(1024 * 1024):
                    pass
                stream.close()
    backups = [database_backup(db, quarantine / f'database-{i}.sqlite3')
               for i, db in enumerate(databases)]
    sync(quarantine)
    return {'runtime': {'path': str(archive), 'sha256': sha(archive)},
            'base_prefix': str(info.base_prefix), 'databases': backups}


def stage(native, root, uv, record_path, reuse_python=None):
    record = json.loads(record_path.read_text()) if record_path.exists() else None
    if record and record['state'] != 'provisioned':
        return record
    live = root / 'venv' / 'bin' / 'python'
    current = native.probe_sqlite_runtime(live)
    if current is None:
        raise ValueError('Cannot probe exact live interpreter')
    if not current.wal_reset_vulnerable:
        return {'state': 'already-safe', 'runtime': asdict(current)}
    from prepare_runtime_packaging import manifest
    if manifest(root)['files']:
        raise ValueError('Native state modules missing from packaging; apply reviewed packaging correction first')
    before = inventory(live)
    if record:
        if record['root'] != str(root):
            raise ValueError('Provisioned record belongs to another installation')
        reuse_python = Path(record['python'])
    if reuse_python:
        python = Path(reuse_python).absolute()
        info = native.probe_sqlite_runtime(python)
        store = native.managed_python_install_dir(root).resolve()
        if info is None or info.wal_reset_vulnerable or not python.resolve().is_relative_to(store):
            raise ValueError('Reuse requires a verified fixed interpreter inside native private store')
        generation = next((parent for parent in python.resolve().parents if parent.parent == store), None)
        if generation is None:
            raise ValueError('Cannot identify private generation')
    else:
        provisioned = native._install_safe_python_generation(uv, project_root=root, current=current)
        if provisioned is None:
            raise ValueError('Native provisioning found no fixed runtime; live venv unchanged')
        generation, python, info = provisioned
    save(record_path, {'state': 'provisioned', 'root': str(root),
                       'generation': str(generation), 'python': str(python)})
    candidate = native._stage_candidate_venv(uv, project_root=root, generation=generation, python=python)
    if candidate is None:
        raise ValueError('Native candidate failed locked dependency/import checks; provisioned runtime retained for retry')
    record = {'state': 'staged', 'root': str(root), 'candidate': str(candidate),
              'before': asdict(current), 'after': asdict(info), 'inventory': before,
              'helper_sha256': sha(root / 'hermes_cli/managed_uv.py'),
              'project_sha256': sha(root / 'pyproject.toml'),
              'lock_sha256': sha(root / 'uv.lock'),
              'package_differences': differences(before, inventory(candidate / 'bin/python'))}
    save(record_path, record)
    return record


def apply(native, root, record_path, databases, quiesced):
    if not quiesced or not databases:
        raise ValueError('Cutover requires quiet-window acknowledgement and explicit databases')
    record = json.loads(record_path.read_text())
    if record['root'] != str(root):
        raise ValueError('Stage belongs to another installation')
    live = root / 'venv' / 'bin/python'
    if record['state'] == 'applied':
        current = native.probe_sqlite_runtime(live)
        if current is None or current.wal_reset_vulnerable:
            raise ValueError('Previously applied runtime has drifted')
        return record
    if record['state'] != 'staged':
        raise ValueError('Interrupted cutover: inspect preserved backup and live venv before recovery')
    for relative, key in [('hermes_cli/managed_uv.py', 'helper_sha256'), ('uv.lock', 'lock_sha256'), ('pyproject.toml', 'project_sha256')]:
        if sha(root / relative) != record[key]:
            raise ValueError('Native helper or dependency lock changed after staging')
    current = native.probe_sqlite_runtime(live)
    if current is None or str(current.base_prefix) != record['before']['base_prefix']:
        raise ValueError('Live runtime changed after staging')
    candidate = Path(record['candidate'])
    if not candidate.resolve().is_relative_to(root / '.hermes-runtime'):
        raise ValueError('Candidate outside native runtime store')
    if inventory(live) != record['inventory']:
        raise ValueError('Live dependencies changed after staging')
    drift = differences(record['inventory'], inventory(candidate / 'bin/python'))
    if drift:
        raise ValueError('Candidate loses/changes existing packages: ' + json.dumps(drift))
    healthy, detail, info = native._smoke_candidate_venv(candidate)
    if not healthy or info is None or info.wal_reset_vulnerable:
        raise ValueError('Candidate smoke/fixed SQLite gate failed: ' + detail)
    record['backups'] = backup_runtime(root, record_path.parent, current, databases)
    if inventory(live) != record['inventory'] or differences(record['inventory'], inventory(candidate / 'bin/python')):
        raise ValueError('Dependencies drifted during backup; cutover refused')
    for relative, key in [('hermes_cli/managed_uv.py', 'helper_sha256'), ('uv.lock', 'lock_sha256'), ('pyproject.toml', 'project_sha256')]:
        if sha(root / relative) != record[key]:
            raise ValueError('Native files drifted during backup; cutover refused')
    record['state'] = 'cutover-intent'
    save(record_path, record)
    success, parked, final, detail = native._cut_over_candidate(candidate, project_root=root, live=root / 'venv')
    record.update(state='applied' if success else 'failed', parked_venv=str(parked),
                  final=asdict(final) if final else None, detail=detail)
    sync(root)
    save(record_path, record)
    if not success:
        raise RuntimeError('Native cutover failed; see saved rollback detail: ' + detail)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['stage', 'apply'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--quarantine', type=Path, required=True)
    parser.add_argument('--uv', required=True)
    parser.add_argument('--reuse-python', type=Path)
    parser.add_argument('--database', type=Path, action='append', default=[])
    parser.add_argument('--quiesced', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    quarantine = args.quarantine.resolve()
    home = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes'))).resolve()
    if quarantine.is_relative_to(home) or quarantine.is_relative_to(root) or any((parent / '.git').exists() for parent in [quarantine, *quarantine.parents]):
        raise ValueError('Use private quarantine outside Hermes/project/brain indexes')
    quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
    quarantine.chmod(0o700)
    sys.path.insert(0, str(root))
    native = importlib.import_module('hermes_cli.managed_uv')
    if Path(native.__file__).resolve() != root / 'hermes_cli/managed_uv.py':
        raise ValueError('Wrong native helper imported')
    lock = native._acquire_repair_lock(root / '.hermes-runtime')
    if lock is None:
        raise RuntimeError('Another native runtime repair holds the lock')
    try:
        record_path = quarantine / 'repair.json'
        if args.action == 'stage':
            result = stage(native, root, args.uv, record_path, args.reuse_python)
        else:
            result = apply(native, root, record_path, [p.resolve() for p in args.database], args.quiesced)
        print(json.dumps(result, indent=2, default=str))
    finally:
        native._release_repair_lock(lock)


if __name__ == '__main__':
    main()
