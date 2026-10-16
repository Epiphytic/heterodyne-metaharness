# Plan5 executor design R1 author dispositions

Actual review of original source `79aec6f`: **REVISE**, round 1 of at most 3.
Its structured output has ten findings, despite the introduction saying eight
problems. All ten are accepted below. This is author disposition, not a new
review verdict or claim of passing implementation. Original source/report and
held Plan5a history remain intact; provenance is pinned in
[the d2 input manifest](plan5-actions-executor-d2-inputs.json).

| Finding | Applied design correction | Required future proof |
| --- | --- | --- |
| 1: two decision writers | §2/§6 choose all-kinds submission to the sole wsd writer; coordinated cutover fences every old direct writer. Current operator channel remains Marmot/admind only, terminal/forge decision entrypoints disabled. | Exact native finalization preserves approval_valid/pins; incomplete writer conversion keeps new queue/effects disabled. |
| 2: unwritten execution/results | §7 wsd alone durably writes Beads intent, attempt/claim, action state, results and closure. Effector is independently read-only, reports through authenticated bounded IPC. | Native conditional record/readback and report tuple binding must be demonstrated; no implied SQL, private effector authority store or caller-authored provenance. |
| 3: impossible reconcile argv | §7 sole privileged `run <approval-id>` reads explicit persisted initial/reconcile purpose. One-use claim is consumed/read back before effect; reconcile is read-only. | Concurrent launches, lost ack, restart and report authentication; launch identity must be proven across sudo/exec. Unproven contract keeps all effects disabled. |
| 4: self-ID whole ask | §4/§5 allocate one unposted/unroutable shell by durable origin key, then construct ID-bound whole ask, mandatory gates, install/readback, edge/park/card stages. | Unique-key native lookup, conditional install and partial-state visibility; ambiguous create never blindly repeats or posts/blocks. |
| 5: descendant push | §7 success is approved commit reachability with retained pre-effect tip, non-force ancestry and same target/attempt; later legitimate descendants preserve positive evidence. | Full readable ancestry; no force correction, speculative retry or shallow-history proof of absence. |
| 6: heart semantics | §6 heart commits ordinary approval and separately recoverable idempotent rule proposal; no silent rule application. | Proposal failure cannot undo decision or repeat effect. |
| 7: material resolution | §3 minor resolution only preserves an already uniquely specified canonical target. Ambiguous/new/changed target needs authenticated confirm before routing. | Resolver/fallback cannot guess a different repo/workstream into executable authority. |
| 8: enlarged interim exception | §5 mandatory ADR sufficiency before every new Plan5 action card/effect; existing interim relay remains unchanged until coordinated cutover. | No new type-specific exemption; unavailable gate means disabled action path. |
| 9: privileged local Git | §7 unprivileged credential-free bundle staging, regular no-symlink descriptor copy into private store; privileged Git reads only that private verified store. | Parser/config/transport/object-closure/size/time and replacement probes before adapter admission. |
| 10: impossible acceptance | §4/§8/§9 staged availability: all undefined adapters unavailable; candidate Stage1 push/PR/merge need their complete matrices and supported boundaries. Deploy/notify need separate contracts/approvals. | Stage0 offline only, Stage1 separately approved scratch acceptance only for enabled adapters; no nonexistent deploy/notify demo or whole-Plan5 completion claim. |

Governing ADR checked at `93d0e90b121a07db720d92924cda443a1cfc239c`:
§3.1 lines117–118 (single writer/separate user/sole launcher), §3.3 lines150–153
(authoritative Beads execution/results), §5.4 lines479–510 (all-surfaces serial
queue, actual decision readback, target reconciliation, heart proposal and interim
relay), §5.5 lines514–519 (forge single-queue architecture), §5.9 lines549–579
(whole-ask installation, digest and mandatory gate/limited interim exception).
No ADR amendment is implied by selecting its existing all-kinds submission option.
Wider surface enablement is not granted against the operator's current Marmot-only
instruction. New operator questions go through root's Marmot/admind flow; terminal
is diagnostics under existing authority, never consent.

This revision applies the prepared replacements and makes their disabled-path and
launch-binding requirements explicit. It invokes no review/model, tests, queue,
source implementation, service, push or live effect. Undefined native APIs must
be proven or concretely redesigned under review; prose is not evidence of support.
The exact approved supervisor archive remains byte-for-byte unchanged.
