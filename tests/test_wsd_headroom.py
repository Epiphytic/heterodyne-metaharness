"""AU-5: the pure headroom gate (design §2, §3.3; §5 headroom). Keys are obvious fakes."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from heterodyne.config.capabilities import NONE, Capabilities
from heterodyne.wsd.accounts import (
    AccountChanged,
    Candidate,
    Chosen,
    ConfiguredAccounts,
    Failover,
    ProfileAccounts,
    ProfileView,
)
from heterodyne.wsd.headroom import (
    Deadline,
    Mark,
    UsageCache,
    UsageSettings,
    Window,
    admits,
    blocking,
    epoch,
    gate,
    permitted,
    quota_detail,
    wake_time,
)
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.launches import LaunchEntry
from heterodyne.wsd.usage import admitted, decide

S = UsageSettings()
NOW = 1_800_000_000
SWITCH = Capabilities(handoff_relaunch=True)
A, B, C = (Candidate(n, f"ck1-{n * 32}") for n in "abc")


def view(*accounts: Candidate, failover: Failover = "next", caps: Capabilities = NONE) -> ProfileView:
    return ProfileView("p-one", "codex", failover, accounts, caps)


def window(pct: float, observed: int = NOW, resets: int | None = None, wid: str = "w5h") -> Window:
    return Window(wid, "5h", pct, resets, observed, 1, "codex.host-read")


def cache(**rows: tuple[Window, ...]) -> UsageCache:
    return UsageCache({globals()[k].key: v for k, v in rows.items()})


FULL = (window(100.0, resets=NOW + 3600),)

offsets = st.integers(-10 * 86400, 10 * 86400)
pcts = st.floats(0, 100, allow_nan=False)
windows = st.builds(lambda p, o, r: window(p, NOW + o, None if r is None else NOW + r),
                    pcts, offsets, st.none() | offsets)
marks = st.builds(lambda u, o: Mark(NOW + u, NOW + o, 1), offsets, offsets)
caches = st.builds(
    lambda wa, wb, ma: UsageCache({A.key: tuple(wa), B.key: tuple(wb)}, {} if ma is None else {A.key: ma}),
    st.lists(windows, max_size=3, unique_by=lambda w: w.window_id),
    st.lists(windows, max_size=3), st.none() | marks)


# --- eligibility ---

def test_unknown_stale_expired_and_future_rows_are_eligible() -> None:
    p = view(A)
    assert gate(p, None, UsageCache(), NOW, S) == Chosen("a", A.key)
    stale = window(100.0, observed=NOW - S.stale_minutes * 60)
    expired = window(100.0, observed=NOW - 60, resets=NOW)
    future = window(100.0, observed=NOW + 1)
    for row in (stale, expired, future):
        assert gate(p, None, cache(A=(row,)), NOW, S) == Chosen("a", A.key), row
    assert gate(p, None, UsageCache(marks={A.key: Mark(NOW, NOW - 60, 1)}), NOW, S) == Chosen("a", A.key)
    assert gate(p, None, UsageCache(marks={A.key: Mark(NOW + 60, NOW + 1, 1)}), NOW, S) == Chosen("a", A.key)


def test_a_window_blocks_from_the_reserve_until_it_clears() -> None:
    edge = 100 - S.reserve_percent
    assert blocking(A.key, cache(A=(window(edge - 0.1),)), NOW, S) == ()
    assert blocking(A.key, cache(A=(window(edge),)), NOW, S) == (NOW + S.stale_minutes * 60,)
    assert blocking(A.key, cache(A=(window(edge, resets=NOW + 60),)), NOW, S) == (NOW + 60,)


@settings(max_examples=300)
@given(caches, st.none() | st.sampled_from([A.key, B.key, C.key]), st.booleans())
def test_eligible_means_nothing_blocks(c: UsageCache, previous: str | None, switch: bool) -> None:
    p = view(A, B, caps=SWITCH if switch else NONE)
    result = gate(p, previous, c, NOW, S)
    if isinstance(result, Chosen):
        assert blocking(result.key, c, NOW, S) == ()
        for w in c.windows.get(result.key, ()):     # every row it has is unknown, stale, expired or low
            assert (w.observed_at > NOW or w.observed_at + S.stale_minutes * 60 <= NOW
                    or (w.resets_at is not None and w.resets_at <= NOW)
                    or w.used_percent < 100 - S.reserve_percent)


# --- "none" and continuity ---

@settings(max_examples=300)
@given(caches, st.none() | st.sampled_from([A.key, B.key, C.key]), st.booleans())
def test_none_only_ever_gives_the_first_account_on_its_own_key(c: UsageCache, previous: str | None,
                                                               switch: bool) -> None:
    p = view(A, B, failover="none", caps=SWITCH if switch else NONE)
    result = gate(p, previous, c, NOW, S)
    if isinstance(result, Chosen):
        assert result == Chosen("a", A.key)
    if previous is not None and previous != A.key:
        assert isinstance(result, AccountChanged)


def test_reordering_or_repointing_under_none_is_account_changed() -> None:
    for caps in (NONE, SWITCH):
        assert isinstance(gate(view(B, A, failover="none", caps=caps), A.key, UsageCache(), NOW, S),
                          AccountChanged)
        repointed = Candidate("a", C.key)
        assert isinstance(gate(view(repointed, failover="none", caps=caps), A.key, UsageCache(), NOW, S),
                          AccountChanged)


def test_affinity_keeps_the_previous_account() -> None:
    assert gate(view(A, B, caps=SWITCH), B.key, UsageCache(), NOW, S) == Chosen("b", B.key)
    assert gate(view(A, B), B.key, UsageCache(), NOW, S) == Chosen("b", B.key)
    blocked = gate(view(A, B), B.key, cache(B=FULL), NOW, S)
    assert blocked == Deadline(NOW + S.stale_minutes * 60, ("b",))           # never A without switching
    assert gate(view(A, B, caps=SWITCH), B.key, cache(B=FULL), NOW, S) == Chosen("a", A.key)
    assert permitted(view(A, B, caps=SWITCH), B.key) == (B, A)
    assert permitted(view(A, B), C.key) == ()


# --- deadlines ---

@settings(max_examples=300)
@given(caches, st.none() | st.sampled_from([A.key, B.key]), st.booleans(),
       st.sampled_from(["none", "next"]))
def test_a_deadline_is_never_before_the_recheck(c: UsageCache, previous: str | None, switch: bool,
                                                failover: Failover) -> None:
    result = gate(view(A, B, failover=failover, caps=SWITCH if switch else NONE), previous, c, NOW, S)
    if isinstance(result, Deadline):
        assert result.at > NOW and result.at >= NOW + S.min_recheck_seconds


def test_a_deadline_is_the_earliest_permitted_clear() -> None:
    soon = (window(100.0, resets=NOW + 600),)
    later = (window(100.0, resets=NOW + 900), window(100.0, resets=NOW + 1200, wid="w7d"))
    assert gate(view(A, B), None, cache(A=later, B=soon), NOW, S) == Deadline(NOW + 600, ("a", "b"))
    assert gate(view(A, B), None, cache(A=later, B=later), NOW, S) == Deadline(NOW + 1200, ("a", "b"))
    early = cache(A=(window(100.0, resets=NOW + 5),))
    assert gate(view(A), None, early, NOW, S) == Deadline(NOW + S.min_recheck_seconds, ("a",))


# --- untrusted rows ---

@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.sampled_from([A.key, B.key]), windows), max_size=6), caches)
def test_untrusted_rows_never_change_the_gate(tmp_path_factory: pytest.TempPathFactory,
                                              untrusted: list[tuple[str, Window]],
                                              trusted: UsageCache) -> None:
    j = Journal(tmp_path_factory.mktemp("j") / "wsd.db")
    try:
        for key, ws in trusted.windows.items():
            for w in ws:
                j.usage_put_trusted(key, w, j.usage_seq_next())
        for key, m in trusted.marks.items():
            j.exhausted_put(key, m, j.usage_seq_next())
        p = view(A, B)
        before = gate(p, None, j.usage_cache(c.key for c in p.accounts), NOW, S)
        for n, (key, w) in enumerate(untrusted):
            j.usage_put_untrusted(key, n + 1, w, j.usage_seq_next())
        assert gate(p, None, j.usage_cache(c.key for c in p.accounts), NOW, S) == before
    finally:
        j.close()


# --- one permitted list for pickup, step 2 and step 3 ---

def test_pickup_and_both_guard_steps_share_one_permitted_list(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}")
    accounts = ConfiguredAccounts({"p-two": ProfileAccounts("codex", failover="next")}, {"HOME": str(home)})
    j = Journal(tmp_path / "wsd.db")
    try:
        key = accounts.current_key("codex", "default")
        chosen = decide(accounts, j, "p-two", None, NOW, S)
        assert chosen == Chosen("default", key)
        entry = LaunchEntry("sk", 1, "alpha", "btq-1", "coder", "p-two", "default", key, "", False, "t")
        assert admitted(accounts, j, None, entry, NOW, S)
        assert admits(accounts.view("p-two"), None, entry, UsageCache(), NOW, S)
        j.usage_put_trusted(key, FULL[0], j.usage_seq_next())
        assert isinstance(decide(accounts, j, "p-two", None, NOW, S), Deadline)
        assert not admitted(accounts, j, None, entry, NOW, S)
        gone = ConfiguredAccounts({}, {"HOME": str(home)})        # the profile no longer exists
        assert not admitted(gone, j, None, entry, NOW, S)
    finally:
        j.close()


# --- helpers ---

def test_epoch_quota_detail_and_wake_time() -> None:
    assert epoch("1800000000") == NOW and epoch(None) is None and epoch("soon") is None
    assert quota_detail(NOW) == "no headroom until 2027-01-15T08:00:00+00:00"
    assert wake_time([NOW + 5, NOW + 1], NOW) == NOW + 1
    assert wake_time([NOW, NOW + 9], NOW) is None and wake_time([], NOW) is None
