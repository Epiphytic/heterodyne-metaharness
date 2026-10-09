# btq-yk7ns (AU-5): usage cache, headroom gate and per-candidate pickup (design r1)

Base: main 1ff70c1 (AU-2 and AU-3 merged). Sources:
- the accounts plan, §AU-5 and the dependency graph (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at 66ad195);
- ADR 0001 r14 §4.4 D1, D3, D4, D5 (the parts AU-5 reads), D6, D9, §5.2, §10 and §11 (hermes-workstreams-v2 at 82b2e4b);
- AU-3 as merged: the v2 journal tables, the launch guard's steps 0–6 and the `Accounts` seam (`src/heterodyne/wsd/accounts.py`), whose docstring says AU-5 replaces `choose` at the same call site;
- spike S7 (`docs/spikes/s7-accounts-and-usage.md`) for the shapes of the usage sources.

Scope: plan 3b's usage cache, the pure headroom gate, ingestion, and per-candidate gating in pickup, with the quota wake time. Design only: nothing is implemented here. PoC-scoped: no usage producer, no deferral writer and no rendering (AU-7, AU-4 and AU-9).

## 0. What AU-5 changes in production, and what it doesn't

Until S7's findings are accepted (G2), `CAPABILITIES` is empty, and AU-2 rejects named accounts on every adapter (no login binding) and `failover = "next"` everywhere (no trusted read). So in production every profile resolves to the implicit `default` account with `failover = "none"`. Nothing writes usage rows either: ingestion's only callers before AU-7 are tests. So with AU-5 merged, the gate always returns the profile's `default` account, exactly as AU-3's `DefaultOnly.choose` did, and pickup never skips a candidate. Everything below is exercised by tests, with capabilities, accounts and usage rows injected. That is the intended state: AU-7 and AU-8 plug producers and failover into a gate that is already tested.

## 1. What the sources fix (implemented exactly)

- **Tables** (created by AU-3's upgrade; AU-5 adds no schema): `account_usage` (trusted, key (credential_key, window_id)), `account_usage_untrusted` (key (session_key, generation, window_id)), `account_exhausted` (key credential_key, trusted only), and the counter `meta.usage_receipt_seq`.
- **Ingestion (D3):** each observation gets the next receipt sequence and `observed_at` = wsd's clock at receipt; any payload timestamp is ignored. The account is the delivering channel's launch entry, never a payload value; a payload naming another account is dropped. `used_percent` must be a finite number in [0, 100], otherwise the observation is dropped. `resets_at` must be after `observed_at` and no later than `observed_at + max_window_hours`, otherwise it is stored as unknown. Within a key, the higher receipt sequence wins; the two tables never replace each other.
- **Gate (D4):** a pure function of (resolved profile, previous launch key, capabilities, cache, now, settings). Trusted windows and trusted exhaustion marks block; unknown, stale and expired data are eligible. The permitted list is built from the failover mode and then credential continuity. The result is the first eligible permitted account, `account_changed` when the list is empty, or a deadline no earlier than `now + min_recheck_seconds`.
- **Clock (D4):** times are UTC. A window or mark observed after `now` is ignored until a newer receipt replaces it or the clock catches up. A stored deadline or `until` later than `now + max_window_hours` is rewritten once, in the journal, to that bound.
- **Pickup (§5.2):** each candidate is gated on its own resolved profile (a resume on its recorded profile). A candidate with no eligible account is skipped, never claimed, and pickup goes on. At the end of a pickup that leaves the coder role idle, the quota wake time is computed over quota-only waiters and armed only if it is after `now`. It is not stored.

## 2. Modules and data shapes

Two new modules. `gate.py` already exists (the pause gate), so the headroom gate gets its own name.

- `src/heterodyne/wsd/usage.py`: the observation types, ingestion and the journal reads (the impure half).
- `src/heterodyne/wsd/headroom.py`: the pure gate, the permitted list, the clamp rule and the wake-time function. It doesn't import the journal or the filesystem.

### 2.1 Settings

```python
@dataclass(frozen=True)
class UsageSettings:              # [usage], host only; AU-2 already validates every bound
    reserve_percent: int
    stale_minutes: int
    unknown_backoff_minutes: int
    untrusted_max_defer_minutes: int
    min_recheck_seconds: int
    max_window_hours: int
```

`settings.resolve` reads `[usage]` from the host config (the defaults come from `defaults.toml`) and puts one `UsageSettings` on every `WorkstreamSettings` as `usage`. `Deps` gains `clock: Callable[[], datetime]`, which defaults to UTC now truncated to whole seconds. Every scheduling decision in AU-5 takes `now` from `deps.clock`, so the tests drive one injected clock, jumps included. The journal's own `journaled_at`/`since` stamps are bookkeeping and keep using `journal.now()`.

### 2.2 Times

ADR D4 says "times are stored as UTC epoch seconds". AU-3's frozen schema made the usage columns `TEXT`, like every other journal time. AU-5 stores them as ISO-8601 UTC with whole seconds (`journal.now()`'s format) and compares them as aware `datetime`s. That is the same instant and resolution, timezone-free. Open decision **O2**.

