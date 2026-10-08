# btq-17a31 (AU-3): launch entries, launch receipts and the journal upgrade (design r3)

Base: main b136b30. Sources:
- the accounts plan, §AU-3 (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at 66ad195);
- ADR 0001 r14 §3.3 and §4.4 D1, D2, D4 and D7 (hermes-workstreams-v2 at 82b2e4b);
- the approved PICKUP.md bullets "Launch entries", "Launch outcomes" and "Adopted entries", plus its closing paragraph (beads-task-queue master at 72a5fa6);
- AU-2's design (`docs/superpowers/specs/2026-10-08-au2-config-design.md` on au2-config-design at 239897f) for the credential identity and key. AU-2 (btq-wnbdp) now blocks AU-3;
- spike S7 (`docs/spikes/s7-accounts-and-usage.md`).

Scope: plan 3b's launch entries only. Design only: nothing is implemented here.

**r2 changes** (review r1 on 9a8d889, one change per blocker):
1. The credential key and the `~` expansion are AU-2's. The strict existence check applies only to adoption (§2.1).
2. Replay finishes the operation's own dispatch from its receipt. It never pins a new generation for it, and it restores the receipt's native ID (§4.2 step 0).
3. Release no longer writes a `launched` outcome. A dispatched entry with no receipt stays unresolved, and the bead stays held (§4.3).
4. A held adoption has a journaled exit: release re-verifies, and the original evidence is kept (§3.3).
5. Startup escalation hands off the open operation with `escalate_from`, or replays an existing escalation. `settled` commits with it (§3.2).
6. A missing record is no longer proof that nothing launched. Only beads proven never to have launched are exempt (§3, step 2).
7. Launch entries and generation sequences rebuild from `wsd_launches`. Receipts stay journal-only (§4.2 step 1).

**r3 changes** (review r2 on f054601):
9. Release keeps the upgrade-time condition "no configured accounts at the upgrade". A hold for accounts that existed at the upgrade stays held, and resolving it is out of scope (§3.3).
10. `current_key` and `login_resolves` resolve freshly from the configured or default path, with the supplied environment. They never rehash a stored canonical `Account` (§2.1).
11. Release verifies and resolves an adoption before plan 3's parked/unparked branch, so a parked bead is covered too (§3.3).

Item 8 (non-blocking) is kept: the interim `account_changed` escalation must be replaced by AU-4's deferral (§4.2 step 2).

**Where the sources differ:**
- The plan's AU-3 "Dispatch" bullet reconciles from runtime evidence (tmux tags, hook-spool events, transcripts). ADR r14 replaced that after the G1 r1 review: reconciliation uses **wsd's own launch receipt only**. PICKUP.md says the same. This design follows the ADR and PICKUP.md, and no runtime evidence ever sets an outcome.
- The plan has adoption write its journal entries "in the same transaction" as the upgrade. The session key and profile live only on the bead, so the upgrade reads beads first, read-only, and then commits everything in one SQLite transaction (§3).

**Out of scope** (later items):
- configured accounts (AU-2);
- the usage cache and the real gate (AU-5);
- deferrals (AU-4);
- account binding, the freshness lock and handoff relaunches with their own native IDs (AU-6, AU-8);
- the real runtime (plan 4);
- a procedure that resolves a dispatched entry with no receipt (§4.3);
- a procedure that verifies the account history of a legacy session held because accounts were configured at the upgrade (§3.3).

AU-3 creates the tables those items need. Until they land, every launch uses account `default`.

## 1. What the sources fix (implemented exactly)

- **Session key.** It stays `ids.role_session(bead, role, profile)`, and no account is an input. `metadata.wsd_session`, `SessionRecord` and `ensure_record` are unchanged.
- **Entries.** Every launch gets one entry: first launch, resume, relaunch, failover. The entry is keyed (session key, generation), and generations count per session key from 1.
  - An entry is journaled first, then appended to `metadata.wsd_launches` with read-back, and only then launched.
  - An entry is never removed or changed. The only exception: each empty field among the dispatch mark, the native ID, the reported model and the outcome may be set once, also with read-back.
  - Entries and sequences rebuild from `metadata.wsd_launches` alone.
- **The account is pinned at the entry.** Replay launches the journaled account and model.
  - Immediately before the launch, the guard re-checks two things: the account's current credential key equals the journaled one, and the account is still eligible.
  - If either check fails, the outcome is `abandoned` and the next generation chooses again. A generation is never reused.
- **Outcomes.**
  - No dispatch mark: the entry may be abandoned.
  - Dispatched: the outcome comes only from wsd's launch receipt. `started` sets `launched`, and `refused` sets `abandoned`.
  - Dispatched with no receipt: the bead holds, escalated `unexpected_state`. This happens before any later launch of that session is gated.
  - Tags, hooks and transcripts are never outcome evidence.
