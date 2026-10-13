"""Verdicts on the live probe-protection checks (Task 15). Each takes a check's exit status and output, and
returns "" only when the check ran to completion and every operation it reports got exactly the answer
the enforcing layer gives (D23, Task 9B); otherwise it returns the reason. A status or any other errno is
not a refusal: a missing command, a gone process or a garbled line must not pass for one. Kept apart
from the live module so the normal suite can test them offline. A reason reaches pytest's output, so it
quotes only lines of the checks' own vocabulary (`shown`)."""

import re

# OpenShell's child filter answers the ptrace family and pidfd_getfd with EPERM; Landlock keeps /proc
# read-only, so a mem write open gives EACCES (Task 9B's table).
MEMORY_ANSWERS = {"attach": "EPERM", "readv": "EPERM", "writev": "EPERM", "mem": "EACCES"}
# /run/hz, which holds the probe, its configuration, the token, the hook shim and both sockets, is a
# read-only bind under Landlock's read_only: a write gives EROFS, or EACCES (Task 9C's table).
FILE_OPS = (("create", "hz-new"), ("mkdir", "hz-dir"), ("bind", "p.sock"), ("append", "token"),
            ("append", "probes.py"), ("append", "agent-probe.json"), ("append", "shim.py"),
            ("append", "shim.json"), ("rename", "token"), ("unlink", "agent-probe.json"),
            ("unlink", "s.sock"))
FILE_ANSWERS = ("EROFS", "EACCES")
# The child filter denies unshare whatever its flags: unshare(0), a no-op otherwise, shows the filter.
NS_OPS = ("unshare-none", "unshare-user-mount")
MOUNT_NS = re.compile(r"mnt:\[\d+\]")
# The overlay's steps and the answer that is each one's refusal: the filter (unshare), Landlock's
# read-only /proc (the uid map) and Landlock's mount denial (each bind).
OVERLAY_REFUSALS = {"unshare": "EPERM", "maps": "EACCES", "bind-probe": "EPERM", "bind-cli": "EPERM"}
SENT = ("OK", "EPIPE", "ECONNRESET")       # the channel may reject the peer before its send lands
SAFE = re.compile(r"[A-Za-z0-9_.:=\[\] -]{0,100}")


def shown(line: str) -> str:
    """A line for a reason: quoted when it is the checks' own vocabulary, else only described."""
    return repr(line) if SAFE.fullmatch(line) else "an unrecognised line"


def memory_access_refused(rc: int, out: str) -> str:
    """The memory check prints `targets <pid>...` (every process it can see but itself), `child <pid>`
    (its own child, one of them), `getfd <errno>`, then `<op> <pid> <errno>` for each operation on each
    target, then `done`. Each target must have exactly one of each operation, each with its answer."""
    lines = out.splitlines()
    if rc != 0 or len(lines) < 4 or lines[-1] != "done":
        return f"the memory check did not run to completion (exit {rc})"
    head, child, getfd = lines[0].split(), lines[1].split(), lines[2]
    if head[:1] != ["targets"] or not head[1:] or not all(p.isdigit() for p in head[1:]) \
            or len(set(head[1:])) != len(head) - 1:
        return "the memory check did not list its targets"
    targets = set(head[1:])
    if len(child) != 2 or child[0] != "child" or child[1] not in targets:
        return "the memory check did not name its child among its targets"
    if getfd != "getfd EPERM":
        return f"pidfd_getfd was not refused by the filter: {shown(getfd)}"
    seen: set[tuple[str, str]] = set()
    for line in lines[3:-1]:
        parts = line.split()
        if len(parts) != 3 or parts[0] not in MEMORY_ANSWERS or parts[1] not in targets:
            return f"not a result: {shown(line)}"
        if parts[2] != MEMORY_ANSWERS[parts[0]]:
            return f"not the enforcing answer ({MEMORY_ANSWERS[parts[0]]}): {shown(line)}"
        if (parts[0], parts[1]) in seen:
            return f"an operation reported twice: {shown(line)}"
        seen.add((parts[0], parts[1]))
    if seen != {(op, pid) for op in MEMORY_ANSWERS for pid in targets}:
        return "a target was not checked by every operation"
    return ""


def file_writes_refused(rc: int, out: str) -> str:
    """The file check prints `<op> <name> <answer>` for exactly FILE_OPS, in order, then `done`. The answer
    is OK, an errno name, or MISSING or PRESENT when the target was not as the operation needs it."""
    lines = out.splitlines()
    if rc != 0 or lines[-1:] != ["done"]:
        return f"the file check did not run to completion (exit {rc})"
    results = [line.rsplit(" ", 1) for line in lines[:-1]]
    if [tuple(r[0].split(" ", 1)) for r in results] != list(FILE_OPS) or any(len(r) != 2 for r in results):
        return "the file check did not report exactly its operations"
    for line, (_, answer) in zip(lines[:-1], results, strict=True):
        if answer not in FILE_ANSWERS:
            return f"a file operation was not refused by the read-only bind: {shown(line)}"
    return ""


