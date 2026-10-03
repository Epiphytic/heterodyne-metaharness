"""Plan 2b Task 9: `!details` and `!details full` (ADR 0001 r13 §8, B12, B13, B19, B20).

Nothing here runs the real claude, touches ~/.claude or the network: the admin agent is the fake claude in
a private tmux server, the summarizer is a shell script in tmp_path, and wn-agent is the fake.
"""

import asyncio
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from admind_waits import wait_until
from fakes.settings import OPERATOR_HEX
from test_admind_daemon import Harness, needs_tmux, run_with
from test_admind_r13_replies import (
    LONG,
    ORIGIN,
    TOKEN,
    append,
    audited,
    configure,
    prompt,
    result,
    said,
    sent_for,
    session,
    terminal_stop,
    tool,
)

from heterodyne.admind import chunk, commands, hook, summarize
from heterodyne.admind.daemon import DETAILS_BUSY, DETAILS_NOT_READ, READY_NOTICE
from heterodyne.admind.hook import turn_tool_calls
from heterodyne.admind.redact import redact
from heterodyne.admind.store import Store

SUMMARY = f"short summary\n\n{summarize.FOOTER}"
NO_TOOLS = "(tool calls are not available for this turn)"


def test_parse_details() -> None:
    assert commands.parse("!details") == commands.Command("details")
    assert commands.parse("!details full") == commands.Command("details", arg="full")
    with pytest.raises(commands.CommandError):
        commands.parse("!details everything")
    with pytest.raises(commands.CommandError):
        commands.parse("!details full now")
    assert "!details [full]" in commands.HELP


# --- turn_tool_calls ---


def test_tool_calls_of_this_turn_only(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    start = append(t, [prompt("old prompt"), tool("Old")])  # an earlier turn
    end = append(
        t,
        [
            prompt("check the gateway"),
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "secret plan"},
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "systemctl --user status x"},
                        },
                    ]
                },
            },
            result("active (running)"),
            said("It is running."),
        ],
    )
    append(t, [prompt("later"), tool("Later")])  # the next turn, after this Stop
    out = turn_tool_calls(t, start, end)
    assert "▸ Bash" in out and "systemctl --user status x" in out and "◂ active (running)" in out
    assert "Old" not in out and "secret plan" not in out and "Later" not in out
    assert "It is running." not in out


def test_metadata_before_the_prompt_does_not_start_the_turn(tmp_path: Path) -> None:
    # Codex r2 finding 6: a queue-operation record before the turn's own prompt.
    t = tmp_path / "s.jsonl"
    end = append(
        t,
        [
            {"type": "queue-operation", "operation": "enqueue", "content": "mine"},
            prompt("mine"),
            {"type": "user", "isMeta": True, "message": {"content": "a caveat"}},
            tool("Mine"),
            result("done"),
            prompt("queued next"),
            tool("Next"),
        ],
    )
    out = turn_tool_calls(t, 0, end)
    assert "▸ Mine" in out and "◂ done" in out and "Next" not in out


def test_a_later_prompt_inside_the_span_ends_the_turn(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("mine"), tool("Mine"), prompt("queued next"), tool("Next")])
    out = turn_tool_calls(t, 0, end)
    assert "Mine" in out and "Next" not in out


def test_no_tool_calls(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("hi"), said("hello")])
    assert turn_tool_calls(t, 0, end) == "(no tool calls in this turn)"


def test_long_turn_keeps_its_first_calls(tmp_path: Path) -> None:
    # More than MAX_TRANSCRIPT (8 MiB) of output between the first and the last call.
    t = tmp_path / "s.jsonl"
    end = append(
        t, [prompt("go"), tool("Early"), *[said("t" * (1024 * 1024)) for _ in range(9)], tool("Late")]
    )
    out = turn_tool_calls(t, 0, end)
    assert "▸ Early" in out and "▸ Late" in out


def test_record_larger_than_a_read_window_is_shown_whole(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), tool("Cat"), result("r" * (2 * 1024 * 1024)), tool("After")])
    out = turn_tool_calls(t, 0, end)
    assert "◂ " + "r" * (2 * 1024 * 1024) + "\n" in out and "▸ After" in out


