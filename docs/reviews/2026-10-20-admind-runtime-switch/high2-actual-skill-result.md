Skill "code-review" completed (forked execution).

Result:
I reviewed the uncommitted working-tree changes. They replace the `fulfillment.ENABLED` module constant with a per-daemon `[admind] fulfillment_enabled` setting. The range against upstream covers 844 files, so I kept the review to the working tree. The 236 tests in the affected suites pass, and ruff and pyright are clean. No CLAUDE.md in the repo governs this code, and the global ones have no rule this diff clearly breaks, so the conventions angle found nothing. The first two findings are availability problems that turning the switch on in production can actually trigger. The rest are lower-severity design, efficiency and cleanup points.

```json
[
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 1079,
    "summary": "If fulfillment_enabled is true and the store has not been migrated, post_ask refuses every question and merge ask with 'fulfillment-migration-required'. Nothing at startup catches this mismatch, so a config mistake turns into a silent outage instead of a startup error (EX_CONFIG).",
    "failure_scenario": "An operator sets `fulfillment_enabled = true` and restarts before running the quiesced `admind fulfillment migrate --apply`. The daemon starts normally, but every controller question or merge post is refused until someone notices. A projection that is actually damaged is tolerated ('fulfillment-projection-unavailable' falls through to the conservative count), yet the safer unmigrated state blocks all posting. Fix: check the projection in recover()/run() and fail startup, or handle migration-required the same way as projection-unavailable."
  },
  {
    "file": "src/heterodyne/admind/cli.py",
    "line": 517,
    "summary": "The running daemon calls the full settings.resolve() again on every ingress (load_operators → refresh_authority). Writing the new key, or writing it with a typo, changes the running daemon's operator authority straight away, even though the docs say the setting takes effect only after a restart.",
    "failure_scenario": "Deploy and enable happen in one step: the key is written while the old daemon is still running, before it restarts. The old reader rejects the unknown key, refresh_authority sets operators to {} and audits policy-unreadable, so every operator command is denied until the restart. Separately, writing `fulfillment_enabled = \"true\"` (a string) into a running new-code daemon's config also locks out all operators, even though the flag itself is not reread until restart. The docs ('Upgrade every shared-config reader before writing the key') never say the upgraded process has to be restarted first."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 982,
    "summary": "The disabled gate also blocks the read-only Get and Inspect requests, which grant no authority. After an unplanned disable (an emergency rollback), there is no supported way to inspect unresolved outcomes or pending notices.",
    "failure_scenario": "An operator urgently removes fulfillment_enabled and restarts while intents are pending or outcomes are unknown. `admind fulfillment get <id>` and `inspect` now return 'capability disabled', and reconcile stops. The only way to see the state is to re-enable the producer capability that was just turned off. Fix: put only the mutating requests (Apply/Propose/Dispose) behind the flag."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 1081,
    "summary": "With the flag on, every question and merge post runs the full projection validate(): validate_schema_db, one sqlite_master lookup per DDL statement, a trigger count and a meta read. This runs synchronously on the event loop under ask_post_lock.",
    "failure_scenario": "Once enabled in production, each AskPost runs about 20 or more schema queries before the capacity check, and other ask-socket work waits behind them. This path never ran before because ENABLED was hard-coded False. A cheaper approach is to validate once at startup or after migration and cache the result, or to rely on the projection-unavailable fallback that already exists."
  },
  {
    "file": "src/heterodyne/admind/cli.py",
    "line": 518,
    "summary": "The daemon's start audit record does not include the fulfillment_enabled value it resolved. Because a change needs a restart, the value in config.toml and the value in the running daemon can differ, and nothing durable records which mode the daemon is in.",
    "failure_scenario": "Someone edits config.toml to true but does not restart. `admind ask list` and other CLI readers resolve true while the daemon still runs disabled. Operators cannot tell from audit.jsonl which mode produced a given 'capability disabled' or 'migration-required' refusal. Fix: add fulfillment_enabled to audit.write(\"admind\", action=\"start\")."
  },
  {
    "file": "tests/test_admind_fulfillment_activation_r1.py",
    "line": 214,
    "summary": "test_verifier_runs_with_producers_disabled_and_wakes_without_poll_delay lost its setup line that disabled producers, and nothing replaced it. The test name now claims a condition the test never creates.",
    "failure_scenario": "The removed `monkeypatch.setattr(f, \"ENABLED\", False)` was the only line that established 'producers disabled'. The test now runs on a bare Store with no daemon settings, so it would still pass if a regression made the verifier worker depend on daemon.s.fulfillment_enabled (for example, starting it only when enabled). ADR §4.3 requires the verifier to be independent of the flag, and this test no longer covers that."
  },
  {
    "file": "src/heterodyne/admind/settings.py",
    "line": 90,
    "summary": "The default for fulfillment_enabled is hard-coded inline in resolve() with .get(..., False). Every other [admind] scalar takes its default from defaults/defaults.toml, and the new test asserts the key is absent from defaults, which locks in the inconsistency.",
    "failure_scenario": "The default lives in two places: the dataclass field default and the inline .get default. It also bypasses defaults.toml, which is where the other admind defaults are documented and merged. defaults.toml ships with the same code as the reader, so listing the key there would not affect rollback. A future reader that iterates defaults to find the [admind] keys will miss this one."
  },
  {
    "file": "tests/test_admind_fulfillment_enabled_integration.py",
    "line": 26,
    "summary": "Removing the monkeypatch.setattr calls left nine test functions or fixtures that still request a monkeypatch parameter they no longer use.",
    "failure_scenario": "The dead parameters remain in test_admind_fulfillment.py:407, 441 and 513; test_admind_fulfillment_activation.py:600; test_admind_fulfillment_activation_r2.py:72, 159 and 256; test_admind_fulfillment_enabled_integration.py:26 (the unit fixture); and test_admind_fulfillment_r1.py:89. They suggest the tests still patch global state when they don't, which is misleading noise for the next reader."
  }
]
```
