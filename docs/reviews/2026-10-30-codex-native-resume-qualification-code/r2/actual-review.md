**Verdict: APPROVE.** Nothing blocks merge. Both R1 blocking findings are fixed, and I found three low-severity follow-ups, listed below. I didn't check the full 290-test suite result, so this approval doesn't cover it.

**The `code-review` skill misfired.** I ran it at high against `a053630`, but all 10 of its findings were about `src/heterodyne/actions/deployment_root_helper.py` and its tests. That file isn't in this diff: it was merged in PR #147, which is the base commit. I discarded those findings and reviewed the real diff myself. It's two commits on top of `a053630`, touching `runtime.py` (+12/−5) and `tests/test_sandbox_runtime.py` (+131/−5), plus the evidence docs.

**What I confirmed**
- HEAD is `61065df8e888e10cc935b4847d3ca833fb51dacb`. Nothing in the tree has changed except the untracked `.venv`.
- All 50 hashes you listed match the committed files (2 source files and 48 evidence files).
- I reran the focused tests: **19 passed, 271 deselected (76.48s)**, the same count as `r1-repair/focused.log`. I didn't rerun Ruff, Pyright or the hooks.

**R1 finding 1, install binding: fixed** (`src/heterodyne/sandbox/runtime.py:625-629`)
- A medium resume now needs three things: the currently verified install is version `0.160.0`, and the previous generation's recorded install name and digest both equal the current one.
- Old records have empty name and digest fields (`runtime.py:166-167`), so they can never match and are refused. Only one place writes these fields (`:597`), so cleanup and replace keep them.
- Order is right: the unlanded and WIP holds are checked first (`:614-618`), then the install check, then cleanup of a crashed generation at `:566`. Low effort, high effort, other versions, and resumes with no previous record are all refused before any cleanup.
- Checking the install a second time early has no new side effects. A bad install raises the same error before cleanup that it would have raised later anyway.

**R1 finding 2, tests: fixed**
- Low effort and medium on 0.166.0 are refused before cleanup, with the record bytes unchanged.
- A changed install name or digest is refused before cleanup.
- The unlanded and WIP holds run before both cleanup and the install check.
- For a crashed generation, the install is verified before cleanup runs.
- The high-to-medium test now checks that the first generation really wrote `high`.
- The high-effort refusal tests were moved and still apply.

**Evidence**
- The 0.160.0 high-to-medium run uses the same thread ID `01a18df3-535d-…`. It shows `gpt-6.1-sol` at high then medium, two `task_complete` answers, and `codex-cli 0.160.0`.
- Two limits are disclosed and acceptable: only `gpt-6.1-sol` was tested over the network, and low-to-medium wasn't tested separately.

**Findings (all low, none block)**
1. **The "missing install" refusal has no test** (`tests/test_sandbox_runtime.py:2653`). The disposition says a record with no install provenance is refused. The code does that, but the test only checks a different name and an all-zero digest. Adding an `("install_name", "")` case would cover the old-record path.
2. **The README is out of date for R1** (`docs/reviews/2026-10-30-codex-native-resume-qualification-code/README.md:12-16, 46-51`). It describes the gate as a version and effort check only, without the previous-install match. It also still calls the 14-test run "final". The correct details are only in `r1-repair/`, so the README should point there or be updated.
3. **The archive-digest claim has no stored local hash** (`README.md:9-10`). Only the GitHub API digest (`306865…`) and the binary hash (`12eb3e…`) are kept, not the downloaded archive's own sha256. `run.py.txt` also doesn't hash the binary it ran. The recorded `--version` output supports the claim, but nothing kept proves it.

**Also noted, not findings:** the quality check exits 2 with 9 gating rows. These are the gate's complexity going from 13 to 17, churn, and duplicated test setup. None of them is a correctness problem.

I made no edits and didn't touch services, the queue or any deployment. I didn't write an R2 receipt into `docs/reviews/`, since you said no edits.
