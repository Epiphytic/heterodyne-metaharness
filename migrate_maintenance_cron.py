#!/usr/bin/env python3
"""Retire duplicate supervision through native cron API. Contract: spec/sessions.md."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def migrate(home, backup):
    os.environ['HERMES_HOME']=str(home)
    sys.path.insert(0,str(home/'hermes-agent'))
    from cron.jobs import get_job, pause_job, update_job
    ids=('2538a41f4ea2','71d6d41bb520','56e8145d8d4d')
    records={sid:get_job(sid) for sid in ids}
    backup.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=backup/'cron.before.json'
    if not path.exists():
        path.write_text(json.dumps(records,indent=2));path.chmod(0o600)
        with path.open('rb') as handle: os.fsync(handle.fileno())
        fd=os.open(backup,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    for sid in ('2538a41f4ea2','56e8145d8d4d'):
        if records[sid] and records[sid].get('enabled',True):
            pause_job(sid,reason='Retired duplicate workstream supervision; durable harness owns reporting and routing.')
    prompt=('Read-only failure monitor. Inspect workstream doctor and systemctl --user --failed. '
            'Report actionable failures to the configured OPS destination. Managed coding sessions are owned by '
            'the durable harness; never steer tmux, restart/replay jobs or duplicate progress relays. '
            'Route significant Hermes fixes with workstream maintenance --title TITLE --file TASK --key REQUEST_ID. '
            'Contract: /home/operator/repos/hermes-workstream-harness/spec/sessions.md.')
    if records['71d6d41bb520'] and records['71d6d41bb520'].get('prompt')!=prompt:
        update_job('71d6d41bb520',{'prompt':prompt})
    return {'retired':['2538a41f4ea2','56e8145d8d4d'],'updated':['71d6d41bb520'],'backup_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--home',type=Path,required=True);p.add_argument('--backup',type=Path,required=True)
    a=p.parse_args();print(json.dumps(migrate(a.home,a.backup)))
