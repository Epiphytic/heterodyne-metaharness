# admind: refuse an approval ask that btq's gate would reject (btq-ryfxf)

**Status:** draft r1 for Codex review. Design only; no code.

**Line numbers:** heterodyne-metaharness at origin/main 7cacb83; beads-task-queue at cfe9d90.

## The problem

On 2026-10-14, three approval asks (tw9w9, cjwn3 and j2015) reached Liam through Marmot. Each was approved, and each then failed btq's `approval_valid()`:
- tw9w9 had no ADR file ref at its `adr_revision`;
- cjwn3 and j2015 had no `design_review`.

Each failure cost a round trip through the operator. Liam: "it's a waste of everyone's time if an approval makes it to marmot before being validated by the btq deterministic gate."

admind's post path (relay spec §8 "Post an approval ask", step 2) refuses only on what `approve-bead --json` reports today: no digest, not open, not `kind:approval`, gaps, an invalid `purpose`, an *invalid* `design_review`, a stale posted digest, or a recorded decision (`approvals.postable`, `src/heterodyne/admind/approvals.py:237`). It never asks btq whether the bead would pass once approved:
- A **missing** `design_review` gives `design_review_valid: null`, which `postable` lets through (`:252` refuses only `False`).
- The ADR pin, the revision, the policy approvers and the ask's exact field set are checked only by `approval_valid`.
- `approve-bead --json` runs `approval_valid` only on a bead that is already closed and approved (`bin/approve-bead:408`). So its `gate_valid` is always `null` at post time.

The interim fix is `~/.hermes/scripts/approval-gate-precheck.py`, run by hand before each post. It:
1. deep-copies the bead;
2. computes `btq.ask_digest`;
3. simulates the approval: `decision=approve`, `approved_by` set to the first policy approver, `approved_at` now, `approved_digest` set to the digest, `context_digest` popped, `status=closed`;
4. calls `q.approval_valid(sim, revision)`.

It works, but nothing makes a poster run it.

## Recommendation

**btq computes a gate preview, and admind refuses on it.**
- `approve-bead --json` gains an opt-in flag, `--gate-preview`.
- With the flag, approve-bead simulates its own approval of the bead it just read, using the same field builder its real decision writes. It then calls btq's own `approval_valid` on the simulated bead, capturing the reasons that function prints.
- The readout gains one key, `gate_preview`: `{"valid": bool, "reasons": [...]}`, or `null` when the bead isn't open and undecided.
- admind always passes the flag. `postable` refuses unless the preview is present and valid, and the refusal quotes btq's reasons back to the poster.
- No ask row is stored on a refusal. The poster fixes the bead, which is still open and editable, and posts again.

## Constraints, and how the design meets each

| Constraint | How |
|---|---|
| Reuse btq's rules through btq, never a copy | The verdict is `Queue.approval_valid`, called inside approve-bead, which already imports btq (`bin/approve-bead:75`). The simulated fields come from the function approve-bead's real decision uses (below), so the preview can't drift from the write. admind still imports no btq code (`approvals.py:3`); it only reads one more JSON key. |
| Refuse with the gate's reasons | `approval_valid` prints one stderr line per failed condition, and since d21f38a every rejection has at least one reason. approve-bead captures them exactly as it already does for `gate_reasons` (`bin/approve-bead:407–411`). admind quotes them, redacted, to the poster. |
| Deterministic, no network | The preview adds no new I/O beyond what `--json` already does: it reuses the beads fetched for the digest (below) and re-reads only local git objects at pinned SHAs. Every input is local: the bead and its linked beads from the same read, git objects at full SHAs, and the policy file. `approved_at` is "now", but only its format is checked (`_parse_deadline`), so the verdict doesn't depend on the clock. |
| btq unavailable fails closed | A missing or unrunnable approve-bead, a non-zero exit, a timeout, output over the cap, output that doesn't decode, a missing `gate_preview`, or a `null` preview for an open undecided bead: every case refuses the post. |
| Question asks unaffected | The question and merge post paths never run approve-bead, and nothing in them changes. |
| Offline test plan | See "Tests". Both sides use the existing offline fakes: btq's fake `bd`, and admind's `tests/fakes/fake_approve_bead.py`. |

## btq side (beads-task-queue `bin/approve-bead`)

### One field builder for the write and the preview

Today `decide()` builds its fields inline (`bin/approve-bead:640–644`):
- `{prefix}_by`, `{prefix}_at`, `{prefix}_digest`;
- `context_digest=digest`, `decision`, `via`;
- `via_ref`, when one is given.

