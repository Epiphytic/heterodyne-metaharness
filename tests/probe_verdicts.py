"""Verdicts on the live probe-protection checks (Task 15). Each takes a check's exit status and output, and
returns "" only when the check ran to completion and every operation it reports got exactly the answer
the enforcing layer gives (D23, Task 9B); otherwise it returns the reason. A status or any other errno is
not a refusal: a missing command, a gone process or a garbled line must not pass for one. Kept apart
from the live module so the normal suite can test them offline. A reason reaches pytest's output, so it
renders only the checks' own vocabulary (`shown`); the self-test's and the channel's reasons are rendered
the same way, from their fixed wording (`launch_shown`, `rejection_shown`)."""

import errno
import re
from collections.abc import Collection

import pytest

from heterodyne.sandbox.openshell_selftest import AGENT_CHECKS, EXEC_CHECKS

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
SENT = ("OK", "EPIPE", "ECONNRESET")
# A curl output descriptor that can't be reopened: a socket whose write open gives ENXIO, or one whose link
# and write open the sandbox both refuse from outside the probe's domain (EACCES; team-lead, T15 live).
UNREACHABLE = ("socket:ENXIO", "hidden:EACCES")       # the channel may reject the peer before its send lands
UNRECOGNISED = "<unrecognised>"
# Codex T15 r2: a reason renders only words the checks print (operation and file names, steps, answers,
# errno names, descriptor kinds), numeric PIDs and statuses and mount-namespace IDs; any other piece, a
# secret or a 64-hex value among them, is replaced. A PID has at most 7 digits (pid_max is 2^22).
WORDS = frozenset({*MEMORY_ANSWERS, *(op for op, _ in FILE_OPS), *(name for _, name in FILE_OPS), *NS_OPS,
                   *OVERLAY_REFUSALS, "connect", "sent", "run", "forged", "written", "targets", "child",
                   "getfd", "curl", "probe", "mnt", "done", "exited", "running", "gone", "none", "OK",
                   "MISSING", "PRESENT", "OPENED", "TIMEOUT", "socket", "pipe", "anon_inode", "hidden",
                   "error", *errno.errorcode.values()})
NUMBER = re.compile(r"-?\d{1,7}")
NS_ID = re.compile(r"\[\d{1,12}\]")
SEPARATORS = frozenset({"", " ", "=", ":"})
MAX_PIECES = 24


def shown(line: str) -> str:
    """A line for a reason: each piece of it the checks' own vocabulary, any other piece replaced."""
    pieces = re.split(r"([ =:])", line)
    if len(pieces) > MAX_PIECES:
        return "an unrecognised line"
    return repr("".join(p if p in SEPARATORS or p in WORDS or NUMBER.fullmatch(p) or NS_ID.fullmatch(p)
                        else UNRECOGNISED for p in pieces))


def session_socket_checks(source: str) -> tuple[str, ...]:
    """The probes' checks that each rest on a request over the session socket (an `r = sock(...)` and the
    `check(...)` on its reply), in the order the probes run them: closing the socket fails exactly
    these. `source` is the probes' own (openshell_selftest.PROBES_SOURCE)."""
    names: list[str] = []
    pending = False
    for line in source.splitlines():
        if line.startswith("r = sock("):
            pending = True
        elif pending and (m := re.match(r"check\('([a-z0-9-]+)', r\b", line)):
            names.append(m[1])
            pending = False
    return tuple(names)


# The self-test's own words for each refusal (openshell_selftest, channel). A launch's reason is the
# runtime's ("launch self-test failed: ..."), which may add the agent path's freeze attempts.
SELFTEST_FAILED = "launch self-test failed: "
CHANNEL = "agent-path-channel: "
NOT_THE_PROBE = "not the probe"
ENV_REJECTED = "environment beyond the allowlist: "
NAMESPACE_REJECTED = "the probe or its CLI is outside the workload's user and mount namespaces"
# Every name a self-test failure gives: the probes' checks and the host's own (openshell_selftest).
FAILURES = frozenset({*EXEC_CHECKS, *AGENT_CHECKS, "probes", "probes-output", "probes-timeout",
                      "canary-precondition", "workload-namespaces", "workload-netns", "workload-cgroup",
                      "outer-fence-network-none", "agent-prompt", "proxy-logged-refusal",
                      "proxy-logged-allow", "proxy-logged-direct-deny", "agent-ancestry-allowed",
                      "exec-path-not-agent"})
CHANNEL_VERDICTS = frozenset({"the channel did not finish", "a peer that is not the probe connected",
                              "no verified probe run", "more than one probe run", "malformed result",
                              "the probe changed before it finished", "the probe did not finish cleanly",
                              "the probe did not finish in time"})
