**Verdict: no critical flaws. PR157 is good enough to implement as the finite no-effect private root admission PoC.**

I ran `/code-review medium 157`. It reported 10 findings and labelled none of them by severity, so I read the journal, entry and binding code myself to check the important ones. Every correctness finding fails closed: none of them creates a false admission, allows a second use, or releases an issued hold. Nothing was edited, run, or posted.

**Worth fixing, but not blocking:**

1. **First-time schema creation is not atomic** (`deployment_admission_journal.py:76`). The schema is built with `executescript` while `isolation_level=None`, so each CREATE commits separately. A crash partway through, or two processes opening the journal for the first time at once, leaves a partial schema. Every later open then fails with `admission-journal-schema`. This bricks the journal until someone repairs it by hand, but it never admits anything wrongly. Wrapping the creation in `BEGIN IMMEDIATE…COMMIT` fixes it. This is the one I'd fix before the PoC sees repeated use.
2. **The window is checked twice and can disagree** (`journal.py:156`). If `not_before` passes during the DB wait (up to 5s), the row is inserted as `failed-no-effect` but `admit` then returns success. The later `fail_unissued` call fails, and the child replies `unknown` and exits 2 when the correct result was a clean `failed-no-effect`. Nothing is held. Using the window decision made at insert time fixes it.
3. **A failed settlement inside `admit` is not retried** (`journal.py:163`). If `fail_unissued` itself fails there, `admitted` is still False, so the entry skips its retry. The row stays `ready` and the target stays locked until root runs `recover_never_issued`, which is the designed recovery path.
4. **The exception list in `_entry` is incomplete** (`deployment_admission_entry.py:73`). An exception outside the listed types (RuntimeError, SystemExit, and so on) skips the `unknown` reply and the HOLD message. The hold remains and root recovery can release it.

**Rejected as intended by design:**

- **Finding 5, "nonce binding weakened"** (`binding.py:151`). It flags that the old `consumed.nonce_hex == channel.nonce.hex()` check was removed. Under the design, `channel.nonce` is the fresh admission nonce B, and Consumed must equal the native nonce A, so the old check compared the wrong values. A is now checked against `receiver_nonce_hex` and required to differ from B. The consumed record is also bound to installation, enrollment, profile, capability and the original packet. One-time use is enforced by UNIQUE constraints on nonce, use_key, execution and approval. Binding A to the native parent is the separately reviewed parent repair you mentioned, so this is not a PoC defect.

**Cleanups only:** the same validation runs twice in `_correlate` and `admit` (fd 13 re-read, re-hashed and re-parsed), the hex check for `receiver_nonce_hex` exists in three places, the entry retries `fail_unissued` even after the first settlement succeeded, `fail_unissued` takes a `channel` parameter it never uses, and the tests hard-code `/usr/bin/python3.12` (`tests/test_deployment_admission_entry.py:481`), which will break under CI on any other Python version.

The R2 repairs you listed did not cause any of these.
