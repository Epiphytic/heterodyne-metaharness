"""Single timer poll, with injectable adapters and awaited socket delivery."""
import argparse
import asyncio
import fcntl
import importlib
import json
from pathlib import Path
import sys
import time

from .adapters import Collection
from .samples import THRESHOLDS, number
from .state import State, identity


def adapter(name):
    target = {'codex': 'harness.usage.adapters:codex', 'zai': 'harness.usage.adapters:zai'}.get(name, name)
    module, symbol = target.split(':', 1)
    return getattr(importlib.import_module(module), symbol)


def thresholds(config):
    values = config.get('thresholds', THRESHOLDS)
    if not values or len(set(values)) != len(values):
        raise ValueError('thresholds must be nonempty and unique')
    if any(not 0 < number(v) <= 100 for v in values):
        raise ValueError('thresholds must be in (0,100]')
    return sorted(values)


async def collect(sources, state, now, levels, resolve):
    errors = []
    for name, source in sources.items():
        try:
            if not source.get('enabled', False):
                continue
            result = await resolve(source['adapter'])(name, source, now, state.root)
            if not isinstance(result, Collection):
                raise ValueError('adapter must return Collection')
            errors.extend(name + ': ' + error for error in result.errors)
            if not result.samples and not result.errors:
                raise ValueError('adapter returned no observations')
            for sample in result.samples:
                if sample.source != name:
                    raise ValueError('adapter returned mismatched source')
                state.record({'sample': sample.data()}, now)
                state.admit(sample, levels)
        except Exception as exc:
            errors.append(name + ': ' + type(exc).__name__ + ': ' + str(exc))
    return errors


async def poll(config, state, sender, now, resolve=adapter):
    errors = []
    try:
        levels = thresholds(config)
        sources = config['sources']
        if not isinstance(sources, dict) or not sources:
            raise ValueError('sources must be a nonempty mapping')
    except Exception as exc:
        sources = {}
        errors.append('configuration: ' + type(exc).__name__ + ': ' + str(exc))
    errors.extend(await collect(sources, state, now, levels if sources else [], resolve))
    for error in errors:
        state.record({'error': error}, now)
        # Once per poll epoch, frozen for retry; repeated failures remain visible.
        state.enqueue('usage-error:' + identity([int(now // 600), error]),
                      '[waiting-on-agent]\nUsage monitor: ' + error)
    state.health(errors, now)
    try:
        await state.deliver(sender, now)
    except Exception as exc:
        error = 'delivery: ' + type(exc).__name__
        errors.append(error)
        state.record({'error': error}, now)
        state.enqueue('usage-error:' + identity([int(now // 600), error]),
                      '[waiting-on-agent]\nUsage monitor: ' + error)
        state.health(errors, now)
    state.retain(now)
    return 1 if errors else 0


class SocketSender:
    def __init__(self, config):
        self.config = config
        self.client = None

    async def __call__(self, text, key):
        if self.client is None:
            config = self.config
            # Host explicitly supplies the installed plugin import root/module.
            if config.get('client_path'):
                sys.path.insert(0, str(Path(config['client_path']).expanduser()))
            module = importlib.import_module(config.get('client_module', 'marmot.adapter'))
            token = None
            if config.get('auth_token_file'):
                token = Path(config['auth_token_file']).expanduser().read_text().strip()
            self.client = module.MarmotAgentControlClient(config['socket'], auth_token=token)
        response = await asyncio.wait_for(self.client.send_final(
            self.config['account_id'], self.config['ops_group'], text=text,
            idempotency_key=key), timeout=35)
        if response.get('type') != 'final_sent' or not response.get('message_ids_hex'):
            raise ValueError('socket did not acknowledge final_sent with message IDs')


def load_config(path):
    text = path.read_text()
    if path.suffix == '.json':
        data = json.loads(text)
    else:
        import yaml  # Existing host config dependency; core remains stdlib.
        data = yaml.safe_load(text)
    return data['usage_monitor']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('~/.hermes/config.yaml').expanduser())
    parser.add_argument('--state', type=Path, default=Path('~/.hermes/workstreams/usage-monitor').expanduser())
    args = parser.parse_args()
    args.state.mkdir(parents=True, exist_ok=True)
    with (args.state / 'poll.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = State(args.state)
        now = time.time()
        try:
            config = load_config(args.config)
            return asyncio.run(poll(config, state, SocketSender(config), now))
        except Exception as exc:
            # Unreadable config may make the destination unknowable. Preserve it
            # for the operator and next run, with nonzero exit/journal evidence.
            error = 'monitor startup: ' + type(exc).__name__
            state.record({'error': error}, now)
            state.health([error], now)
            state.enqueue('usage-error:' + identity([int(now // 600), error]),
                          '[waiting-on-agent]\nUsage monitor: ' + error)
            print(error, file=sys.stderr)
            return 1
        finally:
            state.db.close()


if __name__ == '__main__':
    sys.exit(main())
