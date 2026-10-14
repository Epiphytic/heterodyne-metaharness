# pyright: basic
"""Inside a Codex sandbox while its session runs (plan 4b E5): ask a second `codex app-server`, over
stdio, for the account's rate limits. It runs in a scratch CODEX_HOME holding only a link to the
session's read-only login, so it never touches the live app-server's state, and a refresh it attempts
fails on the read-only bind (§7). No model request is made. Prints the `rateLimits` object as JSON and
exits 0. Any failure exits 1 with nothing on stdout and only the error's type on stderr. The host
parses and validates the output. Standard library only.

A reading counts only if all of these hold: the app-server answered before its deadline (an answer
that races the deadline is a timeout), it was then ended and reaped by its own handle with the status
of its own clean exit or of the kill (anything else is ExitStatus), and the scratch home is confirmed
gone (ScratchKept). An error in ending the app-server or removing the scratch home replaces any
earlier error: the cleanup's failure is the one reported.

The app-server leads its own process group, and the deadline and the end kill the whole group. Its
output is read through poll against the deadline, never a blocking read: a descendant that keeps the
pipe open can neither hold the read past its limit nor outlive it in the group.

The host's trusted read runs outside every sandbox, so it starts the app-server through this file's
second mode (btq-9yiku): it checks the launch conditions (`unprivileged`), sets and confirms
no_new_privs, then execs. A setuid exec by a descendant then gains nothing, and the app-server keeps
the trampoline's PID, group and session. Any failure exits 126 before the exec.

Usage: python3 -I codex_usage.py <codex binary> [limit seconds]
       python3 -I codex_usage.py --exec-no-new-privs <program> [args...]
"""

import contextlib
import ctypes
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

LIMIT_SECONDS = 30.0
END_SECONDS = 5.0           # how long a killed app-server may take to be reaped
MAX_ANSWER = 1 << 20        # bytes in one line of the app-server's output
ENDED = (0, -signal.SIGKILL)  # its own clean exit, or the kill
PR_SET_NO_NEW_PRIVS = 38
PR_GET_NO_NEW_PRIVS = 39


class AnswerError(Exception):
    """The app-server answered with an error, or with something that isn't a JSON-RPC object."""


class ClosedOutput(Exception):
    """The app-server's output ended before it answered."""


class ExitStatus(Exception):
    """The app-server ended with a status that is neither its clean exit nor the kill."""


class NotGone(Exception):
    """The app-server could not be reaped after the kill."""


class NoRateLimits(Exception):
    """The answer holds no `rateLimits` object."""


class ScratchKept(Exception):
    """The scratch home is still there after its removal."""


def main(argv):
    codex = argv[0]
    limit = float(argv[1]) if len(argv) > 1 else LIMIT_SECONDS
    home = tempfile.mkdtemp(prefix="hz-usage-")
    try:
        os.symlink(os.path.join(os.environ["CODEX_HOME"], "auth.json"), os.path.join(home, "auth.json"))
        limits = read(codex, dict(os.environ, CODEX_HOME=home), limit)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        if os.path.lexists(home):
            raise ScratchKept("the scratch home is still there")
    if not isinstance(limits, dict):
        raise NoRateLimits("the answer has no rate limits")
    print(json.dumps(limits))
    return 0


def read(codex, env, limit, *, no_new_privs=False):
    """The `rateLimits` of the app-server run with `env` (its CODEX_HOME included), or None if the
    answer has none. Raises on any failure, TimeoutError past `limit` seconds. The app-server is ended
    in a `finally` whatever happens, the timer that bounds it included. Shared with the host's trusted
    read (sandbox/hostread.py), which loads this file and passes `no_new_privs`: the app-server is then
    started through `barrier_argv()`. In the sandbox the workload is already under no_new_privs."""
    deadline = time.monotonic() + limit
    argv = [*barrier_argv(), codex, "app-server"] if no_new_privs else [codex, "app-server"]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
    expired = threading.Event()

    def expire():
        expired.set()
        _kill(proc)

    timer = threading.Timer(limit, expire)
    started = False
    result = None
    try:
        try:
            timer.start()
            started = True
            result = _converse(proc, deadline)
        finally:
            timer.cancel()
            if started:
                timer.join()            # from here on, `expired` can't change
            if expired.is_set():
                raise TimeoutError("codex app-server did not answer within its limit")
    finally:
        _end(proc)
    return result.get("rateLimits") if isinstance(result, dict) else None


