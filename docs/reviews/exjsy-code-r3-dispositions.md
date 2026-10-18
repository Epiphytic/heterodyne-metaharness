# Third actual MEDIUM review: author dispositions

Actual result REVISE; original full report SHA256 2d2dbe0864452463d37858ce3994718b06b65876ea1c4bf3eb457c66df8455e1. All prior reviews and ordinals retained. These dispositions are author work, not reviewer approval. Next actual review is FOURTH MEDIUM using the operator's exact PoC phrase.

1. Restored ordinary R26 restart settlement. Initial issue_decision now records decision-issuing, distinct from maintenance retries. After an actual validated HOLD result, a separate exact-attempt CAS commits maintenance-issuing before overlay/notice publication. Ordinary cancellation after the real on_launch callback is exercised with a reopened Store for untouched/open, foreign/blocked, and exact/approved outcomes. No automatic decision replay is introduced. If the process or storage fails before the post-HOLD marker commit, restart has only ordinary unknown issuance; it does not acquire proven-HOLD retirement authority. New proof coverage and retirement must compose before deployment.

2. Accepted existing limitation; not permission to activate this version alone. Suspended stale/gate/native/authority rows retain the original consent and occupy existing MAX_OPEN slots. Separate recovery design aa860fe7b16cfbe452c7bae4ba18cf285c177a7c has one actual HIGH REVISE review, author incorporation once, and pending genuine approval btq-m727i/ask9567, targetbtq-4kcjg. No retirement implementation or authorization is fabricated here. Historical/ambiguous attempts still require a separate approved recovery path if encountered before deployment.

3. Latch is now a permanently suspended reason. Daemon.latch atomically suspends all existing maintenance overlays with its latch state, so rearm before the next retry cannot reuse their old consent. A real validated HOLD observed while latched also suspends. Regression covers both with and without a retry while latched. Temporary group observation failures remain retryable as required by R2; they are not attributed as proven membership revocation.

4. Corrected cancellation documentation: ordinary decisions settle through R26 readback; interrupted maintenance retries retain prelaunch uncertainty and cannot auto replay. Regressions start from the real run_attempt child boundary rather than seeding synthetic outcome-free rows.

5. Ordinary stranded decisions no longer enter this path. Remaining maintenance-issuing uncertainty still permits readback per explicit !asks/startup backstop and can settle an exact native result; it does not issue another decision. Noncritical bounded-load/terminal-recovery limitation retained, not represented as solved by the 30-second overlay retry loop. Retirement proof design and any unknown-effect recovery remain separate.

6. Failed issue CAS now finishes the original inbound with the existing fixed CONFLICT reply, matching the later settlement-CAS path. Offline regression asserts no child launched and inbound done with a conflict response.

7. Updated issue_decision and the proven-HOLD marker commit to update updated_at. Internal outcome state remains in the existing asks column; moving to a new overlay schema is deferred rather than disguising a schema redesign as a critical repair.

8. Retained noncritical defensive second authority/preflight check immediately before issuance. The first check precedes awaited native readback; current authority must be refreshed afterwards. It does duplicate some policy I/O and complexity; these quality findings are unwaived.

9. Removed issue_maintenance_retry dead compatibility API. Existing tests now exercise the actual production issue_decision(..., maintenance_retry=True) API for exact-CAS and one-time overlay consumption.

10. Retained the small service-local duplicate-key decoder. Importing actions.codec private helpers would couple admind service parsing to the action interpretation layer; no new general codec API was designed here. NaN/Infinity cannot equal the closed three-string dictionary and are refused; no permissive HOLD proof or differing accepted bytes are claimed. Deduplication remains a noncritical quality finding.

No deployment, native decision, service activation, production DB migration, or recovery authority introduced. Existing quality debt is UNWAIVED. New design/recovery and compatible queue/Hermes composition are required before requesting a separate deployment approval.

Current repair-only quality comparison against signed R2 HEAD1701018 exits2: 21 findings,16gating, versus prior full-range45gates58rows (not waived by changing the comparison floor). run_attempt complexity51 to55 retains explicit proof publication outside the ambiguous-child exception handler and reports both issuance CAS failures. Added callback-bound restart tests use three explicit async scenarios; reported clone/dead-code rows include nested-pytest symbol aliases. No claim that these numbers establish clean code quality.
