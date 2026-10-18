# Plan7A pure contracts

This component implements the approved Plan7A D4 contract as inactive records, bounded
validation, public projections and advisory dispositions. It installs no gatekeeper,
routing, native task writer, model provider, permission authority or transport consumer.
The genuine approval and exact design archive are pinned in the adjacent inputs manifest.

`gatekeeper.contracts` defines frozen, closed msgspec records. `validate.prepare` separates
origin-profile gaps from host failures; `decode_request` and `decode_result` accept bounded
strict JSON bytes. The model-facing result uses public aliases; normalization uses only
the frozen request maps. `validate.egress` projects public labels and omits private host
provenance. Hashes use sorted, compact UTF-8 JSON without Unicode normalization.

Call `prepare` and `decode_result` before passing their typed results to the exact two-argument
`intake_disposition`, `sufficiency_disposition` or `permission_disposition` function. These
functions consume validated host tokens; constructing a `Ready` or `Valid` record manually
is not validation. Returned proposals are inert. Permission floors precede any future model
port. The `ModelPort` protocol has no implementation and performs no model invocation.

Sufficiency enrichment requires an exact safe complete Git blob at most 4 KiB; it never
fetches, truncates or summarizes a larger document. Unicode quote offsets and source/ref
identities are checked before an atomic append. The proposed whole candidate must later
undergo actual native reassessment. Native lint and digest values remain future trusted
adapter inputs, not approval evidence authenticated by this module.

The explicit redaction entry point shares the existing redaction kernel with default admind
callers. It compiles the supplied immutable directory context without mutating global login
configuration. Default caller and interleaved explicit-context behavior are compared with
the exact approved source revision in offline regression fixtures.

## Offline acceptance mapping

| Approved category | Named coverage |
|---|---|
| Closed grammar, canonical hashes, native shape/ID compatibility | `test_gatekeeper_contracts.py` |
| Host/origin ownership, public paths, aliases and private egress | `test_gatekeeper_contracts.py`, `test_gatekeeper_boundaries.py` |
| All request/result bounds and one-over refusal | `test_gatekeeper_boundaries.py` |
| Intake material flags, dependency aliases and failure provenance | `test_gatekeeper_projection.py` |
| Sufficiency lint precheck, all outcomes, Unicode proof and atomic overflow | `test_gatekeeper_projection.py`, `test_gatekeeper_boundaries.py` |
| Groom counters, duplicate/outage and missing-origin consumer contract | `test_gatekeeper_projection.py` |
| Permission budget, advisory outcomes and deterministic floors | `test_gatekeeper_projection.py`, `test_gatekeeper_boundaries.py` |
| Legacy redaction parity, explicit context isolation, unstable backstop | `test_gatekeeper_redaction.py`, existing `test_admind_r13_redact.py` |
| Existing card/readback/config behavior | existing `test_admind_approvals.py`, `test_admind_asks.py`, `test_admind_cli.py`, `test_config.py` |

Recording consumer fixtures demonstrate the intended pure boundary; they do not prove
persistent routing or real endpoint binding. There is no clock, counter storage, origin
resume/nudge, subprocess, native write, send or effectful executor in this component.

The intake fallback departures require a separate genuine ADR amendment before enforcement.
Pure design approval does not amend the governing ADR or authorize deployment. Later
integration must establish authentic origin/policy/native readbacks, provider/timeout behavior,
existing-session routing, hard floors and deployment authority. The 4 KiB evidence limit and
ordinary advisory-model semantic risk remain explicit approved limitations.

## First HIGH code-review repairs

The actual first HIGH review returned REVISE with ten findings. All ten are addressed
in this candidate; a second code review remains pending. The public/wire `Ready` grammar
is unchanged. `prepare` retains its immutable privacy context in a private in-memory token
without adding msgspec fields. Every disposition rechecks the complete request and normalized
outcome under that bound context; plain, reconstructed or replaced tokens fail closed.
This token is not authority or a persistence format. Explicit four-argument internal calls
also rerun those checks. No registry, ambient login-directory lookup or global mutation exists.

Enrichment budgets the complete mandatory Used reassessment, including retained base text
and quotes. Host reconstruction evidence is checked after source-context validation and
has host-owned diagnostics. NativeValue traversal charges every occurrence before descent,
so shared Python containers cannot expand past the global budget before refusal.

The inputs manifest now pins both actual redaction source blobs at the existing signed
product revision containing the parity-tested extraction, retaining the original legacy
pins separately. A pure fingerprint checks the loaded explicit-directory kernel, patterns
and constants on admitted CPython 3.12; drift or another unadmitted interpreter fails closed.
It reads no source files or environment and is compatibility evidence, not authentication
against code replacement by a trusted host. A later redaction/interpreter update must admit
its exact source and kernel identity together with parity fixtures before use.

The review's optional regex-compilation and generated-field-set suggestions are deferred:
there is no measured performance regression requiring a new caching lifecycle, and closed
schema tests cover the explicit public diagnostic field allowlist. No review pass is claimed.

## Second HIGH code-review repairs

The actual second FULL HIGH review returned REVISE with ten findings. The adjacent
ordered review dispositions record eight addressed findings and two noncritical deferrals;
no code-review pass is claimed. Conversion now has an allocation prewalk before expanding
shared Python graphs. Normalized results have the same 32 KiB aggregate ceiling as wire
results. Typed malformed fields and unhashable host codes refuse without escaping the
projection, and egress is detached from caller-owned mutable input. Host invocation envelope
faults stay host-owned; digit-bearing public diagnostic paths retain their full safe ancestor.

The exact redactor binding actually used must match the fingerprinted kernel. Runtime
admission is specifically **unoptimized CPython 3.12**; optimized execution, including `-OO`
which removes fingerprinted docstrings, fails closed. The expected identity is unchanged.
There is no ambient runtime detection fallback or new authority claim.

Known noncritical limits remain: a future enrichment reassessment allocating a longer fresh
request ID/revision can exceed the aggregate limit after a candidate proposal; it still
refuses safely without sending oversized data. Semantic host grammar failures can be labeled
`invariant` rather than `schema` at the same safe public field path. Neither limitation is
claimed fixed or waived by this author. The earlier first-HIGH section records its candidate
state; the actual second-HIGH findings and current limitations supersede that earlier pending
review wording.
