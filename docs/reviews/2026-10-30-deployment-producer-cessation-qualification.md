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
binaries and kernel-UID-checked systemd/private and fallback user-bus sockets. Before a stop it retains the actual
PID descriptor and original cgroup directory. Afterward it requires the original
PID to have exited and the retained cgroup to be empty or actually removed,
with stable unit invocation/start identity and boot. A forced timeout may prove
cessation when those positive facts hold; it never settles historical UNKNOWN.
Its stopped snapshot reuses the existing bounded WAL snapshot implementation,
reads the configured live DB path, includes committed WAL contents, retains an
exclusive attempt output, publishes it without overwrite only after validation,
and surrounds the copy with exact old-unit observations.
No older backup is restored. This covers one invocation only: source-specific
qualification and positive exclusion of all other writers remain mandatory
before recovery can release any issued target. A snapshot or dataclass is not
independent recovery authority.

The retained journal now allows ordinal-zero no-effect failure after recovery
only if all retained recovery commands were cancel. Safe-stop, held-restore,
mixed commands, any issued intent or uncertain effect retain target exclusion.
This closes PR143's reported small medium gap in a separate source change.

Original source validation: 116 focused tests passed with inherited bytecode suppression unset.
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
protected deployment profile and exact deployment packet. No production path, unit, account, installed binary, queue, native approval or
production database changed. The later R1 causal test creates/removes only one
uniquely named disposable kernel cgroup and its own harmless fixture child.
The next concrete source boundary is the fixed root helper with genuine sudo
ancestry/UID proof, independently protected permit/profile and durable one-use
intent, followed by the owning receiver integration and full finite recipe.


## First HIGH review repair

The actual first HIGH built-in code-review verdict is CHANGES REQUESTED and is
retained under `deployment-producer-cessation-code/r1/`, with command/report/source
receipts. The reviewer could not run the configured test environment; that limit
is preserved in the actual report, independently of the source-bound test logs.
No second/validation review has been run by the author.

The actual Linux cgroup probe confirmed the finding: an empty cgroup directory's
link count remains 2 after removal. Cessation now accepts removal only when the
kernel `/proc/self/fd` link of the originally retained descriptor names the exact
fixed `/sys/fs/cgroup` plus captured ControlGroup and its deleted marker. A
missing current path or missing fixture events alone remains insufficient. The
real causal regression creates one unique disposable cgroup, moves only its own
harmless child into it, observes populated refusal, kills/reaps that child,
observes empty acceptance, removes that cgroup and observes retained-descriptor
acceptance with link count still 2. It never stops, reads or alters a host service.
The original manager/cgroup fixtures remain explicitly synthetic.

Constructor/source/database/SQLite failures now produce typed HOLD diagnostics.
Snapshot attempts live in an exclusive directory under the explicit destination
parent; failure removes the whole attempt. Publication is a non-overwriting link
only after stopped-state and live-source validation; an existing destination is
never deleted. Primary `systemd/private` and fallback bus sockets receive actual
peer-UID checks plus inode checks before and after the pinned systemctl command.
The manager's Result=success classifies configured successful exits and ordinary
SIGTERM stops as clean; actual exit code/status remain retained facts. No result
settles an earlier UNKNOWN effect.

Publication reuses the unchanged core's tar-error tuple. Its strict role-bounded
copy loop is retained deliberately: `store_core._copy` uses a different global
1GiB bound, while a commit object requires 1MiB and must refuse growth at that
limit. The scratch location is now fixed `/run/heterodyne-deploy-publication`,
opened through the existing protected descriptor walk and required to be owned
by the producer UID with mode0700 and protected ancestors. Missing provisioning
refuses. Anonymous mode0600 temporary files use that descriptor anchor, never
ambient TMPDIR, and no needless durability fsync is claimed for ephemeral copies.
Fixtures use an equivalent private directory in their isolated worktree, without
creating the production directory. This exact runtime directory must join the
future protected provisioning write set.

The exact final card is checked once against one profile snapshot before HTTPS
corroboration. The original whole-packet fixture remains byte-identical. Returned
request/row/parts reuse the checked renderer, and oversized cards cause no network
read. Duplicate complete-tree parsing remains because the unchanged core returns
no validated member inventory; that bounded cost is preferred to changing its
authentication contract or reimplementing Git validation.