def test_an_oversized_record_makes_the_turn_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex r3 finding 3, r7 finding 1: an unparsed record could be metadata, an answer or the next
    # prompt, so no position of one may let another turn's tool calls through.
    monkeypatch.setattr(hook, "MAX_RECORD", 1000)
    big = "q" * 5000
    for i, records in enumerate(
        (
            [
                {"type": "queue-operation", "content": big},
                prompt("mine"),
                tool("Mine"),
                prompt("next"),
                tool("Next"),
            ],
            [prompt("mine"), tool("Mine", data=big), prompt("next"), tool("Next")],
            [prompt("mine"), tool("Mine"), prompt(big), tool("Next")],
        )
    ):
        t = tmp_path / f"{i}.jsonl"
        end = append(t, records)
        assert turn_tool_calls(t, 0, end) == hook.TOO_LARGE


def test_non_text_results_are_kept_distinct(tmp_path: Path) -> None:
    # Codex r3 finding 4: structured and image results are rendered, not collapsed to one placeholder.
    t = tmp_path / "s.jsonl"
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}

    def tool_result(**fields: Any) -> dict[str, Any]:
        return {"type": "user", "message": {"content": [{"type": "tool_result", **fields}]}}

    end = append(
        t,
        [
            prompt("go"),
            tool("Shot"),
            tool_result(
                content=[{"type": "text", "text": "saved"}, img, {"type": "resource", "uri": "file:///x"}]
            ),
            tool_result(content={"rows": 2, "ok": True}),
            tool_result(is_error=True, content="denied"),
        ],
    )
    out = turn_tool_calls(t, 0, end)
    assert "◂ saved\n[image image/png, 12 base64 characters, sha256 " in out
    assert '{"type": "resource", "uri": "file:///x"}' in out
    assert '◂ {"ok": true, "rows": 2}' in out
    assert "◂ (error) denied" in out


def test_values_are_redacted_before_they_are_serialized(tmp_path: Path) -> None:
    # Codex r4 finding 1: json.dumps turns "\x01ghp_…" into "\\u0001ghp_…", where the token follows "1".
    token = "ghp_" + "A" * 30
    t = tmp_path / "s.jsonl"
    end = append(
        t,
        [
            prompt("go"),
            tool("Bash", command="\x01" + token),
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "content": {"env": "\x01" + token}}]},
            },
        ],
    )
    out = turn_tool_calls(t, 0, end)
    assert "AAAAAAAAAA" not in out and out.count("<redacted GitHub token>") == 2


def test_keys_that_redact_alike_keep_every_entry(tmp_path: Path) -> None:
    # Codex r5 finding 1: two hex keys both become "<redacted hex key>"; neither entry may be lost.
    both = {"a" * 64: "first result", "b" * 64: "second result"}
    t = tmp_path / "s.jsonl"
    end = append(
        t,
        [
            prompt("go"),
            tool("Bash", **both),
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": both}]}},
        ],
    )
    out = turn_tool_calls(t, 0, end)
    assert out.count("first result") == 2 and out.count("second result") == 2
    assert '"<redacted hex key> #2"' in out and "aaaa" not in out


def test_a_corrupt_record_is_unreadable(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    append(t, [prompt("go"), tool("Bash", command="ls")])
    with t.open("a") as fh:
        fh.write("not json\n")
    assert turn_tool_calls(t, 0, t.stat().st_size) == "(the transcript could not be read)"


def test_a_lone_surrogate_in_a_tool_call_is_replaced_not_raised(tmp_path: Path) -> None:
    # JSON `\\ud800` parses to a lone surrogate, which no database or socket can carry; as an image's data it
    # must not make the digest raise either.
    t = tmp_path / "s.jsonl"
    with t.open("w") as fh:
        for rec in (
            prompt("go"),
            '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", '
            '"input": {"command": "a\\ud800b"}}]}}',
            '{"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": '
            '"image", "source": {"type": "base64", "media_type": "image/png", "data": "\\ud800"}}]}]}}',
        ):
            fh.write((rec if isinstance(rec, str) else json.dumps(rec)) + "\n")
    out = turn_tool_calls(t, 0, t.stat().st_size)
    out.encode("utf-8")
    assert "▸ Bash" in out and "[image image/png, 1 base64 characters, sha256 " in out


def structured(value: str) -> list[Any]:
    """Untrusted structured values for a field that is shown inline: a dict and a list holding `value`."""
    return [{"label": value}, [value], {"a": [{"b": value}]}]


@pytest.mark.parametrize("sep", ["\n", "\t", "\x01"])
def test_structured_names_and_media_types_are_redacted_before_they_are_formatted(
    tmp_path: Path, sep: str
) -> None:
    # Codex T9 r1 finding 1: str() of {"label": "\nghp_…"} turns the newline into the letter n, which
    # defeats the scanner's lookbehind.
    token = "ghp_" + "A" * 30
    t = tmp_path / "s.jsonl"
    records: list[dict[str, Any]] = [prompt("go")]
    for value in structured(sep + token):
        records.append(
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": value, "input": {}}]}}
        )
        source = {"type": "base64", "media_type": value, "data": "iVBORw0KGgo="}
        content = [{"type": "image", "source": source}]
        records.append(
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": content}]}}
        )
    end = append(t, records)
    out = redact(turn_tool_calls(t, 0, end))
    assert "AAAAAAAAAA" not in out and out.count("<redacted GitHub token>") == 6
    assert "▸ " in out and "[image " in out


def test_non_string_names_are_serialized_not_formatted(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(
        t,
        [
            prompt("go"),
            tool("x"),
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": None, "input": {}},
                        {"type": "tool_use", "name": 7, "input": {}},
                    ]
                },
            },
        ],
    )
    assert "▸ null {}\n▸ 7 {}" in turn_tool_calls(t, 0, end)