### 2.3 Observations and rows

```python
@dataclass(frozen=True)
class Observation:               # what a producer (AU-7) hands to ingestion, already parsed
    window_id: str               # stable per window, chosen by the producer (§2.4)
    kind: str                    # a short display label, e.g. "5h" or "7d" (§2.4)
    used_percent: object         # validated here: a finite int/float in [0, 100], never a bool
    resets_at: datetime | None   # the payload's reset hint, if any
    source: str                  # e.g. "codex.app-server", "claude.statusline", "codex.host-read"
    claimed_key: str | None = None   # a credential key the payload claims, if the producer can derive one

@dataclass(frozen=True)
class Window:                    # a stored row, either table
    window_id: str; kind: str; used_percent: float
    resets_at: datetime | None; observed_at: datetime; receipt_seq: int; source: str

@dataclass(frozen=True)
class Mark:                      # account_exhausted
    until: datetime; observed_at: datetime; receipt_seq: int

@dataclass(frozen=True)
class UsageCache:                # the gate's whole view: trusted rows only (§3.4)
    windows: Mapping[str, tuple[Window, ...]]    # by credential key
    marks: Mapping[str, Mark]                    # by credential key
```

`window_id`, `kind` and `source` must each match `[a-z0-9][a-z0-9._:-]{0,63}`, otherwise the observation is dropped. They are short labels and must never carry a path, an email or an ID.

### 2.4 Window identity (from the offline probe)

What I probed, read-only and offline, with no call that spends quota:
- `claude --version`: 2.1.286. `codex --version`: codex-cli 0.160.0. These are S7's pinned versions.
- I read the shape of one recent local Codex session log under `~/.codex/sessions`. I printed only key names and types, plus the window length and a zero percentage. Its `token_count` events carry `rate_limits = {limit_id, limit_name, plan_type, primary: {used_percent: float, window_minutes: int, resets_at: int epoch s}, secondary, credits: {...}, rate_limit_reached_type, ...}`. In that log, **`primary` was the 10080-minute (7-day) window and `secondary` was null**. S7's app-server read had `primary` = 300 minutes and `secondary` = 10080 minutes. So the slot name doesn't identify a window.
- I counted the `isApiErrorMessage` entries in the local Claude transcripts by `(error, apiErrorStatus)`, with no content. `("rate_limit", 429)` entries exist and carry no reset time, which matches S7 (e).

So **`window_id` is the producer's stable identity of a window, never a slot name.** The recommendation for AU-7 is Codex `"<limit_id>:<window_minutes>m"` (for example `codex:300m` and `codex:10080m`) and Claude's own keys `five_hour` and `seven_day`. `kind` is the display label AU-9 shows (`5h`, `7d`). AU-5 treats both as opaque labels and checks only their format. Open decision **O3**.

### 2.5 Journal API (`journal.py`)

