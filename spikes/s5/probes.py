"""Inside the sandbox: the §7 launch self-test probes under OpenShell. Spike S5.

Ported from spikes/s3/probes.sh. Each probe prints PASS/FAIL with its evidence, and only the
specific enforcement passes: a generic failure (missing canary, dead socket, timeout) FAILs.
Under OpenShell the egress refusal is a connect() EACCES from the supervisor's transparent
interception (not a CONNECT 403), so the marker is the policy-DNS synthetic address, and the
launcher checks the supervisor's own log afterwards (as S3 checked proxy.log).
Run twice per launch: by a separate `sandbox exec` (path "exec") and by the agent itself through
its own tool (path "agent"), so the agent's process tree is probed too. On the agent path the config
comes from the read-only /run/hz/agent-probe.json, and every result also goes to the host over
/run/hz/probe.sock, where the launcher verifies this process before counting anything it sends.
Usage: probes.py < JSON from launch.py          (exec path)
       python3 -I /run/hz/probes.py --agent       (agent path)
"""
import errno
import hashlib
import ipaddress
import json
import os
import socket
import subprocess
import sys

AGENT = sys.argv[1:] == ['--agent']
if AGENT:
    with open('/run/hz/agent-probe.json') as f:
        cfg = json.load(f)
    channel = socket.socket(socket.AF_UNIX)
    channel.connect('/run/hz/probe.sock')
else:
    cfg = json.load(sys.stdin)
TOKEN = cfg['token']
PATH = cfg.get('path', 'exec')
SYNTHETIC = ipaddress.ip_network('198.18.0.0/15')   # OpenShell policy DNS answers from this range
rc = 0


def report(msg: dict) -> None:
    if AGENT:
        channel.sendall((json.dumps(msg) + '\n').encode())


def check(name: str, ok: bool, evidence: str) -> None:
    global rc
    print(f"{'PASS' if ok else 'FAIL'} {name} [{evidence}]", flush=True)
    report({'check': name, 'ok': ok, 'evidence': evidence})
    rc = rc or (0 if ok else 1)


def open_result(path: str, mode: str = 'rb') -> str:
    try:
        open(path, mode).close()
        return 'READABLE' if 'r' in mode else 'WRITABLE'
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))


def connect(addr: tuple, family=socket.AF_INET, kind=socket.SOCK_STREAM) -> str:
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
        return errno.errorcode.get(e.errno, str(e.errno))
    finally:
        s.close()


def sock(req: dict) -> str:
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(3)
        s.connect('/run/hz/session.sock')
        s.sendall((json.dumps(req) + '\n').encode())
        return s.makefile().readline().strip()
    except OSError as e:
        return f'socket error {errno.errorcode.get(e.errno, e.errno)}'


# 1. Real home: the canary (readable outside just before) gives ENOENT or EACCES/EPERM here.
r = open_result(cfg['real_home_canary'])
check('real-home-canary-unreadable', r in ('ENOENT', 'EACCES', 'EPERM'), f"open({cfg['real_home_canary']}) -> {r}")

# 2. Egress: the denied host resolves through OpenShell's policy DNS (synthetic address = marker),
#    and the connect is refused with EACCES by the supervisor's interception. The launcher then
#    requires the supervisor log's DENIED line for this binary and host.
host = cfg['denied']
try:
    addrs = sorted({a[4][0] for a in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)})
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

# 3. Direct network. Under OpenShell this is two layers, and both must show:
#    - the kernel fence: the workload's netns has only lo and no route (podman --network none,
#      checked from outside as outer-fence-network-none);
#    - OpenShell's seccomp user-notification broker (Seccomp: 2) answers every INET socket op
#      before the kernel can: TCP and UDP connect() -> EACCES (TcpOpenDenial::PolicyDenied),
#      a destination-bearing UDP send that is not DNS -> EDESTADDRREQ, any other INET socket type
#      (raw, ICMP) -> EPROTONOSUPPORT at socket().
#    So the literal-address connect fails with EACCES, never "unreachable": a deviation from r14's
#    wording that needs an ADR amendment. Only these exact broker answers pass.
with open('/proc/net/dev') as f:
    ifs = [ln.split(':')[0].strip() for ln in f.readlines()[2:]]
