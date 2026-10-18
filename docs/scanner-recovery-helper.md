# Private scanner recovery helper

This helper implements the private environment construction in approved revision
f05aeb43d8b871dec2d83c5d0df8efa933b53b68, approval btq-7k1bf. It is source-only:
btq-57l70 does not run scanner gates or clear btq-1wu2i's hold. The original
manifest, verifier/mask chunks,19 malformed probes, product and oracle bytes stay
unchanged. The approved ADR is copied verbatim, including its historical status.

Run `python scripts/scanner_recovery/runner.py --help` for exact inputs. Without
`--execute`, it validates frozen source/provenance/reservation/predecessor inputs
and prints the exact execution plan. Execution additionally requires a separate
controller release binding driver, support, supplement, failure map, reviewed
inventory, baseline disposition, source receipt and reservation hashes, exact
stage/source/run directory. Neither a JSON receipt nor this helper authenticates
Marmot: the controller must read back real native approval and ownership first.

Stages are diagnostic375, exact-H92, fresh red88, create-red6, B1-3373,
overlap7, B2-3373 and create-green6. Each stage is one attempt with a new private
root. No automated retry occurs. The final signed whole-suite freeze and hosted
unfiltered CI are separate controller gates, not implemented or claimed by this
strict-gate driver. Completed stages are preserved and never rerun implicitly.

Before any scanner child, provide the actual source-backed inventory for its
frozen whole offline suite, including final-suite modules. The inventory contains
`source_root`, exact `source_candidates` from `environment.source_candidates`,
`reviewed_by_controller: true`, and nonempty `derivations`. Every candidate line
must be covered by references and a concrete reason. Socket rows additionally
bind base (`h`,`p`,`t`,`r`,`root`), case-component bytes, suffix bytes and exact maximum
encoded bytes. Maximum calculations reserve the full16-byte root/18-byte child
budget and include the separator before a nonempty case component; suffix_bytes
includes every separator in the remaining suffix. Actual shorter roots never
change a reviewed worst-case bound. Tmux rows bind fixture-owned `-S`/`-L` or actual audited guard source
references. Lines containing socket or tmux tokens cannot be assigned non-endpoint rows;
non-endpoint rows still need a source-backed explanation. This deliberately
requires controller review; keyword matching alone does not prove a path bound
or tmux isolation. Missing or changed coverage HOLDs before scanner execution.
The enumeration includes indirect Python bind/connect/Unix calls throughout the
tree and every shell/config line, including scripts under docs. It remains a
review aid: the controller must trace test-reachable endpoint construction and
external commands/configs, identify any additional reachable inputs, and refuse
unbounded or unresolved paths. Equality to a keyword inventory is never proof
of whole-suite reachability. The helper does not ship an invented inventory for
a future source snapshot.

Each attempt uses a new0700 `/tmp/hz*` root of at most16encoded bytes, children
of at most18bytes, explicit basetemp, an empty14-key environment with a private
empty XDG runtime, and a fresh Hypothesis directory. It tests executable scratch,
records mountinfo and requires2GiB available. A synthetic100-byte AF_UNIX bind
and101-byte pre-bind rejection precede scanner children. Four new construction
probes produce their own source-bound report; they do not alter the original19.
Every completed result requires all57 non-null mandatory fields.

At completion, including HOLD, archive regular scratch bytes using nofollow
source descriptors and preserve link/special-file metadata without following
external targets. Retention is bounded to2GiB and65536entries; overflow or any
preservation failure HOLDs. The original volatile scratch remains for disposition.
No service, live user bus, queue mutation, model call or deployment is performed
by this helper. Test children remain the separately admitted scanner operation.

The bounded construction tests use stdlib unittest to avoid collecting scanner
or application tests:

```sh
python -m unittest discover -s tests -p 'test_scanner_recovery*.py' -v
```

Plan mode also validates inventory bounds/coverage, the additive contract, four
construction rejection checks, and exact diagnostic source hashes and375nodes
before creating an attempt directory. All later stages bind every predecessor,
including the diagnostic, to common driver/support/supplement/disposition/failure
map hashes and transitive source-receipt references. H92 additionally requires
clean porcelain, only hostread node IDs and exact added-node adjacency. Input
JSON is parsed and hashed from the same read bytes.

