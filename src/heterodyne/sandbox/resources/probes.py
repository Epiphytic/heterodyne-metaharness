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
What the probe reads from a program it runs comes back on a socket, never a pipe (see `captured`).

Usage: python3 -I /run/hz/probes.py < input        (exec path)
       python3 -I /run/hz/probes.py --agent        (agent path)
Standard library only.
The probe never holds a value the agent can't read itself (Task 9B): at Yama's scope 1 the agent may read
this process's memory.
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
import time

RUN = '/run/hz'
CURL = '/usr/bin/curl'      # by path: the agent's PATH can't put another program in its place
# Every curl run starts with -q, so no .curlrc applies: the home is the agent's, and a curlrc there could
# change what curl does or prints.
CAPTURE_SECONDS = 30       # one deadline for a captured run: the program and the read of its output
CAPTURE_BYTES = 1 << 20     # the most output a captured run may write; past it the run fails
TIMED_OUT = 'timeout'       # a captured run's status past its deadline; it never equals an exit status
OVERFLOW = 'overflow'       # a captured run's status when output past CAPTURE_BYTES was discarded
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


def captured(argv, seconds=CAPTURE_SECONDS, keep=CAPTURE_BYTES):
    """Run argv; return its exit status and its stdout and stderr together. The output comes back on a
    socket pair, never a pipe: any process with this UID can reopen a pipe through /proc/<pid>/fd and
    write into it, and a socket can't be reopened that way (ENXIO). So what this returns is only what
    argv wrote (D23, Amendment 1). The output is read while argv runs, so none can fill the socket's
    buffer and stall it, and only its last `keep` bytes are kept. One deadline covers the run and the
    read: past it, argv is killed and the status is TIMED_OUT, whatever argv exited with. argv is always
    reaped. Output discarded for the bound makes the status OVERFLOW (Codex 9C r2): what was dropped can't
    be checked, so the run fails, though its tail is still returned as evidence."""
    deadline = time.monotonic() + seconds
    out, dropped = b'', False
    ours, theirs = socket.socketpair()
    with ours:
        with theirs:                                    # the parent's copy closes once argv has its own
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=theirs.fileno(),
                                     stderr=theirs.fileno())
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError
                ours.settimeout(left)
                chunk = ours.recv(65536)
                if not chunk:
                    break
                out += chunk
                if len(out) > keep:
                    out, dropped = out[-keep:], True
            rc = child.wait(timeout=max(deadline - time.monotonic(), 0.001))
        except (TimeoutError, subprocess.TimeoutExpired):
            rc = TIMED_OUT
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    if dropped and rc != TIMED_OUT:
        rc = OVERFLOW
    return rc, out.decode('utf-8', 'replace')


def http_status(out):
    """curl's write-out record, http=DDD, when it is the output's one such line and its last, complete
    (newline-terminated), three ASCII digits and nonzero; else ''. Anything else fails the reach."""
    lines = out.split('\n')
    records = [ln for ln in lines if ln.startswith('http=')]
    code = records[0][5:] if len(records) == 1 else ''
    complete = lines[-2:] == [f'http={code}', '']
    if complete and len(code) == 3 and all(ch in '0123456789' for ch in code) and code != '000':
        return f'http={code}'
    return ''


def issuer_of(out):
    """The issuer in curl's verbose certificate block: the first `*  issuer:` line among the `*  ` lines
    after the output's one `* Server certificate:` line; else ''. Response headers (`< `) and request lines
    (`> `) are never read, so a server can't supply it (Codex 9C r2). No block, or more than one, fails."""
    lines = out.split('\n')
    starts = [i for i, ln in enumerate(lines) if ln == '* Server certificate:']
    if len(starts) != 1:
        return ''
    for ln in lines[starts[0] + 1:]:
        if not ln.startswith('*  '):
            break
        if ln.startswith('*  issuer:'):
            return ln[len('*  issuer:'):].strip()
    return ''


def curl(host):
    rc, out = captured([CURL, '-q', '-sv', '-o', '/dev/null', '--max-time', '10',
                        '-w', '\nhttp=%{http_code}\n', f'https://{host}/'])
    return rc, out, issuer_of(out), http_status(out)


def unescape(field):
    """A mountinfo path decoded: the kernel writes a space, tab, newline and backslash in it as `\\040`,
    `\\011`, `\\012` and `\\134`."""
    escapes = {'\\040': ' ', '\\011': '\t', '\\012': '\n', '\\134': '\\'}
    out, i = [], 0
    while i < len(field):
        if field[i:i + 4] in escapes:
            out.append(escapes[field[i:i + 4]])
            i += 4
        else:
            out.append(field[i])
            i += 1
    return ''.join(out)


