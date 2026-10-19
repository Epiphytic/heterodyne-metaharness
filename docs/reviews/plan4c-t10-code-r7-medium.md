**Verdict: REVISE.** Most of the R6 repairs land. Inline `_ask` removes the unbounded join. Recover now handles each row on its own. The taint path no longer waits on approval network calls. Selectors and ignored-source provenance are both addressed.

Two things block an approve. The ambiguous-publication quarantine (#4) is still open by design: it needs an approved operator recovery design before it can be fixed. And the new "truthful install outcome" reply can now say something false when the ask's outcome is unknown.

This was a static review of `117b28f2..46fcd036`, focused on commit `46fcd03`. I ran no tests and made no edits.

10 findings, most severe first:

1. `src/heterodyne/wsd/harness.py:569`: Still open (R6 #4): if an approved row keeps failing recovery, its adapter stays locked for good. Nothing expires the row and no command clears it, so use/rollback refuse and every install of that adapter counts as in use for remove/prune.

   Failure scenario: The helper publishes under a different name, or the store's outcome never resolves. The row stays 'approved' forever because _advance only expires staged/posted rows. _check refuses RECOVERY_FAILED for every use/rollback of that adapter, and in_use (line ~668) returns True for all its installs, so remove/prune refuse IN_USE. The only way out is a manual journal edit, which is prohibited. This is acknowledged as design-required, but it is still a critical operational flaw for a PoC that publishes.

2. `src/heterodyne/wsd/daemon.py:363`: The new 'truthful' install reply refuses ASK_UNPOSTABLE whenever the row is not 'posted', including when admind actually posted the ask but wsd could not confirm it. The operator is told no ask exists while a live approval card does.

   Failure scenario: admind accepts the post, but its reply arrives after ASK_SECONDS, is not valid JSON, or the journal is busy for harness_set(state='posted') (harness.py:372-381). The except branch fails the row and approval_result raises ASK_UNPOSTABLE, so wsctl prints 'refused'. The card is still live in admind. A human approves it and nothing happens, and the operator may install again and get a second card. The reply cannot tell 'not posted' apart from 'outcome unknown'. The same happens when bd create times out but still commits (btq notes this can happen).

3. `src/heterodyne/wsd/harness.py:636`: tainted() no longer takes HarnessOps.lock, so verify's 'clear marker' and '_clear_alert' are no longer atomic with respect to a new taint alert. A fresh taint can lose its alert.

   Failure scenario: verify() clears the marker at line 636. Before it reaches _clear_alert, a launch guard writes a new marker and the scheduler's _signal -> tainted() (it holds only alerts_lock now) writes alert sequence N. verify's _clear_alert then bumps the sequence to N+1 and unlinks alert N. The install is tainted with no alert, which breaks the documented 'never a taint without one' invariant. Previously, self.lock held across both verify steps blocked tainted() until the clear finished.

4. `src/heterodyne/wsd/harness.py:253`: resolve_selector only resolves store entries in state 'published', but list shows an @selector for every entry whose name matches INSTALL, including non-published store entries and OPEN journal rows. Those selectors always refuse NOT_PUBLISHED.

   Failure scenario: A store entry is in a non-published state (for example INSTALL_BROKEN). list shows it with 'selector': '@abcdef123456' and the version withheld. `wsctl harness remove codex @abcdef123456` or `verify` refuses NOT_PUBLISHED, and the canonical name is hidden from every wsctl output. The operator has no working selector to verify or remove the broken install, which is the gap R6 #8 was meant to close.

5. `src/heterodyne/wsd/daemon.py:362`: install now runs fetch, staging and the full approval round-trip inline on HARNESS_LANE (up to about FETCH_SECONDS+600 s, plus bd/git/ask deadlines). Concurrent list/use/verify and the advance timer wait behind it, beyond the client's REPLY_SECONDS.

   Failure scenario: A slow download (up to 600 s), verity staging, then ask_digest git reads (20 s each, unbounded in number across nested refs), bd create 20 s, show 20 s and the post 10 s. A `wsctl harness list` issued meanwhile hits REPLY_SECONDS=600 and the client reports a timeout, and posted approvals are not decided for the whole window. R6 #2's lane-blocking scenario is only partly fixed.

6. `src/heterodyne/wsd/btq.py:67`: The 'bounded native Git resolution' relies on subprocess.run(timeout=GIT_TIMEOUT) with capture_output. After the timeout kills git, Python still waits for the pipes to close, so a grandchild that holds stdout/stderr open (credential helper, fsmonitor, ssh) keeps blocking the call.

   Failure scenario: A git subcommand inside ask_digest spawns a helper that inherits the pipes and hangs. TimeoutExpired kills only the direct child, and run() then blocks in communicate() until the helper exits, so the HARNESS lane hangs anyway. btq's _git does not use start_new_session or process-group kill, unlike its own line-1868 path. The bound depends on a module global being patched, not enforced.

7. `src/heterodyne/sandbox/build.py:160`: Provenance excludes every ignored __pycache__/*.pyc wholesale. An unchecked-hash .pyc there is imported without being checked against its .py, so code that differs from HEAD can run while the approval names a clean commit.

   Failure scenario: Someone places a PEP 552 unchecked-hash pyc in src/heterodyne/wsd/__pycache__/ whose code differs from harness.py. git status, with the exclude pathspec, reports clean and _commit returns HEAD. Python loads the pyc without checking it against the source, so the approval card's commit ref does not describe the running code. This is narrower than R6 #10, but the 'ignored importable source fails provenance' claim does not cover bytecode.

8. `src/heterodyne/wsd/harness.py:545`: Only approved rows are isolated during recovery. A transient JournalBusy while failing any leftover 'staged' row, or in harness_close_all, still propagates out of harness_recover and aborts wsd startup.

   Failure scenario: wsd crashes during an inline install, leaving a staged row. On restart another journal writer holds the lock past BUSY_TIMEOUT, so _end raises JournalBusy. harness_recover re-raises, _startup fails and systemd restarts into the same race. Every workstream is down even though nothing about that row is unreadable state.

9. `src/heterodyne/wsd/harness.py:724`: Still deferred (R6 #9): list and _prunable call in_use for each install. Each call re-reads selections, harness_opened and OPEN rows, and lists runtime sessions for each launch, all under HarnessOps.lock (and every guard lock for prune).

   Failure scenario: With 20 installs and many past launches, prune repeats selection.load and runtime.sessions installs × launches times while holding every workstream's operation lock, so every pickup is blocked for the duration. The fix is to snapshot these once per call and pass them in.

10. `src/heterodyne/wsd/harness.py:243`: Dead or stale leftovers from the removed worker: wait() is a no-op with no callers, self.workers and worker_lock (186-187) are unused, and the _ask/recover docstrings and cli.py:162 still describe a worker thread or a reply that comes after staging. approval_result also re-reads the journal instead of taking _ask's result.

    Failure scenario: Maintenance cost. Readers think a background worker exists and that a reply can come before the ask is posted. Having _ask return its outcome (posted, or the failure constant) would remove approval_result's extra locked journal read and let the reply carry the real cause instead of a blanket ASK_UNPOSTABLE.
