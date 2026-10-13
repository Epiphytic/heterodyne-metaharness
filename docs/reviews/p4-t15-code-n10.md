# Plan 4 Task 15 code review, n10 delta (2247442..b96d6e0)

Reviewer: gpt-6.1-sol. Reviewed commit: b96d6e061add2f7d06c15ca204f6816174bdc31f. Verdict: approve.

Note: a focused check of the n10 change after the live run on 2247442, not a 4th full cycle. The nit (EACCES alone does not identify the enforcing mechanism) is deferred and stated in the addendum approval.

1. [NIT] This broadens acceptance and loses proof that the fd is a socket, but still requires an actual write-open denial. Readlink EACCES followed by a successful open produces `hidden:OPENED` and fails; another open errno also fails ([test_live_sandbox.py:1143](~/repos/heterodyne-metaharness-v2-p4t15/tests/live/test_live_sandbox.py:1143), [probe_verdicts.py:265](~/repos/heterodyne-metaharness-v2-p4t15/tests/probe_verdicts.py:265)). EACCES alone cannot identify the enforcing mechanism, so erratum 9’s “across the sandbox boundary” is stronger than the evidence ([plan:9862](~/repos/heterodyne-metaharness-v2-p4t15/docs/superpowers/plans/2026-10-09-plan4-agents-and-sandbox.md:9862)). No new bypass demonstrated; alternative fd duplication is covered separately.

2. Coverage is adequate: OPENED, other errnos, `error:*`, all-gone, no curl, and partial observations fail ([test_probe_verdicts.py:145](~/repos/heterodyne-metaharness-v2-p4t15/tests/test_probe_verdicts.py:145), [test_probe_verdicts.py:173](~/repos/heterodyne-metaharness-v2-p4t15/tests/test_probe_verdicts.py:173), [test_probe_verdicts.py:180](~/repos/heterodyne-metaharness-v2-p4t15/tests/test_probe_verdicts.py:180)). All 126 offline tests passed.

3. No raw assertion leak found. n10 asserts only the sanitized verdict; rejected lines pass through `shown()`. Evidence output contains counts only ([test_live_sandbox.py:1210](~/repos/heterodyne-metaharness-v2-p4t15/tests/live/test_live_sandbox.py:1210), [probe_verdicts.py:52](~/repos/heterodyne-metaharness-v2-p4t15/tests/probe_verdicts.py:52)).

Verdict: approve