def mountinfo_problem(text, install):
    """None when `install` is the mount point of exactly one line of `text` (/proc/self/mountinfo), that
    mount is read-only with no `shared:` or `master:` tag, and no mount point lies below it; else the
    MI_* reason. Paths are compared decoded, by whole component. A line that can't be parsed could be
    any mount, so it counts as one more at the install."""
    mine, below = [], False
    for line in text.split('\n'):
        if not line:
            continue
        fields = line.split(' ')
        if len(fields) < 7 or '-' not in fields[6:]:
            return 'MI_NOT_ONE_MOUNT'
        point = unescape(fields[4])
        if point == install:
            mine.append(fields)
        elif point.startswith(install.rstrip('/') + '/'):
            below = True
    if len(mine) != 1:
        return 'MI_NOT_ONE_MOUNT'
    fields = mine[0]
    if 'ro' not in fields[5].split(','):
        return 'MI_NOT_READ_ONLY'
    if any(t.startswith(('shared:', 'master:')) for t in fields[6:fields.index('-', 6)]):
        return 'MI_PROPAGATES'
    return 'MI_SUBMOUNT' if below else None


def listing(dir_fd, prefix, found, limit, depth=0):
    """Every entry below the directory `dir_fd`, by fd and following no link, into `found` as the record
    writes it: [path, kind, mode, size, nlink, dev, ino, ctime_ns, link target or None]. Past `limit`
    entries or 64 levels it stops: the listing already differs."""
    if depth > 64:
        raise OSError(errno.ELOOP, 'too deep')
    with os.scandir(dir_fd) as entries:
        names = sorted(e.name for e in entries)
    for name in names:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        kind = ('dir' if stat.S_ISDIR(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode)
                else 'link' if stat.S_ISLNK(st.st_mode) else 'other')
        path = prefix + name
        found[path] = [path, kind, stat.S_IMODE(st.st_mode), st.st_size, st.st_nlink, st.st_dev, st.st_ino,
                       st.st_ctime_ns, os.readlink(name, dir_fd=dir_fd) if kind == 'link' else None]
        if len(found) > limit:
            raise OSError(errno.E2BIG, 'more entries than the record')
        if kind == 'dir':
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
            try:
                listing(child, path + '/', found, limit, depth + 1)
            finally:
                os.close(child)


