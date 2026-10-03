Reviewed HEAD `1bb4749` against ADR `66b3aec` §8 and the prior reviews. No files changed.

Round 4’s three fixes are present in `7eae5f9` and retained: redaction before JSON serialization, mixed tool-result classification, and incomplete-read rejection. Round 6’s fixes are also present: full attributed audits for latched operators, per-row backoff timers, preservation of colliding audit keys, and the requested deviation-table corrections.

1. **[BLOCKING] Tasks 8–9 / B13 — oversized records can defeat turn boundaries.**

   [The oversized-record branch](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2839) yields a size and continues without knowing whether the skipped record was an assistant response or a user prompt. [Boundary detection](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2849) then relies entirely on `begun`.

   I exercised the proposed function in memory and reproduced both cases:

   - Own prompt → oversized first answer → next prompt → next answer: the next answer is returned as this turn’s answer.
   - Own prompt → own answer → oversized next prompt → next answer: both answers are returned.

   The same reader supplies fallback replies and full tool details, so both can include another turn’s content. This contradicts the ADR and row 17’s stated protection against showing another turn’s tool calls.

   Preserve enough record classification while streaming, or fail extraction when an oversized record makes boundaries ambiguous. Add both regression cases; the existing oversized-metadata and oversized-result tests miss them.

2. **[NON-BLOCKING] Task 2 — inherited emitters still discard nonsensitive text, and the deviation table omits this.**

   [Task 2 retains `show` for user-facing errors and wraps the existing alert renderer](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:558). However:

   - [Alert rendering](src/heterodyne/admind/alerts.py:118) withholds the entire alert when it contains one secret or identifier, and separately truncates long alerts.
   - [Existing `show`](src/heterodyne/config/secret_scan.py:117) replaces an entire sensitive-bearing value. This remains in command errors and [service replies](src/heterodyne/services.py:17).

   Applying `redact` afterward cannot recover the discarded context. These paths remain conservative against leakage, but differ from §8’s common marker redaction and the plan’s “rest of the text is kept” claim. Use the common redactor before formatting, or explicitly list these retained exceptions. Add a secret-bearing alert test that checks preservation of surrounding text.

3. **[NON-BLOCKING] Task 6 / deviation row 22 — two descriptions remain inaccurate.**

   [Task 6 says rearm “needs only the count”](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1991), but [Task 5 requires active event reading](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1631). Correct the prose.

   [Deviation row 22](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:92) should specify controls **other than newline and tab**, matching [the retained predicate](src/heterodyne/admind/commands.py:34). Its proposed “If no” alternative—escaping before passthrough—also still violates byte-for-byte passthrough.

The complete deviation inventory I identified is below. Existing row numbers refer to the [operator sign-off table](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:65).

| Row / location | Difference from ADR r13 §8 |
|---|---|
| 1 / B1 | Newline and tab remain literal. |
| 2 / B1 | Hex runs longer than 64 digits are also masked. |
| 3 / B1 | Redaction still changing after ten passes replaces the whole text. |
| 4 / B2 | Additional name restrictions, duplicate-key rejection, and rejection of an empty eligible operator set. |
| 5 / Task 5 | Last-operator removal is refused. |
| 6 / B6 | Rearm requires a running daemon. |
| 7 / B22 | Rearm additionally requires event reading and refuses races or unreconciled membership. |
| 8 / B21 | Authorization requires policy eligibility plus durable confirmed membership; ambiguous multi-operator upgrades latch. |
| 9 / B8 | Up to ten summary body lines are accepted; the footer increases displayed length. Additional size limits trigger backstop. |
| 10 / B8 | Summarizer profile arguments are ignored. |
| 11 / Tasks 3, 7 | Admin and summarizer adapters remain Claude-only. “Any adapter” is explicit for the admin agent; broader summarizer support is an interpretation. |
| 12 / B10 | Oversized batches shorten selected lines and potentially cut the ending. |
| 13 / B10 | Failed batches reopen with a new window/key; additional replies can join. |
| 14 / B10 | Joining closes strictly at 60 seconds, but posting uses a one-second poll. |
| 15 / B9 | Unreadable replies become notices; ordinary details cannot recover their actual text. Valid transcript replacement is undetected. |
| 16 / B13 | Oversized records become notices; images become metadata/digests; structured data becomes sorted JSON with collision numbering. |
| 17 / B19–B20 | Full tool details can be unavailable for unknown spans, contention, timeout or corruption, without automatic retry. |
| 18 / B16 | Audit ID fields use hashed references rather than plain markers. |
| 19 / B17 | Upgrade recovery can repeat delivered text or replace unverifiable continuations wholesale. |
| 20 / Task 1 | Unsupported membership operations stop implementation; the init-only alternative has no task. |
| 21 / Task 7 | Question/error preservation is requested through the prompt without validating accepted summaries. |
| 22 / retained passthrough | Operator messages containing non-layout controls are refused. |
| **Unlisted / Task 2 alerts** | Sensitive-bearing alerts are withheld wholesale; long alerts are truncated. |
| **Unlisted / retained error and service rendering** | Sensitive-bearing values lose surrounding nonsensitive context before common redaction. |
| **Unlisted defect / Tasks 8–9** | Oversized records can cause replies or details to include another turn, as finding 1 demonstrates. |

Otherwise, I found no additional blocking mismatch in membership steps 1–5, locking, transactional settlement, latch/restart recovery, lane selection, or the revised audit interfaces. Summarizer isolation remains conditional on Task 1 verifying the actual CLI’s proposed argv.

The automated-test scenarios consistently use fakes and temporary resources; Task 1’s live research is explicitly separate. I ran isolated in-memory checks, not the implementation test suite.

**Verdict: REVISE**