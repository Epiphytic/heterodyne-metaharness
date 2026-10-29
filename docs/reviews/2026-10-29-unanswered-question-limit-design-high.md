# HIGH design review: unanswered question admission limit

**Scope:** I read only `docs/designs/2026-10-29-unanswered-question-limit.md`. I have no code search in this session, so I checked the design's claims about existing code against the document alone, not the source. The claims I couldn't check are marked below. I made no changes and took no external actions.

**Verdict:** It's implementable after fixes. The core idea is sound: count open questions under the existing post lock, admit the 100th, reject the 101st, and exempt approvals. But four gaps could let the limit leak, reject valid calls, or allow a deploy without real approval. Each has a small fix.

## Critical flaws

**C1. "Unanswered" is defined by a single status, `open`.**
Lines 5 and 9 exclude `deciding`, `uncertain` and `blocked` from the count. If any of those is a question still waiting for a human answer, the real unanswered backlog can grow past 100 without limit. The same leak happens if a new row is inserted with a pre-post status (for example pending or queued) and only becomes `open` after the outgoing card is delivered. In-flight questions would then not be counted.
- **Fix:** Classify every status of a question explicitly, as unanswered or not, in one function with an exhaustive `match` and no wildcard arm. The COUNT should use that set. A newly added status then fails to compile until someone classifies it. Add a test for each status.
- *Unverified:* the actual status enum and the status of a newly inserted row.

**C2. The design doesn't say how wide the lock is or who else writes.**
"Under the existing ask-post lock" is only safe if two things hold:
- The same critical section covers both the COUNT and the INSERT.
- admind is the only process that inserts asks.

If `ask_post_lock` is an in-process mutex held only around posting, or if a CLI or other process also writes, two callers can both see 99 and both insert.
- **Fix:** State that the lock spans count → insert → enqueue. Also run the COUNT and INSERT in a single write transaction (`BEGIN IMMEDIATE` on SQLite, or the equivalent). That makes the limit hold even if another writer exists. The concurrency test should go through the real database, not a mocked count.
- *Unverified:* the lock's type and scope, and whether any other process writes asks.

**C3. Idempotent re-asks could be rejected.**
The design lists same-bead and latch controls but doesn't say whether they run before or after the limit check. At count 100, a retry of a question that's already admitted, or a same-bead re-ask that dedupe would merge, must not get the limit error.
- **Fix:** Specify the order as dedupe/same-bead/latch first, then the limit check, then rate limit, insert and enqueue. Also state that a rejected attempt uses up no hourly rate budget and sets no latch. Add both cases to the regression list.

**C4. Authorization is claimed inside the artifact under review.**
Line 3 says the operator "directs immediate implementation and deployment without additional approval." Under the shared-queue protocol, a design is not authorization, and this note is not evidence of a human approval. Deploying to the live install is outward-facing and hard to reverse.
- **Fix:** Move the approval out of the design text into a separate recorded approval (the bead or a human sign-off) that cites this reviewed revision. Until that exists, treat the doc as not yet approved for implementation or deploy.

## Significant, but not blocking once noted

- **The rejection contract isn't defined.** "Explicit error" doesn't tell callers what to do. Agent callers may treat it as temporary and retry in a loop.
  - **Fix:** Give it a stable error code that's different from the rate-limit error. Include `limit=100` and the current count, and mark it "retry after answers", not "retry soon".
- **The limit is only checked at admission.** If any transition can move a question back to `open` (for example `uncertain` → `open`, or a re-open), the count can go above 100.
  - **Fix:** Say explicitly that 100 is an admission limit, not a stored invariant. Otherwise, define how re-opens are handled.
- **Approvals now have no aggregate limit, which matches the operator's intent.** Only the hourly rate and in-flight controls will bound them.
  - **Fix:** Confirm in the design that the hourly rate applies to approval and operation-consent asks. If it doesn't, those paths become unbounded.
  - *Unverified:* which ask kinds the hourly rate covers.
- **The full list of places the old limit of 20 appears isn't given.** "Keep listing projections unchanged" could leave status or health output showing `x/20`.
  - **Fix:** List every place the 20 constant, its config key, its error message and any capacity display appear, and say how each changes.
- **The deploy plan is missing detail:**
  - **Backup:** require a consistent live SQLite backup (`.backup` or `VACUUM INTO`), not a file copy that ignores the WAL file.
  - **Rollback:** state that rollback is "restore the previous admind binary". There's no schema change, so no data rollback is needed.
  - **Branch:** record the release tag or branch that gets the patch, so the release and main versions can be checked against each other.
  - **Answer check:** define "existing answers unchanged" as a checksum of answered rows (id, status, answer, timestamps) taken before and after.

## What's fine as written

- The boundary rule (count 99 admits, count 100 rejects) and checking before insert and enqueue.
- Counting fresh, without depending on fulfillment.
- Answering a question frees its slot immediately.
- No schema change. Without an index the COUNT is cheap at this scale.
- Keeping the rate and in-flight transport controls separate from the stored-queue limit.

## Smallest changes to make it implementable

1. Add a status classification table with an exhaustive `match` (C1).
2. Say that the lock and transaction span count → insert → enqueue, and that admind is the only writer (C2).
3. Fix the check order, and state that a rejected attempt uses up no rate budget and sets no latch (C3).
4. Get a separate recorded human approval of this revision (C4).
5. Define the error code, the re-open semantics, and how backup, rollback and answer verification work.
