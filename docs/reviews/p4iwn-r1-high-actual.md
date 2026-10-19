I reviewed the full diff `a5ea9cb3..f5c24bdb` in `<operator-home>/repos/heterodyne-metaharness-v2-btq-btq-p4iwn`: the slice plan, `gateway_custody.py`, the control and CLI modules, the `ctl.py`, `daemon.py` and `cli.py` hunks, and the seven new test modules. I checked them against the approved Q1 ADR. Nothing was edited, run or probed. These findings were not put through a verify pass, so treat each one as plausible rather than confirmed.

The gaps you already listed stay open and are not repeated below: no production source/publication adapter (default HOLD), no native ACK/queue settlement, no physical 256KiB reserve, no intake crash-redelivery, and the unwaived quality-delta reports. This is not an approval and not a completion or deploy claim.

10 findings, most severe first:

1. `src/heterodyne/admind/gateway_custody.py:797`: A second valid `!custody settle` on an already consented proposal falls into the `state in ("awaiting-consent","consented")` branch and revokes the consent. The ADR only lets decline, revoke or a conflict block unissued authority ("Other commands never replace decision").

   Failure scenario: Operator A sends `!custody settle T` and the state becomes consented. Operator B (no unanimity is required) also replies `!custody settle T` in the notice thread with a new message id. The outcome is 'revoked' and the permit can never be issued, so an agreeing approval destroys consent. test_contrary_command_holds_unissued_authority_without_replacing_decision[settle] encodes this as expected, so the test hides the divergence from the ADR.

2. `src/heterodyne/admind/gateway_custody.py:528`: Renewal leaves the old permit live. The ADR says the product 'verifies both, tombstones old permit', but the code only swaps the current pointer. The generation-N immutable file stays published, nothing on the product side tombstones it, and renewal does not require the certificate's observed_boottime_ns to be at or after previous.initiate_before_boottime_ns.

   Failure scenario: Generation 1 is issued at t. A nonconsumption/superseded pair observed at t+5s renews to generation 2 at t+6s. Until t+30s, generations/<key>/1.json and permits/<key>.json (generation 2) are both valid unexpired permits. Whether that is safe depends entirely on an absent queue consumer honouring the superseded file, with no product-side tombstone record.

3. `src/heterodyne/admind/gateway_custody.py:666`: If the custody notice's outbox row ends up 'failed' (send_failed after MAX_SEND_ATTEMPTS), the proposal can never be decided. A duplicate registration returns the existing key without re-enqueueing, and the dead row still counts toward the 4096-total and 16-per-event caps.

   Failure scenario: Native send_final fails non-retryably once, so outbox status='failed'. Every later `!custody settle` hits thread-unmapped (no 'sent' delivery). `admind gateway-custody register` with the same descriptor returns 'registered' while doing nothing. The operator gets a misleading success and the event permanently loses one of its 16 proposal slots.

4. `src/heterodyne/admind/gateway_custody.py:782`: When the compare-and-swap fails with proposal-changed, an authenticated decline or revoke is dropped without being retained and without blocking consent. The ADR requires that decline/revoke/conflict always block unissued authority.

   Failure scenario: A settle and a decline from two operators are processed concurrently (the Service contract allows it; test_decision_cas_detects_concurrent_contrary_command shows one side gets proposal-changed). If settle wins, the decline raises Invalid('proposal-changed'), only an audit line is written, and the proposal stays 'consented', so a permit can be issued despite the decline. No native redelivery exists to retry it.

5. `src/heterodyne/admind/gateway_custody.py:751`: The replay-mismatch branch (and the capacity-hold branch at line 810) runs an unconditional `UPDATE ... SET state='revoked'`. There is no compare-and-swap, no state guard and no authority check, so it overwrites a 'terminal' or other settled state.

   Failure scenario: A proposal is in state 'terminal'. A redelivered native event reuses a retained message id with different text or reply_to, or a 'held' receipt pushes the bundle past 32KiB. The state is rewritten from 'terminal' to 'revoked', corrupting the retained final disposition.

6. `src/heterodyne/admind/gateway_custody.py:694`: The '<=256 undelivered' notice cap counts only outbox rows with status='pending'. Custody notices that never got delivered (status 'failed', or a held/delayed group) drop out of the count.

   Failure scenario: Native transport is failing, so each custody notice goes pending, then retries, then 'failed'. The pending count stays low, and registrations continue up to the 32/hour and 4096 limits while every notice is undelivered. The ADR's undelivered bound is not enforced.

7. `src/heterodyne/admind/gateway_custody.py:511`: issue_permit holds the shared Store RLock through recovery.publish/replace_current (several fsyncs plus readbacks) on the worker thread, which stalls the event-loop thread's own Store calls.

   Failure scenario: While a permit is published on slow storage, outbox_pass, on_message's claim_inbound and check_group's store.transaction on the asyncio loop block on store.lock for the whole fsync sequence. That freezes the subscription loop, against the plan's 'Blocking service work stays off the subscription event loop'.

8. `src/heterodyne/admind/daemon.py:2622`: This custody branch in on_message is dead code, and it runs under work_lock. on_event already sends every custody-command InboundMessage to custody_work before self.work, so this path is unreachable and only reintroduces waiting on the model lock.

   Failure scenario: Cost: a second dispatch path that contradicts the 'does not wait on the model work lock' invariant. Any future producer that feeds self.work directly (backfill or replay) would silently process custody commands behind a held model dispatcher. Delete it, or route it to custody_work.

9. `src/heterodyne/admind/gateway_custody.py:764`: The settle-branch source re-observation in message() repeats Service.observation() line for line, and register() repeats a subset of it. The 128MiB capacity arithmetic and its magic number are also repeated three times.

   Failure scenario: Cost: three copies of the source-binding check that can drift, so a fix to one (for example a boot or expiry rule) silently misses the others. Call self.observation(key, bundle), and add a shared MAX_TOTAL constant plus one capacity helper.

10. `src/heterodyne/admind/ctl.py:192`: The custody socket passes the raw line, including its trailing newline, to gateway_custody.decode(limit=MAX_REQUEST). The server's own check strips the newline first, so a request of exactly the 256KiB maximum is refused.

    Failure scenario: A client sends a valid request whose JSON is exactly MAX_REQUEST bytes plus '\n'. The server's `len(line.rstrip(b"\n")) > max_request` check passes, but decode sees MAX_REQUEST+1 bytes, raises Invalid('oversized'), and the client gets 'malformed request'. That is off by one against the ADR's <=256KiB framing.
