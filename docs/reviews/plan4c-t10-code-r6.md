I found 10 problems; the first 4 are the most serious. None of them is a crash on the normal path, but 1 to 4 are worth fixing before this goes in as a PoC. This was a read-only static review of `117b28f2..e46d413`; I ran no tests.

- **1 and 2:** `wsctl harness install` now blocks on the approval worker with no time limit. It also reports `ok` even when the approval failed.
- **3:** one bad approved row can stop wsd from starting at all, and it will keep failing on every restart.
- **4:** the quarantine for an ambiguous publish has no way out.
- **5 and 6:** the fix for finding 3 is only partial. Startup and taint handling still wait on approval network calls.

10 findings, most severe first:

1. `src/heterodyne/wsd/daemon.py:361`: `install` now joins the approval worker before replying, but it always replies `ok ... its approval is being created and posted`, even when the worker failed the row with ASK_UNPOSTABLE.

   Failure scenario: The secret scan withholds the approval text, the btq digest has gaps, the read-back doesn't match, or admind refuses the post. The worker calls `_fail_open`, so the row is `failed` before the reply is sent, yet the operator gets status `ok` saying the approval is in progress. They wait for an approval card that will never arrive. Since the worker is joined anyway, run `_ask` inline (the thread does nothing now) and return its result as refused/failed.

2. `src/heterodyne/wsd/harness.py:253`: `wait()` calls `worker.join()` with no timeout while the HARNESS_LANE job is running, so one hung `bd`/btq call blocks every harness operation and the advance timer indefinitely.

   Failure scenario: Inside `_ask`, `queue.bd('create', ...)`, `queue.show` or `ask_digest` hangs (a btq lock, a stuck dolt). `harness_one` never returns, so HARNESS_LANE never runs another job: `_harness_every` advances stop, posted approvals are never decided, and `list`/`use`/`verify`/`remove`/`prune` all wait until the client gives up. The client waits up to INSTALL_SECONDS (1200 s); the lane stays stuck. Shutdown then ends in Undrained. The fetch alone (up to 600 s) already blocks `wsctl harness list`, whose REPLY_SECONDS limit is 600 s, for that long.

3. `src/heterodyne/wsd/harness.py:542`: `recover()` only catches HarnessRefused for each approved row, so any other exception from `_settle` aborts the whole startup recovery, and with it wsd's startup.

   Failure scenario: Example: the fallback `Store._call` catches only sc.Refused, so an OSError (EIO/EACCES on the store root) escapes `store.outcome`/`publish`. Others: a JournalBusy/sqlite error in `_published`/`_end`, or KeyError from `harness_set`. Any of these propagates out of `harness_recover`, which re-raises, so `_startup` fails and wsd exits. systemd restarts it into the same row and it fails again. Every workstream is down because of one approved install. This contradicts the disposition's claim that ambiguous publications are quarantined 'while unrelated lanes can continue'.

4. `src/heterodyne/wsd/harness.py:664`: An `approved` row that keeps hitting RECOVERY_FAILED (wrong publish name, or an outcome that never resolves) never expires and has no operator command to clear it, so the whole adapter is locked permanently.

   Failure scenario: The helper publishes under a different name. The row stays `approved` for good (`_advance` only expires staged/posted rows), and every tick logs at debug and changes nothing. Meanwhile `_check` refuses `use`/`rollback` for every install of that adapter, and `in_use` returns True for all of them, so `remove`/`prune` refuse IN_USE. The 'exact subsequent readback' never comes because the store really did publish another name. The only way out is editing the journal by hand.

5. `src/heterodyne/wsd/harness.py:753`: `taint_current` and `tainted` take HarnessOps.lock from the scheduler while it holds its parker lock, but `advance` holds that same lock across approve-bead (5 s) and `bd show` (20 s) for every posted row. So workstream pickups still wait on approval I/O, which finding 3 was meant to remove.

   Failure scenario: Three posted installs and a slow `bd`: an advance tick holds `self.lock` for about 75 s. Any workstream pickup that processes an InstallTainted signal blocks inside `_signal` (parker lock held) until advance finishes, delaying that workstream's launches and its other signals. `taint_current` only reads a marker file and needs no HarnessOps lock. Alternatively, `advance` could do its network reads without holding the lock.

6. `src/heterodyne/wsd/daemon.py:730`: `_startup` (and `startup`) awaits a full `harness_advance` (approve-bead plus `bd show` per posted row, store recovery per approved row) before any workstream's recovery or startup pickup, though the invariant only requires `recover`.

   Failure scenario: With N posted installs and a slow btq, every workstream's startup recovery and pickup is delayed by up to N×25 s after each restart. Startup does not need the approval decisions: the harness timer advances on its own lane. Drop the startup advance, or start it without awaiting it before the workstream recoveries.

7. `src/heterodyne/wsd/harness.py:363`: A row that ends failed, expired or denied never closes its approval bead and never withdraws the admind ask, so a human can still approve an ask that wsd has abandoned.

   Failure scenario: admind accepts the post, but the reply arrives after ASK_SECONDS (10 s) or isn't valid JSON. The worker's except branch fails the row while the ask is live in admind. The same happens with every `expired` row after `staging_hours`, and with a bead created before a read-back mismatch. A human later approves the card and nothing happens. Nothing in wsd closes the bead, so the approval queue fills with abandoned `kind:approval` beads.

8. `src/heterodyne/wsd/daemon.py:351`: `list` now keys its entries by `install_label` (adapter plus manifest digest prefix) and withholds the version, but `use`/`verify`/`remove` still accept only the canonical `<version>-<12hex>` name, so no wsctl output gives the operator a working selector.

   Failure scenario: An operator installs from the recipe default (no version typed), then runs `wsctl harness list`. They see `codex manifest abcdef123456`, version 'withheld'. `wsctl harness use codex abcdef123456` fails with NOT_PUBLISHED, and they have to work out the version from the config or the store. Entries whose names don't match the install pattern all get the same label, `an install (malformed name)`, so later ones overwrite earlier ones in the dict.

9. `src/heterodyne/wsd/harness.py:736`: `list` and `_prunable` call `in_use` once per store install. Each call re-reads the selection file, re-queries `harness_opened`/`harness_installs`, and calls `runtime.sessions(ws)` for every launched entry, all while holding HarnessOps.lock (and every parker lock for prune).

   Failure scenario: With 20 installs and many past launches, a single `prune` repeats the selection read and runtime session listing installs × launches times while holding every workstream's operation lock, so all pickups wait for it. Load the selections, open-use set, OPEN rows and per-workstream session sets once per call and pass them in.

10. `src/heterodyne/sandbox/build.py:160`: The clean-source check uses `git status --porcelain --untracked-files=all`, which leaves out ignored files, so an ignored `.py` file under `src/heterodyne` can be imported while the approval records a clean HEAD commit.

    Failure scenario: A file matching a `.gitignore` pattern (a local `*_local.py` override, a generated module) is present under `src/heterodyne` and imported by the package. `_commit` sees an empty status and returns HEAD. The approval card's `commit` ref then names code that isn't what is running. Add `--ignored` (and ignore only `__pycache__`), or compare tracked files only.
