**Verdict: REVISE.** I statically reviewed `2c0c5b5..af8deaf` against the approved design at `e8b5db3`, using `git show` only: no tests, probes or edits. I checked the r1 fixes and found them present:

- Reaction answer IDs (`r:` prefix) can now be cited as consumed answers.
- A merge with no evidence is refused on all three paths: controller apply, operator propose and `!fulfill` confirm.
- Identical proposals past 24 hours return stale-intent.
- `!fulfill` confirmation and inbound completion happen in one transaction, and the database-error path is handled.
- Kernel credentials decode PID/UID/GID as `iII`, and a missing socket raises OSError, so it falls back to the PID-only peer.
- Lifecycle reads take the Store lock, and protection checks run before every status or answer write.
- Notices go through the central `Admind.post`, which redacts and wakes the sender.

New problems remain in the operator route (#1–#3). There are also smaller locking, compatibility and cleanup issues. No CLAUDE.md rule applies to this Python code, so there are no convention findings.

10 findings, most severe first:

1. `src/heterodyne/admind/fulfillment.py:567`: propose() builds the confirmation notice from the whole ask title and body plus raw consumed-answer text, then refuses if it exceeds 3,500 chars / 40 LF or if redaction would change it. The declaration cannot narrow any of that material, so many real asks can never use the operator route.

   Failure scenario: A question whose body is about 3,200 characters, or mentions a configured login directory or token-shaped text that redact() rewrites, is answered and the work is completed. Every propose, with any short declaration, raises 'notice-redaction-or-bounds'. The refusal tells the controller to narrow its declaration, but the size and redaction come from immutable ask/answer bytes. With no machine receipt, the ask holds a MAX_OPEN slot until it is cancelled, which the design says not to substitute for fulfillment.

2. `src/heterodyne/admind/fulfillment.py:591`: The intent notice quotes operator answer text (response_context) but is queued with owner=None, i.e. system ownership. Under the integrated guard model, operator-derived content must carry its requester owners so a revoked requester's material is suppressed.

   Failure scenario: Operator B answers a question. A controller proposes, and the notice quoting B's answer text is queued with ownership_kind='system'. B is then revoked before delivery. prepare_delivery suppresses only request-owned rows whose owners are no longer operators, so B's quoted answer is still sent to the group. Reply and !details paths would fence the same content with request_owners.

3. `src/heterodyne/admind/fulfillment.py:676`: confirm() calls commit(), and commit() calls retire_pending(ask_id,'fulfilled') while the intent being confirmed is still 'pending'. The confirmed intent is therefore first retired, with an audit tombstone saying it was retired by fulfillment, and only then set to 'consumed', keeping reason='fulfilled'.

   Failure scenario: The operator replies !fulfill to a valid notice. ask_fulfillment_audit gets both (ask, intent_digest, 'applied') and (ask, intent_digest, 'fulfilled'), and the intent row ends status='consumed', reason='fulfilled'. An auditor cannot tell a consumed confirmation from an intent retired because something else fulfilled the ask. This contradicts the disposition 'Operator confirmation explicitly consumes its own intent atomically'.

4. `src/heterodyne/admind/fulfillment.py:371`: view(), the read-only 'get', runs inside store.transaction(), which issues BEGIN IMMEDIATE. That takes SQLite's write lock for a pure read, contradicting disposition #8 ('Pure reads do not open a writer transaction'), and it blocks the event loop on busy waits.

   Failure scenario: A CLI or other process holds a write transaction. A `fulfillment get`, or reconcile's per-entry pre-apply Get, waits up to the 5s busy timeout on the event loop, stalling the outbox and inbound worker. It then returns 'unavailable', so a reconcile plan stops at that entry even though a plain read would have succeeded.

5. `src/heterodyne/admind/daemon.py:2420`: In the !fulfill handler, the fulfillment.Invalid branch calls finish() with no sqlite3.Error guard. The catch-all `except Exception` labels every non-Invalid error, including programming errors like KeyError or TypeError, as 'store unavailable'.

   Failure scenario: confirm() raises Invalid('stale-intent'), then the database becomes busy or locked before the refusal reply's finish(). OperationalError escapes handle() to the worker's generic handler, so the operator gets no reply and only 'handler failed' is audited. The path the r1 #3 fix was meant to make deterministic still fails silently. Separately, a code bug inside confirm is reported to the operator as a store outage.

6. `src/heterodyne/admind/ctl.py:182`: strict_json (rejects duplicate keys and NaN, and does a second full json.loads) now runs on every ask.sock request, including legacy post/get/list/cancel/binding. That changes the old protocol's input acceptance, although the design requires explicit client opt-in and unchanged legacy behavior.

   Failure scenario: An existing client sends an AskPost whose JSON repeats a key, which the msgspec decoder previously accepted. After this change it gets 'malformed request' even with fulfillment ENABLED=False. Every legacy request of up to 256KiB is also parsed twice on the event loop.

7. `src/heterodyne/admind/store.py:371`: Lifecycle.migrate() runs inside every Store() construction, including every CLI _load. It takes BEGIN IMMEDIATE and re-hashes every retained fulfillment's full answer set (refresh) on each open. Its Invalid (ValueError) failures are not caught by cli._load, which only catches OSError and sqlite3.Error.

   Failure scenario: With many retained fulfillments (never pruned), every admind CLI invocation and daemon start rewrites the valid flags and hashes up to 64KB of answers per ask under the write lock, while the daemon is serving. An 'incompatible-schema' or 'missing-store-identity' failure shows the operator a raw Python traceback instead of the fixed StateDirError message.

8. `src/heterodyne/admind/fulfillment_cli.py:118`: In reconcile --apply, a fresh successful application (result 'applied') is reported as outcome 'already-applied', the same label as a receipt that was committed earlier.

   Failure scenario: Reconciling 5 eligible receipts in apply mode reports all 5 as 'already-applied'. The controller cannot tell which entries this run actually committed and which were committed before an earlier lost response, which is exactly the distinction the design's response-loss recovery relies on.

9. `src/heterodyne/admind/store.py:836`: The fulfillment-aware capacity predicate is written out three times: in capacity_asks, in capacity_count, and as a hand-written complement in inactive_asks. One shared SQL fragment, or a single view, would do.

   Failure scenario: A later change to what counts as capacity-active (for example a new conflict rule) edited in one copy but not the others makes the MAX_OPEN cap count, the list's active section and its inactive section disagree. An ask could then be listed twice or omitted, or count toward the cap while showing as inactive.

10. `src/heterodyne/admind/fulfillment.py:194`: check_id re-implements the ask-ID grammar with a literal alphabet and length 4, instead of reusing asks.ID_ALPHABET / asks.ID_LENGTH (or asks.normalise_id with an exact-case comparison).

    Failure scenario: If the ask-ID alphabet or length in asks.py changes, fulfillment keeps validating against the stale copy. Every apply, propose, dispose or get for new-format asks then fails with 'identity', while ordinary ask commands accept them.
