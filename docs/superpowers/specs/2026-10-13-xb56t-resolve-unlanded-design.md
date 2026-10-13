# btq-xb56t: `wsctl unlanded` and `wsctl resolve`, an operator path for an unlanded hold

Bead: btq-xb56t. Base: main 850d531. ADR 0001 r15 (28e3df4): D26 (the session's private git, landed fast-forward only) and §9 (operator control over the wsd socket) are unchanged. This adds two operator commands on that socket, and they do what docs/wsd.md §5 tells a human to do by hand today. No amendment is needed.

## Problem

When `sessiongit.land` refuses a session's commits (its tip does not descend from the landed tip, or its branch can't be read), `_end` records the session ENDED with `unlanded=True` and keeps `sessions/<short id>/git`. From then on `sessions(ws)` and `stop()` raise `RuntimeUnavailable`, so the whole workstream is held until a human runs the five manual steps in docs/wsd.md §5:

1. check that nothing runs;
2. fetch from the untrusted directory and review;
3. land fast-forward only, or skip;
4. remove the session directory;
5. tick pickup.

T14's live run hit this, and the common cause is a branch that moved under the session (another session's WIP; btq-jjnqm now refuses that at launch). In that case a fast-forward is impossible, so the manual procedure's only options are to lose the work or to do a by-hand merge outside the procedure.

## Commands

`wsctl unlanded <ws>` is read-only. For each unlanded record of the workstream it shows:

- the short ID, the bead, the generation and the branch `btq/<bead>`;
- the landed tip and the session's tip, as full SHAs;
- `ahead` (session commits not on the branch) and `behind` (branch commits not in the session);
- whether a fast-forward is possible;
- up to 20 of the session's commit subjects, `ahead` first.

The subjects are agent-written. Each one is cut to 72 characters, every character outside printable ASCII becomes `?`, and they are labelled untrusted.

`wsctl resolve <ws> <short id> --tip <sha> (--land | --keep | --discard)` resolves one record.

- **The tip is the confirmation.** `--tip` must equal the session tip that `unlanded` showed. The resolve reads the tip again and refuses if it differs, so the operator acts on exactly what they reviewed.
- **`--land`** runs `sessiongit.land`, the same fast-forward-only import with its checks (trusted alternates, `--strict` index-pack, connectivity, compare-and-swap `update-ref`). It refuses when the branch can't be fast-forwarded and leaves everything as it was.
- **`--keep`** imports the session's objects the same way, but writes them to a new ref, `refs/heads/unlanded/<bead>/<short id>`, with `update-ref <ref> <tip> <zero>` (create only). The bead branch is not touched, so the operator can merge or cherry-pick later, by hand, in their own checkout. If the ref already exists at `tip` (a replay), that counts as done. If it exists at another SHA, the resolve refuses.
- **`--discard`** imports nothing.

Each of the three then removes the whole session directory, and the reply says the hold clears at the next pickup.

## Where it runs

There is a new runtime method, `AgentRuntime.resolve_unlanded(ws, short_id, tip, action) -> Resolution`, with a matching `unlanded(ws) -> list[UnlandedView]`:

- `SandboxRuntime` implements both.
- `NoRuntime` returns `[]` and refuses resolve ("no runtime holds sessions").
- `FakeRuntime` records the calls.

The daemon runs both on the workstream's lane (`self._run(ws, key, ...)`), so they are serialized with pickup, park and reconcile. They are never on the status lane: `unlanded` runs git.

`SandboxRuntime.resolve_unlanded` refuses, each with a fixed reason and nothing changed, unless all of these hold:

1. **The record.** `sessions/<short id>/session.json` exists, its `ws` is `ws`, its phase is ENDED and `unlanded` is set. `short_id` must fully match `hz[0-9a-f]{12}` (the form `short_id()` makes), so it can't name a path.
2. **Nothing runs.** Neither the session's pane (`tmux_name`, `wsd-<short id>`) nor any deadline watcher (`reaper_name`, `wsd-<short id>-r<n>`) is on wsd's tmux server, and the backend lists no sandbox with that short ID. If either listing fails, it is `RuntimeUnavailable`, never taken as absent.
3. **The tip.** `sessiongit._tip(private, f"btq/{bead}")` equals `tip`.
4. The action's own checks, above.

