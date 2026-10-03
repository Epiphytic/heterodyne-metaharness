1. **[BLOCKING] Task 5 / B22 — rearm can discard a buffered membership event.**  
   [Rearm checks `acked` and event counters](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1525), but acknowledgment does not mean events are being consumed. Existing [confirm_observing](src/heterodyne/admind/daemon.py:459) sets `acked` before awaiting its count check; [subscribe](src/heterodyne/marmot/control.py:265) reads no events until that callback finishes.

   During this interval, a count-preserving membership change can remain buffered while rearm clears the latch. Rearm then increments `membership_epoch`; the outstanding acknowledgment check defers, and `confirm_observing` can raise `unverified` because the latch is now clear. The subscription closes without processing the buffered event. A subsequent subscription sees the unchanged count and passes.

   Require an established event reader before rearm can settle, while still allowing recovery from a latched state. Add a barrier test covering acknowledgment, a blocked acknowledgment count check, a buffered membership event, and concurrent rearm. Round-2 finding 3 is not fully resolved.

2. **[BLOCKING] Task 8 / B9, B19 — fallback reply extraction retains the old truncation and failure semantics.**  
   [Task 8 keeps `reply_text` as the extraction function](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2599). Existing [last_assistant_text](src/heterodyne/admind/hook.py:196) reads only the transcript tail and replaces previously collected text whenever another assistant record contains text.

   Consequently:
   - Earlier assistant questions or errors disappear from the fallback reply and its durable `!details` record.
   - An assistant JSONL record larger than the existing 8 MiB tail limit can be discarded completely.
   - An unreadable transcript returns `""`, which [the proposed Stop handling](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2654) treats as a successful `NO_REPLY`, rather than a pipeline failure requiring backstop delivery.

   In-memory reproduction with an assistant question, tool activity, and final assistant text returned only the final text. Use the captured turn interval for fallback extraction, preserve the required assistant text, and distinguish read failure from a genuinely silent turn. Add fallback-specific large-record, multi-record, and unreadable-file tests.

3. **[BLOCKING] Task 9 / B13 — oversized metadata still terminates the turn before its own prompt.**  
   [The oversized-record branch unconditionally sets `begun = True`](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:3082). For an oversized `queue-operation` record followed by the turn’s own prompt and tool calls, the prompt is therefore mistaken for the next turn and parsing stops.

   I reproduced this with `MAX_RECORD` lowered as the planned tests already do: output contained the oversized-record notice but omitted the subsequent tool call. The [existing oversized-record test](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2948) starts with the prompt and misses this case.

   The explicitly flagged 64 MiB omission is acceptable as a reviewed tradeoff; silently omitting the remaining turn is an additional defect. Preserve enough record classification to avoid treating oversized metadata as turn content. Round-2 finding 6 is only partially resolved.

4. **[BLOCKING] Task 9 — `!details full` silently discards non-text tool results.**  
   [_result_text](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:3042) replaces every non-text content block or structured result with `[non-text result]`, even for small records well below the guard. Different image or structured results thus produce identical output, losing their contents permanently from the details response.

   ADR §8 requires tool calls **and their results**, with omission limited to the explicitly flagged deviations. Preserve non-thinking result data in a deterministic, redacted representation, or explicitly flag and justify this additional deviation. Add fixtures containing mixed text/non-text results and structured content.

Prior-review verification: round-2 findings **1, 2, 4, 5, 7, 8, 9 and 10** are addressed under the review criteria you supplied; **3 and 6** retain the gaps above. Round-1 findings **1–7, 9–11 and 13–15** have corresponding fixes; **8 and 12** remain incomplete because transcript completeness and its boundary tests still have these defects.

The plan assigns tasks to all §8 requirement areas. B10, B13, B21 and B22 are clearly flagged and have sound motivations; the findings above concern additional behavior. Planned automated tests respect the fake-service, private-tmux, no-network constraints. Task 1 is explicitly separate live research. I exercised isolated proposed snippets; I did not run an implementation test suite.

**Verdict: REVISE**