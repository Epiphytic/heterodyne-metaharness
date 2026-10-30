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

20 focused tests passed in 0.29 seconds before final documentation-only changes.
They cover the recipe, durable reopen, interrupted effects and reconciliation,
target exclusion through two connections, one-use identities, cancellation,
expiry, mapping/state drift, duplicate/unknown fields and finite schema bounds.
Synthetic native SQL is read through the actual core reader; synthetic CLI,
policy and clock fixtures exercise exact native authentication and two-sweep
refusals. No test result establishes genuine operational approval or host trust.
Focused Ruff and strict Pyright checks passed. Pyright used the canonical Product
interpreter because this clean worktree intentionally has no `.venv`.

`ripwire --quality-delta` reported exit 2: dormant `_bindings`/`_capture` were
classified as preexisting dead functions by its canonical-name matching; new
fixture classes/tests were also marked dead, and the fixture journal/advance
were reported for size/parameter count. These findings are retained, not a claim
of a clean quality gate. The dormant entry points intentionally have no host
caller. Focused tests exercise the actual native validator and capture path.

Host owner/UID separation, protected mapping/journal/artifact custody, immutable
artifact staging and real process/manager checks remain Step 2 qualification.
The fixed H admind endpoint must be verified during that qualification; a missing
or differently pinned executable causes refusal. Outage execution is explicitly
`refuse`; no transport-gap capsule, independent host cancellation/recovery route
or installer is installed by this slice. The production opener always raises
HOLD. Existing generic action decision code is unchanged and cannot activate
this private fixture or make its receipts into host authority.

## Packaging exception

The `install-agnostic` pre-commit hook rejects the fixed H home paths in the
native adapter and original core. The controller explicitly authorized skipping
only `install-agnostic` for this dormant source slice; Ruff and secret detection
remain enabled. The original core is unchanged. These are fixed deployment
profile endpoints, not portable defaults: relocation requires a reviewed new
install profile and source pins, never an ambient path override. No host trust or
activation is implied by this narrow packaging exception.
