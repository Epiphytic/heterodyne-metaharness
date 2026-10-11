# pyright: basic
"""Inside the sandbox: the §7 launch self-test probes under OpenShell (ported from spike S5).

Each probe prints PASS/FAIL with its evidence, and only the specific enforcement passes: a generic
failure (missing canary, dead socket, timeout) FAILs. Under OpenShell an egress refusal is a connect()
EACCES from the supervisor's interception, so the marker is the policy-DNS synthetic address, and the
host checks the supervisor's own log afterwards.

Run twice per launch: by a separate `sandbox exec` (path "exec", input on stdin) and by the agent itself
through its own tool (path "agent", input from the read-only /run/hz/agent-probe.json). On the agent path
every result also goes to the host over /run/hz/p.sock, where the host verifies this process before it
counts anything it sends. The session token is read from /run/hz/token; the input holds no secret.

Usage: python3 -I /run/hz/probes.py < input        (exec path)
       python3 -I /run/hz/probes.py --agent        (agent path)
Standard library only.
"""
import ctypes
import errno
import hashlib
import ipaddress
import json
import os
import socket
import stat
import subprocess
import sys

RUN = '/run/hz'
AGENT = sys.argv[1:] == ['--agent']
if AGENT:
    with open(f'{RUN}/agent-probe.json') as f:
        cfg = json.load(f)
    channel = socket.socket(socket.AF_UNIX)
    channel.connect(f'{RUN}/p.sock')
else:
    cfg = json.load(sys.stdin)
with open(f'{RUN}/token') as f:
    TOKEN = f.read().strip()
PATH = cfg.get('path', 'exec')
SYNTHETIC = ipaddress.ip_network('198.18.0.0/15')   # OpenShell policy DNS answers from this range
rc = 0


def errname(e):
    """An OSError's errno name, else its number."""
    return errno.errorcode.get(e.errno or 0, str(e.errno))


def report(msg):
    if AGENT:
        channel.sendall((json.dumps(msg) + '\n').encode())


def check(name, ok, evidence):
    global rc
    print(f"{'PASS' if ok else 'FAIL'} {name} [{evidence}]", flush=True)
    report({'check': name, 'ok': ok, 'evidence': evidence})
    rc = rc or (0 if ok else 1)


def open_result(path, mode='rb'):
    try:
        open(path, mode).close()
        return 'READABLE' if 'r' in mode else 'WRITABLE'
    except OSError as e:
        return errname(e)


def connect(addr, family=socket.AF_INET, kind=socket.SOCK_STREAM):
    s = socket.socket(family, kind)
    s.settimeout(3)
    try:
        if kind == socket.SOCK_DGRAM:
            s.sendto(b'x', addr)
            return 'SENT'
        s.connect(addr)
        return 'CONNECTED'
    except TimeoutError:
        return 'TIMEOUT'
    except OSError as e:
        return errname(e)
    finally:
        s.close()


def sock(req):
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(3)
        s.connect(f'{RUN}/s.sock')
        s.sendall((json.dumps(req) + '\n').encode())
        return s.makefile().readline().strip()
    except OSError as e:
        return f'socket error {errname(e)}'


def sock_create(fam, kind, proto):
    try:
        socket.socket(fam, kind, proto).close()
        return 'CREATED'
    except OSError as e:
        return errname(e)


def curl(host):
    cu = subprocess.run(['curl', '-sv', '-o', '/dev/null', '--max-time', '10', '-w', 'http=%{http_code}',
                         f'https://{host}/'], capture_output=True, text=True)
    issuer = next((ln.split('issuer:')[1].strip() for ln in cu.stderr.splitlines() if 'issuer:' in ln), '')
    return cu, issuer


# 1. Real home: the canary (readable outside just before) gives ENOENT or EACCES/EPERM here.
r = open_result(cfg['real_home_canary'])
check('real-home-canary-unreadable', r in ('ENOENT', 'EACCES', 'EPERM'), f"open(canary) -> {r}")