These move, unchanged, into one function:

```python
def decision_metadata(verb, name, now, digest, via, via_ref=None):
    """The metadata a decision writes: what `decide` sets with bd, and what --gate-preview simulates."""
```

`decide()` turns its result into `--set-metadata` arguments, exactly as now. The preview applies the same dict to a deep copy. So an approval and its preview are built from one definition, and a later change to what an approval writes changes the preview with it.

This replaces the interim script's hand-written copy. Two small differences go away:
- The script pops `context_digest`; approve-bead sets it to the current digest. Either way the digest-conflict check passes.
- The script's `approved_at` uses `isoformat()`; approve-bead uses `%Y-%m-%dT%H:%M:%SZ`. Both parse.

### The preview

With `--json --gate-preview`, after the existing `--json` read and hash (`bin/approve-bead:555`):

1. **When it is computed.** Only if the bead's status is `open` and it holds no decision field (`decision_fields(meta)` is empty). Otherwise `gate_preview` is `null`: a closed or decided bead already has its real verdict in `gate_valid`.
2. **The simulated bead.** A `copy.deepcopy` of the fetched bead. Its metadata is updated with `decision_metadata('APPROVE', approver, now, digest, 'marmot')`, and its `status` is set to `closed`.
   - `approver` is the first entry of the policy's `approvers`, if that is a non-empty list of non-empty strings. Otherwise it is `''`, which `approval_valid` rejects with its own policy and "approved_by is missing" reasons.
   - The only approver-dependent condition in `approval_valid` is membership in `approvers`, so any listed approver gives the same verdict.
   - `via` and `via_ref` are provenance only, never read by `approval_valid` (PICKUP.md). The values here don't matter.
3. **The revision.** `meta.get('adr_revision')`, the same revision approve-bead's own post-write check uses (`bin/approve-bead:670`). That is the check tw9w9, cjwn3 and j2015 failed.
4. **Same content, no second fetch.** `approval_valid` recomputes `ask_digest` through `queue.show`. For the preview, `queue.show` is replaced by a function that serves beads from the `Resolution.fetched` of the hash just computed. A bead recorded as not found raises the same "not found" error `bd show` gives, so `_show_or_none` returns `None` as before. A bead not in `fetched` falls through to the lock-holding `show`; this shouldn't happen, but if it does it is ordinary I/O, and a failure is an exit 1. So the preview judges exactly the content the readout shows and the digest covers. A linked bead edited between two fetches can't make them disagree.
5. **The verdict.** `approval_valid(sim, revision)` is called under `contextlib.redirect_stderr`.
   - `reasons` is the non-blank captured lines, unchanged, in the same form as `gate_reasons` (`btq: approval <id> rejected: <reason>`).
   - If the verdict is false and no line was captured, which d21f38a should make impossible, `reasons` is `["btq: approval <id> rejected: no reason given"]`. A false verdict always carries a reason.
   - An exception other than the ones `approval_valid` absorbs (a `bd` failure, for example) propagates. That is approve-bead's existing exit 1, and no JSON is printed.
6. **Read only.** Nothing is written. The preview runs under the same per-bead lock as `--json`.

### The flag's interface

