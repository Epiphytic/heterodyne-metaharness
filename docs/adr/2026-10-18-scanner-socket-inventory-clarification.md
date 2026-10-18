# Scanner socket inventory: original negative cases and ownership witnesses

Proposed amendment only; design task btq-zckld. No review, human approval,
implementation or execution authority is claimed. The controller will obtain
one actual Claude Opus HIGH design review, incorporate once, publish and request
separate authentic human approval through Marmot. No second design validation.
[Inputs and finite registry](../reviews/scanner-boundary/socket-inventory-clarification-inputs.json)
pin the exact parents and source facts; historical status labels are not authority.

## 1. Decision and unchanged gates

Amend only the interpretation and representation of source-backed socket/tmux
inventory in f05aeb43d8b871dec2d83c5d0df8efa933b53b68,
`docs/adr/2026-10-16-scanner-private-environment-recovery.md`.
Retain its parent e22f4ed366d6ddcdc1a5d7f0c9fe30d088b12ea8,
`docs/adr/0002-scanner-execution-fd-prerequisite-d4.md`, unchanged.
Successful pathname endpoints still require encoded argument length<=100.
The only exception is the exact original caught-negative client call in §2.
An unknown, changed or newly discovered over-budget call remains HOLD, never an
implicit extension of this registry. No original test/assertion/expected JSON,
scanner matching, production source or collection identity changes are authorized.

All existing authority, source/import/interpreter/lock checks, fresh roots and
empty exactly14-key environment remain. Original51 result fields plus all six
f05a supplements remain mandatory. Keep verify()/mask() function bytes and all19
negative probes pinned unchanged. Keep the exact diagnostic375 → fresh detached
H92 at e22f4ed → FRESH red88/create-red6 → B1-only3373
with exactly four expected assertions → overlap7 → archived B2 → all-green3373 →
create-green6 → signed clean freeze, final serial offline suite, implementation
review and hosted CI/secrets sequence. This amendment authorizes no extra stage,
retry, source transition or rerun of a completed gate. Failed attempts stay failed.
Loss of reservation, missing proof or unexpected outcome stops before another
child and requires the existing authenticated disposition; no self-unhold.
The existing authenticated disposition 7k1bf / 2a9d3b2de451 selected FRESH.
Run red88 (53 declared failures/35 passes) and create-red6 (4 declared failures/
2 controls) under the corrected environment in the prescribed disposable sources.
No reuse is permitted by this amendment; the historical rcje question is not
authority. Root must retain/read back the genuine FRESH decision and native
approval evidence before admission. The reported active-checkout H92 is not
the required detached-copy H92 evidence.

## 2. One finite original caught-negative client exception

Registry ID `original-missing-wn-agent-client` names only:
`tests/test_admind_cli.py::test_socket_errors_do_not_echo_the_path` in scanner
source c2d919ec6fa18088b0211a7d9898e4f8436bff2b, with exact file/function pins in
the appendix. It builds `tmp_path / NPUB / "missing.sock"`; NPUB comes from the
literal32-byte public test key via existing hex_to_npub. Encoding has4 HRP +1
separator +52 data +6 checksum ASCII characters:63 bytes. The sanitized case
prefix is30 bytes, first numbered suffix0 gives31. Thus basetemp<=18 + slash1 +
case31 + slash1 + NPUB63 + slash1 + missing.sock12 =127 bytes; the current fixed
mkdtemp spelling gives126. Both exceed100 and Linux's successful pathname budget.
This is a derived upper bound, not a measured endpoint or successful connection.

ControlClient.account_list calls `_open`, which directly attempts
asyncio.open_unix_connection and converts OSError into ControlError. The original
test requires ControlError and asserts neither public key nor filename leaks.
It creates no listener and cannot be reclassified as a successful endpoint.
Do not insert a truncation, redirect the call, bypass the test, mock its error,
pre-bind anything, or change which error its existing assertion accepts.

