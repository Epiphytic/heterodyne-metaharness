# Scanner private execution environment recovery

Status: proposed; no retry or new test execution authorized by this document.
Author: Codex. Design bead: btq-sxni3. Implementation remains btq-1wu2i.
Amendment ADR path: docs/adr/2026-10-16-scanner-private-environment-recovery.md.
R=D means the final published amendment commit is the exact approval revision;
its native approval supplements original scanner approval, never rewrites it.
Base D4 is delivered by e22f4ed366d6ddcdc1a5d7f0c9fe30d088b12ea8 at
docs/adr/0002-scanner-execution-fd-prerequisite-d4.md, raw SHA256
`e551a9a287f1ce850bc87cfa196e905b1b35747f0dd95c53b2e9e0d373bed840`.
Its historical UNREVIEWED label is preserved; actual prior authority is recorded
Marmot/native approval, not that label.

This additive amendment changes only the private execution environment and the
explicit recovery sequence of ADR 0002 D4. Scanner matching, production code,
expected outcomes, collection identities, and historical evidence stay unchanged.

## Actual failure and responsibility

Renewed B1 attempt a22759f7-d5c5-45a6-9a2e-d9d605a4f7cb collected and executed
the required 3373 nodes, but recorded 379 failures and 2994 passes in 718.04s.
Its strict verifier correctly returned HOLD for a nonassertion failure. The
expected four scanner assertions are among those failures. Actual result SHA256:
`d036ff9b308c04298b288b5a5d54efc133fe7b346eee94c016c268d99e13db4e`.
All raw logs, JUnit, collection, source receipts, and durations are retained.
The source snapshot after the attempt equals its admitted source receipt.

The controller prepared a fresh private TMPDIR underneath a long evidence path.
AF_UNIX socket filenames derived from that directory exceeded the kernel limit,
causing sandbox/channel failures and their downstream assertions. Two generated
unit checks also reported that systemd-analyze could not resolve RuntimeDirectory
in the isolated environment. These are observed causes, not a claim that every
unexpected failure has already been explained or that the corrected run will pass.
No retry, overlap, B2 transition, or final suite followed the HOLD.

The complete 379-node table is docs/reviews/scanner-boundary/environment-recovery-failure-map.json
SHA256 `12bd59c8d8ac1d8dea1b2857989c591e5e5d5ab2580ec76610da9db31a010ff6`.
It records 4 expected assertions,55traces explicitly naming overlong Unix paths,
2 explicit runtime-directory failures, and 318 UNEXPLAINED failures. Generic wrapped
OSError is deliberately not counted as proven path failure. The amendment therefore
authorizes one diagnostic pass of exactly375unexpected failed nodes, in their
original collection order, before any new gate. No new selected nodes or implicit
retry; require375passes/zero skips/errors/xfail and exact collection. If it fails,
retain actual output and HOLD for a new disposition. Its result is diagnostic,
never a substitute for B1 or the unchanged strict gates.

## Private short-path layout

Each new attempt creates one exclusively owned, unpredictable directory directly
under the real system temporary directory, using mkdtemp and mode 0700. The parent
is selected explicitly as `/tmp`, not from inherited TMPDIR. Validate its real
directory identity, ownership and mode without following a caller-supplied
symlink. Within it create private home, tmp, runtime and Hypothesis directories.
The attempt keeps all these directories until evidence and human disposition
permit cleanup, while recognizing /tmp is volatile. Before launch require the
real /tmp is root-owned mode01777, record mount type/options and require executable
files plus at least2GiB available space. At completion, including HOLD, archive
regular scratch files and symlink metadata to durable private evidence with a
hashed inventory; never follow symlinks, archive live sockets/devices, or inspect
external targets. Bound retained bytes to2GiB; capacity/archive failure is explicit
HOLD, not a preservation claim. Do not promise survival of pre-archive reboot.
It records their literal private paths and lstat identities;
public documents use logical locators. Never reuse a failed attempt directory.

