**No critical flaws. HEAD `117dae6` is good enough to build as a PoC.** I ran the code-review skill at MEDIUM on PR 151 and checked the top findings against the source myself. Nine findings came back; none blocks a PoC.

**Worth fixing before production:**

1. **The group-hex check accepts odd lengths** (`deployment_authority.py:47`). `[0-9a-f]{32,128}` lets through 33- or 127-character values, which can't be hex-encoded bytes. The old producer check accepted exactly 64. This matches the existing binder policy you chose to align with, so it isn't new behaviour. The fix is `(?:[0-9a-f]{2}){16,64}` in the one shared spot.
2. **O_NOCTTY is still missing from some opens.** The roothelper, cessation and cgroup.events sites are fixed. But `_load_core` in `deployment_authority.py:58` (a file this PR touches) and `_FILE_FLAGS` in `deployment_host_store.py:25` still open without it. These read fixed paths and check that the target is a regular file, so the real risk is low. The concern is that `qualification.md` reads as if every such open has O_NOCTTY now, which overstates the change.
3. **The final evidence covers less than the earlier run.** `qualification.md:7` reports 140 tests passing, but the final run after the last flag changes was only 101 (line 13). The 39 producer and authority tests, including the new group-width tests, weren't rerun on the final source. The last edits didn't touch those modules, so this is a gap in the record rather than likely breakage. Rerunning the 140-test suite would close it.

**Minor:**
- No test runs the real child process to check that a bad group is refused. Group-width refusal is only tested in-process through `_produce`.
- The extracted cgroup test fixture copies module constants in by hand. It has already broken once and will again if `_empty_group` picks up a new constant.
- The producer imports the private `_GROUP` from the authority module. It should be a public constant.
- Two styles of hex validation sit side by side: a regex for the group, a hand-written loop for the SHA-256 fields.
- In the root helper, the directory opens in `_binary` (around line 101) lack O_CLOEXEC, and the `/` open lacks O_NOFOLLOW.
- The group-width test reads its expected group from the same profile it gives the producer. It would still pass if the producer got the group from somewhere else, so it doesn't prove the group binding the doc claims.

I made no source edits and touched no services, queue or live state. Items 1 and 3 are the cheap ones to do first if you want a cleaner record.
