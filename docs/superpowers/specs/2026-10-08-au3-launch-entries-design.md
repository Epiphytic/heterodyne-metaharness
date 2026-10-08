# btq-17a31 (AU-3): launch entries, launch receipts and the journal upgrade (design r1)

Base: main b136b30. Sources:
- the accounts plan, §AU-3 (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at 66ad195);
- ADR 0001 r14 §4.4 D1, D2, D4 and D7 (hermes-workstreams-v2 at 82b2e4b);
- the approved PICKUP.md bullets "Launch entries", "Launch outcomes" and "Adopted entries", plus its closing paragraph (beads-task-queue master at 72a5fa6);
- spike S7 (`docs/spikes/s7-accounts-and-usage.md`).

Scope: plan 3b's launch entries only. Design only: nothing is implemented here.

**Where the sources differ:**
- The plan's AU-3 "Dispatch" bullet reconciles from runtime evidence (tmux tags, hook-spool events, transcripts). ADR r14 replaced that after the G1 r1 review: reconciliation uses **wsd's own launch receipt only**. PICKUP.md says the same. This design follows the ADR and PICKUP.md, and no runtime evidence ever sets an outcome.
- The plan has adoption write its journal entries "in the same transaction" as the upgrade. The session key and profile live only on the bead, so the upgrade reads beads first, read-only, and then commits everything in one SQLite transaction (§3).

**Out of scope** (later items):
- configured accounts and the credential identity of a named account (AU-2);
- the usage cache and the real gate (AU-5);
- deferrals (AU-4);
- account binding, the freshness lock and handoff relaunches with their own native IDs (AU-6, AU-8);
- the real runtime (plan 4).

AU-3 creates the tables those items need. Until they land, every launch uses account `default`.

## 1. What the sources fix (implemented exactly)

- **Session key.** It stays `ids.role_session(bead, role, profile)`, and no account is an input. `metadata.wsd_session`, `SessionRecord` and `ensure_record` are unchanged.
- **Entries.** Every launch gets one entry: first launch, resume, relaunch, failover. The entry is keyed (session key, generation), and generations count per session key from 1.
  - An entry is journaled first, then appended to `metadata.wsd_launches` with read-back, and only then launched.
  - An entry is never removed or changed. The only exception: each empty field among the dispatch mark, the native ID, the reported model and the outcome may be set once, also with read-back.
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

  Every other case holds, escalated `unexpected_state`, and is never resumed.
- **Metadata keys.** wsd writes no metadata keys other than `wsd_session` and `wsd_launches`, and never deletes a launch entry.

## 2. Data shapes

### 2.1 Credential key (D1): AU-2's definition

AU-3 doesn't define a credential key of its own. It uses AU-2's (`docs/superpowers/specs/2026-10-08-au2-config-design.md` on au2-config-design at 39f2b82, §"Credential identity", still under review).
- **Identity:** the adapter plus its canonical login files, `Path(login_dir, f).resolve(strict=False)` for each `f` in `config/capabilities.LOGIN_FILES[adapter]`. From S7, those are `.credentials.json` for claude-code and `auth.json` for codex.
- **Key:** `"ck1-" + sha256("\0".join([adapter, *login_files])).hexdigest()[:32]`. It is never 64 hex characters, so it never trips the redaction.
- **The implicit `default`** lives at the fixed `~/.claude` and `~/.codex` (AU-2's open question 2). The environment never moves it.
- **The key depends on paths only.** A token refresh keeps the key. Repointing a login, for example by re-symlinking `.credentials.json`, changes it. That is the "credential replacement" in the acceptance list.

AU-3 adds two things on top:
- **`current_key(adapter, account)`** recomputes the key at launch with AU-2's function, from the `Account` that `resolve_accounts` returned for the loaded config. It is not cached from config load, because a repoint between the entry and the dispatch must be seen (D2's check before launch).
- **"The default login's credential identity resolves"** (D2 adoption) is stricter than AU-2's `strict=False` canonical form. Every login file of the implicit default must `resolve(strict=True)` to a regular file. AU-2 allows an absent login, but adoption needs the login the session actually ran on. `current_key` returns None when the login doesn't resolve this way.

**Dependency.** Using AU-2's module makes AU-3 depend on AU-2's `config/accounts.py` and `config/capabilities.py`. The plan's graph doesn't have that edge (AU-3 → G1, AU-0, plan 3). Either add AU-2 as a blocker of AU-3, or land those two modules first.

**`Accounts` interface.** The guard reads accounts only through this, so tests can change eligibility and logins between steps:

