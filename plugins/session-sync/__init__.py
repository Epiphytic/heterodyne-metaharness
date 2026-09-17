"""Shared brain adapter. Contract: spec/brain.md."""
import json
from pathlib import Path
import sys


def _event(event, payload):
    # Installer pins an absolute canonical package root; never resolve through cwd.
    root = Path(json.loads(Path(__file__).with_name('runtime.json').read_text())['package_root'])
    if not root.is_absolute() or not (root / 'harness/brain_hooks.py').is_file():
        raise ValueError('Canonical brain runtime unavailable')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from harness.store import Store
    from harness.brain_hooks import handle
    store = Store()
    try:
        payload['hook_event_name'] = event
        result = handle(store, 'hermes', payload, root / 'spec')
        if event == 'pre_llm_call' and result:
            return {'context': result['context']}
    finally:
        store.db.close()


def pre_llm_call(**payload):
    return _event('pre_llm_call', payload)


def post_llm_call(**payload):
    _event('post_llm_call', payload)


def register(ctx):
    ctx.register_hook('pre_llm_call', pre_llm_call)
    ctx.register_hook('post_llm_call', post_llm_call)
