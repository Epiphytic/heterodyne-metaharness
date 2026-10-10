Reviewer: gpt-6.1-sol. Reviewed commit: 10b9f8e. Verdict: APPROVE.

1. [NON-BLOCKING] `src/heterodyne/session/shim.py:156` — `_bounded` checks encoded size but ignores the envelope’s added nesting. An accepted depth-128 event becomes depth 129 on the wire; an in-memory reproduction returned `malformed`. This unnecessarily denies tools and silently loses lifecycle events near the depth limit. **Fix:** check the wrapped message’s depth alongside its size, projecting when either exceeds the server’s limits. Add a depth-boundary integration test.

The previous blocking findings and configuration-spooling defect are fixed. Missing-home behavior now has explicit coverage. The tests contain meaningful assertions, and projection is compatible with Task 5’s current allow-all policy. No blocking PoC defects remain.

Full pytest execution was blocked by the environment denying `/tmp` creation. In-memory projection, deadline, malformed-field, and path checks passed.

APPROVE