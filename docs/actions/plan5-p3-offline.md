# Plan5 P3 offline filing owner

This disposable, explicit-fixture implementation follows the finite-stage design
`092c10221660605598bb41151b496bda0f0bfa38`, especially §§3.3, 3.5–3.6 and 5.
It composes the existing P0/P1/P2 reducers. It provides no real queue, native
client, model service, authentication, CLI, default ports, installer or activation.
All five live adapters remain unavailable. Fixture observations and clocks are
trusted host inputs, not independent evidence of authority.

`FilingJournal.create` requires explicit fixture-only ports, clock and nonce
providers. It creates a private SQLite journal exclusively; `open` refuses an
unexpected schema. Each mutation command records its immutable request, source,
policy, origin, nonce and clock before the provider/port call. A fresh independent
`Current` observation is required before issuance. Issued/uncertain commands are
never automatically replayed. Read-only reconciliation can adopt exact positive
readbacks; retrospective "definitely unissued" readbacks cannot authorize retry.
Only the immediate adapter's typed pre-effect refusal has that meaning. A prepared
intent can be locally CAS-abandoned without any port call; an uncertain intent
cannot. A proved unissued allocation becomes terminal cancelled while retaining
its issued-history bit, never enabling another create. Exact-schema reopen
normalizes interrupted issuing commands to uncertain.

Allocation lookups consume the durable P1 lookup count before each read and are
spaced 1/5/30 seconds from the actual issued call, then preceding owned lookup.
P1 owns separate allowance-consumption and readback-adoption reducers; the
legacy combined reducer retains its original version and validation semantics. Read failure, zero or
multiple matches do not prove absence and never recreate a shell. Unknown work
retains its capacity reservation. Capacity is two active requests per bead across
generations, 64 globally, 4096 retained requests and 16384 events. Each request
reserves 161 lifecycle slots before allocation; retained history is not evicted.
Transactional per-request counters avoid per-event history scans. Unknown or
unavailable readbacks update bounded command diagnostics without spending the
terminal reserve. Ordinary lifecycle traffic stops at 124; cleanup has four
reserved slots within the 128-slot lifecycle bound.
Detail traffic has 32 exact bounded records plus one saturated aggregate, with
four hashed samples. Overflow is bounded diagnostic information, not unlimited
per-event deduplication or authority.

Blocker/park/landing observations include the exact bead, approval dependency,
stopped logical session, retained claim, repository/source/tree. Filing also
requires the exact whole ask, digest and revision. Withdrawal uses the actual
P1 `settle_request` result and typed Q1-style cleanup observations, including the
returned terminal Request. Unsupported nonexecution cleanup remains held; a
synthetic terminal projection cannot release capacity.

P2 receives the exact precommitted command nonce through a source-compatible
optional `assess(..., nonce=...)` argument. Legacy callers retain their existing
default. The provider's complete Invocation is persisted before its single call.
The result persists complete Reply, issued-command hash, original start and end
Clock together before P2 can project it. SQL CAS and affected-row checks reject
command/source/policy changes during the await. The resulting Receipt is then
persisted before sufficiency adoption. A P2 preflight refusal with no provider
call retains its exact Receipt and a refused-unissued intent; it can still yield
the original P2 grooming projection without becoming a pending provider effect. A crash between result and Receipt leaves
an explicit HOLD; it does not resample an end time, reroll a nonce or call again.
Projection validates the retained input/result/receipt and current source/boot/
60-second deadline. There is no permission Allow path.

Grooming uses actual P2 grooming advice and P1 transitions, at most twice for the
same logical origin. A changed source, new ask, fresh park/readback and new
judgement are required. A crash between prepared grooming and its authorization
record leaves a HOLD rather than an automatically issued command.

## Reciprocal composition prerequisite

The accepted predecessor pair is C1 product runtime
`562e685956fe375b7052aef01a2fbbbe33858d59` and Q1 queue runtime
`eaa8fa0d065a108600d4da6c0df987d40bdb3bd7`. Its source-owned capsule pins exact
commit/tree/module bytes. This candidate changes product bytes and therefore
cannot claim acceptance under that predecessor capsule. The root controller must
perform the separately authorized Q1 companion pin refresh and test the exact
new signed reciprocal pair. Existing pins have not been weakened or edited.
The current positive tests exercise genuine local reducers with disposable
fixture ports; they do not substitute for that outstanding pair acceptance.

Local journal files are a trusted same-UID boundary. Raw SQLite changes used in
adversarial tests model stale/concurrent data and are not a supported operator
recovery interface. The first-review predecessor journal schema is deliberately incompatible with
the counter/index schema: opening it holds without mutation, reset or automatic
migration. Legacy/partial journals, missing receipts, unresolved effects
and unavailable capabilities stay held. No deployment or production acceptance
is claimed. Quality findings remain unwaived in the external task evidence. The full
configured typing check uses the explicit Python3.12 environment and all required
test import roots; earlier missing-import diagnostics are retained as an
environment failure, not an accepted baseline.
