"""Run one agent session under OpenShell (podman driver), behind the §7 launch self-test. Spike S5.

Usage:
  launch.py up <adapter> <sid>        prepare the session, create the sandbox, run the self-test
  launch.py agent <adapter> <sid>     re-run the complete self-test, then launch the agent in host tmux
                                      (-L s5spike) with its lifetime reaper, then have the agent run the
                                      probes through its own tool; any failure stops the session
  launch.py selftest <adapter> <sid>  re-run the self-test against an existing sandbox
  launch.py down <sid>                delete the sandbox, stop the session socket and the reaper

<adapter> is claude or codex. The session lives in ~/.cache/s5/<sid>; the sandbox is s5-<sid>.
Env knobs: S5_ACCOUNT=<name> chooses the account (default: the real login). For negative controls,
S5_SELFTEST_INJECT=<name> sabotages one precondition so a probe must fail and the launch be refused.
S5_AGENT_INJECT=<name> does the same for the agent path only (forge, leak-tool-env).
S5_MAX_LIFETIME_S / S5_STOP_MARGIN_S shorten the lifetime to demonstrate the reaper.
"""
import base64
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
S3 = HERE.parent / 's3'
ROOT = Path.home() / '.cache' / 's5'
IMAGE = 'localhost/s5-agent:0'
TMUX = ['tmux', '-L', 's5spike']
REAL_HOME_CANARY = Path.home() / '.s5-canary'
# Session maximum lifetime and stop margin: the freshness gate requires the token to outlive both,
# and the reaper (started with the agent) stops the session at the maximum. Env overrides exist only
# so the reaper can be demonstrated with a short lifetime.
MAX_LIFETIME_S = int(os.environ.get('S5_MAX_LIFETIME_S', 2 * 3600))
STOP_MARGIN_S = int(os.environ.get('S5_STOP_MARGIN_S', 15 * 60))

# Accounts per adapter: name -> login dir. 'default' is the adapter's default login (the operator's
# real one, bound read-only). The others are spike-made dummy accounts (setup_accounts), so the
# Other accounts probe has every class to check:
#   b         present: a dummy login file; its configured dir is a symlink, so the canonical path differs
#   c         absent:  an empty dir
#   d         unknown: a dir with mode 000 (cannot be listed)
#   e         unknown: the login file is a dangling symlink (listed, but cannot be opened)
#   alias     present: a symlink to the default login dir, so the default login is also checked through
#             a configured path that differs from its canonical one when another account is chosen
ACCTS = ROOT / 'accounts'
ACCOUNTS = {
    a: {'default': Path.home() / home_dir, 'b': ACCTS / f'{a}-b-link', 'c': ACCTS / f'{a}-c',
        'd': ACCTS / f'{a}-d', 'e': ACCTS / f'{a}-e', 'alias': ACCTS / f'{a}-default-alias'}
    for a, home_dir in (('claude', '.claude'), ('codex', '.codex'))
}
LOGIN_FILES = {'claude': ['.credentials.json'], 'codex': ['auth.json']}
CONFIG_ENV = {'claude': 'CLAUDE_CONFIG_DIR', 'codex': 'CODEX_HOME'}
CONFIG_DIR = {'claude': '.claude', 'codex': '.codex'}

# Exact egress hosts per adapter (S3), plus the probes' allowlisted control. The OAuth refresh
# hosts (platform.claude.com, auth.openai.com) are deliberately absent: no refresh from inside.
EGRESS = {'claude': ['api.anthropic.com'], 'codex': ['chatgpt.com']}
PROBE_ALLOWED, PROBE_DENIED = 'api.openai.com', 'example.org'

# Environment allowlist (§7 environment probe), one source for the probes and the host-side check.
LAUNCHER_ENV = {'HOME', 'PATH', 'LANG', 'TERM', 'USER', 'HZ_SESSION_SOCKET', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME',
                'ENABLE_CLAUDEAI_MCP_SERVERS'}
OPENSHELL_ENV = {'OPENSHELL_SANDBOX', 'OPENSHELL_USER_ENVIRONMENT', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE',
                 'REQUESTS_CA_BUNDLE', 'GIT_SSL_CAINFO', 'NODE_EXTRA_CA_CERTS', 'DENO_CERT', 'container',
                 'HOSTNAME', 'DEBIAN_FRONTEND', 'SHELL'}
