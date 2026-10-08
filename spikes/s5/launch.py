"""Run one agent session under OpenShell (podman driver), behind the §7 launch self-test. Spike S5.

Usage:
  launch.py up <adapter> <sid>        prepare the session, create the sandbox, run the self-test
  launch.py agent <adapter> <sid>     launch the agent in host tmux (-L s5spike), only after a PASS
  launch.py selftest <adapter> <sid>  re-run the self-test against an existing sandbox
  launch.py down <sid>                delete the sandbox and stop the session socket

<adapter> is claude or codex. The session lives in ~/.cache/s5/<sid>; the sandbox is s5-<sid>.
Env knobs for negative controls: S5_SELFTEST_INJECT=<probe-name> makes the launcher
sabotage one precondition so that probe must fail and the launch must be refused.
"""
import base64
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
S3 = HERE.parent / 's3'
ROOT = Path.home() / '.cache' / 's5'
IMAGE = 'localhost/s5-agent:0'
TMUX = ['tmux', '-L', 's5spike']
REAL_HOME_CANARY = Path.home() / '.s5-canary'
MAX_LIFETIME_S = 2 * 3600      # session maximum lifetime for the freshness gate
STOP_MARGIN_S = 15 * 60

# Accounts per adapter: name -> login dir. 'default' is the adapter's default login (the operator's
# real one, bound read-only). The others are spike-made dummy accounts, so the Other accounts probe
# has a present, an absent and an unknown login file to check, and a configured path that is a
# symlink (so the canonical path differs).
ACCTS = ROOT / 'accounts'
ACCOUNTS = {
    'claude': {'default': Path.home() / '.claude', 'b': ACCTS / 'claude-b-link',
               'c': ACCTS / 'claude-c', 'd': ACCTS / 'claude-d'},
    'codex': {'default': Path.home() / '.codex', 'b': ACCTS / 'codex-b-link',
              'c': ACCTS / 'codex-c', 'd': ACCTS / 'codex-d'},
}
LOGIN_FILES = {'claude': ['.credentials.json'], 'codex': ['auth.json']}
CONFIG_ENV = {'claude': 'CLAUDE_CONFIG_DIR', 'codex': 'CODEX_HOME'}
CONFIG_DIR = {'claude': '.claude', 'codex': '.codex'}

# Exact egress hosts per adapter (S3), plus the probes' allowlisted control. The OAuth refresh
# hosts (platform.claude.com, auth.openai.com) are deliberately absent: no refresh from inside.
EGRESS = {'claude': ['api.anthropic.com'], 'codex': ['chatgpt.com']}
PROBE_ALLOWED, PROBE_DENIED = 'api.openai.com', 'example.org'


def cli_binary(name: str) -> Path:
    return Path(shutil.which(name)).resolve()


def cli_root(name: str) -> Path:
    real = cli_binary(name)
    return real.parent.parent if real.parent.name == 'bin' else real.parent