An inventory row may use `original-negative-client` only when registry ID,
complete node ID, source/function hashes, constructor/call-chain references,
path-component derivation and unchanged caught-error assertion all match.
Record the actual reconstructed encoded bytes separately from successful-path
measurements, maximum127, with `expected_operation: connect-error`, never
`bound` or `connected`. The helper validates this row by reconstruction only;
it must not perform a synthetic connection to the negative path. Only the
unchanged test exercises it in its already required genuine batch.
The synthetic exactly100 bind/101 rejection probes from f05a remain unchanged.
Unexpected success or a different actual test outcome remains an unexpected
failure under the strict existing oracle; this exception waives no assertion.
No general negative-test label, filename pattern or caller boolean grants this
exception. Registry changes require a further explicit design disposition.

## 3. Relative AF_UNIX bind arguments and fixture-owned cwd

Two original full-offline sites use actual relative bind arguments:
`tests/deploy_host.py::bind_bus` changes into its fixture directory and binds
`"bus"` (3 bytes); `tests/test_harness_walk.py::test_a_fifo_a_socket_and_a_device_are_refused`
changes into `sock_tree / "cli"` and binds `"s"` (1 byte). Both restore the prior
cwd in finally. Record and verify the exact source pins, argument and derivation
of cwd from the current fresh fixture root. Kernel argument length is3/1, not
the length of a fabricated absolute spelling. Do not change cwd from the helper
or run a new endpoint probe to obtain this evidence.

A `relative-bind` witness records source/call reference, literal basename,
encoded argument length<=100, fixture-root locator, derived cwd components,
source guarantees for creation/ownership and finally restoration, plus a
resolved physical-location derivation for cleanup association. It requires a
single safe basename (no slash, dot/dotdot or NUL), directory provenance below
the current private fixture root, and no source mutation or concurrent process
cwd change invalidating the proof. A different dynamic cwd/basename remains HOLD.
The witness does not assert that an arbitrarily long absolute path would bind.
Only the two pinned sites are admitted by this clarification; an additional
relative bind requires an explicit source-backed controller disposition before
execution, and a design amendment if it changes this finite scope.

## 4. Independently owned private tmux fixture root

The existing tests/tmux_guard.py creates a separate `/tmp/hzt` +8-random-character
root (16 bytes), then `s/` +8 hex characters, making the actual -S argument27 bytes.
This source-owned root is not a child of the scanner helper root. Preserve its
existing private creation/permissions checks, launch.lock inheritance, guarded
-S invocation, watchdog and teardown. Source tmux._selector/_run/new_session
must establish the actual selector, not infer isolation from environment absence.
No default server, guessed -L name or unowned endpoint is permitted.

An `independent-fixture-root` witness binds exact creator/guard/cleanup source
hashes, creation branch, owned mode/UID requirements, argument formula and actual
root lstat identity when created during the genuine batch. Preflight records
the source proof without creating a substitute tmux root. Existing fixture
cleanup/watchdog evidence must correlate that same identity and its selector;
record missing cleanup/unknown outcome as HOLD, never delete another server or
claim cleanup merely because a process ended. Scratch preservation must retain
this root's regular-file/symlink metadata under the same f05a2GiB bounded archive
policy without following links or archiving sockets/devices/external targets.
Do not stop a live watchdog to make evidence appear complete. Additional root
creators in watchdog tests need their own proof; this row is no blanket waiver.

## 5. Finite source closure and evidence scope

Retain the broad candidate census as a discovery aid. Classify all candidates,
including exclusions, with pinned references; do not equate a token with an actual
socket or a local AST import closure with execution proof. Record selection and
exclusion reasons separately from constructor derivations. SQLite file connects,
unnamed socketpairs and monkeypatched receivers need their actual transport or
mock boundary proved; unknown receiver/dynamic import/exec/subprocess is HOLD.
Do not label reachable production constructors harmless just because a fake
class or CLI help branch exists elsewhere.

The scope receipt binds exact stage argv, active source snapshot including the
three dirty B1 file hashes, ordered selected modules/nodes, explicit plugins and
sources, installed package/interpreter provenance, fixtures/conftest imports,
resource/inline-script/child-process edges and external executable identities.
Use directed edges with origin reference, target bytes or external pin, and
source-backed branch/fixture/call rationale. Paths use machine-neutral root labels
in public manifests and actual private mappings in execution evidence. Exclusions
such as docs/spikes require proof no selected import/resource/exec edge reaches
them, not a directory-name rule. Source changes invalidate affected derivations;
reconcile the entire candidate set against its pinned source snapshot before a
child. Finite receipt counts and hashes must match exactly; missing rows HOLD.

