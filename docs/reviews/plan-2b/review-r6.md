Reviewed HEAD `c0167be` against ADR `66b3aec` §8 and rounds 4–5. No files changed.

Round 4’s three fixes are present in `7eae5f9` and retained: redaction before JSON serialization, mixed tool-result classification, and rejection of incomplete transcript reads. Round 5’s three blocking findings also have fixes and regression scenarios: colliding tool-data keys, malformed records, and batch arrival order. The batch deadline is now checked when joining. The retry change introduces the regression below.

1. **[BLOCKING] Task 3 / Task 2 — latched operator messages escape the required full audit.**

   [The guard returns `drop` without the operator name when latched](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:765). Task 3 only adds `operator` to the accepted `inbound` record at [line 834](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:834).

   Existing [drop handling](src/heterodyne/admind/daemon.py:498) records only a sender prefix, reason and text length. Consequently, an authenticated operator’s message while latched has neither its full redacted text nor its sending operator recorded. Malformed-ID and replay drops likewise lack the sending operator.

   §8 requires each operator message to be logged in full with its operator, timestamp and action. Preserve authenticated operator identity on rejected verdicts and audit those messages while retaining the latch’s prohibition on dispatch and posting. Add a test covering a long, secret-bearing operator message received while latched.

2. **[NON-BLOCKING] Task 4 — the single retry slot loses another row’s backoff.**

   [Initialization](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1073) provides one `_backoff` tuple; [every retry replaces it](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1115).

   I exercised the proposed function in memory:

   - Details delivery fails and starts its timer.
   - An urgent row arrives, fails, and replaces that timer.
   - The urgent timer completes; urgent delivery succeeds.
   - Details retries although its original timer remains pending.

   This defeats the claimed per-row backoff, can consume retries prematurely, and leaves earlier timers outside the stated shutdown cancellation. Track timers by row or retain retry deadlines. Test simultaneous failures in both lanes; the successful urgent-delivery case alone misses this regression.

3. **[NON-BLOCKING] Task 2 — audit dictionaries retain the collision bug fixed in Task 9.**

   [Audit `clean`](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:522) still constructs dictionaries using redacted keys without collision handling. Two distinct hex keys become the same marker, deleting one value.

   Task 9’s [numbered-key implementation](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:3347) fixes this for tool data. Apply equivalent preservation to recursive audit fields and test it. Current full operator messages are strings, so this does not itself reproduce finding 1.

4. **[NON-BLOCKING] Deviation table — substantially improved, but incomplete and partly inaccurate.**

   Correct these entries in the [operator sign-off table](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:65):

   - **Row 16 is not an ADR deviation.** The ADR expressly permits transcript assistant text blocks as the fallback. Joining all texts in turn order implements that requirement; using only the last block would omit content.
   - **Row 4 omits empty-name rejection**, implemented at line 712.
   - **Row 5’s recovery claim is inaccurate.** Host-side `operators add` provides a potential recovery route after allowing last-operator removal; recovery would not necessarily require `init`.
   - **Row 11’s “by extension” is an inference.** §8 explicitly permits any **admin** adapter; distinguish that statement from the plan’s summarizer restriction.
   - **Row 15 overstates changed-file detection.** The reader detects truncation, incomplete records and malformed JSON. [Offsets and filename checks](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2698) do not detect replacement with another sufficiently long, valid transcript. Narrow the wording or add file-provenance validation.
   - Add the audit omission in finding 1 and the retained passthrough exception below.

   The complete deviation inventory I identified is:

   | Table row / plan section | Difference from ADR §8 |
   |---|---|
   | 1 / B1 | Newline and tab remain literal rather than escaped. |
   | 2 / B1 | Hex runs longer than 64 digits are also masked. |
   | 3 / B1 | Unstable redaction replaces the whole text after ten passes. |
   | 4 / B2 | Empty, oversized or control-bearing names, duplicate keys, and an empty eligible operator set are rejected. |
   | 5 / Task 5 | Removing the last operator is refused. |
   | 6 / B6 | Rearm requires the running daemon. |
   | 7 / B22 | Rearm additionally requires event observation and refuses concurrent changes or unreconciled membership. |
   | 8 / B21 | Authorization requires policy eligibility plus durable confirmed membership; ambiguous multi-operator upgrades latch. |
   | 9 / B8 | Ten summary body lines are accepted, with footer increasing displayed length; character and byte limits also trigger backstop. |
   | 10 / B8 | Summarizer profile arguments are ignored. |
   | 11 / Tasks 3, 7 | Admin and summarizer adapters remain Claude-only; broader adapter support is deferred. |
   | 12 / B10 | Oversized batches shorten lines and potentially the ending instead of preserving all text selected by the line rules. |
   | 13 / B10 | Failed batches reopen with a fresh window and delivery key; additional affected replies can join the reopened batch. |
   | 14 / B10 | Posting occurs through polling, approximately one second after the strict joining deadline. |
   | 15 / B9 | Unreadable replies become fixed notices, leaving their actual text unavailable through ordinary `!details`. |
   | 17 / B13 | Oversized records become notices; images become metadata/digests; structured results become sorted JSON with collision numbering. |
   | 18 / B19–B20 | Unknown spans, contention, timeout or corruption make full tool details unavailable without automatic retry. |
   | 19 / B16 | Audit ID fields use hashed references rather than markers. |
   | 20 / B17 | Upgrade recovery can repeat delivered text and replace unverifiable continuations wholesale. |
   | 21 / Task 1 | Unsupported membership operations stop implementation; the ADR’s init-only alternative has no implementation task. |
   | 22 / Task 7 | Verbatim question/error preservation is requested through the prompt, without validating accepted summaries. |
   | Task 3 / audit | Known operator messages rejected while latched are not logged in full or attributed. |
   | Retained `handle` behavior | [Control-bearing operator text is refused](src/heterodyne/admind/daemon.py:535), rather than passed byte-for-byte to the admin session. |

   Row 16 should therefore be removed from the deviation inventory.

The membership transition, latch, transaction and restart machinery otherwise retains the prior fixes. I found no additional blocking redaction, ctl-socket or interface mismatch. Summarizer isolation still depends on Task 1 verifying the proposed argv against the installed CLI.

The proposed automated tests use fake services, private tmux servers and temporary files consistently with the stated constraints. Task 1’s live research is separate. Additional tests are needed for findings 1–3; I ran isolated in-memory checks, not an implementation test suite.

**Verdict: REVISE**