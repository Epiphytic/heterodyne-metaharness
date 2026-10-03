Reviewed HEAD `cfc8df1` against ADR `66b3aec` §8, the existing code, and the prior reviews. No files changed. **No blocking findings.**

1. **[NON-BLOCKING] Task 5 — membership-notice keys can collide.**

   [The notice key](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1612) contains only `pending.started` and the operation. `started` comes from [second-resolution `now()`](src/heterodyne/admind/store.py:42), and [enqueue silently ignores duplicate keys](src/heterodyne/admind/store.py:146).

   Two successful additions within one second can therefore commit correctly but emit only the first notice. Use a unique transition identifier and add a regression test with a fixed clock. Membership state and audit records remain correct, so this is nonblocking.

2. **[NON-BLOCKING] Task 6 — rearm’s subscription requirement remains inaccurately described.**

   [Task 6 says rearm requires an “observing subscription”](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1993), while [Task 5 checks `reading`](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1634). These states differ during latch recovery. Say “an actively read subscription”; requiring `observing` could prevent the intended recovery. The proposed implementation uses the correct condition.

Round 7’s oversized-record defect is resolved: `RecordTooLarge` now fails the complete read rather than skipping an unclassified record. Isolated execution of the proposed reader confirmed both reported boundary cases fail closed. The mixed tool-result/text case preserves subsequent assistant text, and premature EOF and unterminated records fail closed.

Round 4’s fixes in `7eae5f9` remain present: recursive redaction before JSON serialization, mixed tool-result classification, and incomplete-read rejection. Round 7’s missing rendering deviations are now rows 23–24; row 22 correctly distinguishes newline/tab and offers raw passthrough as its alternative.

The [deviation table](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:65) is complete and accurate for the intentional departures I identified:

| Row | Departure from ADR r13 §8 |
|---|---|
| 1 | Newline and tab remain literal. |
| 2 | Hex runs longer than 64 digits are also masked. |
| 3 | Text still changing after ten redaction passes is replaced wholesale. |
| 4 | Additional operator-name restrictions, duplicate-key rejection, and rejection of an empty eligible operator set. |
| 5 | Removing the last operator is refused. |
| 6 | Rearm requires a running daemon. |
| 7 | Rearm requires active event reading and refuses concurrent changes or unreconciled membership. |
| 8 | Authorization requires policy eligibility plus durable confirmed membership; ambiguous upgrades latch. |
| 9 | Up to ten summary body lines are accepted, with additional footer and size limits. |
| 10 | Summarizer profile arguments are ignored. |
| 11 | Admin and summarizer adapters remain Claude-only. The admin restriction directly departs from “any adapter”; broader summarizer support is interpretive. |
| 12 | Oversized batches shorten lines and may cut the ending. |
| 13 | Failed batches reopen with a new window/key; additional affected replies can join. |
| 14 | Joining stops at 60 seconds; posting uses a one-second poll. |
| 15 | Unreadable replies become fixed notices rather than recoverable reply text; valid transcript replacement is undetected. |
| 16 | Oversized records make the turn unreadable; images become metadata/digests; structured results become sorted JSON with collision numbering. |
| 17 | Full tool details can be unavailable for unknown spans, contention, timeout or corruption, without automatic retry. |
| 18 | Audit ID fields use hashed references rather than plain markers. |
| 19 | Upgrade recovery can repeat delivered text or replace unverifiable continuations wholesale. |
| 20 | Unsupported membership operations stop implementation; the init-only alternative remains deferred. |
| 21 | Question/error preservation is requested through the prompt, without validating accepted summaries. |
| 22 | Operator messages containing controls other than newline/tab are refused. |
| 23 | Sensitive-bearing alerts are withheld wholesale; long alerts are truncated before common redaction. |
| 24 | Error/service rendering replaces entire sensitive-bearing values before common redaction. |

Apart from those declared departures, I found no blocking gap in membership steps 1–5, latch/rearm, dispatch and send locking, transactional settlement, restart recovery, lanes, reply/detail interfaces, or audit redaction.

Summarizer isolation remains dependent on Task 1 verifying the proposed CLI flags. Automated-test scenarios use fakes and temporary resources; live research and acceptance are separate. I ran isolated reader checks, not an implementation test suite.

**Verdict: APPROVE**