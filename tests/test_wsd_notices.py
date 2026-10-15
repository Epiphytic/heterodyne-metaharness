"""`wsd.notices.Notices`, the notice client (plan 5i part 2, Task 3). Offline: admind is `fake_ask_sock`.

A notice is journaled by its `ref` and posted at most once. The row is marked `sending` in its own committed
transaction before the post, so a crash anywhere inside the post window leaves it `sending`, and `recover`
makes it `uncertain`; it is never posted again. Only an attempt known to have written nothing (no socket, or
a retryable refusal) leaves it `owed` for `retry_owed`.
"""

import contextlib
import socket
import sqlite3
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fake_ask_sock import CHUNK_CHARS, FakeAskSock
from fakes.checkpoints import CrashAt, SimulatedCrash
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sandbox_env import short_dir

from heterodyne.admind import notices as admind_notices
from heterodyne.wsd import notices, relay
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.states import BeadState

WS, BEAD = "ws1", "ws1-1"
T0 = 1_800_000_000
HOUR = 3600


class Clock:
    def __init__(self, at: int = T0) -> None:
        self.at = at

    def __call__(self) -> int:
        return self.at


class Rig:
    """A journal under a short socket directory, the fake admind (started or not), and a clock."""

    def __init__(self, d: Path) -> None:
        self.dir = d
        self.sock = d / "ask.sock"
        self.clock = Clock()
        self.journal = Journal(d / "state" / "wsd.db")
        self.fake = FakeAskSock(self.sock)
        self.up = False

    def start(self) -> FakeAskSock:
        self.fake.start()
        self.up = True
        return self.fake

    def stop(self) -> None:
        if self.up:
            self.fake.stop()
            self.up = False

    def notices(self, cp: Checkpoint = nothing, post: Callable[..., str] = relay.post_notice,
                timeout: float = 2.0) -> notices.Notices:
        return notices.Notices(self.journal, self.sock, clock=self.clock, backoff=60, backoff_max=1800,
                       owed_hours=24, timeout=timeout, cp=cp, post=post)

    def reopen(self) -> None:
        self.journal.close()
        self.journal = Journal(self.dir / "state" / "wsd.db")

    def state(self, ref: str) -> str:
        row = self.journal.notice(ref)
        assert row is not None
        return row.state


@pytest.fixture
def rig() -> Iterator[Rig]:
    with short_dir() as d:
        r = Rig(d)
        try:
            yield r
        finally:
            r.stop()
            r.journal.close()


def send(n: notices.Notices, ref: str = "stuck:ws1-1:1", title: str = "ws1-1 is stuck",
         body: str = "why") -> None:
    n.send(WS, BEAD, "stuck", title, body, ref)


# --- at most once ------------------------------------------------------------------------------
def test_the_same_ref_is_posted_once(rig: Rig) -> None:
    fake = rig.start()
    send(rig.notices())
    send(rig.notices(), title="ws1-1 is stuck again")
    rig.reopen()
    send(rig.notices())
    rig.notices().retry_owed(rig.clock() + 10 * HOUR)
    assert [(n.poster, n.title, n.body) for n in fake.notices] == [("wsd-ws1", "ws1-1 is stuck", "why")]
    assert rig.state("stuck:ws1-1:1") == "posted"
    row = rig.journal.notice("stuck:ws1-1:1")
    assert row is not None and row.notice_id and row.attempts == 1


def column_types(rig: Rig) -> list[tuple[str, str, str, str]]:
    """Each row's state and the SQLite storage class of its three times, read past the Journal."""
    db = sqlite3.connect(f"file:{rig.dir / 'state' / 'wsd.db'}?mode=ro", uri=True)
    try:
        return db.execute("SELECT state, typeof(created_at), typeof(updated_at), typeof(next_at)"
                          " FROM notices ORDER BY rowid").fetchall()
    finally:
        db.close()


