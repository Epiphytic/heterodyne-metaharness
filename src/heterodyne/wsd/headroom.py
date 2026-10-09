"""The headroom gate (ADR 0001 r14 §4.4 D4; AU-5 design §2, §3.3, §3.5): pure functions of a resolved
profile, the session's previous launch key, the trusted usage cache, `now` and `[usage]`.

Nothing here reads the journal or the filesystem: `usage.decide` is the one impure wrapper, and pickup,
the guard's step 2 and its step 3 all go through it, so they share one permitted list.

Times are UTC epoch seconds, as `int` in memory and as decimal strings in the journal (§2.2). A stored
value that doesn't parse is unknown, which never blocks.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from heterodyne.wsd.accounts import AccountChanged, Candidate, Chosen, ProfileView
from heterodyne.wsd.launches import LaunchEntry


@dataclass(frozen=True)
class UsageSettings:
    """`[usage]`, host only; AU-2 validates every bound. The defaults are `defaults.toml`'s."""
    reserve_percent: int = 5
    stale_minutes: int = 30
    unknown_backoff_minutes: int = 30
    untrusted_max_defer_minutes: int = 60
    min_recheck_seconds: int = 60
    max_window_hours: int = 192

    @classmethod
    def from_table(cls, table: Mapping[str, Any]) -> "UsageSettings":
        return cls(**{k: int(v) for k, v in table.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Window:
    """A stored usage row, from either table."""
    window_id: str
    kind: str
    used_percent: float
    resets_at: int | None
    observed_at: int
    receipt_seq: int
    source: str


@dataclass(frozen=True)
class Mark:
    """An `account_exhausted` row."""
    until: int
    observed_at: int
    receipt_seq: int


@dataclass(frozen=True)
class UsageCache:
    """The gate's whole view: trusted rows only, by credential key."""
    windows: Mapping[str, tuple[Window, ...]] = field(default_factory=dict[str, tuple[Window, ...]])
    marks: Mapping[str, Mark] = field(default_factory=dict[str, Mark])


@dataclass(frozen=True)
class Deadline:
    at: int
    accounts: tuple[str, ...]      # the permitted accounts, for the escalation and AU-9's text


def epoch(value: object) -> int | None:
    """A stored time, or None (unknown) if it is missing or doesn't parse."""
    if value is None:
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def quota_detail(at: int) -> str:
    """The PARKED/QUOTA row's detail."""
    return f"no headroom until {datetime.fromtimestamp(at, UTC).isoformat(timespec='seconds')}"


def clamp_bound(now: int, s: UsageSettings) -> int:
    """§3.5: no stored deadline may reach past this."""
    return now + s.max_window_hours * 3600


def permitted(p: ProfileView, previous_key: str | None) -> tuple[Candidate, ...]:
    """D4: the failover mode's list, then credential continuity."""
    listed = p.accounts[:1] if p.failover == "none" else p.accounts
    if previous_key is None:
        return listed
    if p.failover == "none":
        return listed if listed and listed[0].key == previous_key else ()
    previous = tuple(c for c in listed if c.key == previous_key)[:1]
    if p.capabilities.can_switch:
        return previous + tuple(c for c in listed if c not in previous)    # affinity
    return previous


def blocking(key: str, cache: UsageCache, now: int, s: UsageSettings) -> tuple[int, ...]:
    """The clear time of each trusted condition that blocks `key` now. Empty: eligible. A row observed
    after `now` is ignored; stale and expired rows don't block."""
    found: list[int] = []
    for w in cache.windows.get(key, ()):
        stale_at = w.observed_at + s.stale_minutes * 60
        if (w.observed_at <= now and w.used_percent >= 100 - s.reserve_percent and stale_at > now
                and (w.resets_at is None or w.resets_at > now)):
            found.append(stale_at if w.resets_at is None else min(w.resets_at, stale_at))
    mark = cache.marks.get(key)
    if mark is not None and mark.observed_at <= now < mark.until:
        found.append(mark.until)
    return tuple(found)


def gate(p: ProfileView, previous_key: str | None, cache: UsageCache, now: int,
         s: UsageSettings) -> Chosen | AccountChanged | Deadline:
    """The first eligible permitted account; `AccountChanged` when nothing is permitted; otherwise the
    earliest time a permitted account clears, never earlier than `now + min_recheck_seconds`."""
    allowed = permitted(p, previous_key)
    if not allowed:
        return AccountChanged(f'profile {p.profile} (failover = "{p.failover}") permits no account: the '
                              "session's last launch used a credential key it can't continue on now")
    clears: list[int] = []
    for c in allowed:
        blocked = blocking(c.key, cache, now, s)
        if not blocked:
            return Chosen(c.account, c.key)
        clears.append(max(blocked))
    return Deadline(max(min(clears), now + s.min_recheck_seconds), tuple(c.account for c in allowed))


def admits(p: ProfileView, previous_key: str | None, entry: LaunchEntry, cache: UsageCache, now: int,
           s: UsageSettings) -> bool:
    """The guard's step 3: the pinned account is still permitted, under the key it was pinned with, and
    nothing blocks it."""
    key = entry.credential_key
    return (any(c.account == entry.account and c.key == key for c in permitted(p, previous_key))
            and not blocking(key, cache, now, s))


def wake_time(times: Iterable[int], now: int) -> int | None:
    """§3.6: the earliest of `times`, armed only if it is after `now`."""
    soonest = min(times, default=None)
    return soonest if soonest is not None and soonest > now else None
