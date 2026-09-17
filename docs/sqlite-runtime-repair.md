# SQLite runtime repair deployment

Contract: [active runtime specification](../spec/runtime.md).
Bead: `btq-harness-9a729b18a99e76122751427b`.

Verified before repair: Hermes venv Python 3.11.15, built-in `_sqlite3`, SQLite 3.50.4,
source ID `2025-07-30 19:33:53 4d8adfb30e03f9cf27f800a2c1ba3c48fb4ca1b08b0f5ed59a4d5ecbf45e20a3`.
Gateway and maintenance Hermes manager use this venv. System library replacement
cannot repair this bundled SQLite. Anubis has a separate Nix runtime; the harness
supervisor uses system Python. Do not infer their provenance from Hermes.

[SQLite upstream](https://www.sqlite.org/wal.html#walreset) documents the race and
fix in 3.51.3+, with backports 3.50.7 and 3.44.6. Existing integrity checks passed;
that does not negate the vulnerability. Ubuntu package 3.45.1-1ubuntu2.7 has
[other security fixes](https://ubuntu.com/security/notices/USN-8565-1); that notice
alone does not establish a WAL-reset backport. The harness uses DELETE journaling.
No system-package upgrade is part of this venv repair.

The wrapper reuses `managed_uv.py` provisioning, locked all-extras venv staging,
smoke and rename/rollback helpers. These are private interfaces: helper and lock
hashes are pinned between stage and apply. A future native interface change needs
review. It avoids `hermes update`, source changes, auto-updating uv, and the public
repair helper's success-time deletion of the old venv. Failed provisioning stops.

## Stage while services remain available

Run from the integrated harness repository with system Python, outside the old
venv. Use a fresh private quarantine outside Hermes, brain and Git directories.
No secrets/transcripts or backup archives belong in Git.

```sh
env -u HERMES_WORKSTREAM_RUN -u HERMES_WORKSTREAM_ROLE -u BTQ_SESSION_ID \
  /usr/bin/python3 -m harness.runtime_repair stage \
  --root /home/operator/.hermes/hermes-agent \
  --uv /home/operator/.hermes/bin/uv \
  --quarantine /home/operator/.local/state/hermes-quarantine/sqlite-runtime-20260917
```

Review `repair.json`, its `package_differences`, and candidate package inventory.
Any removed package, version change or changed direct URL blocks apply. If the
native all-extras sync omits local packages, stop and reconcile the candidate with
reviewed pinned packages; do not weaken the comparison. The native core smoke does
not prove external plugins work: run installed PluginManager smoke checks using
the candidate interpreter before cutover, without launching model workloads.

## Quiet window and cutover

Operator stops gateway and all consumers of the old Hermes venv, including manager,
while retaining Codex worker and durable identity. Prevent supervisor from automatically
restarting the manager during this window. Record active native bindings and verify
old owners have actually exited. `--quiesced` is an operator assertion, not a process
scanner or concurrency lock against arbitrary Hermes writers.

Inventory each affected profile's native SQLite stores and pass every database
explicitly (paths below are examples; verify exact installed paths). The wrapper
uses SQLite backup API, checks integrity and foreign keys, archives venv plus its
base Python, fsyncs backups and manifest before calling native cutover. It never
changes database schemas or journal modes. Runtime archive paths are `venv/` and
`base-python/`; original base path is retained in manifest.

```sh
env -u HERMES_WORKSTREAM_RUN -u HERMES_WORKSTREAM_ROLE -u BTQ_SESSION_ID \
  /usr/bin/python3 -m harness.runtime_repair apply \
  --root /home/operator/.hermes/hermes-agent \
  --uv /home/operator/.hermes/bin/uv \
  --quarantine /home/operator/.local/state/hermes-quarantine/sqlite-runtime-20260917 \
  --database /home/operator/.hermes/state.db \
  --quiesced
```

The old venv remains parked; a separate private archive survives native cleanup.
A staged repeat reuses its manifest; an applied repeat only probes runtime safety.
A crash during cutover leaves intent for manual reconciliation. Failed backup may
leave a partial archive: preserve for inspection, use a fresh reviewed quarantine
and stage record after confirming no cutover occurred. Do not blindly retry deletes.

## Verification and rollback

Verify new venv's Python/SQLite/source ID, package comparison and plugins. Resume
original manager `20260916_182541_e8cb77` after confirming prior PID gone; leave
Codex fork `01a0acf9-dfb2-7d43-93f6-e90472d7c123` unchanged. Start gateway, restore
supervision, verify process executables, no WAL-reset warning in fresh runtime logs,
DB integrity/FKs and preserved session/lineage counts. Repeat apply should not cut
over again. No physical reboot test or replay of coding tasks is required.

If native promotion/import checks fail, helper synchronously restores the parked
venv where possible; inspect manifest detail. For a later plugin/service failure,
quiesce consumers again, park failed new venv and restore old parked directory to
its original `venv` path. If the parked copy is unavailable, verify runtime.tar
SHA256 from manifest, unpack privately, restore `venv` to original path; original
base Python must remain at recorded base_prefix (archive contains a copy if needed).
Never automatically restore DB backups: the repair does not mutate session data,
and rollback must not overwrite subsequent conversations. Keep private backup until
reviewed retention. Root records actual deployment evidence before completion.

## Packaging prerequisite discovered by staging

First staging failed safely at the unchanged native smoke: `hermes_state_holders`
was absent from setuptools' explicit module list. Seven existing state modules were
omitted; root-source execution could mask this, while isolated editable imports expose
it. `prepare_runtime_packaging.py` prepares an exact file-hash guarded manifest adding
only these module declarations. Apply with existing private-backup cleanup tooling:

```sh
/usr/bin/python3 prepare_runtime_packaging.py \
  --root /home/operator/.hermes/hermes-agent \
  --output /tmp/hermes-runtime-packaging.json
/usr/bin/python3 -m harness.cleanup \
  --manifest /tmp/hermes-runtime-packaging.json \
  --quarantine /home/operator/.local/state/hermes-quarantine/sqlite-packaging-20260917
```

Review and commit only native `pyproject.toml`. This is packaging metadata for
existing code, not an upstream update. Native `uv sync --locked` must still pass;
no smoke bypass, PYTHONPATH override or hand-editing installed editable finders.

The first failed attempt left exactly this inspected interpreter:
`/home/operator/.hermes/hermes-agent/.hermes-runtime/python/generation-1789611043-154103-256b7913/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11`.
Retry stage with the same arguments plus `--reuse-python` naming that exact path.
The wrapper verifies fixed SQLite and containment in the native private store.
Subsequent provisioning records are written before building the candidate, so another
smoke failure retains a reusable generation record. No newest-generation guessing or
automatic deletion is performed. An interruption before that record is written still
requires explicit operator inspection/adoption; native temporary candidate cleanup
remains owned by its helper.
