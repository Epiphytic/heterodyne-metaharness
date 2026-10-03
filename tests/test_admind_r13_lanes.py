import asyncio
import sqlite3
from pathlib import Path

from admind_waits import wait_until
from test_admind_daemon import Harness, needs_tmux, run_with

from heterodyne.admind.store import Store

TOKEN = "ghp_" + "A" * 30
HEX = "ab" * 32

OLD_OUTBOX = ("CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, "
              "reply_to TEXT, text TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('pending', 'sent', "
              "'failed')), attempts INTEGER NOT NULL DEFAULT 0, message_id TEXT);")


def old_database(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """A plan-2 database: no lane column, rows queued before redaction existed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(OLD_OUTBOX)
    db.executemany("INSERT INTO outbox(key, text, status) VALUES (?, ?, ?)", rows)
    db.commit()
    db.close()


def pending(s: Store) -> list[tuple[str, str]]:
    return [(r.key, r.text) for r in s.pending()]


def test_lane_one_first_then_lane_two(tmp_path: Path) -> None:
    s = Store(tmp_path / "a.db")
    s.enqueue("d1", "details 1", None, lane=2)
    s.enqueue("d2", "details 2", None, lane=2)
    s.enqueue("c1", "command reply", None)
    row = s.next_pending()
    assert row is not None and row.key == "c1" and row.lane == 1
    s.mark_sent(row.seq, None)
    row = s.next_pending()
    assert row is not None and row.key == "d1"
    s.mark_sent(row.seq, None)
    s.enqueue("alert", "urgent", None)          # arrives while lane 2 is being sent
    row = s.next_pending()
    assert row is not None and row.key == "alert"


def test_old_database_gains_the_lane_column(tmp_path: Path) -> None:
    old_database(tmp_path / "old.db", [("k", "t", "pending")])
    s = Store(tmp_path / "old.db")
    row = s.next_pending()
    assert row is not None and row.lane == 1
    assert s.get("outbox_needs_redaction") == "1"


def test_upgrade_redacts_a_secret_split_across_pending_chunks(tmp_path: Path) -> None:
    old_database(tmp_path / "old.db", [("reply:s:1:0", "token " + TOKEN[:12], "pending"),
                                       ("reply:s:1:1", TOKEN[12:] + f" and {HEX}", "pending"),
                                       ("ready", f"hello {HEX}", "pending")])
    s = Store(tmp_path / "old.db")
    assert s.redact_pending_outbox(4000) == 3
    assert sorted(pending(s)) == [("ready", "hello <redacted hex key>"),
                                  ("reply:s:1:r0", "token <redacted GitHub token> and <redacted hex key>")]
    assert s.get("outbox_needs_redaction") is None
    assert s.redact_pending_outbox(4000) == 0          # once only


def test_upgrade_hides_the_rest_of_a_value_begun_in_a_sent_chunk(tmp_path: Path) -> None:
    old_database(tmp_path / "old.db", [("reply:s:2:0", "token " + TOKEN[:12], "sent"),
                                       ("reply:s:2:1", TOKEN[12:] + " end", "pending")])
    s = Store(tmp_path / "old.db")
    s.redact_pending_outbox(4000)
    assert pending(s) == [("reply:s:2:r0", "<redacted fragment> end")]


def test_upgrade_hides_a_value_that_spans_several_sent_chunks(tmp_path: Path) -> None:
    # Codex r2 finding 2: the chunk just before the pending one holds no token prefix.
    long_token = "ghp_" + "Z" * 600
    old_database(tmp_path / "old.db", [("reply:s:3:0", long_token[:250], "sent"),
                                       ("reply:s:3:1", long_token[250:500], "sent"),
                                       ("reply:s:3:2", long_token[500:] + " end", "pending")])
    s = Store(tmp_path / "old.db")
    s.redact_pending_outbox(4000)
    assert pending(s) == [("reply:s:3:r0", "<redacted fragment> end")]


def test_upgrade_hides_a_continuation_whose_start_is_gone(tmp_path: Path) -> None:
    old_database(tmp_path / "old.db", [("reply:s:4:0", "start", "sent"),
                                       ("reply:s:4:2", "Z" * 40 + " end", "pending")])   # :1 is missing
    s = Store(tmp_path / "old.db")
    s.redact_pending_outbox(4000)
    assert pending(s) == [("reply:s:4:r0", "<redacted continuation of a partly sent reply>")]


def test_upgrade_rechunks(tmp_path: Path) -> None:
    # Not hex: a 600-character hex run would be one marker.
    old_database(tmp_path / "old.db", [("cmd:x:0", "x" * 300, "pending"), ("cmd:x:1", "y" * 300, "pending")])
    s = Store(tmp_path / "old.db")
    s.redact_pending_outbox(250)
    rows = pending(s)
    assert [k for k, _ in rows] == ["cmd:x:r0", "cmd:x:r1", "cmd:x:r2"]
    assert "".join(t for _, t in rows) == "x" * 300 + "y" * 300


def test_a_new_database_needs_no_upgrade(tmp_path: Path) -> None:
    s = Store(tmp_path / "new.db")
    assert s.get("outbox_needs_redaction") is None
    assert s.redact_pending_outbox(4000) == 0


async def join(h: Harness) -> None:
    """The operator's first message opens the outbound gate; wait for its echo and an empty outbox."""
    await h.say("hello")
    await wait_until(lambda: "echo: hello" in h.texts() and h.store.next_pending() is None)


def records(path: Path) -> list[dict[str, object]]:
    import json
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@needs_tmux
def test_lane_one_is_sent_before_the_rest_of_lane_two(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.fake.on_send = lambda req: (h.daemon.post("c", "command reply", None)
                                      if req["text"] == "d1" else None)
        for i in (1, 2, 3):
            h.daemon.post(f"d{i}", f"d{i}", None, lane=2)
        await wait_until(lambda: "d3" in h.texts())
        sent = [t for t in h.texts() if t in ("d1", "d2", "d3", "command reply")]
        assert sent == ["d1", "command reply", "d2", "d3"]
    run_with(tmp_path, scenario)


@needs_tmux
def test_redaction_at_delivery(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.store.enqueue("raw", f"see {HEX}", None)         # bypasses Admind.post

    async def scenario(h: Harness) -> None:
        await h.say("hello")                                # the join signal opens the outbound gate
        await wait_until(lambda: "see <redacted hex key>" in h.texts())
        assert all(HEX not in t for t in h.texts())
    run_with(tmp_path, scenario, before)


@needs_tmux
def test_upgrade_at_startup(tmp_path: Path) -> None:
    def before_store(h: Harness) -> None:
        old_database(h.settings.state_dir / "admind.db",
                     [("reply:s:1:0", "token " + TOKEN[:12], "pending"),
                      ("reply:s:1:1", TOKEN[12:] + " end", "pending")])

    async def scenario(h: Harness) -> None:
        await h.say("hello")
        await wait_until(lambda: "token <redacted GitHub token> end" in h.texts())
        assert all(TOKEN[12:] not in t for t in h.texts())

    h = run_with(tmp_path, scenario, before_store=before_store)
    audit = records(h.settings.state_dir / "audit.jsonl")
    assert {"kind": "outbox", "action": "redacted-after-upgrade", "rows": 2}.items() <= next(
        r for r in audit if r.get("action") == "redacted-after-upgrade").items()


async def backoff_gates(h: Harness) -> list[asyncio.Event]:
    """Make every backoff (a sleep of 2 s or more) wait on its own event; shorter sleeps return at once."""
    gates: list[asyncio.Event] = []

    async def sleep(delay: float) -> None:
        if delay >= 2:
            gate = asyncio.Event()
            gates.append(gate)
            await gate.wait()
        else:
            await asyncio.sleep(0)
    h.daemon._sleep = sleep         # noqa: SLF001 - the test seam for time
    await join(h)
    return gates


def attempts(h: Harness, text: str) -> int:
    return sum(1 for r in h.fake.requests if r.get("text") == text)


@needs_tmux
def test_backoff_is_per_row(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        gates = await backoff_gates(h)
        h.fake.fail_sends = 2
        h.daemon.post("d", "details", None, lane=2)
        await wait_until(lambda: len(gates) == 1)           # details failed: gates[0] is its backoff
        h.daemon.post("c", "urgent", None)
        await wait_until(lambda: len(gates) == 2)           # urgent was tried at once, failed, backs off
        gates[1].set()
        await wait_until(lambda: "urgent" in h.texts())
        assert "details" not in h.texts() and attempts(h, "details") == 1
        gates[0].set()
        await wait_until(lambda: "details" in h.texts())
    run_with(tmp_path, scenario)


@needs_tmux
def test_lane_one_does_not_wait_for_a_lane_two_backoff(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        gates = await backoff_gates(h)
        h.fake.fail_sends = 1
        h.daemon.post("d", "details", None, lane=2)
        await wait_until(lambda: len(gates) == 1)
        h.daemon.post("c", "urgent", None)
        await wait_until(lambda: "urgent" in h.texts())
        assert not gates[0].is_set() and "details" not in h.texts()
        gates[0].set()
        await wait_until(lambda: "details" in h.texts())
    run_with(tmp_path, scenario)
