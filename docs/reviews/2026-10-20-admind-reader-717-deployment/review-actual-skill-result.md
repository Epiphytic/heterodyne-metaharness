Skill "code-review" completed (forked execution).

Result:
**Verdict: REVISE.** I verified the draft's SHA256 (f6a043fe...65bd5); it matches. The draft's source-delta claims check out against git: 21 runtime files change, the only admind change is `fulfillment_projection.py`, `deploy/` and the lock are unchanged, `ENABLED=False`, and admind settings are strict with no `fulfillment_enabled`. The findings below are gaps in how the procedure uses the existing installer, guard and units. None of them asks for a new capability or proof. Findings 1–3 are HIGH, 4–8 are MEDIUM and 9–10 are LOW. Nothing was edited, tested, run or queried live.

```json
[
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 48,
    "summary": "[1 HIGH] Every HOLD in the outage window (verifier won't drain, guard refusal, installer refusal or failure) leaves admind, the Marmot transport, stopped or restart-looping, yet §5 sends recovery through a Marmot disposition request; there is no pre-authorized abort that restores the unchanged 5db admind.",
    "failure_scenario": "Step A stops admind. install.sh then refuses in its first stage (e.g. 'the uv cache lacks a package', lingering off, a plan refusal, install.sh:345-433), which changes nothing. Under the draft, the operator must HOLD with admind down. The 'concrete Marmot recovery disposition' cannot be delivered because admind carries Marmot. Liam is unreachable and the outage is unbounded. If the failure comes after write_unit in the services step (install.sh:843-890), the 717 unit has Restart=always/RestartSec=5 (admind/unit.py:17-23). It keeps reopening the live DB while 'held'. Fix: consent to 'restart the unchanged 5db unit' as the abort for any failure before the first change, and to an explicit `systemctl --user stop` for failures after it."
  },
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 63,
    "summary": "[2 HIGH] The procedure never runs the supported `install.sh --dry-run` (same pinned args plus --no-wsd --no-verity) before the outage. It also defines no exit-code or summary acceptance.",
    "failure_scenario": "The store-helper README says `--dry-run` runs every check, including the offline scratch build, and prints `would:` for every change. Skipping it means every refusal and unexpected `would:` surfaces only after step A has taken the transport down. The outage also absorbs two full uv syncs (the preflight scratch build and the release build). There is no acceptance rule either. Exit 3 with 'skipped (admind init has not run)' (install.sh:498, 884-887) leaves admind not started, yet would read as a non-failure. Under --no-wsd the services summary says 'skipped (--no-wsd)' even when admind was restarted (install.sh:904-905). Require a pre-outage dry run with a reviewed would-list, then exit 0 plus the literal 'ok: heterodyne-admind.service is up and runs the render'."
  },
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 74,
    "summary": "[3 HIGH] The effect list and consent item 2 leave out install.sh's `store` step (setup-store.sh). That step can create a system account, rewrite root config, the helper copy and the socket drop-in, and restart the helper socket. This contradicts §3's 'not authority to initialize a new account/group'.",
    "failure_scenario": "If heterodyne-store is absent, or /etc/heterodyne/store-helper.json, /usr/local/libexec/heterodyne/store_core.py or the socket 10-group.conf drop-in differ, setup-store.sh:205-227 runs `useradd --system ... heterodyne-store` and `install` over those root files. live_drift then triggers `systemctl restart heterodyne-store-helper.socket` (install.sh:596-611). None of this is in the §2 table, §4C or the consent text, so consent covers less than the installer does. Require dry-run `ok:` for every store and units line, and HOLD on any `would:` there unless consent names it."
  },
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 21,
    "summary": "[4 MED] Step A cannot keep its 'no automatic kill' promise. The admind unit sets no TimeoutStopSec, so `systemctl --user stop` SIGKILLs after systemd's default stop timeout. That stop also runs the outgoing 5db worker, which lacks the repeated-cancel repair, and the draft never says so.",
    "failure_scenario": "The PR132 repair report shows the predecessor's repeated cancellation replaces the original cancellation and can block shutdown on the loop. The 5db process being stopped in step A (and every stop after a rollback) still has that path. If it hangs, systemd kills it at roughly 90s (KillMode=control-group, unit.py:21). The draft's 'retain state and HOLD; no automatic kill' can't happen: the kill occurs anyway, and nothing records the stop Result as timeout. Fix: issue exactly one stop request, record the unit Result/ExecMainStatus, and treat Result=timeout or SIGKILL as a non-clean stop that HOLDs before the guard."
  },
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 48,
    "summary": "[5 MED] The writer-quiescence step names no concrete writers. It misses the source-known ones: `admind` CLI commands open Store directly on admind.db, the admin agent's tmux session deliberately survives an admind stop, and WSD posts through ask.sock and alert files.",
    "failure_scenario": "admind/cli.py:72-77 `_load()` constructs Store(state_dir/'admind.db') for CLI operations. unit.py:18-20 says the admin agent's tmux 'runs in its own transient scope ... a stop or restart leaves the agent running'. With admind stopped, that agent, or a controller/hook running `admind ask ...`, can open and write the DB after the guard's before.db snapshot. The backup is then not final, and the guard's preservation comparison does not describe the live DB the 717 reader opens. Enumerate these clients (admin agent session, controller scripts, WSD, hooks) and stop or block them, or HOLD."
  },
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 40,
    "summary": "[6 MED] The draft rightly requires HOLD on an unmanaged WSD but names no probe, and --no-wsd turns off the installer's own probe. It also gives no rule for an active managed WSD, and misstates the WSD unit's release after deployment.",
    "failure_scenario": "install.sh:183 `((!no_wsd)) || return 0` skips wsd_outside_check. A hand-made heterodyne-wsd.service not in record.json is neither stopped nor disabled (install.sh:848-857). A WSD holding ~/.local/state/heterodyne/wsd/wsd.lock goes undetected unless the preflight runs `platform_state.py wsd-lock` and `record-show` itself. If the managed WSD is active, the installer stops it mid-run and may cancel in-flight launches; §2's 'remains stopped' assumes otherwise. `[admind] restart_units` can let an operator `!restart` the WSD unit. Under --no-wsd that unit is never re-rendered, so it still runs the 5db release while record.release becomes 717. §2's 'internal owner remains the default if later authorized' therefore describes 5db WSD code, and pruning the 5db release would break the unit."
  },
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 82,
    "summary": "[7 MED] Rollback never requires stopping the 717 admind and other writers before the 'newest' snapshot and the 5db guard. 'Restarts the 5db reader against that newest preserved schema/history' can be read as restoring the snapshot rather than keeping the live DB.",
    "failure_scenario": "The operator takes the 'newest coherent snapshot' while 717 admind is still running; the draft itself says a WAL backup does not establish writer cessation. Answers keep arriving until the installer restarts the unit. If the snapshot is then put in place (one reading of 'against that newest preserved ...'), those authenticated answers are lost, which §5 forbids. Specify: repeat the step A quiescence on 717, snapshot only as evidence, keep running on the live DB, and never copy the snapshot into place. Also require stopping a Restart=always loop before snapshotting after a failed install."
  },
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 66,
    "summary": "[8 MED] The root invocation `bash \"$ROOT_PRIVATE_VERIFIED_INSTALLER\"` splits the runbook's single copy, hash-check and run block into separate steps, and leaves the root step as hand-run steps.",
    "failure_scenario": "The README `install-pinned` block (and the guard's installer-command.sh) creates $b with mktemp in /run, checks its sum, and runs it inside one `sudo bash -c`. The EXIT trap deletes $b, so no lasting 'ROOT_PRIVATE_VERIFIED_INSTALLER' exists. Copying, checking and invoking separately reopens a time-of-check/time-of-use gap. The README says 'The block stays as it is' and flags go on the same `bash \"$b\"` line. The generated script's --service-user is the guard runner's getuid name (prepare_admind_guard.py:111), so it is wrong unless the guard runs as SERVICE_USER. ~/.claude/CLAUDE.md requires 'Root/host changes go into an install script he runs with sudo, never ad-hoc steps'. Use the README block verbatim with ' --no-wsd --no-verity' appended and sums from deploy/store-helper/manifest.sh, as one root script plus one command."
  },
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 46,
    "summary": "[9 LOW] PREPARATION_DIRECTORY is described as 'a new private 0700 directory' and passed as --output, but the guard creates --output itself with a non-resumable mkdir.",
    "failure_scenario": "prepare_admind_guard.py:79 calls `output.mkdir(mode=0o700)` with no exist_ok, deliberately not resumable. An operator who pre-creates the 0700 directory as the draft describes gets FileExistsError during the outage window, and again at the 5db rollback guard. Specify a nonexistent path inside an existing private parent outside the checkout and state."
  },
  {
    "file": "<operator-home>/.hermes/handoff/2026-10-15/codex/autonomous-resume/next-admind-reader-deployment-design/draft.md",
    "line": 90,
    "summary": "[10 LOW] The 'dormant Plan8' claim depends on nobody calling the new CLIs, but the rendered admind unit puts the 717 release's venv bin first on PATH, and the success criteria include no check that no Plan8 state appeared.",
    "failure_scenario": "install.sh:645/721 renders the admind unit with PATH=\"$vbin:$service_path\" (units.py:52-59). Children admind spawns, such as a newly started admin agent or hooks, resolve `wsd`/`wsctl` to 717, exposing `babysitter`, `effect-wake`, `paired-backup`, `scheduled-wake` and `wsctl scheduled-hold` (wsd/cli.py:215-220). Step D checks timers, the fulfillment flag and the queue repin, but not the absence of schedule manifests, effects or backup-cycle state, or new local alert files under the state dir. An invocation would go unseen, and the rollback scope would not cover it. Add a before/after inventory of those paths."
  }
]
```

The governing activation design and the prior preparation report raise nothing beyond what is above.