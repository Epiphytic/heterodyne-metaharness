# The store helper's host setup (plan 4c Task 12)

The managed-install store (`/var/lib/heterodyne/harnesses`) belongs to the system user `heterodyne-store`, so
the service user who runs `wsd` can read installs but never write them. Every change goes through the store
helper (`store_core.py`), which systemd starts as `heterodyne-store` for each connection to its socket.
`wsd` connects as the service user, and the helper checks that peer by uid (`SO_PEERCRED`).

| File | What it is |
| --- | --- |
| `heterodyne-store-helper.socket` | `/run/heterodyne-store/helper.sock`, 0660, owned by `heterodyne-store`; `Accept=yes`, so each connection gets its own helper |
| `heterodyne-store-helper@.service` | the helper for one connection, which arrives on stdin, run as `heterodyne-store` under a strict sandbox |
| `setup-store.sh` | creates the user, the store, the helper copy, the helper's config and the socket's group drop-in (step 3) |

`tests/test_deploy_store_helper.py` checks all of this offline:
- the units pass `systemd-analyze verify` with no output;
- their keys match the plan and the paths the harness reads (`[harnesses]` in `defaults.toml`, `settings.STORE`, `store_core.CONFIG`);
- the units' command line serves a request;
- the script passes `bash -n`;
- its dry run lists every change and makes none, and each refusal is checked.

## Departures from the plan's unit text

- **`ExecStart` passes `--config /etc/heterodyne/store-helper.json`, not `serve`.** `store_core.main` takes only `--config`; a `serve` argument would fail argument parsing on every connection. The path is the helper's own default, given explicitly so that the unit states it.
- **`SocketGroup=` is in a drop-in, not in the unit.** The group is the service user's, so it differs per host. `setup-store.sh` writes `/etc/systemd/system/heterodyne-store-helper.socket.d/10-group.conf` (`[Socket]` `SocketGroup=<group>`), and the unit files stay as committed, byte for byte.

## Preview (no root, changes nothing)

Run this from a checkout at the pinned commit, as the service user:

```bash
bash deploy/store-helper/setup-store.sh --dry-run --commit <pinned commit> --service-user "$(id -un)"
```

The dry run runs every check. It prints `ok: <what>` for what is already in place and `would: <command>` for each change, ending with `done`. A failed check prints `refused: <check>` on stderr and exits 1. Usage errors exit 2. A dry run by any other user refuses, because only the service user can answer the "writable by the service user" check.

## The root commands (T12b, steps 3–6)

These are the commands the execution approval names. `<checkout>` is a clean checkout owned by the service user, at `<pinned commit>`. `<service user>` is the user `wsd` runs as. Nothing here runs before that approval.

### Step 3: the user, the store and the helper copy

```bash
cd <checkout>
sudo bash deploy/store-helper/setup-store.sh --commit <pinned commit> --service-user <service user>
```

The script runs every check before it makes any change. It refuses if:
- the checkout's HEAD is not the commit, or its `deploy/store-helper` differs from it;
- the service user is root or doesn't exist;
- an existing `heterodyne-store` has uid 0, the service user's uid, or a login shell;
- any component of the paths below, from `/` down, is a symlink, isn't a directory, or is writable by the service user;
- `/var/lib/heterodyne` or `/var/lib/heterodyne/harnesses` (or one of the other directories) exists with another owner;
- a file it writes exists and isn't a regular file.

Root never runs git from the checkout. The helper copy is read with `git cat-file` as the service user, and then installed by root. Every change is printed before it runs (`change: ...`), and anything already correct is reported `ok:`, so running the script again is safe.

| Change | Undo |
| --- | --- |
| `useradd --system --no-create-home --shell /usr/sbin/nologin --user-group heterodyne-store` | `userdel heterodyne-store` (also removes its group) |
| `install -d -o root -g root -m 0755 /var/lib/heterodyne` | see Rollback |
| `install -d -o heterodyne-store -g heterodyne-store -m 0755 /var/lib/heterodyne/harnesses` | see Rollback |
| `install -d -o root -g root -m 0755 /usr/local/libexec/heterodyne` | `rmdir /usr/local/libexec/heterodyne` |
| `install -d -o root -g root -m 0755 /etc/heterodyne` | `rmdir /etc/heterodyne` |
| `install -d -o root -g root -m 0755 /etc/systemd/system/heterodyne-store-helper.socket.d` | `rmdir` it |
| `install -o root -g root -m 0644 <staged> /usr/local/libexec/heterodyne/store_core.py` (the helper at the commit) | `rm` it |
| `install -o root -g root -m 0644 <staged> /etc/heterodyne/store-helper.json` (`{"service_uid": <uid>, "store": "/var/lib/heterodyne/harnesses"}`) | `rm` it |
| `install -o root -g root -m 0644 <staged> /etc/systemd/system/heterodyne-store-helper.socket.d/10-group.conf` (`SocketGroup=<group>`) | `rm` it |

A directory that already exists with the right owner but a different mode gets `chmod 0755` instead of `install -d`. Its undo is `chmod` back to the mode the dry run showed.

### Step 4: the units

```bash
cd <checkout>
sudo install -m 0644 deploy/store-helper/heterodyne-store-helper.socket deploy/store-helper/heterodyne-store-helper@.service \
     /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heterodyne-store-helper.socket
```

- These commands add the two unit files, plus the `sockets.target.wants/` symlink that `enable` creates, and start listening on `/run/heterodyne-store/helper.sock`.
- To undo: `systemctl disable --now heterodyne-store-helper.socket`, `rm` both unit files, then `systemctl daemon-reload`.

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

This step needs the operator's go for the config change.
1. Remove `[harnesses] fallback = true` from the host config if it was set.
2. Run `heterodyne config check`. It must report no store problem and no `HELPER_DRIFT`.
3. Restart `wsd`.

To undo, put `fallback = true` back and restart `wsd`.

## Rollback

1. Run `systemctl disable --now heterodyne-store-helper.socket`. Then `rm /etc/systemd/system/heterodyne-store-helper.socket /etc/systemd/system/heterodyne-store-helper@.service`, followed by `rm -r /etc/systemd/system/heterodyne-store-helper.socket.d` and `systemctl daemon-reload`.
2. Run `rm /usr/local/libexec/heterodyne/store_core.py /etc/heterodyne/store-helper.json`, then `rmdir /usr/local/libexec/heterodyne /etc/heterodyne`.
3. Remove `/var/lib/heterodyne` only if it is empty, or after `wsctl harness list` shows that nothing in the store is in use.
4. Run `userdel heterodyne-store`. This comes after step 3, so that no file is left owned by a uid with no user.
5. Leave the ext4 `verity` feature set. `tune2fs` doesn't clear it, and it is harmless without the store.
