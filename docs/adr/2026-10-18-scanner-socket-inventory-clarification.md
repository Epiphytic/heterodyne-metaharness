# Scanner socket inventory: original negative cases and ownership witnesses

Proposed amendment; design task btq-zckld. ONE actual Claude Opus HIGH built-in
/code-review returned ten findings against draft debd0c4a1a709610a495d69ca76efec1bd2fdebb.
The author incorporated them once below; raw findings and dispositions accompany
this proposal. No second design review or validation, reviewer APPROVE, human
approval, implementation or execution authority is claimed. Separate authentic
human approval is requested through Marmot.
[Inputs and finite registry](../reviews/scanner-boundary/socket-inventory-clarification-inputs.json)
pin the exact parents and source facts; historical status labels are not authority.

## 1. Decision and unchanged gates

Amend only the interpretation and representation of source-backed socket/tmux
inventory in f05aeb43d8b871dec2d83c5d0df8efa933b53b68,
`docs/adr/2026-10-16-scanner-private-environment-recovery.md`.
Retain its parent e22f4ed366d6ddcdc1a5d7f0c9fe30d088b12ea8,
`docs/adr/0002-scanner-execution-fd-prerequisite-d4.md`, unchanged.
Successful pathname endpoints still require encoded argument length<=100.
The only attempted over-budget exception is the exact caught-negative client
call in §2. Its two source-proven unreached sibling derivations are classified
separately; neither becomes a permitted successful or attempted socket endpoint.
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
The existing approval btq-7k1bf selected FRESH: Liam at 2026-10-18T00:26:51Z,
Marmot message marmot:id:2a9d3b2de451, full context/approved digest
f4d1a9c371fe6282ce0314eb6d3849b477be7a5f1cbfb1b0f5df0640df8c7b30.
The appendix binds this full decision identity; 2a9d3b2de451 is a message reference,
not an abbreviated digest. Read back exact unchanged native approval before admission.
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
literal32-byte public test key via existing hex_to_npub, both pinned with the
module-level assignment and fixed independently encoded public NPUB literal parts in
the appendix. Reconstruction concatenates those fixed ASCII literal parts; the helper neither
imports tested product encoding code nor reimplements bech32. Encoding has4 HRP +1
separator +52 data +6 checksum ASCII characters:63 bytes. The sanitized case
prefix is30 bytes, first numbered suffix0 gives31. Thus basetemp<=18 + slash1 +
case31 + slash1 + NPUB63 + slash1 + missing.sock12 =127 bytes; the current fixed
mkdtemp spelling gives126. The frozen node list proves exactly ONE occurrence of
this sanitized prefix and a single tmp_path allocation, so suffix0 is justified
for this row. Recompute the same finite count for every admitted stage including
the full suite, and verify no matching manual/repeated creator; otherwise HOLD.
A two-digit suffix would require a new derivation and is not admitted as127. Both exceed100 and Linux's successful pathname budget.
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

Two exact siblings also have over-budget *derived settings* but no socket call:
`test_init_unwritable_marmot_home_is_a_safe_error` and
`test_unwritable_state_dir_is_a_safe_config_exit` in the same pinned module.
They use locked/NPUB/marmot and locked/NPUB/admind under mode0500 directories.
Register each as `original-unreached-permission-error`, with complete test,
make_settings, CLI loading/init and WnAgent.prepare/Store constructor source
pins and the unchanged nonroot guard and PermissionError assertions. On the
first path WnAgent.prepare fails before start/control connect; on the second
Store setup fails before init/hook socket creation. Record every derived path
component and its encoded length, even over100, as `syscall_reached: false`.
Verify the exact failure-before-effect chain at the admitted nonroot UID;
never infer it merely from a test name or a later passing assertion. No kernel
path measurement, synthetic connection or broad negative exception is permitted.
Any changed source or inability to establish that branch is HOLD. The existing
38-byte successful admind nesting bound applies only to actual successful
constructors and does not suppress these longer unexecuted settings derivations.

## 3. Relative AF_UNIX bind arguments and fixture-owned cwd

Two original full-offline sites use actual relative bind arguments:
`tests/deploy_host.py::bind_bus` changes into its fixture directory and binds
`"bus"` (3 bytes); `tests/test_harness_walk.py::test_a_fifo_a_socket_and_a_device_are_refused`
changes into `sock_tree / "cli"` and binds `"s"` (1 byte). Both restore the prior
cwd in finally. Record and verify the exact source pins, argument and derivation
of cwd from the current fresh fixture root. Kernel argument length is3/1, not
the length of a fabricated kernel argument. Additionally the reconstructed
resolved absolute endpoint MUST remain<=100 bytes, preserving f05a for absolute
consumers, unlink/stat and archive margin_to_100. Record both independent lengths
and refuse if either exceeds100. Do not change cwd from the helper
or run a new endpoint probe to obtain this evidence.

