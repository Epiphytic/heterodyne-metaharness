1. **[BLOCKING] Task 9 / B1, B13 — JSON serialization can bypass token redaction.**  
   [Tool inputs](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:3235) and [structured results](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:3223) are serialized before redaction. A control character immediately preceding a token becomes, for example, literal `\u0001ghp_…`. The scanner’s [alphanumeric boundary restriction](src/heterodyne/config/secret_scan.py:42) then rejects the token because it follows `1`. Repeated redaction does not repair this.

   In-memory reproduction removed the token from the original string but retained it in both serialized representations. Recursively redact decoded strings before serialization, retain final whole-message redaction, and add tests for tool inputs and structured results.

2. **[BLOCKING] Tasks 8–9 / B9, B13, B19 — mixed tool-result records prematurely terminate extraction.**  
   [_is_prompt](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2665) classifies any non-meta user record containing a text block as a prompt, even when it also contains `tool_result`. [turn_records](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2702) checks that classification before `_has_result`.

   A fixture containing an assistant tool call, a mixed tool-result/text record, and final assistant text lost both the result and final text. This affects fallback replies, summaries, and `!details full`. Exclude tool-result records from prompt classification and test mixed records alongside genuine next-turn boundaries.

3. **[BLOCKING] Tasks 8–9 / B9, B19 — incomplete transcript reads still report success.**  
   [_lines](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2639) silently returns when EOF occurs before the captured `end`, and silently discards an unterminated final record. Consequently, a truncated or replaced transcript can produce partial text or `""`; Stop handling treats that as a successful reply or `NO_REPLY`, bypassing the extraction-failure backstop.

   Mocked reads reproduced both silent outcomes. Detect premature EOF and incomplete records within the captured interval, propagate an explicit extraction failure, and test truncation between offset capture and reading. This completeness gap remains related to round-3 finding 2.

The four round-3 findings have corresponding fixes for their reported cases. Earlier fixes remain represented, but transcript completeness still has the defects above. All §8 requirement areas have tasks; B10, B13, B21, and B22 are clearly flagged and reasonably justified.

Planned automated tests respect the fake-service and no-network constraints; Task 1 separates live research. I exercised isolated snippets in memory, not an implementation test suite.

**Verdict: REVISE**