Selected closure starts with the exact81 modules and actual conftest/plugins;
full-suite scope is the D4 command `pytest -q --ignore=tests/live` with its existing
explicit plugins, HZ_REQUIRE_TMUX=1 and timeout3600. Keep that ignore before
collection; skip marks alone do not prevent collection-time live imports. This
clarifies existing D4 scope, not a newly excluded test. Hosted CI remains its
required unfiltered suite with genuine existing live opt-in rules. Its execution
is not made private or certified by this local offline inventory.

The private R4 static research reports204 selected and272 top-level-full imported
files, not a complete accepted closure. Inline Python, extracted probes/resources,
child scripts, package dependencies, external executables and remaining real
fixture/tmux receivers still require source-backed resolution. This design does
not mark them complete or authorize diagnostic launch before they are complete.

Fresh explicit basetemp removes/recreates the base in the pinned pytest version.
The frozen3372 node IDs are ASCII and have maximum41 equal sanitized30-character
prefixes, so suffix0..40 bounds a single allocation per node to32 bytes. The exact
added H node has no prefix collision. The selected explicit mktemp prefixes
approved/outside are disjoint. These are conditional proofs: repeated/dynamic
matching creators, parametrization/fixture reuse and full-suite factory calls
must have finite source-backed allocation bounds too. No global3373 suffix rule,
reuse of a failed directory or inference from a previously passing collection.

## 6. Small helper delta and acceptance obligations

Propose only a versioned additive inventory schema at
scripts/scanner_recovery/environment.py::validate_inventory/measured_socket_path
and runner inventory capture/archival. A run-local explicit inventory observer
may read existing tmux_guard._guard.run after configure and tmux_guard.last after
unconfigure, using ordered pytest hook wrappers and the existing watchdog
return/error/timed_out fields. It records bounded no-follow identity/metadata
before/after existing cleanup, never changes the fixture or starts/stops a server.
Require an offline helper fixture proving hook ordering and durable publication
after unconfigure; absent final proof remains HOLD. Pin observer bytes and explicit
plugin argv before invocation; no inherited plugin loading. Preserve existing
successful-path and tmux checks. Add the three closed witness variants above and transport/reachability
classification with proof references, so lexical socket tokens in proved
non-endpoints do not demand a fabricated socket row. Retain exact candidate
coverage and independent socket/tmux classification where both are real.
A controller-reviewed flag alone is not coverage or authority; use matching source
pins, complete finite receipts and authenticated task/design admission as well.
Legacy schema receipts retain original meaning and are not silently reinterpreted.
No runner/plugin/product/test changes are implemented by this design publication.

Future offline helper tests must cover: exact negative registry acceptance and
changed node/function/chain/maximum/error-assertion rejection; unknown overlong
constructor rejection; unchanged100/101 construction probes; correct relative
argument/cwd versus unsafe/dynamic/unowned cwd; independent-root identity/cleanup
mismatch and archive capacity failure; unguarded tmux; uncovered dynamic edge;
false SQLite/socketpair classification; altered source/candidate/selection hashes;
full command missing live-ignore; fresh-prefix collision/repeated mktemp failure.
Use synthetic fixtures only for helper construction tests, never substitute them
for scanner results or the unchanged19 verifier probes.

Before implementation root must identify and genuinely admit the helper repair
scope (existing btq-57l70 or a supported successor) under this new approval without
rewriting old pins. Before any scanner execution retain native authority and
receipt/source/reservation checks required by both parents and this amendment.
Human ask must explicitly disclose the127-byte original negative exception,
relative kernel-argument/cwd interpretation, independent tmux root evidence and
remaining closure HOLD. Human approval of this design is not proof of completed
inventory, a new diagnostic allowance or deployment authority. All questions and
unexpected-result dispositions use Marmot admind. No live service/backend action.