Final repair verification: 130 focused tests passed in 4.33s, with Ruff and
strict source Pyright passing. Source hashes were unchanged throughout the run;
the exact commands, source hashes and complete logs are retained in
`deployment-producer-cessation-code/r1-repair/`. The real cgroup test requires
the qualified Linux host's disposable cgroup/sudo prerequisites; this is not a
universal portable-suite claim.

The actual quality-delta exits 2: 24 rows, 1 minor, 12 preexisting-worse,
12 new-symbol and 11 gating. Production constructor complexity was reduced by
extracting the same capture logic and fixed socket check. Remaining rows report
class verbosity (215 to 238 lines), renderer churn, test callback reachability
and short regression-fixture clones. These are retained findings, not a passing
quality gate or a changed baseline. The causal regression fixtures and explicit
security checks are preserved for review rather than hidden behind an abstraction
that changes their evidence. Runtime admission and production authority remain
unqualified; no protected directory, service or deployment was installed.

## Second HIGH review repair

The actual cumulative second HIGH report and its honest command/result receipt
are retained in `deployment-producer-cessation-code/r2/`. Its verdict remains
CHANGES REQUESTED; the author has not run a validation review.

SQLite no longer opens any service-owned original database or sidecar path with
root privileges. A descriptor-anchored no-follow walk retains the live database,
WAL and SHM (or exact sidecar absence), requires regular single-link files with
matching expected ownership and no group/world write, and checks complete inode,
size and change metadata. Strict bounded pread copies only DB (1GiB maximum) and
WAL (256MiB maximum) into a new protected private attempt. SQLite reconstructs
SHM only there. Source names, descriptors and absent sidecars must remain stable
across the copy and stopped observations. The existing WAL snapshot operates on
that private copy. Its output is linked without overwrite through the retained
protected destination-parent descriptor after matching destination custody is
checked again. Failure removes the private attempt and never deletes a prior
output. These checks still do not establish exclusion of every external writer.

The causal namespace regression runs actual namespace-root and subordinate
service UID1 against real SQLite WAL files, with synthetic cessation facts.
Four planted WAL/SHM symlink/hardlink aliases are refused while a root-owned
read-only sentinel remains byte/owner/mode unchanged. Service-owned, writable
and symlinked destination parents are refused. The positive protected snapshot
contains the newest committed WAL row, while source SHM remains service-owned.
This requires Linux user/subordinate-ID and mount-namespace support; it neither
provisions a host account nor touches a real service/database. The historical
real cgroup fixture retains its separately documented host prerequisites.

Whole OUTAGE preparation and later native binding now use the exact same posted
whole-card validation. Native ask shape and membership checks are reused without
requiring a separate unposted inner Release card to render. The regression makes
inner-card validation refuse while whole-card preparation and actual core
synthetic-native validation still agree; malformed native shape remains refused.
CLD_DUMPED is retained as forced even if the manager reports success.

The fixed HTTPS reader binds the raw TCP socket, wraps without an implicit
handshake, binds the TLS socket and only then performs the handshake. The total
watchdog can therefore interrupt a trickling handshake, including deadline
expiry during socket ownership transfer. A real loopback TLS trickle regression
exercises that deadline; it is not production CA/route qualification. Platform
DNS/TCP-connect behavior remains explicitly unqualified. Hosted Git/HTTPS bytes
are exactly canonical JSON; the original request may be noncanonical JSON with
the same decoded packet. Original bytes/hash and canonical bytes/hash remain
separate retained values, with no byte-equality claim between them.

Final second-review repair verification: 134 focused tests pass in 4.79s with
inherited bytecode suppression unset; Ruff and strict source Pyright pass.
Exact commands, hashes and complete logs are in `r2-repair/`. The quality delta
exits 2 with 29 rows and 18 preexisting-worse gating findings, principally repair
churn, synthetic test reachability and short test scaffold clones, plus the new
81-line retained-database helper and 80-line causal namespace probe. These are
recorded costs rather than a passing quality gate; no baseline or metric was
changed. The privileged-path repair deliberately retains explicit file identities
and bounded copies instead of delegating authority to a caller-provided reader.
No production runtime, source capsule, queue or installed profile was changed.
