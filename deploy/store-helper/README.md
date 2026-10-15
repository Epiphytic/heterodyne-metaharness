# The store helper's host setup (plan 4c Task 12)

The managed-install store (`/var/lib/heterodyne/harnesses`) belongs to the system user `heterodyne-store`, so
the service user who runs `wsd` can read installs but never write them. Every change goes through the store
helper (`store_core.py`), which systemd starts as `heterodyne-store` for each connection to its socket.
`wsd` connects as the service user, and the helper checks that peer by uid (`SO_PEERCRED`).

| File | What it is |
| --- | --- |
| `heterodyne-store-helper.socket` | `/run/heterodyne-store/helper.sock`, 0660, owned by `heterodyne-store`; `Accept=yes`, so each connection gets its own helper |
| `heterodyne-store-helper@.service` | the helper for one connection, which arrives on stdin, run as `heterodyne-store` under a strict sandbox |
| `setup-store.sh` | creates the user, the store, the helper copy, the helper's config and the socket's group drop-in (step 3), from a staged tree |
| `manifest.sh` | prints the sums the execution approval pins: the archive, `deploy/install.sh`, and each file in the tree (unprivileged) |
| `../install.sh` | the whole install in one root script: the store helper (steps 3 to 6), and the platform's release and user units (design btq-v7sgt) |
| `../uninstall.sh` | the platform's user units, then the store helper's rollback, as one fail-stop root script |
| `../platform_state.py` | the installer's decisions about the user units (the install record, the identity checks, the uninstall's classification), stdlib only, run as the service user |

`tests/test_deploy_store_helper.py` checks all of this offline:
- the units pass `systemd-analyze verify` with no output;
- their keys match the plan and the paths the harness reads (`[harnesses]` in `defaults.toml`, `settings.STORE`, `store_core.CONFIG`);
- the units' command line serves a request;
- the scripts pass `bash -n`, and `install.sh` and `manifest.sh` name the same paths;
- `setup-store.sh`'s dry run lists every change and makes none, and each refusal is checked;
- `install.sh` runs as a non-root user against a fake root, with shims for the host's commands: each refusal, the commit pinning, the socket reconciliation, each verity outcome, the config check and the dry run;
- the pinned install and uninstall blocks below run as written, and run nothing from a changed script;
- the rollback runs as written, against stubs.

`tests/test_deploy_platform.py` checks the platform's part against the same fake root, with a fake user manager (`tests/fake_user_manager.py`): the preflight, the release build, the services and their reconciliation, adoption and each failure-then-rerun boundary, the dry run, and the uninstall.

## Departures from the plan's unit text

- **`ExecStart` passes `--config /etc/heterodyne/store-helper.json`, not `serve`.** `store_core.main` takes only `--config`; a `serve` argument would fail argument parsing on every connection. The path is the helper's own default, given explicitly so that the unit states it.
- **`SocketGroup=` is in a drop-in, not in the unit.** The group is the service user's, so it differs per host. `setup-store.sh` writes `/etc/systemd/system/heterodyne-store-helper.socket.d/10-group.conf` (`[Socket]` `SocketGroup=<group>`), and the unit files stay as committed, byte for byte.


## Where root's trust comes from

The checkout belongs to the service user, who can change any file in it, or what git reports for a commit (a `refs/replace/` ref), after any check. So root never runs, reads or installs anything from the checkout except through a sum the approval names. `install.sh`:
1. resolves the commit once, as the service user, with replacement refs ignored (`GIT_NO_REPLACE_OBJECTS=1`, no git config, no hooks), and names that full ID from then on, never `HEAD`;
2. archives it the same way into `/run/heterodyne-install.XXXXXX`, a directory only root can enter;
3. checks the archive against `--expect-sha256` **before** extracting it;
4. extracts it, and runs the stage's copy of itself, which does steps 3 to 6 from the stage alone.

The first stage, `install.sh` itself, is read from the checkout. In the pinned form root copies it into a root-only file in `/run` and checks that copy's sum before running it, so the code that does the archive compare is itself verified. The sums come from `manifest.sh`, which the lead runs on the merged commit, unprivileged, and pastes into the approval. It builds the archive the same way, so the sums hold only for the commit's own bytes.

Nothing runs from the checkout's work tree. The platform runs from a release: the commit's whole tree, archived by the service user with the same hardened git, so its bytes are the commit's even if the work tree changes or a `refs/replace/` ref is planted. Its runtime packages match `uv.lock`'s hashes. Root never runs the release, so root doesn't verify it. Step 6, `heterodyne config check`, runs from the release, as the service user, not root. It can lie about the result, but it gains no privilege. The platform runs as the service user, from a directory the service user owns, so the installer claims no integrity for it against the service user or the agents that share that uid.

