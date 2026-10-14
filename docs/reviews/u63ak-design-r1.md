Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: a6cc5ddb02fc3b9726db30ad78a10056f43429b0 (range a6cc5dd~1..a6cc5dd). Verdict: REVISE.

Note: first design.

[BLOCKING] **Survey is incomplete — `tests/test_sandbox_probes.py:642`, `tests/test_admind_r13_summary.py:160` and `:354`.** The probe test spawns a 60-second grandchild before writing its PID file, then cleans up only if that file is readable: the same failure scenario this design addresses. The summary tests start background sleepers and rely entirely on production group cleanup; a cleanup regression leaves them alive after the assertion fails. Include these sites or justify their exclusion. The short-lived fork at `tests/fakes/fake_approve_bead.py:237` also needs explicit treatment before removing `kill_quietly`.

[BLOCKING] **The macOS kill path violates the required safety guarantee — Design → “Kill” and “Safety”.** A marked process can exit after `ps`, and its PID can be reassigned before `os.kill`; the helper then kills an unmarked process. The window is not bounded to microseconds, and existing unsafe cleanup does not establish safety. Specify a portable ownership mechanism, such as a supervising process retaining child handles, or explicitly resolve the weaker guarantee before implementation. The proposed tests do not exercise this race.

[BLOCKING] **Forked fake marking is underspecified — Design → “The fakes”; `tests/fakes/fake_approve_bead.py:148`, `tests/admind_asks_fixture.py:23`.** The wrapper currently supplies settings through environment variables. Adding `HZ_ESCAPE_MARK` there does not put it in argv; `os.fork()` inherits the fake’s existing, unmarked argv. If the parent dies before publishing `descendant.pid`, this child remains invisible to both sweeps. Specify how the mark enters argv before the fork, while preserving the fake’s argument parsing and logged-call assertions. Add a missing-PID regression for this fork path.

[BLOCKING] **Exact matching and session sweeping have contradictory expectations — Design → “Run-end sweep”; Tests → 4.** A bystander carrying `<mark>2` avoids fixture equality matching but still begins with `hz-escape.<run>.`, so the prescribed session sweep kills it although test 4 requires it to survive. Define separate exact-mark and run-mark predicates, including the valid counter syntax. On Linux, require equality against NUL-delimited argv entries; splitting flattened `ps` command text cannot establish argument boundaries.

[NON-BLOCKING] **The bound needs to cover discovery — Design → “Wait”; Tests → 5.** A monotonic polling deadline does not bound a blocking `ps` invocation. Specify a subprocess timeout using the remaining budget and fail visibly on discovery errors. The no-op-kill test proves only the polling deadline; add a stalled-discovery test.

[NON-BLOCKING] **The root cause is credible, but the proof claims exceed the proposed tests — Problem; Tests → 7–9.** The actual code confirms spawn-before-publication and non-atomic writes. However, scanning for this test’s mark when the mark was unset cannot detect an accidentally spawned **unmarked** child. Test refusal by observing that spawning never occurs, and cover `hold_stdout` and `descendant` too. Sessionfinish also cannot cover SIGKILL or an xdist worker crash whose run token disappears with that worker; qualify “always” accordingly.

The existing assertions can remain intact if cleanup stays after the test bodies. Atomic PID publication alone is insufficient. A small supervisor that owns the sleeper handles is a simpler adequate alternative where strict portable PID-reuse safety is required.

Verdict: REVISE