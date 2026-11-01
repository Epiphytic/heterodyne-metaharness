Actual review invocation (historical evidence, never runtime configuration).

Original local invocation JSON SHA256: `76f92702ddb1c40fd8f44d1c31e86690b15210fab6846a970c8a2d6c3613b81b`.

```python
argv = [
    '/home/openclaw/.nvm/versions/node/v22.22.0/bin/claude', # install-agnostic: allow=home-path actual historical review argument
    '-p',
    '--settings',
    '{"disableAllHooks":true}',
    '--model',
    'opus',
    '--effort',
    'high',
    '--output-format',
    'stream-json',
    '--verbose',
    '--dangerously-skip-permissions',
    '--tools',
    'Read,Bash,Skill,Agent,ReportFindings',
    '--strict-mcp-config',
    '--mcp-config',
    '{"mcpServers":{}}',
    '--add-dir',
    '/home/openclaw/repos/heterodyne-deployment-helper-cancel-20261101', # install-agnostic: allow=home-path actual historical review argument
    '--',
    'Use your built-in /code-review skill with explicit args "high 155" for Epiphytic/heterodyne-metaharness PR155. This is SECOND full HIGH code review of small cancellation guard at frozen 233729ce313c4cfa93b7366f024d70d4062aa9e8. Read-only: no comments, pushes, edits, service changes or tests. Actual83 focused tests passed1.95seconds; Ruff passed, full quality report retained. First fullHIGH actualreport preserved under docs/reviews/2026-11-01-deployment-helper-cancellation/r1; repair moves bounded ordered issued-before-cancelled checks into _issue under caller-heldtargetlock, preserves O_EXCLfinalgate, typesunexpectedstaterrors, tests issued+cancelledUNKNOWNprecedence/otherpacketisolation. Actual83tests passed2.02s. Review cumulative diff and actualR1repair, stop ifcleared; do not invent broad newprojectrequirements. Existing roothelper is dormant and genuine producer/installed qualification remain separate implementation. Scope rootowner futureproducer must use same flock and neverdeletecancel. Review actual diff not hypothetical missingwholeproject; report real correctness/security flaws, concrete fixes, and whether source PoC may merge. Preserve actual model/effort/skill details.',
]
```

Frozen head: `233729ce313c4cfa93b7366f024d70d4062aa9e8`; started at 2026-11-01T21:31:45.905519+00:00.
