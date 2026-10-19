# R5 producer-fact framing repair

Actual prior review: FIFTH MEDIUM, report SHA256 4fc8a914db12137127dc53a934ded04d4b0d9986505cf708281cbc6d21bee51a. Offline PoC accepted, producer-text spoofing findings1–3 remained live-use HOLD. All previous reviews/findings remain retained.

The renderer now marks its local facts as unverified and prefixes every admitted title/body line with `| ` before the authoritative answer legend. This preserves each exact literal and distinguishes prefixed/alternative-delimiter/natural-language producer instructions from renderer-owned instructions without expanding the native answer grammar or relying solely on delimiter blocklists. Whole real-chunk framing still obeys36lines/3200characters; no truncation. Local facts still cannot supply links, Markdown, control characters or secrets. This is presentation, never authenticated authority.

The normal card already carries complete approval and denial grammar, so details no longer repeat three grammar lines. It retains the punctuation/variation-selector note. Conservative ordinary-fact refusals and other noncritical findings are OPEN; no automatic deployment or authority is implied.

Tests:21 corrected new regressions FAIL against exact signed predecessor, plus one separate details duplicate RED; candidate175 focused PASS. Initial RED/green with too-short new body fixtures is retained as a fixture error and not behavioral proof. All258 prior tracked test files except one are byte-exact. The sole change replaces a substring assertion in `test_ascii_facts_preserved_without_sanitization` with exact equality of every original title/body line after removing only the renderer-owned framing prefix. This strengthens retained-value coverage instead of deleting the oracle. Native V1/parser paths are unchanged.

Configured full typing406files0errors0warnings before removing only duplicate strings. Ruff/format/whitespace pass. Narrow quality3findings/1gate (short-horizon churn and2new pytest-dead-code findings) remain OPEN, as does all prior whole-change quality/coverage debt. Full admind rerun and actual SIXTH MEDIUM review remain required before integration; no review ordinal reset.
