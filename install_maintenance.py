#!/usr/bin/env python3
"""Install reviewed maintenance binding and policy. Contract: spec/maintenance.md."""
import argparse
import json
from pathlib import Path
import yaml
from install import replace, ROOT
from harness.store import Store


def deploy(home):
    store = Store(home)
    with store.lock():
        run = store.get('hermes-maintenance')
        if run['id'] != 'b61d651b-8206-52d9-8965-d30d937dd103' or run['agent'] != 'codex' or not run.get('native_session_id'):
            raise ValueError('Existing verified maintenance fork required')
        if run['state'] in ('stopped', 'archived', 'completed'):
            raise ValueError('Inspect terminated run before installing binding')
        run['persistent'] = True
        with store.db: store.save(run)
        store.checkpoint(run)
        path = home / 'workstreams/harness-config.json'
        config = json.loads(path.read_text())
        config['maintenance'] = {'run_id': run['id'], 'group_id': run['group_id']}
        replace(path, json.dumps(config, indent=2)+'\n', 0o600)
    path = home / 'AGENTS.md'
    previous = path.read_text() if path.exists() else ''
    begin, end = '<!-- BEGIN HERMES MAINTENANCE -->', '<!-- END HERMES MAINTENANCE -->'
    block = begin + '\n' + (ROOT / 'policy/hermes-AGENTS.md').read_text() + end
    if begin in previous:
        if previous.count(begin) != 1 or previous.count(end) != 1: raise ValueError('Ambiguous managed AGENTS block')
        prefix, rest = previous.split(begin); _, suffix = rest.split(end)
        content = prefix + block + suffix
    else:
        content = previous.rstrip() + '\n\n' + block + '\n'
    replace(path, content, 0o644)
    for name in ('plugin.yaml', '__init__.py'):
        replace(home / 'plugins/hermes-maintenance' / name,
                (ROOT / 'plugins/hermes-maintenance' / name).read_text(), 0o644)
    path = home / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    enabled = config.setdefault('plugins', {}).setdefault('enabled', [])
    if not isinstance(enabled, list): raise ValueError('Unexpected plugins.enabled schema')
    if 'hermes-maintenance' not in enabled:
        enabled.append('hermes-maintenance')
        replace(path, yaml.safe_dump(config, sort_keys=False), 0o600)
    return {'persistent': run['id'], 'plugin_restart_required': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    print(json.dumps(deploy(parser.parse_args().home)))
