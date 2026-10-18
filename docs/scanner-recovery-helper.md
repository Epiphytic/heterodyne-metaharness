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
bind base (`p`,`t`,`r`,`root`), case-component bytes, suffix bytes and exact maximum
encoded bytes. Tmux rows bind fixture-owned `-S`/`-L` or actual audited guard source
references. Non-endpoint rows need the source-backed explanation. This deliberately
requires controller review; keyword matching alone does not prove a path bound
or tmux isolation. Missing or changed coverage HOLDs before scanner execution.
The helper does not ship an invented inventory for a future source snapshot.

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
python -m unittest discover -s tests -p test_scanner_recovery_environment.py -v
```