def test_every_write_path_stores_integer_times_through_the_real_t2_ddl(rig: Rig) -> None:
    n = rig.notices()
    send(n)                                                         # owed, next_at set
    n.send(WS, BEAD, "stuck", "ws1-1 is stuck", "sk-" + "a" * 40, "stuck:ws1-1:2")  # withheld + its stand-in
    assert column_types(rig) == [("owed", "integer", "integer", "integer"),
                                 ("withheld", "integer", "integer", "null"),
                                 ("owed", "integer", "integer", "integer")]
    rig.start()
    rig.clock.at += HOUR
    n.retry_owed(rig.clock())                                       # owed -> sending -> posted
    assert {t[1:3] for t in column_types(rig)} == {("integer", "integer")}
    assert [t[0] for t in column_types(rig)] == ["posted", "withheld", "posted"]
    with pytest.raises(sqlite3.IntegrityError):                     # the CHECKs are live, not decoration
        rig.journal.notice_add("stuck:ws1-1:3", WS, BEAD, "stuck", "t", "b", "owed", None,
                               "2026-10-15T00:00:00Z")  # pyright: ignore[reportArgumentType]


def test_admind_down_leaves_it_owed_and_a_later_pickup_posts_it(rig: Rig) -> None:
    n = rig.notices()
    send(n)                                                 # no socket: nothing was written
    assert rig.state("stuck:ws1-1:1") == "owed"
    fake = rig.start()
    n.retry_owed(rig.clock() + 59)                          # not due before the backoff
    assert fake.frames == []
    n.retry_owed(rig.clock() + 60)
    assert rig.state("stuck:ws1-1:1") == "posted"
    assert len(fake.frames) == 1


def test_the_backoff_doubles_up_to_its_cap(rig: Rig) -> None:
    n = rig.notices()
    send(n)
    due: list[int] = []
    for _ in range(8):
        row = rig.journal.notice("stuck:ws1-1:1")
        assert row is not None and row.state == "owed" and row.next_at is not None
        due.append(row.next_at - rig.clock())
        rig.clock.at = row.next_at
        n.retry_owed(rig.clock())
    assert due == [60, 120, 240, 480, 960, 1800, 1800, 1800]


@pytest.mark.parametrize("step", ["drop", "hang", "garbage", "failed"])
def test_a_lost_reply_is_uncertain_and_never_retried(rig: Rig, step: str) -> None:
    fake = rig.start()
    fake.script.append(step)
    n = rig.notices(timeout=0.3)
    send(n)
    assert rig.state("stuck:ws1-1:1") == "uncertain"
    for later in (HOUR, 10 * HOUR, 100 * HOUR):
        n.retry_owed(rig.clock() + later)
    send(n)
    assert len(fake.frames) == 1 and fake.notices == []


class RawPeer:
    """A bare listener that accepts and then closes, at once or after reading one byte: the client's
    write or its read of the reply fails after the connect."""

    def __init__(self, path: Path, read: int) -> None:
        self.read, self.accepted = read, 0
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(path))
        self.server.listen()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        with contextlib.suppress(OSError):
            while True:
                conn, _ = self.server.accept()
                self.accepted += 1
                with conn:
                    if self.read:
                        conn.recv(self.read)

    def close(self) -> None:
        self.server.close()


@pytest.mark.parametrize("read", [0, 1])
def test_a_failed_write_after_connect_is_uncertain(rig: Rig, read: int) -> None:
    peer = RawPeer(rig.sock, read)
    try:
        n = rig.notices()
        send(n, body="x" * 3000)
        assert rig.state("stuck:ws1-1:1") == "uncertain"    # NoticeUncertain, never NoticeUnavailable
        n.retry_owed(rig.clock() + 10 * HOUR)
        assert peer.accepted == 1
    finally:
        peer.close()


def test_any_other_transport_exception_is_uncertain(rig: Rig) -> None:
    def broken(_sock: Path, **_kw: object) -> str:
        raise OSError("boom")

    n = rig.notices(post=broken)
    send(n)                                                 # never raises on transport
    assert rig.state("stuck:ws1-1:1") == "uncertain"


# --- crashes ------------------------------------------------------------------------------------
def crash_after_the_write(sock: Path, **kw: object) -> str:
    """The frame reaches admind, then wsd dies before it reads the reply ("drop" makes the fake read the
    frame and close)."""
    try:
        relay.post_notice(sock, **kw)  # type: ignore[arg-type]
    except relay.NoticeUncertain:
        pass
    raise SimulatedCrash("inside the post")