- **Adoption.** A legacy session is adopted only when all three of these hold:
  - its adapter had no configured accounts at the upgrade;
  - its default login's credential identity resolves;
  - journal and bead agree.

  Every other case holds, escalated `unexpected_state`, and is never resumed on a guessed account. A journaled release (ADR §4.3) is the only exit from the hold.
- **Metadata keys.** wsd writes no metadata keys other than `wsd_session` and `wsd_launches`, and never deletes a launch entry.

## 2. Data shapes

### 2.1 Credential key (D1): AU-2's definition

AU-3 doesn't define a credential identity or key of its own. It uses AU-2's §"Credential identity" (`config/accounts.py`):
- **Expansion:** `~` is expanded with AU-2's `paths.expand(value, env)`, from the supplied environment's `HOME`, for named accounts and the implicit `default` alike.
- **The implicit `default`** is at `DEFAULT_LOGIN_DIRS[adapter]`, which is `~/.claude` or `~/.codex`. `CLAUDE_CONFIG_DIR` and `CODEX_HOME` never move it.
- **Identity:** the adapter plus its canonical login files, `Path(login_dir, f).resolve(strict=False)` for each `f` in `LOGIN_FILES[adapter]`. From S7, those are `.credentials.json` for claude-code and `auth.json` for codex.
- **Key:** AU-2's `"ck1-" + sha256("\0".join([adapter, *login_files])).hexdigest()[:32]`. It is never 64 hex characters, so it never trips the redaction.
- **The key depends on paths only.** A token refresh keeps the key. Repointing a login, for example by re-symlinking `.credentials.json`, changes it. That is the "credential replacement" in the acceptance list.

AU-3 adds two things on top:
- **`current_key(adapter, account)`** resolves the login afresh at every call, then hashes it with AU-2's single `credential_key(adapter, login_files)`.
  - **Where it starts.** It starts from the account's **configured** path:
    - a named account: the raw `login_dir` string from the loaded config;
    - `default`: `DEFAULT_LOGIN_DIRS[adapter]`.
  - **How it resolves.** It expands that path with `paths.expand(value, env)` and the supplied environment, then computes `Path(dir, f).resolve(strict=False)` for each `f` in `LOGIN_FILES[adapter]`.
  - **It never rehashes the canonical `login_dir` or `login_files` stored in AU-2's `Account`.** Those were resolved at config load. Once `~/.codex` resolved to one login, repointing it to another would be invisible through them. Fresh resolution sees both a repointed directory symlink and a repointed file symlink, without a reload. This is D2's check before launch.
  - AU-2 keeps the raw configured value next to the canonical one, for example as `Account.configured_dir`. This design asks AU-2 for that field.
  - Like AU-2, it accepts an absent login file. Launch-time refusals for a missing login belong to the §7 freshness gate (AU-6).
- **`login_resolves(adapter, account)`** is used **only for adoption** (D2: "that login's credential identity resolves"). It resolves freshly from the same configured path. Every login file must `resolve(strict=True)` to a regular file, because adoption needs the login the session actually ran on.

**`Accounts` interface.** The guard reads accounts only through this, so tests can change eligibility and logins between steps:

```python
class Accounts(Protocol):
    def configured(self, adapter: str) -> tuple[str, ...]     # named accounts, `default` excluded
    def current_key(self, adapter: str, account: str) -> str  # AU-2's key, recomputed now
    def login_resolves(self, adapter: str, account: str) -> bool   # adoption only (strict)
    def eligible(self, profile: str, account: str) -> bool    # still permitted for the profile
    def choose(self, profile: str, previous_key: str | None) -> Chosen | AccountChanged
```

AU-3 ships `DefaultOnly`, built on AU-2's `resolve_accounts(merged, env)`:
- `configured` returns the adapter's named accounts. That is `()` until AU-6 enables login binding.
- `eligible` is true for `default`.
- `choose` returns `Chosen("default", key)`, or `AccountChanged` when `previous_key` is set and differs from the default login's current key.
  - A changed key is a switch (D7). A switch is allowed only with `CAPABILITIES[adapter].can_switch` (AU-2), which is false while that table is empty. Without it, a switch is `account_changed`, never a fresh first launch.

AU-5 replaces `choose` with its `gate`, and the guard's call site stays the same.

### 2.2 The `launches` table and the bead copy

```sql
CREATE TABLE launches (
    session_key TEXT NOT NULL, generation INTEGER NOT NULL CHECK (generation >= 1),
    ws TEXT NOT NULL, bead TEXT NOT NULL, role TEXT NOT NULL, profile TEXT NOT NULL,
    account TEXT NOT NULL, credential_key TEXT NOT NULL, model_passed TEXT NOT NULL,
    dispatched_at TEXT, native_id TEXT, model_reported TEXT,
    outcome TEXT CHECK (outcome IN ('launched', 'abandoned')),
    adopted INTEGER NOT NULL DEFAULT 0 CHECK (adopted IN (0, 1)),
    journaled_at TEXT NOT NULL,
    rebuilt_at TEXT,
    PRIMARY KEY (session_key, generation));
```

