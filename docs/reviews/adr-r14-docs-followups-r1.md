# Review: ADR r14 docs follow-ups, r1

- Reviewed commit: `2a19275bd578f683f87b2797303d0ccc9065d344` (branch `adr-r14-followups`)
- Reviewer: gpt-6.1-sol, reasoning high
- Verdict: REVISE

## Review body (verbatim)

1. [BLOCKING] `docs/admind.md:77`, `:249`, `:282`, `:483` — The new Marmot-only guidance conflicts with instructions to read logs, stop the agent or investigate decisions directly on the host, without clearly labelling those procedures as break-glass. Make Marmot the normal diagnostic path and explicitly label direct host recovery steps and their triggers.

2. [NON-BLOCKING] `docs/security-model.md:34`, `docs/install.md:9` — These pages still describe bubblewrap as the unconditional Linux target, contradicting the updated roadmap and ADR §7. Describe OpenShell as gated on S5, with bubblewrap only after S5 is closed as failed; distinguish that target from today’s setup defaults.

3. [NON-BLOCKING] `docs/superpowers/plans/2026-10-04-heterodyne-plan-3-wsd-queue-state.md:19` — The progress amendment `btq-xv48a` is still described as “pending”, although r14 incorporates it and line 99 records r14’s approval. Qualify this as historical wording and point to the approved r14 follow-up section.

REVISE
