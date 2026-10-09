# btq-yk7ns (AU-5): usage cache, headroom gate and per-candidate pickup (design r2)

Base: main 1ff70c1 (AU-2 and AU-3 merged). Sources:
- the accounts plan, §AU-5 and the dependency graph (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at 66ad195);
- ADR 0001 r14 §4.4 D1, D3, D4, D5 (the parts AU-5 reads), D6, D9, §5.2, §10 and §11 (hermes-workstreams-v2 at 82b2e4b);
- AU-3 as merged: the v2 journal tables, the launch guard's steps 0–6 and the `Accounts` seam (`src/heterodyne/wsd/accounts.py`), whose docstring says AU-5 replaces `choose` at the same call site;
- spike S7 (`docs/spikes/s7-accounts-and-usage.md`) for the shapes of the usage sources.

Scope: plan 3b's usage cache, the pure headroom gate, ingestion, and per-candidate gating in pickup, with the quota wake time. Design only: nothing is implemented here. PoC-scoped: no usage producer, no deferral writer and no rendering (AU-7, AU-4 and AU-9).

**r2 changes** (review r1 on c30465c):
1. Clamped values carry a persisted marker, keyed by the value's own identity, so a rewritten value is never moved again, even after another backward jump. A genuinely new value has a new identity and so starts unmarked (§2.5, §3.5).
2. The interim quota shelve is its own journaled step (`quota`), replayed explicitly. A crash at any point ends `PARKED/QUOTA`, unless a hold, `needs-human` or a blocker appeared meanwhile, which wins (§3.4).
3. The wake time also counts every runnable `PARKED/QUOTA` waiter, so a bead the guard shelved in a race always has a wake (§3.6).
4. A ready candidate whose gate gives `account_changed` is skipped without claiming, arms no timer, and is recorded `STUCK/ACCOUNT_CHANGED` like an unclaimable bead. Only claimed resumes go to the guard's escalation (§3.6).
5. O4 is now an explicit plan amendment, moving the whole `account_changed` lifecycle bullet to AU-4, with its tests and the reload trigger named (§8).
6. Times are stored as UTC epoch seconds, as D4 says (in AU-3's `TEXT` columns, as decimal integers). O2 is dropped (§2.2).

Items that need Liam's sign-off: **O1** (the interim quota shelve as a temporary stand-in for D5's deferral, until AU-4) and **the §8 plan amendment** (O4).

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

`settings.resolve` reads `[usage]` from the host config (the defaults come from `defaults.toml`) and puts one `UsageSettings` on every `WorkstreamSettings` as `usage`. `Deps` gains `clock: Callable[[], int]`, which defaults to UTC now in whole epoch seconds. Every scheduling decision in AU-5 takes `now` from `deps.clock`, so the tests drive one injected clock, jumps included. The journal's own `journaled_at`/`since` stamps are bookkeeping and keep using `journal.now()`.

### 2.2 Times