def _converse(proc, deadline):
    stdin, stdout = proc.stdin, proc.stdout
    if stdin is None or stdout is None:
        raise ClosedOutput("codex app-server has no pipes")
    fd = stdout.fileno()
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    pending = b""

    def receive():
        """The next line of output, read only as it arrives and never past the deadline."""
        nonlocal pending
        while b"\n" not in pending:
            if len(pending) > MAX_ANSWER:
                raise AnswerError("codex app-server sent an overlong line")
            left = deadline - time.monotonic()
            if left <= 0 or not poller.poll(max(1, int(left * 1000))):
                raise TimeoutError("codex app-server did not answer within its limit")
            chunk = os.read(fd, 65536)
            if not chunk:
                raise ClosedOutput("codex app-server closed its output")
            pending += chunk
        line, pending = pending.split(b"\n", 1)
        if len(line) > MAX_ANSWER:
            raise AnswerError("codex app-server sent an overlong line")
        return line

    def send(message):
        stdin.write(json.dumps(message) + "\n")
        stdin.flush()

    def call(id_, method, params):
        send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        while True:
            message = json.loads(receive())
            if not isinstance(message, dict):
                raise AnswerError("codex app-server sent something other than an object")
            if message.get("id") == id_:
                if "error" in message or "result" not in message:
                    raise AnswerError("codex app-server returned an error")
                return message["result"]

    call(0, "initialize", {"clientInfo": {"name": "heterodyne", "version": "1"}})
    send({"jsonrpc": "2.0", "method": "initialized"})
    return call(1, "account/rateLimits/read", None)


def _kill(proc):
    """SIGKILL the app-server's process group, its descendants included. The group is named by the
    app-server's own PID, which can't be reused before it is reaped here (nothing else reaps it)."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


def _end(proc):
    """Kill the app-server's group by its own handle, reap it and close its pipes. NotGone if it isn't
    reaped within END_SECONDS; ExitStatus unless it ended by its own clean exit or the kill."""
    try:
        _kill(proc)
        try:
            status = proc.wait(END_SECONDS)
        except subprocess.TimeoutExpired:
            raise NotGone("codex app-server is still running") from None
    finally:
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
    if status not in ENDED:
        raise ExitStatus("codex app-server ended abnormally")


def barrier_argv():
    """The argv prefix that runs the rest of an argv under no_new_privs, through this file's mode."""
    return [sys.executable, "-I", __file__, "--exec-no-new-privs"]


def _own_status():
    with open("/proc/self/status", encoding="ascii", errors="replace") as f:
        return f.read()


def exec_no_new_privs(argv, *, status=_own_status, prctl=None, execv=os.execv, exit=os._exit):
    """`python3 -I codex_usage.py --exec-no-new-privs <codex> app-server`: check the launch conditions,
    set and confirm no_new_privs, then exec argv. Exits 126 if any fails; never returns. The keywords
    are seams for the tests only."""
    try:
        if not unprivileged(status()):
            exit(126)
        prctl = prctl or ctypes.CDLL(None, use_errno=True).prctl
        if prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 or prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1:
            exit(126)
        execv(argv[0], argv)
    finally:
        exit(126)


def unprivileged(status):
    """The launch conditions, from one read of status: uids all this user's and not 0, and CapPrm,
    CapEff and CapAmb zero. CapInh doesn't count: under no_new_privs it can't be raised. A missing or
    unparsable line is false."""
    fields = {}
    for line in status.splitlines():
        name, sep, value = line.partition(":")
        if sep:
            fields[name] = value.split()
    uid = os.getuid()
    if uid == 0 or fields.get("Uid") != [str(uid)] * 4:
        return False
    for name in ("CapPrm", "CapEff", "CapAmb"):
        value = fields.get(name)
        try:
            if value is None or len(value) != 1 or int(value[0], 16) != 0:
                return False
        except ValueError:
            return False
    return True


if __name__ == "__main__":
    if sys.argv[1:2] == ["--exec-no-new-privs"]:
        exec_no_new_privs(sys.argv[2:])
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - any failure is "no reading", decided on the host
        print(f"codex_usage: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