def test_no_total_cap(tmp_path: Path) -> None:
    # §8: `!details` has no length cap (Codex r2 finding 5).
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), *[tool(f"T{i}", n="x" * 1000) for i in range(300)]])
    out = turn_tool_calls(t, 0, end)
    assert all(f"▸ T{i} " in out for i in range(300)) and len(out) > 300_000


def test_unreadable_transcript(tmp_path: Path) -> None:
    link = tmp_path / "s.jsonl"
    link.symlink_to(tmp_path / "elsewhere.jsonl")
    assert turn_tool_calls(link, 0, 10) == "(the transcript could not be read)"


# --- store: a delivered post is found again after a restart ---


def test_details_target_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    s = Store(path)
    turn = s.add_turn("k", "s", None, "o", "t", None, None, None, "summarized")
    s.enqueue("sum:1", "summary", None)
    s.record_post("sum:1", "summary", turn, None)
    row = s.next_pending()
    assert row is not None
    s.mark_sent(row.seq, "m")
    s.close()
    again = Store(path)
    by_id = again.details_target("m")
    assert by_id is not None and by_id.key == "sum:1" and by_id.turn_id == turn
    assert again.details_target(None) == by_id


# --- the daemon ---


async def ask(h: Harness, text: str, reply_to: str | None = None) -> str:
    """Send an operator message (optionally a reply to a message); return its ID."""
    h.seq += 1
    mid = f"{h.seq:064x}"
    await h.fake.push_event(h.fake.message_event(text, OPERATOR_HEX, mid, reply_to=reply_to))
    return mid


def details_texts(h: Harness, mid: str) -> list[str]:
    rows = h.store.db.execute(
        "SELECT text FROM outbox WHERE key LIKE ? ORDER BY seq", (f"details:{mid}:%",)
    ).fetchall()
    return [r[0] for r in rows]


def delivered(h: Harness, mid: str, n: int | None = None) -> bool:
    rows = h.store.db.execute(
        "SELECT status FROM outbox WHERE key LIKE ? ORDER BY seq", (f"details:{mid}:%",)
    ).fetchall()
    return bool(rows) and all(r[0] == "sent" for r in rows) and (n is None or len(rows) == n)


def summary_message_id(h: Harness) -> str:
    row = h.store.db.execute(
        "SELECT message_id FROM outbox WHERE key LIKE '%:s0' AND status = 'sent'"
    ).fetchone()
    return str(row[0])


async def summary_delivered(h: Harness) -> str:
    await wait_until(
        lambda: (
            SUMMARY in h.texts()
            and h.store.db.execute(
                "SELECT 1 FROM outbox WHERE key LIKE '%:s0' AND status = 'sent'"
            ).fetchone()
        ),
        20,
    )
    return summary_message_id(h)


