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
4. **Check the copy.** Each copied pack is re-indexed with `index-pack --strict` in `dest`. Each loose object is checked with `cat-file -t` and must then pass `fsck --strict --connectivity-only` from the tip. Any failure makes the snapshot `tip=None` with a fixed reason. Nothing in `dest` is trusted until this step has passed.

The private directory is only read, never changed. The existing `land` rewrites `objects/info/alternates` there (`_trusted_alternates`), so it is replaced by `land_snapshot(p, snap, tip)`. That runs the same fast-forward check, `pack-objects` from `dest` into the repository, the connectivity check, and the compare-and-swap `update-ref`, with the given `tip` all through, never a second read. `_end`'s automatic land moves to snapshot-then-land too, so there is a single import path. Its tests are kept.

The snapshot is taken only once nothing runs (below). `dest` is removed when the command ends, and any left there is cleared at wsd's start, under the instance lock.

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
- **`--land`:** `land_snapshot`, fast-forward only. If the branch can't be fast-forwarded, it refuses and changes nothing.
- **`--keep`:** imports the snapshot to `refs/heads/unlanded/<bead>/<short id>-g<generation>` with `update-ref <ref> <tip> <zero>` (create only). The bead branch is not touched.
  - A ref already at `tip` is a replay and counts as done.
  - A ref at another SHA, or a name conflict (an existing `unlanded` or `unlanded/<bead>` ref), refuses with the fixed reason "the keep ref can't be created".
- **`--discard`:** imports nothing.

## Durable intent, removal and the journal

Every resolve that passes its checks first writes an intent to `<state>/resolutions/<short id>-g<generation>.json` (0600, atomic write and fsync). It is outside the session directory, wsd-owned and never bound into a sandbox. The intent holds:

- `ws`, `bead`, `key`, `generation`;
- `tip` (or null), `action` and `ref` (for keep);
- `step`, one of `confirmed`, `imported` or `moved`.

The steps, each recorded before the next one starts:

1. `confirmed`: the intent is written.
2. The action runs. Land and keep are idempotent, as above. Then `step=imported`.
3. The session directory is renamed, in one `rename`, to `<state>/resolutions/<short id>-g<generation>.trash`. That path is on the same filesystem and is not under `sessions/`, so `_records` stops seeing the record at once, and never sees half of it. Then `step=moved`.
4. The trash is removed with `rmtree` and checked with `lexists`.
5. `journal.emit_once(ws, bead, "unlanded_resolved", detail, ref=<short id>-g<generation>)`. This is a new Journal method: inside one transaction it inserts the event only if no event of that kind and `ref` exists, so a replay never writes it twice. The detail is `action=<action> tip=<tip or none>` (plus `ref=` for keep), with no paths.
6. The intent file is removed.

**The hold.** `sessions(ws)` and `stop()` also raise `RuntimeUnavailable` while any intent for `ws` exists: "an unlanded resolve of session <short id> is unfinished; run `wsctl resolve` for it again". The workstream stays held from the first write until the intent is gone. It is never released while the trash or the journal entry is still owed.

**A replay.** `wsctl resolve` for the same short ID and generation, while an intent exists:

- It must name the same action and tip as the intent; otherwise it refuses.
- It continues from the recorded `step`, so finished steps are skipped:
  - an intent at `moved` removes the trash and emits once;
  - an intent at `confirmed` or `imported` still has the record.
- A missing trash at `moved` is fine.

A session directory without a record and without an intent is not resolvable by this command. It is refused ("the session has no record; see docs/wsd.md §5"). An unrelated orphan from a launch therefore can never be deleted through it.

A failure leaves the intent at its last step, with nothing beyond it done, and the reply says `failed` and why.

## Nothing runs, and serialization

The snapshot and every resolve step need all of these:

