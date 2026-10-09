"""AU-5: usage ingestion, the journal's usage cache and the deadline clamp (design §2.3-§2.5, §3.1, §3.5,
§3.7; §5 usage). Offline, with an injected clock; every key is an obvious fake."""

import math
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, SimulatedCrash
from wsd_env import WS, make_rig

from heterodyne.wsd.accounts import Chosen, ConfiguredAccounts, ProfileAccounts
from heterodyne.wsd.headroom import Deadline, Mark, UsageSettings, Window, clamp_bound
from heterodyne.wsd.journal import CLAMP_DEFERRAL, CLAMP_EXHAUSTED, Journal
from heterodyne.wsd.launches import LaunchEntry
from heterodyne.wsd.usage import (
    Dropped,
    Observation,
    Stored,
    decide,
    ingest_trusted,
    ingest_untrusted,
    mark_exhausted,
    untrusted_defer_until,
)

S = UsageSettings()
NOW = 1_800_000_000
KEY_A = "ck1-" + "a" * 32
KEY_B = "ck1-" + "b" * 32
HOUR = 3600


@pytest.fixture
def j(tmp_path: Path) -> Iterator[Journal]:
    journal = Journal(tmp_path / "wsd.db")
    yield journal
    journal.close()


def obs(pct: object = 50.0, resets: int | None = None, wid: str = "w5h",
        claimed: str | None = None) -> Observation:
    return Observation(wid, "5h", pct, resets, "codex.app-server", claimed)


def launch(j: Journal, session: str = "sk-p", generation: int = 1, key: str = KEY_A,
           dispatched: bool = True, outcome: str | None = None) -> tuple[str, int]:
    j.launch_insert(LaunchEntry(session, generation, WS, "btq-1", "reviewer", "p-one", "default", key, "",
                                False, "2026-10-08T00:00:00+00:00"))
    if dispatched:
        j.launch_set(session, generation, "dispatched_at", "2026-10-08T00:00:01+00:00")
    if outcome is not None:
        j.launch_set(session, generation, "outcome", outcome)
    return session, generation


def trusted(j: Journal, key: str = KEY_A) -> tuple[Window, ...]:
    return tuple(j.usage_cache([key]).windows.get(key, ()))


# --- ingestion: validation ---

@pytest.mark.parametrize("pct", [math.nan, math.inf, -math.inf, True, False, "50", -1, 100.5, None,
                                 10**400, -(10**400), 101, 1j])
def test_bad_percentages_are_dropped_and_nothing_is_written(j: Journal, pct: object) -> None:
    assert ingest_trusted(j, KEY_A, obs(pct), NOW, S) == Dropped("bad_percent")
    assert trusted(j) == () and j.usage_seq_next() == 1         # not even the counter moved


@pytest.mark.parametrize("pct", [0, 100, 0.0, 100.0, 42.5])
def test_the_bounds_are_kept(j: Journal, pct: float) -> None:
    assert isinstance(ingest_trusted(j, KEY_A, obs(pct), NOW, S), Stored)
    [w] = trusted(j)
    assert w.used_percent == pct and w.observed_at == NOW


@pytest.mark.parametrize("hint", [NOW - 1, NOW, NOW + S.max_window_hours * HOUR + 1])
def test_an_unusable_reset_is_stored_unknown(j: Journal, hint: int) -> None:
    ingest_trusted(j, KEY_A, obs(100.0, hint), NOW, S)
    [w] = trusted(j)
    assert w.resets_at is None


def test_a_usable_reset_is_kept_and_observed_at_is_the_receipt_time(j: Journal) -> None:
    ingest_trusted(j, KEY_A, obs(100.0, NOW + HOUR), NOW, S)
    [w] = trusted(j)
    assert (w.resets_at, w.observed_at, w.receipt_seq) == (NOW + HOUR, NOW, 1)


@pytest.mark.parametrize("field", ["window_id", "kind", "source"])
def test_bad_labels_are_dropped(j: Journal, field: str) -> None:
    fields = {"window_id": "w5h", "kind": "5h", "source": "codex.host-read", field: "Bad Label!"}
    o = Observation(fields["window_id"], fields["kind"], 10.0, None, fields["source"])
    assert ingest_trusted(j, KEY_A, o, NOW, S) == Dropped("bad_label")


def test_a_claimed_key_for_another_account_is_dropped(j: Journal) -> None:
    assert ingest_trusted(j, KEY_A, obs(claimed=KEY_B), NOW, S) == Dropped("other_account")
    assert isinstance(ingest_trusted(j, KEY_A, obs(claimed=KEY_A), NOW, S), Stored)
    session = launch(j)
    assert ingest_untrusted(j, session, obs(claimed=KEY_B), NOW, S) == Dropped("other_account")


