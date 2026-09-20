"""Owned tmux panes on a dedicated socket; no shell interpolation or approvals."""
import re
import os
from pathlib import Path
import shlex
import signal
import subprocess
import uuid


class Tmux:
    def __init__(self, socket='hermes-workstreams'):
        self.socket = socket

    def _call(self, *args, check=True, input=None):
        return subprocess.run(['tmux', '-L', self.socket, *args], input=input,
                              text=True, capture_output=True, check=check, timeout=10)

    def _session(self, run):
        name = run['tmux_session']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('invalid tmux session name')
        return name

    def _owner(self, run):
        name = self._session(run)
        result = self._call('show-environment', '-t', '=' + name,
                            'HERMES_WORKSTREAM_RUN', check=False)
        if result.returncode:
            return False
        if result.stdout.strip() != 'HERMES_WORKSTREAM_RUN=' + run['id']:
            raise RuntimeError('tmux session is owned by another run')
        return True

    def launch(self, run, argv):
        name = self._session(run)
        if self._owner(run):
            info = self.inspect(run)
            if info['dead']:
                raise RuntimeError('owned pane exited; explicit recovery must remove it first')
            return info['pane_id']
        if not argv or not all(isinstance(arg, str) and '\0' not in arg for arg in argv):
            raise ValueError('argv must be nonempty strings without NUL')
        environment = []
        for key, value in run.get('config', {}).get('env', {}).items():
            if (not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key)
                    or key.startswith('HERMES_WORKSTREAM_')
                    or not isinstance(value, str) or '\0' in value):
                raise ValueError('invalid or reserved launch environment variable')
            environment.extend(['-e', key + '=' + value])
        # tmux parses a trailing semicolon even in argv; quote it for tmux itself.
        argv = [arg[:-1] + r'\;' if arg.endswith(';') else arg for arg in argv]
        # tmux treats ONE command argument as a shell command. env gives at least two.
        result = self._call('start-server', ';', 'set-option', '-g', 'remain-on-exit', 'on',
                            ';', 'new-session', '-d', '-P', '-F', '#{pane_id}',
                            '-s', name, '-c', run['workdir'],
                            '-e', 'HERMES_WORKSTREAM_RUN=' + run['id'],
                            *environment,
                            '--', '/usr/bin/env', '--', *argv)
        pane = result.stdout.strip().splitlines()[0]
        if not re.fullmatch(r'%\d+', pane):
            raise RuntimeError('tmux did not return an immutable pane ID')
        logfile = run.get('config', {}).get('logfile')
        if logfile:
            path = Path(logfile).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.fchmod(descriptor, 0o600)
            os.close(descriptor)
            self._call('pipe-pane', '-o', '-t', pane, 'cat >> ' + shlex.quote(str(path)))
        return pane

    def inspect(self, run):
        if not self._owner(run):
            return {'alive': False, 'dead': True, 'missing': True, 'exit_code': None, 'text': ''}
        name = self._session(run)
        for attempt in range(2):
            result = self._call('list-panes', '-s', '-t', '=' + name, '-F',
                                '#{pane_id}\t#{pane_dead}\t#{pane_dead_status}\t#{pane_dead_signal}')
            panes = [line.split('\t') for line in result.stdout.splitlines()]
            selected = [p for p in panes if not run.get('pane_id') or p[0] == run['pane_id']]
            if len(selected) != 1:
                raise RuntimeError('owned pane identity is missing or ambiguous')
            pane, dead, code, killed = selected[0]
            if attempt or dead != '1' or code or killed:
                break
            # tmux 3.4 can miss a coalesced SIGCHLD, retaining an unreaped zombie
            # and no exit status. Notify only our server to reap, then recheck once.
            server_pid = int(self._call('display-message', '-p', '#{pid}').stdout.strip())
            os.kill(server_pid, signal.SIGCHLD)
        exit_code = int(code) if code else None
        if exit_code is None and killed:
            number = int(killed) if killed.isdigit() else getattr(signal, 'SIG' + killed.removeprefix('SIG'), None)
            if number is not None:
                exit_code = 128 + int(number)
        text = self._call('capture-pane', '-p', '-t', pane, '-S', '-2000').stdout
        return {'pane_id': pane, 'alive': dead == '0', 'dead': dead == '1',
                'missing': False, 'exit_code': exit_code, 'exit_signal': killed or None, 'text': text}

    def send(self, run, text, submit=False):
        info = self.inspect(run)
        if not info['alive']:
            raise RuntimeError('cannot send to a missing or exited pane')
        if any(ord(char) < 32 and char not in '\n\t' for char in text):
            raise ValueError('terminal control characters are not permitted')
        if not text:
            if submit:
                self._call('send-keys', '-t', info['pane_id'], 'C-m')
            return
        buffer = 'hws-' + uuid.uuid4().hex
        self._call('load-buffer', '-b', buffer, '-', input=text)
        try:
            self._call('paste-buffer', '-p', '-d', '-b', buffer, '-t', info['pane_id'])
            if submit:
                self._call('send-keys', '-t', info['pane_id'], 'Enter')
        finally:
            self._call('delete-buffer', '-b', buffer, check=False)

    def stop(self, run):
        if self._owner(run):
            self._call('kill-session', '-t', '=' + self._session(run))