A `relative-bind` witness records source/call reference, literal basename,
encoded argument length<=100, fixture-root locator, derived cwd components,
source guarantees for creation/ownership and finally restoration, plus a
resolved physical-location derivation and absolute encoded length<=100 for
cleanup/absolute-consumer association. The appendix pins host_fixture supplying
root/run/user/<actual UID> to bind_bus and make_tree supplying sock/cli; it names
exact bind/chdir/finally spans and fixture-root components. Recompute with the
actual UID rather than assuming a fixed digit count. It requires a
single safe basename (no slash, dot/dotdot or NUL), directory provenance below
the current private fixture root, and no source mutation or concurrent process
cwd change invalidating the proof. A different dynamic cwd/basename remains HOLD.
An arbitrarily long resolved absolute path is refused even when the relative
kernel argument is short; there is no weakening of the existing100-byte rule.
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
cleanup/watchdog evidence must correlate that same root identity and the pinned
selector formula. Success deliberately deletes the root: the accepted success
receipt requires the actual same Guard/proc, error=None, timed_out=False,
proc.returncode=0 after existing teardown, root absent by no-follow lookup and
no surviving summary.json. Source pins for Sweeper/main/finish prove the cleanup
branch; returncode0 alone is insufficient. Preserve the pre-cleanup root identity
and source-derived selector formula, not invented individual UUID selector logs.
There is no requirement to archive files that successful cleanup already deleted.
If the root survives, only its same-identity regular files and symlink metadata
may be archived after the watchdog has positively ended, and the failed cleanup
remains HOLD. Unknown/replaced identity, timeout or incomplete publication is HOLD;
never delete another server, interrupt the watchdog, or treat archiving as cleanup.
All helper-root and independent-root artifacts share ONE combined2GiB archive
budget and the existing combined65536-entry bound, reserved once before the child.
No second allocation or extra2GiB is implied. Archive accounting includes both
roots and observer receipts, no following links/sockets/devices/external targets.
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

The approved helper repair scope, if approved, is additive inventory schema in
scripts/scanner_recovery/environment.py::validate_inventory/measured_socket_path,
runner capture and admission, a new run-local observer plugin module, and the
necessary archive_scratch/filesystem_preflight extension for two identity-bound
roots sharing the single combined2GiB/65536-entry budget. Include synthetic offline
observer/archival fixtures and all required command/manifest pin updates. This is
an explicit limited amendment of D4's two-plugin list and stage argv: retain
scanner_probe and _hypothesis_pytestplugin and add exactly the pinned observer
via explicit -p for EVERY source pytest invocation, including collection-only,
diagnostic375, detached H92, red88/create-red6, B1, overlap7, B2/green3373,
create-green6 and final full offline suite. Helper construction fixtures run
outside scanner stage authority and cannot substitute stage receipts. No
inherited plugin loading or unlisted additional plugins are authorized.

Do not wrap historic pytest_configure or import tmux_guard from the observer.
At pytest_sessionstart resolve sys.modules['tmux_guard'], verify its __file__
against the pinned module identity/hash and read that same module's _guard.run.
Use a tryfirst outer pytest_unconfigure wrapper to observe that same module's
last AFTER existing unconfigure completes; verify the same Guard/proc identity.
For collection-only, sessionstart still precedes collection and unconfigure runs;
if configure aborts before sessionstart, publish a bounded configuration-failure
receipt in the unconfigure wrapper when available and HOLD. No missing observer
receipt is accepted as success. The plugin copies source facts and bounded
no-follow identity metadata, never patches fixture/launcher or starts/stops a
server. Synthetic helper fixtures must prove actual installed pytest/pluggy
hook ordering, late plugin registration, collection-only, abort and final durable
publication; failure to meet this contract is HOLD before scanner admission.
Pin observer bytes, module identity and each exact explicit argv before invocation. Preserve existing
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
Human ask must explicitly disclose the127-byte caught-negative exception and
two finite unreached permission-error settings rows; relative argument PLUS
resolved absolute<=100 checks; independent-root deletion-as-success evidence;
shared2GiB/65536-entry archive; the new third explicit observer plugin in EVERY
pytest/collection argv; and remaining closure HOLD. Human approval of this design is not proof of completed
inventory, a new diagnostic allowance or deployment authority. All questions and
unexpected-result dispositions use Marmot admind. No live service/backend action.