@pytest.mark.parametrize("point", ["before-reply", "outcome"])
def test_a_crash_inside_the_post_window_is_never_posted_again(rig: Rig, point: str) -> None:
    fake = rig.start()
    if point == "before-reply":
        fake.script.append("drop")
        n = rig.notices(post=crash_after_the_write)
    else:
        n = rig.notices(cp=CrashAt("notice.outcome"))      # inside the outcome transaction: rolled back
    with pytest.raises(SimulatedCrash):
        send(n)
    rig.reopen()
    fresh = rig.notices()
    fresh.recover()
    for later in (HOUR, 10 * HOUR, 100 * HOUR):
        fresh.retry_owed(rig.clock() + later)
    send(fresh)
    assert len(fake.frames) == 1
    assert rig.state("stuck:ws1-1:1") == "uncertain"
    diags = [e for e in rig.journal.events_since(0) if e.kind == notices.IN_FLIGHT_EVENT]
    assert [e.detail for e in diags] == [notices.DIAG_NOTICE_IN_FLIGHT.format(ref="stuck:ws1-1:1")]
    assert all("ws1-1 is stuck" not in e.detail and "why" not in e.detail for e in diags)


def test_recover_is_idempotent(rig: Rig) -> None:
    rig.start().script.append("drop")
    with pytest.raises(SimulatedCrash):
        send(rig.notices(post=crash_after_the_write))
    rig.notices().recover()
    rig.notices().recover()
    assert sum(e.kind == notices.IN_FLIGHT_EVENT for e in rig.journal.events_since(0)) == 1


def test_a_crash_before_the_sending_commit_is_retried(rig: Rig) -> None:
    fake = rig.start()
    with pytest.raises(SimulatedCrash):
        send(rig.notices(cp=CrashAt("notice.owed")))       # after the insert, before `sending`
    assert fake.frames == []
    rig.reopen()
    fresh = rig.notices()
    fresh.recover()
    assert rig.state("stuck:ws1-1:1") == "owed"
    fresh.retry_owed(rig.clock())
    fresh.retry_owed(rig.clock() + HOUR)
    assert rig.state("stuck:ws1-1:1") == "posted" and len(fake.frames) == 1


# --- refusals -----------------------------------------------------------------------------------
PERMANENT = sorted(set(admind_notices.MESSAGES) - relay.NOTICE_RETRYABLE)


@pytest.mark.parametrize("code", PERMANENT)
def test_a_permanent_refusal_is_recorded_and_never_retried(rig: Rig, code: str) -> None:
    fake = rig.start()
    fake.script.append(admind_notices.MESSAGES[code])
    n = rig.notices()
    send(n)
    row = rig.journal.notice("stuck:ws1-1:1")
    assert row is not None and (row.state, row.why) == ("refused", code)
    n.retry_owed(rig.clock() + 100 * HOUR)
    assert len(fake.frames) == 1


@pytest.mark.parametrize("code", sorted(relay.NOTICE_RETRYABLE))
def test_rate_and_queue_refusals_are_retried_with_backoff(rig: Rig, code: str) -> None:
    fake = rig.start()
    fake.script.append(admind_notices.MESSAGES[code])
    n = rig.notices()
    send(n)
    assert rig.state("stuck:ws1-1:1") == "owed"
    n.retry_owed(rig.clock() + 59)
    assert len(fake.frames) == 1
    n.retry_owed(rig.clock() + 60)
    assert rig.state("stuck:ws1-1:1") == "posted" and len(fake.frames) == 2


# --- the text -----------------------------------------------------------------------------------
SECRETS: dict[str, Callable[[], str]] = {
    "sk- API key": lambda: "sk-" + "Zy9x" * 6,
    "npub": lambda: "npub1" + "q" * 58,
    "hex key": lambda: "ab" * 32,
}


@pytest.mark.parametrize("kind", sorted(SECRETS))
@pytest.mark.parametrize("where", ["title", "body"])
def test_a_secret_in_the_text_is_withheld_and_never_stored(rig: Rig, kind: str, where: str) -> None:
    fake = rig.start()
    secret = SECRETS[kind]()
    title, body = ("ws1-1 is stuck " + secret, "why") if where == "title" else ("ws1-1 is stuck",
                                                                                  "why " + secret)
    send(rig.notices(), title=title, body=body)
    row = rig.journal.notice("stuck:ws1-1:1")
    assert row is not None and (row.state, row.title, row.body) == ("withheld", "", "")
    assert [n.title for n in fake.notices] == [
        f"wsd notice for {BEAD} withheld: it contained {kind}."]
    replacement = rig.journal.notice("withheld:stuck:ws1-1:1")
    assert replacement is not None and replacement.state == "posted"
    assert secret not in repr(replacement) and secret not in repr(fake.frames)
    assert all(secret[i:i + 8] not in repr(replacement) for i in range(len(secret) - 7))
    stored = rig.journal.db.execute("SELECT * FROM notices").fetchall()
    events = rig.journal.events_since(0)
    assert secret not in repr(stored) and secret not in repr(events)
    send(rig.notices(), title=title, body=body)            # the ref is still posted at most once
    assert len(fake.notices) == 1


