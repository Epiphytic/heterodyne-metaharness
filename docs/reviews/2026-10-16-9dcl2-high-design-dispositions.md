# 9dcl2 one HIGH design review: author dispositions

Actual review SHA256 `811316f12690bc8fe1add3e2a97f5b2a886d35dd86b7d96127c110f33db6add5`. Reviewed 59608f866b23f5f88bc5cf27bb3e3f34fb13b7ab; actual verdict needs revision. One review used; no second review or validation. Findings below retain verbatim fields and original order. Author incorporation does not grant approval.

## 1

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.md:17`

HIGH: The design doesn't say how the default root is validated. 'Also canonicalized/ownership checked' could mean the configured-root rule (owned by the effective UID, not group/world writable, no symlink components) also applies to the default. On this host /home/openclaw/repos is mode 0775 openclaw:claws, and claws includes ironclaw and liamhelmer.

If the implementer applies the configured-root rule to the default, every inspection under ~/repos refuses (inspection-policy or not-owned). Every Handoff card and management plan stops working on deploy, including Y's repository side. If instead the default skips the mode check, the default root is weaker than configured roots and nothing says so. Missing ~/repos also has no defined outcome (outside-roots or inspection-policy). Fix: state exactly how the default is checked. Use realpath of pw_dir/repos, require a directory owned by the effective UID, and either keep today's no-mode check (disclose it) or make chmod 0755 an explicit deployment precondition. A missing default should give 'outside-roots', not inspection-policy.

Accepted. Default owned directory keeps existing 0775 tolerance; missing contributes no root/outside-roots, wrong owner/type inspection-policy. Configured additions remain strict. No chmod authorized.

## 2

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.md:13`

HIGH: Fixing home to pwd.getpwuid(os.getuid()).pw_dir with no test seam breaks today's isolated tests, and the required offline cases can't be built. Tests would read the operator's real policy file.

tests/test_handoff.py:689-701 creates repos under HOME=tmp/home/repos. Under the new default root they refuse as outside-roots, so the existing suite fails. Cases 1, 4 and 5 need the policy to be absent, malformed or unsafe, but the canonical path is the real /home/openclaw/.config/beads-task-queue/policy.json. After deployment that file contains worktree_inspection_roots, so case 1 ('absent key') can't be produced without writing to the real home. The test harnesses already assert that nothing points into REAL_HOME. Fix: specify one in-process seam (for example, _trusted_identity() returns (uid, canonical home)) that tests patch with mock and that is never set from env, argv or task data. Tests must assert they never open the real passwd home. Disclose that subprocess-level tests can't use the seam.

Accepted. Internal OS-derived _trusted_identity() has only an in-process mock seam; fixtures align disposable HOME and identity and forbid real home reads. No subprocess mock claim.

## 3

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.md:33`

HIGH: Management write readback and resume re-run the full inspection. bin/btq-manage.py:295 _observed_bead calls evidence_snapshot, and move() runs _current_and_check on every resume. A policy edit, a policy file mode/parse drift, or a rollback between an 'issued' step and its readback strands the request.

Example: a dependency replacement (add, then remove). Step 1 'add' is issued, then the root list is edited or rolled back. Readback raises ValueError('worktree was not inspected (inspection-policy/outside-roots)') instead of verifying the issued write. Every resume then fails 'Management facts changed; post a fresh card'. The add is live, the remove never happens, and the design forbids repost or retry, so there is no way to finish. Fix: make readback use bead-only facts with no worktree inspection, or pin the record's policy digest for the record's lifetime. Also state that root-setting edits are operator changes allowed only with pickup paused and no non-terminal handoff or management records, the same precondition as the initial switch.

Accepted. Issued native outcomes use exact bead-only projection/readback before full resume validation; every new step still checks current full evidence. Policy edits require zero non-terminal records, and drift holds remaining steps without replay.

## 4

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.md:53`

MEDIUM-HIGH: The rollout doesn't require the canonical bin/btq and the management bundle's bin/btq to switch together. Both share one handoff state dir (Path.home()/.local/state/beads-task-queue) and HANDOFF_FORMAT 1, and the facade exposes 'handoff'.

Canonical btq is upgraded first. A Handoff card posted with worktree.inspection_policy_sha256 is later resumed through the old facade bundle, or the reverse. The digests never match, so the result is a permanent 'facts changed; post a new card'. Meanwhile the old side still refuses Y as outside-roots. Fix: require both installs to switch in one pause window, with no non-terminal records under either, and cover both in the rollback steps.

Accepted. Canonical and facade queue copies switch/rollback in one paused window with zero non-terminal records; shared state and mixed-reader danger explicit.

