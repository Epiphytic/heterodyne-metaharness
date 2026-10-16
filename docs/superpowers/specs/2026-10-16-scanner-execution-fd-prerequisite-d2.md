# Scanner execution amendment: exact hostread test prerequisite d2

Proposed design only, planning task btq-1a845. Supersedes d1's admission/execution
procedure; frozen d1 commit d1bfc8da0e39fd5827edd44f434d1fcd9fe61691 remains history.
Actual built-in design R1 was REVISE with 13 findings; one of three rounds used.
No new verdict, implementation or approval is asserted. See [manifest](../../reviews/scanner-boundary/execution-fd-prerequisite-d2-inputs.json)
and [all 13 dispositions](../../reviews/scanner-boundary/execution-fd-prerequisite-r1-dispositions.md).

## Exact decision and preserved scope

Admit only test-only commit `9a14b3b2664e4ea0c5452c9eea013bf37aab658f`, based
on `2c0c5b5b775ee652d2ce32c192091aa2314e5b32`, as a separately admitted hostread
implementation prerequisite to scanner btq-1wu2i at exact source
`07637745fe75dc614109a0b8ba72d3489f262a97`. Existing scanner d3, original
btq-pxtig approval/digest/ADR pins, metadata19 cases, expected failures and all
historical review counters remain intact. This approval would authorize one
renewal of each listed gate, not retries until green. B2 remains untouched until
all renewed B1 gates pass. Root alone operates queue, approvals, push/merge and live systems.