The removal comes last. It is `shutil.rmtree` of the session directory, then `os.path.lexists`. A directory that is still there is reported as `failed`, and the hold stays: the record is still there and still `unlanded`.

**Crash safety.** No new state is needed, because each step can be replayed:

- A crash after `--land`'s `update-ref`, before the removal: the replay's `land` sees the tip already landed (returns None) and goes on to remove.
- A crash after `--keep`'s ref was created: the replay finds the ref at `tip` and goes on.
- A crash during `rmtree`: the record may be gone while `git` is left, and a private git directory without a record refuses the next launch (§5). The replay therefore accepts a session directory that has no record if, and only if, the operator passes the same short ID: it re-checks condition 2 and finishes the removal. With nothing to check the tip against, it imports nothing.

**Journal.** After a resolve succeeds, the daemon emits `unlanded_resolved` for the workstream and bead, with `{"session": short_id, "action": action, "tip": tip}` (and the kept ref for `--keep`). No path appears in it. A refused or failed resolve emits nothing; its reply says why.

## Wire changes

- `CtlRequest.op` gains `"unlanded"` and `"resolve"`.
- New optional fields: `session: str | None`, `tip: str | None` and `action: Literal["land", "keep", "discard"] | None`.
- `refusal()` checks the combinations: both ops need `ws`; resolve needs all three new fields; nothing else takes them.
- `wsctl_main` gets the two subparsers. `--land`, `--keep` and `--discard` are a required mutually exclusive group.
- The views come back in `CtlReply.data`, keyed by short ID, as strings.

The hold's detail text (`_unlanded`) changes from "land them … by hand (docs/wsd.md §5)" to "see `wsctl unlanded <ws>` (docs/wsd.md §5)".

## Tests (offline)

`tests/test_sandbox_runtime.py`, using the existing git fixtures:

1. `test_unlanded_shows_ahead_behind_and_untrusted_subjects`: covers a diverged session (behind 1) and a fast-forwardable one. A subject with an escape sequence and one over 72 characters are shown sanitized.
2. `test_resolve_land_fast_forwards_and_removes`, and `test_resolve_land_refuses_a_diverged_branch` (the branch and the session directory are unchanged).
3. `test_resolve_keep_writes_a_side_ref_and_leaves_the_branch`. A replay with the ref already at `tip` succeeds. The ref at another SHA refuses.
4. `test_resolve_discard_removes_without_importing`: the repository gains no objects from the session.
5. `test_resolve_refuses_a_moved_tip`: the session tip is changed after the view, and the resolve refuses with nothing changed.
6. `test_resolve_refuses_while_anything_runs`, parameterized: a pane listed, a sandbox listed, the record not ENDED, the record not unlanded, another workstream's record, a short ID that isn't one. Failed listings give `RuntimeUnavailable`.
7. `test_resolve_replays_after_a_crash`, parameterized: after `update-ref` (land), after the ref was made (keep), and after the record went but `git` stayed. Each replay completes, and the branch holds one landing.
8. `test_the_hold_clears_after_resolve`: `sessions(ws)` raises before the resolve and lists sessions after it.

`tests/test_wsd_daemon.py` (where `CtlRequest` and `refusal` are tested today):

- the `refusal()` combinations;
- the wsctl argument group;
- the daemon runs both ops on the workstream lane, not the status lane (a pickup holding the lane delays the resolve);
- `unlanded_resolved` is journaled once on success and not at all on a refusal.

## Docs

docs/wsd.md §5 "Unlanded commits": the five manual steps become the two commands, with what each action does. The manual steps stay as the fallback when wsd is not running. docs/configuration.md is unchanged.

## Open questions for Liam

1. **Offer `--keep`?** I recommend yes. It is the only option that keeps the work when the branch moved, which is T14's case, without a human doing git by hand in wsd's state directory. The ref namespace `unlanded/` is new.
2. **Tick pickup after a resolve, or leave it to the next pickup?** I recommend leaving it: the resolve stays one action, and the reply says how to tick.
3. **Fallback without wsd.** I recommend keeping the manual steps documented, and no offline command: an offline resolve would need wsd's instance lock to be safe, and that is a second path to test.