- The plan's columns are kept, plus two more:
  - `ws`, so recovery can scope by workstream;
  - `rebuilt_at`, journal-only and never on the bead. It is set when the row was rebuilt from the bead copy (§4.2 step 1), not journaled by this wsd.
- `model_passed` is the profile's `model` at the entry, or `""` when none is passed.
- The four nullable fields among the plan's columns are the set-once fields. `Journal.launch_set(key, gen, field, value)` runs `UPDATE … WHERE <field> IS NULL`.
  - It raises `EntryConflict` if the field already holds a different value.
  - Setting the same value again is a no-op, so replay is safe.
  - Nothing else updates or deletes a `launches` row.

**`LaunchEntry` (msgspec struct, in `wsd/launches.py`).**
- Its fields are the table's columns except `rebuilt_at`.
- It is encoded with `order="sorted"`.
- In the bead copy, the set-once fields are omitted while empty, not written as null.

`metadata.wsd_launches` holds one JSON array of entries for the bead. It is one key per bead and covers every session key on that bead, for example a reviewer's P and Q sessions.

### 2.3 The `receipts` table

```sql
CREATE TABLE receipts (
    session_key TEXT NOT NULL, generation INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('started', 'refused')),
    refusal TEXT CHECK (refusal IN ('failed', 'unavailable')),
    tmux_session TEXT, tmux_pane TEXT, pane_pid INTEGER, native_id TEXT, error TEXT,
    at TEXT NOT NULL,
    PRIMARY KEY (session_key, generation),
    FOREIGN KEY (session_key, generation) REFERENCES launches (session_key, generation),
    CHECK ((kind = 'refused') = (refusal IS NOT NULL)));
```

- A receipt is written only by the guard, from the return of `AgentRuntime.launch` (§4.2 step 5).
- `refusal` records which tail a refused launch takes:
  - `failed`: the runtime raised `LaunchFailed`;
  - `unavailable`: it raised `RuntimeUnavailable`.

  A replay then takes the same tail.
- It is journal-only. It never goes on the bead, and nothing rebuilds it, because a session can write bead metadata and so a bead copy would be forgeable.
- This is a different thing from the plan's "receipt-sequence counter". That counter orders AU-5's *usage* receipts (D3). It is created here as `meta('usage_receipt_seq', '0')` and is unused until AU-5.

### 2.4 Runtime changes (`runtime.py`)

- `LaunchSpec` gains `generation: int` and `native_id: str | None`.
  - For a first launch of Claude, `native_id` is the session key (§4.1).
  - For a resume, it is the native ID of the latest `launched` entry.
  - For Codex before its thread ID is known, it is None.
  - The runtime tags the launch with (session key, generation): tmux options, the hook-spool launch tag, and Claude's native ID.
- `AgentRuntime.launch(spec) -> Started`, where `Started(tmux_session, tmux_pane, pane_pid, native_id: str | None)` describes what the runtime created on wsd's own tmux server.
- `LaunchFailed` and `RuntimeUnavailable` keep their meaning: nothing was started.
- Any other exception, `LaunchUncertain` included, means the runtime can't say.
- `NoRuntime` and the fake runtime are updated to match. The fake returns a `Started` with synthetic IDs, and gains `raise_after_start` for the crash tests. It also counts calls, so tests can assert "not dispatched again".

### 2.5 The `adoptions` table and AU-4/AU-5 tables

```sql
CREATE TABLE adoptions (
    ws TEXT NOT NULL, bead TEXT NOT NULL, session_key TEXT,
    verdict TEXT NOT NULL CHECK (verdict IN ('adopted', 'held')), detail TEXT NOT NULL,
    facts TEXT NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0 CHECK (settled IN (0, 1)),
    resolution TEXT CHECK (resolution IN ('adopted')), resolved_at TEXT, resolved_by TEXT,
    PRIMARY KEY (ws, bead));
```

- There is one row per legacy bead, written by the upgrade. Its fields fall into four groups:
  - **The original evidence**, never changed: `verdict`, `detail` (the failed condition), and `facts`, which holds the upgrade-time facts as JSON (configured accounts per adapter, whether the default login resolved, the claim view, the open op, the record or its absence).
  - **`settled`**, set once: startup has either put the adopted entry on the bead (read back), or opened the escalation.
  - **The `resolution` group, each field set once**, only on a `held` row: a release that re-verified the adoption sets them (§3.3). `resolved_by` is the release op's ID.
  - **A held row with no `resolution`** blocks every launch of the bead.

The upgrade also creates, exactly as specified in the plan (AU-4, AU-5) and unused here:
- `deferrals(session_key, number, bead, role, profile, reason, defer_until, trust)`, PK (session_key, number);
- `account_usage(…)`, PK (credential_key, window_id);
- `account_usage_untrusted(…)`, PK (session_key, generation, window_id);
- `account_exhausted(…)`, PK credential_key.

## 3. The journal upgrade (v1 → v2)