HOME, TMPDIR and HYPOTHESIS_STORAGE_DIRECTORY point into this short private root.
Evidence, plugins and logs may remain in the durable controller evidence tree;
they do not become the parent of runtime socket filenames. Check filesystem
encoded byte lengths before launching any test process. Use prefix hz directly
under /tmp; require resolved root length<=16bytes and its one-letter child paths
<=18bytes. Pin pytest --basetemp=PRIVATE_ROOT/p explicitly in collection and
execution argv; never allow pytest's username/number nesting to grow from TMPDIR.
This is an explicit D4 argv amendment. Derive a source-backed socket-suffix
inventory for the entire offline suite before execution, including tmp_path's
<=32byte case component and admind's38byte nested path (source conftest budget),
short_dir direct TMPDIR fixtures and SessionLayout's session/rN/{s,p}.sock.
Require every inventoried reconstructed socket path<=100bytes, below Linux107;
missing/unbounded derivation HOLDs before diagnostic. Retained traces print a
10-byte hz fixture dirname plus p.sock (17-byte relative suffix), but many wrapped
failures omit the actual path: do not claim that truncated logs bound all paths.
Before the diagnostic, synthetic fixtures must successfully bind a private Unix
socket at exactly100encoded bytes and reject an over-budget path without a bind.
No live endpoint is contacted. Inventory, actual measured path lengths and margin
are required evidence. Unexpected longer real paths still HOLD, never truncate.


## Explicit runtime directory

Extend the empty-environment allowlist from exactly 13 to exactly 14 keys by
adding XDG_RUNTIME_DIR. Derive it from the newly created private runtime directory;
never copy the parent value, use the actual user runtime directory, or expose a
live user bus. The directory must be owned by the current UID, mode0700, real,
fresh, and empty before the attempt. No DBUS_SESSION_BUS_ADDRESS is passed.
systemd-analyze --user verify remains an offline generated-unit validation;
no service start, reload, bus call, or deployment is introduced.

All other 13 keys and values retain D4 meaning, including locked Python3.12,
active source first in PYTHONPATH, explicit plugins, no inherited variables,
and HZ_REQUIRE_TMUX=1. Record the exact 14-key map and new runner source hash.
Do not add environment keys opportunistically after any failure. No production
code, test, expected-failure JSON, verifier function, or dependency changes.

The additive docs/reviews/scanner-boundary/environment-contract.json pins
the original manifest raw SHA and original 13 keys. Its new set adds only
XDG_RUNTIME_DIR. The original51required result fields stay mandatory; six new
required fields are private_runtime_layout, socket_path_budget_inventory,
environment_filesystem_preflight, scratch_archive_inventory_sha256,
baseline_mode_disposition, and diagnostic_result_reference (explicit NA reason
only for the diagnostic itself). No old manifest bytes are edited. The adapted
runner loads and hashes this supplement and requires all57fields; the strict
verify()/mask() function bytes and19malformed-report probes stay exactly pinned.
These functions inspect status/XML/node/failure identities, not environment keys.
Separate new construction probes reject13keys,15keys, missing supplemental fields
and over-budget paths. Pin that new probe source/hash and report it separately
from the unchanged19. No evidence is treated as optional.

## One explicit authenticated recovery disposition

Approval must name failed attempt a22759f7 and the following permitted sequence.
Question rcje asks the operator to select baseline reuse or fresh red baselines;
its answer is retained with the separate approval. The native design approval must freeze one explicit mode before any recovery:
reuse only with authenticated rcje A; otherwise approval explicitly names fresh.
Never treat silence or elapsed time as approval. Read back the selected mode and
answer provenance before step1, retain its digest, and ignore late mode changes
for this sequence until a new explicit disposition. Neither the question nor
this proposed design clears needs-human.

1. Root authenticates approval, checks unchanged source/test/lock/interpreter
   and H-delivery receipts, and uses supported recovery/claim APIs. Keep existing
   design pins and history; do not manufacture a replacement task approval.
