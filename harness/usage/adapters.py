"""Read-only provider adapters. Never read transcript text into persisted evidence."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import signal
from pathlib import Path

from .samples import INTERVALS, Sample, number, window


@dataclass
class Collection:
    samples: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def tail_lines(path, size=8 * 1024 * 1024):
    """Newest bounded portion only; incomplete final writes are handled by parser."""
    with path.open('rb') as stream:
        stream.seek(0, 2)
        offset = max(0, stream.tell() - size)
        stream.seek(offset)
        if offset:
            stream.readline()  # May begin inside UTF-8 or JSON; discard fragment.
        return stream.read().decode('utf-8').splitlines(keepends=True)


def daily_models(data):
    rows = data.get('daily')
    if not isinstance(rows, list):
        raise ValueError('ccusage output requires daily array')
    result = []
    for row in rows:
        date = datetime.strptime(row['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        models = row.get('models')
        if isinstance(models, dict):
            pairs = models.items()
        else:
            pairs = [(m['modelName'], m) for m in row['modelBreakdowns']]
        for model, counts in pairs:
            # Codex totalTokens already includes cached input; do not add twice.
            tokens = counts.get('totalTokens')
            if tokens is None:
                tokens = sum(number(counts.get(k, 0)) for k in
                             ('inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheCreationTokens'))
            result.append((date.timestamp(), model, number(tokens)))
    return result


def estimates(source, config, now, data, intervals=INTERVALS):
    rows = daily_models(data)
    models = config.get('models') or sorted({r[1] for r in rows})
    if not models:
        raise ValueError('no configured or observed models')
    result = []
    for interval in intervals:
        ceiling = number(config.get('quota_ceiling', {}).get(interval, 0))
        if not ceiling:
            raise ValueError('missing positive quota_ceiling for ' + interval)
        start, end = window(interval, now)
        for model in models:
            total = sum(tokens for day, name, tokens in rows
                        if name == model and day < min(end, now + 1) and day + 86400 > start)
            method = 'daily-bucket upper bound' if interval == '5h' else 'configured token estimate'
            result.append(Sample(source, model, interval, start, end,
                                 total / ceiling * 100, method, now))
    return result


async def ccusage(provider, root, timeout=180):
    env = dict(os.environ, TZ='UTC', npm_config_cache=str(root / 'npm-cache'))
    process = await asyncio.create_subprocess_exec(
        'npx', '--yes', 'ccusage@latest', provider, 'daily', '--json',
        env=env, start_new_session=True, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
        await process.communicate()
        raise
    if process.returncode:
        raise ValueError('ccusage ' + provider + ' failed, exit ' + str(process.returncode))
    return json.loads(stdout)


def quota_record(line, now, max_age):
    try:
        event = json.loads(line)
    except ValueError:
        if not line.endswith('\n'):
            return None  # Writer's incomplete final record.
        raise ValueError('malformed complete Codex JSONL record')
    payload = event.get('payload', {})
    if payload.get('type') != 'token_count' or not payload.get('rate_limits'):
        return None
    observed = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')).timestamp()
    if not now - max_age <= observed <= now:
        return None
    limits = payload['rate_limits']
    return limits.get('limit_id') or 'codex', observed, limits


def quota_window(source, identity, value, observed, now):
    if value is None:
        return None
    minutes = number(value['window_minutes'])
    interval = {300: '5h', 1440: 'daily', 10080: 'weekly', 43200: 'monthly'}.get(minutes)
    if interval is None:
        raise ValueError('unsupported Codex quota duration: ' + str(minutes))
    end = int(number(value['resets_at']))
    if end <= now:
        return None
    return Sample(source, 'quota:' + identity, interval, int(end - minutes * 60),
                  end, number(value['used_percent']), 'server quota', observed)


def codex_samples(source, paths, now, max_age=86400):
    latest = {}
    for path in paths:
        for line in tail_lines(path):
            record = quota_record(line, now, max_age)
            if record is None:
                continue
            identity, observed, limits = record
            if identity not in latest or observed > latest[identity][0]:
                latest[identity] = (observed, limits)
    result = []
    for identity, (observed, limits) in latest.items():
        for slot in ('primary', 'secondary'):
            sample = quota_window(source, identity, limits.get(slot), observed, now)
            if sample is not None:
                result.append(sample)
    return result


async def zai(source, config, now, root):
    return Collection(estimates(source, config, now, await ccusage('hermes', root)))


async def codex(source, config, now, root):
    directory = Path(config.get('sessions', '~/.codex/sessions')).expanduser()
    paths = sorted(directory.rglob('*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
    # Fresh files can contain multiple quota IDs. Scan every recently active file.
    paths = [p for p in paths if p.stat().st_mtime >= now - config.get('max_age', 86400)]
    samples = codex_samples(source, paths, now, config.get('max_age', 86400))
    missing = [i for i in INTERVALS if i not in {s.interval for s in samples}]
    result = Collection(samples)
    if missing:
        try:
            result.samples += estimates(source, config, now, await ccusage('codex', root), missing)
        except Exception as exc:
            result.errors.append('ccusage fallback: ' + type(exc).__name__ + ': ' + str(exc))
    return result