SHELL_ENV = {'PWD', 'SHLVL', '_', 'OLDPWD'}
# Variables each pinned CLI adds to its tools' environment (observed in the r1 agent-path runs; names
# only, none carries a host secret). A different CLI version refuses the launch until re-pinned.
CLI_PIN = {'claude': '2.1.286', 'codex': '0.160.0'}
CLI_TOOL_ENV = {
    'claude': {'AI_AGENT', 'CLAUDECODE', 'CLAUDE_CODE_AUTO_COMPACT_WINDOW', 'CLAUDE_CODE_CHILD_SESSION',
               'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_EXECPATH', 'CLAUDE_CODE_MESSAGING_SOCKET',
               'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_CODE_SESSION_ATTENDED', 'CLAUDE_CODE_SESSION_ID',
               'CLAUDE_EFFORT', 'CLAUDE_PID', 'COREPACK_ENABLE_AUTO_PIN', 'GIT_EDITOR',
               'NoDefaultCurrentDirectoryInExePath'},
    'codex': {'CODEX_CI', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'CODEX_VERSION', 'COLORTERM', 'GH_PAGER',
              'GIT_PAGER', 'LC_ALL', 'LC_CTYPE', 'NO_COLOR', 'PAGER'},
}
# The agent-path probe as the host requires to see it in /proc: the image's python, isolated mode
# (-I: no PYTHON* variables, no user site), the read-only script, config from the read-only file.
PROBE_EXE = '/usr/bin/python3.12'
PROBE_ARGV = ['python3', '-I', '/run/hz/probes.py', '--agent']
AGENT_CHECKS = ['real-home-canary-unreadable', 'non-allowlisted-host-blocked', 'direct-network-blocked',
                'control-op-rejected', 'wsd-socket-absent', 'allowlisted-host-reachable', 'model-host-agent-path',
                'hook-event-accepted', 'host-env-not-inherited', 'openshell-control-material-unreadable',
                'other-accounts']


def setup_accounts() -> None:
    """Create the dummy accounts (idempotent). Dummy login files hold no credential: only a far expiry,
    so that a dummy account can be the chosen one for a self-test (never for a model call)."""
    exp = int(time.time() + 30 * 86400)
    dummy = {'claude': json.dumps({'claudeAiOauth': {'expiresAt': exp * 1000, 'accessToken': 'dummy-not-a-token'}}),
             'codex': json.dumps({'tokens': {'access_token': 'x.' + base64.urlsafe_b64encode(
                 json.dumps({'exp': exp}).encode()).decode().rstrip('=') + '.x'}})}
    ACCTS.mkdir(parents=True, exist_ok=True)
    for a, (f,) in LOGIN_FILES.items():
        b = ACCTS / f'{a}-b'
        b.mkdir(exist_ok=True)
        (b / f).write_text(dummy[a])
        (b / f).chmod(0o600)
        for link, target in ((ACCTS / f'{a}-b-link', b.name), (ACCTS / f'{a}-default-alias', ACCOUNTS[a]['default'])):
            if not link.is_symlink():
                link.symlink_to(target)
        (ACCTS / f'{a}-c').mkdir(exist_ok=True)
        d = ACCTS / f'{a}-d'
        if not d.exists():
            d.mkdir()
        d.chmod(0o000)
        e = ACCTS / f'{a}-e'
        e.mkdir(exist_ok=True)
        if not (e / f).is_symlink():
            (e / f).symlink_to(ACCTS / 'nonexistent-target')


def cli_binary(name: str) -> Path:
    return Path(shutil.which(name)).resolve()


def cli_root(name: str) -> Path:
    real = cli_binary(name)
    return real.parent.parent if real.parent.name == 'bin' else real.parent


