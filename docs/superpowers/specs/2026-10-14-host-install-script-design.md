# One host install script (`deploy/install.sh`), r2

Requested by Liam on 2026-10-14: "anything that's required to install this has to be encapsulated in an install
script anyways, otherwise this platform is not installable by others. So make an install script for it, and get
me to run it using sudo."

r2 answers Codex design review r1 (5 blocking findings, 1 non-blocking) and p4b-t1's notes. The changes:
- a verified bootstrap;
- one path set shared with `manifest.sh`;
- one resolved commit;
- socket reconciliation;
- verity failures split from unsupported capability;
- a prerequisite preflight;
- orchestration tests.

## Problem

The store helper's host setup (plan 4c T12b) is four hand-run blocks in `deploy/store-helper/README.md`:
- steps 3 and 4 are root heredocs;
- step 5 (ext4 verity) is a checklist with `<device>` to fill in by hand;
- step 6 is a check run as the service user.

Each needs its sums pasted in from `manifest.sh`. Nobody else can install the platform from that, and the
operator can't run it from a phone.

## The two ways to run it

**A trusted checkout** is one that only root, or the operator, can write: a fresh clone on a new host. Run:

```
sudo bash deploy/install.sh [--dry-run] [--no-verity] [--service-user USER]
```

Root runs the checkout's `install.sh` as is. That is the standard model for "clone and `sudo ./install`": the
operator trusts the checkout they run it from.

**An agent-writable checkout** (this host: the service user owns it, and agents run as the service user) uses
the pinned command, which the approval spells out in full:

```
sudo bash -euo pipefail -c 'b=$(mktemp /run/hz-install-boot.XXXXXX); trap "rm -f $b" EXIT;
  cat /path/to/checkout/deploy/install.sh >"$b";
  echo "<install.sh sum>  $b" | sha256sum --check --strict --quiet;
  bash "$b" --commit <commit> --expect-sha256 <archive sum> --service-user <user>'
```

- `mktemp` creates the copy as root, mode 0600, in `/run`, which the service user can't write. After the copy,
  edits to the checkout can't touch it.
- The copy is checked before it runs, so the code that does the later archive compare is itself verified.
- `<install.sh sum>`, `<archive sum>` and `<commit>` come from `manifest.sh`, run unprivileged on the merged
  commit (see below). They travel in the execution approval, and the operator pastes the whole block.

