"""One repeatable renderer for worker launch settings and live update decisions."""
import copy
import hashlib
import json
import os
import tomllib

from .agents import Adapter
from .capabilities import apply_capabilities


WAIT_SECONDS = 300


def _roots(args):
    roots = []
    for index, arg in enumerate(args):
        value = args[index + 1] if arg in ('-c', '--config') and index + 1 < len(args) else (
            arg.split('=', 1)[1] if arg.startswith('--config=') else '')
        if not value.startswith('sandbox_workspace_write.writable_roots='):
            continue
        try:
            parsed = tomllib.loads('value=' + value.split('=', 1)[1])['value']
        except tomllib.TOMLDecodeError as exc:
            raise ValueError('Invalid writable_roots launch setting') from exc
        if not isinstance(parsed, list) or not all(isinstance(root, str) for root in parsed):
            raise ValueError('writable_roots must be a list of strings')
        roots.extend(parsed)
    return list(dict.fromkeys(roots))


def _normalize_args(args, workdir, task_checkout=False):
    """Remove stale cwd flags and duplicate roots, preserving all other policy."""
    clean = []
    roots_seen = False
    had_cwd = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ('-C', '--cd'):
            if index + 1 >= len(args):
                raise ValueError('cwd launch flag requires a value')
            had_cwd = True
            index += 2
        elif arg.startswith('--cd='):
            had_cwd = True
            index += 1
        else:
            value = args[index + 1] if arg in ('-c', '--config') and index + 1 < len(args) else (
                arg.split('=', 1)[1] if arg.startswith('--config=') else '')
            if value.startswith('sandbox_workspace_write.writable_roots='):
                if not roots_seen:
                    clean.extend(['-c', 'sandbox_workspace_write.writable_roots=' + json.dumps(_roots(args))])
                    roots_seen = True
                index += 2 if arg in ('-c', '--config') else 1
            else:
                clean.append(arg)
                index += 1
    if had_cwd or task_checkout:
        clean.extend(['-C', workdir])
    return clean


def apply(supervisor, target, recovering=False, worker=False):
    """Converge one target and return the exact launch specification."""
    config = target.setdefault('config', {})
    agent = target['agent']
    if agent == 'codex':
        args = list(config.get('extra_args', config.get('args', [])))
        task_checkout = any(record.get('path') == target.get('workdir')
                            for record in target.get('task_worktrees', {}).values())
        config['extra_args'] = _normalize_args(args, target['workdir'], task_checkout)
    target['capabilities'] = apply_capabilities(target, config)
    if worker and supervisor.beads.enabled and agent in ('codex', 'claude'):
        config.setdefault('env', {}).update(supervisor.beads.environment(target))
    if agent == 'claude' and supervisor.config.get('native_hook_command'):
        from .hook_config import claude_args
        config['extra_args'] = claude_args(config.get('extra_args', config.get('args', [])),
                                           supervisor.config['native_hook_command'])
    return {'argv': Adapter(agent).argv(target, recover=recovering),
            'env': dict(config.get('env', {})), 'workdir': target['workdir']}


def desired(supervisor, run, recovering=None):
    trial = copy.deepcopy(run)
    if recovering is None:
        recovering = run.get('launch_recovering', False)
    if (trial['agent'] == 'codex' and not recovering
            and trial.get('config', {}).get('fork_session_id')):
        # Preview the original fork command after its native identity is known.
        # The live adapter correctly resumes that identity on a real relaunch.
        trial.pop('native_session_id', None)
        trial.pop('fork_attempted', None)
    return apply(supervisor, trial, recovering=recovering, worker=True)


def signature(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def diff(actual, expected):
    old_roots = _roots(actual.get('argv', []))
    new_roots = _roots(expected['argv'])
    return {'argv_changed': not _argv_equal(actual.get('argv'), expected['argv']),
            'workdir_changed': actual.get('workdir') != expected['workdir'],
            'env_keys_changed': sorted(key for key, value in expected['env'].items()
                                       if actual.get('env', {}).get(key) != value),
            'writable_roots_added': [root for root in new_roots if root not in old_roots],
            'writable_roots_removed': [root for root in old_roots if root not in new_roots]}


def changed(actual, expected):
    return (not _argv_equal(actual.get('argv'), expected['argv'])
            or actual.get('workdir') != expected['workdir']
            or any(actual.get('env', {}).get(key) != value for key, value in expected['env'].items()))


def _argv_equal(actual, expected):
    if not actual or not expected:
        return actual == expected
    executable = os.path.basename(expected[0])
    if os.path.basename(actual[0]) == executable and actual[1:] == expected[1:]:
        return True
    # Script shims may leave the interpreter as pane_pid; match the selected
    # executable in its short prefix and every provider argument exactly.
    return (len(actual) > len(expected)
            and any(os.path.basename(part) == executable for part in actual[:3])
            and actual[-(len(expected) - 1):] == expected[1:])


def ended(run):
    from .continuation import safe
    completion = run.get('native_completion', {})
    return (safe(run, require_pickup=False) and completion.get('applied') is True
            and [completion.get('native'), completion.get('turn')] == run.get('native_turn_key'))
