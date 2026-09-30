"""Build and run the bwrap command for one agent session. Spike S3."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

RO_SYSTEM = ['/usr', '/etc/ssl', '/etc/ca-certificates', '/etc/alternatives', '/etc/passwd',
             '/etc/group', '/etc/nsswitch.conf', '/etc/localtime']


def binary_roots(names: list[str]) -> list[str]:
    """Real install dirs of the agent CLIs (they may live under the real home)."""
    roots = set()
    for name in names:
        path = shutil.which(name)
        if path:
            real = Path(path).resolve()
            roots.add(str(real.parent.parent if real.parent.name == 'bin' else real.parent))
    return sorted(roots)


CRED_FILES = ['.claude/.credentials.json', '.codex/auth.json']


def resolve_agent(cmd: list[str], agents: list[str]) -> list[str]:
    """Deviation: the claude launcher is a symlink to bin/claude.exe, so 'claude' is not on the in-sandbox PATH."""
    if cmd and cmd[0] in agents and (path := shutil.which(cmd[0])):
        return [str(Path(path).resolve()), *cmd[1:]]
    return cmd


def argv(work: Path, home: Path, run: Path, cmd: list[str], agents: list[str],
         ro_creds: bool = False) -> list[str]:
    # Deviation: --clearenv. bwrap inherits the caller's whole environment by default, which leaked
    # operator secrets (bot tokens, parent-session sockets/tokens) and parent CLAUDE_CODE_* settings.
    a = ['bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
         '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'TERM', os.environ.get('TERM', 'dumb'),
         '--setenv', 'USER', os.environ.get('USER', 'agent'),
         '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
         '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
         '--symlink', 'usr/sbin', '/sbin']
    for p in RO_SYSTEM:
        if os.path.exists(p):
            a += ['--ro-bind', p, p]
    for p in binary_roots(agents):
        a += ['--ro-bind', p, p]
    a += ['--bind', str(home), str(home), '--setenv', 'HOME', str(home),
          '--bind', str(work), str(work), '--chdir', str(work),
          '--ro-bind', str(run), '/run/hz',
          '--setenv', 'HZ_SESSION_SOCKET', '/run/hz/session.sock',
          '--setenv', 'PATH', ':'.join(['/usr/bin', *[f'{r}/bin' for r in binary_roots(agents)], *binary_roots(agents)])]
    if ro_creds:   # Step 7.5 experiment: credential files read-only over the read-write synthetic home
        for rel in CRED_FILES:
            if (home / rel).exists():
                a += ['--ro-bind', str(home / rel), str(home / rel)]
    return a + ['--', 'python3', '/run/hz/bridge.py', '/run/hz/proxy.sock', '--', *resolve_agent(cmd, agents)]


if __name__ == '__main__':
    work, home, run = (Path(p) for p in sys.argv[1:4])
    sys.exit(subprocess.call(argv(work, home, run, sys.argv[4:], ['claude', 'codex'],
                                  ro_creds=os.environ.get('HZ_S3_RO_CREDS') == '1')))