def test_untrusted_reports_need_a_live_dispatched_launch(j: Journal) -> None:
    assert ingest_untrusted(j, ("sk-x", 1), obs(), NOW, S) == Dropped("unknown_launch")
    undispatched = launch(j, "sk-u", dispatched=False)
    abandoned = launch(j, "sk-a", outcome="abandoned")
    for session in (undispatched, abandoned):
        assert ingest_untrusted(j, session, obs(), NOW, S) == Dropped("unknown_launch")
    ok = launch(j, "sk-ok")
    assert isinstance(ingest_untrusted(j, ok, obs(), NOW, S), Stored)
    assert [w.window_id for w in j.usage_untrusted(*ok)] == ["w5h"]
    assert j.usage_untrusted(*undispatched) == () and trusted(j) == ()      # never in the trusted cache


def test_a_reviewer_moving_between_profiles_reads_only_its_own_launch(j: Journal) -> None:
    """P -> Q -> P: three launches of one reviewer, each reporting on its own channel."""
    first, middle, last = launch(j, "sk-p", 1), launch(j, "sk-q", 1, KEY_B), launch(j, "sk-p", 2)
    for n, session in enumerate((first, middle, last)):
        ingest_untrusted(j, session, obs(10.0 * (n + 1)), NOW + n, S)
    assert [w.used_percent for w in j.usage_untrusted(*first)] == [10.0]
    assert [w.used_percent for w in j.usage_untrusted(*middle)] == [20.0]
    assert [w.used_percent for w in j.usage_untrusted(*last)] == [30.0]


def test_untrusted_defer_until_clamps_both_ways() -> None:
    assert untrusted_defer_until(None, NOW, S) == NOW + S.unknown_backoff_minutes * 60
    assert untrusted_defer_until(NOW + 1, NOW, S) == NOW + S.min_recheck_seconds
    assert untrusted_defer_until(NOW + 10 * HOUR, NOW, S) == NOW + S.untrusted_max_defer_minutes * 60
    assert untrusted_defer_until(NOW + 600, NOW, S) == NOW + 600


# --- replacement by receipt sequence ---

def test_a_newer_low_read_after_a_jump_back_replaces_a_blocking_row(tmp_path: Path, j: Journal) -> None:
    accounts = codex_accounts(tmp_path)
    key = accounts.current_key("codex", "default")
    ingest_trusted(j, key, obs(100.0, NOW + HOUR), NOW, S)
    assert isinstance(decide(accounts, j, "p-two", None, NOW, S), Deadline)
    back = NOW - 10 * 60                                       # the clock jumps back ten minutes
    ingest_trusted(j, key, obs(3.0), back, S)
    [w] = trusted(j, key)
    assert (w.used_percent, w.observed_at, w.receipt_seq) == (3.0, back, 2)
    assert decide(accounts, j, "p-two", None, back, S) == Chosen("default", key)


def test_an_older_sequence_never_replaces_a_newer_one(j: Journal) -> None:
    j.usage_put_trusted(KEY_A, Window("w5h", "5h", 3.0, None, NOW, 0, "codex.host-read"), 5)
    j.usage_put_trusted(KEY_A, Window("w5h", "5h", 100.0, None, NOW, 0, "codex.host-read"), 4)
    [w] = trusted(j)
    assert (w.used_percent, w.receipt_seq) == (3.0, 5)


def test_the_trusted_and_untrusted_tables_never_replace_each_other(j: Journal) -> None:
    session = launch(j, key=KEY_A)
    ingest_trusted(j, KEY_A, obs(100.0), NOW, S)
    ingest_untrusted(j, session, obs(1.0), NOW, S)
    assert [w.used_percent for w in trusted(j)] == [100.0]
    ingest_trusted(j, KEY_A, obs(2.0), NOW, S)
    assert [w.used_percent for w in j.usage_untrusted(*session)] == [1.0]


def test_a_mark_blocks_until_its_reset_or_the_backoff(j: Journal) -> None:
    mark_exhausted(j, KEY_A, NOW + HOUR, NOW, S)
    assert j.usage_cache([KEY_A]).marks[KEY_A] == Mark(NOW + HOUR, NOW, 1)
    mark_exhausted(j, KEY_B, None, NOW, S)
    assert j.usage_cache([KEY_B]).marks[KEY_B].until == NOW + S.unknown_backoff_minutes * 60


def test_the_cache_reads_only_the_keys_asked_for(j: Journal) -> None:
    ingest_trusted(j, KEY_A, obs(100.0), NOW, S)
    mark_exhausted(j, KEY_A, None, NOW, S)
    assert j.usage_cache([KEY_B]).windows == {} and j.usage_cache([KEY_B]).marks == {}


# --- the clamp (§3.5) ---

def deferral(j: Journal, number: int, until: int | None, session: str = "sk-c", bead: str = "btq-1",
             reason: str = "quota") -> None:
    j.db.execute("INSERT INTO deferrals VALUES (?, ?, ?, 'coder', 'p-one', ?, ?, 'trusted')",
                 (session, number, bead, reason, None if until is None else str(until)))


def stored(j: Journal) -> tuple[str | None, str | None]:
    mark = j.db.execute("SELECT until FROM account_exhausted WHERE credential_key = ?", (KEY_A,)).fetchone()
    defer = j.db.execute("SELECT defer_until FROM deferrals WHERE session_key = 'sk-c' "
                         "ORDER BY number DESC LIMIT 1").fetchone()
    return (None if mark is None else mark[0]), (None if defer is None else defer[0])


