# Admind runtime switch: source gate

Genuine design approval p5r9 / btq-pzr7d, digest 9101289f687e; approved revision aee72a40ec37f051456f3d7c1cfb36abd451ff56. Code-Review: reviewer=claude-opus author=codex mode=cross-model range=018ebc94b1e4b66bd6431a54cc68ffa20e6097aa..frozen-15-file-candidate

Actual built-in Skill(code-review), first FULL HIGH REVISE, second FULL HIGH APPROVE source only; exact wrapper and fork outputs retained. Wrapper adjudicates design-chosen behavior against the governing ADR. R1 findings 1–4 fixed; unused fixture parameters, inline bool validation, test resource setup ordering and explicit restart wording remain OPEN low/nits. Prior lifecycle/fallback findings remain OPEN and this does not supersede them. Reviewer skill ran tests despite readonly instructions; source remained hash-exact. Its count is not author verification.

Author verification: 282 compatibility tests pass, 15 new tests pass, scoped types zero errors/warnings, Ruff and edited-test formatting pass. Eight original test modules migrate only the flag owner; the original assertions remain. All 294 original test-file counts and 12,091 assertions audited, seven AST comparisons exact with one explicitly migrated ENABLED assertion. Four original formatting failures remain baseline OPEN. Quality exit 2, 16 major gates, remains OPEN without suppression or waiver.

This merges source only. Packaged defaults omit the key; missing means False. No live key write, migration, activation, producer enablement or deployment. Every shared-config reader must be upgraded and restarted before writing the key. Remove it before rollback to an old reader, preserve newest DB/history, settle unknown outcomes while enabled. The separately approved deployment remains exact 71713d8; this candidate cannot silently be substituted.

Public integration parent840de44 has a byte-identical tree to reviewed base018ebc94; all15 frozen source hashes match. Ripwire records broad name-based daemon reach (6854 dependents/275tests), not runtime proof; 282 compatibility and15 new tests cover the setting. New untracked test is absent from the ripwire working-tree map.
