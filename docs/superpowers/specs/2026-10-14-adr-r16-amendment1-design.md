# ADR 0001 r16 Amendment 1: the S9 findings

Base: ADR 0001 revision 16 (design repo `hermes-workstreams-v2`, branch `adr-r16`, text at `93d0e90`). The findings are spike S9's (`docs/spikes/s9-managed-installs.md` on main, PR #80, `a913622`), as plan 4c Task 1 checked them. Plan 4c Amendment 1 (`docs/superpowers/specs/2026-10-14-plan4c-amendment1-s9-design.md`, §6) proposed this text, and its cross-model review (r1–r3, `docs/reviews/p4c-amend1-design-r*.md`) approved it there.

**Status:** draft for review. The form, a standalone amendment on the `adr-r16` line rather than a fold into r17, was decided in ask bpq9 (Liam, 2026-10-14; plan 4c Amendment 1 §7 Q2). The text itself needs its own cross-model review, then operator approval (§5.9). Once approved, it is applied to the ADR on `adr-r16` as one new commit, and plan 4c Task 8 may start.

## 1. Why

ADR r16 §7 says the install's bind is "one non-recursive, private bind", and A.3 item 2 says the same. S9-8 refuted that for the v1 runtime: OpenShell 0.1.2 has no field for a non-recursive bind, and its bind is always `ro,rbind,rprivate`. So a mount below the install that exists when the sandbox is created is carried in, writable. The operator chose to have the Managed install probe refuse any such mount (ask bpq9, Q1 = A). An upstream non-recursive bind field is a follow-up that blocks nothing.

ADR r16 §7 Updates says the launcher runs only the install's own entry point, so nothing an updater writes into a synthetic home is run by the launcher. That stays true, but S9-7 shows the CLI itself runs what it writes: Codex 0.160.0 puts PATH aliases under `$CODEX_HOME/tmp/arg0/` first on its children's `PATH`, and with the default daemon setting it runs a copy of itself from `$CODEX_HOME/packages/`. The plan's fix presents both directories as empty and read-only (ask bpq9, Q3 = E25). The ADR's argument has to say so.

S9-5 and S9-6 only change which switch each adapter sets. The ADR names the updaters by example ("Codex's update check at startup and Claude Code's auto-updater"), so it needs no change for them.

## 2. The edits

Four edits, each quoted against r16's text at `93d0e90`. Text added is tagged "(revision 16, Amendment 1)" in the ADR, as earlier revisions tag theirs.

### 2.1 §7 Managed installs, "How a session sees it"

r16:

> The launcher binds the install's root read-only at its own path, as one non-recursive, private bind. So a mount added under the store on the host, before or after the launch, never appears inside the sandbox.

Replacement:

> The launcher binds the install's root read-only at its own path, as one private bind, so a mount added under the store after the launch never appears inside the sandbox. The backend's bind is recursive (OpenShell 0.1.2), so a mount below the install at launch time is carried in. The Managed install probe refuses the launch when it sees any mount below the install's path, so no session runs with one.

The rest of the paragraph ("The adapter presents the content to the CLI from that read-only tree, in one of two ways:" and its list) is unchanged.

### 2.2 Appendix A.3 item 2

r16:

> The host walk refuses an entry whose mount ID differs from the root's, a same-filesystem bind mount included. The bind is one non-recursive, private mount. The Managed install probe checks from inside that it has no propagation and no mount below it, and that its listing equals the host's.

Replacement:

> The host walk refuses an entry whose mount ID differs from the root's, a same-filesystem bind mount included. The bind is one private mount, read-only at the top. The backend's bind is recursive, so the Managed install probe checks from inside three things: the bind has no propagation, there is no mount below it, and its listing equals the host's. The launch fails otherwise.

The host mount-ID check and the inside listing check stay exactly as r16 has them. "Where it lands: §7; §11." is unchanged.

### 2.3 §7 Updates, a fourth item

r16's list ("No CLI updates itself:") keeps its three items. A fourth is added after "the launcher always runs the install's own entry point by its path, so anything an updater writes into a synthetic home is never run by the launcher.":

> - the CLI may also run files it writes into its own home (Codex 0.160.0's PATH aliases and its app-server daemon copy). The adapter turns those writers off where a switch exists, and the sandbox presents the directories they write to as empty and read-only, so nothing written there is run.

The third item's full stop becomes a semicolon.

### 2.4 §11 Managed installs controls

Two negative controls are added after "an updater's write into a synthetic home, never run by the launcher.":

> - a write into a home directory the CLI runs from, which must fail;
> - a submount made only in the backend's namespace, which must fail the probe.

The previous last item's full stop becomes a semicolon.

## 3. Bookkeeping in the ADR

- **Status line.** It gains "amended by r16 Amendment 1 (2026-10-14; §7, §11 and A.3; see A.4)".
- **Revision 16 changes, item 6 (Updates).** It gains one sentence: "Amendment 1 adds that the directories a CLI runs from in its own home are presented empty and read-only (S9-7)."
- **Appendix A.4**, new:

> ### A.4 Plan 4c S9 findings (r16 Amendment 1)
>
> Plan 4c Task 1 checked spike S9 (`docs/spikes/s9-managed-installs.md` in the product repository). Two findings change this ADR. The operator decided both in ask bpq9 (2026-10-14).
>
> 1. **S9-8, the install bind** (refuted). OpenShell 0.1.2's bind is always recursive, so a mount below the install at launch time is carried in. The Managed install probe refuses any mount below the install's path. Where it lands: §7 Managed installs; A.3 item 2; §11.
> 2. **S9-7, what the CLI runs from its own home** (changed). Codex writes PATH aliases and a daemon copy into its home and runs them. The sandbox presents those directories as empty and read-only, and the adapter turns the writers off where it can. Where it lands: §7 Updates; §11.
>
> S9-5 and S9-6 change only which switch each adapter sets (Codex's `check_for_update_on_startup`, and Claude Code's `DISABLE_AUTOUPDATER` with `DISABLE_UPDATES`). §7 names the updaters by example, so it is unchanged. A non-recursive bind from the backend is a follow-up that blocks nothing.

## 4. Not changed

- §7 Launch self-test, the Managed install probe: it already requires "exactly one mount, read-only and private" with "no mount below it", which is what A needs. The probe's empty-read-only checks for the home directories (plan 4c E25) are plan detail under §7 Updates as amended.
- §4.4 D7, §6.3, §12, §15 and §17, and every other part of r16.
- The existing §11 control "a mount added under the store after the launch, which must not appear inside the sandbox" stays true under a recursive, private bind and is kept.
