# btq-xb56t: `wsctl unlanded` and `wsctl resolve`, an operator path for an unlanded hold

Bead: btq-xb56t. Base: main 850d531. ADR 0001 r15 (28e3df4) is unchanged. Two operator commands are added on wsd's control socket (§4.3, §16: wsctl is wsd's operator interface), and they do what docs/wsd.md §5 tells a human to do by hand today. The rules they follow are plan 4's D26 (Task 7A; plan 4 is approved under btq-9r2w3):

- the sandbox never sees anything host git trusts;
- only the bead branch's tip is imported (`index-pack --strict`, compare-and-swap);
- the bead branch moves fast-forward only.

Nothing here relaxes them, so no ADR or plan amendment is needed. The new `refs/heads/unlanded/` namespace is a recovery contract, recorded in docs/wsd.md §5.

## Problem

When `sessiongit.land` refuses a session's commits, `_end` records the session ENDED with `unlanded=True` and keeps `sessions/<short id>/git`. The land is refused when:

- the session's tip doesn't descend from the landed tip;
- its branch or objects can't be read;
- the import fails.

From then on `sessions(ws)` and `stop()` raise `RuntimeUnavailable`, so the whole workstream is held until a human runs the five manual steps in §5. T14 hit this case: the branch moved under the session. A fast-forward is then impossible, and the manual procedure can only throw the work away.

## The untrusted directory: one inspection boundary

The agent could write to the private git directory, so it is never used as a git directory, an alternate or a config source. Its objects are snapshotted into wsd-owned storage, and every later step reads only the snapshot. That covers the view, `--land` and `--keep`. The new function is `sessiongit.snapshot(p: Pinned, private: Path, dest: Path) -> Snapshot`:

1. **Make the store.** `dest` is a fresh directory under `<state>/inspect/` (0700, wsd-owned, outside every sandbox bind), set up as a bare repository by pinned host git, with no hooks and `core.hooksPath=/dev/null`. The bead repository's object store is its only alternate, written by wsd.
2. **Read the tip.** It is the session's `refs/heads/btq/<bead>`, read with the existing `_tip` rules: no link below `refs`, at most `PACKED_LIMIT` bytes, a full SHA. If the tip can't be read, the snapshot is `Snapshot(tip=None, error=<fixed reason>)`.
3. **Copy the objects.** Only these are copied from `private/objects`:
   - `objects/??/<38 hex>` loose objects;
   - `objects/pack/pack-*.pack` packs.
   
   Each is opened with `O_NOFOLLOW | O_NONBLOCK`, through a directory fd walk that refuses links (the `_plain_tree` and `no_link` checks). It must be a regular file with one link (`st_nlink == 1`) and under a size cap, and it is copied by fd. Indexes, `info/alternates`, `info/packs`, config, hooks and every other file are ignored.
4. **Check the copy** (Codex xb56t r2). Each copied pack is re-indexed with `index-pack --strict` in `dest`. Then `fsck --strict --full --no-dangling` runs over `dest`'s own objects. It re-hashes every loose object against its name and checks every object's content, so an object stored under the wrong name, or a malformed one, fails. The tip must be a commit (`cat-file -t` is `commit`), and `rev-list --objects <tip>` must be complete from `dest` plus the bead repository's objects. Any failure makes the snapshot `tip=None` with a fixed reason. Nothing in `dest` is read, for counts or subjects or anything else, until this step has passed.

The private directory is only read, never changed. The operator commands therefore don't use the existing `land`, which rewrites `objects/info/alternates` there (`_trusted_alternates`). They use the new `import_snapshot(p, snap, tip, ref, old)`, which both `--land` and `--keep` call:

- `pack-objects` of `tip --not --all` from `dest`;
- `index-pack --strict --fix-thin` into the repository;
- the connectivity check;
- the compare-and-swap `update-ref ref tip old`.

It uses the given `tip` throughout and never reads it twice. `--land` passes `refs/heads/btq/<bead>` with the landed tip as `old`, after the fast-forward check. `--keep` passes the keep ref with the zero SHA as `old`.

`_end`'s automatic land is unchanged (Codex xb56t r2): it keeps its own preconditions, a durably STOPPING record and a confirmed teardown, and its tests.