# 2. Egress: the denied host resolves through OpenShell's policy DNS (synthetic address = marker), and
#    the connect is refused with EACCES by the supervisor's interception. The host then requires the
#    supervisor log's DENIED line for this binary and host.
host = cfg['denied']
try:
    addrs = sorted({str(a[4][0]) for a in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)})
except OSError as e:
    addrs = [f'resolve-error:{e}']
v4 = [a for a in addrs if not a.startswith('resolve') and ':' not in a]
marker = bool(v4) and len(v4) == len(addrs) and all(ipaddress.ip_address(a) in SYNTHETIC for a in v4)
c = connect((addrs[0], 443)) if marker else 'not-tried'
cu = subprocess.run(['curl', '-sv', '-o', '/dev/null', '--max-time', '8', f'https://{host}/'],
                    capture_output=True, text=True)
ok = marker and c == 'EACCES' and cu.returncode == 7 and 'Permission denied' in cu.stderr
check('non-allowlisted-host-blocked', ok,
      f'{host} -> {",".join(addrs)} (policy-DNS synthetic={marker}) connect -> {c}; curl rc={cu.returncode}')

# 3. Direct network, two layers, both required (the criterion ADR revision 15 accepts):
#    - the kernel fence: the workload's netns has only lo and no route (podman --network none, also
#      checked from outside as outer-fence-network-none);
#    - OpenShell's seccomp user-notification broker (Seccomp: 2) answers every INET socket operation
#      first: TCP and UDP connect() -> EACCES, a destination-bearing UDP send that is not DNS ->
#      EDESTADDRREQ, any other INET socket type (raw, ICMP) -> EPROTONOSUPPORT at socket().
#    Only these exact answers pass.
with open('/proc/net/dev') as f:
    ifs = [ln.split(':')[0].strip() for ln in f.readlines()[2:]]
non_lo = [i for i in ifs if i != 'lo']
with open('/proc/net/route') as f:
    routes = len(f.readlines()) - 1
with open('/proc/net/ipv6_route') as f:
    routes6 = [ln.split()[-1] for ln in f if ln.split()[-1] != 'lo']
with open('/proc/self/status') as f:
    status = dict(ln.split(':', 1) for ln in f if ':' in ln)
seccomp = status.get('Seccomp', '').strip()
nnp = status.get('NoNewPrivs', '').strip()
capeff = status.get('CapEff', '').strip() or 'f'
c4 = connect(('1.1.1.1', 443))            # install-agnostic: allow=ip-port (public anycast probe target)
c6 = connect(('2606:4700:4700::1111', 443), family=socket.AF_INET6)
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    u.connect(('1.1.1.1', 53))           # install-agnostic: allow=ip-port
    uc = 'CONNECTED'
except OSError as e:
    uc = errname(e)
finally:
    u.close()