`journal.py`:
- `V1_SCHEMA` is a frozen copy of today's `SCHEMA` text.
- `SCHEMA = V1_SCHEMA + V2_TABLES`.
- `SCHEMA_VERSION = "2"`.
- `EXPECTED_V1` is computed like `EXPECTED_SCHEMA`.

**Opening (`_check`).**
- **Integrity check fails:** `JournalCorrupt`, as today.
- **Schema is exactly v2 and version `"2"`:** it opens.
- **Schema is exactly v1 and version `"1"`:** `JournalNeedsUpgrade`. Nothing is written: `_validate` is read-only, as today.
- **Anything else:** `JournalCorrupt`. That covers a v2 schema with version 1, a v1 schema with version 2, an unknown version, or extra or missing tables. A file is never recreated.
- **Missing file:** it is created directly at v2 (`_create`, unchanged).

**`upgrade.run(settings, beads, accounts, path)`** (new `wsd/upgrade.py`). `cli.run` calls it when `Journal()` raises `JournalNeedsUpgrade`, under the instance lock and before `assemble`. It has three steps.

1. **Gather (read-only, no journal write).**
   - Open the v1 file read-only.
   - Take the legacy beads: every `beads` row whose state is not `closed` or `dropped`. Record each one's open op, if any, with its kind and step.
   - For each legacy bead, read the bead with `beads.show`: the record (or that it is missing or unreadable), `wsd_launches`, and the claim view.
   - Per adapter, record `accounts.configured(adapter)`, `accounts.login_resolves(adapter, "default")` and `accounts.current_key(adapter, "default")`.
   - Any `BeadsUnavailable`, `UnexpectedShape` or `OSError` aborts the upgrade. The file stays untouched and is still v1. wsd exits 1 with "beads unreachable during the journal upgrade; retrying is safe", and systemd's restart retries it.
2. **Decide (pure function `adopt(legacy, facts) -> list[Verdict]`).**
   - **Exempt (no row): a bead proven never to have launched.** Plan 3 runs the launch guard only after a pickup's `worktree` step, so a bead is proven never to have launched only when both hold:
     - its journal row is `claiming` or `starting`;
     - its open op is a pickup at step `intent`, `claimed` or `placed`.

     That pickup carries on normally and launches generation 1.
   - **Every other legacy bead gets a row,** a missing or unreadable record included. A record can be lost after a launch, which is plan 3's `LAUNCH_UNRECORDED`. It is `adopted` only if all of these hold:
     - **(a)** its record parses;
     - **(b)** the record's `session_key == role_session(bead, role, profile)`;
     - **(c)** the profile still exists, and its adapter has `configured == ()`;
     - **(d)** `login_resolves(adapter, "default")`;
     - **(e)** journal and bead agree:
       - the claim view is `OURS`;
       - no op is open for the bead (an open pickup past `placed`, or a resume, has an unknown launch state);
       - the bead's `wsd_launches` is absent, empty, or exactly this adopted entry.

   An adopted entry has:
   - generation 1, account `default`, and the adapter's current default key;
   - `model_passed = ""`;
   - `native_id`:
     - Claude: the session key, which plan 3 used as its native ID;
     - Codex: the record's thread ID if plan 4's field is present, else empty, to be set once later;
   - `outcome = 'launched'`, `adopted = 1`;
   - `dispatched_at = journaled_at` = the upgrade time.

   Anything else is `held`, with the failed condition as its detail. Examples: "accounts were configured for codex at the upgrade", "no session record; launch history unknown".
3. **Commit (one transaction on the v1 file).**
   - First take an online backup to `<journal>.v1.bak` (0600, the existing `backup()` path).
   - Open rw and `BEGIN IMMEDIATE`.
   - Re-run the v1 exact-schema and version check inside the transaction.
   - Run each `CREATE` from `V2_TABLES` with `db.execute`. `executescript` must not be used, because it COMMITs any pending transaction first.
   - Insert the `adoptions` rows (with `facts`), and a `launches` row for each adopted one.
   - Set `meta.schema_version = '2'` and `meta.upgraded_at`.
   - Emit a `journal_upgraded` event and `COMMIT`.
   - Any exception rolls back, so a crash leaves v1 intact. The SQLite transaction is the atomicity, and the backup is for the operator only.
   - wsd then reopens with `Journal(path)`, which must now pass the v2 check.

The facts are fixed at the upgrade, as the sources require: "no configured accounts at the upgrade". An account added later does not un-adopt a session (§6, test 9).

### 3.2 Startup: settling adoptions (recovery step 4a)

This is a new recovery step, run after step 4 has replayed the open park, release and escalate journals, and before step 5's sweep opens any resume. It runs for each unsettled `adoptions` row of the workstream.