class Session:
    def __init__(self, adapter: str, sid: str, account: str = 'default'):
        self.adapter, self.sid, self.account = adapter, sid, account
        self.dir = ROOT / sid
        self.home, self.work, self.run, self.bridge = (self.dir / n for n in ('home', 'work', 'run', 'bridge'))
        self.conf = self.home / CONFIG_DIR[adapter]
        self.name = f's5-{sid}'
        self.state = self.dir / 'state.json'
        # The Other accounts canary lives beside the session dir, outside every bind source.
        self.oa_canary = ROOT / f'oa-canary-{sid}'

    # ---------- preparation ----------
    def prepare(self) -> None:
        for d in (self.home, self.work, self.run, self.bridge, self.conf):
            d.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        for f in ('session_stub.py', 'hook.py'):           # reused unchanged from S3
            shutil.copy(S3 / f, self.run / f)
        for f in ('probes.sh', 'probes.py', 'ws-request'):
            shutil.copy(HERE / f, self.run / f)
        (self.run / 'ws-request').chmod(0o755)
        if not (self.work / '.git').exists():
            subprocess.run(['git', 'init', '-q', str(self.work)], check=True)
            (self.work / 'README').write_text('S5 spike worktree stand-in\n')
        if self.adapter == 'claude':
            self.prepare_claude(token)
        else:
            self.prepare_codex()
        self.state.write_text(json.dumps({'token': token, 'adapter': self.adapter}))
        self.start_socket(token)

    def prepare_claude(self, token: str) -> None:
        hook = {'type': 'command', 'command': f'python3 /run/hz/hook.py {token}'}
        settings = {'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [hook]}]},
                    'skipDangerousModePermissionPrompt': True}
        (self.conf / 'settings.json').write_text(json.dumps(settings, indent=1))
        # Interactive first-run state: onboarding done, the worktree trusted.
        cj = {'hasCompletedOnboarding': True, 'theme': 'dark', 'bypassPermissionsModeAccepted': True,
              'projects': {str(self.work): {'hasTrustDialogAccepted': True, 'hasCompletedProjectOnboarding': True}}}
        (self.conf / '.claude.json').write_text(json.dumps(cj))

    def prepare_codex(self) -> None:
        (self.conf / 'config.toml').write_text(
            'approval_policy = "never"\nsandbox_mode = "danger-full-access"\n'
            f'[projects."{self.work}"]\ntrust_level = "trusted"\n'
            '[features]\nconnectors = false\napps = false\n')

    def start_socket(self, token: str) -> None:
        sock = self.run / 'session.sock'
        sock.unlink(missing_ok=True)
        p = subprocess.Popen([sys.executable, str(self.run / 'session_stub.py'), str(sock), token,
                              str(self.dir / 'session.log')], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=open(self.dir / 'stub.err', 'w'))
        for _ in range(50):
            if sock.exists():
                break
            time.sleep(0.1)
        sock.chmod(0o777)   # the sandbox user is the same uid (keep-id), but keep it explicit
        (self.dir / 'stub.pid').write_text(str(p.pid))

    # ---------- policy ----------
    def login_dir(self) -> Path:
        return ACCOUNTS[self.adapter][self.account]

    def login_binds(self) -> list[tuple[Path, Path]]:
        """(host source, sandbox target) for each login file of the chosen account."""
        return [(self.login_dir() / f, self.conf / f) for f in LOGIN_FILES[self.adapter]]

    def mounts(self) -> list[dict]:
        m = [{'type': 'bind', 'source': str(self.home), 'target': str(self.home), 'read_only': False},
             {'type': 'bind', 'source': str(self.work), 'target': str(self.work), 'read_only': False},
             {'type': 'bind', 'source': str(self.run), 'target': '/run/hz', 'read_only': True}]
        if self.adapter == 'codex':
            m.append({'type': 'bind', 'source': str(self.bridge), 'target': '/run/hz-bridge', 'read_only': False})
        for name in ('claude', 'codex'):   # both CLIs' install dirs, read-only, at their host paths
            r = str(cli_root(name))
            m.append({'type': 'bind', 'source': r, 'target': r, 'read_only': True})
        for src, dst in self.login_binds():
            m.append({'type': 'bind', 'source': str(src.resolve()), 'target': str(dst), 'read_only': True})
        return m

    def policy(self) -> dict:
        rw = ['/tmp', '/dev/null', '/dev/tty', '/dev/pts', str(self.home), str(self.work)]
        if self.adapter == 'codex':
            rw.append('/run/hz-bridge')
        ro = ['/usr', '/lib', '/lib64', '/etc', '/proc', '/dev/urandom', '/run/hz',
              str(cli_root('claude')), str(cli_root('codex'))]
        ro += [str(dst) for _, dst in self.login_binds()]
        nets = {f'{self.adapter}_model': {
                    'endpoints': [{'host': h, 'port': 443} for h in EGRESS[self.adapter]],
                    'binaries': [{'path': str(cli_binary(self.adapter))}]},
                'probe_control': {'endpoints': [{'host': PROBE_ALLOWED, 'port': 443}],
                                  'binaries': [{'path': '/usr/bin/curl'}]}}
        return {'version': 1,
                'filesystem_policy': {'include_workdir': False, 'read_only': ro, 'read_write': rw},
                'landlock': {'compatibility': 'hard_requirement'},
                'process': {'run_as_user': str(os.getuid()), 'run_as_group': str(os.getgid())},
                'network_policies': nets}

    def env(self) -> dict:
        path = ':'.join(['/usr/local/bin', '/usr/bin', '/bin', str(cli_binary('claude').parent),
                         str(cli_binary('codex').parent)])
        e = {'HOME': str(self.home), CONFIG_ENV[self.adapter]: str(self.conf), 'PATH': path,
             'LANG': 'C.UTF-8', 'TERM': 'xterm-256color', 'USER': 'agent',
             'HZ_SESSION_SOCKET': '/run/hz/session.sock'}
        if self.adapter == 'claude':
            e['ENABLE_CLAUDEAI_MCP_SERVERS'] = 'false'   # §7: claude.ai connectors off
        return e

    def create(self) -> None:
        pol = self.dir / 'policy.yaml'
        pol.write_text(json.dumps(self.policy(), indent=1))   # JSON is valid YAML
        dc = json.dumps({'podman': {'mounts': self.mounts()}})
        (self.dir / 'driver-config.json').write_text(dc)
        a = ['openshell', 'sandbox', 'create', '--name', self.name, '--detach', '--from', IMAGE,
             '--policy', str(pol), '--driver-config-json', dc, '--no-credential-warnings']
        for k, v in self.env().items():
            a += ['--env', f'{k}={v}']
        subprocess.run(a + ['--', 'sleep', 'infinity'], check=True, stdout=subprocess.DEVNULL)

    def exec(self, cmd: list[str], **kw) -> subprocess.CompletedProcess:
        if 'input' not in kw:
            kw.setdefault('stdin', subprocess.DEVNULL)
        return subprocess.run(['openshell', 'sandbox', 'exec', '-n', self.name, '--no-tty',
                               '--no-login-shell', '--workdir', str(self.work), '--', *cmd], **kw)


