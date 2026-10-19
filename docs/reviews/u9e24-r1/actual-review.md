I reviewed the full range at high effort, by reading the code only. Nothing was run and nothing was changed. I checked it against §6 of the approved design (commit 6f1c110). The most serious problem is that a crash in the middle of publishing a permit can leave it blocked for good with no repair path. The 128-message cap per proposal can also block a later revoke, and the 128-proposal cap never frees up.

10 findings, most severe first:

1. `src/heterodyne/admind/recovery.py:237`: publish() links the temp file to the final path and only then unlinks the temp, and nothing on restart removes leftover `.recovery-*` files. A crash between those two steps leaves the permanent generation file with two hard links, and every later repair refuses it.

   Failure scenario: The permit DB row commits, publish_permit runs os.link(temp, generations/KEY/1.json), and the process crashes before temporary.unlink(). After restart, `permit KEY` takes the repair path: os.link raises FileExistsError, and private_read(path) finds st_nlink == 2 and raises Invalid('unsafe-file'). The single-link rule now permanently rejects the stored generation, so the proposal can never be published (and the queue consumer would also reject it). This goes against the 'restart repairs that same candidate' claim.

2. `src/heterodyne/admind/recovery.py:512`: The 128-message cap per proposal is checked before the revoke branch, so once a proposal's history is full, an authenticated revoke is refused and never recorded.

   Failure scenario: After consent, operators send repeated settle replies, or their clients redeliver under new message IDs, until 128 rows exist for the proposal. An operator then sends `!maintenance revoke <token>` before the permit is issued. message() raises Invalid('message-capacity'), the state stays 'consented', and permit() still issues. §6 requires a revoke after consent to revoke before permit issuance.

3. `src/heterodyne/admind/recovery.py:422`: The proposal limit counts every row ever stored. Expired, revoked and finished proposals are never deleted, so 128 is a lifetime cap for the daemon, not a limit on live proposals.

   Failure scenario: After 128 registrations over the daemon's life, even if all are long expired or revoked, every new register raises Invalid('capacity') forever. Maintenance recovery stops working until someone hand-edits the database. §6 sets a 24-hour lifetime, which implies expired proposals free their slot.

4. `src/heterodyne/admind/recovery.py:554`: permit() never compares current authority against the policy digest stored at consent (the `policy` column, row[4], is never read). It only checks that current authority stays stable across the transaction and that the consenting operator is still current.

   Failure scenario: An operator consents under policy digest P1. The operator set or confirmed membership then changes (for example, another operator is removed or added), giving P2, while the consenting operator stays. permit() issues a permit stamped with P2 and raises no drift error, even though §6 says to recheck against the retained policy digest and refuse on drift.

5. `src/heterodyne/admind/recovery.py:682`: The rendered notice never tells the operator that an already-issued permit can still start the recovery for up to 30 seconds after a revoke. §6 requires this to be disclosed to the human.

   Failure scenario: The operator reads 'Revoke: !maintenance revoke TOKEN' and reasonably assumes a revoke takes effect immediately. If the permit was already issued, the queue can still consume it within 30 seconds, and the operator was never warned. This breaks the design's 'no zero-after-revoke claim' requirement.

6. `src/heterodyne/admind/daemon.py:798`: recovery_message only writes an audit line. No receipt or refusal is ever posted back to the operator's thread, so consent is never acknowledged and a duplicate never gets its retained receipt back.

   Failure scenario: An operator replies `!maintenance settle TOKEN` to the wrong message, after expiry, or while authority is drifting. The command is refused silently (thread-unmapped, history-only, authority), and on success they see nothing either. They cannot tell whether consent was recorded and are likely to resend. §6 requires persisting provenance before acknowledging, and returning the retained receipt for a duplicate.

7. `src/heterodyne/admind/recovery.py:460`: The duplicate lookup runs only after authority() succeeds, so redelivery of an already-recorded consent message is refused instead of returning its retained receipt.

   Failure scenario: Message M is recorded as 'consented'. The policy then changes, or the operator is removed, and the transport redelivers M. authority() returns None and Invalid('authority') is raised before the dedup lookup, so the retained receipt is never returned. §6 says repeated delivery of that message returns the same receipt.

8. `src/heterodyne/admind/daemon.py:2375`: Routing on startswith('!maintenance') with no word boundary takes over any operator message that begins with that prefix. Such messages skip claim_inbound and normal handling and are dropped with only an audit entry.

   Failure scenario: An operator sends '!maintenance-window tonight, pause deploys' or '!maintenance status'. Before this change it went to the agent. Now recovery.message fails COMMAND.fullmatch, raises Invalid('command-auth'), and the message is dropped silently with no reply and no inbound record.

9. `src/heterodyne/admind/recovery.py:329`: enrollment() re-hashes up to 256 source files of up to 16 MiB each with blocking reads on the asyncio event loop, while holding work_lock, on every register and permit request.

   Failure scenario: With a large source closure, each permit or register call blocks the daemon loop for seconds. Inbound message handling, membership checks, outbox sending and the ask/ctl sockets all stall. This can also push the permit past its tight 30-second initiation window as the queue sees it.

10. `src/heterodyne/admind/recovery.py:221`: path.parent.mkdir(mode=0o700, parents=True) creates any missing parent directories (maintenance-recovery/, generations/, permits/ parents) with the default umask mode, and only the immediate parent is checked for 0700.

    Failure scenario: On a fresh state directory with umask 022, the first publish creates maintenance-recovery/ and generations/ as 0755. Other users can then list proposal hashes and permit filenames, which contradicts the documented 0700 maintenance-recovery directory. No check catches it, because only generations/KEY is checked.

Other candidates that didn't make the top 10:
- **No status or terminal tracking:** the `terminal` column and `terminal` state are never written, so status can never report begun, completed or unknown.
- **Repeated validation:** each request is validated three times (`ctl.py` decodes it again, the server runs `check`, and `on_recovery` and `register` re-check). `check()` re-encodes the decoded request, so its duplicate-key check there can never trigger.
- **Possible broken thread link:** the outbox re-redaction rekey (`:r0`) would break the `notice_key:0` link to the sent thread.
- **No server identity check in the CLI:** the CLI doesn't verify the user ID of the process behind the daemon socket it connects to.