```python
class Accounts(Protocol):
    def configured(self, adapter: str) -> tuple[str, ...]     # named accounts; () until AU-2
    def current_key(self, adapter: str, account: str) -> str | None   # None: does not resolve
    def eligible(self, profile: str, account: str) -> bool    # still permitted for the profile
    def choose(self, profile: str, previous_key: str | None) -> Chosen | AccountChanged
```

AU-3 ships `DefaultOnly`:
- `configured` returns the adapter's named accounts from AU-2's `resolve_accounts`, minus `default`. That is `()` until AU-6 enables login binding;
- `eligible` is true for `default`;
- `choose` returns `Chosen("default", key)`, or `AccountChanged` when `previous_key` is set and differs from the default login's current key.
  - A changed key is a switch (D7). A switch is allowed only with `CAPABILITIES[adapter].can_switch` (AU-2), which is false while that table is empty. Without it, a switch is `account_changed`, never a fresh first launch.
  - An unresolvable default login is `AccountChanged` too, with detail "the default login does not resolve".

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
    PRIMARY KEY (session_key, generation));
```

- The plan's columns are kept, plus `ws` so recovery can scope by workstream.
- `model_passed` is the profile's `model` at the entry, or `""` when none is passed.
- The first four nullable columns are the set-once fields. `Journal.launch_set(key, gen, field, value)` runs `UPDATE … WHERE <field> IS NULL`.
  - It raises `EntryConflict` if the field already holds a different value.
  - Setting the same value again is a no-op, so replay is safe.
  - Nothing else updates or deletes a `launches` row.

**`LaunchEntry` (msgspec struct, in `wsd/launches.py`).**
- Its fields match the table's columns.
- It is encoded with `order="sorted"`.
- In the bead copy, the set-once fields are omitted while empty, not written as null.

`metadata.wsd_launches` holds one JSON array of entries for the bead. It is one key per bead and covers every session key on that bead, for example a reviewer's P and Q sessions.

### 2.3 The `receipts` table

```sql
CREATE TABLE receipts (
    session_key TEXT NOT NULL, generation INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('started', 'refused')),
    tmux_session TEXT, tmux_pane TEXT, pane_pid INTEGER, native_id TEXT, error TEXT,
    at TEXT NOT NULL,
    PRIMARY KEY (session_key, generation),
    FOREIGN KEY (session_key, generation) REFERENCES launches (session_key, generation));
```

- A receipt is written only by the guard, from the return of `AgentRuntime.launch` (§4.3).
- It is journal-only. It never goes on the bead, because a session can write bead metadata and so a bead copy would be forgeable.
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
- `NoRuntime` and the fake runtime are updated to match. The fake returns a `Started` with synthetic IDs, and gains `refuse_after_start` and `raise_after_start` knobs for the crash tests.

### 2.5 The `adoptions` table and AU-4/AU-5 tables

```sql
CREATE TABLE adoptions (
    ws TEXT NOT NULL, bead TEXT NOT NULL, session_key TEXT,
    verdict TEXT NOT NULL CHECK (verdict IN ('adopted', 'held')), detail TEXT NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0 CHECK (settled IN (0, 1)),
    PRIMARY KEY (ws, bead));
