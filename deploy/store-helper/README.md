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
| `manifest.sh` | prints the sums the execution approval pins for step 3 (unprivileged) |

`tests/test_deploy_store_helper.py` checks all of this offline:
- the units pass `systemd-analyze verify` with no output;
- their keys match the plan and the paths the harness reads (`[harnesses]` in `defaults.toml`, `settings.STORE`, `store_core.CONFIG`);
- the units' command line serves a request;
- both scripts pass `bash -n`;
- the script's dry run lists every change and makes none, and each refusal is checked;
- the step-3, step-4, step-6 and rollback blocks below run (or parse) as written, including step 3 against a planted replacement ref.

## Departures from the plan's unit text

- **`ExecStart` passes `--config /etc/heterodyne/store-helper.json`, not `serve`.** `store_core.main` takes only `--config`; a `serve` argument would fail argument parsing on every connection. The path is the helper's own default, given explicitly so that the unit states it.
- **`SocketGroup=` is in a drop-in, not in the unit.** The group is the service user's, so it differs per host. `setup-store.sh` writes `/etc/systemd/system/heterodyne-store-helper.socket.d/10-group.conf` (`[Socket]` `SocketGroup=<group>`), and the unit files stay as committed, byte for byte.

## Where root's trust comes from

The checkout belongs to the service user, who can change any file in it, or what git reports for a commit (a `refs/replace/` ref), after any check. So root never runs, reads or installs anything from the checkout. Step 3:
1. archives the pinned commit as the service user, with replacement refs ignored (`GIT_NO_REPLACE_OBJECTS=1`, no git config, no hooks), into `/run/heterodyne-store-setup`, a directory only root can enter;
2. checks the archive against the sha256 that the execution approval names, **before** extracting it;
3. extracts it, and checks each file against the approval's sums;
4. runs the staged `setup-store.sh`, which installs the staged helper.

Step 4 installs the units from the same stage. The sums come from `manifest.sh`, which the lead runs on the merged commit, unprivileged, and pastes into the approval. It builds the archive the same way, so the sums hold only for the commit's own bytes. Anything else makes `sha256sum` fail, and since step 3 is one `bash -e` script, nothing after that runs.

## Preview (no root, changes nothing)

Run this as the service user, from a checkout that has the pinned commit:

```bash
stage=$(mktemp -d)
git archive <pinned commit> -- deploy/store-helper src/heterodyne/harness/resources/store_core.py | tar -x -C "$stage"
bash "$stage/deploy/store-helper/setup-store.sh" --dry-run --staged "$stage" --service-user "$(id -un)"
rm -rf "$stage"
```

The dry run runs every check. It prints `ok: <what>` for what is already in place and `would: <command>` for each change, ending with `done`. A failed check prints `refused: <check>` on stderr and exits 1. Usage errors exit 2. A dry run by any other user refuses, because only the service user can answer the "writable by the service user" check.

## The root commands (T12b, steps 3–6)

These are the commands the execution approval names. Nothing here runs before that approval. The approval fills in:
- `<checkout>`: a checkout that has `<pinned commit>`;
- `<service user>`: the user `wsd` runs as;
- the two sums placeholders, with `manifest.sh`'s output for `<pinned commit>` (its first line, then the rest);
- `<run checkout>` (step 6): the checkout heterodyne runs from.

### Step 3: the user, the store and the helper copy

```bash step-3
sudo bash -euo pipefail -s <<'STEP3'
umask 077
stage=/run/heterodyne-store-setup
mkdir -m 0700 "$stage" "$stage/tree"
cd /
runuser -u <service user> -- env -i PATH=/usr/bin:/bin GIT_NO_REPLACE_OBJECTS=1 GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null git -C <checkout> -c core.hooksPath=/dev/null archive --format=tar \
    <pinned commit> -- deploy/store-helper src/heterodyne/harness/resources/store_core.py >"$stage/archive.tar"
cd "$stage"
sha256sum --check --strict --quiet <<'SUMS'
<the archive line from manifest.sh>
SUMS
tar -x --no-same-owner --no-same-permissions -C tree -f archive.tar
sha256sum --check --strict --quiet <<'SUMS'
<the tree lines from manifest.sh>
SUMS
bash tree/deploy/store-helper/setup-store.sh --staged "$stage/tree" --service-user <service user>
STEP3
```