The snapshot itself has no record precondition. Its callers must first confirm that nothing can write the private directory: the operator commands do so under the conditions below. `dest` is removed when the command ends, and any left there is cleared at wsd's start, under the instance lock.

## Commands

`wsctl unlanded <ws>` lists each unlanded record of the workstream:

- **Identity:** short ID, generation, bead and branch.
- **Running:** if anything still runs for it, it shows `running` and takes no snapshot.
- **Tips:** the landed tip and the session's tip as full SHAs, or `tip: unreadable (<fixed reason>)`.
- **Counts:** `ahead` and `behind`, and whether a fast-forward is possible.
- **Subjects:** up to 20 commit subjects from the snapshot, labelled untrusted. Each is decoded as UTF-8 with replacement; every character outside printable ASCII (newlines and escapes included) becomes `?`, and it is cut to 72 characters.

A failure on one record is shown on that record, and the others are still listed.

`wsctl resolve <ws> <short id> --generation <n> (--land --tip <sha> | --keep --tip <sha> | --discard [--tip <sha>])`:

- **`--generation` binds the identity:** it must equal the record's generation.
- **`--tip` is the confirmation:** it must equal the tip the snapshot reads at resolve time. `--discard` takes no `--tip` only when the tip is unreadable. If the tip is readable, `--discard` needs `--tip`, so an operator can't discard something they haven't seen.
- **`--land`:** `import_snapshot` to the bead branch, fast-forward only. If the branch can't be fast-forwarded, it refuses and changes nothing.
- **`--keep`:** imports the snapshot to `refs/heads/unlanded/<bead>/<short id>-g<generation>` with `update-ref <ref> <tip> <zero>` (create only). The bead branch is not touched.
  - A ref already at `tip` is a replay and counts as done.
  - A ref at another SHA, or a name conflict (an existing `unlanded` or `unlanded/<bead>` ref), refuses with the fixed reason "the keep ref can't be created".
- **`--discard`:** imports nothing.

## Durable intent, removal and the journal

Every resolve that passes its checks first writes an intent to `<state>/resolutions/<short id>-g<generation>.json` (0600, atomic write and fsync). It is outside the session directory, wsd-owned and never bound into a sandbox. The intent holds:

- `ws`, `bead`, `key`, `generation`;
- `tip` (or null), `action` and `ref` (for keep);
- `step`, one of `confirmed`, `imported`, `moved` or `removed`;
- `dir_id`, the session directory's `(st_dev, st_ino)` as it was when the intent was first written.

Every intent write is a temporary file, an fsync, a rename over the intent and an fsync of `resolutions/`.

The steps, each recorded before the next one starts:

1. `confirmed`: the intent is written.
2. The action runs. Land and keep are idempotent, as above. Then `step=imported`.
3. The session directory is renamed to `<state>/resolutions/<short id>-g<generation>.trash` in one `rename`, and both `sessions/` and `resolutions/` are fsynced. The path is on the same filesystem and is not under `sessions/`, so `_records` stops seeing the record at once, and never sees half of it. Then `step=moved`.
4. The trash is removed with `rmtree`, checked with `lexists`, and `resolutions/` is fsynced. Then `step=removed`. `resolve_unlanded` returns `Resolution(ws, bead, short_id, generation, action, tip, ref)`, read from the intent, and the intent stays.
5. The daemon's job calls `journal.emit_once(ws, bead, "unlanded_resolved", detail, ref=<short id>-g<generation>)`. This new Journal method inserts the event in one transaction, unless an event of that kind with that `ref` already exists, so a replay never writes it twice. The detail is `action=<action> tip=<tip or none>` (plus `ref=` for keep), with no paths. The values come from the `Resolution`, so from the intent.
6. Once that has committed, the job calls `runtime.finish_resolution(ws, short_id, generation)`. It removes the intent, which must be at `removed`, then fsyncs `resolutions/`. If the journal write fails, the intent stays at `removed` and the reply is `failed`. The hold stays until a retry finishes steps 5 and 6.

**The hold.** `sessions(ws)` and `stop()` also raise `RuntimeUnavailable` while any intent for `ws` exists: "an unlanded resolve of session <short id> is unfinished; run `wsctl resolve` for it again". The workstream stays held from the first write until the intent is gone. It is never released while the trash or the journal entry is still owed.

