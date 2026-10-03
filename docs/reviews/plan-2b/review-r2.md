Reviewed against ADR `66b3aec` §8 and the current `plan-2b-admind` code. References below point into the [plan](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md).

1. **[BLOCKING] Task 2 / B1 — redaction remains non-idempotent.**  
   [Lines 393–399](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:393) run token recognition before hex replacement. With `"f" * 64 + "ghp_" + "A" * 30`, the existing scanner’s token boundary initially prevents recognition. Hex replacement introduces a delimiter:
   - First pass: `<redacted hex key>ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`
   - Second pass: `<redacted hex key><redacted GitHub token>`

   I reproduced this using the plan’s function and current scanner. The prior control-escape example is fixed, but prior finding 5 remains unresolved. Add this regression and ensure replacements cannot expose newly recognizable secrets.

2. **[BLOCKING] Task 4 / B17 — upgrade redaction misses secrets spanning multiple already-sent chunks.**  
   [Lines 895–898](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:895) retrieve only the immediately preceding chunk. For a token `"ghp_" + "Z" * 600`, with the first two 250-character chunks already sent, that preceding chunk contains no token prefix. `redact_continuation` therefore sends the remaining token suffix unchanged; I reproduced this. Reconstruct the complete preceding context needed to recognize a spanning value, or conservatively suppress uncertain continuations. Prior finding 6 is only partially resolved.

3. **[BLOCKING] Task 5 — rearm can erase a new membership-event latch.**  
   [Lines 1378–1393](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1378) await `group_info`, then unconditionally clear `latched`. During that await, the existing subscription reader can process a membership event and latch through [daemon.py:469](src/heterodyne/admind/daemon.py:469). A count-preserving change can thus be latched, cleared by rearm, and followed by a passing count check. Rearm needs observation/event-generation checks around its awaited read and settlement. Add a barrier test delivering a membership event while rearm’s count request is outstanding.

4. **[BLOCKING] Tasks 3 and 5 — recovery does not reconcile the authorization map.**  
   A committed transition updates only in-memory `self.operators` ([line 1362](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1362)). Rearm changes the count and clears the journal, but never updates that map. If an add succeeds and its reply is lost, rearm trusts the new count while the added operator remains unauthorized; removal by name also fails because it searches the stale map. Startup instead rebuilds the map from every policy entry ([lines 689–690](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:689)), losing the distinction between configured operators and committed group membership. Define durable authorization state and an explicit reconciliation procedure for uncertain changes. Test lost-reply add/remove recovery and restart after committed changes.

5. **[BLOCKING] Task 9 / B13 — `!details full` still contradicts the uncapped details requirement.**  
   [Lines 2768–2771](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2768) cap records at 16 MiB, rendered tool lines at 20,000 characters, and each turn’s tool output at 200,000 characters. Explicit omission notices improve transparency but do not satisfy §8’s requirement to send the tool calls and results with no details-length cap. Bounded incremental reading is appropriate; bounded total delivery requires an ADR revision. Prior finding 8 is partially resolved.

   Additionally, [lines 2862–2863](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2862) truncate before redaction. Cutting through a token can leave a recognizable prefix with too few remaining characters for the scanner to match. Redact complete tool values before any permitted shortening.

6. **[BLOCKING] Task 9 — transcript metadata can terminate parsing before the turn’s own prompt.**  
   [Lines 2855–2859](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2855) set `begun = True` for every non-prompt dictionary, including metadata records. A span containing `queue-operation`, the turn’s own user prompt, then a tool call returns “no tool calls in this turn”: the prompt is mistaken for the next turn. I reproduced this with the proposed parser. Distinguish metadata, the initial prompt, and actual turn content; add metadata-before-prompt fixtures.

7. **[BLOCKING] Task 8 — failed backstop delivery permanently strands replies.**  
   [Lines 2574–2585](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2574) explicitly decline to recover failed batches. But the batch was already marked `sent` when merely enqueued ([line 2565](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2565)), and both details lookups require a successfully sent outbox row ([lines 2317–2322](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2317)). Consequently, the comment claiming `!details` still provides these replies is false. Define durable batch delivery recovery and test terminal failure followed by recovery, including restart. Prior finding 9 remains partially unresolved.

8. **[BLOCKING] Task 7 / B10 — one-message batches now omit more than the ADR permits.**  
   `fit` at [lines 2073–2085](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2073) shortens individual lines and may cut off the batch’s end. §8 requires batches of at most 50 collapsed lines to be sent whole, and larger batches to contain the specified first 10 and last 40 lines. A 49-line reply with long lines violates those rules despite fitting into one message. Prior finding 10’s multiple-message problem is fixed, but replaced by another spec conflict. Resolve the transport-size requirement in the ADR before implementation.

9. **[BLOCKING] Tasks 4 and 5 — specified tests cannot pass against the specified code.**  
   - Task 4’s [lines 820–826](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:820) expect `"a" * 300 + "b" * 300` to survive redaction and produce three chunks. It is one 600-character hex run and becomes one marker. Use non-hex fixture text.
   - Task 5’s work-lock scenario ([lines 1043–1046](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1043)) requires `changing` to be true while the transition waits for `work_lock`. The implementation sets it only after acquiring that lock ([lines 1273–1274](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1273)). Give this scenario different expectations consistent with finishing the message already in hand.

10. **[NON-BLOCKING] Tasks 3 and 6 — policy and ctl names still have different contracts.**  
    The ASCII restriction is removed, but ctl still rejects names longer than 128 characters or containing controls ([lines 1611–1614](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1611)), while policy names remain unrestricted. An initialized operator can still be impossible to remove through ctl. Align validation or provide an unambiguous supported lookup mechanism. Prior finding 13 is partially resolved.

Prior-round verification: findings **1–4, 7, 11, 14 and 15** are addressed by the revised mechanisms and planned checks. Findings **5, 6, 8–10 and 13** remain unresolved or partially resolved as above. Finding **12** gains substantial concurrency and restart coverage, but the contradictory assertions and missing recovery cases above still need correction.

The proposed automated tests use fakes and private tmux servers and respect the stated service/network constraints. Task 1 remains separate live research. I reviewed and exercised isolated plan snippets; I did not run the implementation test suite.

**Verdict: REVISE**