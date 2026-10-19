I found no critical flaws in the two R3 repairs, so this is good enough for a dormant source-only PoC with the eight known findings still open. That is not an approval. I made no edits and ran no tests, probes or verify pass, so treat each item below as plausible rather than confirmed.

- **Fix 1 (publisher alias repair):** this looks correct. An unsafe file now makes `_published` return False. The code then goes back into `recovery.publish`, which hits `FileExistsError` and calls `repair_alias`. That function still refuses anything except the exact single `.recovery-<32hex>` alias with the same inode and bytes. A FIFO, directory, wrong-mode, wrong-owner or extra-link file still refuses, either in `repair_alias` or in the readback after it. An unsafe current pointer still refuses through `replace_current`'s `private_read`. The publisher can't leave the current pointer with two links, because the rename only runs after the alias is removed.
- **Fix 4 (directory fsync on readback):** this is correct for the file's immediate parent directory. The gaps are below.

Nothing below repeats the eight OPEN UNWAIVED findings. No CLAUDE.md rule is broken: the only coding rule in the CLAUDE.md files that apply is the Rust clippy gate, and this code is Python.

10 findings, most severe first:

1. `src/heterodyne/admind/gateway_custody.py:626`: Fix 4 fsyncs only the file's immediate parent; the ancestors that private_parents() creates (generations/, generations/<key>/, permits/) are never fsynced into their own parents, so 'issued' is still not a durability receipt for a first publication.

   Failure scenario: First gen-1 issuance for key K: private_parents mkdirs generations/K/, publish links 1.json and fsyncs generations/K/, replace_current fsyncs permits/. Neither enrolled.root nor generations/ is fsynced, so the new directory entries for generations/K (and permits/ on first use) are not durable. The daemon replies 'issued'. A power loss can drop generations/K/ along with 1.json even though the readback precheck fsynced the leaf directory.

2. `src/heterodyne/admind/gateway_custody.py:627`: The repair depends on a later directory fsync succeeding after an earlier one failed, but on Linux a failed fsync can clear the error state (errseq/'fsyncgate'), so the retry can return 0 while the earlier metadata was never persisted.

   Failure scenario: The first publish fails at os.fsync(directory) with EIO. The kernel records and then clears the writeback error. On retry, _published reads matching bytes and the second fsync returns 0 because nothing is marked dirty any more. issue_permit replies 'issued', and a power loss can still drop the directory entry. The new test only shows that the call is retried, not that the data is durable.

3. `src/heterodyne/admind/gateway_custody.py:538`: A gen>1 publication whose crash left an alias can only be repaired through the full historical certificate re-validation (permit_certificate with nonconsumption-(N-1) and superseded-(N-1), and no consumed/issuing/receipt). Removing our own alias depends on queue state that may have moved on.

   Failure scenario: Gen-2 bundle commits; recovery.publish(2.json) links, then the process dies before unlink (nlink=2). Before the retry, the queue prunes nonconsumption-1 or writes K.issuing.json. On retry, _published returns False, the repair branch's permit_certificate raises 'consumption-unknown' or FileNotFoundError, and 2.json keeps nlink=2. Every later readback refuses, so the R3 fix 1 repair is unreachable for renewals in this state.

4. `src/heterodyne/admind/gateway_custody.py:616`: The repair decision compares exception text (str(exc) != 'unsafe-file'), so recovery's error text becomes part of how custody makes safety decisions.

   Failure scenario: If recovery.private_read ever words this refusal differently, adds detail, or reuses 'unsafe-file' for a different cause, _published either re-raises and fix 1 silently stops working, or sends a different unsafe condition into publish(). No type or code ties the two modules together, and only the four new tests would catch it.

5. `src/heterodyne/admind/gateway_custody.py:599`: Altitude: the _published precheck is a second, partial copy of what recovery.publish already does idempotently (existing identical file -> repair_alias -> readback -> directory fsync). R3 had to patch both regressions back into it rather than removing it.

   Failure scenario: Cost: every future publisher invariant (alias repair, fsync ordering, bounds) now has to be kept consistent between recovery.publish and _published. Calling recovery.publish unconditionally for the immutable file and tombstone (the pre-R2 behaviour) and computing 'complete' without side effects would remove both special cases. This is the same root as open finding 8.

6. `src/heterodyne/admind/gateway_custody.py:529`: _published is a readback predicate that now has durability side effects (a directory fsync), and it runs outside publication_lock and before the CAS. Fsync failures in the completeness probe surface as 'unavailable' even when the retry would otherwise be refused for proposal-changed or policy reasons.

   Failure scenario: A read-only-looking 'complete' probe on gen-2 fsyncs generations/K/ twice and permits/ once, and an EIO there returns 'custody source or store unavailable' before the transaction checks run. Callers can't tell a durability failure from a store outage, and the probe's writes happen without the publication lock.

7. `src/heterodyne/admind/gateway_custody.py:626`: Wasted work: a complete gen>1 readback fsyncs the same generations/<key>/ directory twice (immutable and tombstone), and the repair path calls _published (fsync) and then recovery.private_read on the same file again.

   Failure scenario: Each renewed-permit readback makes 3 directory fsyncs, where 2 distinct directories would do, plus redundant opens and reads of up to MAX_BYTES under publication_lock. Fsyncing each distinct parent once, or returning the bytes from _published, would avoid it.

8. `src/heterodyne/admind/gateway_custody.py:626`: Reuse: the directory-fsync block (os.open O_DIRECTORY|O_NOFOLLOW, fsync, close) is copied a fourth time instead of being moved into one helper in recovery.py, where it already appears at lines 317, 387 and 1141.

   Failure scenario: Cost: four copies of a durability primitive. A later fix, such as adding ancestor fsync or handling EINVAL on filesystems without directory fsync, has to be made in four places, and missing one copy re-opens the R3 finding 4 class of bug.

9. `tests/test_admind_gateway_custody_r3.py:31`: The durability test never reproduces the reported failure (publish's own directory fsync failing after link and unlink). It only makes the readback precheck's first fsync fail, and it asserts len(calls) >= 2.

   Failure scenario: If _published's fsync were removed and recovery.publish's directory-fsync failure left a state that later read back as 'issued', the test would only catch it by chance. Simulating the failure at the publish/replace_current directory fsync during the original issuance, then asserting the retry fsyncs before replying, would actually cover R3 finding 4.

10. `src/heterodyne/admind/gateway_custody.py:603`: Orphans from crashed replace_current/publish calls (.permit-<hex> with nlink=2, plus .recovery-<hex>) are never cleaned up, and fix 1's repair only covers the generations/ directory.

    Failure scenario: A process death inside replace_current's inner publish leaves permits/.permit-X and its .recovery-Y alias. Retries create new temporaries and never remove the old ones, so permits/ grows without bound across crashes. Any future repair_alias-style scan of permits/ would hit the 256-entry publication-directory-bound limit.