## Preview (no root, changes nothing)

Run this as the service user, from a checkout that has the pinned commit:

```bash
stage=$(mktemp -d)
git archive <pinned commit> -- deploy src/heterodyne/harness/resources/store_core.py | tar -x -C "$stage"
bash "$stage/deploy/store-helper/setup-store.sh" --dry-run --staged "$stage" --service-user "$(id -un)"
rm -rf "$stage"
```

The dry run runs every check. It prints `ok: <what>` for what is already in place and `would: <command>` for each change, ending with `done`. A failed check prints `refused: <check>` on stderr and exits 1. Usage errors exit 2. A dry run by any other user refuses, because only the service user can answer the "writable by the service user" check.

`sudo bash deploy/install.sh --dry-run` previews the whole install the same way: it runs every check, the offline scratch build included, and prints `would:` for each change, without making any. It changes nothing outside the scratch directory, which it removes.

## Prerequisites

1. **Lingering is on for the service user.** The installer never changes it. With it off, or unreadable, the preflight refuses, `--dry-run` included, with the command to run:

   ```bash
   sudo loginctl enable-linger <service user>
   ```

   With lingering on, the service user's systemd manager runs without a login. The preflight requires its bus, `/run/user/<uid>/bus`, to be a socket the service user owns, and `systemctl --user is-system-running` to report `running` or `degraded`. It never starts, stops or restarts the manager.
2. **`uv` is on the service user's login PATH.**
3. **The uv cache holds every package the release needs**, or the run has `--fetch`. Without `--fetch` the build is offline, and the preflight proves the cache is enough before any change (see Release, below).
4. **For admind to start: `admind init` has run** ([docs/admind.md](../../docs/admind.md) §2). Until it has, the installer writes admind's unit but neither enables nor starts it, and the exit is 3.

## Running the installer

On a trusted checkout (one only root or the operator can write, such as a fresh clone):

```bash
sudo bash deploy/install.sh [--dry-run] [--no-verity] [--fetch] [--adopt] [--no-wsd] [--service-user <service user>]
```

- `--dry-run`: run every check, and print `would:` for each change instead of making it.
- `--no-verity`: leave `/var/lib`'s filesystem as it is (`skipped (--no-verity)`, exit 0).
- `--fetch`: let `uv` use the network for the release build. It is still `--locked` and hash-checked. It skips the preflight's offline scratch build.
- `--adopt`: replace a hand-made `heterodyne-admind.service` or `heterodyne-wsd.service`, keeping a backup (see Adoption, below).
- `--no-wsd`: write no wsd unit, and stop and disable a managed one, keeping its file (`skipped (--no-wsd)`).

