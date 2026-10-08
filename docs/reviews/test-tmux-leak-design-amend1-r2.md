# Design review r2: tmux leak design, Amendment 1 (btq-q1r4p)

Reviewed commit `afc044b`. Reviewer: gpt-6.1-sol (Codex, read-only, reasoning high). Author: claude-opus-5-5. Mode: cross-model.

The review text is verbatim except that link targets are repository-relative: the reviewer's absolute worktree prefix was removed for the install-agnostic check.

---

1. **[NON-BLOCKING] R1 finding 1: yes, resolved.** [A2, lines 175–178](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:175) returns the Harness’s existing guarded `h.tmux`, preserving shared-server identity and targeting the correct server during teardown. **Fix:** None.

2. **[NON-BLOCKING] R1 finding 2: yes, resolved.** [A2, lines 147–155](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:147) restricts launchers to `None` and the controlled `GateLauncher`, rejects others before execution, and explicitly requires fd preservation. Closing the launcher’s descriptor without `LOCK_UN` correctly preserves the inherited SH lock. Regression 9 covers rejection and gate inheritance. **Fix:** None.

3. **[BLOCKING] R1 finding 3: partially resolved; the scan excludes too little trusted infrastructure.** [A2, lines 185–206](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:185) adds the requested bypass cases, documents limitations, and narrowly exempts mocked tests. However, scanning every other module under `tests/` rejects the watchdog’s required raw command at [tests/tmux_watchdog.py:95](tests/tmux_watchdog.py:95). That call must remain outside SH so cleanup can kill SH-holding servers. Parsing every valid Python string also rejects the scanner’s own negative specimens, while the private-reference rule conflicts with regression 8’s `_open_lock_at` call.

   The `_exec` extraction and tests written before extraction are sound: arguments and exception mapping remain pinned in their existing callers.

   **Fix:** Explicitly identify trusted watchdog command plumbing and scanner specimen storage. Put private-helper regression plumbing inside the trusted guard module or give it a narrowly named allowance. Add positive cases for these exceptions and negative cases for every enforcement rule.

4. **[NON-BLOCKING] R1 finding 4: yes, resolved.** [A3, lines 225–228](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:225) makes earlier replacement detection terminal and repeats identity validation under final EX. Regressions 2(c)/(d) cover both requirements. **Fix:** None for this finding; post-unlink recovery has a separate defect below.

5. **[NON-BLOCKING] R1 finding 5: yes, resolved.** [A3, lines 235–239](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:235) now distinguishes current SH holders, delayed processes holding an unlinked inode, and filesystem closure established by successful `rmdir`. It no longer claims closure proves all guarded processes have exited. **Fix:** None.

6. **[NON-BLOCKING] R1 finding 6: yes, resolved.** [A2, lines 141–145](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:141) explicitly separates parent opening from final-component opening. `LOCK_FLAGS` excludes `O_CREAT`, and [regression 8](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:299) deterministically exercises creation after final unlink but before `rmdir`. Removing the equivalence classification is correct. **Fix:** None to the delayed-create test sequence; address its scan allowance and recovery behavior separately.

7. **[NON-BLOCKING] R1 finding 7: partially resolved; regression 10 can falsely prove descriptor release.** [Regressions 5 and 9–11](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:293) add the requested early-unlock, child-release and mixed-survivor coverage. Regression 11 correctly requires later kills to proceed despite held removals. However, scanning only descriptors below 1024 misses a retained launch fd above that bound. I reproduced the probe returning no matches while the target descriptor remained inherited at fd 1500.

   **Fix:** Probe the known inherited descriptor, enumerate actual open descriptors, or enforce and assert its range. Include a retained high-fd negative control. Platform execution remains future validation, not evidence supplied by this design revision.

8. **[BLOCKING] Final-unlink recovery cannot follow the stated removal protocol.** [A3, lines 229–233](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:229) closes the watchdog’s sole lock fd, then promises another normal sweep after `ENOTEMPTY`. That sweep cannot acquire EX on the closed descriptor. Reopening a replacement would violate the original-inode protocol; continuing without EX would introduce an unserialized removal path. Closing the last reference also removes the protection against reuse of the recorded inode number.

   **Fix:** Retain the original descriptor through the `rmdir` attempt. Make unexpected post-unlink contents a terminal failure: preserve them, report `closed: false`, and perform no further removals. Alternatively, specify a complete recovery protocol that preserves the original lock invariant. Test both a recreated lock and another post-unlink entry.

9. **[NON-BLOCKING] Regression 9 needs stronger fd identity checks on macOS.** [A6, lines 309–320](docs/superpowers/specs/2026-10-08-test-tmux-leak-design.md:309) names suitable inspection mechanisms, but `lsof -Fi` supplies inode identity without device identity. An inode match alone can identify an unrelated file on another filesystem. Apple’s documentation exposes separate device, inode and descriptor fields. [Apple lsof manual](https://raw.githubusercontent.com/apple-oss-distributions/lsof/main/lsof/lsof.8).

   **Fix:** On macOS, request device and inode fields, require a numeric fd, and compare `(st_dev, st_ino)` after checking command success. On Linux, stat the `/proc/<pid>/fd/<n>` targets and compare the same pair. Keep the server alive through inspection and fail explicitly on inspection errors. Descriptor presence establishes inheritance; regression 5 supplies the separate lock-lifetime assertion. [Linux proc documentation](https://kernel.org/doc/html/v6.15/filesystems/proc.html#proc-pid-fd-list-of-symlinks-to-open-files).

REVISE