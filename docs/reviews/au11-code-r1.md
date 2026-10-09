Reviewer: gpt-6.1-sol. Reviewed range: 58309dc..3d45f06. Verdict: REVISE.

1. [BLOCKING] `src/heterodyne/admind/settings.py:187` — Interpretation **(a) is wrong against approved §3.2**: inherited selectors must be refused for either process’s adapter, regardless of its configured account. Both named accounts currently allow startup with a stray `CLAUDE_CONFIG_DIR`; `tests/test_admind_settings.py:329` explicitly expects that deviation. Although the configured selector overwrites it safely, this changes the approved refusal contract. **Fix:** remove the default-account condition and update the test to require the path-free `ConfigError` and exit 78.

Interpretations **(b)–(i) are acceptable**. The period boundary strengthens redaction; the notice and replacement choices preserve durability and safety; the direct-start, patched-capture, and seeded-account tests are meaningful.

Static inspection and a mocked command check confirm the chained unsets preserve argv and the `hz-started` marker parser. Tests could not run because the sandbox denied temporary-file creation under `/tmp`.

REVISE