def test_a_rollback_clamps_once_and_never_again(j: Journal) -> None:
    far = NOW + 20 * 24 * HOUR
    j.exhausted_put(KEY_A, Mark(far, NOW, 1), j.usage_seq_next())
    deferral(j, 1, far)
    first = clamp_bound(NOW - 10 * 24 * HOUR, S)               # a rollback larger than max_window_hours
    assert j.clamp_deadlines(first) == 2
    assert stored(j) == (str(first), str(first))
    for back in (20, 30, 40):                                   # several more jumps back, then forward
        assert j.clamp_deadlines(clamp_bound(NOW - back * 24 * HOUR, S)) == 0
    assert j.clamp_deadlines(clamp_bound(NOW + 30 * 24 * HOUR, S)) == 0
    assert stored(j) == (str(first), str(first))


def test_the_clamp_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "wsd.db"
    j = Journal(path)
    far = NOW + 20 * 24 * HOUR
    j.exhausted_put(KEY_A, Mark(far, NOW, 1), j.usage_seq_next())
    first = clamp_bound(NOW - 10 * 24 * HOUR, S)
    j.clamp_deadlines(first)
    j.close()
    j = Journal(path)
    try:
        assert j.clamp_deadlines(clamp_bound(NOW - 30 * 24 * HOUR, S)) == 0
        assert stored(j)[0] == str(first)
    finally:
        j.close()


def test_a_replacing_value_is_clamped_once_in_its_turn(j: Journal) -> None:
    far = NOW + 20 * 24 * HOUR
    j.exhausted_put(KEY_A, Mark(far, NOW, 1), j.usage_seq_next())
    deferral(j, 1, far)
    first = clamp_bound(NOW - 10 * 24 * HOUR, S)
    j.clamp_deadlines(first)
    j.exhausted_put(KEY_A, Mark(far, NOW, 2), j.usage_seq_next())    # a new mark: a new sequence
    deferral(j, 2, far)                                              # a new deferral number
    second = clamp_bound(NOW - 15 * 24 * HOUR, S)
    assert j.clamp_deadlines(second) == 2
    assert stored(j) == (str(second), str(second))
    assert j.clamp_deadlines(clamp_bound(NOW - 30 * 24 * HOUR, S)) == 0
    markers = {str(r[0]) for r in j.db.execute("SELECT key FROM meta WHERE key LIKE 'clamp:%'")}
    assert markers == {f"{CLAMP_EXHAUSTED}{KEY_A}:2", f"{CLAMP_DEFERRAL}sk-c:2"}    # the stale ones pruned


def test_a_stale_marker_never_freezes_a_new_value(j: Journal) -> None:
    """A marker left for a value that was replaced under the same identity (written by hand here) does not
    match the new value, so the new value is still clamped."""
    far = NOW + 20 * 24 * HOUR
    deferral(j, 1, far)
    first = clamp_bound(NOW - 10 * 24 * HOUR, S)
    j.clamp_deadlines(first)
    j.db.execute("UPDATE deferrals SET defer_until = ? WHERE session_key = 'sk-c'", (str(far + 1),))
    second = clamp_bound(NOW - 15 * 24 * HOUR, S)
    assert j.clamp_deadlines(second) == 1
    assert stored(j)[1] == str(second)


def test_a_crash_after_the_clamp_replays_without_moving_it(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    far = rig.clock() + 20 * 24 * HOUR
    rig.journal.exhausted_put(KEY_A, Mark(far, rig.clock(), 1), rig.journal.usage_seq_next())
    rig.restart(CrashAt("usage.clamped"))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    first = clamp_bound(rig.clock(), S)
    rig.restart()
    rig.clock.advance(-10 * 24 * HOUR)
    rig.pickup()
    rig.clock.advance(30 * 24 * HOUR)
    rig.pickup()
    row = rig.journal.db.execute("SELECT until FROM account_exhausted").fetchone()
    assert row[0] == str(first)


# --- losing the tables ---

def test_losing_the_usage_tables_gives_the_first_account(tmp_path: Path) -> None:
    accounts = codex_accounts(tmp_path)
    path = tmp_path / "wsd.db"
    j = Journal(path)
    key = accounts.current_key("codex", "default")
    ingest_trusted(j, key, obs(100.0, NOW + HOUR), NOW, S)
    mark_exhausted(j, key, NOW + HOUR, NOW, S)
    assert isinstance(decide(accounts, j, "p-two", None, NOW, S), Deadline)
    j.close()
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("DELETE FROM account_usage")
    db.execute("DELETE FROM account_exhausted")
    db.close()
    j = Journal(path)
    try:
        assert decide(accounts, j, "p-two", None, NOW, S) == Chosen("default", key)
    finally:
        j.close()


def codex_accounts(tmp_path: Path) -> ConfiguredAccounts:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "auth.json").write_text("{}")
    return ConfiguredAccounts({"p-two": ProfileAccounts("codex")}, {"HOME": str(home)})