# ---------- freshness gate (§7, §4.4 D8; check only, no refresh) ----------
def access_expiry(adapter: str, login_dir: Path) -> float:
    if adapter == 'claude':
        return json.loads((login_dir / '.credentials.json').read_text())['claudeAiOauth']['expiresAt'] / 1000
    tok = json.loads((login_dir / 'auth.json').read_text())['tokens']['access_token']
    p = tok.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4)))['exp']


def freshness_gate(s: Session) -> bool:
    left = access_expiry(s.adapter, s.login_dir()) - time.time()
    need = MAX_LIFETIME_S + STOP_MARGIN_S
    ok = left > need
    print(f"{'PASS' if ok else 'FAIL'} freshness-gate [access token {left / 3600:.2f} h left, "
          f"need > {need / 3600:.2f} h; no refresh is attempted here]", flush=True)
    return ok


# ---------- Other accounts (§7, revision 14) ----------
def classify(path: Path) -> str:
    try:
        path.open('rb').close()
        return 'present'
    except FileNotFoundError:
        try:
            os.listdir(path.parent)
            return 'absent'
        except OSError:
            return 'unknown'
    except OSError:
        return 'unknown'


def other_accounts(s: Session) -> list[dict]:
    out = []
    for name, d in ACCOUNTS[s.adapter].items():
        if name == s.account:
            continue
        for f in LOGIN_FILES[s.adapter]:
            configured = d / f
            canonical = Path(os.path.realpath(configured))
            out.append({'account': name, 'class': classify(configured),
                        'paths': sorted({str(configured), str(canonical)})})
    return out


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------- the self-test ----------
def logs_since(s: Session, since: str) -> str:
    return subprocess.run(['openshell', 'logs', s.name, '--since', since, '--source', 'sandbox', '-n', '2000'],
                          capture_output=True, text=True).stdout