def walk_problem(record):
    """None when the install path opens, following no link, as a directory with the record's root
    `(dev, ino, mode, ctime_ns)`, and a walk by fd finds exactly the record's entries, field by field;
    else MI_ROOT_DIFFERS or MI_LISTING_DIFFERS. A bind of another tree over the root, or a change made
    after the host verified it, shows here."""
    try:
        root = os.open(record['path'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return 'MI_ROOT_DIFFERS'
    try:
        st = os.fstat(root)
        if [st.st_dev, st.st_ino, stat.S_IMODE(st.st_mode), st.st_ctime_ns] != record['root']:
            return 'MI_ROOT_DIFFERS'
        want = {e[0]: e for e in record['entries']}
        found = {}
        try:
            listing(root, '', found, len(want))
        except OSError:
            return 'MI_LISTING_DIFFERS'
        return None if found == want else 'MI_LISTING_DIFFERS'
    finally:
        os.close(root)


def empty_problem(path):
    """None when `path` is a directory, not a link, with no entries, on a read-only mount (E25); else the
    ER_* reason. A directory that can't be listed can't be shown empty."""
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            return 'ER_MISSING'
    except OSError:
        return 'ER_MISSING'
    try:
        if os.listdir(path):
            return 'ER_NOT_EMPTY'
    except OSError:
        return 'ER_NOT_EMPTY'
    try:
        return None if os.statvfs(path).f_flag & os.ST_RDONLY else 'ER_NOT_READ_ONLY'
    except OSError:
        return 'ER_NOT_READ_ONLY'


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
cu_rc, cu_out = captured([CURL, '-q', '-sv', '-o', '/dev/null', '--max-time', '8', f'https://{host}/'])
ok = marker and c == 'EACCES' and cu_rc == 7 and 'Permission denied' in cu_out
check('non-allowlisted-host-blocked', ok,
      f'{host} -> {",".join(addrs)} (policy-DNS synthetic={marker}) connect -> {c}; curl rc={cu_rc}')

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
cu_rc, cu_out, issuer, status = curl(cfg['allowed'])
ok = cu_rc == 0 and 'OpenShell Sandbox CA' in issuer and status != ''
check('allowlisted-host-reachable', ok,
      f"curl rc={cu_rc} {status or 'no valid http= record'} issuer='{issuer}'")

# 6b. The model host's policy names only the CLI binary, and OpenShell also authorizes a binary's
#     executable ancestors. So curl reaches it only from inside the agent's own process tree: on the
#     agent path it must be reachable (through the proxy), on the separate exec path refused. The host
#     checks the supervisor log for the matching ALLOWED/DENIED line.
cu_rc, cu_out, issuer, status = curl(cfg['model_host'])
if PATH == 'agent':
    ok = cu_rc == 0 and 'OpenShell Sandbox CA' in issuer and status != ''
else:
    ok = cu_rc == 7 and 'Permission denied' in cu_out
check(f'model-host-{PATH}-path', ok,
      f"{cfg['model_host']}: curl rc={cu_rc} {status or 'no valid http= record'} issuer='{issuer}' "
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

# 10b. The managed install (plan 4c T8, E24): the install path is one read-only mount that propagates
#      nothing, with nothing mounted below it (the backend's bind is recursive, so this keeps a submount
#      out), and a walk by fd finds exactly the tree the host verified (the record, read-only here).
#      E25: each of the adapter's exec directories is an empty directory on a read-only mount.
try:
    with open(cfg['install_record']) as f:
        record = json.load(f)
except (OSError, ValueError):
    record = None
if record is None:
    reason = 'MI_ROOT_DIFFERS'                  # nothing to compare the tree against
else:
    try:
        with open('/proc/self/mountinfo') as f:
            reason = mountinfo_problem(f.read(), record['path'])
    except OSError:
        reason = 'MI_NOT_ONE_MOUNT'
    reason = reason or walk_problem(record)
check('managed-install', reason is None, reason or 'ok')
empty = [empty_problem(p) for p in (record or {}).get('empty_read_only', [])]
reason = 'ER_MISSING' if record is None else next((r for r in empty if r), None)
check('empty-read-only', reason is None, reason or 'ok')
# 11. Probe protection (§7, D23, Amendment 1). Nothing in the workload may trace this probe or write its
#     memory. OpenShell's child seccomp filter answers ptrace, process_vm_writev, pidfd_getfd and unshare
#     with EPERM, whatever Yama allows (it allows a parent, as here). Landlock keeps /proc read-only, so
#     writing a child's mem, or this process's own comm, gives EACCES. Only those answers pass. The host
#     checks the filters and Yama's scope from outside.
PTRACE_ATTACH, PTRACE_DETACH = 16, 17
libc = ctypes.CDLL(None, use_errno=True)
libc.ptrace.restype = ctypes.c_long
libc.ptrace.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
libc.process_vm_writev.restype = ctypes.c_ssize_t
libc.process_vm_writev.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                   ctypes.c_ulong, ctypes.c_ulong]


def libc_call(name, *args):
    """A libc call's outcome: OK, or the name of the errno it failed with."""
    if getattr(libc, name)(*args) != -1:
        return 'OK'
    code = ctypes.get_errno()
    return errno.errorcode.get(code, str(code))


child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'])
try:
    attach = libc_call('ptrace', PTRACE_ATTACH, child.pid, None, None)
    if attach == 'OK':
        libc.ptrace(PTRACE_DETACH, child.pid, None, None)
    vm = libc_call('process_vm_writev', child.pid, None, 0, None, 0, 0)   # no iovec: writes nothing
    mem = open_result(f'/proc/{child.pid}/mem', 'r+b')
finally:
    child.kill()
    child.wait()
getfd = libc_call('pidfd_getfd', -1, 0, 0)        # unfiltered: EBADF
unshare = libc_call('unshare', 0)                 # unfiltered: a no-op, OK
check('probe-tamper-denied', {attach, vm, getfd, unshare} == {'EPERM'} and mem == 'EACCES',
      f'ptrace(ATTACH, own child) -> {attach}; process_vm_writev -> {vm}; pidfd_getfd -> {getfd}; '
      f'unshare(0) -> {unshare}; open(/proc/<child>/mem, rw) -> {mem}')
comm = open_result('/proc/self/comm', 'r+b')
check('proc-readonly', comm == 'EACCES', f'open(/proc/self/comm, rw) -> {comm}')
own = [f'{RUN}/hz-tamper', f'{RUN}/probes.py', f'{RUN}/token', f'{RUN}/agent-probe.json',
       f'{RUN}/install-record.json']
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