- `usage_put_trusted(key, window, seq)`, `usage_put_untrusted(session_key, generation, window, seq)` and `exhausted_put(key, mark, seq)`. Each is an `INSERT … ON CONFLICT(<key>) DO UPDATE … WHERE excluded.receipt_seq > receipt_seq`.
- `usage_seq_next()`: increments `meta.usage_receipt_seq` and returns the new value. It is always called inside the same transaction as the row write.
- `usage_cache(keys) -> UsageCache`: the trusted windows and marks for exactly those keys. Rows of keys outside `keys` are never read, which implements D1's "rows whose key no configured account has are ignored" without deleting them.
- `usage_untrusted(session_key, generation) -> tuple[Window, ...]`: one launch's own rows.
- `deferrals_current(ws, role) -> list[DeferralRow]`: each session's highest-numbered record for beads the journal has in `ws`, joined through `beads`, because `deferrals` has no `ws` column. AU-5 only reads deferrals, for the wake time; AU-4 writes them.
- `clamp_deadlines(bound) -> int`: in one transaction, it rewrites to `bound` every `account_exhausted.until` and every current `deferrals.defer_until` later than `bound`, and returns how many it changed (§3.5).

## 3. Behaviour

### 3.1 Ingestion (`usage.py`)

```python
def ingest_untrusted(j, launch: tuple[str, int], obs: Observation, now: datetime, s: UsageSettings) -> Ingested
def ingest_trusted(j, credential_key: str, obs: Observation, now: datetime, s: UsageSettings) -> Ingested
def mark_exhausted(j, credential_key: str, reset_hint: datetime | None, now: datetime, s: UsageSettings) -> Ingested
```

`Ingested` is `Stored(seq)` or `Dropped(reason)`. A dropped observation writes nothing, not even the counter.