@pytest.mark.parametrize("title", ["", " ", "a\nb", "x" * 201, "tab\there", "line sep", "para sep",
                                   "del\x7f", "c1\x85"])
def test_a_bad_title_is_a_value_error_and_nothing_is_journaled(rig: Rig, title: str) -> None:
    with pytest.raises(ValueError, match="title"):
        send(rig.notices(), title=title)
    assert rig.journal.notice("stuck:ws1-1:1") is None


def test_a_ref_over_128_characters_is_a_value_error(rig: Rig) -> None:
    with pytest.raises(ValueError, match="ref"):
        send(rig.notices(), ref="r" * 129)
    send(rig.notices(), ref="r" * 128)


def test_a_long_body_is_cut_with_the_marker(rig: Rig) -> None:
    fake = rig.start()
    send(rig.notices(), body="\n".join(f"line {i}" for i in range(100)))
    (posted,) = fake.notices
    assert posted.body.endswith(notices.TRUNCATED)
    assert posted.body.count("\n") + 1 <= admind_notices.MAX_BODY_LINES


_seq = iter(range(1_000_000))


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(body=st.text(max_size=8000) | st.lists(st.text(max_size=120), max_size=120).map("\n".join))
def test_the_body_is_fitted_to_admind_bounds(rig: Rig, body: str) -> None:
    if not rig.up:
        rig.start()
    rig.fake.notices_this_hour = 0                          # admind's hourly cap is not under test here
    ref = f"fit:{next(_seq)}"
    send(rig.notices(), ref=ref, body=body)
    posted = rig.fake.notices[-1]
    assert rig.state(ref) in ("posted", "withheld")
    assert not isinstance(admind_notices.admit(posted, 4000), str)
    assert CHUNK_CHARS <= 4000


# --- retry_owed ---------------------------------------------------------------------------------
def test_retry_owed_posts_at_most_three_per_call(rig: Rig) -> None:
    n = rig.notices()
    for i in range(5):
        rig.clock.at = T0 + i
        send(n, ref=f"stuck:ws1-1:{i}")
    fake = rig.start()
    n.retry_owed(T0 + HOUR)
    assert [p.title for p in fake.notices] == ["ws1-1 is stuck"] * 3
    assert [rig.state(f"stuck:ws1-1:{i}") for i in range(5)] == ["posted"] * 3 + ["owed"] * 2
    n.retry_owed(T0 + HOUR)
    assert len(fake.notices) == 5


def test_an_owed_notice_of_a_closed_bead_expires(rig: Rig) -> None:
    n = rig.notices()
    send(n)
    rig.journal.adopt(WS, BEAD, BeadState.CLOSED)
    fake = rig.start()
    fake.latched = True                                     # still refused: it stays owed
    n.retry_owed(T0 + 23 * HOUR)
    assert rig.state("stuck:ws1-1:1") == "owed"
    n.retry_owed(T0 + 24 * HOUR + 1)
    row = rig.journal.notice("stuck:ws1-1:1")
    assert row is not None and (row.state, row.why) == ("refused", "expired")


def test_an_owed_notice_of_an_open_bead_never_expires(rig: Rig) -> None:
    n = rig.notices()
    send(n)
    fake = rig.start()
    n.retry_owed(T0 + 100 * HOUR)
    assert rig.state("stuck:ws1-1:1") == "posted" and len(fake.notices) == 1


def test_a_withheld_notice_without_a_bead_names_the_workstream(rig: Rig) -> None:
    fake = rig.start()
    rig.notices().send(WS, None, "stuck", "ws1 is stuck", "why " + SECRETS["npub"](), "stuck:ws1")
    assert [n.title for n in fake.notices] == [f"wsd notice for {WS} withheld: it contained npub."]
