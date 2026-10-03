"""Plan 2b Task 8: the reply pipeline (verbatim, summary, backstop) in the daemon.

Nothing here runs the real claude, touches ~/.claude or the network: the admin agent is the fake claude in
a private tmux server, the summarizer is a shell script in tmp_path, and wn-agent is the fake.
"""

import asyncio
import json
import re
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_admind_daemon import Harness, needs_tmux, run_with

from heterodyne.admind import backstop, hook, summarize
from heterodyne.admind.daemon import EXTRACT_FAILED, MAX_SEND_ATTEMPTS
from heterodyne.admind.hook import HookEvent, reply_text
from heterodyne.admind.store import Store, now

TOKEN = "ghp_" + "A" * 30
HEX = "ab" * 32


def script(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / f"summ-{uuid.uuid4().hex[:6]}.sh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return [str(p)]


def configure(
    summarizer: str | None = None, *, batch: float = 0.3, timeout: float = 5.0
) -> Callable[[Harness], None]:
    """Seed `operator_seen_at` and set the daemon's timings; `summarizer` is a shell body (None: unset)."""

    def before(h: Harness) -> None:
        h.store.set("operator_seen_at", now())
        h.daemon.batch_seconds = batch
        h.daemon.batch_poll = 0.05
        h.daemon.summary_poll = 0.1
        h.daemon.summary_timeout = timeout
        h.daemon.summarizer_argv = None if summarizer is None else script(h.settings.workdir, summarizer)

    return before


def batches(h: Harness) -> list[str]:
    return [t for t in h.texts() if t.startswith(backstop.TITLE)]


async def batch_arrives(h: Harness, n: int = 1) -> str:
    await h.until(lambda: len(batches(h)) >= n)
    return batches(h)[n - 1]


async def session(h: Harness) -> str:
    """The admin agent's session ID, once its launch is ready (so hook events count as current)."""
    await h.until(lambda: h.agent.session_id is not None and h.daemon.is_ready())
    assert h.agent.session_id is not None
    return h.agent.session_id


async def terminal_stop(h: Harness, reply: str | None, path: Path | None = None, **kw: Any) -> None:
    """A Stop for a turn begun at the terminal, put straight on the hook queue."""
    sid = await session(h)
    await h.daemon.hooks.put(h.event("Stop", sid, None if path is None else str(path), reply, **kw))


def audit_records(h: Harness) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (h.settings.state_dir / "audit.jsonl").read_text().splitlines()]


def audited(h: Harness, **want: Any) -> bool:
    return any(all(r.get(k) == v for k, v in want.items()) for r in audit_records(h))