- **`adopted`:** `beads.ensure_launch(ws, bead, entry)` (§4.1), then set `settled`. Crash points are `adopt.appended!` and `adopt.settled`.
- **`held`:** the bead is escalated `UNEXPECTED_STATE` with the row's `detail`, in one transaction that also sets `settled`. Which escalation depends on the bead's open op:
  - **an open pickup or resume:** `Parker._stuck(op, …)`, the journal half of `escalate_from`. It ends that op as STUCK and opens the escalation, so there is never a second open op (`ops_one_open`).
  - **an open escalation already:** none is opened. `settled` is set, and the existing one is replayed.
  - **no open op:** the journal half of `Parker.escalate` opens the escalation.

  After the commit, `replay_escalate(esc)` adds `needs-human`. A crash at `adopt.settled` replays the open escalation, and never opens a second one.

A failure raises `BeadsUnavailable`: recovery fails (`ok=False`) and pickup never runs, so no legacy session resumes before its entry is on the bead.

The guard checks the adoption row before step 0 of §4.2:
- **unsettled:** WAIT;
- **`held` with no `resolution`:** `escalate_from(op, UNEXPECTED_STATE, detail)` → ENDED;
- **`adopted`, or resolved:** it goes on.

### 3.3 Release of an adoption hold (the journaled exit)

`Parker.release` is the only exit, as for every hold (ADR §4.3). It handles a bead whose adoption row is `held` with no `resolution` as follows.

- **At release intent, before anything is written.** If the row's upgrade-time `facts` show accounts configured for the session's adapter, `release` raises `NotReleasable("accounts were configured for <adapter> at the upgrade")`. The bead stays held.
  - D2 and PICKUP.md make "no configured accounts **at the upgrade**" a condition of adoption. Today's configuration can't establish which login the session used then, so removing accounts later authorises nothing.
  - Resolving such a hold needs an explicitly authorised account-history verification. That is out of scope for this PoC, like a dispatched entry with no receipt (§4.3).
- **Otherwise, in `replay_release` after the `unlabelled` step, before plan 3's parked/unparked branch** (`park.py`, the `PARKED in shown.labels` early return):
  1. **Recover the record if needed:** `_record_for_release(shown)`, plan 3's operator-only path. It recreates a missing or unreadable record from the current placement once every session under another key is confirmed stopped. It now runs for parked beads too, but only when an adoption is unresolved. A parked bead normally has no session, so the stop step is a no-op for it.
  2. **Re-verify** conditions (a), (b), (d) and (e) of §3, step 2, with today's facts.
     - (c) is taken from the stored upgrade-time `facts`, never re-evaluated. It passed, or the release would have been refused at intent.
     - (d) is a fresh `login_resolves`.
     - (e)'s "no op open" excludes the release op itself. Its claim and `wsd_launches` checks are read now.
  3. **Pass.** One transaction does three things:
     - writes the adopted entry: generation 1, `default`, a fresh `current_key`, `adopted = 1`, `outcome = 'launched'`;
     - sets the `resolution` fields;
     - emits `adoption_resolved` with the release op's ID.

     cp `release.adopted`. Then `ensure_launch`, cp `release.adopted!`.
  4. **Fail.** `escalate_from(release_op, UNEXPECTED_STATE, "adoption still unverified: <condition>")`. The bead is held again. The row's original `verdict`, `detail` and `facts` are kept, and the failed re-check goes into the escalation's event.
  5. **Then plan 3's branch runs unchanged.** A parked bead keeps `v2:parked` and its blockers, and goes back to waiting (PARKED, BLOCKED_ON_BEAD or WAITING_INPUT). Its later parked resume launches generation 2 through the guard. An unparked bead goes on to its resume.
- **Replay.** A crash at `release.adopted` or `release.adopted!` replays the release from `unlabelled`. It finds the row resolved, re-runs `ensure_launch` (one entry), and takes the branch.
- Unclaiming the bead is not a recovery mechanism, and does nothing to the row.

## 4. The launch guard with entries (`park.py`)

