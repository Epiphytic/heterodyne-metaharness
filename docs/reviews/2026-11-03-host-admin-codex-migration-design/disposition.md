# Sole design review disposition

One actual independent Claude Opus HIGH review at frozen ee3cb01, exit0, ended08:03:04Z, 319 events. Actual verdict: CHANGES REQUIRED. Incorporated once by the author; no second review or validation. Reports and start/result metadata are preserved here; raw stream remains in the durable controller handoff. No reviewer APPROVE is claimed. Human design decision is separate.

- C1: replaced stdio child with private Unix app-server inside existing admin tmux scope, socket bounds, no shared-daemon attachment, retained restart hold semantics.
- C2: removed infeasible post-restart fence/quarantine/requeue proposal. Required pre-stop idle/no-in-flight/no-hook-loss/no-launch-hold evidence and explicit genuine deployment approval of legacy restart dispositions. Retain durable input evidence; never replay old input. Added a stop-before-deploy condition if existing storage cannot preserve required content. Existing uncertain effects are never cleared. This preserves the operator requirement instead of silently treating queued input as surviving a restart.
- C3: tied migration to genuine native deployment approval and existing approval-operation lifecycle; a supplied reference alone is insufficient. The native approval's exact ask digest binds its descriptor, rather than pretending arbitrary descriptor hash is an approval AskRow digest. Added atomic finite consumption and verifier implementation as an effect prerequisite. Reuse existing new_session/new lifecycle; no parallel writer or synthetic approval.
- C4: config.toml is the only profile selector; Store records observed binding. Reuse adapter credential-key mismatch guard.
- I1: named typed daemon events, turn_lock, request anchor, terminal record/no fallback, pending transport _flush gate, synchronous command bridge, uncertain writes and interrupt/stale completion. Documented NO_TOOL_CALLS limitation.
- I2: separate issued launch ID from actual assigned native thread; transactional acknowledgement with updated matching predicates.
- I3: effective-config preflight/allowlist; strict explicit overrides; no global config/hook mutation, bypass-hook-trust or credential copies. Provisional account override qualification remains required before effects.
- I4: check all five effective startup fields, reviewer=user, exact never, retained rollout, non-authoritative clientUserMessageId, no steer, bounded refusal of unsupported server requests and nonterminal inProgress.
- I5: admin capability widened separately; summarizer remains Claude only.

PoC advisories incorporated: exact binary/schema fixture, fake protocol coverage, developerInstructions handoff, restart-midturn and live effective-policy evidence. Owner recovery PR301 and original Ask bridge PR302 remain independent and unchanged. Code and deployment gates have not passed.