```

- There is one row per legacy bead, written only by the upgrade.
- `settled` is set once startup has put the adopted entry on the bead (read back), or once the escalation is open.

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
   - Take the legacy beads: every `beads` row whose state is not `closed` or `dropped`.
   - Record whether each has an open op.
   - For each legacy bead, read the bead with `beads.show` (record, `wsd_launches`, claim view).
   - Per adapter, record `accounts.configured(adapter)` and `accounts.current_key(adapter, "default")`.
   - Any `BeadsUnavailable`, `UnexpectedShape` or `OSError` aborts the upgrade. The file stays untouched and is still v1. wsd exits 1 with "beads unreachable during the journal upgrade; retrying is safe", and systemd's restart retries it.
2. **Decide (pure function `adopt(legacy, facts) -> list[Verdict]`).** A legacy bead that has no `wsd_session` record never launched a session, so it gets no row. Every other legacy bead is `adopted` only if all of these hold:
   - **(a)** its record parses;
   - **(b)** the record's `session_key == role_session(bead, role, profile)`;
   - **(c)** the profile still exists, and its adapter has `configured == ()`;
   - **(d)** that adapter's default key resolved;
   - **(e)** journal and bead agree:
     - the claim view is `OURS`;
     - the journal row is not `claiming`;
     - no op is open for the bead (an open pickup or resume has an unknown launch state);
     - the bead's `wsd_launches` is absent, empty, or exactly this adopted entry (a crash after an earlier upgrade attempt can't have written it, since the bead copy comes later, but the check costs nothing).

   An adopted entry has:
   - generation 1, account `default`, and the adapter's current default key;
   - `model_passed = ""`;
   - `native_id`:
     - Claude: the session key, which plan 3 used as its native ID;
     - Codex: the record's thread ID if plan 4's field is present, else empty, to be set once later;
   - `outcome = 'launched'`, `adopted = 1`;
   - `dispatched_at = journaled_at` = the upgrade time.

   Anything else is `held`, with the failed condition as its detail, for example "accounts were configured for codex at the upgrade".
3. **Commit (one transaction on the v1 file).**
   - First take an online backup to `<journal>.v1.bak` (0600, the existing `backup()` path).
   - Open rw and `BEGIN IMMEDIATE`.
   - Re-run the v1 exact-schema and version check inside the transaction.
   - Run each `CREATE` from `V2_TABLES` with `db.execute`. `executescript` must not be used, because it COMMITs any pending transaction first.
   - Insert the `adoptions` rows, and a `launches` row for each adopted one.
   - Set `meta.schema_version = '2'` and `meta.upgraded_at`.
   - Emit a `journal_upgraded` event and `COMMIT`.
   - Any exception rolls back, so a crash leaves v1 intact. The SQLite transaction is the atomicity, and the backup is for the operator only.
   - wsd then reopens with `Journal(path)`, which must now pass the v2 check.

The facts are fixed at the upgrade, as the sources require: "no configured accounts at the upgrade". An account added later does not un-adopt a session (§6, test 9).

**Startup (recovery, new step 2a, after "read beads" and before actions).** For each unsettled `adoptions` row of the workstream:
- **`adopted`:** `beads.ensure_launch(ws, bead, entry)` (§4.1). Then set `settled`.
- **`held`:** `parker.escalate(bead, UNEXPECTED_STATE, detail)`, then set `settled`. The escalation op is opened, and the journal row becomes `STUCK` in the same transaction as `settled`, so a crash replays to one escalation.

A failure raises `BeadsUnavailable`: recovery fails (`ok=False`) and pickup never runs, so no legacy session resumes before its entry is on the bead.

The guard also refuses any bead with an adoption row that is `held`, or not `settled`: `held` re-escalates `UNEXPECTED_STATE`, and unsettled is `WAIT`. That covers an operator release of an adoption hold. An unadoptable session is never resumed on a guessed account; the operator unclaims the bead instead.

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
  - **Anything else:** `LaunchConflict`. That includes a field set on the bead but empty or different in the journal, because the bead is never a source for the journal.
- It reads back and requires the stored entry to equal `entry`. Otherwise it raises `BeadsUnavailable("launch entry did not read back")`.
- The guard maps `LaunchesUnreadable` and `LaunchConflict` to `escalate_from(op, UNEXPECTED_STATE, …)`.
- The other entries in the array are rewritten byte-for-byte as read, so no entry is ever removed or changed.

### 4.2 Steps

1. **Reconcile** (`reconcile(key, shown)`) before anything chooses. It runs over the journal's entries for `rec.session_key` and the bead's copies:
   - **Dispatched, no outcome, with a receipt:** `started` sets `launched`, `refused` sets `abandoned`. Journal first, cp `<kind>.reconciled`. Then the bead, cp `<kind>.reconciled!`.
   - **Dispatched, no outcome, no receipt:** `escalate_from(op, UNEXPECTED_STATE, "generation N was dispatched with no launch receipt")` → ENDED. This is never on a timer.
   - **No dispatch mark and no outcome:** if this is the op's pinned entry, keep it for step 3. Otherwise set `abandoned`; it never reached the runtime.
   - **An outcome set in the journal but not on the bead:** copy it to the bead.
   - **A bead entry the journal lacks** (a lost or restored journal): `UNEXPECTED_STATE` → ENDED. This case is normally already held by plan 3's `JOURNAL_LOST`. It is never imported from the bead, which sessions can write.
2. **Pin** (only when `op.data` has no `generation`, or its entry has an outcome):
   - `previous_key` = the credential key of the highest-generation `launched` entry for the key, or None.
   - `accounts.choose(rec.profile, previous_key)`:
     - `AccountChanged` → `escalate_from(op, ACCOUNT_CHANGED, detail)` → ENDED. `Reason.ACCOUNT_CHANGED` is new. AU-4 replaces this escalation with its deferral.
     - `Chosen(account, key)` → `generation` = 1 + max(the journal's generations ∪ the bead copy's generations for the key). Taking both sides means a generation is never reused, even after a restore.
   - In one transaction: insert the entry, and `op_step(op, "entry", {…, "generation": n})`. cp `<kind>.entry`.
   - `ensure_launch`. cp `<kind>.entry!`.
   - On replay with `generation` already in `op.data`, the journal row exists. `ensure_launch` then keeps an identical bead entry, writes a missing one, and escalates a different one.
3. **Check the pin, immediately before dispatch:**
   - `accounts.current_key(adapter, entry.account) == entry.credential_key`;
   - `accounts.eligible(rec.profile, entry.account)`.

   On failure, set `abandoned` (cp `<kind>.abandoned`, then the bead, cp `<kind>.abandoned!`), drop `generation` from the op data in the same transaction, and go back to step 2 once. A second failure in the same call returns WAIT, and the next pickup tries again. With `DefaultOnly`, a session that has launched before reaches `AccountChanged` on the re-pin. A first launch gets generation n+1 on the new key.
4. **Dispatch.**
   - Journal `dispatched_at` and `op_step(op, "dispatched")`. cp `<kind>.dispatched`.
   - Set it on the bead copy. cp `<kind>.dispatched!`.
   - Build `LaunchSpec(…, generation=n, native_id=…)` and call `runtime.launch`.
5. **Receipt, before anything else is done with the result.** Exactly one of these:
   - **Returns `Started`:** journal the `started` receipt (cp `<kind>.launched!` sits before it, as plan 3's point after the external effect, then cp `<kind>.receipt`).
   - **Raises `LaunchFailed` or `RuntimeUnavailable`:** journal a `refused` receipt with `str(exc)`. cp `<kind>.receipt`.
   - **Raises anything else:** no receipt. Escalate `UNEXPECTED_STATE` ("the runtime could not say whether generation N started") → ENDED.

   This replaces plan 3's `_uncertain` (`LAUNCH_UNCERTAIN`, settled later from the session list) on this path. The session list is runtime evidence and can't set an outcome. `_uncertain` stays only for the pre-dispatch "listed but not confirmed live" check, which decides whether to launch at all, never an outcome.
6. **Outcome from the receipt.**
   - Journal the outcome (`launched` or `abandoned`), and the native ID from `Started.native_id` if set. cp `<kind>.outcome`.
   - Then set it on the bead. cp `<kind>.outcome!`.
   - Then plan 3's tail:
     - **`launched`:** `_finish(DONE, RUNNING)`, cp `<kind>.done`.
     - **`refused` from `LaunchFailed`:** `_spend` and FAILED, as today.
     - **`refused` from `RuntimeUnavailable`:** hold `RUNTIME_UNAVAILABLE` and WAIT, as today.

**Own session already listed live** (plan 3's `Launch.LIVE` path, no launch call). The pinned entry, if undispatched, is set `abandoned` (it never reached the runtime) before `_finish`. A dispatched one has already been reconciled in step 1.

**Model.** `model_passed` is journaled at the pin, and replay passes the journaled value, never a re-read profile. `model_reported` stays empty until plan 4 sets it once.

**Release.** Plan 3's operator release stops the bead's session first. For a bead escalated for a dispatched entry with no receipt, the journaled release then sets that entry's outcome to `launched`, in the release op's transaction, with an event naming the operator.
- This is the one non-receipt outcome. It is chosen as the conservative reading for D4: the session may have run on the pinned account, so the next gate sees that key as the previous launch and can't mistake a later launch for a first one.
- Without a release, the entry stays unresolved and the bead stays held.

## 5. Failure modes

| Case | Result |
|---|---|
| Crash during the upgrade | rollback; v1 intact; next start re-gathers |
| Beads down during the upgrade | file untouched; exit 1; retried |
| v1 schema with any extra or changed object | `JournalCorrupt`, never upgraded |
| Legacy session, accounts configured for its adapter at upgrade | `held` → STUCK `unexpected_state` at startup |
| Legacy session, default login doesn't resolve, or journal and bead disagree | same |
| Crash between journal entry and bead entry | replay re-runs `ensure_launch`: one entry |
| Bead entry differs from the journal's under one generation | `unexpected_state` |
| Login repointed or account ineligible between the entry and dispatch | generation `abandoned`; re-pin (first launch: next generation; otherwise `account_changed`) |
| Crash after dispatch mark, before launch | no receipt → held `unexpected_state` (the ADR's stated cost of the window) |
| Crash after launch, before receipt (`launched!`) | same |
| Crash after receipt, before outcome (journal or bead) | step 1 sets it from the receipt: `launched` whether the session runs or has ended |
| Runtime raises `LaunchUncertain` or anything unexpected | no receipt → held `unexpected_state` at once |
| Lost or restored journal, bead shows entries | held; never imported from the bead |
| A session forges hook events, transcripts, tmux tags or `wsd_launches` fields | none is read as an outcome; a forged bead field is a `LaunchConflict` → held |

**S7 signals.** Claude's `StopFailure` (`rate_limit`) and its auto-continue at the reset are hook and terminal observations. They are untrusted and never set or change a launch outcome. A session that stops on a limit and continues by itself is still the same `launched` generation. Turn-end and limit handling belong to AU-4 and AU-7.

## 6. Tests

New files: `tests/test_wsd_launches.py` and `tests/test_wsd_journal_upgrade.py`. They extend `test_wsd_park.py` and `test_wsd_recovery.py`. Crash tests use the existing checkpoint fakes (`tests/fakes/checkpoints.py`): crash at a point, reopen everything, replay, then assert.

1. **Key independence.** `role_session(b, r, p)` is identical under two `Accounts` fakes with different accounts and keys. `wsd_session` is byte-identical after launches on both.
2. **Append crash, one entry.** For each point `entry`, `entry!`, `dispatched`, `dispatched!`, `outcome` and `outcome!`, crash then replay. The bead has exactly one entry per generation, and earlier entries are byte-identical. A hand-edited conflicting bead entry escalates `unexpected_state`.
3. **Pinned account.** Crash at `entry!`, then before replay either repoint the default login (a symlink in a scratch HOME) or make the account ineligible. The replay never launches another key under that generation: it gets `abandoned` then generation 2 on the new key for a first launch, or `account_changed` when a launched entry exists. A third case, nothing changed, launches the pinned account and model exactly.
4. **Upgrade keeps rows.** Build a populated v1 journal from the frozen `V1_SCHEMA`, with rows in every table, then upgrade. Every v1 row is equal, the version is `"2"`, and the v2 check passes.
5. **Upgrade crash.** Inject a failure after each `CREATE` and after the inserts. The file still passes the v1 check, and its rows are unchanged. Non-v1/v2 shapes give `JournalCorrupt` with no write, for example a v1 schema with version `"2"`, or one extra table.
6. **P→Q→P.** A reviewer's session keys for P and Q get generations P1, Q1, P2. Rebuilding from `wsd_launches` alone (the pure `rebuild(entries)`) gives the same sequences and next generations. Pinning after a journal restore never reuses a generation the bead has.
7. **Crash after launch.** At `receipt` (the receipt is journaled, the outcome is not), with the fake session live and with it ended, the replay sets `launched` in both cases. Then repoint the default login. The next resume gets `account_changed`, never generation n+1 as a first launch.
8. **No receipt holds.** Crashes at `dispatched`, `dispatched!` and `launched!`, plus a runtime raising `LaunchUncertain`: the bead is STUCK `unexpected_state`, and gate and runtime were not called again. This holds with the fake listing a live tagged session, a spool event and a transcript for that generation.
9. **Adoption.** Upgrade a v1 journal with legacy Claude and Codex sessions and no accounts. Each gets generation 1 `adopted` `launched`, on the bead before the first pickup, and the next resume is generation 2 with the adopted native ID. Then add, reorder or repoint accounts (fakes):
   - the session stays on the adopted key;
   - or, if that key no longer matches, it gets `account_changed`;
   - it is never un-adopted.
   Startup crash at the step-2a points replays to one bead entry.
10. **Adoption held.** With accounts configured for codex at the upgrade, the codex session is held and the claude one is adopted. Also held: a default login that doesn't resolve, a key mismatch, a non-`OURS` claim, an open op. In each held case no launch happens, before or after an operator release.
11. **Plan 3 unchanged.** `test_ensure_record_never_rewrites_an_existing_record` passes unchanged. The existing park, pickup and recovery suites pass with the fake runtime returning `Started`. Tests asserting `LAUNCH_UNCERTAIN` after a raised launch now assert STUCK `unexpected_state` and are listed in the PR.
12. **Release of a no-receipt hold.** It stops the session, sets `launched`, and the next resume gates with that key as the previous one.

Install-agnostic: test homes are `tmp_path` scratch dirs, and the keys are AU-2's `ck1-` 32-hex form.