us = connect(('1.1.1.1', 53), kind=socket.SOCK_DGRAM)   # install-agnostic: allow=ip-port
raw = sock_create(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
icmp = sock_create(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
ok = (not non_lo and routes == 0 and not routes6 and seccomp == '2' and nnp == '1' and int(capeff, 16) == 0
      and c4 == 'EACCES' and c6 == 'EACCES' and uc == 'EACCES' and us == 'EDESTADDRREQ'
      and raw == 'EPROTONOSUPPORT' and icmp == 'EPROTONOSUPPORT')
check('direct-network-blocked', ok,
      f"non-lo interfaces={non_lo or 'none'} ipv4 routes={routes} ipv6 non-lo routes={len(routes6)} "
      f"Seccomp={seccomp} NoNewPrivs={nnp} CapEff={capeff} "
      f"tcp4 connect -> {c4} tcp6 connect -> {c6} udp connect -> {uc} udp sendto -> {us} "
      f"raw socket -> {raw} icmp socket -> {icmp}")

# 4. A control operation on the session socket, with a valid token, gets the explicit forbidden reply.
r = sock({'token': TOKEN, 'type': 'approve', 'payload': {}})
check('control-op-rejected', r == '{"ok": false, "error": "forbidden"}', f'reply={r}')

# 5. wsd's control socket is not reachable, and /run/hz holds no socket but the session's own two.
sockets = []
for entry in sorted(os.listdir(RUN)):
    try:
        if stat.S_ISSOCK(os.lstat(f'{RUN}/{entry}').st_mode):
            sockets.append(entry)
    except OSError:
        sockets.append(f'{entry}?')
gone = open_result(cfg['wsd_socket']) in ('ENOENT', 'EACCES', 'EPERM')
check('wsd-socket-absent', gone and set(sockets) <= {'s.sock', 'p.sock'},
      f"wsd socket reachable={not gone}; sockets in {RUN}: {' '.join(sockets) or 'none'}")

# 6. The allowlisted control is reachable, through OpenShell's TLS-terminating proxy (its CA issues).
cu, issuer = curl(cfg['allowed'])
ok = cu.returncode == 0 and 'OpenShell Sandbox CA' in issuer and 'http=000' not in cu.stdout
check('allowlisted-host-reachable', ok, f"curl rc={cu.returncode} {cu.stdout} issuer='{issuer}'")

# 6b. The model host's policy names only the CLI binary, and OpenShell also authorizes a binary's
#     executable ancestors. So curl reaches it only from inside the agent's own process tree: on the
#     agent path it must be reachable (through the proxy), on the separate exec path refused. The host
#     checks the supervisor log for the matching ALLOWED/DENIED line.
cu, issuer = curl(cfg['model_host'])
if PATH == 'agent':
    ok = cu.returncode == 0 and 'OpenShell Sandbox CA' in issuer
else:
    ok = cu.returncode == 7 and 'Permission denied' in cu.stderr
check(f'model-host-{PATH}-path', ok,
      f"{cfg['model_host']}: curl rc={cu.returncode} {cu.stdout} issuer='{issuer}' "
      f"(expect {'reachable via the CLI ancestor' if PATH == 'agent' else 'refused: no CLI ancestor'})")

# 7. A hook event with the valid token is accepted.
r = sock({'token': TOKEN, 'type': 'hook_event', 'payload': {}})
check('hook-event-accepted', r == '{"ok": true}', f'reply={r}')

# 8. Environment: only the launcher's allowlist, OpenShell's fixed secret-free set and the shell's own;
#    on the agent path also the pinned CLI's tool variables (all from the host, by name).
extra = sorted(set(os.environ) - set(cfg['env_allowed']))
user_env = set(json.loads(os.environ.get('OPENSHELL_USER_ENVIRONMENT', '{}')))
extra += sorted(f'USER_ENVIRONMENT:{k}' for k in user_env - set(cfg['env_user']))
check('host-env-not-inherited', not extra, f"{PATH} path, unexpected vars: {' '.join(extra) or 'none'}")

# 9. OpenShell's own control material is not readable by the workload.
paths = ['/.openshell/channel/sandbox/bootstrap.json', '/.openshell/channel/sandbox/server.key',
         '/run/secrets', '/etc/openshell/auth/sandbox.jwt', '/etc/openshell/tls/client/tls.key']
res = {p: open_result(p) for p in paths}
#    A directory that opens (EISDIR) fails: what it holds is unknown. S5 saw /run/secrets give EACCES
#    under OpenShell and ENOENT without it, never EISDIR.
check('openshell-control-material-unreadable', all(v in ('ENOENT', 'EACCES', 'EPERM') for v in res.values()),
      ' '.join(f'{p}->{v}' for p, v in res.items()))

# 10. Other accounts (§7): each present or unknown login file, by configured and canonical path, gives
#     ENOENT/EACCES; an absent one gives ENOENT; the canary outside every bind gives ENOENT/EACCES; each
#     chosen login file matches the host's and is read-only here.
ev, ok = [], True
for o in cfg['other_accounts']:
    want = ('ENOENT',) if o['class'] == 'absent' else ('ENOENT', 'EACCES', 'EPERM')
    for p in o['paths']:
        r = open_result(p)
        ok &= r in want
        ev.append(f"{o['account']}/{o['class']}:{p}->{r}")
r = open_result(cfg['oa_canary'])
ok &= r in ('ENOENT', 'EACCES', 'EPERM')
ev.append(f"canary->{r}")
for c in cfg['chosen']:
    try:
        with open(c['path'], 'rb') as f:
            same = hashlib.sha256(f.read()).hexdigest() == c['sha256']
    except OSError as e:
        same = f'error {errname(e)}'
    w = open_result(c['path'], 'ab')
    ok &= same is True and w in ('EROFS', 'EACCES', 'EPERM')
    ev.append(f"chosen:{c['path']} sha256-match={same} write->{w}")
check('other-accounts', ok and bool(cfg['chosen']), '; '.join(ev))
# 11. Probe protection (§7, D23). Nothing in the workload may trace this probe or write its memory: a
#     child of this probe (the case Yama's scope 1 would allow) can't be attached to and its memory
#     can't be opened for writing. The probe's own files are read-only. The host checks the same
#     denial from outside (Yama's scope, no capability anywhere in the workload).
PTRACE_ATTACH, PTRACE_DETACH = 16, 17
libc = ctypes.CDLL(None, use_errno=True)
libc.ptrace.restype = ctypes.c_long
libc.ptrace.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'])
try:
    if libc.ptrace(PTRACE_ATTACH, child.pid, None, None) == 0:
        attach = 'ATTACHED'
        libc.ptrace(PTRACE_DETACH, child.pid, None, None)
    else:
        attach = errno.errorcode.get(ctypes.get_errno(), str(ctypes.get_errno()))
    mem = open_result(f'/proc/{child.pid}/mem', 'r+b')
finally:
    child.kill()
    child.wait()
check('probe-tamper-denied', attach in ('EPERM', 'EACCES', 'ENOSYS') and mem in ('EACCES', 'EPERM'),
      f'ptrace(ATTACH, own child) -> {attach}; open(/proc/<child>/mem, rw) -> {mem}')
own = [f'{RUN}/hz-tamper', f'{RUN}/probes.py', f'{RUN}/token', f'{RUN}/agent-probe.json']
res = {p: open_result(p, 'ab') for p in own}
check('probe-files-readonly', all(v in ('EROFS', 'EACCES', 'EPERM') for v in res.values()),
      ' '.join(f'{p}->{v}' for p, v in res.items()))
# The host freezes the workload's cgroup to check its tasks: the workload must not be able to thaw it,
# nor move a task out of it. Its cgroupfs is absent or mounted read-only, and its controls can't be
# opened for writing (r+, so a missing control is never created).
CGROUPFS = '/sys/fs/cgroup'
try:
    mount = 'read-only' if os.statvfs(CGROUPFS).f_flag & os.ST_RDONLY else 'READ-WRITE'
except FileNotFoundError:
    mount = 'absent'
except OSError as e:
    mount = f'error {errname(e)}'
ctl = {}
for name in ('cgroup.freeze', 'cgroup.procs'):
    try:
        open(f'{CGROUPFS}/{name}', 'r+b').close()
        ctl[name] = 'OPENED'
    except OSError as e:
        ctl[name] = errname(e)
check('cgroupfs-readonly', mount in ('absent', 'read-only')
      and all(v in ('EROFS', 'EACCES', 'ENOENT') for v in ctl.values()),
      f'{CGROUPFS}: {mount}; ' + ' '.join(f'{n}->{v}' for n, v in ctl.items()))
print(f'DONE {rc}', flush=True)     # the host passes only output that ends here
report({'done': rc})
if AGENT:
    # The host checks this process again at done, by its pidfd: stay alive until it acknowledges.
    channel.settimeout(10)
    try:
        channel.recv(16)
    except OSError:
        pass
sys.exit(rc)
