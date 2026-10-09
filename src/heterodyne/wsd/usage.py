"""Usage ingestion and the gate's journal reads (ADR 0001 r14 §4.4 D3, D4, D5; AU-5 design §2.3, §3.1).

Each observation gets the next receipt sequence and `observed_at` = wsd's clock at receipt; a payload's
own timestamps are never read. The account is the delivering channel's, never a payload value: an
untrusted report is attributed to the launch entry wsd established for its channel (AU-7 must never let a
session choose it), a trusted one to the credential key the host-side read ran under. A dropped
observation writes nothing, not even the counter, and is logged at debug level by reason, with no
payload.

`decide` is the gate's one impure wrapper: the profile's view, the trusted cache of exactly its keys,
then the pure gate. Pickup, the guard's step 2 and its step 3 (`admitted`) all use it.
"""

import logging
import math
import re
from dataclasses import dataclass

from heterodyne.config import ConfigError
from heterodyne.wsd.accounts import AccountChanged, Accounts, Chosen
from heterodyne.wsd.headroom import Deadline, Mark, UsageSettings, Window, admits, gate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.launches import ABANDONED, LaunchEntry

log = logging.getLogger(__name__)
LABEL = re.compile(r"[a-z0-9][a-z0-9._:-]{0,63}")


@dataclass(frozen=True)
class Observation:
    """What a producer (AU-7) hands to ingestion, already parsed."""
    window_id: str               # the producer's stable identity of the window, never a slot name
    kind: str                    # a short display label, e.g. "5h" or "7d"
    used_percent: object         # validated here: a finite int/float in [0, 100], never a bool
    resets_at: int | None        # the payload's reset hint, if any
    source: str                  # e.g. "codex.app-server", "claude.statusline", "codex.host-read"
    claimed_key: str | None = None   # a credential key the payload claims, if the producer can derive one


@dataclass(frozen=True)
class Stored:
    seq: int


@dataclass(frozen=True)
class Dropped:
    reason: str     # unknown_launch, other_account, bad_percent or bad_label


type Ingested = Stored | Dropped


def kept_reset(hint: int | None, now: int, s: UsageSettings) -> int | None:
    """Rule 4: a reset hint is kept only if it is after `now` and within `max_window_hours`."""
    if hint is None or isinstance(hint, bool) or not now < hint <= now + s.max_window_hours * 3600:
        return None
    return int(hint)


def untrusted_defer_until(hint: int | None, now: int, s: UsageSettings) -> int:
    """D5's clamp for a session's own untrusted report: the hint, or `now + unknown_backoff_minutes`,
    within [now + min_recheck_seconds, now + untrusted_max_defer_minutes]."""
    at = now + s.unknown_backoff_minutes * 60 if hint is None else int(hint)
    return min(max(at, now + s.min_recheck_seconds), now + s.untrusted_max_defer_minutes * 60)


def _drop(reason: str) -> Dropped:
    log.debug("usage observation dropped: %s", reason)
    return Dropped(reason)


def _check(obs: Observation, key: str, now: int, s: UsageSettings) -> Window | Dropped:
    """Rules 1 (the claimed key), 2, 3 and 4. The receipt sequence is filled in at the write."""
    if obs.claimed_key is not None and obs.claimed_key != key:
        return _drop("other_account")
    pct = obs.used_percent
    if isinstance(pct, bool) or not isinstance(pct, int | float) or not math.isfinite(pct):
        return _drop("bad_percent")
    if not 0 <= pct <= 100:
        return _drop("bad_percent")
    if not all(LABEL.fullmatch(v) for v in (obs.window_id, obs.kind, obs.source)):
        return _drop("bad_label")
    return Window(obs.window_id, obs.kind, float(pct), kept_reset(obs.resets_at, now, s), now, 0, obs.source)


def ingest_untrusted(j: Journal, launch: tuple[str, int], obs: Observation, now: int,
                     s: UsageSettings) -> Ingested:
    """A session's own report, delivered on the channel wsd established for `launch` (session key,
    generation). It only ever defers that launch's own bead (AU-4, AU-7); the gate never reads it."""
    with j.transaction():
        entry = j.launch(*launch)
        if entry is None or entry.dispatched_at is None or entry.outcome == ABANDONED:
            return _drop("unknown_launch")
        checked = _check(obs, entry.credential_key, now, s)
        if isinstance(checked, Dropped):
            return checked
        seq = j.usage_seq_next()
        j.usage_put_untrusted(*launch, checked, seq)
    return Stored(seq)


def ingest_trusted(j: Journal, credential_key: str, obs: Observation, now: int, s: UsageSettings) -> Ingested:
    """A host-side read, run under `credential_key`."""
    checked = _check(obs, credential_key, now, s)
    if isinstance(checked, Dropped):
        return checked
    with j.transaction():
        seq = j.usage_seq_next()
        j.usage_put_trusted(credential_key, checked, seq)
    return Stored(seq)


def mark_exhausted(j: Journal, credential_key: str, reset_hint: int | None, now: int,
                   s: UsageSettings) -> Ingested:
    """A trusted limit signal: blocked until the kept reset hint, or `now + unknown_backoff_minutes`."""
    until = kept_reset(reset_hint, now, s)
    with j.transaction():
        seq = j.usage_seq_next()
        j.exhausted_put(credential_key, Mark(now + s.unknown_backoff_minutes * 60 if until is None else until,
                                             now, seq), seq)
    return Stored(seq)


def decide(accounts: Accounts, j: Journal, profile: str, previous_key: str | None, now: int,
           s: UsageSettings) -> Chosen | AccountChanged | Deadline:
    """The gate on the profile as it resolves now. Raises ConfigError (path-free) if it can't resolve."""
    view = accounts.view(profile)
    return gate(view, previous_key, j.usage_cache(c.key for c in view.accounts), now, s)


def admitted(accounts: Accounts, j: Journal, previous_key: str | None, entry: LaunchEntry, now: int,
             s: UsageSettings) -> bool:
    """The guard's step 3 for a pinned entry, through the same view and cache as `decide`. A profile or
    key that can't be resolved has changed: not admitted."""
    try:
        view = accounts.view(entry.profile)
    except ConfigError:
        return False
    return admits(view, previous_key, entry, j.usage_cache(c.key for c in view.accounts), now, s)