- `--gate-preview` is valid only with `--json`; without it, approve-bead exits 2 (`parser.error`, like `--json`'s own check at `bin/approve-bead:495`). `--json` keeps refusing every decision and paging flag, as now.
- Without the flag, `--json`'s output is byte-for-byte what it is today: no `gate_preview` key. So an admind that doesn't know the key keeps working, because its `Readout` forbids unknown fields (`approvals.py:43`).
- The `--json` docstring and PICKUP.md's approve-bead paragraph gain one sentence each.

**Why a flag, not a new default key.** admind decodes the readout with `forbid_unknown_fields=True`. A new key in the default output would turn every admind read into "bad output" until admind was updated, decision read-backs included, and that would make outcomes `uncertain`. With a flag:
- btq can deploy first with no effect;
- then admind deploys and starts passing it.

If admind deploys first, the old approve-bead rejects the unknown flag (argparse exit 2). admind reports that as `unavailable` and refuses every approval post until btq is deployed: closed, and visible to the poster.

## admind side (heterodyne-metaharness)

### Reading

- `ApproveBead.read` (`approvals.py:161`) runs `approve-bead <bead> --json --gate-preview` for every read: the post, the decide preflight, the read-back and the R29 refresh. One argv keeps every read the same.
- `Readout` gains `gate_preview: GatePreview | None` with **no default**, so a readout without the key fails to decode and is `BtqError("bad output")`. `GatePreview` is a frozen `msgspec.Struct` with `forbid_unknown_fields=True` and the fields `valid: bool` and `reasons: list[str]`.
- `read` also checks that the preview is non-null when `status == "open"` and `decided` is false, and null otherwise; a mismatch is "bad output". This pins approve-bead's rule from step 1 and catches an approve-bead that skips the preview.

### Posting

`postable(r)` (`approvals.py:237`) keeps every check, in its order, and adds one at the end:

```python
    if r.gate_preview is None:
        return "approve-bead gave no gate preview for this bead; try again"
    if not r.gate_preview.valid:
        return ("btq's gate would reject this approval once approved; fix the bead and post it again: "
                + "; ".join(_bare(line, r.id) for line in r.gate_preview.reasons))
```

- `_bare` removes the exact prefix `btq: approval <bead> rejected: ` when it is present, and leaves any other line as it is.
- The more specific refusals above keep their wording: the gaps, a stale digest, a decision, and the like.
- `prepare_approval` already passes `postable`'s text through `redact` (`daemon.py:807–809`). The reasons echo no bead metadata values (d21f38a): only fixed texts, counts, the bead ID, validated SHA prefixes and policy values.
- The audit reason for this refusal is `gate`, not `not postable`, so these refusals can be counted. That needs `postable` to return the audit reason with the text; the change is mechanical, done for every caller.
- `pinned_only` (`daemon.py:242`, R29) calls `postable` with the stored pin cleared, so a refresh is refused on the preview too. A refreshed card is never posted for a bead that would fail the gate.

### Deciding (recommended, small)

The policy, or a linked bead's content, can change after a card is posted. The decide preflight (`daemon.py:1250`) reads the bead again with the same argv. I recommend that `preflight` refuse an **approve** when the preview is invalid, before approve-bead writes anything, with the gate's reasons in the operator's refusal notice. Otherwise admind writes an approval that closes the bead and then fails the gate, which is today's failure, just later.
- A **deny** is unaffected: denying a bead that would fail the gate is still useful.
- If the reviewer prefers to keep decide out of scope, the post-time check alone closes the reported cases.

### Question and merge asks

They don't run approve-bead and don't build a `Readout`. Nothing changes for them.

## The workflow after a refusal

1. The poster runs `admind ask post --kind approval --bead <id> ...`.
2. admind refuses: `btq's gate would reject this approval once approved; fix the bead and post it again: <reason>; <reason>`. No ask row is stored and nothing is sent to Marmot, so there is nothing to cancel or supersede.
3. The bead is still open and undecided, so it can be edited. The poster fixes it: adds the ADR file ref at `adr_revision`, sets `design_review`, or trims the ask to exactly the ASK_FIELDS. Its digest changes; no posted `context_digest` was written, so there is no stale pin.
4. The poster posts again. The same read and preview run, and the ask is posted once the preview is valid.

For a quick check without posting, the poster can run `approve-bead <id> --json --gate-preview`. The interim `approval-gate-precheck.py` can be retired once both sides are deployed; its memory note changes to "admind refuses; the preview is `approve-bead --json --gate-preview`".

## Residuals

1. **The task's revision.** `design_allowed` judges an approval against the *task's* `adr_revision`. The preview, like approve-bead's own post-write check and the interim script, uses the approval bead's own. A task created later with a different `adr_revision` still fails the gate after approval. Catching that would need the task to exist at post time; that isn't true in today's flow, where the approval often comes first.
2. **Changes after the post.** A policy edit, or a linked bead's change, between the post and the decision. A linked bead's change already makes the digest differ, and approve-bead's `--expect-digest` refuses the decision. A policy change is caught by the decide-time check above, if it is adopted.
3. **Deployment order.** admind before btq means every approval post is refused as `unavailable` until btq lands. That is closed, and visible to the poster.

## Tests (offline; failing tests first)

### beads-task-queue `tests/test_approve_bead_relay.py` (existing fake `bd`; its setUp already asserts nothing reaches Dolt or the real config)

1. **Clean bead.** `--json --gate-preview` on a complete open bead gives `{"valid": true, "reasons": []}`. A real decision on it then gives `gate_valid: true`: the preview agrees with the outcome.
2. **Parity with the real decision**, parametrized:
   - the ADR ref missing (tw9w9's case), or pinned at another commit;
   - `design_review` missing (cjwn3's and j2015's case), or malformed;
   - the policy `approvers` empty, or malformed;
   - an extra stale `*_digest` key;
   - an ask with an extra field.

   For each, the preview's `reasons` equal the reasons in the real decision's stderr (`Closed …, but btq approval_valid() rejects it: …`), run on a fresh copy of the same bead.
3. **Read only.** After a preview, the fake `bd`'s log holds only `show` calls: no `update`, `comments` or `close`.
4. **No extra fetch.** On a bead with a linked bead ref, the number of `show` calls with `--gate-preview` equals the number without it.
5. **Null when not decidable.** A closed bead, and an open bead holding a decision field, both give `gate_preview: null`.
6. **The flag's interface.** `--gate-preview` without `--json` exits 2, and so does `--json --gate-preview` with a decision flag. `--json` without the flag gives exactly today's key set, with no `gate_preview`.
7. **One field builder.** The fields a real approval writes (from the fake `bd`'s `update` argv) equal `decision_metadata(...)` for the same inputs, and the preview's simulated metadata comes from that function. A test patches `decision_metadata` and observes the patch in both paths.
8. **A failure while previewing.** If the reused fetch is missing and `bd` fails, the exit is 1 with no JSON on stdout.
9. **A reason for every rejection.** With `approval_valid` patched to return False and print nothing, the reasons are the single "no reason given" line.

### heterodyne-metaharness (the fake approve-bead and existing admind fixtures)

- `tests/fakes/fake_approve_bead.py` accepts `--gate-preview` only with `--json`, and gains the controls `preview_ok` (default true) and `preview_reasons`. With the flag, it emits `gate_preview` for an open undecided bead and `null` otherwise. Without the flag, it emits no such key. Its interface docstring is updated.

10. **The argv.** Every `ApproveBead.read` runs `<bead> --json --gate-preview`, checked in `FAKE_BTQ_LOG`.
11. **Refused with the reasons.** With `preview_ok: false` and two reasons, the post is refused:
    - the reply is the gate message with both reasons, with the `btq: approval <id> rejected: ` prefix removed, after `redact`;
    - no ask row is stored and no chunk is queued;
    - the audit records `refused`, reason `gate`.
12. **Failing closed.** Each of these refuses the post, with no row stored:
    - a fake that rejects the flag (exit 2);
    - a readout missing `gate_preview`;
    - an open undecided bead with `gate_preview: null`;
    - a closed bead with a non-null preview.
13. **The existing refusals win.** A bead with gaps, *and* an invalid preview, is refused with the gaps message, not the gate's.
14. **Re-post after a fix.** The first post is refused on the preview. The test then edits the fake's bead to `preview_ok: true` with a new digest. The second post succeeds, superseding nothing, since no row was stored.
15. **The R29 refresh** is refused on an invalid preview, and the stale ask stays as it was.
16. **Decide** (if adopted). With the preview invalid at decide time, `!approve` is refused before any decision run: the log holds no decision argv, and the refusal notice quotes the reasons. `!deny` still runs.
17. **Question and merge posts** never run approve-bead: the fake's log is empty after each.

## Changes, if approved

- **beads-task-queue `bin/approve-bead`:**
  - `decision_metadata()`, used by `decide()`;
  - the `--gate-preview` flag, and its preview in `readout_json`;
  - docstring updates.
- **beads-task-queue `docs/PICKUP.md`:** one sentence on `--gate-preview`.
- **beads-task-queue tests:** 1–9.
- **heterodyne-metaharness `src/heterodyne/admind/approvals.py`:**
  - `GatePreview`;
  - `Readout.gate_preview`;
  - the read argv and its null/non-null check;
  - `postable`'s new check and its audit reason.
- **heterodyne-metaharness `src/heterodyne/admind/daemon.py`:**
  - the `gate` audit reason passed through;
  - the decide preflight check, if adopted.
- **heterodyne-metaharness relay spec** (`docs/superpowers/specs/2026-10-05-admind-marmot-relay-design.md` §8, Post step 2): one more refusal, "btq's gate preview is missing or invalid (the reasons go back to the poster)".
- **heterodyne-metaharness tests:** the fake approve-bead and tests 10–17.
- **Deployment:** btq first, then admind.

Two repositories change, so this is two PRs: btq's first, with admind's following.
