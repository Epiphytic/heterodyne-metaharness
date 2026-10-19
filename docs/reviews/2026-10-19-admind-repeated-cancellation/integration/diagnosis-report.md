# X84 integration test invocation diagnosis

Exact source: b4bebc6990110e42300137de57f91c84b71f63a7. No repository source or tests changed.

The early two broad-run failures are reproducible runner-path failures. Same existing approval-purpose node, interpreter and exact checkout imports; only basetemp changed:

- Long /tmp/x84-diagnosis-long-basetemp-20261019: exit1, first parameter failed,11.25s pytest duration.
- Short /tmp/x84dg: exit0, both parameters passed,4.38s pytest duration.

Exact argv, source, PYTHONPATH and subprocess elapsed times are in long.json/short.json; full outputs are long.log/short.log. Both subprocesses had external65s bounds and exited naturally.

The long-path trace ends at daemon.py:723 → ctl.py:114, sock.bind(str(self.path)), OSError: AF_UNIX path too long. The fixture first waits10seconds for fake wn-agent subscription, then exposes the failed daemon task during cleanup (tests/test_admind_daemon.py:158–163). Repeated failures therefore accumulate slow waits.

Canonical tests/conftest.py explicitly skips automatic short/tmpfs selection when --basetemp is supplied. Otherwise Linux uses writable tmpfs with at least1GiB free via tests/basetemp.py. Configuration documents WAL/fsync disk overhead; that is source-supported context, not measured overhead for this run. Root separately reports171PASS108.65s for the first three modules with shorter basetemp; do not add overlapping runs.

Use compatible Python3.12 with PYTHONPATH=<exact checkout>/src:<exact checkout>/tests. Shared editable environments can import another checkout, as the prior45collection-errors run showed. Prefer automatic basetemp or a short explicit disposable path after capacity checks. Root proposes /dev/shm/x84a.

Limits: this proves only the early two approval-purpose failure causes. Later failures in the interrupted broad run remain unclassified. Partial broad output is not a complete source baseline. No full suite rerun, source edits, native/backend or live service effects occurred. This report requires no production changes.
