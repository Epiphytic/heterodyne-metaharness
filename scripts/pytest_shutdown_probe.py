"""PROPOSED CI-only Linux/macOS supervisor; not an application shutdown fix.

No forced successful exit. Artifacts live in a fresh supplied diagnostic directory.
"""
from __future__ import annotations

import faulthandler
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def record(root: Path, name: str, value: object) -> None:
    temporary = root / (name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(root / name)


def threads() -> list[dict[str, object]]:
    return [{"name": t.name, "ident": t.ident, "native_id": t.native_id,
             "daemon": t.daemon, "alive": t.is_alive()} for t in threading.enumerate()]


def worker(root: Path, args: list[str]) -> int:
    # Keep this FD open through pytest unconfigure AND interpreter shutdown.
    # pytest's own faulthandler plugin controls a different FD and timer.
    fd = os.open(root / "python-thread-stacks.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    faulthandler.register(signal.SIGUSR1, file=fd, all_threads=True, chain=False)
    record(root, "signal-ready.json", {"pid": os.getpid()})
    # Match python -m pytest's repository-root import entry, not script-directory imports.
    sys.path[0] = str(Path.cwd())
    import pytest

    class Phase:
        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_sessionfinish(self, session):  # type: ignore[no-untyped-def]
            try:
                return (yield)
            finally:
                # The outer wrapper resumes after inner sessionfinish/terminal summary.
                record(root, "sessionfinish-return.json", {
                    "monotonic": time.monotonic(), "session_exitstatus": int(session.exitstatus),
                    "threads": threads(),
                })
                faulthandler.dump_traceback(file=fd, all_threads=True)

    code = int(pytest.main(args, plugins=[Phase()]))
    record(root, "pytest-main-return.json", {
        "monotonic": time.monotonic(), "pytest_exitstatus": code, "threads": threads(),
    })
    faulthandler.dump_traceback(file=fd, all_threads=True)
    return code  # Normal sys.exit: deliberately do not bypass threading/atexit shutdown.


def supervise(root: Path, args: list[str]) -> int:
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    start = time.monotonic()
    child = subprocess.Popen(
        [sys.executable, "-u", str(Path(__file__).resolve()), "--worker", str(root), *args],
        start_new_session=True,  # Dedicated CI-only group for bounded timeout cleanup.
    )
    shutdown_start = None
    dumps = 0
    while child.poll() is None:
        now = time.monotonic()
        if shutdown_start is None and (root / "sessionfinish-return.json").exists():
            shutdown_start = now
        elapsed = now - shutdown_start if shutdown_start is not None else 0
        if shutdown_start is not None and elapsed >= 20 * dumps and dumps < 3:
            # The signal is registered before the ready file; never signal an unready worker.
            if (root / "signal-ready.json").exists():
                try:
                    os.kill(child.pid, signal.SIGUSR1)
                except ProcessLookupError:
                    pass
            dumps += 1
        why = ("post-session-shutdown-over-60s" if elapsed >= 60 else
               "whole-pytest-process-over-3600s" if now - start >= 3600 else None)
        if why is not None:
            if (root / "signal-ready.json").exists():
                try:
                    os.kill(child.pid, signal.SIGUSR1)
                except ProcessLookupError:
                    pass
            time.sleep(1)  # Bounded opportunity for the final stack write.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, sig)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    continue
                break
            record(root, "supervisor-result.json", {
                "reason": why, "exit": 124, "child_returncode": child.poll(),
                "elapsed_seconds": time.monotonic() - start,
                "result": "FAIL: watchdog timeout, never test success",
            })
            return 124
        time.sleep(0.25)
    code = child.returncode
    if code is None:
        raise RuntimeError("child status unavailable after exit observation")
    record(root, "supervisor-result.json", {
        "reason": "natural-child-exit", "child_returncode": code,
        "exit": code if code >= 0 else 128 - code,
        "elapsed_seconds": time.monotonic() - start,
    })
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    mode, directory, *arguments = sys.argv[1:]
    destination = Path(directory).absolute()
    if mode not in {"--worker", "--supervise"}:
        raise SystemExit("expected --supervise FRESH_DIRECTORY [pytest args]")
    raise SystemExit(worker(destination, arguments) if mode == "--worker"
                     else supervise(destination, arguments))
