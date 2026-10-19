**Verdict: REVISE**

I ran one full high-effort review using the built-in `code-review` skill, then checked its main findings against source myself. The plan file's hash matches the frozen value (`783c554f…9170`), and the worktree HEAD is `5db4c0c`. I made no edits and touched no live DB, services, queue, network or GitHub. Several claims in the plan hold up: the installer and archive sums, `fulfillment.ENABLED = False`, the read-only nature of `admind initialised`, the additive-only `store_core` change, and the offline evidence. The two reports (main and rollback-compat) agree with what the plan says: 933 sent / 2 quarantined, 14 triggers, zero receipts, and old-writer behaviour not shown. I found no separate `preflight/` directory under N; the `preparation/` outputs are there.

## Findings

**1. HIGH: the preservation check in step 5 fails a healthy deploy** (lines 16, 34)
- The plan says to run "the actual accepted preservation comparison" as a subset check that allows new records.
- The only accepted comparator is `preserved()` in `deploy/prepare_admind_guard.py:42-65`. It is not a subset check:
  - Every table except `kv` needs `sorted(old) == sorted(new)` exactly.
  - Outbox rows need the same set of row keys (`old_status.keys() == new_status.keys()`), and the only status change allowed is pending→quarantined.
- So any legitimate traffic after restart raises an error. That includes a new ask or outbox row, an answer, or pending→sent.
- The failure rule then stops admind on a healthy deploy. The other path is a new, unreviewed comparator, which goes against "no new runtime code."

**2. HIGH: nothing blocks new management posts once the reader is installed, and one such post makes rollback impossible** (lines 13, 30)
- Once installed, `asks.management_check` (`asks.py:151`) accepts any well-formed `task-management-consent-v1` post from the same UID. The daemon only refuses badly formed ones (`daemon.py:1072`).
- The plan checks for zero such requests once, before the deploy. Nothing stops new ones afterwards.
- The ADR's rollback order is "first stop new management submissions … must show zero unresolved v2 requests … If any remain, rollback is refused."
- So one forged or accidental tagged post makes rollback to 076 impossible under the ADR. It also shows a convincing approval card with no producer behind it.
- The ADR's forged-post test assumed the new queue was in place, not the old queue running next to the new reader.
- A posting latch would refuse these posts, but only after the contract check passes. The plan neither relies on that nor states the current latch state.

**3. MEDIUM-HIGH: the root bootstrap command is never written out, and the obvious one does irreversible things** (lines 32-33)
- Step 3 has the helper emit `installer-command.sh`. Per `prepare_admind_guard.py:118-126`, that file has no `--no-wsd` and no `--no-verity`.
- Running it as emitted would:
  - run `tune2fs -O verity` (`install.sh:685`), which cannot be undone;
  - write and start a WSD unit.
- The README's pinned block says "The block stays as it is." It lets an approval add only `--adopt`, `--no-wsd` and `--fetch`. Adding `--no-verity` (step 4) and `--dry-run` (step 2) to that block is itself an unlisted change to the documented process.
- No exact filled-in command or script is given, with expected output. That breaks the rule that root changes go in a script Liam runs with one command.

**4. MEDIUM: allowing traffic before the comparison skips a runbook gate and loses the clean rollback point, and the plan doesn't say so** (line 16)
- `docs/admind-guard-recovery.md` step 3 says to compare "… delivery rows before allowing traffic."
- Its rollback section allows the stopped backup only "Before traffic, with no new effects."
- Deviation 4 says traffic may arrive right after restart, but never states that it overrides this gate or that the only clean rollback window is gone. An approver should see both said outright.

**5. MEDIUM: "preserve the actual managed unit/install record" is not something the installer does** (line 31)
- The installer's `rewrite` branch (`install.sh:737-757`) overwrites `heterodyne-admind.service` with no backup; only `adopt` makes one.
- `record-release` (`install.sh:899`) rewrites `record.json` to point at the new commit.
- Copying the 076 unit and record into the private before-state inventory needs to be a named step before step 4.

**6. MEDIUM: an unmet ADR requirement is missing from the deviations list** (line 26 compared with the ADR's lines 176 and 189)
- The ADR requires offline proof of "both rollback directions, old-writer compatibility." The plan admits this is unproven (zero receipts; only reopen and reads were tested) but doesn't list it as a deviation.
- Step 7 is honest that there is no automatic rollback. Still, the approver should be told plainly that the ADR's rollback acceptance is not met.

**7. LOW-MEDIUM: the zero-request check measures the wrong thing** (line 30)
- Format2 management requests already exist today. What is new is requests tagged with `presentation` / `render_version == 2`.
- As worded, the check either blocks for no reason or misses what it should catch. Say exactly which field or query to check.

**8. LOW-MEDIUM: open review findings are not listed** (line 24)
- "Actual product review findings remain open and unwaived" is stated without the findings themselves or why they don't block this exact source. An approver can't weigh risk they can't see.

**9. LOW: outage length and readability** (lines 32-33)
- admind is stopped before the installer runs, so the release build, `uv sync` and daemon-reload all happen while admind (the approval channel) is down. That makes the "bounded window" longer than it needs to be, and a build failure leaves it down indefinitely. Running the dry-run and build checks while admind is still up would shorten it.
- Numbers run into words throughout ("exited0", "private0700", "outbox933sent"), which makes evidence easy to misread in an approval document.

## What is fine
- Scope is narrow: no gateway, policy, group, `--adopt` or `--fetch` changes, and WSD stays inactive.
- Projection migration and fulfillment stay off.
- Step 7 never automatically restores an old DB, clears latches or cancels uncertain asks.
- Old-card support and live readability are claimed honestly.
- The plan states that a deploy request is not approval of this deviation.

## Minimum before human approval
Fix findings 1–3:
- a comparator that actually allows new rows, or compare before any traffic arrives;
- a way to stop or refuse tagged management posts, or a rollback position that is explicitly accepted;
- one exact pinned root script with `--no-wsd --no-verity` and its expected dry-run output.

Then disclose findings 4–6 as deviations.

`★ Insight ─────────────────────────────────────`
- The plan writes "subset" in prose, but the comparator it names checks for equality. Before signing off, check what an operational plan's verification step would actually compute, not just what it is called.
- Installing reader-first opens the posting path before its producer exists. Here that weakens the ADR's rollback rule more than it weakens readability.
`─────────────────────────────────────────────────`