Runtime checks and archiving run independently; either failure preserves the
original HOLD diagnostic and prevents PASS. Directory traversal opens only
no-follow directory descriptors and propagates listing/open errors. It never
opens a symlink target, and only a complete archive gets an inventory digest.

Second-review repairs retain the original 51+6 fields, 14 environment keys,
plugins, product, dependencies and test oracles. Source candidates now include
systemd `.service`/`.socket` units and extensionless shebang scripts. The parsed
supplement and raw inventory bytes each have one identity; execution re-enumerates the candidate map and rechecks complete candidate-file
hashes before any child. The previous single-enumeration optimization was
replaced because it could miss newly added files.

Every socket derivation used by the runner also supplies
`worst_case_component` and `worst_case_suffix`: actual UTF-8 path components
whose encoded byte lengths equal the reviewed component/suffix maxima. Suffixes
include their leading separator. The helper reconstructs each worst-case path
under the literal private base and records length, reviewed maximum and margin
to 100. These witnesses supplement source review; arbitrary witness strings do
not prove reachability. The controller must cover every reachable constructor
and invocation with exact source references and reject dynamic/unbounded paths.
No complete inventory for an actual scanner source freeze is shipped here.

The retained archive inventory additionally records encoded lengths and margins
for surviving socket entries without opening them. Unlisted bases, lengths
above the reviewed maximum for their base, or lengths above 100 HOLD while
preserving the archive. Transient sockets removed before preservation are not
observed: no sampling or absence claim replaces the complete source-backed
constructor bounds. Runtime socket/bus checks remain a separate no-follow,
depth-first check of the entire private runtime tree.

After every child, including normal exit, the runner observes the leader without
reaping, kills its process group and waits for all non-zombie members to vanish
before preservation. The unreaped leader prevents PID/PGID reuse before the
kill. This proves group quiescence, not containment of a process that deliberately
escapes into another session; test-owned tmux/server cleanup and audited
isolation remain required inventory obligations. Unconfirmed group quiescence
retains scratch in place and refuses to copy it. Construction exceptions after
mkdtemp carry their partial layout into the HOLD result; available scratch is
archived independently of an absent runtime child. No failed attempt retries.

Third-review critical repairs enforce socket and tmux obligations independently
for each referenced line. Mixed rows cannot hide a socket bound behind tmux
isolation; a line containing both needs both row kinds. Candidate discovery and
socket obligations use one casefolded token set, including short_dir, listen_url,
connect_ex, sendto, socketpair, Listener/Manager and XDG_RUNTIME_DIR. Additional
unit/drop-in/shell suffixes and known extensionless build/process files are
review prompts. Candidate equality still does not prove complete reachability.

Source reads require nofollow regular descriptors, at most8MiB per file, with
bounded reads; FIFOs, links and oversized selected sources HOLD planning.
Runtime/archive traversal is iterative with at most64 child-directory levels
and65536entries; excess depth HOLDs with scratch retained, rather than losing
result finalization through recursive stack exhaustion. A full archive digest
is absent when preservation is incomplete. Unknown /proc reads HOLD; ENOENT and
ESRCH are vanished processes. Inspection literals are inserted in one pass.

The compatible process_group_quiescent field refers only to the original PGID;
quiescence_scope states this explicitly and global_descendant_quiescence_proven
is always false. Descendants that escape via setsid or tmux daemonization are
not contained by this helper. Their real fixture cleanup/isolation remains an
unwaived stage-admission obligation, together with the complete source-backed
constructor inventory and transient-socket measurement limits above. No scanner
stage has been run or admitted by these repairs.

Fourth-review failed-attempt preservation handles the mandatory inventory field
while it is still None. Preflight failure and partial-layout failure retain HOLD,
archive available scratch, and allow result.json to be written. Missing required
evidence remains missing; no bounds or PASS are fabricated.

The fourth review also records unwaived future stage gaps: bounded fixture-owned
tmux -S paths, the exact overlap execution tree and symlinks, manual guard-source
review, final ignored-candidate revalidation, and external hardlink copying. The
runtime check and archive are separate observations. These limitations prohibit
using this undeployed helper PoC as actual stage-admission evidence without their
resolution; no scanner gate has been run.