The [actual code](https://github.com/Epiphytic/heterodyne-metaharness/blob/9a14b3b2664e4ea0c5452c9eea013bf37aab658f/tests/test_sandbox_hostread.py)
and [exact patch](https://github.com/Epiphytic/heterodyne-metaharness/commit/9a14b3b2664e4ea0c5452c9eea013bf37aab658f)
are published under design/scanner-hostread-test-prerequisite-9a14. Publication is
not approval or a fresh implementation review. Keep this immutable object reachable.
Only tests/test_sandbox_hostread.py differs: original/current Git blob
306b1fcc632be1eaad921b532bd93e47428c8bda becomes
53b1af47be9a4d71d7bd80fafe3719d7aa62b375. Production hostread stays
2864c87cef46443a039422eeb2f0f1d0fe85f082 in all three trees.
Manifest records complete SHA256, bytes, exact single-path patch and overlay proof.

The patch replaces global FD count equality with frozen (fd, device, inode)
subset comparison. Unrelated descriptor closure is permitted; newly retained
identities fail. Existing home/child/no-kill/deadline assertions remain. The added
SQLite-cycle regression proves a count decrease and detection of a new pidfd.
Do not import broader host-wide scan/subreaper, channel or daemon fixes. Those
availability modes remain present at076, including unrelated live non-dumpable
same-uid processes causing recovery uncertainty, retained children and timeouts.
A serial run can encounter these modes; no unexpected failure is waived.
This narrow task neither duplicates nor completes btq-gtetp SQLite cleanup.

## Honest evidence and accepted limits

Both original3372-node B1 runs actually exited1 with five failures: four exact
scanner assertions plus test_no_readable_worker_settles, global counts43→25
and40→25 respectively. Their log/JUnit/collection hashes remain immutable failures.
An isolated original91-case module passed; a private cyclic SQLite reproduction
released18 FDs. These support the GC explanation but neither failing full run
captured descriptor identities, so its complete attribution is inferred.

The pinned historical brief names cycle1; report says APPROVE, Python3.12.3,
92 serial passes,92 with -n8 and one instrumented regression pass. Neither report
nor brief identifies reviewer model. Do not infer it from a co-author. These are
historical observations, not current-base evidence or fresh review waivers.

Identity sets do not prove preservation of every old FD or universal leak detection.
The same inode reopened at the same FD can evade detection; anonymous inode types
can share identity. The separately archived author note, **not mentioned in the
pinned historical brief/report**, says before Linux6.9 every pidfd shares one inode:
a leaked pidfd reusing a closed pidfd's number is missed. Its probes only establish
behavior on the recorded host kernel. The regression does not force such reuse.
Do not mislabel this supplemental limitation as a pinned review finding.

Two low costs of the exact patch are accepted for this narrow admission: the new
regression invokes full GC during each simulated sleep, and bare subset failures
have weak identity diagnostics. Record its actual duration and retain the complete
first failure. Do not trim calls, repin the blob, or rerun merely for diagnostics.
Any broader correction needs distinct explicit scope/review/approval.

## Native admission: a denial must keep scanner blocked

A second closed approval edge on scanner is insufficient: native design_allowed
checks its existing design_approval=btq-pxtig, and denied approval cards also close.
Use existing supported separate-target admission instead; no new exemption or
queue implementation is requested by this amendment.

1. Root creates an actual hostread-only **kind:task** target H and approval A_H
   through supported APIs, parked until admission. H's acceptance includes only
   this exact patch, fresh92-case serial proof and genuine complete code review.
   H is newly gated, with design_approval=A_H, adr_revision=the final signed
   amendment admission revision, plus its native blocking dependency on A_H.
2. Add scanner→H as a native blocks edge **before** releasing either target.
   Preserve all scanner dependencies and its create-only original approval pins.
   Scanner remains parked until H has genuinely completed, its reviewed patch is
   available, and all remaining scanner gates pass. A_H denial/invalidity leaves
   H unclaimable/incomplete; H's unresolved edge continues blocking scanner.
   Never close H administratively to convert a denial into completion.
3. Before posting, record real H/A_H IDs, exact before/after dependency state,
   final signed docs revision, immutable GitHub design/manifest/code links,
   actual cross-model design review record, and the actual required ADR-glob
   archive/reference validated by native approval checking. No invented IDs,
   approved_* fields, copied old approvals or branch-name-only pins.
4. The Marmot ask must explicitly cover this test oracle change, residuals,
   new dependency shape and one renewed gate sequence. Native validation must
   establish APPROVED for H at its exact revision, not merely closed status.
   The signed admission receipt links this real decision and original pxtig.
5. Scanner's existing stopped/parked state also needs its actual supported OPS
   recovery link, before-state expectation and authenticated authority for the
   narrowly described resume. Obtain these **before** posting the card; absent
   IDs, link or authority means no card yet and no execution. Do not borrow an
   old holder/session or treat this document as an OPS grant. Rejection keeps
   scanner parked; report via root's authenticated Marmot path.

These are admission prerequisites, not instructions to fabricate records. The
manifest deliberately has null future IDs until root obtains actual evidence.
No new approval replaces or relaxes the original scanner contract.

## Reproducible source transition and landing

H implements/reviews the exact historical patch in its own claimed worktree;
root lands only approved H code/docs with passing gates. Preserve **actual9a14b3
ancestry** by merging that exact one-path commit (its parent2c0c is already in076),
not cherry-picking to an equivalent X while claiming9a14b3 is an ancestor. Pin
merge parents/tree and signed integration commit; import no other branch delta.
Existing broader plans' ancestor checks then remain honest. Their other deferred
prerequisites are not declared complete by this merge.

Land this final amendment, its manifest, dispositions and immutable evidence
archives through the same reviewed prerequisite delivery; scanner's branch must
also contain these exact approved docs before final review. Retain d1 unchanged.
No unsupported unreviewed docs-only side branch is treated as landed authority.

Before changing scanner checkout, capture actual HEAD, receipt identity,
porcelain, B1 three-source byte hashes, B2 target hashes, exact planning commits
and test commit1995977b7e0fab4d6caa8ef5d50fea2d80886fa4. Compare with immutable
b1.patch/b2.patch, old scanner pins and admitted076 guard/create/config work.
Save full original B1 bytes and patch outside checkout, fsync and hash them.
Do not commit or invoke hooks with unstaged B1 present: temporarily reverse only
the verified B1 patch to its known committed bytes, check clean state, then do
the signed docs/prerequisite integration. No tests run in this temporary state.
Reapply exact archived B1 and require original three-file hashes, same expected
porcelain, unchanged B2 files and exact fixed hostread blob afterwards. A crash,
hook failure or mismatch holds immediately with both snapshots retained; root
must reconcile it, never assume hooks restored a stash or silently regenerate B1.

The untracked .venv symlink is not a clean candidate. Record its exact target,
then remove **only that alias**, never its environment directory, before clean
commit/full-run checks. Run tools by absolute private interpreter; any temporary
Pyright alias must be absent again at freeze. Do not weaken empty porcelain.

## Evidence tooling and environment contract

Manifest pins byte-exact historical verifier,19 negative probes, pytest identity
plugin,81-module list, overlap/full drivers and B1/B2 inputs under
[immutable archives](../../reviews/scanner-boundary/fd-prerequisite-evidence/).
These are inert historical text exhibits. Driver/patch archives use explicit base64 storage to preserve historical
install paths and patch context whitespace without treating them as configuration; manifest
pins both stored bytes and strict-decoded original bytes. Old drivers' fixed3372 counts, stage
paths and environment are **not** silently executable3373 policy.

New execution-driver adaptation after approval is limited to unique run paths,
explicit checked interpreter/source origins, and the collection delta below.
Keep the archived verify()/mask() algorithm and nineteen rejection probes
byte-identical; no expected failure/strict status rule may change. Record the
actual driver bytes/hash before invocation alongside archived verifier/plugin/
probe/module/expected-data hashes, and retain the source for final scanner code
review. Before a test starts, its collection must satisfy every declared predicate;
this cannot be deferred until after a full run. Any other driver semantic change
requires root review/escalation before running; no mutable 'retained' alias.

Use the exact076 uv.lock/pyproject inputs and existing private locked Python3.12
with offline fake/private tmux only. Record interpreter realpath/version, lock
hash, installed distribution/version inventory and editable import origins for
scanner/redactor/creates. If /tmp environment vanished or rebooted, recreate at a
fresh private path using locked sync and the same requested feature-complete3.12
interpreter, then recheck inventory/origins before any gate. Never substitute3.14,
upgrade dependencies or infer provenance solely from executable pathname.

Every attempt uses a new exclusive private run-ID directory created after source/
clean prechecks; existing directory means hold, not overwrite or reuse. Record
actual status even when verification raises; preserve log, JUnit, collection,
source/env/driver hashes and elapsed time, including aborted attempts.
Raw file hash means complete bytes including formatting/newline. Ordered-node
semantic hashes are separate: legacy uses Python json.dumps(nodes) UTF8/no newline;
portable uses ensure_ascii=True, compact comma/colon separators, UTF8/no newline.
The old3659430f… value is **raw collection-pinned.json**; old ce942161… is the
legacy semantic list hash. Manifest names each, so neither comparison is ambiguous.

## One renewed strict sequence; no rerun roulette

After genuine native admission and H completion, root reserves the sole broad
slot. One sequence is authorized, preserving original failed runs and oracles:

1. H's fresh92-case serial hostread proof includes its added regression; retain
   exact collection, actual exit0, no unexpected outcome and per-node duration.
   Reuse that fresh source-pinned evidence only if scanner's hostread/import/env
   bytes match; otherwise require one matching92-case gate before proceeding.
2. Renew red baselines in disposable copied source only: restore the three
   B1 source files from076, retain approved tests/hostread overlay and prove all
   import paths/restored hashes. Exact t1=53fail/35pass of88; create=4declared
   embedded-field assertion failures/2control passes of6. Expected node data stays
   52d8772fa92925addc23d87b203adb95aca60400681c86a405a190c07ec476e0.
3. Real B1-only81-module collection must equal old3372 **ordered** nodes plus
   only tests/test_sandbox_hostread.py::test_a_leaked_fd_that_another_test_left_closing_meanwhile_is_no_leak
   at its module position. Removing that one node must reproduce the old list
   byte-for-byte semantically. Require3373, raw/masked uniqueness, no skip/error/
   xfail, actual exit1, precisely the original four scanner assertion failures
   and3369passes. All88 AppendixA,6create,92hostread and19metadata cases pass.
4. Disposable widened scanner/original redactor overlap: exactly4declared
   assertion failures/3passes of7 with pinned import/redactor hash. Only after
   these gates pass apply exactly approved B2 four-fixture changes, preserve
   unrelated guard hunks and hash both resulting test files. Same3373 allgreen.
5. Run existing Ruff, Pyright, install-agnostic and own-text checks. Freeze signed
   clean complete candidate including docs and exact prerequisite. One fresh
   serial offline full run: timeout3600, HZ_REQUIRE_TMUX=1, absolute locked Python,
   -m pytest -q --ignore=tests/live with pinned identity plugin. Fresh collection,
   exact statuses, justified normal skips, same HEAD/cleanliness before/after and
   complete hashes required. Hosted secrets/unfiltered CI retain live opt-in rules.
6. Root obtains actual fresh cross-model scanner implementation review, maximum
   three rounds, on complete076..HEAD including prerequisite/docs/tooling context.
   H separately needs its genuine task code-review evidence; historical review
   is not a substitute. Preserve all prior counters. Merge only after actual
   review/gates and existing user merge authority; no extra merge ask inferred.

**Any unexpected failure stops this sequence immediately.** Preserve the attempt,
mark needs-human through root, and send concrete diagnosis/unknowns via authenticated
Marmot. The executing agent cannot lift the hold, rerun a failed gate, apply B2 or
change its oracle. Prior two failures remain in history; this amendment's genuine
human approval authorizes only the single renewal above. Another attempt needs an
explicit authenticated operator disposition identifying failure and allowed scope;
production/design changes also require their genuine gates. No silent self-unhold.

Success admits a repeatable honest scanner path, not universal FD identity,
SQLite cleanup or completion of deferred plan5i consumers. No live backend,
host/service/deployment, dependency upgrade or unrelated cleanup authority.
