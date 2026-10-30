# Build/deployment split Step 1 source qualification

Approved design: `gsc5`, H design at commit
`d8d97ebfe0927de32acd462e6b0000d969751b30`,
`docs/architecture/2026-10-26-build-and-deployment-queues.md`.
Product base: `ee87166c3fe1447bc19685a0e0cb15d18768c2b8`.

This slice preserves `deploy{target}` and reserves `wsd-act run <approval-id>`;
it adds no CLI, host executor, queue enrollment or production opener. The new
release envelope is a closed internal schema, not a compatibility claim for a
live transport. Target mappings are explicit projections in the private fixture;
passing such a value does not establish protected configuration custody.

`deployment_authority.py` reuses the original H native authority core unchanged,
loaded from a fixed package resource only after checking SHA256
`ad4d4a96632646a5d145feb30c30d402b358d3ecb9caf4717e89394877b5f48d`.
The `.py.txt` suffix keeps this immutable copied resource out of normal source
lint/type discovery; its bytes are the reviewed shared implementation, not a
rewritten authenticator. Fixed reader endpoints are the existing H native DB,
host/queue policies and release-qualified admind CLI. The release binds exact
adapter, contract, codec, record, core, CLI and interpreter source-role digests.
Native ask context must exactly bind the release digest, execution/nonce, target,
immutable pushed packet URL, designs and deviations. Native authenticated sender,
original system-owned card, current operator membership, all yes-only answers and
exact CLI readback remain mandatory. Source/policy sweeps surround corroboration;
the wall clock is read internally before and after, not accepted from the caller.
No boolean observation or retained receipt substitutes for native evidence.

The only effect adapter is `FakeEffects`, which hashes transitions in memory and
records its calls. The private fixture journal uses SQLite FULL synchronization,
one-use approval/nonce/execution identities and active-target exclusion. An issued
step is committed before the fake effect. Restarts never retry issued steps;
only exact observed post-state reconciliation advances them. Cancellation, expiry,
changed mapping/state or payload drift prevent forward execution. A cancelled or
uncertain target stays held rather than becoming reusable after a timeout.

## Evidence and limits

The initial source commit `9dc38e3af8ce62e8b1009e6cf3aa2396ff398b0c`
reported 20 focused passes using a canonical Python 3.12 interpreter. The actual
round 1 HIGH built-in review found 19 passes and one failure in this worktree's
Python 3.14 environment: its approximately 32 MiB interpreter exceeded the old
16 MiB read bound. That original report and commit remain historical evidence;
they did not qualify the actual worktree environment.

After the round 1 repairs, 31 focused tests passed in 0.68 seconds using this
worktree's `.venv/bin/python` (uv Python 3.14.7, 32,159,664-byte executable). Source/interpreter hashing now
streams through one checked descriptor with a 128 MiB per-file bound, a 512 MiB
inventory bound and a finite file count. Focused Ruff and strict Pyright checks
passed. The all-tracked install-agnostic checker also passed without a hook skip. A broader
repository run was interrupted after 55.84 seconds with 115 failures, 699 passes
and 87 skips; action/gatekeeper fixtures returned `InvalidHost('/privacy_sha256')`.
The same first failing case,
`test_actions_c1.py::test_permission_terminal_and_blocker_never_project_fresh_denial[completed]`,
also failed against immutable initial commit `9dc38e3` in a separate baseline
worktree under the identical interpreter (0.47 seconds). This establishes that
case predates these repairs; it does not establish that every broad-suite failure
has the same cause. A clean full-repository result is not claimed.

New causal cases cover exact expected packet commit/path, malformed group types,
public deploy alias bounds, unknown cancellation and normalized target conflicts.
Native SQL and CLI observations remain synthetic. The fixture also executes an
actual isolated child interpreter against disposable source files, checks source
module mutation without a wrapper change, refuses bytecode, hashes the actual
worktree interpreter, checks policy parsing of captured bytes, and verifies the
checker exception is limited to the exact unchanged resource and still enforces
the local denylist. These tests establish no genuine operational approval.

The complete CLI executable closure is now explicit: fixed release source tree,
fixed site-packages dependency tree, resolved CLI interpreter, the console wrapper,
current harness package tree and checked adapter/core files. Every tree is pinned
in the release and swept before and after readback; existing bytecode or symlinks
refuse. The fixed child uses `-I -S -B`, explicit source/package roots and a fresh
private cache prefix, avoiding `.pth`, site startup, ambient Python paths and
pre-existing stdlib cache reads. Platform stdlib custody remains part of host
qualification. Ordinary editable/cached installations may correctly HOLD until a
sealed runtime profile is separately qualified; this repair changes no host files.
The core is loaded lazily: absent or unsafe source causes a controlled authority
HOLD when used, rather than breaking unrelated imports. Root-owned and hardlinked
regular resources are allowed only with the same checked descriptor/hash checks.

The release binds the expected immutable packet commit and canonical docs path;
the native card must contain that exact URL, not another syntactically valid
GitHub URL. Policies are parsed from the exact first-sweep captured bytes through
the unchanged core parser, then their original source bytes are re-read at the
second sweep. The local native context wrapper normalizes malformed input to HOLD.

Host owner/UID separation, protected mapping/journal/artifact custody, immutable
artifact staging and real process/manager checks remain Step 2 qualification.
The fixed H admind endpoint must be verified during that qualification; a missing
or differently pinned executable causes refusal. Outage execution is explicitly
`refuse`; no transport-gap capsule, independent host cancellation/recovery route
or installer is installed by this slice. The production opener always raises
HOLD. Existing generic action decision code is unchanged and cannot activate
this private fixture or make its receipts into host authority.

## Packaging exception

The first source commit used the controller-authorized narrow skip of the
`install-agnostic` pre-commit hook; Ruff and secret detection remained enabled.
Round 1 correctly found that this left all-tracked CI failing. The repaired
adapter uses documented per-line `home-path` exceptions. The checker has one
resource exception for **only** `home-path` at the exact resource path and exact
unchanged SHA256 above; another byte, path or rule is not exempt, and local
secret-denylist checking remains active. No original core byte or digest was
changed to make the checker pass. Relocation still requires a reviewed fixed
install profile and new pins, never an ambient endpoint override.

## Quality trade-offs after round 1 repairs

The deterministic quality delta reported new test entry points as dead code and
one 49-token normalized clone between the short lazy-loader refusal test and an
unrelated graph-bounds refusal test. These are distinct behavioral tests, not
production duplication; merging their fixtures would couple unrelated contracts.
The small local `_require` and digest regex remain local deployment validation;
the shared public codec identity/alias patterns are reused. The source authority
reader remains deliberately private and dormant. These are recorded trade-offs,
not a claim that every quality metric is clean.
