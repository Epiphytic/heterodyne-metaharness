# Hostread prerequisite R1 author dispositions

Actual built-in design R1 on076..d1bfc8d: **REVISE**,13 findings, report SHA256 `2bebab4e7abdee455add025fc63e4f173727c5362df819687f381c034b48cffc`. One of three rounds used. These are author dispositions, not reviewer clearance or human approval. Frozen d1 is retained; d2 requires actual next review and genuine native approval.

[Exact quoted findings and structured dispositions](execution-fd-prerequisite-r1-dispositions.json). [D2 design](../../superpowers/specs/2026-10-16-scanner-execution-fd-prerequisite-d2.md).

## 1. Fixed admission contract

Step 1's 'genuine blocking approval dependency' cannot gate btq-1wu2i. The pins are create-only (btq:554, 1476-1477) and already pass through btq-pxtig. design_allowed() reads only metadata.design_approval (btq:866-878). approve-bead closes the approval bead on both approve and deny (approve-bead:30-31, 1517-1521). So a denial, or an 'approval' that approval_valid() rejects, releases the blocks edge, and the spec defines no outcome for a denial.

**Disposition:** Separate newly gated H task pins A_H and blocks scanner. Denial cannot satisfy H design_allowed; H remains incomplete. Original pxtig stays unchanged. Actual IDs/approval/OPS evidence must exist before card.

**Basis:** bin/btq:859–878 checks only task primary design_approval; :827–853 requires actual APPROVED, not closure. Supported metadata pins/dependency model reused; no gate implementation added.

## 2. Fixed visibility and reachability

The human gate publishes GitHub links only for the design and manifest. The admitted code, 9a14b3b, exists only on refs/heads/fix/hostread-flake (no upstream, never pushed), and neither file includes the diff, so the approver cannot inspect the patch being approved.

**Disposition:** Actual immutable9a14 GitHub code/patch links are now published and pinned in manifest; publication explicitly confers no approval.

**Basis:** Parent publication receipt2026-10-16T07:15:54Z names exact commit/blob and canonical GitHub URLs.

## 3. Fixed finite failure policy

An unexpected failure becomes a hold that the executing agent can lift itself, with no rerun limit, no named decider and no needs-human escalation, although btq-1wu2i already has two failed strict gates. Rules: AGENTS.md:59 'Circuit breakers: 3 failures on the same task → halt and escalate to user'; AGENTS.md:66 '3 retries max, then escalate'; PICKUP.md:92-93 'Persist attempt failures on the bead; two failures go to Bel with `needs-human`.' Breaking line, spec:161-162: 'Any further unexpected failure is a hold with concrete diagnosis and complete affected-gate rerun, not an automatic baseline update or retry lottery.'

**Disposition:** One human-approved renewal, then unexpected failure stops immediately, root marks needs-human and sends authenticated Marmot escalation. No executing-agent rerun/self-unhold. Existing failed runs remain failed.

**Basis:** Execution section states escalation before B2 or another attempt; new authenticated operator disposition required.

## 4. Fixed clean/env/run lifecycle

Step 8 requires a 'signed clean complete candidate', but step 2 (117-118) records an untracked .venv alias in that same checkout and never says to remove or exempt it. The '.venv/' gitignore pattern does not match a symlink, so porcelain shows '?? .venv'. The retained run_full.py creates full-ci/ and then asserts empty porcelain.

**Disposition:** Record/remove only .venv symlink before clean freeze; absolute private tools. Missing/rebooted env recreated with locked3.12 sync and checked inventory/imports. Source prechecks precede fresh exclusive attempt-directory creation; aborted dirs never reused.

**Basis:** Archived run_full.py.txt documents original fixed-directory/clean preconditions; new finite protocol replaces these paths without weakening empty porcelain.

## 5. Fixed hash conventions

collection_sha256 3659430f… (lines 96 and 107) is the raw-byte hash of the pretty-printed collection-pinned.json, but the manifest names neither the file nor the encoding. The retained verifier that step 6 mandates computes this key as sha256(json.dumps(pinned)) (scanner_evidence.py:78), which gives ce942161… for the same 3,372-node list.

**Disposition:** Manifest separates raw collection-pinned.json bytes from legacy json.dumps list hash and compact portable ordered-list hash. Fresh results name each explicitly.

**Basis:** Both old3372 collection files were read without execution and their raw and semantic SHA256 recorded separately.

## 6. Fixed docs landing

The amendment gives no landing path for its own spec and manifest. Step 2 fixes the worktree as 'the declared planning commits' plus 1995977, and steps 3 and 9 add and review only the test prerequisite. So the scanner PR carries the changed hostread oracle and the 3,373 accounting with no d1 in the tree, unlike d3:135-137 ('Require exact 076 base plus only these planning documents: cherry-pick their signed docs commits onto 076').

**Disposition:** Exact final d2/manifests/dispositions/evidence land through reviewed prerequisite delivery; scanner imports same signed docs before final complete-diff review. D1 remains historical.

**Basis:** Source-transition section explicitly adds amendment documents to delivery and scanner review.

## 7. Fixed retained-tool identity; bounded future adapter

