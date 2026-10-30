**Verdict: CHANGES REQUESTED.** Don't merge PR143 yet. Two findings mean a "yes" on the outage card would approve things the operator can't see and wasn't told about.

I ran the built-in `code-review` skill at high effort (forked) over `eef8c8f..a05fff8`, read-only, with no edits, commits, comments, asks, live DB, services or queue. All 10 files you listed match your sha256 values at `a05fff8`. The architecture doc isn't in this worktree, so the review read it at `d8d97eb` in the design repo; its content is byte-identical to the approved `3d4822e` (sha256 `e95e7522…`).

### Findings, most severe first

1. **The operator never sees the outer packet digest** (`deployment_outage_authority.py:31`). admind's `redact()` replaces any run of 64+ hex characters with `<redacted hex key>`, and the `Packet:` URL points at the inner Release. Two packets that differ only in a transition `after_sha256` or route credential hash produce identical cards. That contradicts the qualification doc's claim that the human reviews the complete outer digest.
2. **The card doesn't disclose the transport gap** (`deployment_outage_authority.py:36`). The architecture requires the card to say that a queued but undelivered Marmot reply is not a received revocation, and requires the operator to explicitly accept that gap.
3. **Outage cards look like routine release cards** (`deployment_outage_authority.py:38`). They reuse the Step 1 title "Exact release" and the same opening line.
4. **The installer argv can only run under a root-bash sudoers rule** (`deployment_host_store.py:223`). For `sudo -n env -i … /bin/bash -euo pipefail -c <script> …` to run, sudoers would need a wildcard rule, which amounts to arbitrary root bash for the executor UID. It's dormant now, but the argv should target a fixed root helper.
5. **The ingress timeout is per `recv`, not per read** (`deployment_host_store.py:62`). A peer that passes `SO_PEERCRED` and sends one byte every ~1.9s can hold the handler for about 17 hours. It needs a monotonic total deadline.
6. **The size check isn't admind's real fitting function** (`deployment_authority.py:138`, and the outage copy at `:39`). It measures unredacted text, but admind computes `fit(redact(...))`. Login-directory redaction makes text longer, so a body can pass here and then be stored as truncated and refused permanently. Hex redaction makes text shorter, so some bodies that would fit get refused. This fails closed, but the docs' claim is inaccurate.
7. **The journal has no failed or no-effect outcome** (`deployment_outage_journal.py:164`). An installer that refuses without changing anything leaves the job `uncertain` forever and excludes the target until someone edits the DB by hand. The architecture requires receipts to distinguish failed from uncertain.
8. **Duplicated corroboration logic** (`deployment_outage_authority.py:82`). `_capture_checked`, `_capture` and `_sources` nearly copy `release_auth`. That's security-critical logic that can drift; it should be parametrized instead.
9. **Duplicated validation** (`deployment_outage.py:67`). Digest and nonce checks reimplement `deployment._HEX` and codec's identifier validation.

The review also checked whether the SQLite path through `/proc/self/fd/N` could be redirected by a rename. A rename experiment showed the database still lands in the anchored directory, so that one was dropped.

### Not contested
- I'm not asking for the held runtime pieces (MDK custody, independent-route qualification, cessation reconciliation, host runner) before this foundation merges. The PR doesn't claim them.
- Nothing was found against the native identity, card/ask/operation ownership, or the unchanged admind core binding.
- I didn't rerun your test or lint results (76 focused PASS, Ruff, strict Pyright, hooks), and they weren't contested.

**Minimum to clear:** fix 1–3 (put the outer digest on the card in a form redaction leaves intact, add the transport-gap acceptance text, give outage cards their own title). Fix 5 and 6, add a failed outcome to the journal (7), and retarget the installer argv at a fixed helper (4). Items 8–9 are cleanups.