def selftest(s: Session) -> int:
    """Fail closed: 0 only if every outside check and every in-sandbox probe passes."""
    inject = os.environ.get('S5_SELFTEST_INJECT', '')
    rc = 0
    if not freshness_gate(s):
        return 4
    # Real-home canary: readable outside immediately before the probes, or abort.
    REAL_HOME_CANARY.write_text(secrets.token_hex(8))
    if inject == 'canary-missing':
        REAL_HOME_CANARY.unlink()
    try:
        REAL_HOME_CANARY.read_bytes()
    except OSError as e:
        print(f'ABORT canary-precondition: {REAL_HOME_CANARY} not readable outside ({e.strerror})')
        return 3
    print(f'PASS canary-precondition [{REAL_HOME_CANARY} readable outside]')
    # Other accounts: classify on the host, write the outside canary, pin the chosen login hashes.
    s.oa_canary.write_text(secrets.token_hex(8))
    s.oa_canary.read_bytes()
    oa = other_accounts(s)
    for o in oa:
        print(f"INFO other-account {o['account']}: {o['class']} {' '.join(o['paths'])}")
    chosen = [{'path': str(dst), 'sha256': sha256(src)} for src, dst in s.login_binds()]
    probe_in = {'real_home_canary': str(REAL_HOME_CANARY), 'oa_canary': str(s.oa_canary),
                'other_accounts': oa, 'chosen': chosen,
                'token': json.loads(s.state.read_text())['token'],
                'allowed': PROBE_ALLOWED, 'denied': PROBE_DENIED}
    if inject == 'oa-canary-inside':     # bind the canary dir in: the probe must see it readable
        probe_in['oa_canary'] = str(s.home / 'oa-canary-copy')
        (s.home / 'oa-canary-copy').write_text('x')
    if inject == 'chosen-mismatch':
        chosen[0]['sha256'] = '0' * 64
    t0 = time.time()
    cmd = ['sh', '/run/hz/probes.sh']   # config (with the session token) on stdin, not argv
    if inject == 'leak-env':
        cmd = ['env', 'LEAK=1', *cmd]
    try:
        r = s.exec(cmd, input=json.dumps(probe_in).encode(), timeout=180)
        rc = rc or r.returncode
    except subprocess.TimeoutExpired:
        print('FAIL probes-timeout [in-sandbox probes did not finish in 180 s]')
        rc = 1
    # Outside: the OpenShell supervisor's own log must show this run's refusals and allow.
    time.sleep(3)
    log = logs_since(s, '2m')
    checks = [('proxy-logged-refusal', f'DENIED /usr/bin/curl(0) -> {PROBE_DENIED}:443 [reason:transparent_tcp_policy_denied]'),
              ('proxy-logged-allow', f'ALLOWED /usr/bin/curl(0) -> {PROBE_ALLOWED}:443 [policy:probe_control'),
              ('proxy-logged-direct-deny', '-> 1.1.1.1:443 [reason:transparent_tcp_policy_denied]')]  # install-agnostic: allow=ip-port
    lines = [ln for ln in log.splitlines() if ln.startswith('[') and float(ln[1:ln.index(']')]) >= t0 - 2]
    for name, needle in checks:
        hit = [ln for ln in lines if needle in ln]
        ok = bool(hit)
        print(f"{'PASS' if ok else 'FAIL'} {name} [{hit[-1].split('[ocsf] ')[-1] if ok else 'missing: ' + needle}]")
        rc = rc or (0 if ok else 1)
    print('SELFTEST', 'PASS' if rc == 0 else 'FAIL (launch refused)', flush=True)
    (s.dir / 'selftest.rc').write_text(str(rc))
    return rc


# ---------- agent launch in host tmux ----------
def agent(s: Session) -> int:
    if (s.dir / 'selftest.rc').read_text().strip() != '0':
        print('REFUSED: no passing self-test for this sandbox')
        return 2
    base = ['openshell', 'sandbox', 'exec', '-n', s.name, '--no-login-shell', '--workdir', str(s.work)]
    if s.adapter == 'claude':
        inner = base + ['--tty', '--', str(cli_binary('claude')), '--permission-mode', 'bypassPermissions']
    else:
        sock = 'unix:///run/hz-bridge/app.sock'
        # The per-session app-server runs inside the sandbox, detached, listening in the bridge dir.
        s.exec(['sh', '-c', f'setsid {cli_binary("codex")} app-server --listen {sock} '
                '</dev/null >/run/hz-bridge/app-server.log 2>&1 &'], check=True)
        for _ in range(100):
            if (s.bridge / 'app.sock').exists():
                break
            time.sleep(0.2)
        inner = base + ['--tty', '--', str(cli_binary('codex')), '--remote', sock,
                        '--dangerously-bypass-approvals-and-sandbox']
    subprocess.run(TMUX + ['new-session', '-d', '-s', s.sid, '-x', '200', '-y', '50', *inner], check=True)
    print(f'launched in tmux -L s5spike session {s.sid}')
    return 0


def down(sid: str) -> None:
    s = Session('claude', sid)
    subprocess.run(['openshell', 'sandbox', 'delete', s.name], capture_output=True)
    pid = s.dir / 'stub.pid'
    if pid.exists():
        try:
            os.kill(int(pid.read_text()), signal.SIGTERM)
        except ProcessLookupError:
            pass
        pid.unlink()
    subprocess.run(TMUX + ['kill-session', '-t', sid], capture_output=True)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    op = sys.argv[1]
    if op == 'down':
        down(sys.argv[2])
        return 0
    adapter, sid = sys.argv[2], sys.argv[3]
    s = Session(adapter, sid, os.environ.get('S5_ACCOUNT', 'default'))
    if op == 'up':
        s.prepare()
        if os.environ.get('S5_SELFTEST_INJECT') == 'socket-dead':
            os.kill(int((s.dir / 'stub.pid').read_text()), signal.SIGTERM)
        s.create()
        rc = selftest(s)
        if rc != 0:
            print('launch refused: deleting the sandbox')
            down(sid)
        return rc
    if op == 'selftest':
        return selftest(s)
    if op == 'agent':
        return agent(s)
    raise SystemExit(__doc__)


if __name__ == '__main__':
    sys.exit(main())