## 5

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.md:46`

MEDIUM: 'Old-format request holds without replay' doesn't say how an old record is detected. Neither obvious implementation is safe.

Bumping HANDOFF_FORMAT makes Handoff.load (bin/btq:2141) treat every older handoff.json as 'malformed; ask Bel to inspect it', including done and aborted ones. That blocks all future handoffs and management for those beads. Not bumping means old records just fail the generic 'facts changed; post a new card / post a fresh card' check, which invites the reposting the design forbids. Fix: keep format 1. Detect a non-terminal record whose saved worktree facts lack inspection_policy_sha256 and refuse with a dedicated hold message. Leave done/aborted records readable.

Accepted. Keep format 1, detect missing marker only on saved non-terminal successful local evidence; dedicated hold, terminal records readable, absent/other-host not misclassified.

## 6

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.md:17`

MEDIUM: The strict canonical policy read becomes a dependency of every inspection queue-wide, possibly even for beads with no worktree ('at each evidence snapshot'). Its parse rules differ from the lenient load_policy that approvals still use (bin/btq:48, bin/approve-bead:565,1318).

Suppose the operator's policy.json has a duplicate key, grows past 64KiB, or is saved by an editor as mode 0664. Approvals keep working through json.loads, but every Handoff and management snapshot on every bead fails with an opaque 'inspection-policy', even for tasks entirely under ~/repos or with no recorded worktree. Fix: say whether no-worktree and other-host snapshots load the policy at all (they shouldn't need to). Document that this is an intentional queue-wide availability dependency in the deployment disclosure. Add an OPS read-only validation step that reports the failure category without printing contents.

Accepted. No-worktree/other-host skip root policy. Strict local inspection availability dependency disclosed; OPS read-only validation uses same reader and normalized safe category.

## 7

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.md:33`

MEDIUM-LOW: The digest binds the whole effective root set, which is the same for every task, so any root edit invalidates every pending card and request across the queue, not just ones that use the changed root.

Narrowing /home/openclaw/.hermes/workstreams to a run-specific root, which the design itself anticipates, or adding a root for another controller invalidates every pending Handoff card and management request. That includes tasks whose worktree and repository are both under ~/repos, so each needs a new human per-operation approval. Fix: bind uid, home, and the specific roots that admitted the worktree and the repository. Removing or changing an admitting root still invalidates; unrelated edits don't.

Accepted. Hash UID/home and deterministic worktree/repository admitting roots only; unrelated additions and reordering do not invalidate current evidence.

## 8

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.md:33`

LOW-MEDIUM: The new field only feeds the hidden 12-hex facts digest. The Handoff and management cards never show the approver that the worktree was admitted through a non-default root.

An approver looking at Y's status-correction card sees the same worktree line (HEAD, status, diff) as for a ~/repos worktree. They can't tell the inspected checkout lives under a broad controller-runs root that covers every run. Fix: add one card line naming the admitting root (or 'default') and the policy digest prefix, within the existing card budget.

Accepted. Both card types display literal admitting-root/default and policy-prefix line within whole-card budget; full values in facts.

## 9

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.md:9`

LOW: The design says to reuse _read_nofollow for the policy read, but that function raises Uninspected('filters') for symlinks or unreadable files and 'too-large' past the limit. That conflicts with the required single safe reason 'inspection-policy'.

A symlinked or oversized policy.json would be reported as 'filters' or 'too-large', which misleads the operator and breaks the case-4 assertions. Fix: wrap it and convert every failure except absence to Uninspected('inspection-policy'), or name a dedicated bounded reader that also does the ancestor walk and duplicate-key rejection.

Accepted. Dedicated reader normalizes every nonabsence read/type/ownership/size/parser failure to inspection-policy, preserving no raw exception/content output.

## 10

Location: `docs/adr/2026-10-16-controller-worktree-inspection-roots.inputs.json:8`

LOW: The inputs manifest leaves out files the design's claims and scope rely on: tests/test_handoff.py and tests/test_manage.py (their HOME-based repos layout is affected), bin/approve-bead (a load_policy consumer), and harness scripts/workstream_manage.py at c2cd3ae6 (the facade's handoff and management route).

Later byte verification of the design can't show which test fixtures and facade behavior the author relied on. Changes to those files between design and implementation go unnoticed, for example the facade route that makes the mixed-version problem possible. Fix: add these paths with git_blob and sha256 entries.

Accepted. Added exact test_handoff.py, test_manage.py, approve-bead and harness workstream_manage.py inputs. Existing inputs preserved.
