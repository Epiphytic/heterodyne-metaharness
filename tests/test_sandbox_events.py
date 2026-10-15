"""Plan 5i part 1 T3: the per-generation session event log."""

import errno
import json
import os
import threading
from pathlib import Path

import pytest

from heterodyne.sandbox import events
from heterodyne.sandbox.events import ACCEPTED, FULL, UNAVAILABLE, EventLog
from heterodyne.wsd.runtime import SessionEvent

OPS = frozenset({"close", "ask", "question"})
RID = "0123456789abcdef"
ARGS = ("alpha", "btq-1", "sk-a", 1)


def log(tmp_path: Path) -> EventLog:
    lg = EventLog.open(tmp_path / "r1-session-events.jsonl", *ARGS)
    lg.forwarding = True
    return lg


def reopen(lg: EventLog) -> EventLog:
    lg.close()
    again = EventLog.open(lg.path, *ARGS)
    again.forwarding = True
    return again


def stop(text: object = "done") -> dict[str, object]:
    return {"hook_event_name": "Stop", "last_assistant_message": text}


def close(rid: str = RID) -> dict[str, object]:
    return {"op": "close", "rid": rid, "evidence": "it works"}


def ids(lg: EventLog) -> list[str]:
    return [e.event_id for e in lg.events()]


def test_events_are_numbered_from_the_log_and_survive_a_reload(tmp_path: Path) -> None:
    lg = log(tmp_path)
    a = lg.stop(stop("one"))
    b, _ = lg.request(close(), OPS)
    c = lg.stop(stop("two"))
    assert [e.event_id for e in (a, b, c) if isinstance(e, SessionEvent)] == [
        "sk-a:g1:e1", "sk-a:g1:e2", "sk-a:g1:e3"]
    assert isinstance(c, SessionEvent) and (c.turn, c.close_requested) == (2, True)
    again = reopen(lg)
    assert again.events() == [a, b, c] and again.last == 3
    d = again.stop(stop("three"))
    assert isinstance(d, SessionEvent) and (d.event_id, d.turn, d.close_requested) == ("sk-a:g1:e4", 3, True)
    assert EventLog.read(lg.path, *ARGS).events() == [a, b, c, d]


def test_after_pages_the_events_past_a_cursor(tmp_path: Path) -> None:
    lg = log(tmp_path)
    for i in range(5):
        lg.stop(stop(str(i)))
    assert [e.seq for e in lg.after(1, 2)] == [2, 3] and [e.seq for e in lg.after(4, 10)] == [5]
    assert lg.after(5, 10) == []


def test_a_request_carries_the_turn_it_was_sent_in(tmp_path: Path) -> None:
    lg = log(tmp_path)
    lg.stop(stop())
    q, reply = lg.request({"op": "question", "rid": RID, "body": {"text": "which?"}}, OPS)
    assert isinstance(q, SessionEvent) and (q.kind, q.turn, q.body, q.text) == (
        "question", 2, {"text": "which?"}, None)
    assert reply == events.reply(q)


def test_a_repeated_rid_answers_as_the_first_and_is_never_logged_or_delivered_again(tmp_path: Path) -> None:
    lg = log(tmp_path)
    first, reply = lg.request(close(), OPS)
    assert isinstance(first, SessionEvent)
    for _ in range(10_000):
        assert lg.request(close(), OPS) == (None, reply)     # Codex cycle 1 #5: no new event to queue or wake
    assert len(lg.path.read_text().splitlines()) == 1 and lg.events() == [first]
    assert reopen(lg).request(close(), OPS) == (None, reply)  # the rids are read back after a restart