- `mkdir` fails if the stage already exists (from an earlier attempt). Remove it with `sudo rm -r /run/heterodyne-store-setup`, and start again. The stage is on tmpfs, so a reboot also removes it.
- `setup-store.sh` checks the stage (root's, 0700), then the staged helper (a regular file that parses), and then the host, before it makes any change. It refuses if:
  - the service user is root or doesn't exist;
  - an existing `heterodyne-store` has uid 0, the service user's uid, or a login shell;
  - any component of the paths below, from `/` down, is a symlink, isn't a directory, is owned by the service user, or is writable by them;
  - `/var/lib/heterodyne` or `/var/lib/heterodyne/harnesses` (or one of the other directories) exists with another owner;
  - a file it writes exists and isn't a regular file.
- Every change is printed before it runs (`change: ...`), and anything already correct is reported `ok:`, so running the step again is safe.

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
| the stage `/run/heterodyne-store-setup` (removed at the end of step 4) | `rm -r` it |

A directory that already exists with the right owner but a different mode gets `chmod 0755` instead of `install -d`. Its undo is `chmod` back to the mode the dry run showed.

### Step 4: the units

```bash step-4
sudo install -o root -g root -m 0644 /run/heterodyne-store-setup/tree/deploy/store-helper/heterodyne-store-helper.socket \
     /run/heterodyne-store-setup/tree/deploy/store-helper/heterodyne-store-helper@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heterodyne-store-helper.socket
sudo rm -r /run/heterodyne-store-setup
```

- These commands add the two unit files (the staged, checked copies), plus the `sockets.target.wants/` symlink that `enable` creates, start listening on `/run/heterodyne-store/helper.sock`, and remove the stage.
- To undo: Rollback (it removes the units first, and everything after them only once no helper runs).

### Step 5: verity on `/var/lib`'s filesystem

Check first. Stop at the first check that fails, and report which one it was:

```bash
findmnt -no FSTYPE,SOURCE --target /var/lib          # ext4 and <device>
test -e /sys/fs/ext4/features/verity                  # the kernel's ext4 has verity
tune2fs 2>&1 | head -1                                # e2fsprogs 1.45 or later
sudo tune2fs -l <device> | command grep -i '^block size'      # 4096
sudo tune2fs -l <device> | command grep -i '^filesystem features'
```

If the features line already lists `verity`, skip ahead to the confirmation. Otherwise:

```bash
sudo tune2fs -O verity <device>                       # allowed on a mounted ext4
sudo tune2fs -l <device> | command grep -i '^filesystem features'   # confirm: lists verity
```

- This change sets the ext4 `verity` feature flag on `<device>`. `tune2fs` cannot clear it, so it has no undo, and the flag stays set after a rollback.
- If any check fails, the step is not skipped. T12b stays open, and the store runs without verity: each install is recorded `verity: false`.

### Step 6: the host config

Run this as the service user, not root. It checks the config and edits nothing:

```bash step-6
cfg=$HOME/.config/heterodyne/config.toml
/usr/bin/python3 -I -c 'import sys, tomllib; h = tomllib.load(open(sys.argv[1], "rb")).get("harnesses", {}); sys.exit("harnesses.fallback is set: stop and ask the lead" if "fallback" in h else 0)' "$cfg"
env -u HETERODYNE_CONFIG_DIR -u XDG_CONFIG_HOME uv run --offline --project <run checkout> heterodyne config check
```

- `cfg` is the host config that `heterodyne` reads for the service user when `HETERODYNE_CONFIG_DIR` and `XDG_CONFIG_HOME` are unset. `config check` runs with both unset, so it reads the same file. The approval spells out the concrete path.
- The check parses the file as TOML. It stops (exit 1) if `[harnesses]` sets `fallback` at all, and also if the file is missing or isn't valid TOML. On this host (checked 2026-10-14), the config has no `[harnesses]` table, so it passes.
- If it stops, nothing is edited. Removing `fallback` takes a fresh approval, for that exact edit.
- `config check` must report no store problem and no `HELPER_DRIFT`.
- `wsd`: on this host (checked 2026-10-14), `wsd` runs under no unit and isn't running, so there is nothing to restart and the restart is not applicable. If `wsd` runs under a unit by execution time, the approval names it, and the restart is `systemctl --user restart <that unit>`.
- Nothing here changes the host, so there is no undo.

## Rollback

```bash rollback
sudo bash -euo pipefail -s <<'ROLLBACK'
systemctl disable --now heterodyne-store-helper.socket
systemctl stop 'heterodyne-store-helper@*.service'
left=$(systemctl list-units --plain --no-legend --state=active,activating,deactivating 'heterodyne-store-helper@*')
[[ -z $left ]] || { echo "refused: a helper instance is still running" >&2; exit 1; }
if pgrep -u heterodyne-store >/dev/null; then
    echo "refused: a process still runs as heterodyne-store" >&2
    exit 1
fi
rm /etc/systemd/system/heterodyne-store-helper.socket /etc/systemd/system/heterodyne-store-helper@.service
rm /etc/systemd/system/heterodyne-store-helper.socket.d/10-group.conf
rmdir /etc/systemd/system/heterodyne-store-helper.socket.d
systemctl daemon-reload
rm /usr/local/libexec/heterodyne/store_core.py /etc/heterodyne/store-helper.json
rmdir /usr/local/libexec/heterodyne /etc/heterodyne
rmdir /var/lib/heterodyne/harnesses /var/lib/heterodyne
userdel heterodyne-store
ROLLBACK
```

The whole rollback is one fail-stop root script. The first failure stops it, and nothing after that runs.
1. It disables the socket, which stops new connections, and then stops every `heterodyne-store-helper@` instance, since `Accept=yes` instances outlive their socket. It refuses if any instance is still running, or if any process still runs as `heterodyne-store`. Then nothing has been removed.
2. It removes the units, the drop-in, the helper copy and the config.
3. It removes the store with `rmdir`, which fails if the store isn't empty. A store with installs in it stops the script there, and the store and its owner (`userdel` is last) are kept. The script never deletes installs. Emptying the store is the operator's decision (after `wsctl harness list` shows nothing in use), and it is not part of this list.
4. The ext4 `verity` feature stays set. `tune2fs` doesn't clear it, and it is harmless without the store.