**A replay.** `wsctl resolve` for the same short ID and generation, while an intent exists:

- **Identity first.** Before anything is read or changed, the whole intent identity must match the request: `ws`, short ID, generation, action and tip. A request naming another workstream is refused, so cleanup can only run on the lane of the workstream that owns the intent. From then on the intent, not the request, supplies every value: the journal's, the hold's and the trash's.
- **Reconciled, not trusted.** The step is checked against the filesystem, since a crash can come between a side effect and its checkpoint (`S` is the session directory, `T` the trash):

  | intent step | `S` | `T` | then |
  |---|---|---|---|
  | `confirmed` / `imported` | present, with `dir_id` | absent | redo from the action (idempotent) |
  | `imported` | absent | present, with `dir_id` | the rename happened: go on from step 3's checkpoint |
  | `moved` | absent | present or absent | go on from step 4 (a missing `T` means it was already removed) |
  | `removed` | absent | absent | return the `Resolution` (steps 5 and 6 again) |
  | any other combination | | | refused, nothing changed: "the resolve's state is inconsistent; see docs/wsd.md §5" |

  A `T` or `S` whose `(st_dev, st_ino)` is not `dir_id` is "another combination".

A session directory without a record and without an intent is not resolvable by this command. It is refused ("the session has no record; see docs/wsd.md §5"). An unrelated orphan from a launch therefore can never be deleted through it.

A failure leaves the intent at its last checkpoint, and the reply says `failed` and why. The table decides how a replay goes on from it.

## Nothing runs, and serialization

`wsctl unlanded`, and a resolve before its intent's step 3, need all of these. After step 3, the intent's identity takes the record's place, and the pane and sandbox checks still apply:

- **The record:** `sessions/<short id>/session.json` is ENDED and `unlanded`, its `ws` is the request's, and its generation is `--generation`. Before step 3 the record must exist; `short_id` must fully match `hz[0-9a-f]{12}`.
- **No pane:** neither `wsd-<short id>` (`tmux_name`) nor any `wsd-<short id>-r<n>` (`reaper_name`, of any generation) is on wsd's tmux server.
- **No sandbox:** the backend lists no sandbox `<short id>g<n>` for any `n`.

A listing that fails is `RuntimeUnavailable`, never taken as absent.

The daemon runs both commands as jobs on the workstream's lane, so they are serialized with pickup, park and reconcile. Each job also holds the workstream's `Parker.entry()` lock, the operation lock that pickup holds through a launch, for its whole length: validation, snapshot, every step, `emit_once` and `finish_resolution`. The lane keys are:

- `("unlanded", ws)` for the view;
- `("resolve", short_id, generation, action, tip)` for a resolve.

Two different resolves therefore never coalesce, and an identical retry joins the one already queued. If the caller disconnects, the job still runs to its end, and a later `wsctl resolve` replays from the intent.

## Wire changes

- `CtlRequest.op` gains `"unlanded"` and `"resolve"`.
- Optional new fields: `session`, `generation: int`, `tip` and `action: Literal["land", "keep", "discard"]`.
- `refusal()`:
  - both new ops need `ws`;
  - resolve needs `session`, `generation` and `action`, plus `tip` for land and keep;
  - nothing else takes these fields.
- `wsctl_main` gets two subparsers, with a required exclusive group for the actions.
- The views come back in `CtlReply.data`, keyed by short ID, all as strings.
- `_unlanded`'s hold detail now points at `wsctl unlanded <ws>` (docs/wsd.md §5). Refusal texts are fixed, and no path appears in any of them.

`AgentRuntime` gains `unlanded(ws) -> list[UnlandedView]` and `resolve_unlanded(ws, short_id, generation, action, tip) -> Resolution`. `NoRuntime` returns `[]` and refuses with "no runtime holds sessions"; `FakeRuntime` records calls. It also gains `finish_resolution(ws, short_id, generation) -> None`. The intent and the trash belong to `SandboxRuntime`, and the journal belongs to the daemon's job. The hand-off between them is step 4's `Resolution` and step 6's `finish_resolution`.

## Tests (offline)

