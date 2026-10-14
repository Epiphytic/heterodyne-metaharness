Reviewer: gpt-6.1-sol. Author: claude-opus-5-5. Mode: cross-model. Range: 093ede58d4463dd3c267d18ae3a22a8201c5838f..46876581ce807bb1bf2dc7c5691104a312b6bf24. Verdict: APPROVE.

Note: cycle 2 of 3 of the btq-y3e88 design review.

No blocking findings. The r1 fixes are verified in `388f92e..4687658` and agree with the source.

- [NON-BLOCKING] **Negative and count assertions retain their strength.** Design “The fix,” steps 2–3; `tests/test_admind_r13_replies.py:298,314,328,364`. Keeping `batches()` raw catches an erroneous extra publication even when its acknowledgement is delayed or lost. Only `batch_arrives()` moves to `delivered_batches()`. Raw `texts()` and `listed_mid()` preserve absence checks.

- [NON-BLOCKING] **Both flake fixes match the actual ordering.** `tests/fakes/fake_wn_agent.py:191–196` records publication before replying; `src/heterodyne/admind/daemon.py:2811–2827` marks the row afterward. Waiting for matching key, `sent` status, and message ID closes that race. Likewise, `tests/test_admind_daemon.py:117` waits for subscriber registration, while `confirm_observing()` still awaits verification (`daemon.py:1754–1761`). `posting(h)` prevents the restarted `autobump()` checks from returning silently behind a closed gate. Waiting for reminder delivery before teardown stabilizes the restart state.

- [NON-BLOCKING] **The waits fail rather than conceal missing delivery.** `tests/test_admind_daemon.py:94–99` propagates timeout; `tests/test_admind_r13_replies.py:111–116` explicitly fails. If `mark_sent()` is omitted or records the wrong ID, the proposed helpers remain false until failure. The later status assertion becomes redundant, but eventual delivery remains tested. A direct status wait would be simpler for flake 2 alone; shared helpers are justified by the other affected callers.

- [NON-BLOCKING] **The permanent starvation regression addresses r1.** Design step 8; `tests/test_admind_r13_replies.py:429`; `daemon.py:2852–2862`. With a closed gate, the current `no_sleep()` and `relay_alerts()` can both complete without suspending. `await asyncio.sleep(0)` fixes that seam. External supervision catches a reintroduced spin when an asyncio timeout cannot run. The three knob regressions, helper checks, separate delay sweeps, and default offline suite are adequate design-level coverage; implementation must supply the promised pass/fail evidence. Zero-default knobs read only by the test harness leave production unaffected; nonzero CI values intentionally alter timing.

- [NON-BLOCKING] **“No production change” is justified, with the documented limitation.** `daemon.py:2827`; `store.py:647–665`; design “Production” and “Risks.” An operator reaction arriving before acknowledgement can be ignored. That is observable behavior, but it follows the existing sent-only recognition contract and message-ID flow. These tests should wait for that contract’s readiness state; supporting reactions to in-flight sends requires a separate production design.

Review performed by source inspection; no tests run or repository files modified.

Verdict: APPROVE