def cli_version(name: str) -> str:
    if name == 'claude':
        return json.loads((cli_root('claude') / 'package.json').read_text())['version']
    return cli_root('codex').name.split('-')[0]   # .../releases/<version>-<target>


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
        setup_accounts()
        for d in (self.home, self.work, self.run, self.bridge, self.bridge / 'daemon', self.conf):
            d.mkdir(parents=True, exist_ok=True)
        (self.bridge / 'daemon').chmod(0o700)   # codex refuses a socket dir that is not 0700
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
            '[features]\napps = false\n')   # §7: connectors (apps) off

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
            # codex 0.160 binds the real app-server socket in /tmp/codex-daemon-<uid>/ and leaves only a
            # symlink at the --listen path, so that dir is bound from the bridge dir too.
            m.append({'type': 'bind', 'source': str(self.bridge / 'daemon'), 'target': f'/tmp/codex-daemon-{os.getuid()}',
                      'read_only': False})
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
    """present: opens. absent: the dir lists and the name is not in it. unknown: anything else, such
    as an unlistable dir, or a listed name that does not open (a dangling symlink, EACCES)."""
    try:
        path.open('rb').close()
        return 'present'
    except OSError:
        pass
    try:
        return 'unknown' if path.name in os.listdir(path.parent) else 'absent'
    except OSError:
        return 'unknown'


def other_accounts(s: Session) -> list[dict]:
    """Every login file of every other account, by configured and canonical path. An account whose
    canonical login file is the chosen one's (an alias of the chosen account) is not "other"."""
    chosen = {os.path.realpath(src) for src, _ in s.login_binds()}
    out = []
    for name, d in ACCOUNTS[s.adapter].items():
        if name == s.account:
            continue
        for f in LOGIN_FILES[s.adapter]:
            configured = d / f
            canonical = Path(os.path.realpath(configured))
            if str(canonical) in chosen:
                print(f'INFO other-account {name}: alias of the chosen account ({configured}), not other')
                continue
            out.append({'account': name, 'class': classify(configured),
                        'paths': sorted({str(configured), str(canonical)})})
    return out


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------- the self-test ----------
def logs_since(s: Session, since: str) -> str:
    return subprocess.run(['openshell', 'logs', s.name, '--since', since, '--source', 'sandbox', '-n', '2000'],
                          capture_output=True, text=True).stdout


def log_lines(s: Session, t0: float) -> list[str]:
    """This run's supervisor log lines (timestamp >= t0 - 2)."""
    return [ln for ln in logs_since(s, '5m').splitlines()
            if ln.startswith('[') and float(ln[1:ln.index(']')]) >= t0 - 2]


def log_checks(s: Session, t0: float, checks: list[tuple[str, str]]) -> int:
    lines, rc = log_lines(s, t0), 0
    for name, needle in checks:
        hit = [ln for ln in lines if needle in ln]
        print(f"{'PASS' if hit else 'FAIL'} {name} [{hit[-1].split('[ocsf] ')[-1] if hit else 'missing: ' + needle}]")
        rc = rc or (0 if hit else 1)
    return rc


def outer_fence(s: Session) -> int:
    """Outside: the workload container itself has no network (podman --network none). This is the
    kernel layer under OpenShell's seccomp broker, which answers every INET connect() first. A hung podman
    call times out, and selftest() turns the exception into a FAIL (seen once with concurrent podman runs)."""
    ps = subprocess.run(['podman', 'ps', '--filter', f'name=^openshell-default--{s.name}-', '--format', '{{.ID}}'],
                        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30).stdout.split()
    mode = (subprocess.run(['podman', 'inspect', '-f', '{{.HostConfig.NetworkMode}}', ps[0]],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30).stdout.strip() if len(ps) == 1 else f'containers={ps}')
    ok = mode == 'none'
    print(f"{'PASS' if ok else 'FAIL'} outer-fence-network-none [workload container NetworkMode={mode}]")
    return 0 if ok else 1


def probe_config(s: Session, path: str) -> dict:
    """Host-side preconditions (canaries, account classes, pinned hashes) and the probes' input."""
    s.oa_canary.write_text(secrets.token_hex(8))
    s.oa_canary.read_bytes()
    oa = other_accounts(s)
    for o in oa:
        print(f"INFO other-account {o['account']}: {o['class']} {' '.join(o['paths'])}")
    chosen = [{'path': str(dst), 'sha256': sha256(src)} for src, dst in s.login_binds()]
    return {'path': path, 'real_home_canary': str(REAL_HOME_CANARY), 'oa_canary': str(s.oa_canary),
            'other_accounts': oa, 'chosen': chosen, 'token': json.loads(s.state.read_text())['token'],
            'allowed': PROBE_ALLOWED, 'denied': PROBE_DENIED, 'model_host': EGRESS[s.adapter][0],
            'env_allowed': sorted(env_allowed(s.adapter, path)), 'env_user': sorted(LAUNCHER_ENV | OPENSHELL_ENV)}


