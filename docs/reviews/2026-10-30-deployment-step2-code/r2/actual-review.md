I ran the built-in `code-review` skill at **high** effort on `3f83729` against `eef8c8f`. It read the preserved R1 review, the receipt and the qualification doc first, and it worked read-only: no edits, commits, live SQL, services, queue actions or GitHub comments. The skill set its own effort for the review agents it ran, so I can't confirm they all ran at high. It also didn't check the file hashes you listed, and neither did I.

## Verdict: CHANGES REQUESTED (narrow)

Repairs 2, 3 and 5 are fixed. Repair 4 is fixed in source: the command line now names only the fixed root helper and the full outer digest. Repairs 1, 6 and 7 are only partly fixed. The serious problem is repair 6: an operator can approve a card that admind changed, and the executor still accepts it. The qualification doc says this case is refused.

The 84 focused tests pass on `3f83729`. The review doesn't ask for any runtime that doesn't exist yet. The helper install, the producer that stops work after a step is issued, and the other operational prerequisites stay a public executor HOLD, and the doc discloses them honestly.

## Ranked findings

1. **The executor doesn't hide login directories the way admind does** (`src/heterodyne/actions/deployment_outage_authority.py:47`, and the same copy at `deployment_authority.py:141`).
   - The executor's "context must survive redaction unchanged" check uses the global `redact()`. That only hides login directories after `set_login_dirs` has been called, which admind does (`cli.py:71`) and the executor never does.
   - **Failure:** release context contains `~/.claude-b`. The executor's check passes. admind shows the operator `<redacted login dir>` but stores the raw text. When the operator says yes, the raw text matches and the changed card is accepted.
   - The test only passes because it patches `redactor._login_dirs` directly.
   - **Fix:** use `redact_with_dirs` with admind's pinned login directories, or refuse when they can't be resolved.
2. **The operator can't check the outer digest against anything** (`deployment_outage_authority.py:37`). The card now shows the full outer packet digest, but its `Packet:` URL still points at the inner Release. The outer packet can't be published at the commit the Release names, so the operator approves a value they can't look up. The doc overclaims this at `qualification.md:39-41`.
3. **A capsule cancelled before step 0 is stuck forever** (`deployment_outage_journal.py:241`). `fail_never_issued` only accepts phase `ready`. If a cancel arrives before step 0, the phase is `recovery-hold` even though the journal can prove nothing was issued. The target is then never released, and only a hand edit of the database clears it. This is the most common no-effect case.
4. **The packet doesn't pin which root helper runs** (`deployment_host_store.py:226`). The helper path is hard-coded and the packet has no field for the helper's identity or hash. Whatever binary sits at that path when it runs is outside what the human approved.
   - The contract also doesn't require the helper to re-check things itself as root. Without that, the executor account, which owns the store the digest refers to, could pick the packet.
   - **Fix:** add a pinned helper role to the packet schema now.
5. **The card size limit doesn't match the transport chunk size** (`deployment_outage_authority.py:50`). The 3500-character, 40-line limit doesn't guarantee one Marmot chunk, but the binding requires `card_parts == 1`. A card that splits fails safely, but the operator's yes is wasted. The test only checks one part with `chunk_chars=8000`.
6. **The already-merged Step 1 release card changed without disclosure** (`deployment_authority.py:128`). The digest line format changed, the group line is now hashed, and redaction refusal is new. Step 1 asks already posted with the old body will now hold, and the `adapter_sha256` pin changes for every Step 1 packet. Neither the qualification doc nor the ADR mentions this. The operator also now sees a hash of the group ID, which they can't check, instead of the ID.
7. **Every journal open takes a write lock and rebuilds the index** (`deployment_outage_journal.py:98`). It runs `BEGIN IMMEDIATE` plus drop and create index even when nothing needs migrating. With `timeout=0`, any concurrent status or reconciliation open fails while the executor is mid-transaction. The schema check at `:91` also runs outside the transaction, so it can race.
   - **Fix:** migrate only when the existing index matches `_LEGACY_ACTIVE`.
8. **The installer command line accepts any target** (`deployment_host_store.py:221`). `_installer_argv` checks the packet only against its own target, so a target missing from the protected mapping still passes. The separate `validate` call after it also repeats work already done.
9. **Duplicated card-checking code** (`deployment_outage_authority.py:46`). The redact-then-fit check is copied from `deployment_authority.py:140-145`, so the fix for finding 1 has to be made twice. The R1 duplicates (R1 findings 8 and 9) are also still there.
10. **A new shared helper weakens a guarantee** (`src/heterodyne/harness/digests.py:30`). The generic `of_bytes` helper lets any caller turn arbitrary bytes into a digest that can be shown on a card. Previously only manifest or file-descriptor sources could produce one, so the module's "a card can't show a value it was handed" promise now depends on caller discipline.

## How the R1 repairs stand

| # | Status | Remaining gap |
|---|---|---|
| 1 | Partial | Nothing published to check the outer digest against (finding 2) |
| 2 | Fixed | None |
| 3 | Fixed | None; each card type is refused by the other's binding |
| 4 | Fixed in source | Helper identity not pinned by the packet (finding 4) |
| 5 | Fixed | None |
| 6 | Partial | Login-directory gap (finding 1) and transport chunk sizing (finding 5) |
| 7 | Partial | Cancel-before-step-0 stays stuck (finding 3); after-issue cases are deliberately left to the producer, which is acceptable |

Apart from those two overclaims (`qualification.md:39-41` and `:52-54`), the doc's account of what is still dormant is accurate.
