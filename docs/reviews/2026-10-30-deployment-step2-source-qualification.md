# Build/deployment Step 2 protected boundary source qualification

Approved scope is `gsc5`, H commit
`d8d97ebfe0927de32acd462e6b0000d969751b30`,
`docs/architecture/2026-10-26-build-and-deployment-queues.md`.
This source work starts at Product `eef8c8fee6bffdfebe92d71137a7f1ffe9bd4c2e`.
No account, service, socket, approval, queue or host deployment was changed.
Public `deploy{target}` and the reserved `wsd-act run <approval-id>` remain unchanged;
the host opener continues to refuse operation until actual qualification.

## Implemented source boundaries

`deployment_host_store.py` checks every ancestor by an anchored no-follow
directory descriptor, requires an executor-owned 0700 final store and distinct
positive build/executor UIDs, stages bounded original regular-file bytes into
0400 digest-named artifacts and rehashes retained publication. Failed copies
retain private partial files. Retained final symlinks, altered bytes, hardlinks
and unsafe modes refuse. Actual `flock` provides a per-target lifetime lock.
Build ingress checks real Linux `SO_PEERCRED` before reading bounded closed
submission bytes. It grants no human consent. The private installer argv only names the fixed root helper and complete packet
digest; no shell, environment, path, flags or arbitrary command is delegated.
The future root-owned helper/profile must independently bind the exact packet,
source allowlist, executor UID and one-use admission, and preserve the existing
single root lifetime (private bootstrap copy, hash check, then same-copy execution).
That helper is not implemented or installed by this foundation. The argv builder
does not invoke sudo or install a sudoers rule. Ingress uses a monotonic total
deadline; partial-byte progress cannot renew it.

`deployment_outage.py` defines a separate closed outer deployment packet. It
binds the inner Release, exact four remaining outage steps and connected
before/after hashes, finite forward/recovery windows, independent route identity,
epoch, policy and credential hashes, and fixed source-role pins. Independent
recovery has a separate closed cancel/safe-stop/held-restore value, one exact
packet/execution/route epoch, nonce, expected observation and finite window.
Held restore is refused unless the original recovery scope permits it. No
database restore, generic command, argv, alternate reader or selected route is
accepted. Values and hashes alone are not authentication or readiness evidence.

`deployment_outage_authority.py` is a dormant, fixed native corroboration adapter.
Its exact original body includes the complete outer packet digest, immutable
pushed packet URL, original designs/deviations and outage/recovery details. It
uses the unchanged checked native core, original system-owned card, actual sender
key/current allowed membership, yes-only native answers and exact CLI readback.
Both source-role and policy sweeps remain required. An inner-only approval or a
changed outer packet refuses. The existing fixed original reader is not the new
protected MDK receiver and establishes no protected host custody. The packet's
semantic source role currently pins this exact adapter, not an unimplemented
protected receiver factory. The distinct OUTAGE title and opening explicitly disclose and accept that a
queued but undelivered Marmot reply is not a received revocation. Digest lines
use the existing computed public digest grammar, preserving all 256 bits through
the real redactor; the group line hashes the exact group bytes rather than
exposing an identifier. Aggregate title/body budgets use actual
`fit(redact(context))` with the admind 3500-character/40-line bounds and refuse
any context changed by redaction rather than approving altered text. Native
single-card readback is still mandatory; complete transport sizing remains an
actual endpoint qualification requirement.

`deployment_outage_journal.py` is a private real-filesystem ledger with SQLite
FULL synchronization, fixed filename under the already anchored protected store,
one-use approval/execution/nonce, unique active target and committed issued intent
before a fixed step is returned. It executes no effects. A restart cannot issue
the same unknown step again. Exact retained completion remains possible after
a recovery request, while target exclusion and the forward hold remain. Recovery
commands consume a unique nonce and retain their exact payload. Cancelling an
unknown operation never marks a target reusable. These methods consume trusted
factory inputs; they do not authenticate caller-created commands or observations.
A typed `failed-no-effect` receipt can release the active target only when this
journal proves in one transaction that it is still ordinal zero/ready and has
never issued any effect. Its one-use execution/approval/nonce history is retained.
Any issued, completed or recovery-held job refuses this path. A post-issued
installer refusal cannot be inferred from a caller exit code: it remains uncertain
until a separately qualified effect/no-effect or cessation producer supplies proof.
The operational producer of cessation/newest-state observations and its bounded
target-release reconciliation are deliberately not impersonated by fixture data.

