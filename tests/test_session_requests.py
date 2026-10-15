"""Plan 5i part 1 T2: the shim's ws-request ops and the server's request callback."""

import json
from pathlib import Path
from typing import Any

import pytest

from heterodyne.session import shim
from heterodyne.session.server import UNAVAILABLE, UNSUPPORTED, SessionServer

TOKEN = "tok-" + "z" * 20  # noqa: S105 (test value)


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


@pytest.fixture
def run(tmp_path: Path) -> Path:
    d = tmp_path / "r1"
    d.mkdir()
    (d / shim.TOKEN_FILE).write_text(TOKEN)
    (d / shim.CONFIG_FILE).write_text(json.dumps({"wait_seconds": 2}))
    return d


def env_for(run: Path) -> dict[str, str]:
    return {"HZ_SESSION_SOCKET": str(run / "s.sock")}


def serve(run: Path, on_request: Any = None) -> tuple[SessionServer, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    def answer(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return on_request(payload) if on_request else {"ok": True, "ref": "sk-a:g1:e1", "message": "accepted"}

    s = SessionServer(run / "s.sock", TOKEN, run.parent / "events.jsonl", on_request=answer)
    s.start()
    return s, seen


def request(run: Path, *words: str) -> tuple[int, Any]:
    t = FakeTime()
    rc, out = shim.run_request(list(words), env_for(run), clock=t.clock, sleep=t.sleep)
    return rc, (json.loads(out) if out.startswith("{") else out)


def test_close_sends_the_evidence_file(run: Path, tmp_path: Path) -> None:
    s, seen = serve(run)
    try:
        (tmp_path / "ev.txt").write_text("tests pass: 12 passed\n")
        assert request(run, "close", "--evidence-file", str(tmp_path / "ev.txt")) == (
            0, {"ok": True, "ref": "sk-a:g1:e1", "message": "accepted"})
    finally:
        s.close()
    [p] = seen
    assert (p["op"], p["evidence"]) == ("close", "tests pass: 12 passed\n")
    assert shim.RID.fullmatch(p["rid"])


@pytest.mark.parametrize("op", ["ask", "question"])
def test_ask_and_question_send_one_json_object_inline_or_from_a_file(run: Path, tmp_path: Path,
                                                                      op: str) -> None:
    s, seen = serve(run)
    try:
        (tmp_path / "q.json").write_text('{"text": "which one?"}')
        assert request(run, op, '{"text":', '"which one?"}')[0] == 0      # words are joined, as today
        assert request(run, op, "--file", str(tmp_path / "q.json"))[0] == 0
    finally:
        s.close()
    assert [(p["op"], p["body"]) for p in seen] == [(op, {"text": "which one?"})] * 2


@pytest.mark.parametrize(("words", "code"), [
    (["close"], "usage"),
    (["close", "--evidence-file"], "usage"),
    (["close", "--evidence-file", "/nonexistent/ev"], "unreadable"),
    (["question", "[1, 2]"], "bad_json"),
    (["question", "not json"], "bad_json"),
    (["ask"], "usage"),
])
def test_bad_arguments_are_refused_before_sending(run: Path, words: list[str], code: str) -> None:
    s, seen = serve(run)
    try:
        assert request(run, *words) == (2, {"ok": False, "error": code})
    finally:
        s.close()
    assert seen == []


def test_evidence_over_the_limit_or_not_utf8_is_refused(run: Path, tmp_path: Path) -> None:
    s, seen = serve(run)
    try:
        big = tmp_path / "big"
        big.write_bytes(b"x" * (shim.CLOSE_EVIDENCE_MAX + 1))
        bad = tmp_path / "bad"
        bad.write_bytes(b"\xff\xfe")
        empty = tmp_path / "empty"
        empty.write_bytes(b"")
        assert request(run, "close", "--evidence-file", str(big)) == (2, {"ok": False, "error": "too_large"})
        assert request(run, "close", "--evidence-file", str(bad)) == (2, {"ok": False, "error": "bad_utf8"})
        assert request(run, "close", "--evidence-file", str(empty)) == (2, {"ok": False, "error": "usage"})
    finally:
        s.close()
    assert seen == []


def test_evidence_at_the_limit_fits_one_line_even_when_escaped(run: Path, tmp_path: Path) -> None:
    s, seen = serve(run)
    try:
        worst = tmp_path / "worst"
        worst.write_bytes(b"\x01" * shim.CLOSE_EVIDENCE_MAX)        # each byte escapes to 6
        assert request(run, "close", "--evidence-file", str(worst)) == (2, {"ok": False,
                                                                             "error": "too_large"})
        fits = tmp_path / "fits"
        fits.write_bytes(b"y" * shim.CLOSE_EVIDENCE_MAX)
        assert request(run, "close", "--evidence-file", str(fits))[0] == 0
    finally:
        s.close()
    assert len(seen) == 1


def test_a_body_over_the_limit_is_refused(run: Path) -> None:
    s, seen = serve(run)
    try:
        body = json.dumps({"text": "q" * shim.REQUEST_BODY_MAX})
        assert request(run, "question", body) == (2, {"ok": False, "error": "too_large"})
    finally:
        s.close()
    assert seen == []


def test_a_retry_after_a_lost_reply_carries_the_same_rid(run: Path, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The shim resends one line until it gets a reply; the rid lets the runtime answer the resend with the
    first event instead of logging a second. The first connection is dropped after its line is read, with
    no reply, as a reply lost on the way back would be."""
    s, seen = serve(run)
    read = s._read  # pyright: ignore[reportPrivateUsage]
    rids: list[str] = []

    def lossy(conn: Any) -> bytes | None:
        line = read(conn)
        if line is not None:
            rids.append(json.loads(line)["payload"]["rid"])
            if len(rids) == 1:
                return None                     # dropped: the server closes it unanswered
        return line

    monkeypatch.setattr(s, "_read", lossy)
    try:
        (tmp_path / "ev").write_text("done")
        assert request(run, "close", "--evidence-file", str(tmp_path / "ev"))[0] == 0
    finally:
        s.close()
    assert len(rids) == 2 and len(set(rids)) == 1 and len(seen) == 1


def test_unknown_words_and_a_server_without_a_callback_stay_unsupported(run: Path, tmp_path: Path) -> None:
    s, seen = serve(run)
    try:
        assert request(run, "please", "push") == (1, UNSUPPORTED)
    finally:
        s.close()
    assert seen == []
    bare = SessionServer(run / "s.sock", TOKEN, tmp_path / "e2.jsonl")
    bare.start()
    try:
        (tmp_path / "ev").write_text("done")
        assert request(run, "close", "--evidence-file", str(tmp_path / "ev")) == (1, UNSUPPORTED)
    finally:
        bare.close()


def test_a_callback_that_raises_is_unavailable(run: Path, tmp_path: Path) -> None:
    def boom(_p: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("x")

    s, _ = serve(run, boom)
    try:
        (tmp_path / "ev").write_text("done")
        assert request(run, "close", "--evidence-file", str(tmp_path / "ev")) == (1, UNAVAILABLE)
    finally:
        s.close()


def test_no_request_is_answered_once_the_server_has_closed(run: Path, tmp_path: Path) -> None:
    s, seen = serve(run)
    s.close()
    (tmp_path / "ev").write_text("done")
    assert request(run, "close", "--evidence-file", str(tmp_path / "ev"))[0] == shim.EX_TEMPFAIL
    assert seen == []