@needs_tmux
def test_details_on_a_summary_returns_the_whole_reply_in_lane_two(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say(LONG)
        sid = await summary_delivered(h)
        mid = await ask(h, "!details", reply_to=sid)
        await wait_until(lambda: delivered(h, mid), 20)
        parts = details_texts(h, mid)
        assert len(parts) > 1 and all(len(p) <= 300 for p in parts)
        whole = "".join(parts)
        assert ORIGIN.search(whole) and whole.endswith(f"echo: {LONG}")
        sent = [r for r in h.fake.sent if r["idempotency_key"].startswith(f"details:{mid}:")]
        assert {r["reply_to_message_id_hex"] for r in sent} == {mid}
        lanes = h.store.db.execute("SELECT DISTINCT lane FROM outbox WHERE key LIKE ?", (f"details:{mid}:%",))
        assert [r[0] for r in lanes.fetchall()] == [2]
        assert audited(h, kind="command", command="details", full=False)

    run_with(tmp_path, scenario, configure('echo "short summary"'), {"chunk_chars": 300})


@needs_tmux
def test_details_without_a_target_expands_the_latest_summary(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say(LONG)
        await summary_delivered(h)
        mid = await ask(h, "!details")
        await wait_until(lambda: delivered(h, mid), 20)
        assert details_texts(h, mid)[0].count(f"echo: {LONG}") == 1

    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
def test_details_on_a_batch_returns_every_reply_in_order(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await terminal_stop(h, "first " + "y" * 900)
        await terminal_stop(h, "second " + "z" * 900)
        await wait_until(lambda: len(h.store.turns_with_status("batched")) == 2, 20)
        h.daemon.batch_seconds = 0.2
        await wait_until(lambda: any(t.startswith("⚠️ Replies batched") for t in h.texts()), 20)
        row = h.store.db.execute("SELECT message_id FROM outbox WHERE key LIKE 'batch:%'").fetchone()
        mid = await ask(h, "!details", reply_to=row[0])
        await wait_until(lambda: delivered(h, mid), 20)
        whole = "\n".join(details_texts(h, mid))
        assert whole.count("— terminal ·") == 2
        assert whole.index("first " + "y" * 900) < whole.index("second " + "z" * 900)

    run_with(tmp_path, scenario, configure("exit 1", batch=3600))


@needs_tmux
def test_details_full_adds_this_turns_tool_calls_and_no_thinking(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        append(path, [prompt("old"), tool("Before"), result("earlier output")])
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await wait_until(lambda: h.store.get("turn_start") is not None)
        append(
            path,
            [
                prompt("go"),
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "private musings"},
                            {"type": "tool_use", "name": "Bash", "input": {"command": "uptime"}},
                        ]
                    },
                },
                result("up 3 days " + TOKEN),
            ],
        )
        await h.daemon.hooks.put(h.event("Stop", sid, str(path), LONG))
        await summary_delivered(h)
        mid = await ask(h, "!details full")
        await wait_until(lambda: delivered(h, mid), 20)
        whole = "".join(details_texts(h, mid))
        assert LONG in whole and "▸ Bash" in whole and "uptime" in whole and "◂ up 3 days" in whole
        assert "private musings" not in whole and "Before" not in whole and "earlier output" not in whole
        assert TOKEN not in whole and "<redacted GitHub token>" in whole
        assert audited(h, kind="command", command="details", full=True)

    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
