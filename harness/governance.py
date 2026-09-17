"""Validate specification and historical lifecycle; not runtime policy loading."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def validate(root):
    root = Path(root).resolve()
    errors = []
    if not (root / 'spec/README.md').is_file():
        errors.append('missing spec/README.md entrypoint')
    for path in (root / 'spec').rglob('*.md'):
        text = path.read_text()
        if not text.strip(): errors.append(f'{path.name}: empty spec')
        if len(text.encode()) > 8000:
            errors.append(f'{path.name}: spec exceeds 8000 bytes')
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
            if '://' in link:
                continue
            filename, _, anchor = link.partition('#')
            target = (path.parent / filename).resolve() if filename else path.resolve()
            if not target.is_relative_to(root / 'spec') or not target.is_file():
                errors.append(f'{path.name}: invalid spec link {link}')
            elif anchor:
                anchors = {re.sub(r'[^\w -]', '', heading.lower()).replace(' ', '-') for heading in re.findall(r'^#+ (.+)$', target.read_text(), re.M)}
                if anchor not in anchors: errors.append(f'{path.name}: missing anchor {link}')
    errors.extend(validate_direction(root))
    for path in (root / 'governance/decisions').glob('*.json'):
        record = json.loads(path.read_text())
        errors.extend(validate_history(root, path, record))
        proposal_digest = hashlib.sha256(record.get('proposal', '').encode()).hexdigest()
        events = record.get('events', [])
        if not events or events[0].get('state') != 'proposed':
            errors.append(f'{path.name}: first event must propose'); continue
        previous = ''
        approved = False
        for event in events:
            stamp = event.get('at', '')
            if not stamp or stamp < previous:
                errors.append(f'{path.name}: events must be chronological')
            previous = stamp
            state = event.get('state')
            if state not in ('proposed', 'approved', 'completed', 'revoked', 'superseded'):
                errors.append(f'{path.name}: invalid lifecycle state')
            if state == 'approved':
                approved = bool(event.get('by') and event.get('evidence') and event.get('proposal_sha256') == proposal_digest)
                if not approved: errors.append(f'{path.name}: approval lacks evidence')
            if state in ('revoked', 'superseded'):
                approved = False
                if not event.get('evidence'): errors.append(f'{path.name}: missing history evidence')
            if state == 'completed':
                if not approved: errors.append(f'{path.name}: completion without approval')
                errors.extend(validate_artifacts(root, path, event))
    return errors


def validate_direction(root):
    errors = []
    sources = [*root.glob('*.py')]
    for directory in ('harness', 'policy', 'plugins', 'bin'):
        sources.extend(path for path in (root / directory).rglob('*') if path.is_file()
                       and (path.suffix in ('.py', '.md', '.sh') or not path.suffix))
    for path in sources:
        if path == root / 'harness/governance.py': continue
        if re.search(r'(?:governance/decisions|docs/adrs|decisions/)', path.read_text()):
            errors.append(f'{path.relative_to(root)}: runtime historical reference')
    return errors

def validate_history(root, path, record):
    errors = []
    baseline = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:' + str(path.relative_to(root))], capture_output=True)
    if baseline.returncode == 0:
        old = json.loads(baseline.stdout)
        if any(e.get('state') == 'approved' for e in old.get('events', [])):
            if old.get('proposal') != record.get('proposal') or record.get('events', [])[:len(old['events'])] != old['events']:
                errors.append(f'{path.name}: approved history is append-only')
    return errors

def validate_artifacts(root, path, event):
    errors = []
    for field in ('spec', 'implementation', 'deployment', 'dependencies', 'verification'):
        artifacts = event.get(field, [])
        if not artifacts: errors.append(f'{path.name}: missing {field}')
        for item in artifacts:
            revision, name = item.get('commit', ''), item.get('path', '')
            if not re.fullmatch(r'[0-9a-f]{40}', revision) or not name or name.startswith('/') or '..' in Path(name).parts:
                errors.append(f'{path.name}: artifact requires immutable Git revision'); continue
            result = subprocess.run(['git', '-C', str(root), 'show', revision + ':' + name], capture_output=True)
            if result.returncode or hashlib.sha256(result.stdout).hexdigest() != item.get('sha256'):
                errors.append(f'{path.name}: unverified {field} artifact')
            if field in ('deployment', 'dependencies') and item.get('kind') != 'execution-evidence':
                errors.append(f'{path.name}: {field} requires execution evidence, not installer source')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = validate(args.root)
    print(json.dumps({'valid': not errors, 'errors': errors}))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