ADR D4 says "times are stored as UTC epoch seconds". AU-5 does exactly that. AU-3's columns are `TEXT`, which doesn't force a format, so every usage and deadline time (`resets_at`, `observed_at`, `until`, and AU-4's `defer_until`) is stored as a decimal integer string of UTC epoch seconds, `str(int(t))`. It is read back with `int()`, and a value that doesn't parse is treated as unknown, which is eligible. In memory, times are `int` epoch seconds, and `deps.clock` returns one. No schema change.

### 2.3 Observations and rows

```python
@dataclass(frozen=True)
class Observation:               # what a producer (AU-7) hands to ingestion, already parsed
    window_id: str               # stable per window, chosen by the producer (§2.4)
    kind: str                    # a short display label, e.g. "5h" or "7d" (§2.4)
    used_percent: object         # validated here: a finite int/float in [0, 100], never a bool
    resets_at: int | None        # the payload's reset hint, if any
    source: str                  # e.g. "codex.app-server", "claude.statusline", "codex.host-read"
    claimed_key: str | None = None   # a credential key the payload claims, if the producer can derive one

@dataclass(frozen=True)
class Window:                    # a stored row, either table
    window_id: str; kind: str; used_percent: float
    resets_at: int | None; observed_at: int; receipt_seq: int; source: str

@dataclass(frozen=True)
class Mark:                      # account_exhausted
    until: int; observed_at: int; receipt_seq: int

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
- `clamp_deadlines(bound) -> int`: in one transaction, it rewrites to `bound` every **unmarked** `account_exhausted.until` and every unmarked current `deferrals.defer_until` later than `bound`, marks each value it rewrites, and returns how many it changed (§3.5).
- **Clamp markers** live in the existing `meta` table, so there is no schema change. Each marker's key is the identity of the value it marks, and its value is the rewritten time:
  - `clamp:exhausted:<credential_key>:<receipt_seq>` for a mark;
  - `clamp:deferral:<session_key>:<number>` for a deferral record.

  A genuinely new value always has a new identity: a new mark gets a new receipt sequence, and a new deferral gets the next number. So it starts unmarked, and nothing has to reset a marker. `exhausted_put` deletes the replaced mark's marker in its own transaction. `clamp_deadlines` deletes markers whose identity no longer exists. A value counts as marked only while its marker exists **and** equals the stored value, so a marker can never freeze a value it didn't write.

## 3. Behaviour

### 3.1 Ingestion (`usage.py`)

```python
def ingest_untrusted(j, launch: tuple[str, int], obs: Observation, now: int, s: UsageSettings) -> Ingested
def ingest_trusted(j, credential_key: str, obs: Observation, now: int, s: UsageSettings) -> Ingested
def mark_exhausted(j, credential_key: str, reset_hint: int | None, now: int, s: UsageSettings) -> Ingested
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
    at: int
    accounts: tuple[str, ...]      # the permitted accounts, for the escalation and AU-9's text

def permitted(p: ProfileView, previous_key: str | None) -> tuple[Candidate, ...]
def blocking(key: str, cache: UsageCache, now: int, s: UsageSettings) -> tuple[int, ...]   # clear times
def gate(p: ProfileView, previous_key: str | None, cache: UsageCache, now: int,
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
- **The interim for `Deadline` on a claimed bead (before AU-4): the quota shelve.** This is a temporary stand-in for D5's deferral and **needs Liam's sign-off (O1)**. Plan 3's `_shelve` is not reused: it labels the bead before anything is journaled, so a crash at `shelved!` replays as an ordinary shelve and loses the quota reason. Instead, `Parker._quota_shelve(op, deadline)` journals its intent first:
  1. **Intent.** In one transaction: `op_step(op, "quota", {"until": "<epoch s>"})`, then the bead row `PARKED`, reason `QUOTA`, detail `no headroom until <UTC>`. Checkpoint `<kind>.quota`.
  2. **Label.** `ensure_label(v2:parked)`, which is idempotent. Checkpoint `<kind>.quota!`.
  3. **Finish.** `show` the bead again. If it now carries `needs-human`, the operation finishes `STUCK/NEEDS_HUMAN`. If it carries `v2:held` or has open blockers, it finishes `parked_state(shown)` with plan 3's reason and blocker detail, because what was added meanwhile wins. Otherwise it finishes `PARKED/QUOTA` with the journaled `until`. The operation status is `ABANDONED` in every case, as for `_shelve`.

  **Replay.** An open pickup or resume operation at step `quota` goes straight to `_quota_shelve`'s steps 2–3, from `Scheduler._start` and `Parker.replay_resume` respectively, before any other check. So a crash at `<kind>.quota` or `<kind>.quota!` always ends as step 3 says, never as an ordinary shelve and never as a launch.

  **What happens next.** The bead is claimed, labelled `v2:parked` with no blockers and journaled `PARKED/QUOTA`, so plan 3's resumable path picks it up. Pickup's source 1 gates it on each pickup, skips it while the gate gives a deadline, counts it in the wake time (§3.6, including a bead the guard shelved in a race), and resumes it once the gate gives an account.
  - New crash points: `<kind>.quota` and `<kind>.quota!` for `pickup` and `resume`. New `Reason.QUOTA`. There is no new state or label.
  - This path is reachable only in a race between pickup's gate and the guard's, or on a sweep resume of a bead whose session ended. Neither can happen in production before AU-7, because nothing writes usage rows.
  - AU-4 replaces it with the `v2:deferred` defer operation, and its upgrade note must convert any open `PARKED/QUOTA` rows into deferrals, or let them resume through this path first.

### 3.5 The clock rule

`clamp_deadlines(now + max_window_hours)` runs at the start of every pickup, inside the pickup's entry lock, before any gate call, as one transaction followed by the checkpoint `usage.clamped`.
- It rewrites an unmarked value later than the bound to the bound and marks it, in the same transaction.
- A marked value is never moved again, whatever the clock does next: a second or third backward jump, a restart (the marker is in the journal), or a replay after a crash (the transaction either landed, marker included, or didn't).
- A genuinely new value replacing it starts unmarked (§2.5), and is clamped at most once in its turn.

Windows are never clamped. Ingestion caps `resets_at` at `observed_at + max_window_hours`, and a window observed after `now` is ignored, so a window that counts can't reach past the bound. Marks are clamped too, for safety, even though the same argument normally keeps them in range.

### 3.6 Pickup (`scheduler.py`)

After the replay of open operations, and only when the coder role is free:

1. **Source 1: resumable parked beads, in plan 3's order.** These beads are already claimed. For each one, the gate runs on its recorded session's profile, `bead.record().profile`, with that session's `previous_key`.
   - `Deadline`: skip it and keep the deadline.
   - `Chosen`: resume it as today.
   - `AccountChanged`: don't skip. It goes to the guard, which escalates it as AU-3 does (O6), so a repointed account on a claimed bead is never a silent, permanent skip.
2. **Source 2: new ready beads.**
   - Before claiming, pickup computes the bead's placement with `place()`, which applies a `role:` override, and its session key with `record()`. It then gates on that profile. `previous_key` comes from the journal's entries for that session key, if the bead was launched before and handed back.
   - `Deadline`: skip the bead, which is never claimed, and keep the deadline.
   - `AccountChanged` (only possible for a bead that was launched before and handed back to the queue): skip the bead, which is **never claimed** (§5.2), and arm no timer for it. Like a ready bead btq can't claim, it gets the journal row `STUCK/ACCOUNT_CHANGED`, so the workstream reports it and is not idle. `_forget_unlisted` drops that row once the bead is no longer listed, and a later pickup whose gate gives an account claims it as usual, because `start_new` replaces the row. The scan continues with the next candidate.
   - If `place()` raises `ConfigInvalid`, the bead isn't gated: it goes to `start_new`, which escalates as today.
3. **Unresolved entries.** A candidate whose session has an unresolved entry (dispatched, no outcome) or an unsettled adoption is not gated, because D4 forbids gating it. It goes to the guard, which reconciles first (AU-3 steps 0–1) and gates at step 2.
4. **Wake time** (§5.2). It is computed when the pickup leaves the coder role idle: outcome `NOTHING`, `STUCK` or `DEFERRED`. It is the minimum of:
   - the deadline of every candidate skipped in this pickup;
   - for every **runnable `PARKED/QUOTA` waiter**, its current gate deadline, or `now + min_recheck_seconds` if it now gates to an account or to `account_changed`, so the next pickup resumes or escalates it. A runnable waiter is a bead of ours with that journal row, labelled `v2:parked`, with no `v2:held` or `needs-human`, no open blockers and no open operation. This covers a bead the guard shelved in a race after pickup admitted it, and one shelved by a replay;
   - the `defer_until` of every current coder `quota` deferral that is still after `now`, whether or not pickup considered it. This excludes beads whose journal row is `HELD` or `STUCK`, beads labelled `v2:held` or `needs-human`, and beads with open blockers.

   `account_changed` deferrals never count. A minimum that isn't after `now` is never armed. The result is kept on the `Scheduler` as `wake_at` and never journaled.
5. **Outcome `DEFERRED`.** No candidate started, and the wake time is set: a candidate was skipped for quota, the guard quota-shelved one, or a runnable `PARKED/QUOTA` waiter exists. `STUCK` still wins over `DEFERRED`, because a human is needed, and the wake time is armed either way. An `account_changed` skip alone gives `STUCK`, never `DEFERRED`.

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
| `Deadline` at the guard | The interim quota shelve (§3.4, O1). |
| A crash during the quota shelve | The `quota` step is journaled before the label, so replay finishes `PARKED/QUOTA` with the journaled `until`, or the state a hold, blocker or `needs-human` added meanwhile gives. It never becomes an ordinary shelve or a launch (§3.4). |
| A ready bead gating to `AccountChanged` | Not claimed, no timer; journal row `STUCK/ACCOUNT_CHANGED`, so the workstream isn't idle. The scan continues (§3.6). |
| The guard shelves a bead pickup had admitted (a race) | The `PARKED/QUOTA` waiter counts toward the wake time, so pickup ends `DEFERRED` with a timer (§3.6). |
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
- `clamp_deadlines`:
  - after a rollback larger than `max_window_hours`, repeated clamps and gates at later times move nothing;
  - **several backward jumps in a row** (each larger than `max_window_hours`) leave a clamped value at its first rewrite;
  - the same holds after reopening the journal (a restart), and after a crash at `usage.clamped` with replay;
  - a new mark (a new receipt sequence) or a new deferral number replacing a clamped value is clamped once in its turn;
  - a stale marker whose value was replaced never freezes the new value.
- Losing the usage tables gives the first account.

**`tests/test_wsd_pickup.py`:**
- An eligible later candidate starts while an earlier one is quota-ineligible. The ineligible one is never claimed.
- A resumable parked bead is gated on its recorded profile, not the current coder profile.
- The wake time:
  - is never in the past;
  - includes a future coder quota deferral that pickup didn't consider, using a deferral row inserted directly;
  - ignores a held, stuck, blocked or `needs-human` bead's deferral and an `account_changed` deferral.
- `DEFERRED` is returned when only quota-skipped candidates remain.
- **A ready bead gating to `AccountChanged`** (launched before, handed back, its account repointed under `"none"`): `claim` is called **zero** times for it, which the fake queue's claim counter asserts. Its row is `STUCK/ACCOUNT_CHANGED`, no wake time comes from it, and an eligible bead after it in the list starts. Once the account is restored, it is claimed and started, and its row is replaced.
- **The guard race, with no other candidate:** pickup's gate admits the only bead, then a blocking trusted row is written at the checkpoint `gate.checked`, so the guard's step 2 gives a `Deadline`. The pickup ends `DEFERRED` with `wake_at` set to that deadline, never `NOTHING` without a timer. With a crash at `pickup.quota` and at `pickup.quota!`, the replayed pickup ends `PARKED/QUOTA` with the same wake. The same holds for a resume operation (`resume.quota`, `resume.quota!`). After the clock passes the deadline, the wake pickup resumes it.
- A `PARKED/QUOTA` waiter that gains a blocker, `v2:held` or `needs-human` between its crash and the replay ends in that state, and drops out of the wake time.
- The wake time counts a runnable `PARKED/QUOTA` waiter at its current gate deadline, and at `now + min_recheck_seconds` when it now gates to an account or to `account_changed`.
- The never-idle oracle gains the kinds `quota_blocked` (a trusted blocking window on the bead's profile), `quota_clears`, `quota_race` (a blocking row written at `gate.checked`) and `account_repointed`. The oracle checks that `DEFERRED` means no coder session is listed, every claimable or resumable bead and every runnable `PARKED/QUOTA` waiter gates to a `Deadline`, and `wake_at > now`; that a pickup with a quota waiter never ends `NOTHING`; and that an `account_repointed` ready bead is never claimed.

**`tests/test_wsd_launches.py`** (the guard):
- A pinned account that is blocked before dispatch is abandoned, and the bead is quota-shelved `PARKED/QUOTA` with no dispatch.
- A crash at `<kind>.abandoned`, `<kind>.abandoned!`, `<kind>.quota` and `<kind>.quota!`, for both pickup and resume, replays to `PARKED/QUOTA` with the journaled `until`, never to an ordinary shelve and never to a launch. With a hold, a blocker or `needs-human` added before the replay, it ends in that state instead.
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
| an `account_changed` deferral writes one comment, has no timer, survives startup and replay, and is re-gated on a reload | **Moved to AU-4 by the §8 amendment (needs Liam's sign-off).** AU-5 keeps only its gate side: an `account_changed` result never sets a wake time, and a ready bead with it is never claimed (§5 pickup). |
| losing the usage tables recovers to unknown | §5 usage |

## 7. Open decisions

**Needing Liam's sign-off:**
- **O1. The interim quota shelve (§3.4)**, as a temporary stand-in for D5's deferral until AU-4. A claimed bead whose guard gate gives a `Deadline` is journaled `quota`, labelled `v2:parked` with no blockers, and finished `PARKED/QUOTA`. Plan 3's resumable path resumes it, gated, with a wake time. It uses `v2:parked` instead of `v2:deferred` and writes no `wsd-defer` comment. The alternative is escalation, like AU-3's interim `ACCOUNT_CHANGED`, which needs an operator release for a condition that clears by itself. Either way, it can't happen in production before AU-7.
- **O4. The plan amendment in §8:** the `account_changed` lifecycle acceptance bullet moves from AU-5 to AU-4 as a whole.

**Accepted in review r1, kept:**
- **O3. Window identity and `kind`.** `window_id` is the producer's stable window identity (`codex:300m`, `five_hour`), never the Codex slot name, because the probe shows `primary` can be the 7-day window. `kind` is a display label. "A payload naming another account" is implemented as a producer-derived `claimed_key`. If AU-7 finds no payload field to derive it from, the check is inert, and attribution by channel is the only defence, as D3 intends.
- **O5. The gate ignores untrusted rows entirely.** This reads D4 literally: only trusted windows and marks block. Untrusted rows act only through AU-4's and AU-7's own-bead deferral.
- **O7. `WsState.DEFERRED` in AU-5**, because §5.2's "not idle" rule is pickup's. AU-9 only renders it.

**Settled in r2:**
- **O2** is dropped: times are epoch seconds, as D4 says (§2.2).
- **O6** is narrowed: `AccountChanged` goes to the guard's escalation only for claimed resumes. Ready work is skipped without a claim (§3.6).

## 8. Plan amendment (O4; needs Liam's sign-off)

**Change to the accounts plan** (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md`): remove this bullet from AU-5's acceptance and add it, whole, to AU-4's:

> an `account_changed` deferral writes one comment, has no timer, survives startup and replay unchanged, and is gated again on a config reload.

**Why:** every part of it is a property of the deferral operation, its record and its comment. AU-4 builds those, and AU-4 depends on AU-5. AU-5 has no deferral writer, so it can only test the gate side, which it keeps (§6). wsd also has no configuration reload yet.

**What AU-4 must then carry as tests**, all with the injected clock and the crash-point harness:
1. **One comment.** A due quota deferral, or a resume, that gates to `account_changed` writes exactly one `wsd-defer … reason=account_changed until=none` comment and one superseding record. A crash at every step of that transition replays to one record and one comment (the comment and alert are idempotent on the deferral number).
2. **No timer.** With the clock advanced by more than `max_window_hours`, any number of backstop pickups and `QUOTA_WAKE` triggers never re-gate it: the gate's call count for that session stays 0. `wake_at` never comes from it, and the pickup outcome isn't `DEFERRED` because of it.
3. **Startup unchanged.** Restarting wsd (reopening the journal, then startup recovery and the startup pickup) re-gates it exactly once, as D5 requires. While the gate still gives `account_changed`, nothing is written: the same record and number, no new comment, no alert. A crash during startup's re-gate replays to the same result.
4. **Replay unchanged.** A crash at each step of the defer operation and of the re-gate replays to the same record and the same single comment.
5. **Reload re-gates.** The reload trigger re-gates every `account_changed` deferral once:
   - With the account restored, the wait is journaled as over, and the next pickup undefers it with all its checks.
   - With a deadline, it becomes a quota deferral, with the next number and one comment.
   - With `account_changed` still, nothing is written.
   - Repeated reloads with an unchanged config write nothing.

**The reload trigger AU-4 adds:** a `wsctl reload` control request, which wsd's systemd `ExecReload` would also send. It re-resolves the host and workstream settings with plan 1's loader. If they are valid, it swaps them in under every workstream's operation lock and then runs the re-gate. An invalid config is refused with its path-free error, and the running settings are kept. There is no file watcher and no timer. `Parker.release` on a deferred bead runs the same re-gate for that one bead.