Steps 5-8 rely on 'the retained' strict verifier, negative probes, drivers and 81-module list, but neither this manifest nor d3's pins their bytes. These private files were edited during the task: the verifier went through versions A, B and C, and the original t2s2 run was judged by the weaker version A.

**Disposition:** Nine byte-exact historical tool/patch/module artifacts are archived and SHA256 pinned. Old3372/fixed-path drivers are not represented as ready3373 runners. New driver adaptation is limited to declared paths/env/delta; verify/mask and19 probes unchanged, actual driver bytes retained/hashed before invocation and visible in final scanner review.

**Basis:** Manifest includes raw bytes/SHA256 of verifier, plugin, probes, overlap/full drivers, AppendixA driver, module list and B1/B2; semantic changes beyond declared adaptation require root review/escalation. Driver/patch archives use explicit base64 storage to retain historical install paths and patch context whitespace; manifest separately pins stored-file hash and decoded original bytes/hash.

## 8. Fixed provenance and wording

Lines 96-100 present the pre-Linux-6.9 pidfd caveat as one of 'the retained review's disclosed limitations'. The pinned brief (8f070082…) and report (5f26a52d…) never mention 6.9; the caveat appears only in the unpinned pr-hostread-fix2-pidfs.md. The spec also softens that note's 'before 6.9 every pidfd shares one inode' to 'pidfds can share an inode'.

**Disposition:** Pre6.9 every-pidfd-shares-inode caveat attributed to separately archived author probe note, explicitly not in pinned review/brief; only recorded host kernel established.

**Basis:** Original pr-hostread-fix2-pidfs.md bytes copied/pinned as supplemental unreviewed historical author evidence.

## 9. Fixed real ancestry and downstream assumptions

Step 3 requires re-committing the 9a14b3b patch as a new signed commit, which loses the commit's identity. Merging fix/hostread-flake would add only 9a14b3b, since its parent 2c0c5b5 is already in 076. The spec also gives no disposition for pending artifacts that refer to that commit id.

**Disposition:** Preserve actual9a14 ancestry by merging exact commit whose2c0c parent is already in076. Pin parents/tree; do not claim a cherry-picked equivalent proves9a14 ancestor or broader-plan completion.

**Basis:** Original commit/object and published patch pinned; no branch merge, rebase or source changes performed by this revision.

## 10. Fixed B1 hook interruption path

Step 3 commits in a worktree with three unstaged B1 source edits but names no commit route and requires no B1 hash or porcelain recheck afterwards; step 2 checks them only 'before applying'. A plain `git commit` runs the shared pre-commit 4.6.2 hook, which stashes the B1 diff and checks out the 076 sources while the hooks run.

**Disposition:** Externally save/fsync/hash exact B1 files+patch; temporarily restore only verified B1 to committed bytes before hooks/commits; integrate clean then reapply exact snapshot and compare all3 hashes/B2/porcelain. Crash or mismatch holds for root reconciliation.

**Basis:** No assumption that pre-commit restores its stash; no tests run in temporary source state.

## 11. Accepted exact-patch performance residual

In the admitted blob (9a14b3b), the regression's patched sleep runs a full gc.collect() on every one of recover's 200 fake 0.05 s sleeps, though only the first is needed. In the gate's heap this is an estimated 8-18 s for one test, against about 0.06 s for its siblings. This is inferred; no recorded run includes the test. The exact-blob pin (spec:121-123) freezes the cost.

**Disposition:** Keep admitted exact blob; record actual new-regression/per-node duration in fresh92-case evidence. No claim isolated timings bound full-heap cost; no code trim or new test-oracle change.

**Basis:** Historical exact-blob scope retained; source implementation/performance changes require separate scope/review/approval.

## 12. Accepted diagnostic residual; explicit hold

In the admitted blob (9a14b3b), none_left() reduces the subset check to a bare bool, and no call site passes a message (352, 689, 783, 894). A leak failure shows only 'False = none_left(frozenset({...}))', with the baseline truncated to 240 characters under -q. It never lists the new (fd, dev, ino) identities, which an inline `open_fds() <= before` would print as 'Extra items', and it drops the counts the old assertion printed.

**Disposition:** Keep exact blob with weak bare-bool subset assertion. Preserve complete first failure/JUnit; cannot rerun for richer diagnostics or self-unhold when identity attribution is unavailable.

**Basis:** Universal diagnostics not promised. New code diagnostics would change exact admitted blob and require their genuine gates.

## 13. Fixed disclosure; availability risk retained

The spec excludes the larger hostread-flake design's host-wide scan/subreaper, channel and daemon fixes without saying that the flake modes they target are still unfixed at 076 and exposed inside the step 4/6/7/8 gates. For example, d2.r4 cause 2: hostread.py:337 raises _Unknown for a live non-dumpable same-uid process, which hits '36 cases in 21 functions'.

**Disposition:** Disclose excluded wider hostread failure modes still exposed at076 including live non-dumpable same-uid processes. Their recurrence is unexpected and immediately held/escalated; no assertion that serial scheduling removes them.

**Basis:** Decision/limits sections preserve narrow scope and explicitly decline broader hostread/subreaper/channel/daemon fixes or btq-gtetp completion.