- **The record:** `sessions/<short id>/session.json` is ENDED and `unlanded`, its `ws` is the request's, and its generation is `--generation`. Before step 3 the record must exist; `short_id` must fully match `hz[0-9a-f]{12}`.
- **No pane:** neither `wsd-<short id>` (`tmux_name`) nor any `wsd-<short id>-r<n>` (`reaper_name`, of any generation) is on wsd's tmux server.
- **No sandbox:** the backend lists no sandbox `<short id>g<n>` for any `n`.

A listing that fails is `RuntimeUnavailable`, never taken as absent.

The daemon runs both commands as jobs on the workstream's lane, so they are serialized with pickup, park and reconcile, and the intent, the action and the journal write all happen inside that job. The lane keys are:

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

`AgentRuntime` gains `unlanded(ws) -> list[UnlandedView]` and `resolve_unlanded(ws, short_id, generation, action, tip) -> Resolution`. `NoRuntime` returns `[]` and refuses with "no runtime holds sessions"; `FakeRuntime` records calls. The intent and the trash belong to `SandboxRuntime`. The journal call is made by the daemon's job with the identity `Resolution` returns, and `emit_once` makes a replay safe.

## Tests (offline)

**`tests/test_session_git.py`** (snapshot and land_snapshot; the existing land tests move to land_snapshot):

1. `test_snapshot_ignores_private_config_hooks_and_alternates`: a private dir has a malicious `config` (`core.fsmonitor`, `core.hooksPath`), hooks, and `info/alternates` pointing at another repository holding the tip's objects. The snapshot doesn't run any of them, and without the objects present itself it fails with a fixed reason.
2. `test_snapshot_refuses_links_special_files_and_hard_links`, parameterized: a symlinked object dir, a symlinked pack, a FIFO, a hard-linked pack (nlink 2), an oversized object. Each gives a fixed reason, and nothing outside `dest` is read.
3. `test_snapshot_refuses_malformed_objects`: a corrupt loose object, a corrupt pack, a missing parent.
4. `test_snapshot_leaves_the_private_dir_unchanged`: a tree digest of the private dir is the same before and after.
5. `test_land_snapshot_uses_the_given_tip`: the private ref is changed after the snapshot, and the landed tip is the snapshot's.

**`tests/test_sandbox_runtime.py`**:

6. `test_unlanded_view`: covers diverged, fast-forwardable and unreadable tips (a missing ref, a malformed `packed-refs`, a tip whose objects are gone), plus subjects with invalid UTF-8, newlines, escape sequences and over 72 characters. One failing record doesn't hide another.
7. `test_resolve_land`, `test_resolve_land_refuses_a_diverged_branch` (everything unchanged), `test_resolve_keep` (side ref; a replay at the same tip; a ref at another SHA, and an `unlanded` prefix conflict, both refuse with the fixed reason), and `test_resolve_discard`. Discard covers an unreadable tip without `--tip`, and a readable tip refused without `--tip`.
8. `test_resolve_refuses`, parameterized:
   - a moved tip, a wrong generation, another workstream, a non-ENDED record, a non-unlanded record, a bad short ID;
   - a pane, a reaper of an older generation, a sandbox of an older generation;
   - failed tmux or backend listings, which raise `RuntimeUnavailable`;
   - a recordless orphan with no intent.
9. `test_resolve_replays_from_each_step`, parameterized over a crash after each of steps 1-5: the hold stays (`sessions(ws)` raises) until the replay finishes, the event is written once, and the branch has one landing. A replay with a different action or tip refuses.
10. `test_the_hold_clears_after_resolve`.

**`tests/test_wsd_daemon.py`**:

- `refusal()` combinations and the wsctl argument groups;
- both ops run on the workstream lane (a pickup holding it delays them);
- two different resolves queued behind a pickup each get their own result;
- a disconnected caller's resolve still completes;
- `emit_once` writes one event across a replay, and a journal write that fails leaves the intent at `moved` with the reply `failed`.

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
