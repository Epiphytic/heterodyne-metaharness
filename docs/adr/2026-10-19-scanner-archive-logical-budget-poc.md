# Scanner archive logical budget: proposed PoC amendment

Proposed for human design-owner acceptance; **not approved or implemented**.
One actual Claude Opus HIGH built-in code review returned REVISE with ten
findings; the author incorporates them once here. The [actual report](../reviews/gyqsp-design-r1-high.md)
and [ordered dispositions](../reviews/gyqsp-design-r1-high.dispositions.md) preserve
that result. No second review or validation is requested or claimed. Root must
publish this revision and obtain genuine Marmot approval before enabling it.

## 1. Decision and exact scope

Approved ADR `0302d1b5145e1d861f445dcac71c414a547bdf3a`,
`docs/adr/2026-10-18-scanner-socket-inventory-clarification.md` §4 requires:

> All helper-root and independent-root artifacts share ONE combined2GiB archive
> budget and the existing combined65536-entry bound, reserved once before the child.

This proposal explicitly replaces a physical-block-reservation requirement with
one **logical** archive budget, B=2,147,483,648 serialized bytes and E=65,536
entries, established before the child and shared across helper and independent
roots. Filesystem blocks are not reserved. Payload, retained observer receipts,
all generated inventories and archive-created directories/files count; there is
no extra metadata or per-root allowance. Count the destination root itself once;
count each copied directory/file once and each excluded special object's metadata
row once. Symlink targets and sockets/devices are recorded, never followed/copied.
Serialized metadata bytes count even for objects with no copied contents.

The original scanner helper task is `btq-yg7zf`; its existing primary human design
approval is `btq-5jwip`, approving the ADR above. The YG first implementation HIGH
review finding 6 identified that statvfs is not reservation and metadata accounting
was incomplete. The evolving candidate now includes a metadata reserve and total
byte checks, but still needs generated-entry accounting, prepublication planning,
standalone archive-path coverage and genuine runtime amendment admission. These
are observations about mutable source, not pinned implementation qualifications.
This draft disposes only that reservation mismatch, not other implementation
findings or source-closure blockers.

## 2. Runtime admission owns approval enforcement

`environment.py::archive_execution_admission`, currently an unconditional HOLD,
remains the source-owned runtime guard. Implement its replacement only after real
human acceptance: verify the existing primary approval AND the separate amendment
approval through existing source-owned native admission/approval contracts. Require
exact task target, current applicable decision, full approved/context digest,
immutable amendment Git revision and document bytes, exact reviewed helper closure
and the genuine native blocking dependency. Retain primary pins unchanged; do not
rewrite the old ask. Missing native capability or any mismatch remains HOLD; a
caller boolean, metadata file or dependency edge alone cannot replace verification.

The guard returns a bounded receipt of the exact verified bindings for the run;
`runner.py::main` must invoke it before every child, binding the child argv/source,
run identity and destination/scratch admission receipt. Reuse existing freshness,
reservation and task checks, not a new authority protocol. Execution and release
records retain that receipt and explicitly name the accepted logical-budget
deviation. Approval authorizes this behavior only, never a new scanner stage,
retry, deployment or self-unhold. No authentic amendment approval exists by virtue
of this document.

## 3. Destination and space admission before launch

Precreate the private empty archive destination through an owned no-follow parent
before the child, mode0700. Open and retain directory descriptors; record UID,
mode, st_dev, inode, mount ID, filesystem type/source and statvfs f_fsid where
available. Validate parent/destination identities and emptiness before launch,
before copying and at final publication; refuse replacement, symlinks, overlap
with source roots or unexpected contents. Empty precreation is not archive
publication. Implementation must adjust existing mkdir-at-copy assumptions.

Record the same filesystem information for scratch and the run-directory owner
of disposable-source, child/collection/verifier logs and receipts. Do not overlook
these writes merely because they are outside the copied roots. Assign separate
preflight allowances to archive, scratch and run-only writes: each B bytes and E
entries; run-only excludes scratch/archive to avoid counting their allowance twice.
These scratch/run allowances are **headroom estimates, not quotas**. Child and
unrelated writers can exceed them or exhaust space. Even bounded archive writes
can fail after admission; this proposal guarantees no disk capacity.

For each pool use Q=max(4096, reported positive f_frsize, reported positive
f_bsize). For each allowance require B+2*E*Q available bytes (one block-rounding
and one directory/metadata-block allowance per entry), and E+1 available inodes.
This is a conservative estimate under the stated allocation model, not a bound on
all filesystem overhead/compression/COW/quota behavior. Reject unavailable,
nonpositive or unusable block statistics; reject inode statistics with f_files<=0
or unknown/unusable f_favail, including filesystems reporting no inode capacity.
There is no silent btrfs/inode exemption.

Group known identical backing pools and sum their allowances. Different st_dev,
mount IDs or f_fsid alone do not prove separate free-space pools. Distinct-pool
admission requires recorded source-backed proof of independent backing capacity
using the existing mount evidence; overlay, subvolume, bind, tmpfs or unresolved
relations are conservatively grouped as shared. For an ambiguous/shared group,
every member's observed available bytes/inodes must meet the group's summed
threshold (equivalently use its minimum observation). If that policy cannot be
applied with reliable statistics, HOLD. Never add separate free-space observations
together. On a single pool all three allowances sum; no space is counted twice.
Record observations, grouping rationale, formula and thresholds before child.