On a checkout the service user can change (this host's), the installer refuses that form, and the execution approval names this one instead. It fills in `<checkout>`, `<commit>` and `<service user>`, and the two sums from `manifest.sh`'s output for `<commit>` (its `deploy/install.sh` line and its archive line):

```bash install-pinned
sudo bash -euo pipefail -c 'b=$(mktemp /run/hz-install-boot.XXXXXX); trap "rm -f $b" EXIT
cat <checkout>/deploy/install.sh >"$b"
echo "<install.sh sum>  $b" | sha256sum --check --strict --quiet
bash "$b" --checkout <checkout> --commit <commit> --expect-sha256 <archive sum> --service-user <service user>'
```

The block stays as it is. The approval spells out any of `--adopt`, `--no-wsd` and `--fetch` after `--service-user <service user>`, on the same `bash "$b" …` line.

- `mktemp` creates the copy as root, mode 0600, in `/run`, which the service user can't write. After the copy, edits to the checkout can't touch it, and a copy whose sum isn't the approval's runs nothing.
- The copy is outside the checkout, so it is given `--checkout`.

It prints each step as it goes, then a summary: one line per step (`store`, `units`, `release`, `config`, `verity`, `services`), each `ok`, `changed`, `would change` (a dry run), `skipped (<why>)`, `failed (<why>)`, `not run`, or (a dry run's config check) `advisory (<why>; ...)`, then the commit, the release and the archive's sha256. Exit 0: everything is in place. 3: everything is in place except a step skipped for a reason only the host or the operator can resolve: verity, which the host doesn't support (`skipped (<why>)`), or admind, whose `admind init` hasn't run (`skipped (admind init has not run)`). 1: a refusal (`refused: <check>`, before any change) or a failure. 2: a usage error. Re-running it is always safe: what is in place is `ok`, and nothing is restarted unless its live check differs.

## What the installer does

1. **Preflight**, with no change: root; the service user (`--service-user`, or else `$SUDO_USER`; not root, and existing); the tools; `/usr/bin/python3` 3.12 or later; the pinned form on a checkout the service user can change; the host config (below); lingering, the user bus and the user manager (Prerequisites); `uv`; the verity probe (below); the commit, resolved once; the offline scratch build (Release, below); each user unit's file and the install record (Adoption, below); the directories it writes into (below); and a wsd running outside its unit. A work tree that differs from the commit is reported, as advice only.
   - **A wsd outside its unit.** Unless `--no-wsd`, and while `heterodyne-wsd.service` isn't active, the approved archive's `deploy/platform_state.py` probes wsd's own instance lock, `~/.local/state/heterodyne/wsd/wsd.lock`, as the service user. It needs no release, so a fresh install, a dry run, `--fetch` and a rerun all look. A held lock refuses ("a wsd is running outside its unit: stop it first"), since starting the unit would only restart-loop on that lock. A lock that can't be read refuses too ("whether a wsd runs outside its unit can't be told"). The probe runs again before the first change.
   - **The directories.** The user unit directory (`~/.config/systemd/user/`), the install directory (`~/.local/share/heterodyne/install/`) and the scratch parent (`~/.cache/heterodyne/`) are each walked from the service user's home with `lstat`, in every branch, `--fetch` and a completed release included. Every level must be a real directory (not a symlink) of the user's, and no level above the directory may be writable by group or others. A missing level may be absent; an install makes it, 0700, one level at a time, and only once its parent has passed. The unit directory must also not be writable by others, since systemd loads what is in it. The scratch parent must be private (no group or other bits): it is refused, never narrowed. Any of these refuses before any change.
   - **A dry run leaves the home as it found it.** The levels the preflight makes for the scratch build and a dry run's renders are removed again, on success and on a refusal alike. A dry run makes no unit or install directory.
2. **Stage**: the archive, its check against `--expect-sha256`, and the stage's copy of the installer, as above. A trap removes the stage, on success and failure alike.
3. **Store (step 3)**: the staged `setup-store.sh --staged <stage>/tree --service-user <service user>`. It checks the stage (root's, 0700), then the staged helper (a regular file that parses), and then the host, before it makes any change. It refuses if:
   - the service user is root or doesn't exist;
   - an existing `heterodyne-store` has uid 0, the service user's uid, or a login shell;
   - any component of the paths below, from `/` down, is a symlink, isn't a directory, is owned by the service user, or is writable by them;
   - `/var/lib/heterodyne` or `/var/lib/heterodyne/harnesses` (or one of the other directories) exists with another owner;
   - a file it writes exists and isn't a regular file.
4. **Units (step 4)**, reconciled from systemd's live state, so a run that failed half way is repaired by the next:
   - each unit file is installed (root, 0644) only if it differs;
   - `systemctl daemon-reload` always runs, and systemd must then report `NeedDaemonReload=no` for the socket and the helper;
   - `systemctl enable heterodyne-store-helper.socket`;
   - the live check: the socket is active, `/run/heterodyne-store/helper.sock` is a socket owned by `heterodyne-store` with the service user's group and mode 0660, and `systemctl show -p SocketGroup,SocketMode,Listen` matches the units. An inactive socket is started, a drifted one restarted, and the check runs again; a mismatch then fails the install.
5. **Release**, as the service user: `~/.local/share/heterodyne/releases/<commit>/`, the commit's whole tree and its venv.
   - It archives the commit with the hardened git above, extracts it there, and runs `uv sync --locked --offline --no-dev --python /usr/bin/python3 --project <release>`, with `UV_PYTHON_DOWNLOADS=never` and `UV_LINK_MODE=copy`, so a release shares no file with the uv cache. The project is installed editable, from the release tree. `--fetch` drops `--offline`.
   - It writes `<release>/.complete` last: the commit, the uv version and `uv.lock`'s sha256. A release with `.complete` is `ok`. One without it is a failed run's leftover, and is removed and rebuilt in place (the venv's scripts hold absolute paths, so it can't be built elsewhere and renamed).
   - **The offline check.** Without `--fetch`, and unless the release is already complete, the preflight builds the full tree in a scratch directory, `~/.cache/heterodyne/preflight.XXXXXX/`, against a hardlink copy (`cp -al`) of the uv cache, and removes it whatever the result. A copy that fails refuses ("the uv cache could not be copied for the offline build check"), and the real cache is never used. A build that fails refuses before any change ("the uv cache lacks a package this release needs: run with --fetch"), with its last 20 lines of output, 64-hex runs masked. uv replaces cache entries by renaming, so the real cache is unchanged by the scratch build.
   - Old releases are kept, since a running agent's hooks name the interpreter of the release that launched it. Removing one that no process uses is a manual `rm -r`.
6. **Config check (step 6)**, before verity, since verity can't be undone, as the service user, with the units' locations (below): `<release>/.venv/bin/heterodyne config check`. In a dry run where the release isn't built yet, it is `advisory (the release is not built yet)`. A non-zero exit, a store problem (`harnesses problem:`) or `HELPER_DRIFT` fails the install, and verity is not touched; in a dry run either is reported only (`advisory (<why>; ...)`), since the dry run made none of the changes that might fix it. Before any change, the preflight also checks the config that this reads (`~/.config/heterodyne/config.toml`): it may be missing, but if it exists it must be valid TOML and must not set `[harnesses] fallback`. Nothing edits it.
7. **Verity (step 5)** on `/var/lib`'s filesystem, after every check, since it can't be undone, unless `--no-verity`. The preflight reads the filesystem (its type and device, the kernel feature, the e2fsprogs version, and the superblock's block size and features), and the staged run reads it again before its first change and refuses if it differs. Anything it can't read is a refusal, before any change. Then:
   - the host can't have it (not ext4, no `/sys/fs/ext4/features/verity`, e2fsprogs older than 1.45, or a block size that isn't the page size): `skipped`, and the exit is 3;
   - already on: `ok`;
   - otherwise it warns that the change can't be undone, runs `tune2fs -O verity <device>` (allowed on a mounted ext4), and reads the features line again, which must now list `verity`. A failure there is `failed`, exit 1: the one failure that can come after the other steps' changes.
8. **Services**, last, as the service user, on their own manager: `heterodyne-admind.service`, then `heterodyne-wsd.service`, in `~/.config/systemd/user/`, mode 0644. They come after the store helper's socket, which wsd uses, and after verity, so wsd never records installs as `verity: false` on a host that has it. admind comes first because it is the recovery channel.
   - Each unit is rendered from the release (`admind unit`, `wsd unit`), with a first line, `# Rendered by heterodyne's deploy/install.sh for release <commit>; the next install replaces it.`, and an environment block that pins the locations the installer checked, whatever the user manager's environment holds: `PATH` (the release's `.venv/bin`, then the service user's login PATH), `HETERODYNE_CONFIG_DIR` and `HETERODYNE_STATE_DIR` (v1's defaults, `~/.config/heterodyne` and `~/.local/state/heterodyne`), `HETERODYNE_RELEASE`, `HETERODYNE_UNIT_REV` (a hash of the render), and `UnsetEnvironment=BTQ_REPO XDG_CONFIG_HOME XDG_STATE_HOME XDG_DATA_HOME`.
   - A file that differs from the render is written (the install record first, then a temporary file renamed into place). `systemctl --user daemon-reload` runs when something was written or systemd still has an old copy, and systemd must then report `NeedDaemonReload=no`.
   - **The loaded unit** must be the render: its `FragmentPath` the managed file, `ExecStart` the release's `python3 -m heterodyne.admind run` (or `heterodyne.wsd run`) and nothing else, and `Environment` and `UnsetEnvironment` the block's. An operator drop-in (`<unit>.d/`) that changes any of these fails the step, naming the property; one that changes other properties (`Nice=`, say) is a `note:`, and is left in place.
   - **The running process** (its `MainPID`'s `/proc/<pid>/cmdline` and `environ`, read as the service user) must have the loaded unit's argv and the block's values, and none of the unset names. Only key names are ever printed. An unreadable process never counts as a match.
   - It enables each unit, then starts one that is inactive (`reset-failed` first if it failed), restarts one whose process differs (another release, an older render, a changed environment), and leaves a matching one alone. After a start or restart: wsd must answer `wsctl status` within 60 s, and admind must still be running 15 s later with `NRestarts` unchanged. Then the process is checked again, and a unit that restarted meanwhile or came back as something else fails, with the last 20 lines of its journal.
   - admind: `admind initialised`, from the release, says whether `admind init` has run, and changes nothing. It reads a consistent snapshot of admind's database (its file and WAL, each read twice) in a private copy, so it never takes a lock or a read mark on the live files. Exit 0 means initialised, 1 not. Exit 2 means it can't tell (a write was in progress, say), and the install fails with "admind initialised can't tell": run the installer again. If it hasn't run, admind's unit is written but neither enabled nor started: `skipped (admind init has not run)`, exit 3. Run `admind init` (docs/admind.md §2), then the installer again.
   - Units the installer doesn't manage, such as `heterodyne-admind-selftest.service`, are never read, written or restarted. The installer never runs `daemon-reexec`, `set-environment`, `unset-environment` or `import-environment`, and never restarts the user manager.

### Adoption and the install record

A unit file whose first line isn't the marker is hand-made. Without `--adopt` (and with no record entry for it) the preflight refuses: "heterodyne-admind.service was not written by the installer: run with --adopt to replace it (the old file is kept)". With `--adopt`, the installer copies the file to `~/.local/share/heterodyne/install/backup/<unit>.<UTC timestamp>` (0600), records the intent, replaces the file, and restarts the service once onto the release. A symlink, a non-regular file, or a file another user owns is always refused, `--adopt` or not.

The install record is `~/.local/share/heterodyne/install/record.json` (0600, in a 0700 directory, no secrets): the release of the last completed `services` step, and one entry per managed unit, `created` or `adopted` (with its backup's name and sha256), in state `writing` and then `written`. It is written atomically before each unit write, so a failure at any point leaves the intent on disk, and a rerun completes it: an adopted unit whose replacement didn't happen goes on with the same backup, without `--adopt` again. A record that can't be read, a file changed by hand since its backup, or a marker file the record doesn't name refuses, before any change.

### What v1 doesn't do

- **Only the default locations.** The units pin `HETERODYNE_CONFIG_DIR` and `HETERODYNE_STATE_DIR` to `~/.config/heterodyne` and `~/.local/state/heterodyne`, and unset the `XDG_*` overrides. A host that relies on moved locations has them pinned back to the defaults.
- **The build backend is not pinned.** uv checks every package in `uv.lock` against its hashes, which covers the runtime dependencies. The editable install also needs the build backend, `hatchling`, which `pyproject.toml` requires unpinned: uv resolves it and its dependencies outside `uv.lock`, from the cache (or the index, with `--fetch`), with no hash check. It runs as the service user while the release is built.
- **No automatic pruning** of old releases, and no change to lingering, ever.

The changes, and their undo:

| Change | Undo |
| --- | --- |
| `useradd --system --no-create-home --shell /usr/sbin/nologin --user-group heterodyne-store` | `userdel heterodyne-store` (also removes its group) |
| `install -d -o root -g root -m 0755 /var/lib/heterodyne` | see Rollback |
| `install -d -o heterodyne-store -g heterodyne-store -m 0755 /var/lib/heterodyne/harnesses` | see Rollback |
| `install -d -o root -g root -m 0755 /usr/local/libexec/heterodyne` | `rmdir /usr/local/libexec/heterodyne` |
| `install -d -o root -g root -m 0755 /etc/heterodyne` | `rmdir /etc/heterodyne` |
| `install -d -o root -g root -m 0755 /etc/systemd/system/heterodyne-store-helper.socket.d` | `rmdir` it |
| `install -o root -g root -m 0644 <stage>/tree/src/heterodyne/harness/resources/store_core.py /usr/local/libexec/heterodyne/store_core.py` | `rm` it |
| `install -o root -g root -m 0644 <generated> /etc/heterodyne/store-helper.json` (`{"service_uid": <uid>, "store": "/var/lib/heterodyne/harnesses"}`) | `rm` it |
| `install -o root -g root -m 0644 <generated> /etc/systemd/system/heterodyne-store-helper.socket.d/10-group.conf` (`SocketGroup=<group>`) | `rm` it |
| `install -o root -g root -m 0644 <stage>/tree/deploy/store-helper/<unit> /etc/systemd/system/<unit>`, for the socket and the service | see Rollback |
| `systemctl daemon-reload`, `systemctl enable heterodyne-store-helper.socket`, and `start` or `restart` when the live check differs | see Rollback |
| `tune2fs -O verity <device>` | none: `tune2fs` cannot clear it, and the flag stays set after a rollback |
| the stage `/run/heterodyne-install.XXXXXX` (removed by the installer on exit) | `rm -r` it |
| the release `~/.local/share/heterodyne/releases/<commit>/` (the service user's) | kept by the uninstall; `rm -r` one that no process uses |
| `~/.config/systemd/user/heterodyne-admind.service` and `heterodyne-wsd.service`, enabled and started | `deploy/uninstall.sh` (below) |
| the install record and adoption backups, `~/.local/share/heterodyne/install/` | the uninstall drops the record's entries; `--restore-adopted` puts the backups back |

A directory that already exists with the right owner but a different mode gets `chmod 0755` instead of `install -d`. Its undo is `chmod` back to the mode the dry run showed.

`wsd` runs under the installer's `heterodyne-wsd.service` (step 8). A wsd started by hand must be stopped before the install, which refuses while one runs outside its unit.

## Rollback

On a trusted checkout: `sudo bash deploy/uninstall.sh [--service-user <service user>] [--restore-adopted]`. On a checkout the service user can change, the approval names the pinned form, with the sum from `manifest.sh`'s `tree/deploy/uninstall.sh` line:

```bash uninstall-pinned
sudo bash -euo pipefail -c 'b=$(mktemp /run/hz-install-boot.XXXXXX); trap "rm -f $b" EXIT
cat <checkout>/deploy/uninstall.sh >"$b"
echo "<uninstall.sh sum>  $b" | sha256sum --check --strict --quiet
bash "$b"'
```

The block stays as it is. The service user is `$SUDO_USER` unless the approval gives `--service-user <service user>`, and `--restore-adopted` goes on the same `bash "$b" …` line.

The whole uninstall is one fail-stop root script. The first failure stops it, and nothing after that runs.
1. **The platform's user units come first, decided before anything is acted on.** With no install record there is no release to ask, so it classifies both units itself, from their files and systemd:
   - **The runtime directory.** systemd is asked only if the service user's `/run/user/<uid>` exists. logind makes it before the user manager starts and removes it after the manager stops, so when it is absent (ENOENT, and only that) no unit is loaded or running. Any other error reading it, or a `systemctl --user show` that fails, refuses: "the service user's systemd state can't be read".
   - **Nothing installed:** each unit either has no file, and systemd neither loads nor runs it, or has a hand-made file (no marker). It prints `platform: nothing installed`, names any hand-made unit, which it leaves in place even while it runs, and goes on to the root steps.
   - **Can't tell:** a file with the marker but no record, a unit loaded or running with no file, a symlink, a non-regular file or another user's, a file that can't be read, systemd state that can't be read, or a record that can't be checked (any error but ENOENT). It names each unit and the check, and refuses before any root step.

   Otherwise it reads the record's release, which must be complete, and runs that release's `deploy/platform_state.py` as the service user to classify both units, changing nothing:
   - **managed:** the file carries the marker, the record has a `written` entry for it, and systemd loads it from that file. With `--restore-adopted`, an adopted unit's backup must also be there, unchanged;
   - **absent:** no file, no entry, and systemd neither loads nor runs it;
   - **not managed:** no marker and no entry, a hand-made unit the installer never adopted. It is left in place, and reported;
   - **ambiguous:** anything else, such as a marker without an entry, an entry without a marker, an entry still `writing`, a record that can't be read, a symlink, a unit loaded from elsewhere or loaded with no file, or (with `--restore-adopted`) a missing or changed backup.

   If any unit is ambiguous, it refuses, naming each unit and the check, before any stop, disable, removal, restore or reload, and the root steps below don't run either. Otherwise it stops, disables and removes each managed unit, wsd first, then admind; with `--restore-adopted`, an adopted unit's backup is put back instead (0644), and not enabled or started. It drops their entries from the record, runs one `systemctl --user daemon-reload`, and lists the releases, which it keeps, with the state and the config. Lingering stays as it is.
2. It disables the socket, which stops new connections, and then stops every `heterodyne-store-helper@` instance, since `Accept=yes` instances outlive their socket. It refuses if any instance is still running, or if any process still runs as `heterodyne-store`. Then nothing has been removed.
3. It removes the units, the drop-in, the helper copy and the config.
4. It removes the store with `rmdir`, which fails if the store isn't empty. A store with installs in it stops the script there, and the store and its owner (`userdel` is last) are kept. The script never deletes installs. Emptying the store is the operator's decision (after `wsctl harness list` shows nothing in use), and it is not part of this list.
5. The ext4 `verity` feature stays set. `tune2fs` doesn't clear it, and it is harmless without the store.
