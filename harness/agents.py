"""Small native CLI adapters. Recovery never guesses the latest conversation."""
import os
from pathlib import Path
import re
import sqlite3
import uuid


def _argument_list(value, error, allow_empty=True):
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(error)
    if not allow_empty and not value:
        raise ValueError(error)
    return value


def _extra_args(config):
    return _argument_list(config.get('extra_args', config.get('args', [])),
                          'config.args must be a list of strings')


def _exact_session_id(run):
    sid = run.get('native_session_id')
    if not sid or sid in {'latest', 'last'} or sid.startswith('-'):
        raise ValueError('recovery requires an exact native session ID')
    return sid


def _initial_prompt(run, flag='--'):
    prompt = run.get('launch_prompt', run.get('prompt', ''))
    return [flag, prompt] if prompt else []


def _hermes_session_name(run):
    name = run.get('config', {}).get('session_name')
    if name and (not re.fullmatch(r'[A-Za-z0-9_-]+', name) or name in {'latest', 'last'}):
        raise ValueError('invalid deterministic session name')
    return name


class Provider:
    """One provider's CLI contract; no lifecycle or transport ownership."""
    name = None
    database = None

    def argv(self, run, recover=False):
        config = run.get('config', {})
        executable = config.get('executable', self.name)
        extra = _extra_args(config)
        builder = self.resume if recover else self.start
        return builder(run, executable, extra)


class CodexProvider(Provider):
    name = 'codex'
    database = ('CODEX_HOME', '.codex', 'state_*.sqlite', 'threads', 'created_at')

    def start(self, run, executable, extra):
        source = run.get('config', {}).get('fork_session_id')
        if source:
            if run.get('native_session_id'):
                return self.resume(run, executable, extra)
            if run.get('fork_attempted'):
                raise ValueError('Fork already attempted; recover exact native identity before relaunch')
            try:
                source = str(uuid.UUID(source))
            except (ValueError, TypeError, AttributeError):
                raise ValueError('fork requires an exact native UUID') from None
            run['fork_attempted'] = True
            return [executable, 'fork', *extra, '--', source, *([run.get('launch_prompt', run.get('prompt', ''))] if run.get('launch_prompt', run.get('prompt', '')) else [])]
        return [executable, *extra, *_initial_prompt(run)]

    def resume(self, run, executable, extra):
        return [executable, 'resume', *extra, '--', _exact_session_id(run)]


class ClaudeProvider(Provider):
    name = 'claude'

    def start(self, run, executable, extra):
        sid = run.get('native_session_id') or str(uuid.uuid5(uuid.NAMESPACE_URL, 'hermes-workstream:' + run['id']))
        run['native_session_id'] = sid
        name = run.get('config', {}).get('session_name')
        return [executable, *extra, '--session-id', sid,
                *(['--name', name] if name else []), *_initial_prompt(run)]

    def resume(self, run, executable, extra):
        return [executable, *extra, '--resume', _exact_session_id(run)]


class HermesProvider(Provider):
    name = 'hermes'
    database = ('HERMES_HOME', '.hermes', 'state.db', 'sessions', 'started_at')

    def start(self, run, executable, extra):
        name = _hermes_session_name(run)
        return [executable, 'chat', '--cli', *extra,
                *(['--continue', name, '--create-if-missing'] if name else []),
                *_initial_prompt(run, '--query')]

    def resume(self, run, executable, extra):
        name = _hermes_session_name(run)
        if name and not run.get('native_session_id'):
            return [executable, 'chat', '--cli', *extra, '--continue', name, '--no-restore-cwd']
        return [executable, 'chat', *extra, '--resume', _exact_session_id(run), '--no-restore-cwd']


class CommandProvider(Provider):
    name = 'command'

    def start(self, run, executable, extra):
        argv = _argument_list(run.get('config', {}).get('argv'),
                              'command adapter requires config.argv as a nonempty string list',
                              allow_empty=False)
        return argv[:]

    def resume(self, run, executable, extra):
        raise ValueError('command workloads cannot be automatically replayed')


PROVIDERS = {provider.name: provider for provider in
             (CodexProvider(), ClaudeProvider(), HermesProvider(), CommandProvider())}


class Adapter:
    NAMES = {'claude-code': 'claude', **{name: name for name in PROVIDERS}}

    def __init__(self, name):
        try:
            self.name = self.NAMES.get(name, name)
            self.provider = PROVIDERS[self.name]
        except KeyError:
            raise ValueError(f'unsupported agent: {name}') from None

    def argv(self, run, recover=False):
        return self.provider.argv(run, recover)

    def discover(self, run):
        """Only accept a unique cwd/time match; ambiguity stays unresolved."""
        if run.get('native_session_id'):
            return run['native_session_id']
        if self.provider.database is None:
            return None
        started = run.get('launched_at', run.get('created_at'))
        if not isinstance(started, (float, int)):
            return None
        cfg = run.get('config', {})
        environment, directory, pattern, table, clock = self.provider.database
        home = Path(cfg.get('home', os.environ.get(environment, str(Path.home() / directory))))
        paths = sorted(home.glob(pattern))
        ids = set()
        for path in paths:
            if not path.exists():
                continue
            try:
                with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2) as db:
                    ids.update(row[0] for row in db.execute(
                        f'SELECT id FROM {table} WHERE cwd=? AND {clock}>=? AND {clock}<=?',
                        (str(Path(run['workdir']).resolve()), int(started), started + 120)))
            except sqlite3.Error:
                continue
        return next(iter(ids)) if len(ids) == 1 else None

    def observe(self, run, pane_text):
        # Screens are evidence for humans, never an approval or completion oracle.
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', pane_text)
        tail = '\n'.join(text.strip().splitlines()[-8:])
        state = 'unknown'
        if re.search(r'(?i)(approve|permission required|allow this|do you want to proceed|trust this)', tail):
            state = 'awaiting_approval'
        return {'native_session_id': self.discover(run), 'state': state,
                'summary': tail[-1200:] or 'No terminal output observed yet.'}