## 4. Finite metadata plan and exact pre-copy check

After genuine child-group quiescence and existing watchdog checks, enumerate the
owned source roots without following links. Bound traversal to E entries and B
serialized/payload bytes; reject growth or identity changes. Compute content hashes
and final source rows before publication. Build bounded per-root inventory bytes
in memory and a combined ledger in memory. Source bytes remain independently
identity/hash-checked during copy; no producer callback runs under these checks.

Use canonical compact UTF-8 JSON, sorted keys, ensure_ascii=true, no newline for
these generated archive inventories. The combined ledger includes its own byte
length/total but not its own digest. Per-root inventories do not hash the combined
ledger; the combined ledger may hash per-root inventories. Its digest belongs in
the existing external run receipt, avoiding a circular self-hash. Count that
receipt's archived bytes if it is included among source artifacts; do not modify
an archived source receipt after hashing. Final external run result is outside the
archive and is covered by the run-only allowance, not secretly double-counted.

Fixpoint: start combined-metadata length at zero; serialize with total_bytes equal
to payload plus per-root metadata plus proposed combined length, then replace the
proposal with the actual serialized length. Require monotone nondecreasing lengths,
fixed counted entry list, and exact byte-for-byte stabilization of the serialization
and declared totals within at most64 iterations. Cap every intermediate buffer at
remaining B; refuse decrease, overflow, nonconvergence or any entry-budget excess.
This is a bounded algorithm with HOLD on nonconvergence, not a claim every possible
input converges. Generated directories, per-root inventories and combined ledger
are already in the entry list. Preflight estimates do not debit actual counters.

No per-root inventory, payload or combined ledger is written until the whole plan
has stabilized and passed both caps. Immediately before copy, require fresh space
for **measured planned total_bytes + 2*planned total_entries*Q**, and at least
planned total_entries+1 available inodes on the unchanged destination pool. Do not
require another full B once the actual plan is known. Revalidate source-root and
destination identity. Failed pre-copy space check makes HOLD and performs no
archive copy; preserve originals and bounded existing run diagnostics. It does not
initiate an opportunistic partial copy or another child.

Once copying starts, each actual byte/create consumes its planned allowance once;
preflight/fixpoint estimates are never charged a second time. Reject unexpected
bytes/entries; verify copied hashes and source stability, then write only the exact
planned metadata. Publish completion only after all writes/fsyncs and directory
fsync succeed and actual totals match the stable plan. Standalone
`archive_scratch` must use this same planner/accounting contract, or refuse; the
fallback cannot silently retain the old payload-only accounting.

## 5. Failure and partial-evidence semantics

No physical reservation is claimed. ENOSPC, quota/inode exhaustion, short write,
identity drift, cap exhaustion or fsync failure makes archive incomplete and stage
HOLD. Retain available source originals and partial destination files; never label
partial inventories or a completion receipt successful, truncate evidence to claim
fit, clean up originals to retry, or stop a live watchdog to enable copying.
Only a fully validated completion receipt establishes archive completion. If a
failure also prevents recording its receipt, absence remains HOLD; externally
retained runner output and available files are all that can honestly be reported.
Original cleanup/quiescence/ownership rules still apply; this amendment supplies
no recovery permission or guaranteed preservation under total storage failure.

## 6. Owners, acceptance and alternatives

Affected existing owners are `scripts/scanner_recovery/environment.py` functions
`filesystem_preflight`, `archive_execution_admission`, `archive_preflight`,
`archive_scratch`, `archive_roots`, and `scripts/scanner_recovery/runner.py`
functions `main` and `finish_scratch`. Bind the eventual reviewed implementation
by actual revision and whole-source hashes at execution; mutable helper line
numbers and historical private candidate hashes are not current execution pins.
Public parent ADR/review content supplies this proposal's rationale directly.

Future offline acceptance must cover both genuine approval bindings/mismatch;
precreated destination replacement; shared/ambiguous/distinct pool accounting;
unsupported inode statistics; run-only headroom caveat; exact byte/entry limits;
metadata digit-boundary fixedpoints/nonconvergence; no per-root early publication;
standalone fallback accounting; failed measured pre-copy recheck with no copy;
streaming change and ENOSPC/short-write/fsync partial evidence. Synthetic fixtures
are never scanner outcomes or native approval evidence.

Alternatives: retain the physical-reservation rule and HOLD pending a separately
approved storage-container design; accept this explicitly limited logical PoC;
or defer execution with the mismatch unresolved. Placeholder files alone prove
no capacity reserved for the eventual archive. This proposal chooses the logical
PoC, subject to real design-owner acceptance, without a container redesign.

All socket/path limits, finite negative registry, relative-bind/cwd rules, tmux
ownership/watchdog rules, original source pins/probes/oracles, strict fields,
watcher/admission requirements and genuine native approvals remain unchanged.
Parent f05a/e22 and exact FRESH/stage sequence remain unchanged. No extra stage,
retry, release or activation is authorized. Source-closure blockers and every
other unresolved implementation finding remain independently gated.
