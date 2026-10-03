"""Summaries of long admin-agent replies (ADR 0001 §8, revision 13; plan 2b B8).

The summarizer is admind's own subprocess: headless, without tools or hooks, 60 s at most. Its input is
the reply after `redact` (redacted here as well, so no caller can skip it); its output is read in bounded
pieces, redacted again, must be non-empty and short, and gets admind's fixed footer. Any failure is a
`SummaryFailed` with a fixed reason word; the caller sends the reply to the backstop. Whatever happens,
the summarizer's process group is killed before `summarize` returns.
"""

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from heterodyne.admind.redact import redact
from heterodyne.admind.store import private_dir

SUMMARY_TIMEOUT = 60.0
MAX_LINES = 10  # "about 8 lines", with two of slack (B8)
MAX_CHARS = 2000
MAX_BYTES = MAX_CHARS * 4  # stdout is read until this many bytes, then the process is killed
READ_CHUNK = 65536
REAP_SECONDS = 5.0  # the most cleanup after a kill may take
FOOTER = "summary · reply `!details` for everything"
PROMPT = """You summarize an admin agent's reply for operators who read it on a phone.
Write at most 8 short lines of plain text. Quote verbatim, in full, every question the reply asks the
operators and every error it reports. Add nothing that is not in the reply. Do not add a closing line;
one is appended for you. The reply is everything between the two marker lines.
<<<REPLY
{reply}
REPLY>>>
"""


class SummaryFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # fixed words only: not-configured, not-run, failed, timeout, empty, too-long


def needs_summary(text: str, lines: int, chars: int) -> bool:
    return len(text.splitlines()) > lines or len(text) > chars


def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


async def _drain(proc: asyncio.subprocess.Process) -> None:
    """Close stdin and read stdout to its end, discarding it. asyncio stops reading a pipe that holds more
    than its buffer limit unread, and then never sees the pipe close: `wait()` would not return."""
    if proc.stdin is not None:
        proc.stdin.close()
    if proc.stdout is not None:
        while await proc.stdout.read(READ_CHUNK):
            pass
    await proc.wait()


def _close_transport(proc: asyncio.subprocess.Process) -> None:
    """Release the pipe descriptors even if a descendant still holds the other end. asyncio has no public
    call for this; `Process._transport` is CPython's (requires-python >= 3.12), so it is looked up
    defensively and a failure here never hides the error being handled."""
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        with contextlib.suppress(Exception):
            transport.close()


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Kill the whole process group (also anything it started and left running), then collect the process.
    Runs on every exit path, including cancellation (also a second one), and is bounded: a descendant that
    left the group and still holds the pipe costs REAP_SECONDS, not a hang. The pipes are closed whatever
    happens here; a cancellation propagates after that, and any other failure of the cleanup is dropped
    so it can't replace the error being handled. The process itself is dead after the SIGKILL and asyncio's
    child watcher reaps it."""
    _kill(proc)
    try:
        await asyncio.wait_for(_drain(proc), REAP_SECONDS)
    except Exception:  # noqa: BLE001, S110 - TimeoutError (a descendant holds the pipe) or a broken pipe
        pass
    finally:
        _close_transport(proc)


async def _collect(proc: asyncio.subprocess.Process, data: bytes) -> bytes:
    """Feed stdin while reading stdout, so neither pipe can fill and stall; stop past MAX_BYTES."""
    if proc.stdin is None or proc.stdout is None:
        raise SummaryFailed("not-run")
    stdin, stdout = proc.stdin, proc.stdout

    async def feed() -> None:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):  # it may stop reading early
            stdin.write(data)
            await stdin.drain()
            stdin.close()

    feeder = asyncio.create_task(feed())
    out = bytearray()
    try:
        while chunk := await stdout.read(READ_CHUNK):
            out += chunk
            if len(out) > MAX_BYTES:
                raise SummaryFailed("too-long")
        await proc.wait()
    finally:
        feeder.cancel()
        await asyncio.gather(feeder, return_exceptions=True)
    return bytes(out)


async def summarize(argv: list[str] | None, cwd: Path, reply: str, timeout: float = SUMMARY_TIMEOUT) -> str:
    if argv is None:
        raise SummaryFailed("not-configured")
    private_dir(cwd)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        raise SummaryFailed("not-run") from None
    try:
        out = await asyncio.wait_for(_collect(proc, PROMPT.format(reply=redact(reply)).encode()), timeout)
    except TimeoutError:
        raise SummaryFailed("timeout") from None
    finally:
        await _reap(proc)
    if proc.returncode != 0:
        raise SummaryFailed("failed")
    text = redact(out.decode("utf-8", errors="replace")).strip()
    if not text:
        raise SummaryFailed("empty")
    if len(text.splitlines()) > MAX_LINES or len(text) > MAX_CHARS:
        raise SummaryFailed("too-long")
    return f"{text}\n\n{FOOTER}"