def test_appends_from_many_threads_are_numbered_in_file_order(tmp_path: Path) -> None:
    lg = log(tmp_path)

    def burst(i: int) -> None:
        for j in range(50):
            if j % 2:
                lg.stop(stop(f"{i}-{j}"))
            else:
                lg.request(close(f"{i:08x}{j:08x}"), OPS)

    threads = [threading.Thread(target=burst, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = [json.loads(line) for line in lg.path.read_text().splitlines()]
    assert [r["n"] for r in rows] == list(range(1, 401))
    assert [e.seq for e in lg.events()] == list(range(1, 401))     # memory, file and IDs agree


def test_stop_text_is_cut_or_absent(tmp_path: Path) -> None:
    lg = log(tmp_path)
    long = lg.stop(stop("w" * (events.STOP_TEXT_MAX + 50)))
    missing = lg.stop({"hook_event_name": "Stop"})
    odd = lg.stop(stop(["not", "text"]))
    assert isinstance(long, SessionEvent) and long.text == "w" * events.STOP_TEXT_MAX
    assert isinstance(missing, SessionEvent) and missing.text is None
    assert isinstance(odd, SessionEvent) and odd.text is None


def test_nothing_is_forwarded_before_the_brief(tmp_path: Path) -> None:
    lg = EventLog.open(tmp_path / "r1-session-events.jsonl", *ARGS)
    assert lg.stop(stop("the probe's turn")) is None
    assert lg.request(close(), OPS) == (None, {"ok": False, "error": "unsupported"})
    assert not lg.path.exists()


@pytest.mark.parametrize("payload", [
    {"op": "close", "rid": RID},
    {"op": "close", "rid": RID, "evidence": ""},
    {"op": "close", "rid": "short", "evidence": "x"},
    {"op": "close", "rid": RID, "evidence": "\ud800"},
    {"op": "ask", "rid": RID, "body": [1]},
    {"op": "ask", "rid": RID},
])
def test_malformed_requests_are_refused_and_not_logged(tmp_path: Path, payload: dict[str, object]) -> None:
    lg = log(tmp_path)
    assert lg.request(payload, OPS) == (None, {"ok": False, "error": "malformed"})
    assert not lg.path.exists()


def test_an_op_wsd_has_not_enabled_is_unsupported(tmp_path: Path) -> None:
    lg = log(tmp_path)
    assert lg.request({"op": "ask", "rid": RID, "body": {}}, frozenset({"close"})) == (
        None, {"ok": False, "error": "unsupported"})


def test_a_full_log_refuses_requests_and_drops_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "LOG_MAX", 200)
    lg = log(tmp_path)
    while isinstance(lg.stop(stop("x" * 40)), SessionEvent):
        pass
    assert lg.request(close(), OPS) == (None, FULL)
    assert lg.path.stat().st_size <= 200


def test_a_torn_tail_is_cut_before_the_next_append_and_stays_gone(tmp_path: Path,
                                                                  caplog: pytest.LogCaptureFixture) -> None:
    lg = log(tmp_path)
    lg.stop(stop("one"))
    lg.close()
    with lg.path.open("ab") as fh:
        fh.write(b'{"n": 2, "kind": "st')          # a crash mid-write: never answered
    assert ids(EventLog.read(lg.path, *ARGS)) == ["sk-a:g1:e1"]
    assert lg.path.read_bytes().endswith(b'"st')    # reading never repairs
    again = EventLog.open(lg.path, *ARGS)
    again.forwarding = True
    assert lg.path.read_bytes().endswith(b"\n")     # opening to append does, before any append
    assert events.DIAG_EVENT_LOG_TORN in caplog.text  # fixed text: no line content
    nxt = again.stop(stop("two"))
    assert isinstance(nxt, SessionEvent) and nxt.event_id == "sk-a:g1:e2"
    third = reopen(again)                           # Codex cycle 1 #1: the accepted e2 is still there
    assert ids(third) == ["sk-a:g1:e1", "sk-a:g1:e2"] and not third.damaged


def test_a_short_write_is_finished(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lg = log(tmp_path)
    real = os.write

    def short(fd: int, data: bytes | memoryview) -> int:
        return real(fd, bytes(data[:7]))            # at most 7 bytes a call

    monkeypatch.setattr(events, "_write_fd", short)
    ev, reply = lg.request(close(), OPS)
    monkeypatch.undo()
    assert isinstance(ev, SessionEvent) and reply["ok"] is True
    assert ids(reopen(lg)) == ["sk-a:g1:e1"]


def test_a_failed_write_is_cut_back_and_the_log_goes_on(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    lg = log(tmp_path)
    lg.stop(stop("one"))
    real, calls = os.write, [0]

    def fails_after_some(fd: int, data: bytes | memoryview) -> int:
        calls[0] += 1
        if calls[0] == 1:
            return real(fd, bytes(data[:5]))        # part of the line reaches the file
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(events, "_write_fd", fails_after_some)
    assert lg.request(close(), OPS) == (None, UNAVAILABLE)
    monkeypatch.undo()
    assert lg.path.read_bytes().endswith(b"\n") and lg.writable
    ev, _ = lg.request(close(), OPS)                # the agent's retry: a fresh line, the next number
    assert isinstance(ev, SessionEvent) and ev.event_id == "sk-a:g1:e2"
    assert ids(reopen(lg)) == ["sk-a:g1:e1", "sk-a:g1:e2"]


def test_a_failed_fsync_stops_the_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lg = log(tmp_path)
    lg.stop(stop("one"))

    def fails(_fd: int) -> None:
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(events, "_fsync", fails)
    assert lg.request(close(), OPS) == (None, UNAVAILABLE)
    monkeypatch.undo()
    assert not lg.writable and ids(lg) == ["sk-a:g1:e1"]           # readers never saw the unknown line
    assert lg.request(close(f"{1:016x}"), OPS) == (None, UNAVAILABLE)
    assert lg.stop(stop("dropped")) is None


def three(tmp_path: Path) -> tuple[EventLog, list[bytes]]:
    lg = log(tmp_path)
    for text in ("one", "two", "three"):
        lg.stop(stop(text))
    lg.close()
    return lg, lg.path.read_bytes().splitlines(keepends=True)


def test_a_line_that_wont_decode_is_a_damaged_event_and_the_log_goes_on(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    lg, lines = three(tmp_path)
    lg.path.write_bytes(lines[0] + b"not json\n" + lines[2])      # e2 is unreadable
    again = EventLog.open(lg.path, *ARGS)
    again.forwarding = True
    assert again.damaged and again.writable
    assert [(e.event_id, e.kind) for e in again.events()] == [
        ("sk-a:g1:e1", "stop"), ("sk-a:g1:e2", "damaged"), ("sk-a:g1:e3", "stop")]
    gap = again.events()[1]
    assert (gap.turn, gap.text, gap.body, gap.close_requested) == (0, None, {}, False)   # no payload
    assert events.DIAG_EVENT_LOG_DAMAGED in caplog.text and "not json" not in caplog.text
    nxt = again.stop(stop("four"))                                 # appends go on at the next number
    assert isinstance(nxt, SessionEvent) and (nxt.event_id, nxt.turn) == ("sk-a:g1:e4", 4)
    assert lg.path.read_bytes().count(b"not json") == 1            # never cut


def test_a_missing_number_is_a_damaged_event(tmp_path: Path) -> None:
    lg, lines = three(tmp_path)
    lg.path.write_bytes(lines[0] + lines[2])                       # e2's line is gone
    got = EventLog.read(lg.path, *ARGS)
    assert [(e.seq, e.kind) for e in got.events()] == [(1, "stop"), (2, "damaged"), (3, "stop")]


def test_a_repeated_number_is_damaged_in_its_own_place(tmp_path: Path) -> None:
    lg, lines = three(tmp_path)
    lg.path.write_bytes(lines[0] + lines[0] + lines[2])            # e1 twice, no e2
    got = EventLog.read(lg.path, *ARGS)
    assert [(e.seq, e.kind) for e in got.events()] == [(1, "stop"), (2, "damaged"), (3, "stop")]


def test_the_log_is_private_and_the_reply_is_fixed(tmp_path: Path) -> None:
    lg = log(tmp_path)
    ev, reply = lg.request(close(), OPS)
    assert isinstance(ev, SessionEvent)
    assert lg.path.stat().st_mode & 0o777 == 0o600
    assert reply == {"ok": True, "ref": "sk-a:g1:e1", "message": ACCEPTED["close"]}
    assert json.loads(lg.path.read_text())["rid"] == RID
    assert all("granted" not in text and "approved" not in text for text in ACCEPTED.values())   # D1


def test_a_number_far_past_the_last_is_one_damaged_event(tmp_path: Path) -> None:
    """Codex T3 c1 B1: a gap is filled only up to MAX_GAP; a number past that is damage in its own place,
    so one short line can't make the reader build an unbounded run of events."""
    lg, lines = three(tmp_path)
    far = json.loads(lines[1])
    far["n"] = 20000
    lg.path.write_bytes(lines[0] + json.dumps(far).encode() + b"\n" + lines[2])
    got = EventLog.read(lg.path, *ARGS)
    assert [(e.seq, e.kind) for e in got.events()] == [(1, "stop"), (2, "damaged"), (3, "stop")]


def test_a_gap_up_to_the_bound_is_filled(tmp_path: Path) -> None:
    lg, lines = three(tmp_path)
    edge = json.loads(lines[2])
    edge["n"] = 2 + events.MAX_GAP                 # e2 .. e(1+MAX_GAP) missing: exactly MAX_GAP
    lg.path.write_bytes(lines[0] + json.dumps(edge).encode() + b"\n")
    got = EventLog.read(lg.path, *ARGS).events()
    assert len(got) == 2 + events.MAX_GAP and got[-1].kind == "stop"
    assert all(e.kind == "damaged" for e in got[1:-1])


@pytest.mark.parametrize("field", ["n", "turn"])
def test_a_boolean_number_is_damaged(tmp_path: Path, field: str) -> None:
    """Codex T3 c1 B2: JSON true is not a number here (bool is an int subclass)."""
    lg, lines = three(tmp_path)
    odd = json.loads(lines[0])
    odd[field] = True                              # True == 1: as the first line, it was taken as e1
    lg.path.write_bytes(json.dumps(odd).encode() + b"\n" + lines[1] + lines[2])
    got = EventLog.read(lg.path, *ARGS).events()
    assert [(e.seq, e.kind) for e in got] == [(1, "damaged"), (2, "stop"), (3, "stop")]
    assert [e.event_id for e in got] == ["sk-a:g1:e1", "sk-a:g1:e2", "sk-a:g1:e3"]
