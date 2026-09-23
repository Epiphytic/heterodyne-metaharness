# btq-586 verification

The design was written before implementation. No live Marmot sends or service
activation were performed. Collection was read-only against local session quota
fields and ccusage summaries; private live data is not included in fixtures.

- targeted.log: 15 fixture/state/async delivery tests.
- full-suite-before-refactor.log: 420 tests passed, one skipped.
- full-suite.log: final code full suite (see result.json for verified outcome).
- governance.json: specification governance validation.
- quality.txt: non-green static analysis; remaining parser complexity and state
  class length are conscious tradeoffs. Dead-code findings include Python test
  discovery/dynamic adapter calls and unchanged test helpers; no unrelated tests
  were removed to satisfy the heuristic.
- change-check.txt: structural report sees only tracked spec/README.md because
  the sandbox prevents staging new files. It is not a complete diff assessment.
- permission-blocker.txt: Git index lock failure; no signed commit, push or PR
  was performed or recorded by the worker.

Run suite with:
`env -u PYTHONPATH -u HERMES_WORKSTREAM_RUN -u HERMES_WORKSTREAM_ROLE -u BTQ_SESSION_ID python3 -m unittest discover -s tests -q`

Manager deployment must set real token ceilings, full OPS/account IDs and check
socket permission. Estimates explicitly disclose daily-bucket five-hour upper
bounds. No guessed numerical ceilings ship enabled. health.json and the exact
[waiting-on-agent] OPS prefix follow the manager clarification; channel setter
work remains btq-2sa. The timer and service are supplied, not installed.