**`tests/test_session_git.py`** (snapshot and import_snapshot; the existing land tests stay as they are):

1. `test_snapshot_ignores_private_config_hooks_and_alternates`: a private dir has a malicious `config` (`core.fsmonitor`, `core.hooksPath`), hooks, and `info/alternates` pointing at another repository holding the tip's objects. The snapshot doesn't run any of them, and without the objects present itself it fails with a fixed reason.
2. `test_snapshot_refuses_links_special_files_and_hard_links`, parameterized: a symlinked object dir, a symlinked pack, a FIFO, a hard-linked pack (nlink 2), an oversized object. Each gives a fixed reason, and nothing outside `dest` is read.
3. `test_snapshot_refuses_malformed_objects`: a corrupt loose object, a corrupt pack, a missing parent, a valid object stored under the wrong hash, a malformed blob, and a tip that is a tree or a blob, not a commit. The view reports none of their counts or subjects.
4. `test_snapshot_leaves_the_private_dir_unchanged`: a tree digest of the private dir is the same before and after.
5. `test_import_snapshot_uses_the_given_tip`: the private ref is changed after the snapshot, and the landed tip is the snapshot's. The same for a keep ref.

**`tests/test_sandbox_runtime.py`**:

6. `test_unlanded_view`: covers diverged, fast-forwardable and unreadable tips (a missing ref, a malformed `packed-refs`, a tip whose objects are gone), plus subjects with invalid UTF-8, newlines, escape sequences and over 72 characters. One failing record doesn't hide another.
7. `test_resolve_land`, `test_resolve_land_refuses_a_diverged_branch` (everything unchanged), `test_resolve_keep` (side ref; a replay at the same tip; a ref at another SHA, and an `unlanded` prefix conflict, both refuse with the fixed reason), and `test_resolve_discard`. Discard covers an unreadable tip without `--tip`, and a readable tip refused without `--tip`.
8. `test_resolve_refuses`, parameterized:
   - a moved tip, a wrong generation, another workstream, a non-ENDED record, a non-unlanded record, a bad short ID;
   - a pane, a reaper of an older generation, a sandbox of an older generation;
   - failed tmux or backend listings, which raise `RuntimeUnavailable`;
   - a recordless orphan with no intent.
9. `test_resolve_replays_from_each_step`, parameterized over a crash both after each side effect and after its checkpoint (the import, the rename, the rmtree, and their step writes). The hold stays (`sessions(ws)` raises) until the replay finishes, the event is written once, and the branch has one landing.
10. `test_resolve_replay_refuses_inconsistent_state`: both `S` and `T` present; `confirmed` with `S` absent; a `T` or `S` with another `dir_id`. Each refuses with nothing changed.
11. `test_resolve_replay_binds_the_intent_identity`, at every checkpoint: a retry naming another workstream, action, tip or generation refuses before anything is read or changed.
12. `test_the_hold_clears_after_resolve`: only after `finish_resolution`.

**`tests/test_wsd_daemon.py`**:

- `refusal()` combinations and the wsctl argument groups;
- both ops run on the workstream lane (a pickup holding it delays them);
- two different resolves queued behind a pickup each get their own result;
- a disconnected caller's resolve still completes;
- `emit_once` writes one event across a replay;
- a journal write that fails leaves the intent at `removed` with the reply `failed`, and the hold stays;
- a crash before `emit_once`, and one after its commit but before `finish_resolution`: the retry writes no second event and clears the hold;
- a resolve waits for a concurrent operation holding `Parker.entry()`.

## Docs

docs/wsd.md §5 "Unlanded commits" changes:

- the two commands and the three actions replace the five manual steps;
- the `unlanded/` namespace is reserved;
- the hold lasts until a resolve's intent is gone;
- the manual steps stay as the fallback when wsd is not running.

## Open questions for Liam

1. **Offer `--keep`?** I recommend yes: it is the only option that keeps the work when the branch moved, without a human doing git in wsd's state directory.
2. **Tick pickup after a resolve, or leave it to the next pickup?** I recommend leaving it: the reply says how to tick.
3. **An offline fallback?** I recommend none. The manual steps stay documented, and an offline resolve would need wsd's instance lock, which is a second path to test.