def env_allowed(adapter: str, path: str) -> set[str]:
    base = LAUNCHER_ENV | OPENSHELL_ENV | SHELL_ENV
    return base | CLI_TOOL_ENV[adapter] if path == 'agent' else base


def canary_precondition() -> int:
    REAL_HOME_CANARY.write_text(secrets.token_hex(8))
    if os.environ.get('S5_SELFTEST_INJECT') == 'canary-missing':
        REAL_HOME_CANARY.unlink()
    try:
        REAL_HOME_CANARY.read_bytes()
    except OSError as e:
        print(f'ABORT canary-precondition: {REAL_HOME_CANARY} not readable outside ({e.strerror})')
        return 3
    print(f'PASS canary-precondition [{REAL_HOME_CANARY} readable outside]')
    return 0


def egress_log_checks(adapter: str, path: str) -> list[tuple[str, str]]:
    model = EGRESS[adapter][0]
    # The model host's policy names only the CLI binary. OpenShell also authorizes a connection whose
    # executable *ancestor* is listed, so curl reaches it only when run from the agent's own process
    # tree: ALLOWED proves the agent path, DENIED proves the separate exec path is not the agent's.
    ancestry = (('agent-ancestry-allowed', f'ALLOWED /usr/bin/curl(0) -> {model}:443 [policy:{adapter}_model')
                if path == 'agent' else
                ('exec-path-not-agent', f'DENIED /usr/bin/curl(0) -> {model}:443 [reason:transparent_tcp_policy_denied]'))
    return [('proxy-logged-refusal', f'DENIED /usr/bin/curl(0) -> {PROBE_DENIED}:443 [reason:transparent_tcp_policy_denied]'),
            ('proxy-logged-allow', f'ALLOWED /usr/bin/curl(0) -> {PROBE_ALLOWED}:443 [policy:probe_control'),
            ('proxy-logged-direct-deny', '-> 1.1.1.1:443 [reason:transparent_tcp_policy_denied]'),  # install-agnostic: allow=ip-port
            ancestry]


def selftest(s: Session) -> int:
    """Fail closed. Any earlier result is invalidated first, and the result is written on every exit,
    so a cached PASS can never stand in for this run."""
    rcfile = s.dir / 'selftest.rc'
    rcfile.unlink(missing_ok=True)
    try:
        rc = _selftest(s)
    except Exception as e:   # noqa: BLE001 - any error is a failed self-test
        print(f'FAIL selftest-error [{type(e).__name__}: {e}]')
        rc = 1
    print('SELFTEST', 'PASS' if rc == 0 else 'FAIL (launch refused)', flush=True)
    rcfile.write_text(str(rc))
    return rc


def _selftest(s: Session) -> int:
    inject = os.environ.get('S5_SELFTEST_INJECT', '')
    if not freshness_gate(s):
        return 4
    if canary_precondition():
        return 3
    rc = outer_fence(s)
    probe_in = probe_config(s, 'exec')
    if inject == 'oa-canary-inside':     # point the canary into the bound home: the probe must see it readable
        probe_in['oa_canary'] = str(s.home / 'oa-canary-copy')
        (s.home / 'oa-canary-copy').write_text('x')
    if inject == 'chosen-mismatch':
        probe_in['chosen'][0]['sha256'] = '0' * 64
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
    time.sleep(3)   # outside: the supervisor's own log must show this run's refusals and allows
    return log_checks(s, t0, egress_log_checks(s.adapter, 'exec')) or rc


