#!/usr/bin/env python3
"""Extend explicit brain sync inputs without running Git/publication. spec/context.md."""
import argparse
from pathlib import Path
import hashlib
from harness.cleanup import apply

BLOCK='''# BEGIN HERMES MAINTENANCE MIRROR
# SOUL is a personality snapshot; the specification remains in its canonical repo.
cp "$SRC/SOUL.md" "$DST/SOUL.md"
cat > "$DST/HERMES-SPEC.md" <<'SPEC_POINTER'
# Hermes specification pointer

Active authority: /home/operator/repos/hermes-workstream-harness/spec/README.md
This brain repository holds snapshots, not a second normative specification.
Private transcript quarantine and native database backups must never be mirrored.
SPEC_POINTER
# END HERMES MAINTENANCE MIRROR
'''


def install(brain, backup):
    brain=Path(brain).resolve(); backup=Path(backup).resolve()
    if backup.is_relative_to(brain) or backup.is_relative_to(Path.home()/'.hermes'):
        raise ValueError('Brain backup must be outside brain and active Hermes trees')
    path=brain/'sync-brain.sh';text=path.read_text()
    if BLOCK in text: return False
    if '# BEGIN HERMES MAINTENANCE MIRROR' in text: raise ValueError('Existing incompatible mirror block')
    anchor='# Abort the sync if a present memory store cannot be backed up safely.'
    if text.count(anchor)!=1: raise ValueError('Unknown sync script; inspect before editing')
    entry={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
           'mode':path.stat().st_mode & 0o777,'replacement':text.replace(anchor,BLOCK+'\n'+anchor)}
    apply({'files':[entry]},backup)
    return True


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--brain',type=Path,required=True)
    p.add_argument('--backup',type=Path,required=True)
    args=p.parse_args();print(install(args.brain,args.backup))