CHANNEL_LISTS = ("unexpected checks: ", "failed checks: ", "missing checks: ")
# A peer's rejection (channel.ProcVerifier and the channel's acceptor), each in its fixed wording.
REJECTIONS = frozenset({NOT_THE_PROBE, NAMESPACE_REJECTED, "traced", "the probe can gain privileges",
                        "netns is not the workload container's", "the workload's first process changed",
                        "the probe is outside the workload's cgroup",
                        "a workload thread ended while the workload was frozen",
                        "workload process evidence unreadable", "the workload scan did not finish",
                        "the workload could not be frozen", "the workload could not be thawed",
                        "too many peers", "peer credentials unreadable"})
WORKLOAD_PROCESS = re.compile(r"workload process \d{1,7}( \(thread \d{1,7}\))? (can gain privileges|"
                              r"holds capabilities|has no seccomp filter|has \d{1,3} seccomp filters; "
                              r"OpenShell gives every workload process \d{1,3})")
FREEZE_ATTEMPTS = re.compile(r" \(freeze attempts: \d{1,3}(, \d{1,3})*\)$")


def names_shown(text: str, known: Collection[str], sep: str = ", ") -> str:
    """A list of names: each one shown when it is in `known`, any other replaced."""
    return sep.join(n if n in known else UNRECOGNISED for n in text.split(sep))


def rejection_shown(reason: str, tool_vars: Collection[str] = ()) -> str:
    """A peer's rejection for a reason: its fixed wording, with only a PID or a variable named in
    `tool_vars` (the CLI's agent-only tool variables) kept from what it reports; the CLI's path, an
    unreadable entry's error and any other variable or wording are replaced (Codex T15 r2)."""
    if reason in REJECTIONS or WORKLOAD_PROCESS.fullmatch(reason):
        return reason
    if reason.startswith(ENV_REJECTED):
        return ENV_REJECTED + names_shown(reason.removeprefix(ENV_REJECTED), tool_vars, " ")
    if re.fullmatch(r"/proc/\d{1,7} unreadable \(.*\)", reason, re.DOTALL):
        return "/proc/<pid> unreadable"
    if re.fullmatch(r"no .* ancestor inside the workload netns", reason, re.DOTALL):
        return "no <cli> ancestor inside the workload netns"
    if reason.startswith("ptrace-scope: "):
        return "ptrace-scope: <refused>"
    return UNRECOGNISED


def launch_shown(said: str) -> str:
    """A launch's failure for a reason: the self-test's check names, the channel's verdict and the freeze
    attempts as given; any other name, verdict or launch failure (one may hold a path) replaced."""
    if not said.startswith(SELFTEST_FAILED):
        return "a launch failure outside the self-test"
    body = said.removeprefix(SELFTEST_FAILED)
    attempts = m[0] if (m := FREEZE_ATTEMPTS.search(body)) else ""
    body = body.removesuffix(attempts)
    if body.startswith("ptrace-scope: "):
        return SELFTEST_FAILED + "ptrace-scope: <refused>" + attempts
    if not body.startswith(CHANNEL):
        return SELFTEST_FAILED + names_shown(body, FAILURES) + attempts
    verdict = body.removeprefix(CHANNEL)
    listed = next((p for p in CHANNEL_LISTS if verdict.startswith(p)), None)
    if listed is not None:
        verdict = listed + names_shown(verdict.removeprefix(listed), FAILURES)
    elif verdict not in CHANNEL_VERDICTS:
        verdict = UNRECOGNISED
    return SELFTEST_FAILED + CHANNEL + verdict + attempts


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
    open>` (kind `hidden` when its link was refused with EACCES), `error:<errno>` (its link was unreadable
    otherwise) or `gone` (the process ended first), or `curl <pid> gone` (it ended before it could be
    placed), then `probe <pid> exited` once the probe has, and `done`. At least one curl must have been seen
    whole, and every fd seen must be one of UNREACHABLE: a socket that refused the open (ENXIO), or a hidden
    fd whose open was refused too (EACCES)."""
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
        if any(fd not in UNREACHABLE for fd in fds):
            return f"a curl output is reopenable, not a socket, or unreadable: {shown(line)}"
        whole += len(fds) == 2
    return "" if whole else "the check saw no curl process whole while the probe ran"


def require(ok: object, why: str) -> None:
    """Fails with only `why` unless `ok`. Codex T15 r3: pytest's assertion rewriting prints an assert's
    operands raw, so a check on a raw value (a launch's or a peer's reason, a variable's name, a path, a
    process's output) is made through this, with `why` built only from sanitised text."""
    if not ok:
        pytest.fail(why, pytrace=False)


def tool_var_rejection(reasons: list[str], tool_vars: Collection[str]) -> str:
    """"" when the channel rejected exactly one peer, and that for variables all in `tool_vars` (the CLI's
    agent-only tool variables); otherwise the reason, sanitised (`rejection_shown`)."""
    if len(reasons) != 1 or not reasons[0].startswith(ENV_REJECTED):
        return f"the channel rejected {[rejection_shown(r, tool_vars) for r in reasons]}"
    if not set(reasons[0].removeprefix(ENV_REJECTED).split(" ")) <= set(tool_vars):
        return f"rejected for {rejection_shown(reasons[0], tool_vars)}, not only tool-path variables"
    return ""