def test_details_full_on_a_turn_without_a_span(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await terminal_stop(h, LONG)  # no UserPromptSubmit: no span
        await wait_until(lambda: SUMMARY in h.texts(), 20)
        mid = await ask(h, "!details full")
        await wait_until(lambda: delivered(h, mid), 20)
        assert details_texts(h, mid)[0].endswith(f"\n\n{NO_TOOLS}")

    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
def test_details_full_says_so_when_the_reader_is_busy(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        append(path, [prompt("old")])
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await wait_until(lambda: h.store.get("turn_start") is not None)
        append(path, [prompt("go"), tool("Bash", command="ls"), result("x")])
        await h.daemon.hooks.put(h.event("Stop", sid, str(path), LONG))
        await summary_delivered(h)
        busy = asyncio.get_running_loop().create_future()
        h.daemon._reader = busy  # pyright: ignore[reportPrivateUsage]
        mid = await ask(h, "!details full")
        await wait_until(lambda: delivered(h, mid), 20)
        assert details_texts(h, mid)[0].endswith(f"\n\n{DETAILS_BUSY}")
        assert "▸ Bash" not in "".join(details_texts(h, mid))
        busy.set_result(None)
        h.daemon._reader = None  # pyright: ignore[reportPrivateUsage]

    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
def test_a_slow_transcript_read_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def slow(path: Path, start: int, end: int, limit: int | None = None) -> tuple[str, bool]:
        release.wait(5)
        return "late", False

    monkeypatch.setattr("heterodyne.admind.daemon.render_tool_calls", slow)

    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        append(path, [prompt("old")])
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await wait_until(lambda: h.store.get("turn_start") is not None)
        append(path, [prompt("go"), tool("Bash", command="ls")])
        await h.daemon.hooks.put(h.event("Stop", sid, str(path), LONG))
        await summary_delivered(h)
        h.daemon.details_timeout = 0.1
        mid = await ask(h, "!details full")
        try:
            await wait_until(lambda: delivered(h, mid), 20)
        finally:
            release.set()
        assert details_texts(h, mid)[0].endswith(f"\n\n{DETAILS_BUSY}")

    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
def test_details_on_a_message_with_no_record(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("hello")  # the first operator message posts the ready notice
        await wait_until(
            lambda: h.store.db.execute(
                "SELECT 1 FROM outbox WHERE key = 'ready' AND status = 'sent'"
            ).fetchone()
        )
        row = h.store.db.execute("SELECT message_id FROM outbox WHERE key = 'ready'").fetchone()
        assert sent_for(h, READY_NOTICE)["idempotency_key"] == "ready"
        mid = await ask(h, "!details", reply_to=row[0])
        want = "!details: that message has no details. Reply to a summary or a batch."
        await wait_until(lambda: want in h.texts())
        assert sent_for(h, want)["reply_to_message_id_hex"] == mid
        assert details_texts(h, mid) == []
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE lane = 2").fetchone()[0] == 0

    run_with(tmp_path, scenario)


@needs_tmux
def test_details_with_nothing_delivered_yet(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        mid = await ask(h, "!details")
        want = "!details: there is no delivered summary or batch yet."
        await wait_until(lambda: want in h.texts())
        assert sent_for(h, want)["reply_to_message_id_hex"] == mid

    run_with(tmp_path, scenario, configure(None))


@needs_tmux
def test_an_alert_is_sent_before_the_rest_of_a_long_details(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("long " + "w" * 3000)
        sid = await summary_delivered(h)
        relayed: list[bool] = []

        def on_send(req: dict[str, Any]) -> None:
            if str(req["idempotency_key"]).startswith("details:") and not relayed:
                relayed.append(True)  # an alert arrives while the first chunk is going out
                h.daemon.post("alert:x", "urgent alert", None)

        h.fake.on_send = on_send
        mid = await ask(h, "!details", reply_to=sid)
        await wait_until(lambda: delivered(h, mid), 20)
        keys = [str(r["idempotency_key"]) for r in h.fake.sent]
        last = f"details:{mid}:{len(details_texts(h, mid)) - 1}"
        assert len(details_texts(h, mid)) > 3
        assert keys.index(f"details:{mid}:0") < keys.index("alert:x") < keys.index(f"details:{mid}:1")
        assert keys.index("alert:x") < keys.index(last)

    run_with(tmp_path, scenario, configure('echo "short summary"'), {"chunk_chars": 200})


@needs_tmux
def test_details_after_a_restart(tmp_path: Path) -> None:
    async def first(h: Harness) -> None:
        await h.say(LONG)
        await summary_delivered(h)

    run_with(tmp_path, first, configure('echo "short summary"'))
    state: dict[str, str] = {}

    async def second(h: Harness) -> None:
        h.seq = 100  # message IDs the first run used would be dropped as replays
        state["id"] = summary_message_id(h)
        mid = await ask(h, "!details", reply_to=state["id"])
        await wait_until(lambda: delivered(h, mid), 20)
        assert f"echo: {LONG}" in "".join(details_texts(h, mid))

    run_with(tmp_path, second, configure('echo "short summary"'))


@needs_tmux
def test_details_is_refused_when_admind_is_no_longer_authorised(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say(LONG)
        await summary_delivered(h)
        mid = f"{0xABC:064x}"
        assert h.store.claim_inbound(mid)
        h.daemon.remember_sender(mid, OPERATOR_HEX)
        h.daemon.changing = True  # a membership transition: nothing is posted meanwhile
        await h.daemon.details(mid, commands.Command("details"), None)
        assert (
            h.store.db.execute("SELECT status FROM inbound WHERE message_id = ?", (mid,)).fetchone()[0]
            == "dropped"
        )
        assert details_texts(h, mid) == []
        h.daemon.changing = False

    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
def test_details_full_is_refused_if_admind_stops_being_authorised_during_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow(path: Path, start: int, end: int, limit: int | None = None) -> tuple[str, bool]:
        started.set()
        release.wait(10)
        return "▸ Bash {}", False

    monkeypatch.setattr("heterodyne.admind.daemon.render_tool_calls", slow)

    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        append(path, [prompt("old")])
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await wait_until(lambda: h.store.get("turn_start") is not None)
        append(path, [prompt("go"), tool("Bash")])
        await h.daemon.hooks.put(h.event("Stop", sid, str(path), LONG))
        await summary_delivered(h)
        mid = await ask(h, "!details full")
        await wait_until(started.is_set)
        h.daemon.changing = True  # a membership transition begins while the transcript is being read
        release.set()
        await wait_until(
            lambda: (
                h.store.db.execute("SELECT status FROM inbound WHERE message_id = ?", (mid,)).fetchone()[0]
                == "dropped"
            )
        )
        assert details_texts(h, mid) == []
        h.daemon.changing = False

    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
@pytest.mark.parametrize("sep", ["\n", "\t"])
def test_details_full_never_shows_a_secret_hidden_in_a_structured_name(tmp_path: Path, sep: str) -> None:
    token = "ghp_" + "B" * 30

    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        append(path, [prompt("old")])
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await wait_until(lambda: h.store.get("turn_start") is not None)
        value = {"label": sep + token}
        source = {"type": "base64", "media_type": value, "data": "iVBORw0KGgo="}
        append(
            path,
            [
                prompt("go"),
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": value, "input": {}}]},
                },
                {
                    "type": "user",
                    "message": {
                        "content": [{"type": "tool_result", "content": [{"type": "image", "source": source}]}]
                    },
                },
            ],
        )
        await h.daemon.hooks.put(h.event("Stop", sid, str(path), LONG))
        await summary_delivered(h)
        mid = await ask(h, "!details full")
        await wait_until(lambda: delivered(h, mid), 20)
        whole = "".join(details_texts(h, mid))
        assert "BBBBBBBBBB" not in whole and whole.count("<redacted GitHub token>") == 2
        assert not any("BBBBBBBBBB" in t for t in h.texts())

    run_with(tmp_path, scenario, configure('echo "short summary"'))


def old_split(text: str, limit: int) -> list[str]:
    """The quadratic implementation chunk.split replaced, as the reference for what it returns."""
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        newline = rest.rfind("\n", 0, limit)
        cut = newline + 1 if newline >= limit // 2 else limit
        out.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        out.append(rest)
    return out


@pytest.mark.parametrize("limit", [1, 2, 7, 40, 200])
def test_chunking_returns_what_it_always_did(limit: int) -> None:
    text = "".join(f"line {i}\n" * (i % 3) + "x" * (i % 50) for i in range(300))
    assert chunk.split(text, limit) == old_split(text, limit)


def test_chunking_is_linear() -> None:
    # Codex T9 r1 finding 3: copying the remainder per chunk took 2.3 s for 8 MiB at 200 characters.
    text = ("y" * 99 + "\n") * (8 * 1024 * 1024 // 100)
    start = time.perf_counter()
    parts = chunk.split(text, 200)
    assert time.perf_counter() - start < 1.0
    assert "".join(parts) == text


BATCH_MESSAGE = "bb" * 32


def plant_batch(h: Harness, path: Path, sid: str, n: int) -> None:
    """A delivered backstop batch of `n` turns that each have a span in `path`."""
    size = append(path, [prompt("go"), tool("Bash", command="ls"), result("out")])
    batch = h.store.open_batch(time.time(), 3600)
    for i in range(n):
        turn = h.store.add_turn(
            f"k{i}", sid, None, f"— op · 12:00 UTC · “t{i}”", f"reply {i}", str(path), 0, size, "summarizing"
        )
        h.store.add_to_batch(turn, batch)
    h.store.close_batch(batch)
    h.store.enqueue("batch:1.0", "batch", None)
    h.store.record_post("batch:1.0", "batch", None, batch)
    row = h.store.next_pending()
    assert row is not None
    h.store.mark_sent(row.seq, BATCH_MESSAGE)


def cooperative_reader(seconds: float, seen: list[str]) -> Any:
    """A transcript reader that takes `seconds`, in steps, and stops as soon as READ_CANCEL is set."""

    def read(path: Path, start: int, end: int, limit: int | None = None) -> tuple[str, bool]:
        cancel = hook.READ_CANCEL.get()
        assert cancel is not None
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if cancel.is_set():
                seen.append("cancelled")
                raise OSError("the read was abandoned")
            time.sleep(0.01)
        seen.append("read")
        return "▸ Bash done", False

    return read


@needs_tmux
def test_one_details_full_has_one_budget_for_all_its_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex T9 r1 finding 2: 30 s per turn x N turns held the work lock for as long as that.
    seen: list[str] = []
    monkeypatch.setattr("heterodyne.admind.daemon.render_tool_calls", cooperative_reader(0.4, seen))

    async def scenario(h: Harness) -> None:
        sid = await session(h)
        plant_batch(h, tmp_path / f"{sid}.jsonl", sid, 6)
        h.daemon.details_timeout = 1.0
        started = time.monotonic()
        mid = await ask(h, "!details full", reply_to=BATCH_MESSAGE)
        await wait_until(lambda: delivered(h, mid), 20)
        elapsed = time.monotonic() - started
        whole = "".join(details_texts(h, mid))
        assert elapsed < 2.5  # about the budget, not 6 x 0.4 s of reads plus
        assert whole.count("▸ Bash done") == 2 and whole.count(DETAILS_NOT_READ) == 3
        assert whole.count(DETAILS_BUSY) == 1  # the read the budget ran out in
        assert seen.count("read") == 2 and "cancelled" in seen

    run_with(tmp_path, scenario, configure(None))


def test_rendering_stops_at_the_limit_with_whole_lines(tmp_path: Path) -> None:
    # Codex T9 r1 finding 3: many individually legal records must not add up without bound.
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), *[tool(f"T{i}", n="x" * 100) for i in range(1000)]])
    out, cut = hook.render_tool_calls(t, 0, end, 5000)
    assert cut and len(out.encode()) <= 5000
    lines = out.split("\n")
    assert lines[0].startswith("▸ T0 ") and all(ln.endswith('"}') for ln in lines)  # none cut in part
    whole, cut_whole = hook.render_tool_calls(t, 0, end)
    assert not cut_whole and whole.count("\n") == 999


def test_a_lone_surrogate_is_replaced_line_by_line_and_counted_as_the_byte_it_becomes(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    with t.open("w") as fh:
        fh.write(json.dumps(prompt("go")) + "\n")
        fh.write(
            '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "B", '
            '"input": {"c": "a\\ud800b"}}]}}\n'
        )
    out, cut = hook.render_tool_calls(t, 0, t.stat().st_size, 10_000)
    assert not cut and "?" in out
    out.encode("utf-8")


@needs_tmux
def test_details_full_is_capped_in_total_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("heterodyne.admind.daemon.MAX_REPLY", 4000)

    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        append(path, [prompt("old")])
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await wait_until(lambda: h.store.get("turn_start") is not None)
        append(path, [prompt("go"), *[tool(f"T{i}", n="x" * 100) for i in range(200)]])
        await h.daemon.hooks.put(h.event("Stop", sid, str(path), LONG))
        await summary_delivered(h)
        mid = await ask(h, "!details full")
        await wait_until(lambda: delivered(h, mid), 20)
        whole = "".join(details_texts(h, mid))
        assert whole.endswith(hook.TRUNCATED) and len(whole) < 4500
        assert "▸ T0 " in whole and "▸ T199 " not in whole

    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
def test_details_on_a_big_batch_is_capped_in_total_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("heterodyne.admind.daemon.MAX_REPLY", 600)

    async def scenario(h: Harness) -> None:
        sid = await session(h)
        plant_batch(h, tmp_path / f"{sid}.jsonl", sid, 50)
        mid = await ask(h, "!details", reply_to=BATCH_MESSAGE)
        await wait_until(lambda: delivered(h, mid), 20)
        whole = "".join(details_texts(h, mid))
        assert whole.endswith(hook.TRUNCATED) and "reply 0" in whole and "reply 49" not in whole
        assert len(whole) < 1000

    run_with(tmp_path, scenario, configure(None))


def staged(h: Harness) -> int:
    return int(h.store.db.execute("SELECT COUNT(*) FROM details_stage").fetchone()[0])


def inbound_status(h: Harness, mid: str) -> str:
    row = h.store.db.execute("SELECT status FROM inbound WHERE message_id = ?", (mid,)).fetchone()
    return "" if row is None else str(row[0])


def on_stage(h: Harness, after: Callable[[int, int], object]) -> None:
    """Call `after(call number, parts so far)` once each slice of a !details is staged."""
    real = h.store.stage_details
    seen = {"calls": 0, "parts": 0}

    def stage(token: str, first: int, parts: list[str]) -> None:
        real(token, first, parts)
        seen["calls"] += 1
        seen["parts"] += len(parts)
        after(seen["calls"], seen["parts"])

    h.store.stage_details = stage  # type: ignore[method-assign]


@needs_tmux
def test_an_alert_is_delivered_while_a_large_details_is_still_being_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex T9 r1 finding 4: publishing used to be one synchronous transaction; nothing ran meanwhile.
    monkeypatch.setattr("heterodyne.admind.daemon.DETAILS_SLICE", 2)

    async def scenario(h: Harness) -> None:
        await h.say("long " + "w" * 60000)
        sid = await summary_delivered(h)
        mid_holder: list[str] = []
        snapshot: dict[str, Any] = {}

        def on_send(req: dict[str, Any]) -> None:
            if req["idempotency_key"] == "alert:x":
                snapshot["status"] = inbound_status(h, mid_holder[0])
                snapshot["visible"] = len(details_texts(h, mid_holder[0]))

        h.fake.on_send = on_send
        on_stage(h, lambda calls, parts: calls == 3 and h.daemon.post("alert:x", "urgent alert", None))
        mid_holder.append(await ask(h, "!details", reply_to=sid))
        mid = mid_holder[0]
        await wait_until(lambda: delivered(h, mid), 30)
        assert snapshot == {"status": "executing", "visible": 0}  # sent mid-preparation, nothing partial
        assert len(details_texts(h, mid)) > 100 and staged(h) == 0

    run_with(tmp_path, scenario, configure('echo "short summary"'), {"chunk_chars": 200})


@needs_tmux
@pytest.mark.parametrize("how", ["latch", "transition", "sender"])
@pytest.mark.parametrize("slice_size", [1, 10_000])
def test_a_guard_that_fails_while_staging_leaves_nothing_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str, slice_size: int
) -> None:
    # slice 1: caught between slices; 10_000: one slice, so only the check just before the publish can
    monkeypatch.setattr("heterodyne.admind.daemon.DETAILS_SLICE", slice_size)

    def trip(h: Harness) -> None:
        if how == "latch":
            h.daemon.latch("test latch")  # the real thing, not a hand-set flag
        elif how == "transition":
            h.daemon.changing = True
        else:
            h.daemon.operators.pop(OPERATOR_HEX)  # the sender is no longer an authorised operator

    async def scenario(h: Harness) -> None:
        await h.say("long " + "w" * 6000)
        sid = await summary_delivered(h)
        on_stage(h, lambda calls, parts: calls == 1 and trip(h))
        mid = await ask(h, "!details", reply_to=sid)
        await wait_until(lambda: inbound_status(h, mid) == "dropped")
        assert details_texts(h, mid) == [] and staged(h) == 0
        h.daemon.changing = False

    run_with(tmp_path, scenario, configure('echo "short summary"'), {"chunk_chars": 200})


@needs_tmux
def test_a_failure_while_staging_discards_what_was_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("heterodyne.admind.daemon.DETAILS_SLICE", 1)

    async def scenario(h: Harness) -> None:
        await h.say("long " + "w" * 6000)
        sid = await summary_delivered(h)

        failed: list[bool] = []

        def boom(calls: int, parts: int) -> None:
            if calls == 3:
                failed.append(True)
                raise RuntimeError("staging failed")

        on_stage(h, boom)
        mid = await ask(h, "!details", reply_to=sid)
        await wait_until(lambda: failed and staged(h) == 0)
        assert details_texts(h, mid) == [] and staged(h) == 0

    run_with(tmp_path, scenario, configure('echo "short summary"'), {"chunk_chars": 200})


@needs_tmux
def test_an_old_staging_is_purged_when_admind_restarts(tmp_path: Path) -> None:
    async def first(h: Harness) -> None:
        h.store.stage_details("dead", 0, ["a", "b"])

    run_with(tmp_path, first, configure(None))

    async def second(h: Harness) -> None:
        assert staged(h) == 0

    run_with(tmp_path, second, configure(None))


@needs_tmux
def test_the_sender_losing_authority_during_the_read_is_noticed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # every other check (latch, changing, group, subscription) still passes: only the sender is gone
    started = threading.Event()
    release = threading.Event()

    def slow(path: Path, start: int, end: int, limit: int | None = None) -> tuple[str, bool]:
        started.set()
        release.wait(10)
        return "▸ Bash {}", False

    monkeypatch.setattr("heterodyne.admind.daemon.render_tool_calls", slow)

    async def scenario(h: Harness) -> None:
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        append(path, [prompt("old")])
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await wait_until(lambda: h.store.get("turn_start") is not None)
        append(path, [prompt("go"), tool("Bash")])
        await h.daemon.hooks.put(h.event("Stop", sid, str(path), LONG))
        await summary_delivered(h)
        mid = await ask(h, "!details full")
        await wait_until(started.is_set)
        h.daemon.operators.pop(OPERATOR_HEX)
        assert not h.daemon.sender_current(mid) and h.daemon.authorised()
        release.set()
        await wait_until(lambda: inbound_status(h, mid) == "dropped")
        assert details_texts(h, mid) == []

    run_with(tmp_path, scenario, configure('echo "short summary"'))
