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
from pathlib import Path

from heterodyne.admind import reap
from heterodyne.admind.redact import redact
from heterodyne.admind.settings import AdmindAccount
from heterodyne.admind.store import private_dir

SUMMARY_TIMEOUT = 60.0
MAX_LINES = 10  # "about 8 lines", with two of slack (B8)
MAX_CHARS = 2000
MAX_BYTES = MAX_CHARS * 4  # stdout is read until this many bytes, then the process is killed
READ_CHUNK = reap.READ_CHUNK
FEED_CHUNK = 65536
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


_wait = reap.wait_exit      # looked up at call time by _reap, so tests can wrap it


async def _reap(child: reap.Child) -> None:
    """Kill the whole process group (also anything it started and left running), close the pipes, then
    collect the process (`reap.reap`). Runs on every exit path, including cancellation (also a second one),
    and is bounded: a descendant that left the group and still holds a pipe costs nothing, and the wait for
    the child's exit is bounded at REAP_SECONDS."""
    await reap.reap_shielded(child, "summarizer", seconds=REAP_SECONDS, wait=_wait)


async def _talk(child: reap.Child, data: bytes) -> bytes:
    await child.connect()
    return await _collect(child, data)


async def _collect(proc: reap.Child, data: bytes) -> bytes:
    """Feed stdin while reading stdout, so neither pipe can fill and stall; stop past MAX_BYTES."""
    if proc.stdin is None or proc.stdout is None:
        raise SummaryFailed("not-run")
    stdin, stdout = proc.stdin, proc.stdout

    async def feed() -> None:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):  # it may stop reading early
            for i in range(0, len(data), FEED_CHUNK):   # not one write: the transport would buffer it whole
                stdin.write(data[i : i + FEED_CHUNK])
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


def child_env(account: AdmindAccount | None) -> dict[str, str] | None:
    """The summarizer's whole environment: admind's, without the variables its login clears and with the
    ones it sets (ADR §4.4 D11). None (inherit admind's) when no account is given."""
    if account is None:
        return None
    env = {k: v for k, v in os.environ.items() if k not in account.unset}
    env.update(account.set)
    return env


async def summarize(argv: list[str] | None, cwd: Path, reply: str, timeout: float = SUMMARY_TIMEOUT,
                    account: AdmindAccount | None = None) -> str:
    if argv is None:
        raise SummaryFailed("not-configured")
    private_dir(cwd)
    payload = await asyncio.to_thread(lambda: PROMPT.format(reply=redact(reply)).encode())   # off the loop
    try:
        child = reap.spawn(argv, cwd=cwd, feed=True, stderr=False, env=child_env(account))   # owned from here
    except OSError:
        raise SummaryFailed("not-run") from None
    try:
        out = await asyncio.wait_for(_talk(child, payload), timeout)
    except TimeoutError:
        raise SummaryFailed("timeout") from None
    except OSError:
        raise SummaryFailed("not-run") from None       # connecting the pipes failed
    finally:
        await _reap(child)
    if child.returncode != 0:
        raise SummaryFailed("failed")
    text = await asyncio.to_thread(lambda: redact(out.decode("utf-8", errors="replace")).strip())
    if not text:
        raise SummaryFailed("empty")
    if len(text.splitlines()) > MAX_LINES or len(text) > MAX_CHARS:
        raise SummaryFailed("too-long")
    return f"{text}\n\n{FOOTER}"
