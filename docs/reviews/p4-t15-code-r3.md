# Plan 4 Task 15 code review, cycle 3

Reviewer: gpt-6.1-sol. Reviewed commit: daad0a8cce7b480c9a7311131bdc82990b9b9f51. Verdict: REVISE.

Note: this was the last cycle (the cap is 3). The finding is fixed in 99002ed, and 01bf94d extends it to three more checks on script output. The controller (Claude) reviewed both against the fix Codex stated; there was no 4th Codex cycle. The live run on 2247442 then failed n10: the sandbox refused even inspection of the probe's curl outputs (EACCES). d757486 accepts that only as hidden:EACCES, requiring both the readlink and the write open to be refused. gpt-6.1-sol checked that delta separately (p4-t15-code-n10.md).

1. **[BLOCKING] Sanitized messages still leak raw assertion operands.** `tests/live/test_live_sandbox.py:852` compares `rejections(made)` directly. On failure, pytest’s assertion rewriting prints the raw list alongside the sanitized message. A rejection such as `no <home>/.../claude ancestor inside the workload netns` therefore exposes the home path; an unexpected environment name containing a secret or 64-hex value is likewise exposed. The same bypass remains at lines 867, 871 and 1073. Cycle-2 finding 3 is not fully resolved. **Fix:** evaluate sensitive comparisons outside assertions, then use an explicit `if` and `pytest.fail()` containing only sanitized diagnostics. Apply this to all assertions involving raw launch reasons, rejection reasons or environment names.

The dead-socket derivation, diagnostic allowlists and earlier enforcement fixes otherwise hold on static inspection. No tests or live probes ran.

REVISE