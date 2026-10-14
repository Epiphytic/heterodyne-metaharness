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
| `../install.sh` | the whole host setup, steps 3 to 6, in one root script |
| `../uninstall.sh` | the rollback, as one fail-stop root script |

`tests/test_deploy_store_helper.py` checks all of this offline:
- the units pass `systemd-analyze verify` with no output;
- their keys match the plan and the paths the harness reads (`[harnesses]` in `defaults.toml`, `settings.STORE`, `store_core.CONFIG`);
- the units' command line serves a request;
- the scripts pass `bash -n`, and `install.sh` and `manifest.sh` name the same paths;
- `setup-store.sh`'s dry run lists every change and makes none, and each refusal is checked;
- `install.sh` runs as a non-root user against a fake root, with shims for the host's commands: each refusal, the commit pinning, the socket reconciliation, each verity outcome, the config check and the dry run;
- the pinned install and uninstall blocks below run as written, and run nothing from a changed script;
- the rollback runs as written, against stubs.

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

The one thing still run from the checkout is step 6, `heterodyne config check`, as the service user, not root. It can lie about the result, but it gains no privilege.

## Preview (no root, changes nothing)

Run this as the service user, from a checkout that has the pinned commit:

```bash
stage=$(mktemp -d)
git archive <pinned commit> -- deploy src/heterodyne/harness/resources/store_core.py | tar -x -C "$stage"
bash "$stage/deploy/store-helper/setup-store.sh" --dry-run --staged "$stage" --service-user "$(id -un)"
rm -rf "$stage"
```

The dry run runs every check. It prints `ok: <what>` for what is already in place and `would: <command>` for each change, ending with `done`. A failed check prints `refused: <check>` on stderr and exits 1. Usage errors exit 2. A dry run by any other user refuses, because only the service user can answer the "writable by the service user" check.

`sudo bash deploy/install.sh --dry-run` previews the whole install the same way: it runs every check and prints `would:` for each change, without making any.

## Prerequisites

The checkout heterodyne runs from has its venv: run `uv sync` there as the service user, with `uv` on their login PATH. The installer runs `uv run --offline --project <checkout> heterodyne --help` as the service user before any change, and refuses with "run uv sync there first" if it fails.

## Running the installer

On a trusted checkout (one only root or the operator can write, such as a fresh clone):

```bash
sudo bash deploy/install.sh [--dry-run] [--no-verity] [--service-user <service user>]
```

On a checkout the service user can change (this host's), the installer refuses that form, and the execution approval names this one instead. It fills in `<checkout>`, `<commit>` and `<service user>`, and the two sums from `manifest.sh`'s output for `<commit>` (its `deploy/install.sh` line and its archive line):

```bash install-pinned
sudo bash -euo pipefail -c 'b=$(mktemp /run/hz-install-boot.XXXXXX); trap "rm -f $b" EXIT
cat <checkout>/deploy/install.sh >"$b"
echo "<install.sh sum>  $b" | sha256sum --check --strict --quiet
bash "$b" --checkout <checkout> --commit <commit> --expect-sha256 <archive sum> --service-user <service user>'
```

- `mktemp` creates the copy as root, mode 0600, in `/run`, which the service user can't write. After the copy, edits to the checkout can't touch it, and a copy whose sum isn't the approval's runs nothing.
- The copy is outside the checkout, so it is given `--checkout`.

It prints each step as it goes, then a summary: one line per step (`store`, `units`, `config`, `verity`), each `ok`, `changed`, `skipped (<why>)`, `failed (<why>)` or (a dry run's config check) `advisory (<why>; ...)`, then the commit and the archive's sha256. Exit 0: everything is in place. 3: everything but verity, which the host doesn't support. 1: a refusal (`refused: <check>`, before any change) or a failure. 2: a usage error. Re-running it is always safe.

## What the installer does

1. **Preflight**, with no change: root; the service user (`--service-user`, or else `$SUDO_USER`; not root, and existing); the tools; `/usr/bin/python3` 3.12 or later; the pinned form on a checkout the service user can change; the host config (below); `uv` and the run checkout; the verity probe (below); the commit, resolved once. A work tree that differs from the commit is reported, as advice only.
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
5. **Config check (step 6)**, before verity, since verity can't be undone, as the service user with `HETERODYNE_CONFIG_DIR` and `XDG_CONFIG_HOME` unset: `uv run --offline --project <checkout> heterodyne config check`. A non-zero exit, a store problem (`harnesses problem:`) or `HELPER_DRIFT` fails the install, and verity is not touched; in a dry run either is reported only (`advisory (<why>; ...)`), since the dry run made none of the changes that might fix it. Before any change, the preflight also checks the config that this reads (`~/.config/heterodyne/config.toml`): it may be missing, but if it exists it must be valid TOML and must not set `[harnesses] fallback`. Nothing edits it.
6. **Verity (step 5)** on `/var/lib`'s filesystem, last, unless `--no-verity`. The preflight reads the filesystem (its type and device, the kernel feature, the e2fsprogs version, and the superblock's block size and features), and the staged run reads it again before its first change and refuses if it differs. Anything it can't read is a refusal, before any change. Then:
   - the host can't have it (not ext4, no `/sys/fs/ext4/features/verity`, e2fsprogs older than 1.45, or a block size that isn't the page size): `skipped`, and the exit is 3;
   - already on: `ok`;
   - otherwise it warns that the change can't be undone, runs `tune2fs -O verity <device>` (allowed on a mounted ext4), and reads the features line again, which must now list `verity`. A failure there is `failed`, exit 1: the one failure that can come after the other steps' changes.

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

A directory that already exists with the right owner but a different mode gets `chmod 0755` instead of `install -d`. Its undo is `chmod` back to the mode the dry run showed.

`wsd`: on this host (checked 2026-10-14), `wsd` runs under no unit and isn't running, so there is nothing to restart. If `wsd` runs under a unit by execution time, the approval names it, and the restart is `systemctl --user restart <that unit>`.

## Rollback

On a trusted checkout: `sudo bash deploy/uninstall.sh`. On a checkout the service user can change, the approval names the pinned form, with the sum from `manifest.sh`'s `tree/deploy/uninstall.sh` line:

```bash uninstall-pinned
sudo bash -euo pipefail -c 'b=$(mktemp /run/hz-install-boot.XXXXXX); trap "rm -f $b" EXIT
cat <checkout>/deploy/uninstall.sh >"$b"
echo "<uninstall.sh sum>  $b" | sha256sum --check --strict --quiet
bash "$b"'
```

The whole rollback is one fail-stop root script. The first failure stops it, and nothing after that runs.
1. It disables the socket, which stops new connections, and then stops every `heterodyne-store-helper@` instance, since `Accept=yes` instances outlive their socket. It refuses if any instance is still running, or if any process still runs as `heterodyne-store`. Then nothing has been removed.
2. It removes the units, the drop-in, the helper copy and the config.
3. It removes the store with `rmdir`, which fails if the store isn't empty. A store with installs in it stops the script there, and the store and its owner (`userdel` is last) are kept. The script never deletes installs. Emptying the store is the operator's decision (after `wsctl harness list` shows nothing in use), and it is not part of this list.
4. The ext4 `verity` feature stays set. `tune2fs` doesn't clear it, and it is harmless without the store.