The plan 3 order is unchanged up to `verify_worktree` and the listed-but-not-live check. The `if not own:` launch block becomes the steps below. Each journal write is followed by a checkpoint `<kind>.<step>`, and each bead write by `<kind>.<step>!` (plan 3's convention).

### 4.1 Bead copy: `BeadsAdapter.ensure_launch(ws, bead, entry)`

- It runs under `_owned` (the per-bead worker's lock), like `ensure_record`.
- It reads `wsd_launches`:
  - absent means `[]`;
  - a value that doesn't parse as a list of `LaunchEntry` raises `LaunchesUnreadable`.
- It finds the entry with the same (session key, generation):
  - **Absent:** append, then write the whole array with `bd update --set-metadata wsd_launches=<json>`.
  - **Present, every field equal:** nothing.
  - **Present, and every difference is a set-once field that is empty on the bead and set in `entry`:** write the array with those fields set.
  - **Anything else:** `LaunchConflict`. That includes a field set on the bead but empty or different in the journal row.
- It reads back and requires the stored entry to equal `entry`. Otherwise it raises `BeadsUnavailable("launch entry did not read back")`.
- The guard maps `LaunchesUnreadable` and `LaunchConflict` to `escalate_from(op, UNEXPECTED_STATE, …)`.
- The other entries in the array are rewritten byte-for-byte as read, so no entry is ever removed or changed.

### 4.2 Steps

0. **Finish the operation's own dispatch.** This runs when `op.data` has a `generation` whose entry is dispatched. That generation belongs to this operation, so this step completes it and never pins another.
   - **A `started` receipt:**
     1. Set the outcome `launched` and the receipt's `native_id` if it has one: journal (cp `<kind>.outcome`), then bead (cp `<kind>.outcome!`).
     2. Take the DONE tail: `_finish(DONE, RUNNING)`, cp `<kind>.done`.
     3. Return STARTED.

     There is **no second dispatch**, whether the session is still running or has already ended. An ended session is plan 3's: the sweep sees it and opens a resume later, which is a new operation and a new generation.
   - **A `refused` receipt:**
     1. Set the outcome `abandoned`: journal, then bead.
     2. Take the receipt's tail. `failed`: `_spend` and FAILED. `unavailable`: hold `RUNTIME_UNAVAILABLE` and WAIT.
     3. In the same transaction as the tail's journal write, drop `generation` from `op.data`. The next attempt then pins a new generation. A crash before that point replays this step and takes the same tail once.
   - **No receipt:** `escalate_from(op, UNEXPECTED_STATE, "generation N was dispatched with no launch receipt")` → ENDED.
1. **Rebuild and reconcile** (`reconcile(key, shown)`) before anything chooses. It runs over the journal's entries for `rec.session_key` and the bead's copies.
   - **Rebuild** (ADR §3.3, D2). Any bead entry the journal lacks is inserted as-is into `launches`, with `rebuilt_at` set, in one transaction. cp `<kind>.rebuilt`. This happens after a lost or restored journal, when plan 3's `JOURNAL_LOST` hold was released.
     - No receipt is ever created from the bead.
     - A rebuilt entry with an outcome keeps it.
     - A rebuilt entry that is dispatched with no outcome has no receipt, so it holds (below).
     - Generation sequences, P's and Q's included, come out of this rebuild exactly as the bead records them.
   - **Dispatched, no outcome, not this op's own generation:**
     - `started` sets `launched` and restores the receipt's `native_id`;
     - `refused` sets `abandoned`;
     - no receipt: `escalate_from(op, UNEXPECTED_STATE, "generation N was dispatched with no launch receipt")` → ENDED. This is never on a timer.

     Each write goes to the journal first (cp `<kind>.reconciled`), then the bead (cp `<kind>.reconciled!`).
   - **No dispatch mark and no outcome:** if this is the op's pinned entry, keep it for step 3. Otherwise set `abandoned`, because it never reached the runtime.
   - **An outcome or native ID in the journal but not on the bead:** copy it to the bead.
2. **Pin** (only when `op.data` has no `generation`):
   - `previous_key` is the credential key of the highest-generation `launched` entry for the key, or None.
   - `accounts.choose(rec.profile, previous_key)` gives one of two results:
     - **`AccountChanged`:** `escalate_from(op, ACCOUNT_CHANGED, detail)` → ENDED. `Reason.ACCOUNT_CHANGED` is new. This is an interim safety stop: **AU-4 must replace it** with D5's `account_changed` deferral, re-gated at startup, on reload and on release, and never on a timer.
     - **`Chosen(account, key)`:** `generation` = 1 + the highest generation the journal has for the key, rebuilt rows included, so a generation is never reused.
   - In one transaction, insert the entry and run `op_step(op, "entry", {…, "generation": n})`. cp `<kind>.entry`.
   - Then `ensure_launch`. cp `<kind>.entry!`.
   - On replay with an undispatched `generation` in `op.data`, the journal row already exists. `ensure_launch` then keeps an identical bead entry, writes a missing one, and escalates a different one.
3. **Check the pin, immediately before dispatch:**
   - `accounts.current_key(adapter, entry.account) == entry.credential_key`;
   - `accounts.eligible(rec.profile, entry.account)`.

   On failure:
   - set the outcome `abandoned`: journal (cp `<kind>.abandoned`), then bead (cp `<kind>.abandoned!`);
   - drop `generation` from the op data, in the same transaction as the journal write;
   - go back to step 2 once. A second failure in the same call returns WAIT, and the next pickup tries again.

   With `DefaultOnly`, a session that has launched before reaches `AccountChanged` on the re-pin. A first launch gets generation n+1 on the new key.
4. **Dispatch.**
   - Journal `dispatched_at` and run `op_step(op, "dispatched")`. cp `<kind>.dispatched`.
   - Set it on the bead copy. cp `<kind>.dispatched!`.
   - Build `LaunchSpec(…, generation=n, native_id=…)` and call `runtime.launch`.
5. **Receipt, before anything else is done with the result.** Exactly one of these:
   - **Returns `Started`:** cp `<kind>.launched!` (plan 3's point after the external effect), then journal a `started` receipt. cp `<kind>.receipt`.
   - **Raises `LaunchFailed`:** journal a `refused` receipt with `refusal = 'failed'` and `str(exc)`. cp `<kind>.receipt`.
   - **Raises `RuntimeUnavailable`:** the same, with `refusal = 'unavailable'`.
   - **Raises anything else:** no receipt. Escalate `UNEXPECTED_STATE` ("the runtime could not say whether generation N started") → ENDED.

   This replaces plan 3's `_uncertain` (`LAUNCH_UNCERTAIN`, settled later from the session list) on this path, because the session list is runtime evidence and can't set an outcome. `_uncertain` stays only for the pre-dispatch "listed but not confirmed live" check. That check decides whether to launch at all, never an outcome.
6. **Outcome.** Continue exactly as step 0 does with the receipt just written. Step 0 is the single code path for "a dispatched generation of this op has a receipt", for both a live run and a replay.

**Own session already listed live** (plan 3's `Launch.LIVE` path, with no launch call). The pinned entry, if undispatched, is set `abandoned` (it never reached the runtime) before `_finish`. A dispatched one was finished in step 0.

**Model.** `model_passed` is journaled at the pin, and replay passes the journaled value, never a re-read profile. `model_reported` stays empty until plan 4 sets it once.

### 4.3 A dispatched entry with no receipt stays unresolved

- No AU-3 code path sets its outcome: not release, not a timer, not runtime evidence.
- While a bead has such an entry:
  - the gate is never called for its session;
  - every launch of it escalates in step 0 or step 1;
  - `Parker.release` refuses it at intent with `NotReleasable("generation N has no launch receipt")`, and the bead stays held.
- The escalation event lists the untrusted observations (tagged tmux session, spool events, transcript), for the operator's information only.
- Resolving such an entry needs a recovery procedure under separate, operator-approved authority. Neither r14 nor PICKUP.md defines one, so it needs its own amendment. Until then the bead stays held, and the operator's only options are outside wsd: close the bead, or leave it held.
- This is the ADR's stated cost of the window between the dispatch mark and the receipt.

## 5. Failure modes

| Case | Result |
|---|---|
| Crash during the upgrade | rollback; v1 intact; next start re-gathers |
| Beads down during the upgrade | file untouched; exit 1; retried |
| v1 schema with any extra or changed object | `JournalCorrupt`, never upgraded |
| Legacy session, accounts configured for its adapter at upgrade | `held` → STUCK `unexpected_state` at startup; release refused, out of scope (§3.3) |
| Login directory or file symlink repointed after config load, no reload | fresh resolution gives a new key: the pin check abandons, or the gate gives `account_changed` |
| Legacy session, default login doesn't resolve, record missing, or journal and bead disagree | `held` → STUCK `unexpected_state` at startup; release re-verifies (§3.3) |
| Legacy bead with an open pickup or resume | `held`; startup hands that op off to the escalation (§3.2) |
| Crash between journal entry and bead entry | replay re-runs `ensure_launch`: one entry |
| Bead entry differs from the journal's under one generation | `unexpected_state` |
| Login repointed or account ineligible between the entry and dispatch | generation `abandoned`; re-pin (first launch: next generation; otherwise `account_changed`) |
| Crash after dispatch mark, before launch | no receipt → held `unexpected_state`, unresolved (§4.3) |
| Crash after launch, before receipt (`launched!`) | same |
| Crash after receipt, before outcome (journal or bead) or the DONE tail | step 0 finishes the same op from the receipt; no second dispatch |
| Runtime raises `LaunchUncertain` or anything unexpected | no receipt → held `unexpected_state` at once |
| Lost or restored journal, bead shows entries | rebuilt into `launches`; dispatched ones with no outcome hold |
| A session forges hook events, transcripts or tmux tags | never read as an outcome |
| A session edits `wsd_launches` | a field changed under a journaled generation is a `LaunchConflict` → held. An entry added when the journal lacks it is rebuilt as recorded (accepted: the bead is the ADR's recovery source). It can only skip generations, or make the next gate see a different previous key, which gives `account_changed`, never a silent switch |

**S7 signals.** Claude's `StopFailure` (`rate_limit`) and its auto-continue at the reset are hook and terminal observations. They are untrusted and never set or change a launch outcome. A session that stops on a limit and continues by itself is still the same `launched` generation. Turn-end and limit handling belong to AU-4 and AU-7.

## 6. Tests

New files: `tests/test_wsd_launches.py` and `tests/test_wsd_journal_upgrade.py`. They extend `test_wsd_park.py` and `test_wsd_recovery.py`. Crash tests use the existing checkpoint fakes (`tests/fakes/checkpoints.py`): crash at a point, reopen everything, replay, then assert.

1. **Key independence.** `role_session(b, r, p)` is identical under two `Accounts` fakes with different accounts and keys. `wsd_session` is byte-identical after launches on both. The key equals AU-2's `credential_key` for the same files, with `HOME` from the supplied env.
2. **Append crash, one entry.** For each point `entry`, `entry!`, `dispatched`, `dispatched!`, `outcome` and `outcome!`, crash then replay. The bead has exactly one entry per generation, and earlier entries are byte-identical. A hand-edited conflicting bead entry escalates `unexpected_state`.
3. **Pinned account.** Crash at `entry!`. Then, before replay and with no config reload, change one thing:
   - repoint the default login's **directory** symlink (`~/.codex` from one scratch login dir to another);
   - or repoint its **file** symlink (`.credentials.json`);
   - or make the account ineligible.

   Each repoint gives a new `current_key`. The replay never launches another key under that generation: it gets `abandoned` then generation 2 on the new key for a first launch, or `account_changed` when a launched entry exists. A third case, nothing changed, launches the pinned account and model exactly.
4. **Upgrade keeps rows.** Build a populated v1 journal from the frozen `V1_SCHEMA`, with rows in every table, then upgrade. Every v1 row is equal, the version is `"2"`, and the v2 check passes.
5. **Upgrade crash.** Inject a failure after each `CREATE` and after the inserts. The file still passes the v1 check, and its rows are unchanged. Non-v1/v2 shapes give `JournalCorrupt` with no write, for example a v1 schema with version `"2"`, or one extra table.
6. **P→Q→P and rebuild.** A reviewer's session keys for P and Q get generations P1, Q1, P2. Then replace the journal with a fresh one (no `launches` rows), release the `JOURNAL_LOST` hold, and run the guard. It rebuilds P1, Q1 and P2 from `wsd_launches` with `rebuilt_at` set, creates no receipts, and pins P3. A rebuilt dispatched entry with no outcome holds `unexpected_state`.
7. **Crash after launch, one dispatch.** Crash at `receipt`, `outcome`, `outcome!` and just before `done`, with the fake session live and with it ended. Each replay finishes the same op: `launched`, the receipt's native ID on the entry and the bead, op DONE. The fake's launch count stays 1. Then repoint the default login: the next resume gets `account_changed`, never a first launch. A `refused`/`failed` receipt replays to one `_spend`, and `refused`/`unavailable` replays to WAIT.
8. **No receipt holds.** Crashes at `dispatched`, `dispatched!` and `launched!`, plus a runtime raising `LaunchUncertain`: the bead is STUCK `unexpected_state`, and neither gate nor runtime was called again. This holds with the fake listing a live tagged session, a spool event and a transcript for that generation. `Parker.release` refuses it, and the entry stays unresolved.
9. **Adoption.** Upgrade a v1 journal with legacy Claude and Codex sessions and no accounts. Each gets generation 1 `adopted` `launched`, which is on the bead before the first pickup. The next resume is generation 2 with the adopted native ID. Then add, reorder or repoint accounts (fakes). In each case:
   - the session stays on the adopted key;
   - or, if that key no longer matches, it gets `account_changed`;
   - it is never un-adopted.

   A startup crash at `adopt.appended!` and at `adopt.settled` replays to one bead entry.
10. **Adoption held.** With accounts configured for codex at the upgrade, the codex session is held and the claude one is adopted. Also held:
    - a default login that doesn't resolve (strict);
    - a key mismatch;
    - a non-`OURS` claim;
    - a missing record on a `running` row;
    - an open resume or post-`placed` pickup.

    A pickup at `placed` with no record is exempt and launches generation 1. No held case launches.
11. **Startup handoff.** For a held bead with an open resume, step 4a ends the resume STUCK and opens one escalation, with `settled` in the same transaction: no `OpConflict`. Crash at `adopt.settled`: the replay finds the open escalation and opens no second one. A held bead that already has an open escalation is settled without opening another.
12. **Adoption release.**
    - **Held because codex accounts were configured at the upgrade:** release is refused with `NotReleasable`, both with the accounts still configured and after they are removed and the config reloaded. The row is unchanged, the bead stays held, and nothing launches.
    - **Held because the default login didn't resolve:** after the login is restored, release adopts. That gives one entry, `resolution` set and `resolved_by` = the release op. The resume launches generation 2. If the login still doesn't resolve, release re-escalates, and `verdict`, `detail` and `facts` are unchanged.
    - **A missing-record hold:** release recreates the record, then adopts.
    - **A parked adoption hold** (`v2:parked` with an open blocker): release resolves the adoption, and the bead ends PARKED with its blocker and label kept. When the blocker closes, the parked resume launches generation 2. With a crash at `release.adopted` and at `release.adopted!`, the replay gives one entry, the same PARKED end state, and no launch.
    - A crash at `release.adopted` and at `release.adopted!` for an unparked bead replays to one entry and one resume.
13. **Plan 3 unchanged.** `test_ensure_record_never_rewrites_an_existing_record` passes unchanged. The existing park, pickup and recovery suites pass with the fake runtime returning `Started`. Tests asserting `LAUNCH_UNCERTAIN` after a raised launch now assert STUCK `unexpected_state`, and are listed in the PR.

Install-agnostic: test homes are `tmp_path` scratch dirs passed as the env's `HOME`, and the keys are AU-2's `ck1-` 32-hex form.
