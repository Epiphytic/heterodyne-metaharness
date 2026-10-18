I found no critical flaw that blocks the standalone PoC. This was a read-only review of `9b48f79..b436af6` (no tests run), aimed at how C1 withdrawal and decision guards interact with authenticated retirement. The guards hold up well:
- **Decision guards:** withdrawal is re-checked in `begin_attempt`, `issue_decision` and `close_attempt`, in the same transaction.
- **Retirement:** its proof-gated, authenticated flow stays intact. Retiring a withdrawn attempt still needs the full proof and authority checks.

The biggest concern is the first finding below. On a withdrawn source, the retry loop can close a held maintenance attempt as cancelled on a single native "undecided" read. That skips the proof-chain and authentication rules in the approved retirement design. I'd fix that, or explicitly accept it in the design, before treating the merged result as more than a conditional PoC.

9 findings, most severe first:

1. `src/heterodyne/admind/daemon.py:1831`: On a withdrawn source, retry_maintenance closes a held maintenance attempt as cancelled based only on one native 'untouched' readback, skipping the retirement design's rules (aa860fe ADR): complete proof chain, proof_capable_since_begin, same adapter identity, and a command from an authenticated operator.

   Failure scenario: Take a historical (proof_capable_since_begin=0) or proof-incomplete attempt in pending-maintenance. Any same-UID process sends approval.operation.v1 withdraw for its bead. On the next retry cycle read_and_settle returns untouched (possibly through a changed adapter), and close_refused ends the attempt as cancelled, 'no native decision was recorded', clearing the overlay and proof. The ADR says 'Native undecided alone ... never absence proof' and keeps historical attempts ineligible PERMANENTLY, yet this path ends the attempt with no operator involved.

2. `src/heterodyne/admind/daemon.py:1838`: The withdrawn-maintenance branch ends the attempt through close_refused, which records settled='refused' (meaning 'closed before anything was launched') on an attempt that ran one or more HOLD decision children.

   Failure scenario: An attempt has invocations>=1 with exact HOLD proofs and is then withdrawn and found untouched. ask_attempts.settled='refused', exit_status keeps 75, and the audit says action=refused reason=source-withdrawn. Later audits or retirement receipt checks can no longer tell 'never launched' from 'launched and HOLD-proven, then source-withdrawn', and the proof row is deleted by clear_active with no terminal receipt, unlike retirement.commit.

3. `src/heterodyne/admind/operations.py:344`: withdraw() calls enroll() for an operation key that does not exist yet, so any same-UID caller can create a new tombstone for any bead. It needs only a context digest from that bead's history and no receipt from an earlier post. This cancels a human-facing open approval ask and permanently forces the bead off the legacy post path.

   Failure scenario: A same-UID process sends mode=withdraw with fresh source/journal/operation values, bead=X and the digest shown on X's card. enroll() inserts a withdrawn binding, every open or answered ask for X is set to cancelled, and from then on serialised_post refuses all posts for X with 'source-owned bead'. The bead can never be re-posted, because withdrawn bindings make before_commit refuse a replacement card.

4. `src/heterodyne/admind/daemon.py:1846`: On a withdrawn source, a blocked or unreadable readback calls hold_maintenance, which posts the generic notice 'It will retry after fresh card and operator-authority checks'. Withdrawal prevents any retry, so the notice is wrong. For read-unavailable it also re-defers the attempt forever.

   Failure scenario: Withdraw an operation whose attempt is pending-maintenance while approve-bead is unavailable. Every 30 s cycle re-defers with reason read-unavailable (not suspended) and re-notices that a decision will be retried. The operator is told an approval will be re-issued when C1 forbids it, and status reports 'decision-in-flight' indefinitely.

5. `src/heterodyne/admind/daemon.py:810`: The source-owned-bead refusal in serialised_post applies to every ask kind, so question and merge asks that only reference an enrolled bead are refused with 'use approval.operation.v1', even though that protocol can only post approval cards.

   Failure scenario: Enroll bead X through approval.operation.v1 post, then have an agent post a question ask (kind='question', bead='X'). It is refused with 'source-owned bead; use approval.operation.v1', and nothing that protocol supports can post a question, so ordinary questions about that bead are blocked.

6. `src/heterodyne/admind/ctl.py:182`: The untyped msgspec.json.decode(line) for protocol sniffing runs before the max_request length check, and only TimeoutError/ValueError/DecodeError are caught. A RecursionError from deeply nested JSON (which operations.decode itself guards against) skips the per-protocol refusal and the client gets no reply.

   Failure scenario: Send a single line of deeply nested arrays (within the stream limit) to ask.sock. The pre-decode raises RecursionError, which falls to the outer generic except. Only action=failed is audited and the connection closes without the 'malformed request' reply legacy clients expect, and every ask request is now parsed twice.

7. `src/heterodyne/admind/daemon.py:689`: The describe lambda now audits operations requests and fulfillment requests both as {'op': 'Request'}, because both classes are named Request, so the audit log cannot tell the two protocols apart.

   Failure scenario: A forensic review of ask.sock audit lines after a withdraw cannot tell an approval.operation.v1 withdraw/post from a fulfillment request; both appear as op=Request, and handler-failed audits use the same op.

8. `src/heterodyne/admind/daemon.py:922`: post_approval repeats the bead_binding refusal that its only caller, serialised_post, has just done under the same ask_post_lock, adding a second read transaction and a second copy of the refusal text.

   Failure scenario: Every legacy approval post opens two read-only transactions for the same check, and the two refusal strings must be kept in sync by hand. A future edit to one (say, the wording) silently leaves the other different.

9. `tests/test_admind_operations_r1.py:183`: No test covers how C1 withdrawal interacts with authenticated retirement: a suspended (maintenance-held:*) attempt that is withdrawn and then retired, or a historical or proof-incomplete attempt reaching the withdrawn untouched-cancel branch.

   Failure scenario: The merged composition passes CI (7776 passed, 74 skipped), yet nothing pins whether a withdrawn suspended attempt can still be retired through !asks retire, or whether withdrawal may cancel a proof-ineligible attempt, so either behavior could regress unnoticed.
