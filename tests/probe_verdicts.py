"""Verdicts on the live probe-protection checks (Task 15). Each takes a check's exit status and output, and
returns "" only when the check ran to completion and every operation it reports was refused; otherwise
it returns the reason. Kept apart from the live module so the normal suite can test them offline."""

import re

MEMORY_OPS = ("attach", "vm", "mem")
FILE_OPS = ("create", "append", "rename")
MOUNT_NS = re.compile(r"mnt:\[\d+\]")
OVERLAY_STAGES = (["unshare"], ["ns", "bind-probe", "unshare"], ["ns", "bind-probe", "bind-cli", "unshare"],
                  ["ns", "bind-probe", "bind-cli", "run", "unshare"])


def memory_access_refused(rc: int, out: str) -> str:
    """The memory check prints `child <pid>`, then `<op> <pid> <errno name>` for each operation on each
    process it can see, then `done`. Every visible process, its own child included, must have all three
    operations refused."""
    lines = out.splitlines()
    if rc != 0 or not lines or lines[-1] != "done":
        return f"the memory check did not run to completion (exit {rc})"
    first = lines[0].split()
    if len(first) != 2 or first[0] != "child" or not first[1].isdigit():
        return "the memory check did not name its child"
    seen: dict[str, set[str]] = {}
    for line in lines[1:-1]:
        parts = line.split()
        well_formed = len(parts) == 3 and parts[0] in MEMORY_OPS and parts[1].isdigit()
        if not well_formed or not parts[2].startswith("E"):            # an errno name: refused
            return f"not a refusal: {line!r}"
        seen.setdefault(parts[1], set()).add(parts[0])
    if first[1] not in seen:
        return "the memory check did not cover its own child"
    if any(ops != set(MEMORY_OPS) for ops in seen.values()):
        return "a process was not checked by every operation"
    return ""


def file_writes_refused(rc: int, out: str) -> str:
    """The file check prints `<op>=<status>` for exactly its three operations, in order; each status must
    be numeric and nonzero."""
    results = [line.partition("=") for line in out.splitlines()]
    if rc != 0 or [name for name, _, _ in results] != list(FILE_OPS):
        return f"the file check did not report exactly {FILE_OPS} (exit {rc})"
    if not all(code.isdigit() and code != "0" for _, _, code in results):
        return f"a file operation was not refused: {out!r}"
    return ""


def separate_mount_namespace(rc: int, out: str) -> str:
    """The namespace check prints the workload's mount namespace, then either `refused` or the namespace a
    new user and mount namespace got, which must differ from the workload's."""
    lines = out.splitlines()
    if rc != 0 or len(lines) != 2 or not MOUNT_NS.fullmatch(lines[0]):
        return f"the namespace check did not run to completion (exit {rc})"
    own, new = lines
    if new != "refused" and (not MOUNT_NS.fullmatch(new) or new == own):
        return "a new mount namespace must not be the workload's"
    return ""


def overlay_outcome(rc: int, out: str) -> str:
    """The overlay check prints `forged=written`, then, from a new user and mount namespace if one could
    be made, `ns=entered` and the status of each step it reached (`bind-probe`, `bind-cli`, `run`), then
    `unshare=<status>` and `done`. Returns "refused" when a step was refused (the namespace was never
    made, or a bind failed), "ran" when every step succeeded (then only the result channel can refuse
    it), and otherwise the reason the output is incomplete."""
    lines = out.splitlines()
    if rc != 0 or lines[:1] != ["forged=written"] or lines[-1:] != ["done"]:
        return f"the overlay check did not run to completion (exit {rc})"
    stages = [line.partition("=") for line in lines[1:-1]]
    names = [name for name, _, _ in stages]
    values = {name: value for name, _, value in stages}
    statuses = [value for name, _, value in stages if name != "ns"]
    if names not in OVERLAY_STAGES or values.get("ns", "entered") != "entered" \
            or not all(v.isdigit() for v in statuses):
        return f"the overlay check printed an unexpected sequence: {names}"
    if "ns" not in values:
        return "refused" if values["unshare"] != "0" else "a namespace was made but not reported"
    if values["bind-probe"] != "0" or values.get("bind-cli", "0") != "0":
        return "refused"
    return "ran" if "run" in values else "a step stopped without a refusal"


def outputs_unreachable(rc: int, out: str) -> str:
    """The output-channel check prints `curl <pid> <fd 1> <fd 2>` for each curl process it saw while the
    probe ran, each fd as `<kind>:<result of a write open>` or `gone` (the process ended first), then
    `done`. At least one curl must have been seen whole, and every fd must be a socket that refused the
    open (ENXIO)."""
    lines = out.splitlines()
    if rc != 0 or lines[-1:] != ["done"]:
        return f"the output-channel check did not run to completion (exit {rc})"
    whole = 0
    for line in lines[:-1]:
        parts = line.split()
        if len(parts) != 4 or parts[0] != "curl" or not parts[1].isdigit():
            return f"not a result: {line!r}"
        fds = [fd for fd in parts[2:] if fd != "gone"]
        if any(fd != "socket:ENXIO" for fd in fds):
            return f"a curl output channel could be reopened or is not a socket: {line!r}"
        whole += len(fds) == 2
    return "" if whole else "the check saw no curl process while the probe ran"