non_lo = [i for i in ifs if i != 'lo']
with open('/proc/net/route') as f:
    routes = len(f.readlines()) - 1
with open('/proc/net/ipv6_route') as f:
    routes6 = [ln.split()[-1] for ln in f if ln.split()[-1] != 'lo']
status = dict(ln.split(':', 1) for ln in open('/proc/self/status') if ':' in ln)
seccomp = status.get('Seccomp', '').strip()
nnp = status.get('NoNewPrivs', '').strip()
capeff = status.get('CapEff', '').strip()


def sock_create(fam, kind, proto) -> str:
    try:
        socket.socket(fam, kind, proto).close()
        return 'CREATED'
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))


c4 = connect(('1.1.1.1', 443))            # install-agnostic: allow=ip-port (public anycast probe target)
c6 = connect(('2606:4700:4700::1111', 443), family=socket.AF_INET6)

u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    u.connect(('1.1.1.1', 53))           # install-agnostic: allow=ip-port
    uc = 'CONNECTED'
except OSError as e:
    uc = errno.errorcode.get(e.errno, str(e.errno))
finally:
    u.close()
us = connect(('1.1.1.1', 53), kind=socket.SOCK_DGRAM)   # install-agnostic: allow=ip-port
raw = sock_create(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
icmp = sock_create(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
ok = (not non_lo and routes == 0 and not routes6 and seccomp == '2' and nnp == '1' and int(capeff, 16) == 0
      and c4 == 'EACCES' and c6 == 'EACCES' and uc == 'EACCES' and us == 'EDESTADDRREQ'
      and raw == 'EPROTONOSUPPORT' and icmp == 'EPROTONOSUPPORT')
check('direct-network-blocked', ok,
      f"DEVIATION (EACCES by the OpenShell broker, not unreachable; ADR amendment needed): "
      f"non-lo interfaces={non_lo or 'none'} ipv4 routes={routes} ipv6 non-lo routes={len(routes6)} "
      f"Seccomp={seccomp} NoNewPrivs={nnp} CapEff={capeff} "
      f"tcp4 connect -> {c4} tcp6 connect -> {c6} udp connect -> {uc} udp sendto -> {us} "
      f"raw socket -> {raw} icmp socket -> {icmp}")

# 4. Control op on the session socket, with a valid token, gets the explicit forbidden reply.
r = sock({'token': TOKEN, 'type': 'approve', 'payload': {}})
check('control-op-rejected', r == '{"ok": false, "error": "forbidden"}', f'reply={r}')

# 5. The wsd socket is not bound in.
check('wsd-socket-absent', not os.path.exists('/run/hz/wsd.sock'),
      f"exists={os.path.exists('/run/hz/wsd.sock')}; /run/hz: {' '.join(sorted(os.listdir('/run/hz'))) if os.path.isdir('/run/hz') else 'absent'}")

# 6. Allowlisted control: reachable, through OpenShell's TLS-terminating proxy (issuer is its CA).
cu = subprocess.run(['curl', '-sv', '-o', '/dev/null', '--max-time', '10', '-w', 'http=%{http_code}',
                     f"https://{cfg['allowed']}/"], capture_output=True, text=True)
issuer = next((ln.split('issuer:')[1].strip() for ln in cu.stderr.splitlines() if 'issuer:' in ln), '')
ok = cu.returncode == 0 and 'OpenShell Sandbox CA' in issuer and 'http=000' not in cu.stdout
check('allowlisted-host-reachable', ok, f"curl rc={cu.returncode} {cu.stdout} issuer='{issuer}'")

# 6b. The model host's policy names only the CLI binary, and OpenShell also authorizes a binary's
#     executable ancestors. So curl reaches it only from inside the agent's own process tree: on
#     the agent path it must be reachable (through the proxy), on the separate exec path refused.
#     The launcher checks the supervisor log for the matching ALLOWED/DENIED line.
cu = subprocess.run(['curl', '-sv', '-o', '/dev/null', '--max-time', '10', '-w', 'http=%{http_code}',
                     f"https://{cfg['model_host']}/"], capture_output=True, text=True)
issuer = next((ln.split('issuer:')[1].strip() for ln in cu.stderr.splitlines() if 'issuer:' in ln), '')
if PATH == 'agent':
    ok = cu.returncode == 0 and 'OpenShell Sandbox CA' in issuer
else:
    ok = cu.returncode == 7 and 'Permission denied' in cu.stderr
check(f'model-host-{PATH}-path', ok, f"{cfg['model_host']}: curl rc={cu.returncode} {cu.stdout} issuer='{issuer}' "
      f"(expect {'reachable via the CLI ancestor' if PATH == 'agent' else 'refused: no CLI ancestor'})")

# 7. Hook event with the valid token is accepted.
r = sock({'token': TOKEN, 'type': 'hook_event', 'payload': {}})
check('hook-event-accepted', r == '{"ok": true}', f'reply={r}')

# 8. Environment: only the launcher's allowlist, OpenShell's fixed secret-free set and the shell's own;
#    on the agent path also the pinned CLI's tool variables (all from launch.py, by name).
extra = sorted(set(os.environ) - set(cfg['env_allowed']))
user_env = set(json.loads(os.environ.get('OPENSHELL_USER_ENVIRONMENT', '{}')))
extra += sorted(f'USER_ENVIRONMENT:{k}' for k in user_env - set(cfg['env_user']))
check('host-env-not-inherited', not extra, f"{PATH} path, unexpected vars: {' '.join(extra) or 'none'}")

# 9. (added) OpenShell's own control material is not readable by the workload.
paths = ['/.openshell/channel/sandbox/bootstrap.json', '/.openshell/channel/sandbox/server.key',
         '/run/secrets', '/etc/openshell/auth/sandbox.jwt', '/etc/openshell/tls/client/tls.key']
res = {p: open_result(p) for p in paths}
check('openshell-control-material-unreadable', all(v in ('ENOENT', 'EACCES', 'EPERM', 'EISDIR') and
      (v != 'EISDIR' or open_result(p + '/x') != 'READABLE') for p, v in res.items()),
      ' '.join(f'{p}->{v}' for p, v in res.items()))

# 10. Other accounts (revision 14): each present/unknown file, by configured and canonical path,
#     gives ENOENT/EACCES; an absent one gives ENOENT; the outside canary gives ENOENT/EACCES;
#     each chosen login file matches the host's and is read-only here.
ev, ok = [], True
for o in cfg['other_accounts']:
    want = ('ENOENT',) if o['class'] == 'absent' else ('ENOENT', 'EACCES', 'EPERM')
    for p in o['paths']:
        r = open_result(p)
        ok &= r in want
        ev.append(f"{o['account']}/{o['class']}:{p}->{r}")
r = open_result(cfg['oa_canary'])
ok &= r in ('ENOENT', 'EACCES', 'EPERM')
ev.append(f"canary:{cfg['oa_canary']}->{r}")
for c in cfg['chosen']:
    try:
        same = hashlib.sha256(open(c['path'], 'rb').read()).hexdigest() == c['sha256']
    except OSError as e:
        same = f'error {errno.errorcode.get(e.errno)}'
    w = open_result(c['path'], 'ab')
    ok &= same is True and w in ('EROFS', 'EACCES', 'EPERM')
    ev.append(f"chosen:{c['path']} sha256-match={same} write->{w}")
check('other-accounts', ok and bool(cfg['other_accounts']), '; '.join(ev))
report({'done': rc})
sys.exit(rc)