def outbox_status(h: Harness, key: str) -> str | None:
    row = h.store.db.execute("SELECT status FROM outbox WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def sent_for(h: Harness, text: str) -> dict[str, Any]:
    return next(r for r in h.fake.sent if r["text"] == text)


LONG = "long " + "w" * 900
ORIGIN = re.compile(r"— op · \d\d:\d\d UTC · “long w+…”")


# --- 1, 2. verbatim and summarized replies ---


@needs_tmux
def test_a_short_reply_is_posted_verbatim_in_thread(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        mid = await h.say("hello")
        await h.until(lambda: "echo: hello" in h.texts())
        sent = sent_for(h, "echo: hello")
        assert sent["reply_to_message_id_hex"] == mid
        key = sent["idempotency_key"]
        assert re.fullmatch(r"reply:[^:]+:\d+:0", key)
        turn = h.store.turns_with_status("verbatim")[0]
        assert turn.reply_to == mid and turn.key == key.rsplit(":", 1)[0]
        post = h.store.post_record(key)
        assert post is not None and post.kind == "verbatim" and post.turn_id == turn.turn_id

    run_with(tmp_path, scenario, configure("echo never used"))


@needs_tmux
def test_a_long_reply_is_posted_as_a_summary_in_thread(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        mid = await h.say(LONG)
        want = f"short summary\n\n{summarize.FOOTER}"
        await h.until(lambda: want in h.texts())
        assert sent_for(h, want)["reply_to_message_id_hex"] == mid
        assert not any("w" * 100 in t for t in h.texts())
        await h.until(lambda: len(h.store.turns_with_status("summarized")) == 1)
        post = h.store.post_record(sent_for(h, want)["idempotency_key"])
        assert post is not None and post.kind == "summary"

    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
def test_a_summary_is_redacted(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say(LONG)
        await h.until(lambda: any(t.endswith(summarize.FOOTER) for t in h.texts()))
        assert not any(TOKEN in t for t in h.texts())
        assert any("<redacted" in t for t in h.texts())

    run_with(tmp_path, scenario, configure(f'echo "leaked {TOKEN}"'))


# --- 3. summarizer failures fall back to the backstop ---


@needs_tmux
@pytest.mark.parametrize(
    ("body", "reason"),
    [("exit 1", "failed"), ("sleep 5", "timeout"), ("true", "empty"), (None, "not-configured")],
)
def test_summarizer_failure_goes_to_the_backstop(tmp_path: Path, body: str | None, reason: str) -> None:
    async def scenario(h: Harness) -> None:
        await h.say(LONG)
        text = await batch_arrives(h)
        assert ORIGIN.search(text) and LONG in text
        assert sent_for(h, text).get("reply_to_message_id_hex") is None  # a batch is unthreaded
        assert audited(h, kind="summary", action="failed", reason=reason)
        assert audited(h, kind="backstop", action="posted")

    run_with(tmp_path, scenario, configure(body, batch=0.3, timeout=0.3))


@needs_tmux
def test_failures_are_audited_without_reply_text(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await terminal_stop(h, "reply " + TOKEN + " " + "w" * 900)  # the operator's own text is audited whole
        await batch_arrives(h)
        raw = (h.settings.state_dir / "audit.jsonl").read_text()
        assert "w" * 50 not in raw and TOKEN not in raw

    run_with(tmp_path, scenario, configure("exit 1"))


# --- 4, 5. one batch, however big ---


@needs_tmux
def test_two_failures_in_one_window_make_one_batch(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await terminal_stop(h, "first " + "y" * 900)
        await terminal_stop(h, "second " + "z" * 900)
        text = await batch_arrives(h)
        assert text.index("first ") < text.index("second ")
        assert text.count("— terminal ·") == 2
        await asyncio.sleep(0.6)
        assert len(batches(h)) == 1

    run_with(tmp_path, scenario, configure("exit 1", batch=0.5))


@needs_tmux
def test_a_big_batch_is_one_message_with_whole_lines(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        for r in range(3):
            await terminal_stop(h, "\n".join(f"r{r}-{i:03d}-" + "z" * 190 for i in range(100)))
        text = await batch_arrives(h)
        lines = text.splitlines()
        assert lines[0] == backstop.TITLE and len(lines) == 1 + 10 + 1 + 40
        assert "…(+" not in text and len(text) > 4000
        await asyncio.sleep(0.6)
        assert len(batches(h)) == 1

    run_with(tmp_path, scenario, configure("exit 1", batch=0.5))


@needs_tmux
def test_a_batch_over_the_transport_limit_is_shortened(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await terminal_stop(h, "\n".join(f"{i:02d}-" + "q" * 2000 for i in range(49)))
        text = await batch_arrives(h)
        assert len(text) <= backstop.BATCH_MAX_CHARS
        assert all(re.search(r"…\(\+\d+ chars\)$", line) for line in text.splitlines()[2:])
        assert len(batches(h)) == 1

    run_with(tmp_path, scenario, configure("exit 1"))


# --- 6, 7. restarts ---


@needs_tmux
def test_a_turn_left_summarizing_is_summarized_after_startup(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        configure('echo "short summary"')(h)
        h.store.add_turn(
            "reply:old:1",
            "old",
            None,
            backstop.origin(None, now(), None),
            LONG,
            None,
            None,
            None,
            "summarizing",
        )

    async def scenario(h: Harness) -> None:
        await h.until(lambda: f"short summary\n\n{summarize.FOOTER}" in h.texts())
        assert [t.status for t in h.store.turns_with_status("summarized")] == ["summarized"]

    run_with(tmp_path, scenario, before)


@needs_tmux
def test_an_open_batch_is_posted_after_a_restart(tmp_path: Path) -> None:
    async def first(h: Harness) -> None:
        await terminal_stop(h, "kept " + "k" * 900)
        await h.until(lambda: len(h.store.turns_with_status("batched")) == 1)
        assert batches(h) == []

    run_with(tmp_path, first, configure("exit 1", batch=3600))

    async def second(h: Harness) -> None:
        text = await batch_arrives(h)
        assert "kept " in text
        assert len(h.store.turns_with_status("batched")) == 1  # nothing new was opened
        assert h.store.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1

    run_with(tmp_path, second, configure("exit 1", batch=0.3))


# --- 8, 9. transient failures ---


@needs_tmux
def test_a_failing_backstop_step_is_retried_by_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(h: Harness) -> None:
        orig = h.store.open_batch
        calls: list[int] = []

        def flaky(at: float, seconds: float) -> int:
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            return orig(at, seconds)

        monkeypatch.setattr(h.store, "open_batch", flaky)
        await h.say(LONG)
        text = await batch_arrives(h)
        assert LONG in text and len(calls) >= 2
        assert audited(h, kind="summary", action="pass-failed")

    run_with(tmp_path, scenario, configure("exit 1"))


@needs_tmux
def test_a_reply_that_cannot_be_recorded_goes_to_the_backstop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(h: Harness) -> None:
        orig = h.store.record_post
        calls: list[int] = []

        def flaky(*args: Any) -> None:
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("disk I/O error")
            orig(*args)

        monkeypatch.setattr(h.store, "record_post", flaky)
        await h.say("hello")
        text = await batch_arrives(h)
        assert EXTRACT_FAILED in text  # the fixed notice: never the text that just failed
        assert "echo: hello" not in text and "echo: hello" not in h.texts()
        await h.until(lambda: h.store.get("busy") is None)
        assert audited(h, kind="reply", action="record-failed")

    run_with(tmp_path, scenario, configure(None))


# --- 10, 14, 15. delivery gives up ---


async def no_sleep(_seconds: float) -> None:
    return None


@needs_tmux
def test_a_summary_that_cannot_be_delivered_goes_to_the_backstop(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        h.daemon._sleep = no_sleep  # pyright: ignore[reportPrivateUsage]
        await h.say("warm up")
        await h.until(lambda: "echo: warm up" in h.texts() and not h.store.pending())
        h.fake.fail_sends = MAX_SEND_ATTEMPTS
        await h.say(LONG)
        text = await batch_arrives(h)
        assert LONG in text
        rows = h.store.db.execute("SELECT status FROM outbox WHERE key LIKE '%:s0'").fetchall()
        assert [r[0] for r in rows] == ["failed"]
        assert len(h.store.turns_with_status("batched")) == 1

    run_with(tmp_path, scenario, configure('echo "short summary"'))


async def fail_first_batch(h: Harness) -> None:
    h.daemon._sleep = no_sleep  # pyright: ignore[reportPrivateUsage]
    await h.say(LONG)
    await h.until(lambda: len(h.store.turns_with_status("batched")) == 1)
    h.fake.fail_sends = MAX_SEND_ATTEMPTS  # the batch is not due for another second
    await h.until(lambda: outbox_status(h, "batch:1.0") == "failed", 20)
    row = h.store.db.execute("SELECT status, attempt FROM batches WHERE batch_id = 1").fetchone()
    assert tuple(row) == ("open", 1)
    assert audited(h, kind="backstop", action="reopened")


@needs_tmux
def test_a_batch_that_cannot_be_delivered_is_reopened_and_resent(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await fail_first_batch(h)
        text = await batch_arrives(h)
        assert LONG in text
        assert sent_for(h, text)["idempotency_key"] == "batch:1.1"
        assert outbox_status(h, "batch:1.1") == "sent"

    run_with(tmp_path, scenario, configure("exit 1", batch=1.0))


@needs_tmux
def test_a_reopened_batch_is_resent_after_a_restart(tmp_path: Path) -> None:
    async def first(h: Harness) -> None:
        await fail_first_batch(h)
        h.daemon.batch_seconds = 3600

    run_with(tmp_path, first, configure("exit 1", batch=1.0))

    async def second(h: Harness) -> None:
        text = await batch_arrives(h)
        assert LONG in text and sent_for(h, text)["idempotency_key"] == "batch:1.1"

    run_with(tmp_path, second, configure("exit 1", batch=0.3))


# --- 11. an unreadable transcript turn ---


@needs_tmux
def test_a_slow_transcript_read_sends_the_notice_to_the_backstop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def slow(ev: HookEvent, start: int | None, end: int | None) -> str | None:
        release.wait(5)
        return "never used"

    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", slow)

    def before(h: Harness) -> None:
        configure("exit 1")(h)
        h.daemon.extract_timeout = 0.1

    async def scenario(h: Harness) -> None:
        try:
            await terminal_stop(h, None, tmp_path / "x.jsonl")
            text = await batch_arrives(h)
            assert EXTRACT_FAILED in text and "never used" not in text
            assert h.store.get("busy") is None
        finally:
            release.set()

    run_with(tmp_path, scenario, before)


@needs_tmux
def test_a_symlinked_transcript_is_unreadable_not_empty(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        sid = await session(h)
        real = tmp_path / "real.jsonl"
        append(real, [prompt("go"), said("secret answer")])
        link = tmp_path / "links" / f"{sid}.jsonl"
        link.parent.mkdir()
        link.symlink_to(real)
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(link), prompt="go"))
        await h.until(lambda: h.store.get("busy") is not None)
        await h.daemon.hooks.put(h.event("Stop", sid, str(link)))
        text = await batch_arrives(h)
        assert EXTRACT_FAILED in text and "secret answer" not in text and NO_REPLY_TEXT not in text

    run_with(tmp_path, scenario, configure("exit 1"))


NO_REPLY_TEXT = "(the admin agent's turn ended without a text reply)"


@needs_tmux
def test_a_turn_without_a_span_is_unreadable_not_empty(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await terminal_stop(h, None, tmp_path / "x.jsonl")  # no UserPromptSubmit was seen for it
        text = await batch_arrives(h)
        assert EXTRACT_FAILED in text and NO_REPLY_TEXT not in text

    run_with(tmp_path, scenario, configure("exit 1"))


# --- 12. the transcript span ---


@needs_tmux
def test_the_turn_records_its_transcript_span(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        path.write_bytes(b"x" * 99 + b"\n")
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await h.until(lambda: h.store.get("turn_start") is not None)
        with path.open("ab") as fh:
            fh.write(b"y" * 49 + b"\n")
        await h.daemon.hooks.put(h.event("Stop", sid, str(path), "done"))
        await h.until(lambda: len(h.store.turns_with_status("verbatim")) == 1)
        turn = h.store.turns_with_status("verbatim")[0]
        assert (turn.transcript_start, turn.transcript_end) == (100, 150)
        assert h.store.get("turn_start") is None

    run_with(tmp_path, scenario, configure(None))


@needs_tmux
def test_a_stale_launch_stop_has_no_span(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        path.write_bytes(b"x" * 10)
        await h.daemon.hooks.put(HookEvent("Stop", str(sid), str(path), "own text", launch=None))
        await h.until(lambda: "own text" in h.texts())
        turn = h.store.turns_with_status("verbatim")[0]
        assert (turn.transcript_start, turn.transcript_end) == (None, None)

    run_with(tmp_path, scenario, configure(None))


@needs_tmux
def test_a_turn_that_changes_during_extraction_is_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    started = threading.Event()

    def slow(ev: HookEvent, start: int | None, end: int | None) -> str | None:
        started.set()
        release.wait(5)
        return "the old turn's text"

    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", slow)

    def before(h: Harness) -> None:
        configure(None)(h)
        h.daemon.extract_timeout = 5

    async def scenario(h: Harness) -> None:
        try:
            sid = await session(h)
            path = tmp_path / f"{sid}.jsonl"
            path.write_bytes(b"x\n")
            stop = asyncio.create_task(h.daemon.on_stop(h.event("Stop", sid, str(path)), validate=True))
            await h.until(started.is_set)
            await h.daemon.on_hook(h.event("UserPromptSubmit", sid, str(path), prompt="next"), validate=True)
            release.set()
            await stop
            assert audited(h, kind="agent", action="stale-stop-unrecoverable")
            assert h.store.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
        finally:
            release.set()

    run_with(tmp_path, scenario, before)


# --- 13. redaction ---


@needs_tmux
def test_a_hex_value_in_a_reply_never_reaches_the_chat(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say(f"key {HEX}")
        await h.until(lambda: any(t.startswith("echo: key") for t in h.texts()))
        assert not any(HEX in t for t in h.texts())

    run_with(tmp_path, scenario, configure(None))


@needs_tmux
def test_the_backstop_is_redacted(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await terminal_stop(h, f"token {TOKEN} key {HEX}\n" + "w" * 900)
        text = await batch_arrives(h)
        assert TOKEN not in text and HEX not in text and "<redacted" in text
        assert "w" * 900 in text

    run_with(tmp_path, scenario, configure("exit 1"))


# --- 16. the fallback reads the whole turn ---


@needs_tmux
def test_the_fallback_reply_holds_every_text_of_the_turn(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        append(path, [prompt("old"), said("old answer")])
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await h.until(lambda: h.store.get("turn_start") is not None)
        append(
            path,
            [
                prompt("go"),
                said("Which unit, gateway or relay?"),
                tool("Bash", command="ls"),
                result("a b"),
                said("Checked both."),
            ],
        )
        await h.daemon.hooks.put(h.event("Stop", sid, str(path)))
        await h.until(lambda: "Which unit, gateway or relay?\n\nChecked both." in h.texts())
        assert (
            h.store.turns_with_status("verbatim")[0].text == "Which unit, gateway or relay?\n\nChecked both."
        )
        assert not any("old answer" in t or "Bash" in t for t in h.texts())

    run_with(tmp_path, scenario, configure(None))


# --- 17. arrival order across overlapping failures ---


@needs_tmux
def test_the_batch_keeps_the_order_replies_reach_the_backstop(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        h.daemon._sleep = no_sleep  # pyright: ignore[reportPrivateUsage]
        await h.say("warm up")
        await h.until(lambda: "echo: warm up" in h.texts() and not h.store.pending())
        await h.say(LONG)  # A
        await h.until(lambda: len(h.store.turns_with_status("summarizing")) == 1)
        h.fake.fail_sends = MAX_SEND_ATTEMPTS
        await h.say("bee")  # B: its verbatim post fails
        text = await batch_arrives(h)
        assert text.index("“bee”") < text.index("“long ")
        assert text.index("echo: bee") < text.index(LONG)
        assert [t.text for t in h.store.batch_turns(1)] == ["echo: bee", f"echo: {LONG}"]

    run_with(tmp_path, scenario, configure("sleep 1; exit 1", batch=2.0, timeout=5))


# --- the verbatim limits ---


@needs_tmux
def test_replies_at_the_verbatim_limits_are_posted_whole_and_just_over_are_summarized(tmp_path: Path) -> None:
    lines8 = "\n".join(f"line {i}" for i in range(8))
    chars800 = "x" * 800

    async def scenario(h: Harness) -> None:
        for reply in (lines8, chars800):
            await terminal_stop(h, reply)
            await h.until(lambda r=reply: r in h.texts())  # type: ignore[misc]
        assert [t.text for t in h.store.turns_with_status("verbatim")] == [lines8, chars800]
        for reply in (lines8 + "\nline 8", chars800 + "x"):
            await terminal_stop(h, reply)
        await h.until(lambda: h.texts().count(f"SUMMARIZED\n\n{summarize.FOOTER}") == 2)
        assert len(h.store.turns_with_status("summarized")) == 2
        assert lines8 + "\nline 8" not in h.texts() and chars800 + "x" not in h.texts()

    run_with(tmp_path, scenario, configure("echo SUMMARIZED"))


# --- a slow summarizer does not stall the daemon ---


@needs_tmux
def test_a_slow_summarizer_does_not_block_commands(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say(LONG)
        await h.until(lambda: len(h.store.turns_with_status("summarizing")) == 1)
        mid = await h.say("!ps")
        await h.until(lambda: any(r.get("reply_to_message_id_hex") == mid for r in h.fake.sent), 2.5)
        assert len(h.store.turns_with_status("summarizing")) == 1  # the summarizer is still running

    run_with(tmp_path, scenario, configure("sleep 4; echo late", timeout=10))


# --- store ---


def test_details_target_returns_only_delivered_posts(tmp_path: Path) -> None:
    s = Store(tmp_path / "a.db")
    for key, kind in (("sum:1", "summary"), ("sum:2", "summary"), ("batch:1.0", "batch")):
        s.enqueue(key, "t", None)
        s.record_post(key, kind, None, None)
    first = s.next_pending()
    assert first is not None
    s.mark_sent(first.seq, "m" * 64)
    second = [r for r in s.pending() if r.key == "batch:1.0"][0]
    s.mark_failed(second.seq)
    got = s.details_target(None)
    assert got is not None and got.key == "sum:1"
    assert s.details_target("m" * 64) == got
    assert s.details_target("n" * 64) is None
    s.enqueue("sum:3", "t", None)
    s.record_post("sum:3", "summary", None, None)
    assert s.details_target(None) == got  # pending never counts


def test_add_turn_with_a_known_key_returns_the_same_turn(tmp_path: Path) -> None:
    s = Store(tmp_path / "a.db")
    a = s.add_turn("k", "s", None, "o", "t", None, None, None, "verbatim")
    assert s.add_turn("k", "s", None, "o", "t2", None, None, None, "verbatim") == a


def test_replies_join_in_the_order_they_reach_the_backstop(tmp_path: Path) -> None:
    s = Store(tmp_path / "a.db")
    one = s.add_turn("k1", "s", None, "o", "1", None, None, None, "summarizing")
    two = s.add_turn("k2", "s", None, "o", "2", None, None, None, "summarizing")
    b = s.open_batch(100.0, 60.0)
    s.add_to_batch(two, b)
    s.add_to_batch(one, b)
    assert [t.turn_id for t in s.batch_turns(b)] == [two, one]
    s.reopen_batch(b, 500.0)
    three = s.add_turn("k3", "s", None, "o", "3", None, None, None, "summarizing")
    s.add_to_batch(three, b)
    assert [t.turn_id for t in s.batch_turns(b)] == [two, one, three]


def test_a_batch_window_is_checked_when_a_reply_joins(tmp_path: Path) -> None:
    s = Store(tmp_path / "a.db")
    b = s.open_batch(100.0, 60.0)
    assert s.open_batch(159.999, 60.0) == b
    later = s.open_batch(160.0, 60.0)
    assert later != b
    assert s.due_batches(160.0, 60.0) == [(b, 0)]
    assert s.due_batches(220.0, 60.0) == [(b, 0), (later, 0)]


def test_prompts_are_recorded_once(tmp_path: Path) -> None:
    s = Store(tmp_path / "a.db")
    s.record_prompt("m1", "op", "first words")
    s.record_prompt("m1", "other", "changed")
    row = s.prompt("m1")
    assert row is not None and (row.operator, row.words) == ("op", "first words")
    assert s.prompt("m2") is None


# --- the transcript fallback (hook.reply_text) ---


def append(path: Path, records: list[dict[str, Any]]) -> int:
    with path.open("a") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path.stat().st_size


def prompt(text: str) -> dict[str, Any]:
    return {"type": "user", "message": {"content": text}}


def said(text: str) -> dict[str, Any]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def tool(name: str, **inputs: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "t1", "name": name, "input": inputs}]},
    }


def result(text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": text}]},
    }


def stop(path: Path, sid: str = "s") -> HookEvent:
    return HookEvent("Stop", sid, str(path), None, None)


def test_fallback_keeps_every_assistant_text_of_the_turn(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    start = append(t, [prompt("old"), said("old answer")])
    end = append(
        t, [prompt("check it"), said("Which unit?"), tool("Bash", command="ls"), result("a b"), said("Done.")]
    )
    append(t, [prompt("next"), said("next answer")])
    assert reply_text(stop(t), start, end) == "Which unit?\n\nDone."


def test_fallback_reads_a_record_larger_than_the_old_tail_limit(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), said("x" * (9 * 1024 * 1024))])
    assert reply_text(stop(t), 0, end) == "x" * (9 * 1024 * 1024)


def test_fallback_silent_turn_is_empty_not_a_failure(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), tool("Bash", command="true"), result("")])
    assert reply_text(stop(t), 0, end) == ""


def test_a_result_record_with_text_is_not_a_prompt(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    mixed = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "ok"}, {"type": "text", "text": "note"}]},
    }
    end = append(
        t,
        [
            prompt("go"),
            tool("Bash", command="ls"),
            mixed,
            said("Final."),
            prompt("next"),
            said("next answer"),
        ],
    )
    assert reply_text(stop(t), 0, end) == "Final."


def test_a_changed_transcript_is_a_failure(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), said("hi")])
    assert reply_text(stop(t), 0, end + 100) is None  # shorter than the span: truncated
    with t.open("a") as fh:
        fh.write('{"type": "assistant", "message": {"content": [{"type": "text", "text": "par')
    assert reply_text(stop(t), 0, t.stat().st_size) is None  # the span ends inside a record


def test_an_oversized_record_is_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Codex r7 finding 1: an unparsed record may be this turn's answer or the next turn's prompt.
    monkeypatch.setattr(hook, "MAX_RECORD", 1000)
    big = "x" * 5000
    for i, records in enumerate(
        (
            [prompt("go"), said(big), prompt("next"), said("next answer")],
            [prompt("go"), said("mine"), prompt(big), said("next answer")],
        )
    ):
        (tmp_path / str(i)).mkdir()
        t = tmp_path / str(i) / "s.jsonl"
        end = append(t, records)
        assert reply_text(stop(t), 0, end) is None


def test_a_corrupt_record_is_a_failure(tmp_path: Path) -> None:
    # Codex r5 finding 2: a complete record that can't be parsed fails the read, never a partial reply.
    for i, bad in enumerate(
        ('{"type": "assistant", "message": {"content": [{"type": "te', "[1, 2]", "\udcff")
    ):
        (tmp_path / str(i)).mkdir()
        t = tmp_path / str(i) / "s.jsonl"  # the session's own name: else None anyway
        end = append(t, [prompt("go"), said("first answer")])
        assert reply_text(stop(t), 0, end) == "first answer"
        with t.open("a", errors="surrogateescape") as fh:
            fh.write(bad + "\n")
        assert reply_text(stop(t), 0, t.stat().st_size) is None


def test_fallback_failures_are_none(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), said("hi")])
    link = tmp_path / "l" / "s.jsonl"
    link.parent.mkdir()
    link.symlink_to(t)
    assert reply_text(stop(link), 0, end) is None  # a symlink is never followed
    assert reply_text(stop(t), None, None) is None  # no span
    assert reply_text(stop(tmp_path / "missing" / "s.jsonl"), 0, 10) is None
    assert reply_text(stop(t, sid="other"), 0, end) is None  # not this session's transcript


def test_the_hook_text_wins_over_the_transcript(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), said("from file")])
    assert reply_text(HookEvent("Stop", "s", str(t), "from hook", None), 0, end) == "from hook"
    assert reply_text(HookEvent("Stop", "s", None, "from hook", None), None, None) == "from hook"


def test_transcript_size(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    t.write_bytes(b"x" * 7)
    assert hook.transcript_size(HookEvent("Stop", "s", str(t))) == 7
    link = tmp_path / "l" / "s.jsonl"
    link.parent.mkdir()
    link.symlink_to(t)
    assert hook.transcript_size(HookEvent("Stop", "s", str(link))) is None
    assert hook.transcript_size(HookEvent("Stop", "other", str(t))) is None
    assert hook.transcript_size(HookEvent("Stop", "s", None)) is None


# --- review round 1: bounded text, lone surrogates, a failing audit ---


def test_many_small_records_over_the_budget_are_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook, "MAX_REPLY", 1000)
    path = tmp_path / "s1.jsonl"
    append(path, [prompt("go")] + [said("m" * 100) for _ in range(11)])
    ev = HookEvent("Stop", "s1", transcript_path=str(path))
    assert reply_text(ev, 0, path.stat().st_size) is None
    path2 = tmp_path / "s2.jsonl"
    append(path2, [prompt("go")] + [said("m" * 100) for _ in range(10)])
    ok = reply_text(HookEvent("Stop", "s2", transcript_path=str(path2)), 0, path2.stat().st_size)
    assert ok is not None and len(ok) > 1000  # exactly at the budget is read (joiners don't count)


def test_an_abandoned_read_stops_reading(tmp_path: Path) -> None:
    path = tmp_path / "s1.jsonl"
    append(path, [prompt("go"), said("hello")])
    cancel = threading.Event()
    cancel.set()
    token = hook.READ_CANCEL.set(cancel)
    try:
        assert reply_text(HookEvent("Stop", "s1", transcript_path=str(path)), 0, path.stat().st_size) is None
    finally:
        hook.READ_CANCEL.reset(token)


def test_a_lone_surrogate_makes_the_text_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "s1.jsonl"
    append(path, [prompt("go"), said("bad \ud800 text")])
    assert reply_text(HookEvent("Stop", "s1", transcript_path=str(path)), 0, path.stat().st_size) is None
    assert reply_text(HookEvent("Stop", "s1", last_assistant_message="bad \ud800"), None, None) is None


@needs_tmux
def test_a_surrogate_in_a_transcript_reply_sends_the_notice_to_the_backstop(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        path.write_bytes(b"")
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await h.until(lambda: h.store.get("busy") is not None and h.store.get("turn_start") is not None)
        append(path, [said("lone \ud800 surrogate")])
        await h.daemon.hooks.put(h.event("Stop", sid, str(path)))
        text = await batch_arrives(h)
        assert EXTRACT_FAILED in text and "lone" not in text
        await h.until(lambda: h.store.get("busy") is None)

    run_with(tmp_path, scenario, configure("exit 1"))


@needs_tmux
def test_a_surrogate_in_the_events_own_text_sends_the_notice_to_the_backstop(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await terminal_stop(h, "own \ud800 text")
        text = await batch_arrives(h)
        assert EXTRACT_FAILED in text and "own" not in text
        await h.until(lambda: h.store.get("busy") is None)

    run_with(tmp_path, scenario, configure("exit 1"))


@needs_tmux
def test_a_failing_record_falls_back_without_the_text_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # prepare_reply passes a lone surrogate, as if the check were missed: the insert raises
    # UnicodeEncodeError, and the fallback must not insert that text again.
    monkeypatch.setattr(
        "heterodyne.admind.daemon.prepare_reply", lambda raw, lines, chars: ("x\ud800", "verbatim")
    )

    async def scenario(h: Harness) -> None:
        await terminal_stop(h, "anything")
        text = await batch_arrives(h)
        assert EXTRACT_FAILED in text
        await h.until(lambda: h.store.get("busy") is None)
        assert audited(h, kind="reply", action="record-failed", error="UnicodeEncodeError")

    run_with(tmp_path, scenario, configure("exit 1"))


@needs_tmux
def test_a_failing_audit_does_not_hold_dispatch_on_an_unreadable_turn(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        real = h.daemon.audit.write

        def write(kind: str, **fields: object) -> None:
            if fields.get("action") == "reply-extraction-failed":
                raise OSError("audit filesystem unavailable")
            real(kind, **fields)

        h.daemon.audit.write = write  # type: ignore[method-assign]
        await terminal_stop(h, None, tmp_path / "x.jsonl")
        text = await batch_arrives(h)
        assert EXTRACT_FAILED in text
        await h.until(lambda: h.store.get("busy") is None)

    run_with(tmp_path, scenario, configure("exit 1"))


HUGE = ("an ordinary line of reply text 12345\n" * 500_000)[: 16 * 1024 * 1024]


@needs_tmux
def test_the_heavy_work_on_a_reply_runs_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redacting, sizing and rendering a reply are CPU-bound on a big one, so none may run on the loop."""
    main = threading.get_ident()
    seen: dict[str, bool] = {}

    def off_loop(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any) -> Any:
            seen[name] = threading.get_ident() != main
            return fn(*args)

        return wrapper

    from heterodyne.admind import daemon

    monkeypatch.setattr(daemon, "prepare_reply", off_loop("prepare", daemon.prepare_reply))
    monkeypatch.setattr(daemon, "render_batch", off_loop("render", daemon.render_batch))
    monkeypatch.setattr(summarize, "redact", off_loop("summary", summarize.redact))

    async def scenario(h: Harness) -> None:
        await terminal_stop(h, LONG)
        await batch_arrives(h)
        assert seen == {"prepare": True, "summary": True, "render": True}

    run_with(tmp_path, scenario, configure("exit 1", batch=0.3))


@needs_tmux
def test_a_command_is_answered_while_a_huge_reply_is_processed(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await terminal_stop(h, HUGE)
        mid = await h.say("!ps")
        await h.until(lambda: any(r.get("reply_to_message_id_hex") == mid for r in h.fake.sent), 5)
        await batch_arrives(h)

    run_with(tmp_path, scenario, configure("exit 1", batch=0.3))


@needs_tmux
@pytest.mark.parametrize(
    ("reply", "mode"),
    [
        ("\n".join(f"line {i}" for i in range(8)), "verbatim"),
        ("\n".join(f"line {i}" for i in range(9)), "summary"),
        ("w" * 800, "verbatim"),
        ("w" * 801, "summary"),
    ],
)
def test_the_default_limits_route_at_the_boundary(tmp_path: Path, reply: str, mode: str) -> None:
    async def scenario(h: Harness) -> None:
        assert (h.settings.reply_verbatim_lines, h.settings.reply_verbatim_chars) == (8, 800)
        await terminal_stop(h, reply)
        await h.until(
            lambda: (
                len(h.store.turns_with_status("verbatim"))
                + len(h.store.turns_with_status("summarizing"))
                + len(h.store.turns_with_status("summarized"))
                == 1
            )
        )
        got = [t for s in ("verbatim", "summarizing", "summarized") for t in h.store.turns_with_status(s)][0]
        assert (got.status == "verbatim") == (mode == "verbatim")

    run_with(tmp_path, scenario, configure('echo "SUMMARIZED"'))
