Skill "code-review" completed (forked execution).

Result:
```json
[
  {
    "file": "src/heterodyne/admind/fulfillment.py",
    "line": 29,
    "summary": "This sets ENABLED=True as a source-level constant. The approved design (f8a80b8, §1) says \"Implementation must still land disabled\" and requires a separate authenticated deployment decision to enable it; with a compile-time constant there is no configuration switch to turn it off again.",
    "failure_scenario": "Any merge or deploy of this tree turns on the fulfillment wire ops, the `!fulfill` inbound interception (daemon.py:2849) and the post_ask projection gate (daemon.py:1079) the moment the daemon restarts. Rolling back means a code change and redeploy, not a pinned config flip. That defeats the design's 'separate deployment pins and genuine consent' gate. A settings-level flag defaulting to False would keep enablement a deployment decision."
  },
  {
    "file": "src/heterodyne/admind/fulfillment.py",
    "line": 29,
    "summary": "With ENABLED on by default, post_ask calls require_projection() and refuses every question/merge post on any unmigrated store. Existing tests whose fixtures build unmigrated Stores were not updated and will now fail.",
    "failure_scenario": "tests/test_admind_management_presentation.py's `daemon` fixture uses a plain `Store(...)` and asserts `post_ask(...).result == \"posted\"`; it now gets refused with 'fulfillment-migration-required'. The same applies to test_admind_management_r1.py:69 and the Harness-based test_admind_asks.py tests (test_limits, test_post_refused_while_latched, merge card tests). In operation, a daemon restarted on this source before the quiesced migration blocks every controller question/merge ask. The diff only updates one test, and the README admits the broad suite was never green."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 1081,
    "summary": "Now that ENABLED is on, post_ask refuses question/merge posts whenever the projection is damaged (fulfillment-projection-unavailable). Design §4.2 says damage falls back to conservative unsuppressed counting and that 'only fulfillment mutations/verified lifecycle capabilities HOLD'.",
    "failure_scenario": "On a migrated store, a missing or modified fq2_* trigger or malformed projection meta makes validate() raise. capacity_count() would still work via FALLBACK, but post_ask returns refused('fulfillment-projection-unavailable') for every question/merge ask, a global posting HOLD that the design reserves for fulfillment mutations only."
  },
  {
    "file": "tests/test_admind_fulfillment.py",
    "line": 417,
    "summary": "The diff replaces the only assertion of the disabled on_fulfillment path ('unavailable' / 'capability disabled'), and no other test covers it. The `monkeypatch.setattr(f, \"ENABLED\", True)` on line 419 is now a dead no-op.",
    "failure_scenario": "`grep 'capability disabled' tests/` finds nothing, so a regression in the ENABLED=False guard of on_fulfillment (the rollback lever) would pass the suite. Apply/Propose/Dispose could then run on a host that is supposed to be disabled."
  },
  {
    "file": "tests/test_admind_fulfillment.py",
    "line": 431,
    "summary": "The uid/latch/work_lock test now applies before latching, so the latched, work_lock-held call only exercises duplicate()'s early 'already-applied' return. A fresh apply under latch and under work_lock is no longer tested.",
    "failure_scenario": "If Lifecycle.apply/commit started taking daemon.work_lock or being refused while latched, the test would still pass, because the latched call never reaches validate()/commit(). The property in the test's name is silently dropped."
  },
  {
    "file": "tests/test_admind_fulfillment_enabled_integration.py",
    "line": 80,
    "summary": "The test runs `migrate --apply` against a live socket server and an open daemon Store connection. The design requires stopped writers / supervised quiescence for this step, and the live connection never gets the dirty-notify temp triggers.",
    "failure_scenario": "configure_verification_notifications() ran when the store opened, before fulfillment_dirty existed. After an external CLI migration, the running daemon's own writes no longer wake the verifier, which falls back to the 30 s poll. The test makes live, non-quiesced migration look like a supported path instead of asserting it is refused or requiring a restart."
  },
  {
    "file": "tests/test_admind_fulfillment_enabled_integration.py",
    "line": 33,
    "summary": "socket_server hand-builds a JsonSocketServer instead of using the daemon's real ask_server wiring. Its request_type omits operations.Request and its describe differs, so this is not the 'actual daemon' socket it claims to test.",
    "failure_scenario": "daemon.py:734-741 uses request_type=asks.AskRequest | fulfillment.Request | operations.Request and asks.describe. If the production union or check wiring changes (e.g. tag collisions with operations.Request), these tests keep passing against the copied config while the real socket behaves differently. The cost is duplicated, drift-prone wiring."
  },
  {
    "file": "tests/test_admind_fulfillment_enabled_integration.py",
    "line": 68,
    "summary": "The enabled-default tests never assert the most user-visible default interaction: refused question/merge posting before migration, and the exact 'fulfillment-migration-required' message. The capacity test also never posts a new ask to prove a slot was actually released.",
    "failure_scenario": "The pre-migration Get check only asserts result=='unavailable', so a wrong code would still pass. Nothing checks that approvals still post while questions are refused, or that a 21st question is accepted after the exhausted-capacity apply. Design acceptance items 7, 8 and 10 for the enabled mode are left unverified."
  },
  {
    "file": "tests/test_admind_fulfillment_enabled_integration.py",
    "line": 189,
    "summary": "This git-archive / tar-extract / PYTHONPATH-subprocess legacy harness is the fourth copy of the same logic. Existing copies are in tests/test_admind_fulfillment_activation.py:221 and :267 and tests/test_admind_fulfillment_activation_r1.py:289.",
    "failure_scenario": "Four copies of the pinned-revision rollback harness, each with slightly different env handling (this one replaces the whole env; the others extend os.environ). A fix to one, such as cwd-independent `git -C <repo>`, will not reach the others. A shared helper in test_admind_fulfillment_activation would remove the duplication."
  },
  {
    "file": "src/heterodyne/admind/fulfillment.py",
    "line": 3,
    "summary": "The module docstring still says 'Disabled unless the daemon's explicit capability switch is enabled', but the switch is now on by default.",
    "failure_scenario": "Operators and reviewers reading the module will think fulfillment is off unless explicitly opted in, while every daemon on this source has it on. That misleads the rollback and deployment reasoning the design depends on."
  }
]
```
