**Verdict: REVISE.** I ran the review with the built-in `Skill(code-review)` at high effort, not the plugin version. It reviewed only the frozen draft (`draft.md`, SHA256 `f6a043fe…65bd5`, which matches the hash you gave), along with `review.diff`, `README.md`, the evidence manifest and runtime.diff. Nothing was edited, tested, run or queried live.

**What checked out.** The draft's description of the source change is accurate against git. 21 runtime files change. The only admind change is `fulfillment_projection.py`. `deploy/` and the lock are unchanged. `ENABLED=False` holds. Admind settings stay strict and have no `fulfillment_enabled`.

**Implementation defects vs. missing capabilities.** All 10 findings are gaps in how the procedure uses the existing installer, guard and units. None asks for a new capability, an invented proof, or caller JSON treated as authority. The review found no owned production code path that is unavailable everywhere.

### HIGH
1. **(draft.md:48) Recovery depends on the service being stopped.** Every HOLD during the outage (verifier won't drain, guard refuses, installer refuses or fails) leaves admind stopped. Admind carries Marmot, yet §5 sends recovery through a Marmot disposition request. If `install.sh` refuses early (install.sh:345-433), nothing has changed but admind is down, so Liam can't be reached and the outage has no end. If it fails after `write_unit` (install.sh:843-890), the 717 unit has `Restart=always`/`RestartSec=5` (admind/unit.py:17-23) and keeps reopening the live DB while "held".
   - **Fix:** get consent in advance for two aborts: restart the unchanged 5db unit if the failure comes before the first change, and an explicit `systemctl --user stop` if it comes after.
2. **(draft.md:63) No pre-outage `install.sh --dry-run` and no acceptance rule.** Without a dry run, every refusal or unexpected `would:` line only appears after the transport is down, and the outage also has to absorb two full uv syncs. Exit 3 with "skipped (admind init has not run)" (install.sh:498, 884-887) could pass as a non-failure. Under `--no-wsd` the summary says "skipped" even when admind restarted (install.sh:904-905).
   - **Fix:** require a dry run before the outage with a reviewed `would:` list. Accept only exit 0 plus the literal line `ok: heterodyne-admind.service is up and runs the render`.
3. **(draft.md:74) The installer's `store` step is left out of the effects list and the consent.** `setup-store.sh:205-227` can run `useradd --system heterodyne-store` and overwrite root config, the helper copy and the socket drop-in. It can then restart `heterodyne-store-helper.socket` (install.sh:596-611). This contradicts §3's "not authority to initialize a new account/group".
   - **Fix:** require the dry run to show `ok:` on every store and units line, and HOLD on any `would:` there that consent doesn't name.

### MEDIUM
4. **(draft.md:21) The "no automatic kill" promise can't be kept.** The unit sets no `TimeoutStopSec`, so systemd SIGKILLs after about 90s (`KillMode=control-group`, unit.py:21). The 5db process being stopped also lacks the repeated-cancel repair, so it can hang on shutdown.
   - **Fix:** send exactly one stop and record `Result`/`ExecMainStatus`. Treat a timeout or SIGKILL as a non-clean stop and HOLD before the guard.
5. **(draft.md:48) The quiescence step doesn't name the writers.** Three writers known from the source can still change the DB after the guard's snapshot:
   - `admind` CLI commands open Store on admind.db directly (cli.py:72-77).
   - The admin agent's tmux session deliberately survives an admind stop (unit.py:18-20).
   - WSD and hooks post through `ask.sock` and alert files.
   - **Fix:** list these clients and stop or block each one, or HOLD.
6. **(draft.md:40) The WSD handling is incomplete.**
   - `--no-wsd` turns off the installer's own outside-WSD probe (install.sh:183), so the draft has to run `platform_state.py wsd-lock` and `record-show` itself.
   - There is no rule for an active managed WSD, which the installer stops mid-run.
   - Under `--no-wsd` the WSD unit is never re-rendered, so it still runs the 5db release while `record.release` becomes 717. §2's "internal owner remains the default" therefore describes 5db code, and pruning the 5db release would break the unit.
7. **(draft.md:82) Rollback doesn't stop the 717 admind and other writers before the "newest" snapshot.** "Restarts the 5db reader against that newest preserved schema/history" can also be read as copying the snapshot into place, which would lose answers that §5 forbids losing.
   - **Fix:** repeat the step A quiescence on 717, take the snapshot only as evidence, keep running on the live DB, and stop any `Restart=always` loop first.
8. **(draft.md:66) The root step breaks the runbook's single block.** `bash "$ROOT_PRIVATE_VERIFIED_INSTALLER"` turns the README's one copy/hash-check/run block into separate steps. That opens a time-of-check/time-of-use gap, and the EXIT trap deletes `$b` anyway. The generated `--service-user` comes from the guard runner's own user ID (prepare_admind_guard.py:111). It also conflicts with the CLAUDE.md rule that root changes go into one sudo script.
   - **Fix:** use the README block verbatim with ` --no-wsd --no-verity` appended and sums from `manifest.sh`, delivered as one root script plus one command.

### LOW
9. **(draft.md:46) Pre-creating the output directory makes the guard fail.** The guard runs `output.mkdir(mode=0o700)` with no `exist_ok` (prepare_admind_guard.py:79). If the operator pre-creates the directory as described, the guard raises `FileExistsError`, both in the outage window and at the rollback guard.
   - **Fix:** specify a path that doesn't exist yet, inside an existing private parent.
10. **(draft.md:90) "Dormant Plan8" isn't verified.** The unit puts the 717 venv first on `PATH` (units.py:52-59), so child processes can reach `babysitter`, `effect-wake`, `paired-backup`, `scheduled-wake` and `wsctl scheduled-hold`. Step D doesn't check whether schedule, effect, backup or alert state appeared.
    - **Fix:** add a before/after inventory of those paths.

The approved activation design and the prior preparation report added nothing beyond these findings. The prior HIGH REVISE on the enabled branch is not cleared by this deployment design. This review gives no deployment authority and doesn't stand in for Liam's approval.
