"""Map native pickup hooks to stable harness ownership, retaining unmanaged hooks."""
import json
import os
from pathlib import Path
import subprocess
import sys

from .beads import Beads
from .store import Store, home_path

MAX_PAYLOAD = 1024 * 1024
DEFAULT_HOOK = '/home/operator/repos/beads-task-queue/bin/pickup-hook'


def context_response(context):
    return {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': context}}


def managed_pickup(event, run, beads, payload):
    state = run.get('beads', {})
    if payload.get('source') == 'compact':
        return context_response(beads.context(run))
    if state.get('recovery_required') or run.get('resume_required') or not state.get('pickup_enabled', False):
        beads.pause(run)
        return {} if event == 'Stop' else context_response(beads.context(run))
    if state.get('issue_id') and beads.show(run, state['issue_id']).get('status') != 'closed':
        return {} if event == 'Stop' else context_response(beads.context(run))
    if payload.get('stop_hook_active') or payload.get('permission_mode') == 'plan':
        return {}
    if event == 'Stop':
        queue = beads._queue(run)
        current = queue.state / 'current'
        if not current.exists():
            return {}
        previous = queue.show(current.read_text().strip())
        if previous.get('status') != 'closed' or previous.get('assignee') != queue.worker:
            return {}
        current.unlink()
    candidates = beads.ready(run)
    message = beads.context(run) + ' Eligible bead IDs: ' + json.dumps([item['id'] for item in candidates])
    if event == 'Stop':
        return {'decision': 'block', 'reason': message} if candidates else {}
    return context_response(message)


def invoke(event, agent, payload, *, env=None, store=None, config=None):
    env = dict(os.environ if env is None else env)
    owner = env.get('HERMES_WORKSTREAM_RUN')
    settings = (config or {}).get('beads', {})
    if not owner:
        original = settings.get('pickup_hook', DEFAULT_HOOK)
        result = subprocess.run([original, event, agent], input=json.dumps(payload), env=env,
                                text=True, capture_output=True, timeout=30, check=False)
        if result.returncode:
            raise RuntimeError('Original shared queue hook failed; queue work remains paused')
        return json.loads(result.stdout or '{}')
    if store is None:
        raise ValueError('Managed pickup requires harness state')
    if env.get('HERMES_WORKSTREAM_ROLE', 'worker') != 'worker' or owner.endswith('-manager'):
        return {}
    run = store.get(owner)
    if run['agent'] != agent:
        raise ValueError('Queue hook agent does not match managed worker')
    if Path(payload.get('cwd', '')).resolve() != Path(run['workdir']).resolve():
        raise ValueError('Queue hook cwd does not match managed worker')
    beads = Beads(settings)
    if not beads.enabled:
        return {}
    return managed_pickup(event, run, beads, payload)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ('SessionStart', 'Stop') or argv[1] not in ('codex', 'claude'):
        raise ValueError('Expected SessionStart|Stop codex|claude')
    store = None
    try:
        raw = sys.stdin.buffer.read(MAX_PAYLOAD + 1)
        if len(raw) > MAX_PAYLOAD:
            raise ValueError('Queue hook payload exceeds limit')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('Queue hook payload must be an object')
        config_file = home_path() / 'workstreams' / 'harness-config.json'
        config = json.loads(config_file.read_text()) if config_file.exists() else {}
        if os.environ.get('HERMES_WORKSTREAM_RUN'):
            store = Store()
        print(json.dumps(invoke(*argv, payload, store=store, config=config)))
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        print(json.dumps({'systemMessage': 'Shared queue unavailable or identity rejected; do not claim or execute queue work.'}))
        return 0
    finally:
        if store:
            store.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
