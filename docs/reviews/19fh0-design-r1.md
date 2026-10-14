Reviewer: gpt-6.1-sol. Author: claude-opus-5-5. Mode: cross-model. Range: 7c68abb32204678a00860de9e42839aca902a604..27e1ea9051c47e11501b151aed3ce85752c86f0f. Verdict: REVISE.

Note: cycle 1 of 3 of the btq-19fh0 (hostread uid rule for a zombie leader with threads) design review. Verbatim, except that the home directory in link targets is written as `~` (the install-agnostic hook).

- **[BLOCKING] The leader’s UID does not prove that unreadable workers cannot hold our homes.** [Design lines 69–76](~/repos/heterodyne-metaharness-v2-design-19fh0/docs/superpowers/specs/2026-10-14-hostread-uid-first-design.md:69) acknowledge a non-dumpable worker of ours, but incorrectly conclude that clearing it is harmless because signalling might fail. Today that group settles and recovery **keeps every home**. With the proposed fallback, it clears and recovery **removes the homes while the holder survives**. Inability to kill a holder requires retention; it does not establish absence.

  Concrete scenario: a group retains `CODEX_HOME` in its shared environment; its leader has another UID and exits; a surviving worker has our UID but is non-dumpable. Worker environment reads fail, `_ours(facts)` examines only the zombie leader, and recovery removes the occupied home.

  The claimed privilege restriction also fails:
  - A worker can switch to an existing real, effective, or **saved** UID without `CAP_SETUID`. A leader with real/effective UID B and saved UID A can leave a worker that restores A through a raw per-thread syscall. `_ours` ignores saved UID A. Linux explicitly permits these transitions without the capability. [Kernel implementation](https://github.com/torvalds/linux/blob/v6.17/kernel/sys.c).
  - `CAP_SETUID` in a user namespace does not require host privilege. With a mapping containing our host UID and subordinate UIDs, a group can acquire different host-visible thread UIDs using namespace capabilities. A single-ID mapping alone does not produce that difference. [User namespace rules](https://man7.org/linux/man-pages/man7/user_namespaces.7.html).
  - Signal permission can use the target’s saved UID, so “another real/effective UID always means `PermissionError`” is also incorrect. [Signal permission rules](https://man7.org/linux/man-pages/man2/kill.2.html).

  Exec compounds this gap: after the saved zombie status read, a worker of ours can exec, take the leader PID, and disappear from the worker lookup. The fallback then clears the replacement using the old leader’s UID. There need not be a next scan after that clear.

  Require a conservative fallback unless worker ownership is established safely, or an explicit approved scope restriction that excludes these holders. Add deterministic regressions for an unreadable mixed-UID holder, saved-UID restoration, and worker exec between status and worker enumeration; assert that homes remain.

The proposed `_ours` reuse matches rule 5 exactly: real/effective target UIDs compared with the scanner’s real/effective UIDs; saved/fs UIDs are excluded. Missing UID settles. Worker-first ordering, unchanged existing tests, and exclusion of the start-time and `PF_EXITING` clauses are appropriate, but do not resolve the blocking case.

No repository files or credentials were changed.

Verdict: REVISE
