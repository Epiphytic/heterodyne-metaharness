I found no critical flaw. My verdict is **APPROVE as a PoC**. The R5 must-fix is properly repaired: all three rendered services (pickup, reconcile and babysitter) now set `HETERODYNE_STATE_DIR=str(Path(manifest.state.path).parent)`. In the spawned processes, `settings.resolve()` adds `/wsd` back, so they find `<root>/wsd`. The babysitter's alert directory, `paths.state_dir(os.environ) / "alerts"` in `cli.py:238`, now lands on the shared `<root>/alerts`. The new test checks both.

This was a read-only static review of the new commit 7ee7637, read against the full range 35176c8..7ee7637. I made no edits and ran no tests or probes. All of the findings below are minor and none is on a live path, because nothing in `src` calls `schedule_units.render` yet. Each one only matters once timers are actually installed (still gated on T10 before P8). The R5 non-critical items I did not repeat; they still stand as already dispositioned.

6 findings, most severe first:

1. `src/heterodyne/wsd/schedule_units.py:47`: The state root is guessed by taking the parent of manifest.state.path, which is a fully resolved path (location() calls resolve(strict=True)), so this gives the wrong root whenever <root>/wsd is itself a symlink.

   Failure scenario: The operator symlinks ~/.local/state/heterodyne/wsd to /data/wsd-store. The manifest stores state.path=/data/wsd-store, so the units get HETERODYNE_STATE_DIR=/data. Each wake then resolves state_dir=/data/wsd, os.open of the manifest raises FileNotFoundError and the wake exits 1, or validate rejects it. The babysitter writes alerts to /data/alerts, which admind never reads. This is the same silent 'every wake fails' outcome as R5 #1, now only for symlinked layouts.

2. `src/heterodyne/wsd/schedule_units.py:47`: The renderer does not check that the state path actually ends in 'wsd' before dropping the last part, so an off-convention manifest renders units that point outside the state root.

   Failure scenario: A manifest written by an older or different installer has state.path=<root> (the unsuffixed root). Rendering quietly emits HETERODYNE_STATE_DIR=<root>/.., and every scheduled run looks in the wrong tree instead of raising ValueError('scheduled unit manifest') at render time, as the other render checks do.

3. `src/heterodyne/wsd/schedule_units.py:47`: Altitude: the 'root + /wsd' rule is now spread across settings.resolve (the hardcoded "wsd" in settings.py), this renderer (.parent) and the test (/ "wsd"), with no shared constant or helper. The manifest stores the derived wsd directory rather than the root the units need.

   Failure scenario: Renaming the wsd subdirectory, or nesting it deeper, in settings.resolve breaks the renderer silently. A better fix is to store the unresolved root in the manifest, or have settings expose a root, and pass that through as wsd/unit.py does with paths.state_dir(env).

4. `src/heterodyne/wsd/schedule_units.py:57`: Simplification: the same str(Path(manifest.state.path).parent) is computed twice in two nearly identical units.render calls, which is how R5's bug ended up in both places.

   Failure scenario: A future fix to the env block has to be applied in two places again. Computing state_root once, or using a small _service(job, template) helper for both calls, removes the copy-paste.

5. `tests/test_plan8_owner_integration.py:106`: The new test only re-applies paths.state_dir to the Environment= values. It never runs settings.resolve() or load_manifest under the rendered environment, and it has no symlinked-state case.

   Failure scenario: Any break between the rendered env and what the wake processes actually resolve and validate would still pass, for example a symlinked <root>/wsd, or validate comparing against a different config_dir string. The test proves the arithmetic, not the end-to-end path that failed in R5.

6. `src/heterodyne/wsd/schedule_units.py:45`: The scheduled units pin canonical (resolved) config and state paths, while the existing wsd unit (wsd/unit.py) pins unresolved paths.state_dir(env), so daemon and timers can see different path strings for the same socket and state.

   Failure scenario: With a symlinked state root, wsd listens on /home/u/.local/state/heterodyne/wsd/ctl.sock while the wakes connect to /real/heterodyne/wsd/ctl.sock. This works because it is the same inode, but anything that compares by string (logs, health paths, alert paths) diverges, and the unit text changes when the symlink target moves, so unit_hash and the rev drift. <!-- install-agnostic: allow=home-path -- Reviewer hypothetical placeholder path, not an installed host path. -->
