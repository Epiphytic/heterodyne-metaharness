# Round-one repair: typed reasoning and profile validation

This source follows actual Claude built-in code-review round one HIGH on `df968b3798b8bfb42747ac9307ef2e5d279c148e`. Its actual verdict was **CHANGES REQUESTED**, preserved verbatim in `r1-actual-report.md`. The accompanying artifact-hash receipt identifies the unchanged controller command/start/result/stream files. No review has been repeated or reset by this repair. Round two remains the controller's review gate.

The source commit is `2f4dc9b9c2ae95dac9ac72c21279ebb8ff5804d8`, tree `9561818c2e40a449225c30bf1cb6c75c5e800f7c`, based on frozen round-one head `df968b3`. This repair supersedes the historical source-scope claim that server-home reasoning configuration establishes resumed native-thread behavior. Historical qualification logs and the actual original review remain intact.

## Findings addressed

1. Fresh native sessions receive the finite typed server-home setting before sandbox creation. Actual native continuation with a typed setting now raises LaunchFailed until the selected Codex CLI's continuation effort is causally qualified. The runtime uses its existing state reader and resume decision; the caller's resume flag alone does not establish native continuation. A live session remains idempotently unchanged. Tests exercise retained native state with both caller resume flags and require unchanged retained record/home with no cleanup or sandbox creation. Test backends establish the hold and configuration ordering, not actual Codex inference effort or OpenShell isolation.
2. Profile-key validation now rejects unknown keys including a misspelled reasoning key. The allowlist preserves existing `accounts` and `failover`, in addition to `adapter`, `model`, `args` and `reasoning_effort`; these existing account-routing fields are consumed by config/accounts and wsd/accounts. Dedicated tests and the existing account suites preserve that contract.
3. Unsupported adapters and invalid effort values have distinct error messages. Neither values nor generic configuration overrides gain authority.
4. The introduced runtime test uses module imports, the existing quote style and pytest monkeypatch rather than direct method replacement with a type-ignore.

The nonblocking shared adapter parameter-count finding remains disclosed by the original qualification. This narrow repair does not redesign that reviewed interface or open the generic argument allowlist. No actual sandbox, service, account, deployment, live queue operation or runtime adoption occurred.

## Qualification

The repair receipt pins exact changed source hashes and final command/log results. The focused adapter/config/content suite passed 312 tests in 6.45s; existing account configuration/routing suites passed 103 in 0.32s. Ruff on all five changed Python files passed; Pyright using the explicit shared test interpreter reported zero errors and warnings. Commit hooks passed. The complete sandbox-runtime suite passed **274 tests in 472.90s**. Its actual log is retained.

Incremental `ripwire src/heterodyne --quality-delta` compared the uncommitted repair to the actual frozen parent and exited zero with three minor findings: launch complexity27→28, launch LOC60→66, existing runtime-class LOC1361→1367. This is not cumulative quality clearance: the original shared-signature debt and prior exit2 report remain unchanged.
