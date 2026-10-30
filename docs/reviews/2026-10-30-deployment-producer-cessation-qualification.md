# Protected producer and stopped-state source qualification

This continuation of approved gsc5 is source-only. PR143's actual three review
reports and dormant-PoC disposition remain unchanged. It implements a concrete
packet publication check and owned card construction, plus one actual Linux
invocation/SQLite observation primitive. It does not deploy a receiver, root
helper or executor, or assert any approval from fixtures.

The private producer takes original operation bytes, protected targets/group,
an immutable outer publication URL and two descriptors owned by its future
protected receiver: the complete Git archive and original commit object. The
unchanged pure `store_core.check_git` verifies the full commit hash and tree.
The exact canonical packet must be a mode-100644 member. A fixed-host TLS read
from raw.githubusercontent.com must return identical bytes before any card is
returned. Redirects, alternate hosts, encoded bodies and ambient proxies are not
accepted. The existing `HttpsResponse` watchdog bounds connected I/O; platform
DNS/connect timing still requires qualification. The fixed system CA bundle,
Python/stdlib dependencies and object-view custody are not qualified by these
tests. There is no production object-view opener; absence of owned protected
descriptors keeps the owning factory held. A supplied URL alone is no proof.

The returned private value separately retains original request bytes/hash,
canonical packet bytes/hash and publication URL/commit/path/published-byte hash.
The outer commit must differ from the inner Release commit. The fixed Product
renderer resolves the actual fixed-profile login directories and endpoint size;
no request selects a card, operator, group policy, reader, chunk bound or path.
Native owns posting, publication ACKs, authenticated MLS evidence and one-use
semantic consumption in the separate native implementation. This source does
not replace that custody with a deserialized prepared value.

`_AdmindInvocation` observes the fixed user systemd unit through pinned system
binaries and a kernel-UID-checked manager bus. Before a stop it retains the actual
PID descriptor and original cgroup directory. Afterward it requires the original
PID to have exited and the retained cgroup to be empty or actually removed,
with stable unit invocation/start identity and boot. A forced timeout may prove
cessation when those positive facts hold; it never settles historical UNKNOWN.
Its stopped snapshot reuses the existing bounded WAL snapshot implementation,
reads the configured live DB path, includes committed WAL contents, retains an
exclusive new output and surrounds the copy with exact old-unit observations.
No older backup is restored. This covers one invocation only: source-specific
qualification and positive exclusion of all other writers remain mandatory
before recovery can release any issued target. A snapshot or dataclass is not
independent recovery authority.

The retained journal now allows ordinal-zero no-effect failure after recovery
only if all retained recovery commands were cancel. Safe-stop, held-restore,
mixed commands, any issued intent or uncertain effect retain target exclusion.
This closes PR143's reported small medium gap in a separate source change.

Validation: 116 focused tests pass with inherited bytecode suppression unset.
The publication cases use actual deterministic disposable Git commits, complete
archives and file modes, with synthetic HTTPS and profile. The refreshed v2
cross-language fixture is produced by that real local Git proof and actual
Product renderer (1660 characters), but its native group/operators, endpoint
profile and HTTPS result are synthetic. Cessation tests use actual child pidfds,
kill/reap and SQLite WAL contents with synthetic manager/cgroup/custody/capacity
facts. They do not stop or contact a real service. TLS tests model the transport
and verify fixed host/request/no-redirect behavior; they prove no live route.

Strict source Pyright reports zero errors. Ruff and install-agnostic checks pass.
The source quality delta exits 2 with 50 rows: 13 major findings, four minor and
33 new-symbol findings. Reported debt includes short test-pattern clones and
repair churn, pytest/dynamically-called fixture reachability, the new six-input
private producer and the explicit Linux observer's size/complexity. These are
retained limitations; no metric was acknowledged or baseline changed. The
observer's multiple kernel/manager checks stay explicit rather than introducing
a caller-selected observation callback.

Proposed additional runtime writes are anonymous private copies of retained Git
objects and a fresh protected stopped-snapshot destination. Exact owning object
view, snapshot path, permissions and runtime source closure belong in the future
protected deployment profile and exact deployment packet. No host path, unit,
account, installed binary, queue, native approval or production database changed.
The next concrete source boundary is the fixed root helper with genuine sudo
ancestry/UID proof, independently protected permit/profile and durable one-use
intent, followed by the owning receiver integration and full finite recipe.