def namespaces_refused(rc: int, out: str) -> str:
    """The namespace check prints `mnt <ns>`, `<op> <answer>` for each of NS_OPS, `mnt <ns>` again, then
    `done`. Each unshare must be refused by the filter (EPERM), and the namespace must be unchanged."""
    lines = out.splitlines()
    if rc != 0 or len(lines) != len(NS_OPS) + 3 or lines[-1] != "done":
        return f"the namespace check did not run to completion (exit {rc})"
    before, after = lines[0].split(" ", 1), lines[-2].split(" ", 1)
    if before[0] != "mnt" or len(before) != 2 or not MOUNT_NS.fullmatch(before[1]) or after != before:
        return "the mount namespace was not read, or it changed"
    for op, line in zip(NS_OPS, lines[1:-2], strict=True):
        if line != f"{op} EPERM":
            return f"an unshare was not refused by the filter: {shown(line)}"
    return ""


def overlay_outcome(rc: int, out: str) -> str:
    """The overlay check prints `forged=written`, then `<step>=<answer>` for each step it reached (the
    unshare, the uid map, the bind over the probe and the bind over the CLI), each OK or an errno name;
    after them all, the forged probe's `connect=` and `sent=` and the run's `run=<status>`; then `done`.
    Returns "refused" when the last step reached got its enforcing answer, "ran" when the forged probe
    ran from the namespace and reached the channel (then only the channel can refuse it), and otherwise
    the reason: any other answer, a step after a refusal, or a forged probe that never got there."""
    lines = out.splitlines()
    if rc != 0 or lines[:1] != ["forged=written"] or lines[-1:] != ["done"]:
        return f"the overlay check did not run to completion (exit {rc})"
    stages = [line.partition("=") for line in lines[1:-1]]
    if any(not sep for _, sep, _ in stages):
        return "the overlay check printed an unexpected line"
    for i, step in enumerate(OVERLAY_REFUSALS):
        if i >= len(stages) or stages[i][0] != step:
            return f"the overlay check stopped before {step}"
        answer = stages[i][2]
        if answer == OVERLAY_REFUSALS[step]:
            return "refused" if i == len(stages) - 1 else f"a step ran after {step} was refused"
        if answer != "OK":
            return f"{step}: {shown(answer)} is not its enforcing answer ({OVERLAY_REFUSALS[step]})"
    rest = {name: value for name, _, value in stages[len(OVERLAY_REFUSALS):]}
    if [name for name, _, _ in stages[len(OVERLAY_REFUSALS):]] != ["connect", "sent", "run"]:
        return "the forged probe's run was not reported"
    if rest["connect"] != "OK" or rest["sent"] not in SENT or rest["run"] != "0":
        return (f"the forged probe did not reach the channel: connect={shown(rest['connect'])} "
                f"sent={shown(rest['sent'])} run={shown(rest['run'])}")
    return "ran"


def outputs_unreachable(rc: int, out: str) -> str:
    """The output-channel check prints `probe <pid>` once it finds the agent-path probe, then `curl <pid>
    <fd 1> <fd 2>` for each curl it saw that descends from the probe, each fd as `<kind>:<result of a write
    open>` or `gone` (the process ended first), or `curl <pid> gone` (it ended before it could be placed),
    then `probe <pid> exited` once the probe has, and `done`. At least one curl must have been seen whole,
    and every fd seen must be a socket that refused the open (ENXIO)."""
    lines = out.splitlines()
    if rc != 0 or lines[-1:] != ["done"]:
        return f"the output-channel check did not run to completion (exit {rc})"
    first = lines[0].split() if lines else []
    if len(first) != 2 or first[0] != "probe" or not first[1].isdigit():
        return "the output-channel check never found the probe"
    if lines[-2:-1] != [f"probe {first[1]} exited"]:
        return "the output-channel check stopped before the probe exited"
    whole = 0
    for line in lines[1:-2]:
        parts = line.split()
        if len(parts) not in (3, 4) or parts[0] != "curl" or not parts[1].isdigit() \
                or (len(parts) == 3 and parts[2] != "gone"):
            return f"not a result: {shown(line)}"
        fds = [fd for fd in parts[2:] if fd != "gone"]
        if any(fd != "socket:ENXIO" for fd in fds):
            return f"a curl output is reopenable, not a socket, or unreadable: {shown(line)}"
        whole += len(fds) == 2
    return "" if whole else "the check saw no curl process whole while the probe ran"
