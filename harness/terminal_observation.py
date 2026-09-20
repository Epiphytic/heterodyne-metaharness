"""Bounded durable screen evidence; authority: spec/terminal-observation.md."""
import hashlib

MAX_BYTES = 1024 * 1024


def retain(run, info, now):
    """Compare exact captures, independently of normalized status heuristics.

    No change means no observed buffer change, not proof of process inactivity.
    A redraw between captures or output beyond retained history is unknowable.
    """
    raw = info['text'].encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    identity = [info['pane_id'], run.get('native_session_id'), run.get('launched_at')]
    prior = run.get('terminal_buffer', {})
    comparable = prior.get('identity') == identity
    changed = prior.get('sha256') != digest if comparable else None
    run['terminal_buffer'] = {
        'identity': identity, 'sha256': digest, 'observed_at': now,
        'changed': changed, 'bytes': len(raw), 'truncated': len(raw) > MAX_BYTES,
        'text': raw[-MAX_BYTES:].decode('utf-8', errors='replace'),
        'last_changed_at': now if changed is not False else prior['last_changed_at'],
    }
    return changed