# ---------- agent launch in host tmux ----------
def tmux(*a: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(TMUX + list(a), capture_output=True, text=True, **kw)


def wait_pane(sid: str, marker: str, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if marker in tmux('capture-pane', '-p', '-t', sid).stdout:
            return True
        time.sleep(1)
    return False


class ProbeChannel:
    """Host side of the agent-path result channel, run/probe.sock (/run/hz/probe.sock inside). Results
    count only from a peer the host verifies itself: SO_PEERCRED gives its host pid, and /proc must show
    the image's python running the read-only probes.py as PROBE_ARGV, untraced, in the workload
    container's netns, with the CLI binary as an ancestor inside that netns, and with an environment
    inside the allowlist. Any other peer is recorded as rejected, and that alone fails the gate."""

    def __init__(self, s: Session):
        self.s, self.allowed = s, env_allowed(s.adapter, 'agent')
        self.netns = workload_netns(s)
        self.verified: list[dict] = []
        self.rejected: list[tuple[int, str]] = []
        self.path = s.run / 'probe.sock'
        self.path.unlink(missing_ok=True)
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(str(self.path))
        self.path.chmod(0o777)
        self.srv.listen(8)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self) -> None:
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c: socket.socket) -> None:
        pid = struct.unpack('3i', c.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
        why = self.verify(pid)
        if why:
            self.rejected.append((pid, why))
            print(f'  channel: REJECTED peer pid {pid}: {why}', flush=True)
            c.close()
            return
        rec = {'pid': pid, 'checks': {}, 'done': None}
        self.verified.append(rec)
        with c, c.makefile() as f:
            for line in f:
                m = json.loads(line)
                if 'done' in m:
                    again = self.verify(pid)   # the same verified process, still untraced, at completion
                    rec['done'] = m['done'] if not again else f'peer changed before completion: {again}'
                else:
                    rec['checks'][m['check']] = m['ok']
                    print(f"  agent: {'PASS' if m['ok'] else 'FAIL'} {m['check']} [{m['evidence']}]", flush=True)

    def verify(self, pid: int) -> str:
        p = Path(f'/proc/{pid}')
        try:
            exe = os.readlink(p / 'exe')
            argv = [a.decode() for a in (p / 'cmdline').read_bytes().split(b'\0')[:-1]]
            status = (p / 'status').read_text()
            netns = os.readlink(p / 'ns' / 'net')
            env = {e.split(b'=', 1)[0].decode() for e in (p / 'environ').read_bytes().split(b'\0') if e}
        except OSError as e:
            return f'/proc/{pid} unreadable ({e.strerror})'
        if exe != PROBE_EXE or argv != PROBE_ARGV:
            return f'not the probe: exe={exe} argv={argv}'
        if '\nTracerPid:\t0\n' not in status:
            return 'traced'
        if netns != self.netns:
            return f'netns {netns} is not the workload container ({self.netns})'
        if env - self.allowed:
            return f"environment beyond the allowlist: {' '.join(sorted(env - self.allowed))}"
        cli, q = str(cli_binary(self.s.adapter)), pid
        while q > 1:
            try:
                q = int(Path(f'/proc/{q}/status').read_text().split('\nPPid:\t')[1].split('\n')[0])
                if os.readlink(f'/proc/{q}/ns/net') != self.netns:
                    break
                if os.readlink(f'/proc/{q}/exe') == cli:
                    return ''
            except (OSError, IndexError, ValueError):
                break
        return f'no {cli} ancestor inside the workload netns'

    def verdict(self) -> int:
        recs = self.verified
        got = recs[0]['checks'] if len(recs) == 1 else {}
        missing = [c for c in AGENT_CHECKS if c not in got]
        failed = [c for c, ok in got.items() if not ok]
        done = recs[0]['done'] if len(recs) == 1 else None
        ok = len(recs) == 1 and not self.rejected and done == 0 and not missing and not failed
        print(f"{'PASS' if ok else 'FAIL'} agent-path-channel [verified probe runs={len(recs)} (exe {PROBE_EXE}, "
              f"argv {' '.join(PROBE_ARGV)}, untraced, workload netns {self.netns}, {self.s.adapter} CLI ancestor, env allowlisted)"
              f"{' pid ' + str(recs[0]['pid']) if recs else ''}, done={done}, checks {len(got) - len(failed)}"
              f"/{len(AGENT_CHECKS)} PASS, failed: {' '.join(failed) or 'none'}, missing: {' '.join(missing) or 'none'}, "
              f"rejected peers: {'; '.join(f'pid {p}: {w}' for p, w in self.rejected) or 'none'}]")
        return 0 if ok else 1

    def close(self) -> None:
        self.srv.close()
        self.path.unlink(missing_ok=True)


def workload_netns(s: Session) -> str:
    ids = subprocess.run(['podman', 'ps', '--filter', f'name=^openshell-default--{s.name}-', '--format', '{{.ID}}'],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30).stdout.split()
    pid = subprocess.run(['podman', 'inspect', '-f', '{{.State.Pid}}', *ids[:1]], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=30).stdout.strip()
    return os.readlink(f'/proc/{pid}/ns/net')


def agent_path_probes(s: Session) -> int:
    """The probes again, this time run by the agent itself through its own tool (Claude's Bash tool,
    a command run by the Codex app-server), with the host-side preconditions and log checks. What the
    agent writes or says is untrusted: the verdict comes only from ProbeChannel, fed by the verified
    probe process. Normal work (operator steering) starts only after this passes."""
    if canary_precondition():
        return 3
    inject = os.environ.get('S5_AGENT_INJECT', '')
    cfg = probe_config(s, 'agent')
    if inject == 'forge':   # the real probe must fail; then a forger claims success (below)
        cfg['chosen'][0]['sha256'] = '0' * 64
        shutil.copy(HERE / 'forge.py', s.run / 'forge.py')
    (s.run / 'agent-probe.json').write_text(json.dumps(cfg))   # ro at /run/hz; the agent already holds its token
    out = s.home / '.s5-agent-probe.out'
    out.unlink(missing_ok=True)
    marker = '❯' if s.adapter == 'claude' else '›'
    if not wait_pane(s.sid, marker, 90):
        print('FAIL agent-path-probes [agent prompt did not appear]')
        return 1
    ch = ProbeChannel(s)
    time.sleep(2)
    t0 = time.time()
    cmd = f'{" ".join(PROBE_ARGV)} > "$HOME/.s5-agent-probe.out" 2>&1; echo "probes-rc=$?" >> "$HOME/.s5-agent-probe.out"'
    tmux('send-keys', '-t', s.sid, '-l', f'Run exactly this shell command, then reply with only its exit status: {cmd}')
    time.sleep(1)
    tmux('send-keys', '-t', s.sid, 'Enter')
    end = time.time() + 240
    while time.time() < end and 'probes-rc=' not in (out.read_text() if out.exists() else ''):
        time.sleep(2)
    if inject == 'forge':
        # The adversary's moves, scripted from the launcher rather than prompted (the model need not
        # cooperate): a non-probe process in the sandbox sends an all-PASS result over the channel, and
        # the agent-written result file is overwritten with success.
        print(s.exec(['python3', '/run/hz/forge.py'], capture_output=True, text=True, timeout=60).stdout.strip())
        out.write_text('probes-rc=0\n')
    time.sleep(5)   # late peers (a forger after the probe) are still seen and counted
    text = out.read_text().strip() if out.exists() else ''
    print(f"INFO agent-written result file (untrusted, not used): {text.splitlines()[-1] if text else 'none'}")
    rc = ch.verdict()
    ch.close()
    return log_checks(s, t0, egress_log_checks(s.adapter, 'agent')) or rc


def agent(s: Session) -> int:
    pins = {a: cli_version(a) for a in CLI_PIN}
    if pins != CLI_PIN:   # CLI_TOOL_ENV is pinned to these versions
        print(f'REFUSED: CLI versions {pins} differ from the pinned {CLI_PIN}; re-pin CLI_TOOL_ENV first')
        down(s.sid)
        return 2
    print(f"PASS cli-version-pinned [{' '.join(f'{a} {v}' for a, v in pins.items())}]")
    # The complete gate runs immediately before every launch; an earlier PASS counts for nothing.
    if selftest(s) != 0:
        print('REFUSED: the launch self-test did not pass; deleting the sandbox')
        down(s.sid)
        return 2
    base = ['openshell', 'sandbox', 'exec', '-n', s.name, '--no-login-shell', '--workdir', str(s.work)]
    # Agent-path control: a variable only in the CLI's environment, so only its tools inherit it.
    leak = ['/usr/bin/env', 'S5_TOOL_LEAK=1'] if os.environ.get('S5_AGENT_INJECT') == 'leak-tool-env' else []
    if s.adapter == 'claude':
        inner = base + ['--tty', '--', *leak, str(cli_binary('claude')), '--permission-mode', 'bypassPermissions']
    else:
        sock = 'unix:///run/hz-bridge/app.sock'
        # The per-session app-server runs inside the sandbox, detached, listening in the bridge dir.
        s.exec(['sh', '-c', f'setsid {" ".join(leak)} {cli_binary("codex")} app-server --listen {sock} '
                '</dev/null >/run/hz-bridge/app-server.log 2>&1 &'], check=True)
        for _ in range(100):
            if any(p.is_socket() for p in (s.bridge / 'daemon').iterdir()):
                break
            time.sleep(0.2)
        else:
            print('REFUSED: codex app-server socket did not appear; see bridge/app-server.log')
            down(s.sid)
            return 2
        inner = base + ['--tty', '--', str(cli_binary('codex')), '--remote', sock,
                        '--dangerously-bypass-approvals-and-sandbox']
    tmux('new-session', '-d', '-s', s.sid, '-x', '200', '-y', '50', *inner, check=True)
    start_reaper(s)
    print(f'launched in tmux -L s5spike session {s.sid}; running the probes through the agent')
    if agent_path_probes(s) != 0:
        pane = [ln for ln in tmux('capture-pane', '-p', '-t', s.sid).stdout.splitlines() if ln.strip()]
        print('INFO agent pane (last lines):', *(f'  | {ln}' for ln in pane[-12:]), sep='\n')
        print('AGENT-PATH SELFTEST FAIL: stopping the session (no work was given to it)')
        down(s.sid)
        return 2
    print('AGENT-PATH SELFTEST PASS: session ready for work')
    return 0


# ---------- maximum lifetime (§7 freshness, §6.3 stop) ----------
def start_reaper(s: Session) -> None:
    deadline = time.time() + MAX_LIFETIME_S
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'reap', s.adapter, s.sid,
                          str(deadline), str(STOP_MARGIN_S)], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=open(s.dir / 'lifetime.log', 'a'), stderr=subprocess.STDOUT)
    (s.dir / 'reaper.pid').write_text(str(p.pid))
    print(f'INFO reaper pid {p.pid}: session stops at {time.strftime("%H:%M:%S", time.localtime(deadline))} '
          f'(max lifetime {MAX_LIFETIME_S} s, stop margin {STOP_MARGIN_S} s)')


