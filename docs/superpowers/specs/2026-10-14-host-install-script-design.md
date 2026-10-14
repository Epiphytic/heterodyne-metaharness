# One host install script (`deploy/install.sh`)

Requested by Liam on 2026-10-14: "anything that's required to install this has to be encapsulated in an install
script anyways, otherwise this platform is not installable by others. So make an install script for it, and get
me to run it using sudo."

## Problem

The store helper's host setup (plan 4c T12b) is four hand-run blocks in `deploy/store-helper/README.md`.
Steps 3 and 4 are root heredocs. Step 5 (ext4 verity) is a checklist with `<device>` to fill in by hand.
Step 6 is a check run as the service user. Each needs its sums pasted in from `manifest.sh`. Nobody else can
install the platform from that, and the operator can't run it from a phone.

## What it does

One command, from a checkout of the commit to install:

```
sudo bash deploy/install.sh [--dry-run] [--no-verity] [--service-user USER] [--expect-sha256 SUM]
```

1. **Preflight (no changes).** It must be root. The service user defaults to `$SUDO_USER`, and is refused if
   unset, if it is root, or if it doesn't exist. The checkout must be clean for the installed paths
   (`git status --porcelain -- deploy src/heterodyne/harness/resources/store_core.py` is empty). It prints the
   commit it installs.
2. **Stage (step 3's trust model, kept).** As the service user, with replacement refs, git config and hooks
   all off, it archives `HEAD`'s `deploy/` and `store_core.py` into `/run/heterodyne-install.XXXX`, a mkdtemp
   directory owned by root with mode 0700. It prints the archive's sha256, and if `--expect-sha256` is
   given, it refuses unless the archive matches. Then it extracts the archive and **re-execs the staged copy
   of itself** (`--staged DIR`). Every later step runs from the stage, never from the checkout.
3. **Store (step 3).** It runs the staged `setup-store.sh --staged … --service-user …`, unchanged. That script
   is already idempotent and prints `ok:` for anything already in place.
4. **Units (step 4).** It installs the two staged unit files only if they differ (`cmp`; root-owned, 0644),
   then runs `daemon-reload` and `enable --now heterodyne-store-helper.socket`. Both are no-ops when nothing
   changed.
5. **Verity (step 5), on by default.** It finds `/var/lib`'s device with `findmnt`, then makes the checks in
   order: ext4, `/sys/fs/ext4/features/verity`, e2fsprogs ≥ 1.45, and block size 4096. If the feature is
   already set it prints `ok:`. Otherwise it prints a one-line warning that the change can't be undone and
   runs `tune2fs -O verity <device>`, then confirms the feature is set. If any check fails, it prints
   `skipped: verity (<check>)` and carries on: the store works without verity, and installs are recorded
   `verity: false`. `--no-verity` skips this step.
6. **Config check (step 6).** As the service user, with `HETERODYNE_CONFIG_DIR` and `XDG_CONFIG_HOME` unset,
   it refuses if `[harnesses] fallback` is set, then runs `heterodyne config check` from the checkout via
   `uv run --offline`. A store problem or `HELPER_DRIFT` fails the install.
7. **Cleanup and summary.** It removes the stage (trap), then prints one line per step (`ok`, `changed`,
   `skipped` or `failed`) and the commit plus archive sha256 for the record.

`--dry-run` runs every check and prints `would:` for each change. It makes none, but still stages into
`/run`, then removes the stage.

A failure stops the script (`set -euo pipefail`), except in the verity step, which degrades to `skipped`.
Re-running is always safe. On this host, steps 3 and 4 are already done, so a run now reports them `ok` and
only does verity.

## Trust

Root runs `install.sh`'s first stage from the checkout. That is the standard model for "clone and
`sudo ./install`": the operator trusts the checkout they run it from. Everything after the re-exec runs from
the staged, commit-exact bytes, so the replacement-ref and edit-after-check attacks that step 3 guards
against still can't change what gets installed. On a host where agents can write the checkout (this one),
the operator pins the archive with `--expect-sha256`, which `manifest.sh` prints unprivileged. The first
stage is kept short (about 40 lines) so it can be read before running.

## Rollback and docs

`deploy/uninstall.sh` wraps the README's rollback block unchanged (`sudo bash deploy/uninstall.sh`). The
README's "root commands" section becomes "run `sudo bash deploy/install.sh`". The per-step blocks move to a
"what the installer does" reference, kept as the spec that the tests check.

## Tests (offline, in `tests/test_deploy_store_helper.py`)

- `bash -n` on both scripts.
- `--dry-run` as non-root refuses (it must be root). Root-only paths can't run in CI, so preflight and stage
  are factored into functions. The test sources them with a `--prefix` fake root, as `setup-store.sh` does,
  and checks:
  - the dirty-checkout refusal;
  - the `--expect-sha256` mismatch refusal;
  - that a planted `refs/replace` is ignored;
  - that the re-exec target is the staged path.
- The verity decision table, as a pure function over (fstype, kernel feature, e2fsprogs version, block size,
  features line), giving `ok`, `change` or `skipped(<check>)`.
- The README's step blocks still match what the script runs (the existing block tests stay).

## Out of scope

Installing the platform itself (venv, admind/wsd user units), which today is a manual
`uv sync` + `systemctl --user` step. It belongs in this script later, and is filed as a follow-up.
