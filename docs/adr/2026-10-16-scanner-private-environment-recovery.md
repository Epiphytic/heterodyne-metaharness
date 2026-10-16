# Scanner private execution environment recovery

Status: proposed; no retry or new test execution authorized by this document.
Author: Codex. Design bead: btq-sxni3. Implementation remains btq-1wu2i.
This additive amendment changes only the private execution environment and the
explicit recovery sequence of ADR 0002 D4. Scanner matching, production code,
expected outcomes, collection identities, and historical evidence stay unchanged.

## Actual failure and responsibility

Renewed B1 attempt a22759f7-d5c5-45a6-9a2e-d9d605a4f7cb collected and executed
the required 3373 nodes, but recorded 379 failures and 2994 passes in 718.04s.
Its strict verifier correctly returned HOLD for a nonassertion failure. The
expected four scanner assertions are among those failures. Actual result SHA256:
`d036ff9b308c04298b288b5a5d54efc133fe7b346eee94c016c268d99e13db4e`.
All raw logs, JUnit, collection, source receipts, and durations are retained.
The source snapshot after the attempt equals its admitted source receipt.

The controller prepared a fresh private TMPDIR underneath a long evidence path.
AF_UNIX socket filenames derived from that directory exceeded the kernel limit,
causing sandbox/channel failures and their downstream assertions. Two generated
unit checks also reported that systemd-analyze could not resolve RuntimeDirectory
in the isolated environment. These are observed causes, not a claim that every
unexpected failure has already been explained or that the corrected run will pass.
No retry, overlap, B2 transition, or final suite followed the HOLD.

## Private short-path layout

Each new attempt creates one exclusively owned, unpredictable directory directly
under the real system temporary directory, using mkdtemp and mode 0700. The parent
is selected explicitly as `/tmp`, not from inherited TMPDIR. Validate its real
directory identity, ownership and mode without following a caller-supplied
symlink. Within it create private home, tmp, runtime and Hypothesis directories.
The attempt keeps all these directories until evidence and human disposition
permit cleanup. It records their literal private paths and lstat identities;
public documents use logical locators. Never reuse a failed attempt directory.

HOME, TMPDIR and HYPOTHESIS_STORAGE_DIRECTORY point into this short private root.
Evidence, plugins and logs may remain in the durable controller evidence tree;
they do not become the parent of runtime socket filenames. Check filesystem
encoded byte lengths before launching any test process: the private root is at
most 32 bytes; HOME/TMPDIR/runtime/Hypothesis paths are each at most 40 bytes.
Keep an additional 60-byte suffix budget for fixture-created socket names.
This conservative preflight does not claim all arbitrary socket names will fit:
any further path-length failure remains unexpected and stops the sequence.

## Explicit runtime directory

Extend the empty-environment allowlist from exactly 13 to exactly 14 keys by
adding XDG_RUNTIME_DIR. Derive it from the newly created private runtime directory;
never copy the parent value, use the actual user runtime directory, or expose a
live user bus. The directory must be owned by the current UID, mode0700, real,
fresh, and empty before the attempt. No DBUS_SESSION_BUS_ADDRESS is passed.
systemd-analyze --user verify remains an offline generated-unit validation;
no service start, reload, bus call, or deployment is introduced.

All other 13 keys and values retain D4 meaning, including locked Python3.12,
active source first in PYTHONPATH, explicit plugins, no inherited variables,
and HZ_REQUIRE_TMUX=1. Record the exact 14-key map and new runner source hash.
Do not add environment keys opportunistically after any failure. No production
code, test, expected-failure JSON, verifier function, or dependency changes.

## One explicit authenticated recovery disposition

Approval must name failed attempt a22759f7 and the following permitted sequence.
Question rcje asks the operator to select baseline reuse or fresh red baselines;
its answer is retained with the separate approval. No answer defaults to fresh
red baselines. Neither the question nor this proposed design clears needs-human.

1. Root authenticates approval, checks unchanged source/test/lock/interpreter
   and H-delivery receipts, and uses supported recovery/claim APIs. Keep existing
   design pins and history; do not manufacture a replacement task approval.
2. Reserve the sole broad slot. Under the corrected 14-key environment run one
   fresh strict H92 gate from the exact delivered H test/product bytes. Preserve
   the previous passing H92 as historical evidence; it cannot substitute for
   compatibility with this changed environment contract. Unexpected failure HOLDs.
3. If the operator expressly selected reuse, retain the actual t1 88-case and
   create-red 6-case PASS results as red-baseline evidence, documenting that only
   private path layout and the runtime-directory key differ. Otherwise run each
   once again in disposable baseline source under this environment. The original
   expected 53/35 and 4/2 outcomes and all other strict checks remain mandatory.
4. Run one new B1-only3373 gate. Only exact four expected assertion failures and
   3369 passes permit the next stage. Then one overlap7, the exact archived B2
   transition, one all-green3373 and one create-green6. No retries or skipped gates.
5. Preserve the signed clean final freeze, one final serial whole offline suite,
   actual implementation review and hosted unfiltered CI/secrets requirements.
   Full-suite declared normal skips remain separately justified; strict gates
   never accept skips, xfail, errors, changed identities, or changed failure types.

The new H92 execution, and any newly selected red-baseline execution, are explicit
changes from the prior one-renewed-sequence rule. Already-passing data is never
overwritten or relabelled. Bind each predecessor by actual path and raw hash.
Every attempt retains all 51 required fields plus original extra provenance,
source/import/inventory checks, raw and semantic hashes, negative19 verifier
probes, real exit/signal/timeout, durations, and actual source-before/after.

Root separately inspects and pins adapted helper code before invocation. Validate
only the bounded private path/environment construction and result serialization
with synthetic unit fixtures first; these are not scanner gate results. There is
no automatic retry loop, oracle relaxation, GC workaround, source rebase, node
drop, parallel pytest, legacy-driver execution, dependency upgrade or live action.
Any unexpected outcome immediately HOLDs for a new authenticated disposition.

## Review and approval

Use exactly one Claude Opus HIGH built-in /code-review design review. Preserve
the actual report, incorporate findings once and publish the final signed design
with a direct immutable GitHub link. The human request must list the changed
environment contract, fresh H92, selected baseline mode, failed-attempt hash and
retained requirements. No second design review or validation review is requested.
This is private test execution authority only. Deployment remains a separate gate.