def reap(s: Session, deadline: float, margin: int) -> None:
    """Stop the session at its maximum lifetime. The stop window opens `margin` before it; a turn
    boundary in that window (a Stop hook, plan 4) would end it gracefully. The spike has no turn
    tracking, so it always takes the hard path at the deadline: interrupt, then delete the sandbox."""
    sys.stdout.reconfigure(line_buffering=True)
    stamp = lambda: time.strftime('%H:%M:%S')   # noqa: E731
    print(f'{stamp()} reaper up: deadline {time.strftime("%H:%M:%S", time.localtime(deadline))}')
    time.sleep(max(0, deadline - margin - time.time()))
    print(f'{stamp()} stop window open ({margin} s before the maximum lifetime)')
    time.sleep(max(0, deadline - time.time()))
    print(f'{stamp()} maximum lifetime reached: interrupting the agent')
    tmux('send-keys', '-t', s.sid, 'Escape')
    time.sleep(2)
    (s.dir / 'reaper.pid').unlink(missing_ok=True)
    down(s.sid)
    print(f'{stamp()} session stopped: tmux session and sandbox {s.name} removed')


def down(sid: str) -> None:
    s = Session('claude', sid)
    subprocess.run(['openshell', 'sandbox', 'delete', s.name], capture_output=True)
    for f in ('stub.pid', 'reaper.pid'):
        pid = s.dir / f
        if pid.exists():
            try:
                if int(pid.read_text()) != os.getpid():
                    os.kill(int(pid.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
            pid.unlink()
    tmux('kill-session', '-t', sid)
    for _ in range(30):   # sandbox delete is asynchronous: wait until it is really gone
        if s.name not in subprocess.run(['openshell', 'sandbox', 'list'], capture_output=True, text=True).stdout.split():
            return
        time.sleep(1)
    print(f'WARN sandbox {s.name} still listed 30 s after delete')


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    op = sys.argv[1]
    if op == 'down':
        down(sys.argv[2])
        return 0
    adapter, sid = sys.argv[2], sys.argv[3]
    s = Session(adapter, sid, os.environ.get('S5_ACCOUNT', 'default'))
    if op == 'reap':
        reap(s, float(sys.argv[4]), int(sys.argv[5]))
        return 0
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
