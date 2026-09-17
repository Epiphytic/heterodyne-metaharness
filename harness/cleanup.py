"""Reviewed file cleanup manifests implementing spec/context.md. No transcript deletion."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import re


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(manifest, quarantine):
    """Exact file allowlist; verified backups precede all mutations. Restart-safe."""
    quarantine = Path(quarantine).resolve()
    entries = manifest['files']
    for entry in entries:
        source = Path(entry['path'])
        if not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
            raise ValueError('Invalid backup hash')
        if not source.is_absolute() or source.is_symlink():
            raise ValueError('Only absolute regular paths accepted')
        if quarantine.is_relative_to(source.parent):
            raise ValueError('Quarantine must be outside source directories')
        destination = quarantine / entry['sha256']
        desired = entry.get('replacement')
        if source.exists() and desired is not None and source.read_text() == desired:
            if not destination.exists() or digest(destination) != entry['sha256']:
                raise ValueError('Applied content lacks verified backup')
            continue
        if not source.exists():
            if desired is None and destination.is_file() and digest(destination) == entry['sha256']:
                continue
            raise ValueError('Missing source without verified completed quarantine')
        if digest(source) != entry['sha256']:
            raise ValueError('Source drift: ' + str(source))
    quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
    quarantine.chmod(0o700)
    # Backup ALL originals before changing any source.
    for entry in entries:
        source = Path(entry['path']); destination = quarantine / entry['sha256']
        if not destination.exists():
            shutil.copy2(source, destination); destination.chmod(0o600)
        if digest(destination) != entry['sha256']:
            raise ValueError('Backup verification failed')
        with destination.open('rb') as handle: os.fsync(handle.fileno())
    record = quarantine / 'manifest.json'
    encoded = json.dumps(manifest, sort_keys=True, indent=2)
    if record.exists() and record.read_text() != encoded:
        raise ValueError('Quarantine already belongs to another manifest')
    record.write_text(encoded); record.chmod(0o600)
    with record.open('rb') as handle: os.fsync(handle.fileno())
    fd = os.open(quarantine, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
    changed = sum(mutate(entry) for entry in entries)
    return {'changed': changed, 'verified_backups': len(entries), 'manifest': str(record)}

def mutate(entry):
    changed = 0
    source = Path(entry['path']); desired = entry.get('replacement')
    if source.exists() and not (desired is not None and source.read_text() == desired) and digest(source) != entry['sha256']:
        raise ValueError('Source changed after backup: ' + str(source))
    if desired is None:
        if source.exists(): source.unlink(); changed += 1
    elif source.read_text() != desired:
        temp = source.with_name(source.name + '.maintenance-new')
        temp.write_text(desired); temp.chmod(source.stat().st_mode & 0o777)
        with temp.open('rb') as handle: os.fsync(handle.fileno())
        # Recheck immediately before atomic publication. Caller must quiesce writers.
        if digest(source) != entry['sha256']: raise ValueError('Source drift before replace')
        temp.replace(source); changed += 1
    fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return changed


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--quarantine', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(apply(json.loads(a.manifest.read_text()), a.quarantine)))
