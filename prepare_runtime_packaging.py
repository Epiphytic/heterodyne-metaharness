"""Prepare reviewed native packaging correction under spec/runtime.md."""
import argparse
import json
from pathlib import Path
import tomllib
from harness.cleanup import digest

MODULES = ('hermes_state_dbfile', 'hermes_state_errors', 'hermes_state_holders',
           'hermes_state_registry', 'hermes_state_repair', 'hermes_state_sessions',
           'hermes_state_wal')


def manifest(root):
    path = root / 'pyproject.toml'
    original = path.read_text()
    declared = tomllib.loads(original)['tool']['setuptools']['py-modules']
    missing = [name for name in MODULES if name not in declared]
    if not missing:
        return {'files': []}
    if any(not (root / (name + '.py')).is_file() for name in missing):
        raise ValueError('Expected native module missing; refuse packaging edit')
    anchor = '  "hermes_state",\n'
    if original.count(anchor) != 1:
        raise ValueError('Packaging anchor changed; review required')
    replacement = original.replace(anchor, anchor + ''.join(f'  "{name}",\n' for name in missing))
    if not set(MODULES).issubset(tomllib.loads(replacement)['tool']['setuptools']['py-modules']):
        raise ValueError('Packaging correction invalid')
    return {'files': [{'path': str(path.resolve()), 'sha256': digest(path),
                       'mode': path.stat().st_mode & 0o777, 'replacement': replacement}]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(manifest(args.root), indent=2) + '\n')
