"""Read-only Marmot /status projection. Contract: spec/channel-status.md."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

from .task_progress import build, routed
from .task_order import order_key

MAX_BYTES = 6000
STAGES = ('claimed', 'committed', 'tested', 'pr-open', 'merged', 'final-tested', 'deployed', 'done')
HINT = '\n[Truncated; inspect workstream task <name> progress or the owned terminal locally.]'


def clean(value, limit=120):
    return ' '.join(str(value).split())[:limit]


def bounded(text, budget=MAX_BYTES):
    raw = text.encode()
    if len(raw) <= budget:
        return text
    return raw[:budget-len(HINT.encode())].decode('utf-8', errors='ignore').rsplit('\n', 1)[0] + HINT


def snapshot(home, group):
    """One bounded SQLite read transaction. Never initialize Store or call Beads."""
    path = Path(home).expanduser().resolve() / 'workstreams/harness.sqlite3'
    deadline = time.monotonic() + 1
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute('BEGIN')
        rows = db.execute("SELECT group_id,data FROM runs WHERE state NOT IN ('completed','stopped','archived') LIMIT 201").fetchall()
        if len(rows) > 200:
            raise ValueError('Too many active workstreams')
        runs = [(str(g or '').lower(), json.loads(data)) for g, data in rows]
        matches = [run for g, run in runs if g and g == group.lower()]
        if len(matches) > 1:
            raise ValueError('Ambiguous channel ownership')
        if not matches:
            return {'known': sorted({clean(r['name']) for _, r in runs})}
        run = matches[0]
        rows = db.execute('SELECT issue_id,data FROM task_current LIMIT 5001').fetchall()
        if len(rows) > 5000:
            raise ValueError('Cached queue exceeds bounded view')
        cache = {key: json.loads(data) for key, data in rows}
        selected = sorted((i for i in cache.values() if routed(run, i)), key=order_key)
        progress = build(run, selected, cache.__getitem__)
        for row in progress['tasks']:
            row['status'] = cache[row['id']].get('status')
        return {'run': run, 'tasks': progress['tasks'], 'counts': stage_counts(selected)}


def stage_counts(issues):
    counts = dict.fromkeys(STAGES, 0)
    for issue in issues:
        stages = (issue.get('metadata') or {}).get('harness_lifecycle', [])
        stage = stages[-1].get('stage') if stages else None
        if stage is None and issue.get('status') == 'in_progress':
            stage = 'claimed'
        if issue.get('status') == 'closed':
            stage = 'done'
        if stage == 'close-ready':
            stage = 'deployed'
        if stage in counts:
            counts[stage] += 1
    return counts



def capture(run, lines=25):
    """Read-only tmux commands with a shared two-second deadline; no inspect/reap."""
    if not isinstance(lines, int) or not 1 <= lines <= 2000:
        raise ValueError('Invalid capture line count')
    name, pane = run.get('tmux_session', ''), run.get('pane_id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or not re.fullmatch(r'%\d+', pane):
        return 'unavailable (no owned pane)', ''
    deadline = time.monotonic() + 2
    def call(*args):
        result = subprocess.run(['tmux', '-L', 'hermes-workstreams', *args],
                                capture_output=True, text=True, check=True,
                                timeout=max(.01, deadline-time.monotonic()))
        return result.stdout
    try:
        owner = call('show-environment', '-t', '=' + name, 'HERMES_WORKSTREAM_RUN').strip()
        if owner != 'HERMES_WORKSTREAM_RUN=' + run['id']:
            return 'unavailable (ownership mismatch)', ''
        panes = call('list-panes', '-s', '-t', '=' + name, '-F', '#{pane_id}\t#{pane_dead}').splitlines()
        matching = [line.split('\t') for line in panes if line.split('\t')[0] == pane]
        if len(matching) != 1:
            return 'unavailable (pane missing)', ''
        text = call('capture-pane', '-p', '-t', pane, '-S', '-' + str(lines))
        return 'exited' if matching[0][1] == '1' else 'live', '\n'.join(text.rstrip().splitlines()[-lines:])
    except (OSError, subprocess.SubprocessError):
        return 'unavailable (tmux read failed)', ''


def render(data, pane, captured_at):
    if 'known' in data:
        return bounded('Use /status in a workstream channel. Known workstreams: ' + ', '.join(data['known']))
    run = data['run']
    lines = [f"Workstream: {clean(run['name'])} ({clean(run['id'])})",
             f"State: {clean(run.get('state', 'unknown'))}; native: {clean(run.get('native_turn_state', 'unknown'))}",
             'Queue: cached projection; claim eligibility requires live validation.',
             'Stages (latest recorded): ' + ', '.join(f'{key}={value}' for key, value in data['counts'].items()),
             'In flight:']
    active = [t for t in data['tasks'] if t.get('status') == 'in_progress']
    pending = [t for t in data['tasks'] if t.get('status') != 'in_progress']
    for label, tasks in ((None, active), ('Pending (claim order; blocked entries labeled):', pending)):
        if label:
            lines.append(label)
        lines.extend(f"{clean(t['id'])} | {clean(t['title'], 80)} [{clean(t['view'])}]" for t in tasks[:12])
        if not tasks:
            lines.append('(none cached)')
        if len(tasks) > 12:
            lines.append(f'... {len(tasks)-12} more; use workstream task {clean(run["name"])} progress locally.')
    header = bounded('\n'.join(lines), 3000)
    state, text = pane
    # Preserve lines while stripping terminal controls; capture-pane omits ANSI escapes.
    text = ''.join(c for c in text if c in '\n\t' or c.isprintable())
    return bounded(header + f'\nPane {clean(run.get("pane_id", "unknown"))}, run {clean(run["id"])}; captured {captured_at}; {state}\n' + text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('HERMES_HOME', '~/.hermes')).expanduser())
    parser.add_argument('--group', required=True)
    args = parser.parse_args()
    try:
        data = snapshot(args.home, args.group)
        pane = capture(data['run']) if 'run' in data else ('', '')
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        print(render(data, pane, stamp))
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        print('Workstream status unavailable (' + type(exc).__name__ + '); retry /status later.')


if __name__ == '__main__':
    main()