2. Reserve the sole broad slot and run the diagnostic described above. After
   its exact375PASS, under the corrected14-key environment run one
   fresh strict H92 gate from the exact delivered H test/product bytes. Preserve
   the previous passing H92 as historical evidence; it cannot substitute for
   compatibility with this changed environment contract. Use a disposable detached
   copy of H merge e22f4ed366d6ddcdc1a5d7f0c9fe30d088b12ea8, with its src first.
   Record scanner/redactor/creates/hostread actual baseline hashes from that exact
   commit and compare before collection/execution; H test SHA must equal
   35e94c4b67a8f50a7f73e2c16e838b137c3917ae034dc6b384c0922346f19f86.
   It has its own exact92collection file, no B1 overlay. Unexpected failure HOLDs.
3. If the operator expressly selected reuse, retain the actual t1 88-case and
   create-red 6-case PASS results as red-baseline evidence, documenting that only
   private path layout and the runtime-directory key differ. Otherwise run each
   once again in disposable baseline source under this environment. The original
   expected 53/35 and 4/2 outcomes and all other strict checks remain mandatory.
4. Run one new B1-only3373 gate. Only exact four expected assertion failures and
   3369 passes permit the next stage. Then one overlap7, the exact archived B2
   transition, one all-green3373 and one create-green6. No retries or skipped gates.
   D4 steps3–4 per-node requirements apply verbatim: legacy ordered-minus-H SHA,
   exact H adjacency, raw/masked uniqueness, passing88/6/92/metadata19 subsets,
   exact expected exit/status/failure identities and no error/skip/xfail.
   create-green6 is an explicit supplementary subset run of unchanged create-alert
   cases, all6PASS/exit0; it replaces no D4 gate or collection.
5. Preserve the signed clean final freeze, one final serial whole offline suite,
   actual implementation review and hosted unfiltered CI/secrets requirements.
   Full-suite declared normal skips remain separately justified; strict gates
   never accept skips, xfail, errors, changed identities, or changed failure types.

D4 broad-slot reservation, actual start/end, early release after each test batch,
no review wait while holding it, and loss-stop/no-completed-gate-rerun rules remain.
Each diagnostic/H92/baseline/B1+overlap+B2 batch/full-suite gets an explicit bounded
reservation; loss before the next child prevents that child. No other broad suite
runs concurrently. Source-backed private socket inventory covers final-suite
modules too; tests outside375remain unexercised until their genuine gate/full
run and may still reveal unknown failures, which HOLD. No universal success claim.

The new diagnostic, H92 execution, and any newly selected red-baseline execution, are explicit
changes from the prior one-renewed-sequence rule. Already-passing data is never
overwritten or relabelled. Bind each predecessor by actual path and raw hash.
Every attempt retains all 51 required fields plus original extra provenance,
source/import/inventory checks, raw and semantic hashes, negative19 verifier
probes, real exit/signal/timeout, durations, and actual source-before/after.

Root separately inspects and pins adapted helper code before invocation. Validate
only the bounded private path/environment construction and result serialization
with synthetic unit fixtures first; these are not scanner gate results. There is
no automatic retry loop, oracle relaxation, GC workaround, source rebase, node
drop, parallel pytest, legacy-driver execution, dependency upgrade or live action.
Any unexpected outcome immediately HOLDs for a new authenticated disposition.

Before launching, inventory all real tmux invocations in the selected and full
suite and require explicit fixture-owned -S/-L or the existing audited tmux guard.
Unbound default-server use HOLDs; do not infer isolation solely from missing env.
The private XDG directory must contain no bus socket before/after; record any
other new entry rather than asserting emptiness without a check. No inherited
DBus, keyring, live tmux or runtime socket is exposed.

## Review and approval

Use exactly one Claude Opus HIGH built-in /code-review design review. Preserve
the actual report, incorporate findings once and publish the final signed design
with a direct immutable GitHub link. The human request must list the changed
environment contract, fresh H92, selected baseline mode, failed-attempt hash and
retained requirements. No second design review or validation review is requested.
This is private test execution authority only. Deployment remains a separate gate.
