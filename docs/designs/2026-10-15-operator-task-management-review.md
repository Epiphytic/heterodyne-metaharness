**VERDICT: APPROVE** (round 2 of 3)

**Good enough for a PoC? Yes.** d2 makes every fix the round-1 review asked for, and as scope cuts rather than new machinery. Nothing left blocks implementation. The three notes below are optional clarifications to settle while writing the implementation plan; none needs a d3.

## What I checked

- **Integration worktree:** `beads-task-queue-ywnix-integrate` is at `2017239` and clean. Its history matches d2: `61e0852`, then `8ec7781` (merge of master `79becda`), `37996db`, `7968330`, then the merge.
- **The manual recovery procedure** (`docs/OPS.md:155` "Recovering a bead to a controller lane") is what d2 says it is. It changes only the assignee, requires the pins to be byte-identical and needs confirmed termination plus explicit operator authorisation. Line 181 says "A holder without a process record on this host still needs the manual procedure", which is exactly the bootstrap d2 uses for ywnix.
- **The cause of round-1 H1 is real:** `holder_ended()` (`bin/btq:1794`) returns `unknown` in six cases, including `process not recorded` at `:1807`. So d2 adding a narrow "unknown, covered by the card" path is the right fix, and `alive` still always refuses.
- **The native approval edge is part of the protocol:** `docs/PICKUP.md:176` says never remove an approval dependency, and `:261` says approve-bead needs the task to block on the approval. `design_allowed` (`bin/btq:859`) checks only the metadata pin. So d2 requiring both the pin and the edge is correct and stricter than round 1's M3 remedy.

## Round-1 findings in d2

| R1 | Status in d2 |
|---|---|
| H1: reclaim can't move `unknown` holders | **Fixed.** The card shows the `unknown` reason and the cessation evidence, and the chosen answer must explicitly cover the uncertainty. `alive`, elapsed age or a stop request alone still refuse, and liveness is rechecked just before the write. |
| H2: circular bootstrap | **Fixed.** The order is: manual OPS recovery, delta review of `61e0852..2017239`, close through the ywnix lane, Liam's master go, then merge. The yzm5c placeholders stay as reviewed and aren't a prerequisite. |
| H3: wrong home / retired harness | **Fixed.** Code goes only in `beads-task-queue`. Both legacy checkouts are reference only, with no rebinding or checkpoint work. The facade limitation is stated as a real integration limit and left alone, with no impersonation and no new runs. |
| M1: invented authority | **Fixed.** Authority is a btq-posted admind card checked by Handoff's existing ask, approver and digest checks. Self-written attestations are rejected. One task per command is smaller than round 1 allowed, which is fine. |
| M2: status changes unbounded | **Fixed.** Only the two allowed transitions remain. Reopen uses native reopen/update, never create with an existing ID. Overrides never close anything and never set `in_progress` directly. Approval beads can't change status, and the protected labels and pins can't be edited. |
| M3: protected-edge safety | **Fixed, and stricter.** A replacement edge must be added and validated before the old one is removed. Adding an edge relies on bd's native cycle check, and removing one needs no approval from the broken task, so recovery isn't circular. |
| M4: human gates | **Fixed.** Three gates are listed, and reviews and evidence replace none of them. |
| L1: interface too heavy | **Fixed.** One `manage` command with `--plan`/`--post` and `--apply --ask --expect`, reusing Handoff's record. |
| L2: stale citations | **Fixed.** The 400 tests are tied to `61e0852`, and the facade is `harness-improvements`. |

The rest checks out:
- **No impersonation:** the controller keeps its own lane and actor, and no `harness.json` is manufactured.
- **Exact state checks:** status, assignee, labels, metadata, dependency endpoints, worktree/effects and process facts are reread and digested, and stale facts refuse without writing.
- **Native history:** all writes go through native bd under the real actor, plus a request-marked comment.
- **Operator confirmation:** your confirmation that all workers stopped counts as cessation evidence, and no Marmot receipt is invented.
- **Future harnesses:** the queue API stays usable by V2 controllers through btq's `Queue` library.

## Optional notes for the implementation plan

1. **Base branch.** "Reuse the existing integration branch" could mean building `manage` on `btq/ywnix-integrate`. The plan should say whether it starts from master after the merge or stacks on the integration branch. Either is fine, since the master go comes first.
2. **Pin the `unknown` reason.** The digest and card should include the exact `holder_ended` reason, so that a reason changing between plan and apply (say, `process not recorded` becoming `another host`) refuses rather than carrying over approval.
3. **What the evidence file must hold.** For the ywnix bootstrap: who confirmed the stop and when, the holder session and worktree as they actually are, and the reconciled effects. This follows the OPS procedure's existing list, so it adds no new format.

I made no changes, sent no messages, and ran only read-only git log/status, grep and file reads.
