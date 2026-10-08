# Review: ADR r14 docs follow-ups, r2

- Reviewed commit: `9b856c50b27389f76dc4c00b4702508b65dfc2be` (branch `adr-r14-followups`)
- Reviewer: gpt-6.1-sol, reasoning high
- Verdict: REVISE

## Review body (verbatim)

1. [BLOCKING] `docs/admind.md:284` — The shutdown instruction does not take admind out of service. Both commands stop only the agent’s tmux server/scope; the running supervisor can relaunch it (`src/heterodyne/admind/daemon.py:1404`, `:1429`). **Fix:** explicitly stop `heterodyne-admind.service` first, then kill the private tmux server or stop its scopes.

2. [BLOCKING] `docs/admind.md:272`, `:277` — **R1 finding 1 is partially resolved:** host procedures now have labels, but these triggers still escalate ordinary operation prematurely. An inconclusive `!tail` leads to host logs before trying the documented Marmot recovery. “Nothing on Marmot can help” is also false before the first operator message: that message enables posting (`src/heterodyne/admind/daemon.py:1794`). **Fix:** try appropriate `!interrupt`/`!new` recovery before host investigation, and send `!ps` first when diagnosing silence. Reserve host steps for unavailable or unsuccessful Marmot recovery.

3. [NON-BLOCKING] `docs/install.md:9`, `:12`; `docs/security-model.md:34` — **R1 finding 2 is resolved.** These now correctly distinguish the S5-gated OpenShell target, bubblewrap fallback, current setup default, and unimplemented runtime. No further fix required here.

4. [NON-BLOCKING] `docs/superpowers/plans/2026-10-04-heterodyne-plan-3-wsd-queue-state.md:19`, `:78` — **R1 finding 3 is resolved.** “Pending” is explicitly historical and points to the approved r14 follow-up. No further fix required.

5. [NON-BLOCKING] `README.md:50`; `docs/configuration.md:59`, `:198` — The bubblewrap examples accurately describe today’s code (`src/heterodyne/platform.py:13`). However, configuration’s unqualified §3.2 citation now points to the conditional OpenShell design. **Fix:** retain the examples and add a brief note identifying bubblewrap as today’s recorded default, with the r14 runtime gated on S5. A matching README note would help.

6. [NON-BLOCKING] `docs/adr/0001-workstreams-v2.md:1`; roadmap `:24`, `:32`; plan 3 `:99`; `docs/admind.md:77`, `:294`, `:506` — Verification passed: the requested `cmp` exited 0; launch receipts, legacy adoption, S5/S8 gates, gatekeeper release timing, ten §17 decisions, picker/cancel specifications, and section citations match r14. Code confirms the documented current command restrictions, absent picker kind, tail/Esc behavior, passthrough, and ask reconciliation. Prose relative links resolve, and `git diff --check` passes. No fix required for these checks.

REVISE
