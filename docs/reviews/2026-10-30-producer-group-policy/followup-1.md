**Verdict: the design has no critical flaw and is fine as a PoC, but one of the two stated repairs is only partly done. Fix that before you call this follow-up complete.**

I ran the built-in code-review on PR 151 at `79dc58d`, then checked the main findings against the source myself.

**Must fix: the scope claim doesn't hold**
1. **The root helper's reads still lack `O_NOCTTY`.** `src/heterodyne/actions/deployment_root_helper.py:31` sets `_FILE = O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC`, with no `O_NOCTTY`. That constant is what opens the pinned executable and the state files. The PR only changed `deployment_cessation._READ_FILE` (line 107).
2. **One cessation read is also missed.** The `cgroup.events` open at `deployment_cessation.py:338` spells out the same flags inline instead of using `_READ_FILE`, so it doesn't get `O_NOCTTY` either. Switching it to `_READ_FILE` fixes this and prevents the two copies drifting apart again.
3. **`qualification.md:5` overstates the change.** It says "add O_NOCTTY to root's no-follow/nonblocking file opens", which is false until 1 and 2 are fixed. As written, the audit record would mislead the next reviewer.

**The group policy is fine for a PoC**
- `_GROUP = [0-9a-f]{32,128}` accepts odd lengths like 33 or 127, which can't be valid hex-encoded bytes. Your stated intent was to reuse the binder's existing policy, so that's a known looseness rather than something this PR introduced. If you tighten it later, use `(?:[0-9a-f]{2}){16,64}` and change both sides together.
- The 32, 64 and 128 widths are accepted, and 31, 129, uppercase and non-hex are rejected before HTTPS, as you described. The real 32-character group now passes.

**Optional cleanups**
- The producer imports the private `_GROUP` from `deployment_authority`. Making it a public constant would stop a rename in one module silently breaking the other.
- No test runs the real child process (`_entry`) with a bad group to confirm it exits non-zero with no output and no publication. Today the refusal is only tested in-process.
- The SHA-256 check in the producer validates hex with a hand-written loop, right next to the new regex group check. Using one regex style for both would be simpler.
- `qualification.md` is missing spaces in several places ("in15.04s", "widths32/64/128").

I didn't edit anything, run the tests, or touch services or the queue. With items 1 and 2 fixed and the qualification wording corrected, this is good enough to implement as a PoC.
