# Private owned card producer process: source qualification

The approved gsc5/gba3 implementation now has a concrete private producer process
contract for the native semantic owner to call. This source installs no launcher,
executable, service, profile or permit publisher. A card, correct output bytes or
root-owned JSON never independently proves consent or protected invocation.

The fixed entry is `deployment_producer_process._entry()` with no arguments.
Stdin contains exactly the original operation packet (at most 32768 bytes), followed
by EOF. Descriptor 3 holds the closed producer profile, descriptor 4 the complete
Git archive and descriptor 5 its original commit object. Each descriptor must be a
regular mode0400 single-link file, root/producer-owned and opened read-only. The
three identities must differ; all retained stat identities must remain unchanged
through production. Reads use pread and do not change the owning process's seek
positions. The profile itself is bounded, duplicate/unknown keys refuse, and its
original-operation SHA and canonical-packet SHA must match before publication.
The existing exact Target mapping, group and immutable publication URL remain
bound. There is no path, command, card, transport callback or environment selector
in the entry contract.

The native factory must create that operation-specific profile from its protected
Target/group/publication configuration and its own retained original operation.
It must own the complete Git-object descriptors. The profile cannot be accepted
from build ingress as configuration or treated as an authority receipt. Root or
same-UID ownership is only a local custody predicate; native enrollment and
separate build/executor UIDs still have to establish who can create those files.

The intended enrolled invocation uses the fixed protected Python executable with
`-I -S -B`, a fresh private `-X pycache_prefix`, and a fixed source-owned bootstrap
that adds only its protected source/site roots and invokes the private entry.
Native must verify and retain the actual executable, stdlib/shared-library and
package/import closure before execution, provide only the exact three owned
file descriptors, feed the bounded original packet, close stdin, and enforce a
total child deadline with bounded stdout/stderr. This source does not invent
that launcher enrollment or accept caller-selected interpreter/source roots.
The old readback runtime helper remains unchanged; it has different fixed
paths/readback semantics and is not silently generalized into a producer launcher.

The source reuses the actual `_prepare`: exact final card/profile validation
precedes complete local Git commit/tree/regular-mode proof and the fixed HTTPS
publication corroboration. The fixed protected `/run` scratch prerequisite and
platform DNS/connect qualification remain explicit. No provisional card is
returned before those checks succeed.

Stdout is one canonical JSON `heterodyne-owned-card.v1` record, bounded 256KiB.
Byte fields are base64 as encoded by msgspec. It retains original operation and
canonical packet separately, exact original AskPost bytes, group/ask identity,
the whole single rendered card, immutable URL/commit/path/publication SHA,
original/canonical SHA, request SHA, and SHA256 of the JSON single-card array.
The closed output correlation check refuses unknown/duplicate keys, noncanonical
encoding, changed bytes/hashes/group/card/request/publication coordinates and
oversize output. It recomputes the actual packet and final card. The native
factory may consume this only as stdout of its own pinned invocation, retaining
its original operation/profile/card bindings. Parsing a correctly correlated
arbitrary blob is not a supported authority path. Output JSON is a private
source-integration contract, not an external evidence-import protocol.

The interop fixture `tests/fixtures/deployment-card/owned-process-v1.json` is
checked against the actual isolated child and previous whole-packet fixture.
The native owner can bind its private OriginalAsk to these exact base64 fields,
not a caller-written DTO. The fixture supplies no native Engine, real approval
or installed launcher proof.

Tests execute actual Product source in a real isolated subprocess, with inherited
owned descriptors and real disposable Git objects. The positive child uses
explicit synthetic HTTPS bytes and rendering profile; its test bootstrap also
remaps arbitrary fixture descriptors into the fixed slots. A production native
launcher must own those slots and may not reuse test bootstrap source selectors.
Negative child cases use no successful transport mock: an audit hook fails on
any socket operation, proving malformed/original/oversize/argv/custody/closed-fd
refusals occur before network access. Other tests reject writable descriptors
whose file mode appears read-only, hardlinks, pipes and tampered output. No host
service, production directory, queue, native approval or deployment changed.

The runtime write set for future exact deployment approval adds this producer
source and its enrolled complete runtime closure; operation-specific private
profile files; protected Git descriptors; fresh private cache prefix; and retained
original/output hashes in the owning receiver journal. Root permit publication
still requires genuine one-use native decision, independent route admission,
stopped/latest-state proof and durable target reservation. This producer does
not manufacture any of those missing capabilities.

Final source-bound verification: 151 focused tests passed in 11.39 seconds with
inherited bytecode suppression unset. Ruff and strict source Pyright passed.
Exact public command templates, raw argv hashes, source/log hashes and unchanged
source receipts are retained under `deployment-producer-process-code/`. The
existing Step1 interpreter supplies dependencies; the new worktree has no local
`.venv`, which Pyright reports while resolving the explicitly selected interpreter.

The actual quality-delta exits 2 with 11 rows: 5 preexisting-worse/gating and 6 new-symbol.
Its reachability lens does not follow the fixed string bootstrap or pytest fixture
injection; it flags the private entry, fixture and six test functions despite their
actual child executions. Three normalized test-clone rows match ordinary bounded
refusal/assertion skeletons in existing acceptance/descriptor tests. Those tests
exercise different concrete objects and interfaces; no broad cross-subsystem test
helper or public entry was added merely to change the metric. Full findings remain
visible, with no quality-baseline change or invented pass.

The install-agnostic hook initially refused the literal host path in the Pyright
log. Its public copy now explicitly substitutes `${HOST_HOME}`; the original raw
log is preserved privately with its exact SHA in the manifest. No hook is skipped
and no executable/source bytes changed for this documentation correction.
