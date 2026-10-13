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

Usage: python3 -I codex_usage.py <codex binary> [limit seconds]
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

LIMIT_SECONDS = 30.0
END_SECONDS = 5.0           # how long a killed app-server may take to be reaped
MAX_ANSWER = 1 << 20        # characters in one line of the app-server's output
ENDED = (0, -signal.SIGKILL)  # its own clean exit, or the kill


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


def read(codex, env, limit):
    """The `rateLimits` of the app-server run with `env` (its CODEX_HOME included), or None if the
    answer has none. Raises on any failure, TimeoutError past `limit` seconds. The app-server is ended
    in a `finally` whatever happens, the timer that bounds it included. Shared with the host's trusted
    read (sandbox/hostread.py), which loads this file."""
    proc = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env)
    expired = threading.Event()

    def expire():
        expired.set()
        proc.kill()

    timer = threading.Timer(limit, expire)
    started = False
    result = None
    try:
        try:
            timer.start()
            started = True
            result = _converse(proc)
        finally:
            timer.cancel()
            if started:
                timer.join()            # from here on, `expired` can't change
            if expired.is_set():
                raise TimeoutError("codex app-server did not answer within its limit")
    finally:
        _end(proc)
    return result.get("rateLimits") if isinstance(result, dict) else None


def _converse(proc):
    stdin, stdout = proc.stdin, proc.stdout
    if stdin is None or stdout is None:
        raise ClosedOutput("codex app-server has no pipes")

    def send(message):
        stdin.write(json.dumps(message) + "\n")
        stdin.flush()

    def call(id_, method, params):
        send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        while True:
            line = stdout.readline(MAX_ANSWER)
            if not line:
                raise ClosedOutput("codex app-server closed its output")
            if not line.endswith("\n"):
                raise AnswerError("codex app-server sent an overlong line")
            message = json.loads(line)
            if not isinstance(message, dict):
                raise AnswerError("codex app-server sent something other than an object")
            if message.get("id") == id_:
                if "error" in message or "result" not in message:
                    raise AnswerError("codex app-server returned an error")
                return message["result"]

    call(0, "initialize", {"clientInfo": {"name": "heterodyne", "version": "1"}})
    send({"jsonrpc": "2.0", "method": "initialized"})
    return call(1, "account/rateLimits/read", None)


def _end(proc):
    """Kill the app-server by its own handle, reap it and close its pipes. NotGone if it isn't reaped
    within END_SECONDS; ExitStatus unless it ended by its own clean exit or the kill."""
    try:
        proc.kill()
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


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - any failure is "no reading", decided on the host
        print(f"codex_usage: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