In order:
1. **Attribution.**
   - **Untrusted.** The caller passes the delivering channel's `(session_key, generation)`. That must be an identity wsd established, such as the per-session app-server connection or the per-launch spool directory, and never a tag a session can write (that is AU-7's obligation, stated here so AU-7 can't miss it). The launch entry must exist, be dispatched and not be `abandoned`; otherwise the observation is dropped (`unknown_launch`).
   - **Trusted.** The caller passes the credential key that the host-side read ran under.
   - **Claimed key.** If `obs.claimed_key` is set and differs from the attributed key (the entry's `credential_key` for untrusted), the observation is dropped (`other_account`).
2. **Percent.** `used_percent` must be an `int` or `float`, not a `bool`, finite, and in [0, 100]; otherwise the observation is dropped (`bad_percent`).
3. **Labels.** `window_id`, `kind` and `source` must match the label pattern; otherwise the observation is dropped (`bad_label`).
4. **Reset.** `resets_at` is kept only if `now < resets_at <= now + max_window_hours`; otherwise it is stored as unknown.
5. **Write.** In one transaction: `seq = usage_seq_next()`, then the row with `observed_at = now`.

`mark_exhausted` applies rule 4 to `reset_hint`. `until` is the kept hint, or `now + unknown_backoff_minutes`.

AU-5 also provides `untrusted_defer_until(hint, now, s)`, D5's clamp: the hint, or `now + unknown_backoff_minutes` without one, clamped to `[now + min_recheck_seconds, now + untrusted_max_defer_minutes]`. It is a pure helper that AU-4 and AU-7 call. AU-5 only tests it.

### 3.2 The resolved profile (`accounts.py`)

AU-3's `Accounts.choose` and `Accounts.eligible` are replaced by one method that gives the gate its input:

```python
@dataclass(frozen=True)
class Candidate:
    account: str
    key: str                       # the current credential key, resolved now (AU-3 §2.1 rule)

@dataclass(frozen=True)
class ProfileView:
    profile: str
    adapter: str
    failover: Literal["none", "next"]
    accounts: tuple[Candidate, ...]   # profiles.<p>.accounts in order, or (default,) when unset
    capabilities: Capabilities

class Accounts(Protocol):
    def view(self, profile: str) -> ProfileView: ...   # raises ConfigError (path-free) if a key can't resolve
    # adapter, configured, current_key, login_resolves: unchanged from AU-3
```

`DefaultOnly` becomes `ConfiguredAccounts`. It reads each profile's `adapter`, `accounts` and `failover` from the loaded config, and every candidate's key is resolved afresh, as AU-3's `current_key` does. If any listed account fails to resolve, `view` raises `ConfigError`, and the guard escalates `CONFIG_INVALID` as it does today. With `"next"` that is stricter than dropping the account, but `"next"` can't be configured before AU-7, and AU-8 owns the freshness-refusal path that moves on to the next account. Tests keep injecting capabilities through the constructor, as AU-3's tests do.

### 3.3 The gate (`headroom.py`, pure)

```python
@dataclass(frozen=True)
class Deadline:
    at: datetime
    accounts: tuple[str, ...]      # the permitted accounts, for the escalation and AU-9's text

def permitted(p: ProfileView, previous_key: str | None) -> tuple[Candidate, ...]
def blocking(key: str, cache: UsageCache, now: datetime, s: UsageSettings) -> tuple[datetime, ...]   # clear times
def gate(p: ProfileView, previous_key: str | None, cache: UsageCache, now: datetime,
         s: UsageSettings) -> Chosen | AccountChanged | Deadline
```

The capabilities are part of `ProfileView`, so the signature is the ADR's (profile, previous launch, capabilities, cache, now, settings) with the capabilities carried inside the profile.

**`permitted`** follows D4 literally:
1. The mode list: `"none"` takes `accounts[:1]`; `"next"` takes all of `accounts`.
2. Continuity, only when `previous_key` is not None:
   - `"none"`: keep the list if `accounts[0].key == previous_key`; otherwise the list is empty.
   - `"next"` with `capabilities.can_switch`: move the candidate whose key is `previous_key` to the front, if there is one (affinity).
   - `"next"` without `can_switch`: keep only the candidate whose key is `previous_key`; otherwise the list is empty.

**`blocking`** returns a clear time for each condition that blocks the key now:
- **A trusted window** blocks when all of these hold:
  - `observed_at <= now` (a future-dated row is ignored);
  - `used_percent >= 100 - reserve_percent`;
  - `observed_at + stale_minutes > now`;
  - `resets_at` is None or later than `now`.

  Its clear time is `min(resets_at, observed_at + stale_minutes)`, where a missing `resets_at` counts as infinity.
- **A mark** blocks when `observed_at <= now < until`. Its clear time is `until`.

An account is eligible when `blocking` is empty.

**`gate`:**
1. If `permitted` is empty, return `AccountChanged`, with a detail naming the profile and the mode, never a path.
2. Otherwise, return `Chosen` with the first eligible permitted account.
3. Otherwise, return `Deadline(at = max(min over permitted of max(blocking(a)), now + min_recheck_seconds))`.

The gate never reads untrusted rows. D4 lists only trusted windows and trusted marks as blocking. D3 says untrusted rows only defer the producing launch's own bead, which is AU-4's and AU-7's defer path through `usage_untrusted` and `untrusted_defer_until`. So the property "an untrusted row never changes the result for another session, bead or account" holds by construction, and the tests still check it. Open decision **O5** confirms this reading.

**`decide(accounts, journal, profile, previous_key, now, s)`** is the one impure wrapper. It runs `view`, then `usage_cache` on the view's keys, then `gate`. Pickup, the guard's step 2 and the guard's step 3 all call it, so they share one permitted list (D4). Step 3, "still eligible", is:

```python
admits(view, previous_key, entry, cache, now, s) = entry.account's candidate in permitted(view, previous_key)
                                                   with key == entry.credential_key and blocking(...) == ()
```

`previous_key` is the key of the session's highest `launched` generation, read after step 1's reconciliation (AU-3), exactly as `_pin` reads it today.

### 3.4 The launch guard (`park.py`)

The guard's steps keep AU-3's order. Only the account calls change:
- **Step 2 (`_pin`).** `choose` becomes `decide`.
  - `Chosen`: pin it, as today.
  - `AccountChanged`: AU-3's interim `ACCOUNT_CHANGED` escalation, unchanged (AU-4 replaces it).
  - `Deadline`: the interim below.
- **Step 3 (`_pin_holds`).** `accounts.eligible` becomes `admits`. A pinned account that is now blocked is abandoned, as today, and the next generation chooses again through `decide`.
- **The interim for `Deadline` on a claimed bead (before AU-4).** The guard shelves the bead with plan 3's existing `_shelve`: it labels it `v2:parked` with no blockers and finishes the operation. The bead's row becomes `PARKED` with the new reason `QUOTA` and the detail `no headroom until <UTC>`. A parked bead with every blocker closed is already resumable. So pickup's source 1 gates it on each pickup, skips it while it is ineligible, counts its deadline in the wake time, and resumes it once the gate gives an account.
  - This needs no new state, label, operation or crash point (`<kind>.shelved!` exists).
  - It is reachable only in a race between pickup's gate and the guard's, or on a sweep resume of a bead whose session ended. Neither can happen in production before AU-7, because nothing writes usage rows.
  - AU-4 replaces it with the `v2:deferred` defer operation.
  - Open decision **O1**; the alternative is an escalation.

### 3.5 The clock rule

`clamp_deadlines(now + max_window_hours)` runs at the start of every pickup, inside the pickup's entry lock, before any gate call. It only ever moves a value earlier. After a rewrite to `now0 + max`, every later `now >= now0` gives a bound at least as large, so the value doesn't move again. Only a further jump back can lower it again, and that is a new rollback. This satisfies "rewritten once, never moved again" without a marker column. Windows never need rewriting: ingestion caps `resets_at` at `observed_at + max_window_hours`, and a window observed after `now` is ignored.

### 3.6 Pickup (`scheduler.py`)

After the replay of open operations, and only when the coder role is free:

1. **Source 1: resumable parked beads, in plan 3's order.** For each one, the gate runs on its recorded session's profile, `bead.record().profile`, with that session's `previous_key`. A `Deadline` skips it and keeps the deadline. `Chosen` resumes it as today. `AccountChanged` doesn't skip: it goes to the guard, which escalates it as AU-3 does (O6), so a repointed account is never a silent, permanent skip.
2. **Source 2: new ready beads.**
   - Before claiming, pickup computes the bead's placement with `place()`, which applies a `role:` override, and its session key with `record()`. It then gates on that profile. `previous_key` comes from the journal's entries for that session key, if the bead was launched before and handed back.
   - A `Deadline` skips the bead (it is never claimed) and keeps the deadline.
   - If `place()` raises `ConfigInvalid`, the bead isn't gated: it goes to `start_new`, which escalates as today.
3. **Unresolved entries.** A candidate whose session has an unresolved entry (dispatched, no outcome) or an unsettled adoption is not gated, because D4 forbids gating it. It goes to the guard, which reconciles first (AU-3 steps 0–1) and gates at step 2.
4. **Wake time** (§5.2). It is computed when the pickup leaves the coder role idle: outcome `NOTHING`, `STUCK` or `DEFERRED`. It is the minimum of:
   - the deadline of every candidate skipped in this pickup;
   - the `defer_until` of every current coder `quota` deferral that is still after `now`, whether or not pickup considered it. This excludes beads whose journal row is `HELD` or `STUCK`, beads labelled `v2:held` or `needs-human`, and beads with open blockers.

   `account_changed` deferrals never count. A minimum that isn't after `now` is never armed. The result is kept on the `Scheduler` as `wake_at` and never journaled.
5. **Outcome `DEFERRED`.** No candidate started, and at least one was skipped for quota. `STUCK` still wins over `DEFERRED`, because a human is needed, and the wake time is armed either way.

**Daemon.**
- `TriggerKind.QUOTA_WAKE`.
- After every pickup of a workstream, `Wsd` cancels that workstream's pending wake and, if `wake_at` is set, arms a one-shot task on the event loop for `wake_at - clock()`. When it fires, it submits a pickup with `Trigger(QUOTA_WAKE)` through the workstream's normal lane, so it serialises with every other job.
- Stop cancels wakes along with the timers.
- Startup's pickup recomputes the wake time, as §5.2 says.

**Workstream state.** `ws_state` gains `WsState.DEFERRED`. When the wake time is set and the workstream would otherwise be `IDLE`, or every non-terminal row is `PARKED` with reason `QUOTA`, the state is `DEFERRED`, never idle (§5.2). AU-9 renders it as "deferred: quota until HH:MM" from `wake_at`. Open decision **O7**.

### 3.7 Losing the usage tables

The cache is advisory. Missing rows mean unknown, which is eligible. A journal rebuilt by plan 3's D8 path, or tables emptied by hand, gives `Chosen` for the first permitted account. Nothing else is derived from them.

## 4. Failure modes

| Failure | Behaviour |
|---|---|
| A login key can't resolve during `view` | `ConfigError`. At the guard this is `CONFIG_INVALID`, as in AU-3. At pickup the candidate isn't skipped: it goes to the guard, which escalates it with its reason. |
| A malformed or misattributed observation | `Dropped(reason)`, nothing written, a debug log line with the reason and no payload. |
| The clock jumps back | Future-dated rows are ignored. Stored deadlines are clamped once (§3.5). Ordering is by receipt sequence, so a newer low read replaces an older blocking one. |
| The clock jumps forward | Rows expire or go stale, and work is released early. The gate checks again at the next launch. |
| A wake timer fires late or never (a daemon restart) | The backstop pickup and startup's pickup recompute it. Waking late only delays. |
| `Deadline` at the guard | The interim shelve (§3.4, O1). |
| Usage tables lost | Unknown, so eligible (§3.7). |

## 5. Tests (TDD; offline; injected clock; no sleeps)

**`tests/test_wsd_headroom.py`** (pure, hypothesis):
- Unknown, stale (`observed_at + stale <= now`) and expired (`resets_at <= now`) windows, and expired marks, are eligible.
- `"none"` never returns an account other than `accounts[0]`. With a previous key, it never returns a different key, whatever `can_switch` is.
- A `Deadline` is always later than `now`, and at least `now + min_recheck_seconds`.
- Untrusted rows can't change any result. The gate's input has no untrusted rows, and the property test builds the cache through `usage_cache` after writing random untrusted rows.
- Reordering or repointing accounts under `"none"`, with and without `can_switch`, gives `AccountChanged`.
- Affinity: last launched on B, B eligible and A eligible gives B. B blocked, A eligible and no `can_switch` gives a `Deadline`, not A.
- A future-dated row is ignored.
- `permitted` is the same list whether it is called for pickup, step 2 or step 3. All three go through `decide`.

**`tests/test_wsd_usage.py`** (journal):
- Ingestion:
  - drops NaN, ±inf, bools, strings, -1 and 100.5;
  - keeps 0 and 100;
  - stores an unknown reset for one in the past, one equal to `now`, and one beyond `max_window_hours`;
  - ignores payload timestamps;
  - drops a claimed key naming another account;
  - drops observations attributed to an abandoned, undispatched or unknown launch.
- Replacement by receipt sequence: after the clock jumps back, a newer low read replaces an older blocking row and the gate becomes eligible. The two tables never replace each other.
- A reviewer moving P → Q → P: `usage_untrusted` for each `(session_key, generation)` returns only that launch's rows.
- `untrusted_defer_until` clamps both ways.
- `clamp_deadlines`: after a rollback larger than `max_window_hours`, repeated clamps and gates at later times move nothing.
- Losing the usage tables gives the first account.

**`tests/test_wsd_pickup.py`:**
- An eligible later candidate starts while an earlier one is quota-ineligible. The ineligible one is never claimed.
- A resumable parked bead is gated on its recorded profile, not the current coder profile.
- The wake time:
  - is never in the past;
  - includes a future coder quota deferral that pickup didn't consider, using a deferral row inserted directly;
  - ignores a held, stuck, blocked or `needs-human` bead's deferral and an `account_changed` deferral.
- `DEFERRED` is returned when only quota-skipped candidates remain.
- The never-idle oracle gains the kinds `quota_blocked` (a trusted blocking window on the bead's profile) and `quota_clears`, so a case ends `DEFERRED`. The oracle checks that `DEFERRED` means no coder session is listed, every claimable or resumable bead gates to a `Deadline`, and `wake_at > now`.

**`tests/test_wsd_launches.py`** (the guard):
- A pinned account that is blocked before dispatch is abandoned, and the bead is shelved `PARKED/QUOTA` with no dispatch.
- A crash at `<kind>.abandoned`, `<kind>.abandoned!` and `<kind>.shelved!` replays to the same end.
- A shelved bead resumes once the clock passes the deadline.

**`tests/test_wsd_daemon.py`:**
- A pickup that returns `DEFERRED` arms one wake, and the next pickup replaces it.
- Its firing submits `QUOTA_WAKE` through the lane.
- Stop cancels it.

Arming is checked with a fake loop clock and the scheduled delay, with no sleeps.

Existing tests:
- AU-3's tests of `choose`/`eligible` move to `view`/`decide` with unchanged expectations.
- `test_ensure_record_never_rewrites_an_existing_record` is untouched.

## 6. Plan acceptance mapping

| Plan AU-5 acceptance bullet | Where |
|---|---|
| hypothesis: unknown, stale, expired eligible; `"none"` never later or different key; deadline after now; untrusted never affects another | §5 headroom |
| reordering/repointing under `"none"`, switching demonstrated and not, gives `account_changed` | §5 headroom, and the guard's existing interim escalation |
| P → Q → P own untrusted rows; malformed values rejected; injected clock with jumps | §5 usage |
| an eligible later candidate starts when an earlier one is ineligible | §5 pickup |
| a newer low read after a jump back replaces a blocking row; a future-dated row ignored | §5 usage, headroom |
| rollback > `max_window_hours`: deadlines don't move again | §5 usage (`clamp_deadlines`) |
| account affinity on B; B blocked without switching gives a deadline, not A | §5 headroom |
| wake time never past; includes an unconsidered future coder deferral; held, blocked and `account_changed` excluded | §5 pickup |
| an `account_changed` deferral writes one comment, has no timer, survives startup and replay, and is re-gated on a reload | **Partly AU-4** (O4). AU-5 tests that `account_changed` never sets a wake time and that the gate gives it again after a restart. The comment, the deferral record and the re-gate on reload belong to AU-4's defer operation. wsd has no config reload yet. |
| losing the usage tables recovers to unknown | §5 usage |

## 7. Open decisions

- **O1. `Deadline` on a claimed bead before AU-4.** Recommended: the interim shelve (§3.4). It parks with no blockers and reason `QUOTA`, and the existing resumable path, gated, resumes it after the deadline. Alternative: escalate with a new reason, like AU-3's interim `ACCOUNT_CHANGED`. That is safer to reason about, but it needs an operator release for a condition that clears by itself. Either way, it can't happen in production before AU-7.
- **O2. Time storage.** ISO-8601 UTC text with whole seconds in AU-3's `TEXT` columns, instead of the ADR's literal "epoch seconds". This is the same instant and needs no schema change. The alternative is a v3 schema with `INTEGER` columns.
- **O3. Window identity and `kind`.** `window_id` is the producer's stable window identity (`codex:300m`, `five_hour`), never the Codex slot name, because the probe shows `primary` can be the 7-day window. `kind` is a display label. "A payload naming another account" is implemented as a producer-derived `claimed_key`. If AU-7 finds no payload field to derive it from, the check is inert, and attribution by channel is the only defence, as D3 intends.
- **O4. Acceptance bullet moved.** The `account_changed` comment, no-timer and reload behaviour needs AU-4's deferral operation, and AU-4 is blocked by AU-5. AU-5 covers the gate side only (§6). The ADR's "on every configuration reload" needs a reload that wsd doesn't have yet. AU-4 must either add one or name where it lands.
- **O5. The gate ignores untrusted rows entirely.** This reads D4 literally: only trusted windows and marks block. Untrusted rows act only through AU-4's and AU-7's own-bead deferral.
- **O6. `AccountChanged` at pickup.** Recommended: don't skip; send the candidate to the guard, which escalates it, so it is visible. Skipping it would hide it with no wake time and no alert until AU-4.
- **O7. `WsState.DEFERRED` now.** Recommended: in AU-5, because §5.2's "not idle" rule is pickup's. AU-9 only renders it. The alternative leaves the state to AU-9 and reports such a workstream as idle until then.