**Defence against using the wrong form.** If the checkout (any path component of `deploy/install.sh`, or the
repo's `.git`) is owned by or writable by the service user, the script refuses to run without `--commit` and
`--expect-sha256`. A modified first stage could skip this check, so it is not a trust boundary. It catches the
honest mistake of running the unpinned form on this host.

## What it does

1. **Preflight. No changes, and every check runs before the first change.**
   - **Root and the service user.** The script must run as root. The service user is `--service-user`, or else
     `$SUDO_USER`, and is refused if unset, root, or missing.
   - **Tools.** These must be present: `/usr/bin/python3` ≥ 3.12, `git`, `runuser`, `systemctl`, `findmnt`,
     `cmp`, `sha256sum` and `tar`. `tune2fs` is checked only when verity is wanted.
   - **The run checkout works.** As the service user, `uv` must be on the service user's login PATH, and
     `uv run --offline --project <checkout> heterodyne --version` must succeed, so step 6 can run. On a fresh
     host, the operator runs `uv sync` first. The README's prerequisites say so, and the refusal names it.
   - **The host config.** The service user's config is `~/.config/heterodyne/config.toml`, resolved with
     `HETERODYNE_CONFIG_DIR` and `XDG_CONFIG_HOME` unset (that is the file `config check` reads). It may be
     missing: heterodyne reads a missing file as empty (`config/layers.py`), and the fallback check passes.
     If it exists, it must parse as TOML, and it must not set `[harnesses] fallback`. Nothing edits it.
   - **The commit.** It is resolved once, as the service user, with replacement refs, git config and hooks all
     off: `git rev-parse --verify <--commit or HEAD>^{commit}`. Every later git call uses that full ID, never
     `HEAD`, so a later commit switch can't move what gets installed. With `--commit`, the resolved ID must
     equal the one given. A dirty work tree is reported, but only as advice: it is git run as the service user,
     not a trust boundary, and what gets installed is the commit's bytes, not the work tree's.
2. **Stage (step 3's trust model, kept).**
   - As the service user, with the same git hardening, it archives the resolved commit's paths into a mkdtemp
     directory, `/run/heterodyne-install.XXXX`, owned by root with mode 0700.
   - The path set is the one shared with `manifest.sh`, so the sums agree by construction: `deploy/` and
     `src/heterodyne/harness/resources/store_core.py`. `manifest.sh` reads the same path list (one `PATHS`
     array, sourced from `deploy/paths.sh`), and also prints `<sha256>  deploy/install.sh` from the commit's
     blob.
   - It prints the archive's sha256. With `--expect-sha256`, it refuses unless the archive matches, before
     extracting anything.
   - It extracts with `--no-same-owner --no-same-permissions`, then re-execs the staged copy of itself as
     `bash <stage>/tree/deploy/install.sh --staged <stage> …`, passing the resolved commit. From then on
     everything runs from the stage, and nothing is read from the checkout except step 6's `uv run`.
3. **Store (step 3).** It runs the staged `setup-store.sh --staged … --service-user …`, unchanged. That script
   is idempotent. Before it runs, the installer records the drop-in's bytes, so it knows whether
   `SocketGroup` changed.
4. **Units (step 4) and reconciliation.**
   - It installs each staged unit file only if it differs (`cmp`; owned by root, mode 0644), and runs
     `daemon-reload` only if a unit or the drop-in changed.
   - Then `systemctl enable heterodyne-store-helper.socket`.
   - If the socket was already active and a unit or the drop-in changed, it runs
     `systemctl restart heterodyne-store-helper.socket`, so the new `SocketGroup`, mode and path take effect.
     If the socket was inactive, it runs `systemctl start` instead.
   - It then confirms: `is-active`, and that `/run/heterodyne-store/helper.sock` exists with the drop-in's
     group and mode 0660. Any mismatch fails the install.
   - Accept=yes instances that are already serving a connection finish under their old unit. Each one serves
     a single connection, so nothing long-lived is left with the old settings.
5. **Verity (step 5), on by default.**
   - It finds `/var/lib`'s device with `findmnt -no FSTYPE,SOURCE --target /var/lib`.
   - **Unsupported:** any of these means the host can't have verity, so it prints `skipped: verity (<check>)`,
     the install carries on, and the final exit is 3:
     - not ext4;
     - no `/sys/fs/ext4/features/verity`;
     - e2fsprogs < 1.45;
     - block size ≠ the page size (4096 here).
   - **Already on:** if the features line lists `verity`, it prints `ok:`.
   - **Otherwise:**
     1. it prints a one-line warning that the change can't be undone;
     2. it runs `tune2fs -O verity <device>`;
     3. it reads the features line again, which must now list `verity`.
   - A `tune2fs` that fails, a confirmation that doesn't show `verity`, or an unreadable superblock is
     `failed: verity`, exit 1. Like `store_core.enable_verity`, this separates "the host can't" from "it
     broke".
   - `--no-verity` skips the step on purpose (`skipped: verity (--no-verity)`) and doesn't change the exit.
6. **Config check (step 6).** As the service user, with `HETERODYNE_CONFIG_DIR` and `XDG_CONFIG_HOME` unset,
   it runs `uv run --offline --project <checkout> heterodyne config check`. A non-zero exit, a store
   problem, or `HELPER_DRIFT` in the output fails the install.
7. **Cleanup and summary.**
   - A trap removes the stage, on the success path and the failure path alike.
   - It prints one line per step: `ok`, `changed`, `skipped (<why>)` or `failed (<why>)`, followed by the
     commit and the archive sha256.
   - Exit codes: 0 means everything is in place. 3 means everything is in place except verity, which the
     host doesn't support. 1 means a refusal or a failure. 2 is a usage error.

`--dry-run` runs every check, and prints `would:` for each change without making any. It still stages into
`/run` and removes the stage afterwards.

Re-running is always safe. On this host, steps 3 and 4 are already done, so a run now reports them `ok`, turns
verity on, and passes step 6.

## What the trust model guarantees

- **On a trusted checkout:** the installed bytes are the resolved commit's, even if the service user changes
  the work tree or plants a `refs/replace` ref.
- **On an agent-writable checkout, with the pinned command:** root runs only the `install.sh` whose sum the
  approval names, and installs only the archive whose sum the approval names. That is step 3's guarantee,
  extended to the installer's own first stage.
- **Residual:** step 6 runs `heterodyne config check` from the checkout, as the service user, not as root. It
  can lie about the result, but it gains no privilege. The result is advisory for the operator, as it is
  today.

## Rollback and docs

- **Rollback.** `deploy/uninstall.sh` is the README's rollback block, unchanged, as one fail-stop script:
  `sudo bash deploy/uninstall.sh`. On an agent-writable host it gets the same pinned copy-and-check form.
- **README.** The "root commands" section becomes two subsections:
  - prerequisites: `uv sync` in the run checkout;
  - running the installer: both forms above.
- **Reference.** The current per-step blocks move to a "what the installer does" reference. The existing
  block tests are adjusted so that they check the script's commands, not the hand-run blocks.

## Tests (offline and unprivileged, in `tests/test_deploy_store_helper.py`)

The script takes `--prefix DIR`, which is test-only and refused outside `HZ_INSTALL_TEST=1`. It also takes an
injected PATH of shims, so the whole orchestration runs as a non-root user against a fake root. The shims stand
in for `systemctl`, `tune2fs`, `findmnt`, `runuser`, `install`, `useradd` and `id -u`, which reports 0. Each
shim logs its argv, and any one can be made to fail. The tests cover:

- `bash -n` on `install.sh`, `uninstall.sh` and `paths.sh`;
- **`manifest.sh` against the installer:** for the same commit, its archive sum and `install.sh` sum equal what
  the installer computes;
- **bootstrap:** the pinned command, with a tampered `install.sh` in the checkout, fails `sha256sum` and runs
  nothing;
- **refusals before any change:**
  - not root;
  - missing service user;
  - an unpinned run on a service-user-writable checkout;
  - an `--expect-sha256` mismatch;
  - a `--commit` mismatch;
  - a config that sets `fallback`;
  - `uv run` failing;
  
  For each, the shim log has no mutating command.
- **pinning:** a planted `refs/replace`, a commit switch after resolution, and a work-tree edit after staging
  all leave the installed bytes equal to the resolved commit's;
- **the re-exec:** argv is the staged path, and the stage is removed on success and on an injected failure;
- **socket reconciliation:**
  - an unchanged re-run logs no `daemon-reload` or `restart`;
  - a changed drop-in with an active socket logs `daemon-reload` and then `restart`;
  - an inactive socket logs `start`;
- **verity:** each unsupported reason gives exit 3 and `skipped`; `tune2fs` failing, or a confirmation without
  `verity`, gives exit 1 and `failed`; already on gives `ok` and no `tune2fs -O`; `--no-verity` gives exit 0;
- **step 6:** `HELPER_DRIFT` in the shimmed output fails the install;
- **`--dry-run`:** no mutating command is logged.

## Out of scope

Installing the platform itself is out of scope here: the venv, and the admind and wsd user units. Today those
are a manual `uv sync` plus `systemctl --user`, and the preflight checks the first of them. They belong in
this script later, and are filed as a follow-up.