## Evidence and limits

The original source at `a05fff8` had 76 focused tests. Its actual first HIGH
built-in review requested changes; the retained report is
`2026-10-30-deployment-step2-code/r1/actual-review.md`. This repair addresses
findings 1–7 without claiming clearance or adding a review of its own. Findings
8–9 remain visible bounded duplication cleanup.

Focused repair tests: 84 passed, with bytecode-disable environment unset.
They include actual anchored filesystem staging, target lock exclusion, retained
tamper refusal, wrong UID refusal before any socket read, durable journal restart,
uncertain-step non-replay, exact completion, post-cancel acknowledgement without
forward execution, target retention, nonce one-use, actual SQLite busy refusal
the combined real admind renderer budget, total stream deadline despite
continuing progress, complete digest visibility and different outer-packet cards
through the actual renderer, explicit transport-gap text, login redaction refusal,
legacy index migration, typed never-issued completion and issued-target retention. Native DB/CLI rows in tests are
synthetic and confer no genuine approval. The checked original core is unchanged.

An actual disposable Linux user and mount namespace test runs an executor as
UID 1 and a build client as UID 2, checks kernel peer credentials and proves the
build UID cannot write the executor store. Its private mount namespace uses a
fresh tmpfs fixture; no host mount, account or service is changed. This qualifies
the source primitive on this kernel only. It is not the real `hermes-act` account,
protected production install or OpenShell sandbox qualification. Unsupported
namespace environments skip that case rather than claiming success.

Focused Ruff, strict source Pyright with an explicit existing project interpreter,
the install-agnostic checker on every changed/new file and `git diff --check`
pass. There is no Step 2 `.venv`; Pyright's explicit interpreter resolves msgspec
and reports zero errors despite noting that the configured local venv is absent.
`ripwire --quality-delta` against the frozen Step 2 source exits 2: ten rows,
three major findings (a small test-pattern clone, approval-body repair churn and
journal size). Seven new-symbol rows identify pytest/thread entry points as
unused. No metric was acknowledged away. The earlier foundation's capture
clones remain visible in the actual R1 findings; this repair does not introduce
an arbitrary policy/reader callback to remove them.

Required next integration is an owning protected receiver/semantic factory that
correlates retained genuine MLS evidence with its own exact ask/card/packet and
qualifies the independent route before stopping admind. The ledger's typed
recovery inputs must come from that independent authenticated route; target
release additionally requires source-specific positive cessation and newest-state
reconciliation. The finite deterministic host runner, actual target mapping,
protected interpreter/stdlib/imported dependency closure and narrowly configured
root helper are not installed. No public execution path is added here.

## Proposed deployment write set (not authorization)

The future exact deployment packet must resolve and pin these fixed roles before
asking for deployment approval; this source adds no ambient path overrides:

- A distinct `hermes-act` account/group and protected executable/profile roots,
  with build UID denied writes to code, policy, state, credentials and ancestor
  directories. A narrowly configured root helper may run the fixed pinned
  installer recipe; delegating generic env/bash to the build UID is prohibited.
- An executor-owned 0700 artifact/state directory containing digest-named 0400
  staged roles, retained partials, target lock files, `outage-journal.db` and its
  SQLite sidecars. The selected exact path/owners/modes belong to the protected
  installation profile and must appear in the deployment packet.
- Protected build-ingress and independent receiver socket/service configuration,
  exact peer UIDs, genuine receiver MLS state/operator policy and evidence
  journal. The independent route must remain available during the admind outage.
- Existing platform release, current symlink, manifest/install record and user
  units touched by the pinned installer. Existing root store-helper code, config
  and socket units must preflight as already correct; no drift repair is implied.
- Online guard output followed by a new, absent stopped-guard output directory;
  the newest stopped snapshot and live DB remain retained. The recipe may stop
  managed WSD with `--no-wsd`; it does not enable pickup or restore an older DB.

Actual protected paths, full hashes/owners, source-specific client exclusion,
unit cessation facts and exact rollback envelope must be materialized from the
current host before that deployment approval. Historical 717/source approvals,
caller booleans or the namespace fixture cannot replace them.
