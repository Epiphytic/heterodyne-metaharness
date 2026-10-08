# Review: ADR r14 docs follow-ups, r3

- Reviewed commit: `d121e40fcbe2ae9178dabd2d8e8c6bfa63df8f22` (branch `adr-r14-followups`)
- Reviewer: gpt-6.1-sol, reasoning high
- Verdict: APPROVE

## Review body (verbatim)

1. [NON-BLOCKING] `docs/admind.md:284` — **R2 finding 1 resolved.** Shutdown now stops admind before stopping the agent’s tmux server or scopes, preventing supervision from relaunching it. This matches `src/heterodyne/admind/daemon.py:1404` and `:1429`. No further fix required.

2. [NON-BLOCKING] `docs/admind.md:272`, `:277`, `:503` — **R2 finding 2 and the partially resolved R1 finding 1 resolved.** Missing replies now lead through Marmot `!interrupt`/`!new` recovery before host investigation. Silence diagnosis sends the initial operator message and `!ps` first. Setup and host recovery have explicit exception or break-glass context. No further fix required.

3. [NON-BLOCKING] `docs/install.md:9`, `:12`; `docs/security-model.md:34` — **R2 finding 3 / R1 finding 2 remain resolved.** These distinguish today’s bubblewrap default from the unbuilt OpenShell runtime, gated on S5, with bubblewrap fallback after S5 closes as failed. No further fix required.

4. [NON-BLOCKING] `docs/superpowers/plans/2026-10-04-heterodyne-plan-3-wsd-queue-state.md:19`, `:78`, `:99` — **R2 finding 4 / R1 finding 3 remain resolved.** “Pending” is explicitly historical; the follow-up records r14 approval as `btq-k942c`. No further fix required.

5. [NON-BLOCKING] `README.md:50`; `docs/configuration.md:59`, `:207` — **R2 finding 5 resolved.** The examples now identify bubblewrap as today’s recorded default and explain the S5-gated r14 target. No further fix required.

6. [NON-BLOCKING] `docs/adr/0001-workstreams-v2.md:1`; roadmap `:24`, `:35`; plan 3 `:101–105`; `docs/admind.md:77`, `:294`, `:506` — **R2 finding 6 revalidated; no new blocking findings.** The requested `cmp` exits 0. Launch receipts, legacy adoption, S5/S8 requirements, gatekeeper release timing, ten §17 decisions, picker/cancel specifications and section citations match r14. Source confirms `!asks` only accepts `bump`/`repeat`, no implemented picker kind, pane capture for `!tail`, Esc for `!interrupt`, passthrough and Stop-hook replies. Future features remain marked unbuilt. Normal operation uses Marmot; host procedures have setup, exception or break-glass context. All 47 relative Markdown links checked resolve, and `git diff --check` passes. No further fix required.

APPROVE