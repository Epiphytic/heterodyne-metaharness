# AU-2 config design review r1 (cycle 1 of 3)

Reviewer: gpt-6.1-sol (Codex, configured effort). Author: claude-opus-5-5. Reviewed commit: 39f2b82.

1. [BLOCKING] `docs/superpowers/specs/2026-10-08-au2-config-design.md:165` explicitly prints `login_dir`. ADR r14 D10 says no message shows a login path; the spec introduces an exception for local commands that r14 does not state. **Fix:** print `accounts.x.login_dir = <hidden>` with its source layer. This preserves AU-2’s provenance output without exposing the path.

2. [BLOCKING] `docs/superpowers/specs/2026-10-08-au2-config-design.md:100` requires `Path.resolve(strict=False)`, but line 111 specifies sanitized handling only for `stat` failures. Resolution can itself raise an exception containing the login path—for example, a symlink-loop error—and `config check` catches only `ConfigError`. **Fix:** require path-free `ConfigError` handling around directory and file canonicalization as well as alias checks. Add symlink-loop and resolution-error cases to the path-leak tests.

3. [NON-BLOCKING] `docs/superpowers/specs/2026-10-08-au2-config-design.md:141` does not define rejection of a non-table `[usage]` value. Existing merging permits a host leaf such as `usage = 1` to replace the defaults table. Implementers could reject it, crash while inspecting keys, or silently restore defaults. **Fix:** explicitly require `usage` to be a table before rules 19–23, with a `ConfigError` and a test through `load()`.

4. [NON-BLOCKING] `docs/superpowers/specs/2026-10-08-au2-config-design.md:235` — **Open question 1:** recommend rejecting explicit `default` in AU-2. Omission already provides default-login behavior, and postponing mixed named/default lists avoids adding continuity cases to this proof of concept. Make rule 13 explicitly reject `default` rather than describing it as both existing and unlistable.

5. [NON-BLOCKING] `docs/superpowers/specs/2026-10-08-au2-config-design.md:236` — **Open question 2:** recommend fixed `~/.claude` and `~/.codex`, ignoring adapter-specific environment overrides. Specify that named-account `~/` expansion also uses `paths.expand(value, env)`, so it follows the same HOME convention as implicit accounts.

6. [NON-BLOCKING] `docs/superpowers/specs/2026-10-08-au2-config-design.md:237` — **Open question 3:** recommend `ck1-` plus 32 hexadecimal characters. The adapter-qualified, canonical-path formula satisfies D1 and avoids the existing 64-hex redaction. Keep the formula identical between configuration resolution and launch-time recomputation.

REVISE