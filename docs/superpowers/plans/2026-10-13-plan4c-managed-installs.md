# heterodyne-metaharness Plan 4c: the managed install store — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ADR r16 §7 Managed installs, for both adapters. The harness keeps its own authenticated, versioned copy of each agent harness, and a sandbox binds only that copy:

- **The store and its helper.** Installs are staged, hashed and published by a small stdlib-only helper that runs as a dedicated store owner, so the service user can't change an install. Without the store owner, the fallback runs the same steps in `wsd`, with the narrower guarantee r16 accepts as risk.
- **The manifest and the verification.** Every install has a manifest pinned by its digest. Before every launch and every host-side use, an fd walk proves that the install is the one the helper hashed: the recorded device, inode and change time, no mount crossing, and a declared version.
- **The launch.** The launcher binds the install's root read-only as one private, non-recursive bind. The Managed install probe proves from inside that the sandbox sees exactly that tree. A launch entry pins its install when the guard journals it.
- **Taint.** A re-walk at each session end and every 10 minutes taints a changed install, stops its sessions as `/stop` does, and alerts the operator.
- **The operator surface.** `wsctl harness list | install | verify | use | rollback | remove | prune`. `install` is the only command that needs an approval: a digest-pinned approval ask through admind's relay.
- **Plan 4b's contract.** `SandboxRuntime.verified_cli`, `HostUsageReader(cli=)`, `build_usage_reader` keyed on the selected install, and the migration of plan 4b's Tasks 1–11 off `SandboxSettings.binaries`.

**Architecture:**

- `src/heterodyne/harness/resources/store_core.py` is the whole helper: the manifest and its canonical encoding, the walk, unpacking and source checks, and `stage`, `publish`, `verify`, `remove` and `sweep`. It is one standard-library file, so root can install a copy of it that the service user can't write, and `wsd` imports the same file for the fallback and for verification. No second implementation of the manifest exists.
- `src/heterodyne/harness/` holds the `wsd` side: the store clients (`HelperStore` over the helper's socket, `LocalStore` for the fallback), recipes, fetching, the selection file and the launch-time verification.
- `src/heterodyne/sandbox/` gets `verified_cli`, the install bind, the probe and the taint watch. `src/heterodyne/wsd/` gets the install pin on launch entries, the `harness` control ops, the approval and the taint handling. `wsd/` still names no adapter: the adapter reaches it only as a profile's data.
- Each adapter declares its versions (with their tool-path variables), its install layout and sources, its content kinds and how each is presented, the keys it owns, its updater switches, and its per-session state.

**Tech Stack:** Python 3.12+, `uv`, pytest, hypothesis, ruff, pyright strict, msgspec (the only runtime dependency, and not used by `store_core.py`), SQLite (the existing journal), `ctypes` for `statx` and the fs-verity ioctl, `tarfile`, `hashlib`, `socket` (`SO_PEERCRED`, `SCM_RIGHTS`).

**Spec:**
- ADR 0001, revision 16 draft (design repo `hermes-workstreams-v2`, branch `adr-r16`, at `93d0e90`, on r15 `28e3df4`): §4.4 D2 and D7, §6.3 `/harness`, §7 Managed installs, Launch self-test (Managed install), §11 Managed installs controls, §12, §15, §17 items 13–15, Appendix A.3.
- Plan 4b (`docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md`, branch `plan/4b`, at `ba51be9`): the section "Plan 4c: what plan 4b consumes", with its migration contract.
- Plan 4 (`docs/superpowers/plans/2026-10-09-plan4-agents-and-sandbox.md`): the runtime, the self-test, `check_credentials`.
- admind's relay (`docs/admind.md` §10): approval asks over `ask.sock`.

**Status:** draft for review. It needs a cross-model design review, then operator approval (a design-approval bead) before any task starts. Task 12 (the host change) also waits for the operator's go on §17 item 13, separately from the plan's approval. Task 1 is research and changes nothing.

## Global Constraints

- **Variables:**
  - `$HZ` is a fresh clone of `Epiphytic/heterodyne-metaharness`, never the live harness or a symlink to it.
  - `$DESIGN_REPO` is the design repo.
  - `$STORE` is the store's path in a test (`tmp_path / "store"`), never `/var/lib/heterodyne`.
- **Install-agnostic:** "Install-agnostic: no install paths, npubs, tokens or relay URLs." `scripts/check_install_agnostic.py` stays clean. The shipped default for `[harnesses] store` is the ADR's `/var/lib/heterodyne/harnesses`, which is a system path, not an install path; the hook is extended to allow exactly that string (Task 5).
- **Tooling:** "Python 3.12+, matching the repo's tooling (pytest, hypothesis, ruff, pyright)." Managed with `uv`. Runtime dependencies stay exactly `msgspec`.
- **`store_core.py` is standard library only** (`src/heterodyne/harness/resources/`, like the in-sandbox resources). It imports nothing from `heterodyne`. A test imports it in a subprocess with `-I` and a `sys.path` of only its own directory, and fails on any other import.
- **Tests stay offline, except `tests/live`.** "Tests never touch the network, a real wn-agent, the real systemctl, the real claude or codex binaries, ~/.claude, or the real beads database. Use fake executables and temp dirs."
  - Fetching is tested with an injected opener that serves fixture artifacts built in `tmp_path`. No test downloads a release.
  - The helper is tested in-process and over a real socketpair in this process. No test needs a second uid: the store-owner checks are tested with an injected `owner_uid` and with `os.geteuid()`, and the "service user can't write" controls run in Task 13 on the host.
  - `tests/live/test_live_managed.py` (Task 13) is skipped unless `HZ_LIVE=1` and `HZ_LIVE_SANDBOX=1` are set. Its helper-mode tests also need `HZ_LIVE_STORE=helper`.
- **Live-test model budget (testing only):** live model calls use Codex `gpt-6-luna` at `low` reasoning effort, and Claude `claude-haiku-4-5-20251001`, with a small context: one short prompt per session, set through the test's own temporary profile config.
- **Never print or commit tokens, npubs or 64-hex values.**
  - A manifest digest is printed only as its 12-hex prefix, or grouped (E14). Tests compare digests as values and never print them.
  - Fixture artifacts are generated in `tmp_path`; no real checksum is committed.
  - Spike notes (Task 1) record which keys and flags exist, never a checksum or a login's content.
- **Never open a login file in research.** Task 1 runs CLIs only with `HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` pointing at a fresh temp dir, and lists directories under `~/.codex/packages` and the npm prefix without opening anything token-like.
- **No sleeps in tests.** The staging sweep, the taint interval and the fetch deadline take an injected `clock`. Tests wait on a condition with `tests/sandbox_env.py:wait_for`.
- **No adapter names in `wsd/`, `sandbox/` or `harness/`.** Adapter differences reach them as `Adapter` data (`heterodyne.agents.registry.ADAPTERS`). `store_core.py` knows no adapter: `wsd` sends it the layout and the login names, and the manifest records them.
- **Fail closed.** "Every recovery path fails closed. Never infer absent, complete or safe from missing or unreadable evidence." In this plan:
  - each of these refuses: an unreadable manifest or record, a walk that can't finish, a `statx` without a mount ID, an unknown entry type, a store path component the service user could change, an unanswered helper, a selection that names no install, an approval that can't be read back, and a taint marker that can't be read;
  - nothing selects the fallback by itself. `[harnesses] fallback = true` is the only way in, and `config check` and `wsctl harness list` say so every time.
- **Never patch out the verification, the probe, the credential check or the approval.** No setting disables them. Tests prove each one refuses a launch.
- **Negative controls name their exact refusal.** Each refusal test asserts the source's own constant (`store_core.ENTRY_CHANGED`, `verify.UNDECLARED_VERSION`, `harness.NOT_APPROVED`), never a hand-typed string, "any exception" or "nonzero". Exit 127 or a missing executable is never a refusal: the test fails. A timeout fails a negative control.
- **Exit statuses are never masked.** Nothing ends in `|| true`, and no `check=False` result goes unread.
- **Cleanup is proven.** A staging directory the helper abandons is removed and confirmed absent with `os.path.lexists`. Every fd `store_core` opens is closed in a `finally`; the walk tests count `/proc/self/fd` before and after.
- **`src/` style:** ruff line length 110, pyright strict. Error messages name keys and checks, never paths, digests or secret values.
- **The pre-commit install-agnostic hook rejects absolute `/home` paths** in committed files. Tests build paths from `tmp_path` or `short_dir()`.
- **Long commands** use `timeout`. The full suite runs in the foreground with `timeout 3600`; it takes about 8 minutes now. Each task's own tests run first. New test files that open a `Journal` close it (`test_wsd_gate`'s file-descriptor count fails otherwise).
- **Review rule:**
  - Every task ends with a review by a **different LLM than the implementer** (cross-model), or by a fresh-context adversarial agent when only one LLM is available.
  - The brief names the diff range and the ADR sections, and asks for `[BLOCKING]` and `[NON-BLOCKING]` findings. Every blocking finding is fixed or rebutted.
  - Review runs at most three cycles. The third asks whether the result is good enough for this stage.
  - Close evidence includes `Code-Review: reviewer=<model> author=<model> mode=<cross-model|adversarial> range=<BASE>..<HEAD>`.
- **Beads** (workstream `heterodyne`): see "Beads", below.
- **Branches:** each task has its own branch from `main` (`p4c-t<N>`) and its own PR. Merge `origin/main`, never rebase, and never amend a pushed commit. Tasks that touch the same file merge in the order the Parallelism section gives.

---

## Settled from the pinned CLIs (2026-10-13)

These were open while r16 was written. They were settled from the pinned binaries' `--help`, from strings in the binaries, from the release metadata, and from directory listings, with `HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` on a fresh temp dir. No login file was opened. What is still unsettled is Task 1.

| # | Question | Finding | Where it lands |
|---|---|---|---|
| F1 | Does the Codex release publish checksums or signatures? | Yes. The `rust-v0.160.0` GitHub release has `codex-package-x86_64-unknown-linux-musl.tar.gz` with a `.sigstore` bundle, and one `codex-package_SHA256SUMS` file for the packages. | E5: the Codex source is the package archive, checked against `SHA256SUMS`. §17 item 14 (trust on first install) is not needed for the Codex CLI. |
| F2 | Does Codex need the `app-server-daemon` package? | No. Under `~/.codex/packages`, the `app-server-daemon` and `standalone` releases of 0.160.0 have the same file list, and their `bin/codex` binaries are byte-identical. The daemon is the same CLI, run as `codex app-server`. | E4: one Codex source. |
| F3 | Codex's release layout and version source. | The release directory holds `codex-package.json` with `layoutVersion`, `version`, `target`, `variant`, `entrypoint` (`bin/codex`), `resourcesDir` (`codex-resources`) and `pathDir` (`codex-path`); and `bin/codex`, `bin/codex-code-mode-host`, `codex-path/rg` and `codex-resources/` (`bwrap`, `zsh`, `voice`). | E4: `cli/` is that tree; the version, target and entry point are read from `codex-package.json`. |
| F4 | Codex's updater switch. | `check_for_update_on_startup` is a config key in 0.160.0, and `in_app_updates` is a stable feature, on by default (`codex features list`). | E9: the generated `config.toml` sets `check_for_update_on_startup = false` and `[features] in_app_updates = false`. |
| F5 | Codex skills location. | The binary names `$CODEX_HOME/skills`, with `.system` and `.curated` under it, and `.agents/skills`. A `skip_host_skill_discovery` feature exists, under development. | E10: skills are single read-only binds at `$CODEX_HOME/skills/<name>`. Task 1 checks that the CLI loads them and still writes `.system` beside them. |
| F6 | Claude Code's layout. | The npm package `@anthropic-ai/claude-code`, version 2.1.286, has a `postinstall` script (`install.cjs`) that copies the native binary from the platform package (`@anthropic-ai/claude-code-linux-x64`, which declares `libc: glibc`) over `bin/claude.exe`. In the operator's install, that copy has two links. | E4: the Claude source is the platform package alone, whose `package.json` holds the version; `cli/` is its `package/` tree, and the entry point is `cli/claude`. No package script runs. |
| F7 | Claude Code's authentication. | The npm registry returns `dist.integrity` (sha512), `dist.shasum` and `dist.signatures` for the platform package. | E5: `dist.integrity` is checked. The registry signature is not verified in v1 (Q2). |
| F8 | Claude Code's content flags. | `--plugin-dir <path>` (repeatable), `--mcp-config <configs...>` and `--strict-mcp-config` exist. `--setting-sources` takes a comma-separated list; plan 4 passes it empty. `--disable-slash-commands` disables all skills. | E10: plugins and MCP through flags. Task 1 checks that `--plugin-dir` still loads under `--setting-sources=`. |
| F9 | Claude Code's updater switch. | The binary reads `DISABLE_AUTOUPDATER` and the `autoUpdates` setting. | E9: `DISABLE_AUTOUPDATER=1` in the launcher's environment and `"autoUpdates": false` in its settings file. |
| F10 | Codex writes into `CODEX_HOME`. | With a temp `CODEX_HOME`, `codex --help` warns that it would create "PATH aliases" (helper binaries) under it, and refuses to under `/tmp`. | E9: anything Codex writes into the synthetic home is never run by the launcher. Task 1 records where the aliases go in a sandbox home. |
| F11 | fs-verity. | The running kernel lists `verity` in `/sys/fs/ext4/features`. `/var/lib` is on ext4. Whether that filesystem has the `verity` feature set needs `tune2fs -l` as root. | Task 12, step 5. |
| F12 | The store owner. | `heterodyne-store` does not exist on the host. | Task 12. |

## Decisions made in this plan (within the ADR; reviewers should check them)

| # | Where the ADR is silent or loose | Choice | Why |
|---|---|---|---|
| E1 | What the helper is, and how root installs it. | `store_core.py`, one standard-library file. Task 12 copies it, at a pinned harness commit, to `/usr/local/libexec/heterodyne/store_core.py` (root, 0644) and runs it with `/usr/bin/python3 -I`. `config check` compares the installed copy's sha256 with the packaged file and reports a difference as a `HELPER_DRIFT` problem (a refusal for launches). | The service user owns the harness's virtualenv, so the helper can't import from it: any module the helper loads from there is code the service user could change. One file shared with `wsd` means one manifest implementation. |
| E2 | The store's layout. | `<store>/<adapter>/<install>/` (0555) is the install. `<store>/<adapter>/.meta/<install>.manifest.json` and `.record.json` (0444) are its manifest and the recorded `(dev, ino, ctime_ns)` per entry. `<store>/.staging/<id>/` (0700) is a tree being built, and `<store>/.staged/<id>.json` (0444) its manifest, so `wsd` can read and summarise it without reading the tree. `<id>` is 32 random hex characters from `secrets.token_hex(16)`. | The ADR keeps the record "in the store but outside the install", and wsd needs the staged manifest to build the ask. The staging tree itself stays unreadable to the service user. |
| E3 | The helper's protocol. | A Unix stream socket, socket-activated with `Accept=yes`: each connection is one helper process. One JSON request line (at most 1 MiB) and up to 16 fds by `SCM_RIGHTS`; one JSON reply line. `SO_PEERCRED` must give the uid in `/etc/heterodyne/store-helper.json` (root, 0644: `{"service_uid": N, "store": "..."}`), else the reply is `PEER_REFUSED` and nothing else is read. Requests: `stage`, `publish`, `verify`, `remove`, `sweep`, `list`. Every request first sweeps staging older than `staging_hours`. | Per-connection processes keep a crash in one request from affecting another, and need no daemon loop. Root-owned configuration means the service user can't change which uid the helper trusts or where it writes. |
| E4 | The install's parts, per adapter. | `cli/`: Codex is the package archive's tree (F3); Claude Code is the platform package's `package/` tree (F6). `content/<kind>/<name>/`: one directory per content item. `defaults/`: one file, in the CLI's own format (`config.toml` for Codex, `settings.json` for Claude Code). The entry point is recorded in the manifest as a path under `cli/`. | F2, F3 and F6. The platform package is exactly what Claude's own installer copies, without its script. |
| E5 | Fetching and checksums. | `wsd` fetches (it has the network; the helper has none and runs with `PrivateNetwork=yes`). Codex: the package archive and `codex-package_SHA256SUMS` from the GitHub release named by the version; the archive's line in `SHA256SUMS` is the checksum. Claude Code: the registry's version document for the platform package, then its tarball; `dist.integrity` is the checksum. `wsd` passes each artifact to the helper as an fd, with the checksum and where it came from, and the helper checks its own copy. The manifest records the checksum, its origin and `signed: false`. | Checksums are what both upstreams publish and what stdlib can check. The checksum and the artifact come over TLS from the same upstream, so the control is that upstream plus the operator's digest-pinned approval, which the ask states (Q2). |
| E6 | Git content sources. | A git source is `{repo, commit}` with a full commit ID, from a remote URL or a local repository outside every login directory. `wsd` runs `git` (never the helper): `git fetch` into a cache under the state directory, then `git archive --format=tar <commit>` and `git cat-file commit <commit>`, both as fds. The helper checks that the commit object hashes to the commit ID (sha1 or sha256, by its length), reads its tree ID, and rebuilds the tree ID from the archive's entries (blobs, modes 100644, 100755 and 120000, sorted trees). A mismatch, a submodule (mode 160000), or any other mode refuses. | The helper runs nothing from an artifact and has no git, yet must check the bytes against the pin. Git's object hashing is a few dozen lines of `hashlib`. |
| E7 | What `stage` refuses. | Absolute names, `..`, empty or `.` components, names with NUL or `/` inside a component, hard links, devices, FIFOs, sockets, links whose target is absolute or that don't resolve inside the install (component by component, with loops caught by a 40-hop bound), any entry whose name equals a login name the request sends (all adapters' `login_names`, from `ADAPTERS`), the bounds (200_000 entries, depth 64; at the limit is accepted), and any tar member type other than a regular file, a directory or a symbolic link. | The ADR's list, made exact. The login names come from `wsd` because `store_core.py` knows no adapter; a test checks that `wsd` always sends the union. |
| E8 | The walk's primitives. | `statx(dirfd, name, AT_SYMLINK_NOFOLLOW, STATX_BASIC_STATS | STATX_MNT_ID)` through `ctypes`, for the mount ID and the nanosecond change time. Directories are opened with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW` relative to their parent's fd, and regular files with `O_RDONLY | O_NOFOLLOW` only when hashing. A `statx` that doesn't return `STATX_MNT_ID` in `stx_mask` refuses (`NO_MOUNT_ID`). | `os.stat` has no mount ID. Linux 5.8 and later return it, and the sandbox runtime is Linux only (S5). |
| E9 | Updater switches and the launcher's keys. | Codex: `check_for_update_on_startup = false` and `[features] in_app_updates = false`, written after the defaults. Claude Code: `DISABLE_AUTOUPDATER=1` in `extra_env` and `"autoUpdates": false` in the launcher's settings file. Each adapter's `owned_keys` lists these with the approval and sandbox modes, the hooks, the status line and the connectors. The launcher writes its own values last, and a test per adapter proves a default that sets every owned key loses. | F4 and F9. The launcher's values always win (§7). |
| E10 | Content presentation. | Claude Code: each `content/plugins/<name>` as a `--plugin-dir`; `content/mcp/<name>.json` files merged into one launcher-written file passed with `--mcp-config` and `--strict-mcp-config`; instructions through `--append-system-prompt-file`. Codex: each `content/skills/<name>` as a read-only bind at `$CODEX_HOME/skills/<name>`; `content/mcp/<name>.toml` merged into the generated `config.toml` under `[mcp_servers]`; instructions as a read-only bind of one file at `$CODEX_HOME/AGENTS.md`. A content kind an adapter doesn't declare refuses the recipe. | F5 and F8. Each mechanism is one the CLI already has; Task 1 confirms both under the launch shapes plan 4 uses. |
| E11 | Content that sets an owned key. | The adapter checks content in `wsd` on the fetched artifacts, before `stage`: a Claude plugin with a `hooks` directory, a `hooks` key in `plugin.json`, a `settings.json`, or an MCP server named like a launcher server refuses with `OWNS_KEY`; a Codex MCP file with any key other than its servers refuses the same way. The check runs again on the published tree at `use`. | §7: neither a default nor any content can set an owned key. The staging tree is unreadable to `wsd`, so the check runs on the bytes `wsd` hands over; the second run checks the bytes the sandbox will see. |
| E12 | Declared versions. | `Adapter.versions: Mapping[str, Declared]`, where `Declared(tool_env: frozenset[str], presentation: frozenset[str])`. `version_pin` and `tool_env` go; `env_allowed` reads `adapter.versions[cli.version].tool_env`. Codex declares `0.160.0`, Claude Code `2.1.286` (the pins S7 ran). | §7 "Environment": the tool-path allowlist is per declared version, and the version is the manifest's. |
| E13 | The approval. | `install` creates a btq `kind:approval` bead in `[harnesses] approval_workstream` through `wsd`'s own queue, then posts it with `AskPost(kind="approval", bead=...)` to admind's `ask.sock`. Its `metadata.ask` has the five fields; `effect` names the adapter, the install name and the grouped digest (E14); `refs` holds one `commit` ref, the harness commit that declares the version. `wsd` journals the staging ID with the bead ID. At each pickup it reads the bead and publishes only when all hold: it is closed with `decision = approve`, `approved_by` is in btq's `policy.approvers`, every `*_digest` equals btq's `ask_digest` recomputed now with no gaps, the effect's digest equals the journaled staged digest, and the journal's bead ID is this bead. Otherwise it waits, and a denial ends the install (`DENIED`). | The relay is the only Marmot approval path until plan 6. btq's `approval_valid` checks design approvals (an ADR ref and a design review), so wsd checks the subset that applies, using btq's own digest. |
| E14 | Showing a digest on a card. | The card shows a manifest or artifact digest as `sha256:` and 16 groups of 4 hex characters, separated by spaces, and `wsd` parses that form back. The ask's text holds no 64-hex run. | admind withholds a card holding a 64-hex run (`secret_scan.IDENTIFIER_VALUES`), and an approval card must be shown whole (§5.9). The grouping is a display format for a non-secret value. Q3 asks the operator to confirm it. |
| E15 | The launch pin. | `LaunchEntry` and the journal's `launches` gain `install` and `install_digest` (journal schema 3, with an upgrade from 2). The guard reads the selection when it builds the entry and journals both in the same transaction. `LaunchSpec` gains `install` and `install_digest`. The runtime verifies exactly that pin. An entry journaled before schema 3 has empty pin fields; dispatching one refuses with `INSTALL_UNPINNED`, which the guard handles as a failed launch, so the next generation pins the current selection. | D2: the pin is set when the entry is journaled, and a replay uses it. An old entry can't be given a pin after the fact. |
| E16 | In use. | An install is in use while any of these hold: a launch entry names it and has no outcome; a launch entry names it, its outcome is `launched` and the runtime still lists that generation; it is its adapter's selection or most recent previous selection; or a `use` or `install` op on it is open. `remove` and `prune` run under the same `wsd` lock as the guard's dispatch. | §7's definition, with the two windows a crash can leave (an entry not yet dispatched, and an op not yet finished). |
| E17 | Taint. | The runtime re-walks each install it has a live session on, at that session's end and from `expire` when the last walk is 600 s old (injected clock). A mismatch writes a taint marker (`<state>/harness-taint/<adapter>/<install>`, 0600, atomically) before anything else, then reports one `InstallTainted(session_key, generation, install)` signal per session on it. `wsd` parks each bead with `hold=True` and the why `INSTALL_TAINTED`, writes one alert file per install, and `verified_cli` refuses a tainted install while the marker exists. Only `verify` (after a full rehash matches) or `remove` deletes the marker. | The marker makes the refusal survive a crash between the walk and `wsd`'s handling. The signal reuses plan 4b's `signals` drain, so `wsd` stays the journal's only writer. |
| E18 | Re-recording after a reboot. | If a device number changes (for example after a reboot renumbers a disk), every entry's `dev` differs and the install taints. `verify` rehashes every file; if every hash matches the manifest, the helper (or `wsd`, under the fallback, with the fallback publish ordering) writes a new record and the taint clears. | The record is what makes the fast walk sound; a full rehash is the only thing that may replace it. |
| E19 | `/harness` through Marmot. | Plan 4c builds the `wsd` control ops and `wsctl harness`. The Marmot `/harness` command is the Marmot surface plan's (roadmap row 6), as a call to the same ops. Until it ships, an operator runs `wsctl harness` through admind's agent, and the install's approval comes through admind's relay, so no step needs a terminal. | §8.1: Marmot-only operation. `wsd` has no Marmot surface yet; building one here would duplicate plan 6. |
| E20 | Fallback ordering at publish. | `LocalStore.publish`: record every entry's `(dev, ino, ctime_ns)`, rehash every file against the approved manifest, `statx` every entry again and refuse with `CHANGED_DURING_PUBLISH` if any recorded value moved, then set the modes, record again (the modes change the change times), and rename into place. A second comparison after the chmod pass checks that only the mode and change time moved. | §7's fallback rule, made exact: the chmod pass itself changes every change time, so the baseline is re-taken after it under the same check. |

## ADR conflicts and gaps, flagged for the operator (not silently resolved)

### Open questions for the operator

| # | Question | Recommendation |
|---|---|---|
| Q1 | **§17 item 13: create the store owner** (the `heterodyne-store` user, `/var/lib/heterodyne/harnesses`, the helper's units), and optionally turn on ext4 `verity` on `/var/lib`'s filesystem. That is Task 12, a root host change. | Yes to both, as r16 recommends. Until then, Tasks 2–11 and 13's fallback tests run without it, and a host uses the fallback only if you set `[harnesses] fallback = true`. |
| Q2 | **Signatures.** v1 checks the checksums both upstreams publish (E5), not Codex's sigstore bundle or npm's registry signature: stdlib can't check either, and the runtime dependency rule allows only `msgspec`. | Accept checksums for v1. The ask says "checksum from the release; signature not checked". A later plan can verify signatures with a host tool `wsd` runs before `stage`, if you want it. |
| Q3 | **The grouped digest on cards** (E14). | Accept. The alternative is to show only the 12-hex prefix, which §5.9 forbids for content the approval binds. |
| Q4 | **Where install approval beads live** (E13). | `[harnesses] approval_workstream = "heterodyne"`. It is a required key; `install` refuses without it. |
| Q5 | **`[adapters.<adapter>].binary`** stays as admind's key (Contradiction 1). | Keep it for admind until §17 item 15's plan. The sandbox stops reading it in Task 8, and `config check` labels it "admind only". |
| Q6 | **The Claude target.** The host runs the glibc build (F6). The musl build would match Codex. | Use `linux-x64` (glibc), what the host runs today. The recipe names the target, so a change is a new install. |

### Contradictions found in the sources

1. ADR §15 says `[adapters.<adapter>].binary` "is no longer accepted", but §17 item 15 leaves admind on the CLIs it runs today, and `admind/settings.py` reads `adapters.<adapter>.binary` for its agent and summarizer. Plan 4c stops the sandbox reading it and keeps admind's use (Q5). The next ADR revision should say "no longer used by the launcher".
2. ADR §7 says the store helper does "four things". It also sweeps old staging (the same paragraph) and lists installs for `wsd` (`wsctl harness list` needs it). E3 adds `sweep` and `list`, which write nothing outside `.staging` and read only `.meta`.
3. ADR §5.9's approvals are `wsd`'s own decision queue (§5.4), which isn't built. E13 uses admind's interim relay, as §5.9's interim exception allows for every approval ask until `wsd` and the gatekeeper ship.
4. Plan 4b's contract writes `verified_cli(adapter)` with no pin. A launch must verify the entry's pin (D2), so the method takes an optional pin (Task 8); with none, it verifies the current selection, which is what the trusted read wants.

## What plan 4c produces for plan 4b

| Plan 4b consumes | Plan 4c task | Produced as |
|---|---|---|
| `SandboxRuntime.verified_cli(adapter) -> Cli`, refusing with `LaunchFailed` and plan 4c's texts | 8 | `verified_cli(adapter: Adapter, pin: InstallPin \| None = None) -> Cli`. With no pin it verifies the current selection. It raises `LaunchFailed` with exactly one of `verify.REFUSALS`. |
| `_cli` becomes a call to it; `SandboxSettings.binaries` is gone | 8 | `_cli(adapter, spec)` calls `verified_cli(adapter, InstallPin(spec.install, spec.install_digest))`. `binaries` is deleted from `SandboxSettings` and `from_config`. |
| `HostUsageReader(accounts, cli, credlocks, scratch_root, lock_wait_seconds, *, env, ...)` with `cli: Callable[[], Cli]` | 8 | The constructor change, and `_locked` calling `cli()` where it called `adapter.locate` and the pin check. An exception from it is `DIAG_READ_FAILED` with its type name. |
| `build_usage_reader(s, env)` returns a reader when Codex has a selected install | 8 | `build_usage_reader` reads the selection (`harness.selection.load`) and passes `lambda: runtime.verified_cli(codex)`. |
| The migration contract | 8 | Every item in plan 4b's list, in the same PR that deletes `binaries` (Task 8's file list). |
| Plan 4's `test_credentials_pass_with_cli_inside_the_chosen_login_dir` becomes a refusal | 8 | `test_a_cli_inside_the_chosen_login_dir_is_refused`, on the new `INSIDE_LOGIN_DIR` text. |
| "plan 4c's launcher bead" (plan 4b Tasks 11b, 12, the close of AU-6) | 8 | The Task 8 bead. |

Task 8 needs plan 4b's Task 6 and Task 8 merged first, so the code it migrates exists. If plan 4b's Task 8 has not merged when plan 4c's Task 8 is ready, plan 4c's Task 8 waits: the order is fixed so that exactly one PR retires `binaries`.

## Parallelism

Tasks 2, 5 and 7 have no dependencies on each other. Task 1 is research and can run at any time, ideally before the design review, so its findings reach the reviewers. Each later task starts once everything it depends on has merged to `main`.

```
Any time:          T1 spike (research, no code)
Wave 1 (parallel): T2 manifest and walk, T5 config and selection, T7 launch pin
Wave 2 (parallel): T3 sources and stage (T2)
                   T6 adapters (T2, T5, T1's findings)
Wave 3:            T4 publish, helper protocol, store clients (T3)
Wave 4:            T8 the launcher (T4, T5, T6, T7, plan 4b T6 and T8)  <- plan 4b's "launcher bead"
Wave 5 (parallel): T9 taint watch (T8)
                   T10 wsd harness commands and approval (T4, T5, T7, T8)
Wave 6:            T11 docs (T9, T10)
Host (operator go): T12 store owner, helper units, verity (T4, and the operator's go on Q1)
Live:              T13 live test (T9, T10, T11; its helper-mode part also T12)
```

| Task | Depends on | Can run beside | Files it shares (merge order) |
|---|---|---|---|
| 1 | none | any | `docs/spikes/s9-managed-installs.md` (new) |
| 2 | none | 5, 7 | none |
| 3 | 2 | 5, 6, 7 | `harness/resources/store_core.py` (after 2) |
| 4 | 3 | 6, 7 | `store_core.py` (after 3) |
| 5 | none | 2, 3, 7 | `sandbox/settings.py` (adds `harnesses`; 8 removes `binaries`), `config/layers.py`, `scripts/check_install_agnostic.py`, `defaults/defaults.toml` |
| 6 | 2, 5 | 3, 4, 7 | `agents/base.py`, `agents/codex.py`, `agents/claude_code.py`, `sandbox/openshell_selftest.py` (`env_allowed` only) |
| 7 | none | 2–6 | `wsd/launches.py`, `wsd/journal.py`, `wsd/upgrade.py`, `wsd/park.py` (`_pin_and_dispatch`), `wsd/runtime.py` (`LaunchSpec`) |
| 8 | 4, 5, 6, 7, plan 4b T6 and T8 | none | `sandbox/runtime.py`, `sandbox/spec.py`, `sandbox/settings.py`, `sandbox/build.py`, `sandbox/hostread.py`, `sandbox/selftest.py`, `sandbox/openshell_selftest.py`, `sandbox/resources/probes.py`, `tests/sandbox_env.py` |
| 9 | 8 | 10 | `sandbox/runtime.py` (after 8), `wsd/runtime.py` (`InstallTainted`) |
| 10 | 4, 5, 7, 8 | 9 | `wsd/ctl.py`, `wsd/cli.py`, `wsd/daemon.py`, `wsd/scheduler.py`, `wsd/park.py` (the taint park; after 9's signal type) |
| 11 | 9, 10 | none | `docs/` |
| 12 | 4, operator go | any | `deploy/` (new unit and setup files), host only |
| 13 | 9, 10, 11 (helper mode: 12) | none | `tests/live/`, `docs/wsd.md` |

## Beads

All in workstream `heterodyne`. Branches are `p4c-t<N>`.

- **Kinds and gates:**
  - Task 1 is `kind:research`. It changes no code and needs no design approval.
  - Tasks 2–11 and 13 are `kind:task`. Each carries `metadata.design_approval=<this plan's approval bead>`, `metadata.adr_revision=<the approved r16 commit>`, and a blocking dependency on the approval bead.
  - Task 12 is `kind:task` with the same metadata, plus a blocking dependency on a separate **host-change approval bead** (`kind:approval`) for §17 item 13 (Q1). The plan's approval does not authorize it.
- **Dependencies** (each "A ← B" means B blocks on A):

```
approval(plan 4c) ─┬─ T2 ─┬─ T3 ── T4 ─┬───────────────┐
                   │      └─────────┐  │               │
                   ├─ T5 ───────────┼──┼── T6 ──┐      │
                   ├─ T7 ───────────┼──┼────────┼──────┤
                   │                │  │        │      │
plan 4b T6, T8 ────┼────────────────┴──┴── T8 ◄─┘      │
                   │                        ├── T9 ──┐ │
                   │                        └── T10 ◄┼─┘ (T10 also on T4, T5, T7)
                   │                                 └── T11 ── T13
approval(host change, §17 item 13) ── T12 (also on T4) ─────────┘ (helper-mode tests only)
T1 (research): no blockers; T6 reads its findings
```

- **Plan 4b's beads that wait on plan 4c:** plan 4b Task 11b and Task 12 block on plan 4c Task 8's bead, and AU-6 (btq-bc7yd) closes only after it, as plan 4b says.
- **The ADR items:** r16's §7 Managed installs work is closed when Tasks 2–11 and 13 have closed. Task 12's bead closes with the host evidence (Task 12, step 7).

## File map

| File | Tasks | Responsibility |
|---|---|---|
| `src/heterodyne/harness/__init__.py` (new) | 2 | the package |
| `src/heterodyne/harness/resources/store_core.py` (new, stdlib only) | 2, 3, 4 | the manifest, its encoding and digest; `statx`; the walk; the record and the fast verification; unpacking, checksums and git trees; `stage`, `publish`, `verify`, `remove`, `sweep`, `list`; the socket server and `main` |
| `src/heterodyne/harness/store.py` (new) | 4 | `Store` protocol; `HelperStore` (the socket client); `LocalStore` (the fallback, E20); `open_store(settings)` |
| `src/heterodyne/harness/settings.py` (new) | 5 | `HarnessSettings`: `store`, `fallback`, `owner`, `helper_socket`, `helper_copy`, `staging_hours`, `approval_workstream`; the recipes |
| `src/heterodyne/harness/selection.py` (new) | 5 | `harnesses.toml`: `Selection`, `InstallPin`, `load`, `write` (atomic, 0600) |
| `src/heterodyne/harness/paths.py` (new) | 5 | `store_problems(store, logins, worktrees, state, service_uid, owner_uid)`: the location and path-component checks |
| `src/heterodyne/harness/fetch.py` (new) | 3 | fetching artifacts and checksums; git fetch and archive; an injected opener |
| `src/heterodyne/harness/verify.py` (new) | 8 | `VerifiedInstall`, `verify_pin(store, adapter, pin, declared, taint_dir)`, `REFUSALS` |
| `src/heterodyne/config/layers.py` | 5 | `[harnesses]` and `[harnesses.<adapter>]` as host-only tables |
| `src/heterodyne/cli.py` (`config check`) | 5 | the store checks, the selection with its source, the fallback warning, `HELPER_DRIFT` |
| `src/heterodyne/defaults/defaults.toml` | 5 | `[harnesses]` defaults; the `[adapters.*] binary` comment says "admind only" |
| `scripts/check_install_agnostic.py` | 5 | allow exactly `/var/lib/heterodyne/harnesses` |
| `src/heterodyne/agents/base.py` | 6, 8 | `Declared`, `InstallLayout`, `Presentation`; `Adapter.versions`, `.layout`, `.content_kinds`, `.owned_keys`, `.check_content`, `.present`; `Cli.install` and `.digest`; `locate`, `version_pin`, `tool_env`, `find_binary` and `install_root` removed |
| `src/heterodyne/agents/codex.py`, `src/heterodyne/agents/claude_code.py` | 6 | the layouts, declared versions, presentation, owned keys, content checks, updater switches |
| `src/heterodyne/wsd/launches.py`, `src/heterodyne/wsd/journal.py`, `src/heterodyne/wsd/upgrade.py` | 7, 10 | the pin fields, schema 3, the 2-to-3 upgrade; the `harness_installs` and `harness_events` tables |
| `src/heterodyne/wsd/runtime.py` | 7, 9 | `LaunchSpec.install` and `.install_digest`, `INSTALL_UNPINNED`; `InstallTainted` in `Signal` |
| `src/heterodyne/wsd/park.py` | 7, 10 | the pin in `_pin_and_dispatch`; the taint park |
| `src/heterodyne/sandbox/runtime.py` | 8, 9 | `verified_cli`, `_cli`, the install bind; the taint watch and markers |
| `src/heterodyne/sandbox/spec.py` | 8 | `AgentFacts.install_root`, the private non-recursive bind flag, `INSIDE_LOGIN_DIR` |
| `src/heterodyne/sandbox/openshell.py` | 8 | the bind's `rprivate`/non-recursive options in the OpenShell policy |
| `src/heterodyne/sandbox/settings.py` | 5, 8 | 5: `harnesses`; 8: `binaries` removed |
| `src/heterodyne/sandbox/build.py`, `src/heterodyne/sandbox/hostread.py` | 8 | the store wiring; plan 4b's migration |
| `src/heterodyne/sandbox/selftest.py`, `src/heterodyne/sandbox/openshell_selftest.py`, `src/heterodyne/sandbox/resources/probes.py` | 8 | `ProbeContext.install`; the `managed-install` check |
| `src/heterodyne/wsd/harness.py` (new) | 10 | `HarnessOps`: list, install, advance (publish on approval), verify, use, rollback, remove, prune, in use, taint |
| `src/heterodyne/wsd/alerts.py` (new) | 10 | the alert-file writer (admind's `alerts.py` contract) |
| `src/heterodyne/wsd/ctl.py`, `src/heterodyne/wsd/cli.py`, `src/heterodyne/wsd/daemon.py`, `src/heterodyne/wsd/scheduler.py` | 10 | the `harness` op, `wsctl harness`, the wiring, `advance` at pickup |
| `deploy/store-helper/` (new) | 12 | `heterodyne-store-helper.socket`, `heterodyne-store-helper@.service`, `setup-store.sh`, `README.md` |
| `tests/harness_env.py` (new) | 2–4, 8 | fixture artifacts, `store_fixture(root, adapter)`, `selected_install(...)`: the store fixture plan 4b's migration uses |
| `tests/test_harness_*.py` (new) | 2–5, 10 | per task, below |
| `tests/sandbox_env.py`, `tests/test_sandbox_*.py`, `tests/live/*.py` | 8 | the migration off `binaries` |
| `docs/configuration.md`, `docs/wsd.md`, `docs/security-model.md`, `docs/install.md` | 11, 13 | the store, the commands, the fallback risk, the host setup |
| `docs/spikes/s9-managed-installs.md` (new) | 1 | the spike's findings |

---

## Task 1: spike — content and updaters on the pinned versions (research)

Settle what the help output couldn't. This task writes only `docs/spikes/s9-managed-installs.md`. It never opens a login file and never uses a real account: every run is offline or fails at authentication, which is enough to show what the CLI loads.

**Files:**
- Create: `docs/spikes/s9-managed-installs.md`

**Interfaces:**
- Produces: findings S9-1 to S9-7, each "confirmed", "refuted" or "changed", with the exact flag or key and the command that showed it. Task 6 implements what they say. A refuted item stops Task 6 for that content kind, and the kind is removed from the adapter's `content_kinds` until a later plan.

- [ ] **Step 1: Set up a throwaway home and copies**

```bash
T=$(mktemp -d); export HOME=$T CODEX_HOME=$T/.codex CLAUDE_CONFIG_DIR=$T/.claude XDG_CONFIG_HOME=$T/.config
mkdir -p "$CODEX_HOME" "$CLAUDE_CONFIG_DIR"
```

Copy the pinned releases into `$T/store` by listing and `cp -a` only `bin/`, `codex-path/`, `codex-resources/` and `codex-package.json` (Codex), and the platform package's `claude`, `package.json` (Claude Code). Never copy anything from a login directory.

- [ ] **Step 2: Run the checks**

| # | Check | How |
|---|---|---|
| S9-1 | The Codex package archive's tree equals the release directory layout (F3). | `gh release download rust-v0.160.0 -R openai/codex -p 'codex-package-x86_64-unknown-linux-musl.tar.gz' -p 'codex-package_SHA256SUMS'`, check the sum with `sha256sum -c --ignore-missing`, then `tar -tzf` and compare with the release directory's `find`. Record the archive's top-level prefix (the `strip` value). |
| S9-2 | Claude Code loads `--plugin-dir` under `--setting-sources=`. | A plugin with one command; `claude --setting-sources= --plugin-dir "$T/p" -p "/help" </dev/null` and the `--debug` output's plugin lines. A login failure after loading is fine. |
| S9-3 | Claude Code's `--mcp-config` with `--strict-mcp-config` under `--setting-sources=`, and that `ENABLE_CLAUDEAI_MCP_SERVERS=false` still holds. | `claude mcp list` with the flags, in the temp home. |
| S9-4 | Codex loads a skill bound read-only at `$CODEX_HOME/skills/<name>`, and still writes its `.system` skills beside it. | `chmod -R a-w` the skill directory, run `codex exec --skip-git-repo-check 'list your skills' </dev/null` (the login failure ends it), then list `$CODEX_HOME/skills`. |
| S9-5 | Codex's updater keys silence the startup check. | Run with and without `-c check_for_update_on_startup=false -c features.in_app_updates=false` under `strace -f -e trace=connect` (no network allowed: `unshare -rn`), and compare the attempts. |
| S9-6 | Claude Code's `DISABLE_AUTOUPDATER=1` and `"autoUpdates": false`. | The same comparison, with `--debug`. |
| S9-7 | Where Codex puts its PATH aliases in a home outside `/tmp` (F10), and whether it runs them. | `CODEX_HOME=$HOME/.codex` under a home made with `mktemp -d -p "$XDG_RUNTIME_DIR"`; list what it creates. |

- [ ] **Step 3: Write the findings**

Write `docs/spikes/s9-managed-installs.md` with one section per check: the command (with temp paths shown as `$T`), what it showed, and the verdict. No checksums, no tokens, no home paths.

- [ ] **Step 4: Commit**

```bash
git add docs/spikes/s9-managed-installs.md
git commit -m "plan4c T1: S9 spike, content and updaters on the pinned CLIs"
```

## Task 2: the manifest, its digest and the walk

The core every other task stands on, in `store_core.py`.

**Files:**
- Create: `src/heterodyne/harness/__init__.py`, `src/heterodyne/harness/resources/__init__.py` (empty), `src/heterodyne/harness/resources/store_core.py`
- Create: `tests/harness_env.py`, `tests/test_harness_manifest.py`, `tests/test_harness_walk.py`

**Interfaces:**
- Consumes: nothing.
- Produces (all in `store_core`):
  - `MAX_ENTRIES = 200_000`, `MAX_DEPTH = 64`, `MAX_LINK_HOPS = 40`.
  - `Entry(path: str, kind: Literal["dir", "file", "link"], mode: int, size: int, nlink: int, sha256: str | None, target: str | None)`, a frozen dataclass. `path` is relative, `/`-separated, never empty (the root isn't an entry).
  - `Source(part: str, kind: Literal["release", "npm", "git"], name: str, ref: str, checksum: str, origin: str, signed: bool)`.
  - `Manifest(adapter: str, version: str, target: str, entry_point: str, sources: tuple[Source, ...], entries: tuple[Entry, ...], format: int = 1)`.
  - `canonical(m: Manifest) -> bytes`: UTF-8 JSON, sorted keys, `separators=(",", ":")`, `ensure_ascii=False`, entries sorted by their path's UTF-8 bytes; and `digest(m) -> str`, `"sha256:" + hexdigest`. `parse(data: bytes) -> Manifest` refuses anything `canonical` would not produce (`MANIFEST_MALFORMED`).
  - `Stat(kind, mode, size, nlink, dev, ino, ctime_ns, mnt_id)` and `statx_at(dirfd: int, name: str) -> Stat`.
  - `walk(root_fd: int) -> Iterator[tuple[str, Stat, str | None]]`: depth-first, sorted, following no link, bounded; refuses with `WalkRefused(<constant>)`.
  - `Record = dict[str, tuple[int, int, int]]` (path to dev, ino, ctime_ns) plus the root's own triple under `""`; `record(root_fd) -> Record`.
  - `check_fast(root_fd: int, m: Manifest, rec: Record) -> None`: the walk equals the manifest (types, modes, sizes, link counts, link targets) and the record, and every entry's `mnt_id` equals the root's. Raises `Mismatch(<constant>)`.
  - `rehash(root_fd: int, m: Manifest) -> None`: every regular file's sha256, read by fd; raises `Mismatch(CONTENT_CHANGED)`.
  - Refusal constants: `TOO_MANY_ENTRIES`, `TOO_DEEP`, `BAD_TYPE`, `HARD_LINK`, `BAD_LINK`, `NO_MOUNT_ID`, `CROSSES_MOUNT`, `ENTRY_CHANGED`, `ENTRY_ADDED`, `ENTRY_MISSING`, `CONTENT_CHANGED`, `MANIFEST_MALFORMED`.

- [ ] **Step 1: Write the failing tests**

`tests/harness_env.py` builds trees: `make_tree(root, spec)` from a dict of path to bytes, `("link", target)` or `("dir",)`; `manifest_of(root)` through `store_core`; `fd_count()` from `/proc/self/fd`.

`tests/test_harness_manifest.py`:

```python
"""Plan 4c Task 2: the manifest's canonical encoding and digest (r16 §7 Authentication)."""

from hypothesis import given, strategies as st

from heterodyne.harness.resources import store_core as sc
from tests.harness_env import sample_manifest


def test_the_digest_is_the_sha256_of_the_canonical_encoding() -> None:
    m = sample_manifest()
    import hashlib
    assert sc.digest(m) == "sha256:" + hashlib.sha256(sc.canonical(m)).hexdigest()


@given(st.permutations(range(5)))
def test_entry_order_does_not_change_the_digest(order: list[int]) -> None:
    m = sample_manifest()
    shuffled = m.__class__(**{**m.__dict__, "entries": tuple(m.entries[i] for i in order)})
    assert sc.digest(shuffled) == sc.digest(m)


def test_any_field_change_changes_the_digest() -> None: ...           # every Entry and Manifest field
def test_parse_round_trips_and_refuses_a_non_canonical_encoding() -> None: ...   # MANIFEST_MALFORMED
def test_non_ascii_paths_are_encoded_as_utf8_bytes_and_sorted_by_them() -> None: ...
```

`tests/test_harness_walk.py` covers, each against its constant:
- `test_a_tree_walks_to_its_manifest_and_the_fast_check_passes`;
- `test_exactly_at_the_entry_limit_is_accepted_and_one_more_is_refused` (with `MAX_ENTRIES` monkeypatched to 50, and the root uncounted);
- `test_exactly_at_the_depth_limit_is_accepted_and_one_deeper_is_refused` (`MAX_DEPTH` 64, real depth);
- `test_a_fifo_a_socket_and_a_device_are_refused` (`BAD_TYPE`; the device case uses `os.mknod` only where permitted, else is checked through an injected `Stat`);
- `test_a_hard_link_is_refused` (`HARD_LINK`);
- `test_links_that_dangle_loop_leave_or_are_absolute_are_refused` (`BAD_LINK`, one case each; a link to a link inside resolves);
- `test_an_added_removed_renamed_or_rewritten_entry_fails_the_fast_check` (`ENTRY_ADDED`, `ENTRY_MISSING`, `ENTRY_CHANGED`; a same-size rewrite moves the change time);
- `test_a_change_during_the_walk_fails_it` (a `statx_at` wrapper that rewrites a later file the first time it is called);
- `test_a_same_filesystem_bind_mount_inside_is_refused` (`CROSSES_MOUNT`; uses `unshare -rm` and `mount --bind` in a subprocess, skipped with its reason where user namespaces are off, and run in CI where they are on);
- `test_rehash_finds_content_that_kept_its_size_and_metadata` (writes through an injected reader, since an unprivileged process can't restore a change time);
- `test_the_walk_closes_every_fd_it_opens` (`fd_count` before and after, also after a refusal);
- `test_store_core_imports_only_the_standard_library` (runs `python3 -I -c "import store_core"` with only its directory on `sys.path`, and checks `sys.modules` for anything outside the standard library).

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 300 uv run pytest tests/test_harness_manifest.py tests/test_harness_walk.py -q`
Expected: FAIL (no module `heterodyne.harness`).

- [ ] **Step 3: Implement**

The primitives, as they will be written:

```python
# statx(2) through libc: the mount ID and the change time in nanoseconds, which os.stat lacks (plan 4c E8).
AT_SYMLINK_NOFOLLOW = 0x100
STATX_BASIC_STATS = 0x7FF
STATX_MNT_ID = 0x1000


class _Ts(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_uint32), ("_r", ctypes.c_int32)]


class _Statx(ctypes.Structure):
    _fields_ = [("mask", ctypes.c_uint32), ("blksize", ctypes.c_uint32), ("attributes", ctypes.c_uint64),
                ("nlink", ctypes.c_uint32), ("uid", ctypes.c_uint32), ("gid", ctypes.c_uint32),
                ("mode", ctypes.c_uint16), ("_s0", ctypes.c_uint16), ("ino", ctypes.c_uint64),
                ("size", ctypes.c_uint64), ("blocks", ctypes.c_uint64), ("attributes_mask", ctypes.c_uint64),
                ("atime", _Ts), ("btime", _Ts), ("ctime", _Ts), ("mtime", _Ts),
                ("rdev_major", ctypes.c_uint32), ("rdev_minor", ctypes.c_uint32),
                ("dev_major", ctypes.c_uint32), ("dev_minor", ctypes.c_uint32),
                ("mnt_id", ctypes.c_uint64), ("_pad", ctypes.c_uint64 * 13)]


def statx_at(dirfd: int, name: str) -> Stat:
    buf = _Statx()
    if _libc.statx(dirfd, os.fsencode(name), AT_SYMLINK_NOFOLLOW, STATX_BASIC_STATS | STATX_MNT_ID,
                   ctypes.byref(buf)) != 0:
        raise OSError(ctypes.get_errno(), "statx")
    if not buf.mask & STATX_MNT_ID:
        raise WalkRefused(NO_MOUNT_ID)
    return Stat(_kind(buf.mode), stat.S_IMODE(buf.mode), buf.size, buf.nlink,
                os.makedev(buf.dev_major, buf.dev_minor), buf.ino,
                buf.ctime.tv_sec * 1_000_000_000 + buf.ctime.tv_nsec, buf.mnt_id)
```

`walk` keeps a stack of `(dirfd, relpath, depth)`, lists each directory with `os.scandir(dirfd)` (names only; every fact comes from `statx_at`), sorts names by their bytes, counts every entry against `MAX_ENTRIES`, refuses a depth over `MAX_DEPTH`, and opens subdirectories with `os.open(name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=dirfd)`. After the walk, `_check_links` resolves every link component by component against the walked set (no filesystem access), with `MAX_LINK_HOPS`. `check_fast` walks and compares in one pass, and `statx`es the root fd itself again at the end, so a change to the root during the walk is caught.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_harness_manifest.py tests/test_harness_walk.py -q`
Expected: PASS; the bind-mount test passes or skips with "user namespaces are unavailable".

Then: `timeout 300 uv run pyright src/heterodyne/harness tests/test_harness_manifest.py tests/test_harness_walk.py` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/harness tests/harness_env.py tests/test_harness_manifest.py tests/test_harness_walk.py
git commit -m "plan4c T2: the install manifest, its digest and the fd walk"
```

## Task 3: sources and `stage`

**Files:**
- Modify: `src/heterodyne/harness/resources/store_core.py`
- Create: `src/heterodyne/harness/fetch.py`
- Test: `tests/test_harness_stage.py`, `tests/test_harness_fetch.py`, `tests/test_harness_gittree.py` (all new)

**Interfaces:**
- Consumes: Task 2's walk and manifest.
- Produces:
  - `store_core.Layout(version_file: str, version_field: str, target_field: str | None, entry_field: str | None, entry_point: str | None)`: where the version, target and entry point are read, under `cli/`. Exactly one of `entry_field` and `entry_point` is set.
  - `store_core.Part(part: str, format: Literal["tar.gz", "tgz", "tar", "file"], strip: str, checksum: str, origin: str, signed: bool, source: Source, git_commit: str | None)`. `part` is `cli`, `content/<kind>/<name>` or `defaults`.
  - `store_core.stage(store: Path, adapter: str, layout: Layout, parts: Sequence[Part], fds: Sequence[int], login_names: frozenset[str], *, verity: bool) -> Staged`, where `Staged(id: str, digest: str, entries: int, size: int)`. It writes `<store>/.staging/<id>/` and `<store>/.staged/<id>.json`. A git part takes two fds, the archive and the commit object.
  - `store_core.verify_checksum(fd, checksum) -> None`: `sha256:<hex>` or `sha512-<base64>` (SRI); `CHECKSUM_MISMATCH`.
  - `store_core.git_tree_id(members) -> str` and `store_core.check_git(archive_fd, commit_fd, commit_id) -> None`; `GIT_MISMATCH`, `GIT_SUBMODULE`.
  - `store_core.enable_verity(fd) -> bool`: `FS_IOC_ENABLE_VERITY` with sha256 and 4096-byte blocks; `False` on `EOPNOTSUPP` or `ENOTTY`, any other error raises.
  - New constants: `BAD_NAME`, `LOGIN_NAME`, `CHECKSUM_MISMATCH`, `GIT_MISMATCH`, `GIT_SUBMODULE`, `BAD_MEMBER`, `NO_VERSION`, `BAD_PART`.
  - `fetch.Fetcher(opener, cache: Path, clock, deadline_seconds)` with `release(url_base, asset, sums_asset) -> Artifact`, `npm(name, version) -> Artifact` and `git(repo, commit) -> GitArtifact`, where `Artifact(path, checksum, origin)` is a 0600 temp file in `cache` and `GitArtifact(archive, commit_object, commit)`. The opener is `urllib.request.urlopen` in production and a fixture server in tests.

- [ ] **Step 1: Write the failing tests**

`tests/test_harness_stage.py`:
- `test_a_codex_like_release_stages_with_its_version_target_and_entry_point_from_its_metadata`;
- `test_a_claude_like_package_stages_with_its_version_from_package_json_and_a_fixed_entry_point`;
- `test_every_regular_file_in_staging_is_a_new_inode_with_one_link` (compare inodes with the artifact's unpacked copy in another dir);
- `test_absolute_dotdot_empty_and_nul_names_are_refused` (`BAD_NAME`, one tar per case);
- `test_tar_hard_links_devices_fifos_and_other_member_types_are_refused` (`BAD_MEMBER`);
- `test_a_member_named_as_any_login_file_is_refused` (`LOGIN_NAME`, for each of `auth.json` and `.credentials.json`, at any depth);
- `test_a_checksum_mismatch_refuses_and_leaves_no_staging` (`CHECKSUM_MISMATCH`; `.staging` and `.staged` are empty afterwards, by `os.path.lexists`);
- `test_the_staged_manifest_is_readable_by_others_and_the_tree_is_not` (modes 0444 and 0700);
- `test_bounds_apply_at_stage` (as in Task 2, through `stage`);
- `test_no_package_script_runs` (a `package.json` with a `postinstall` that would create a file; the file never appears);
- `test_verity_is_enabled_where_supported_and_recorded_off_where_not` (an injected ioctl).

`tests/test_harness_gittree.py`:
- `test_the_tree_id_matches_git_for_a_real_repo` (builds a repo in `tmp_path` with a file, an executable, a link and a nested dir, runs `git archive` and `git cat-file commit`, and compares with `git rev-parse HEAD^{tree}`);
- `test_a_changed_archive_or_commit_object_is_refused` (`GIT_MISMATCH`);
- `test_a_submodule_is_refused` (`GIT_SUBMODULE`);
- `test_a_sha256_repository_is_checked_too` (`git init --object-format=sha256`, skipped with its reason if the host git lacks it).

`tests/test_harness_fetch.py`: the fixture opener serves a release with a `SHA256SUMS`, an npm version document and its tarball. `test_a_release_asset_gets_its_line_from_sha256sums`, `test_an_asset_missing_from_sha256sums_is_refused`, `test_npm_uses_dist_integrity`, `test_a_deadline_ends_a_stalled_fetch` (injected clock), `test_a_git_source_must_be_a_full_commit_id`, `test_a_local_git_source_inside_a_login_dir_is_refused`.

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 300 uv run pytest tests/test_harness_stage.py tests/test_harness_gittree.py tests/test_harness_fetch.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`stage` reads each part's fd into a staging-private copy first (`os.sendfile` into `<id>.src/<n>`), checks the checksum on that copy, then unpacks from it, so the bytes checked are the bytes unpacked. Tar members are read with `tarfile.open(fileobj=..., mode="r:*")` and written with `os.open(..., O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, dir_fd=...)`; `tarfile.extract` is never used. A member path is checked component by component before any write. `strip` must be a prefix of every member; a member outside it refuses with `BAD_PART`. After unpacking, `stage` reads the version from `layout`, walks the tree (Task 2), hashes every file, writes the manifest to `.staged/<id>.json` by `O_EXCL` then `fsync`, and returns. Any exception removes `.staging/<id>` and `.staged/<id>.json` before it propagates.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_harness_stage.py tests/test_harness_gittree.py tests/test_harness_fetch.py tests/test_harness_walk.py -q`
Expected: PASS.

Then pyright and ruff as in Task 2.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/harness tests/test_harness_stage.py tests/test_harness_gittree.py tests/test_harness_fetch.py \
        tests/harness_env.py
git commit -m "plan4c T3: sources, checksums, git trees and stage"
```

## Task 4: `publish`, `verify`, `remove`, the helper socket and the store clients

**Files:**
- Modify: `src/heterodyne/harness/resources/store_core.py`
- Create: `src/heterodyne/harness/store.py`
- Test: `tests/test_harness_publish.py`, `tests/test_harness_helper.py`, `tests/test_harness_store.py` (all new)

**Interfaces:**
- Consumes: Task 3's `stage`.
- Produces:
  - `store_core.publish(store, staging_id, approved_digest, *, owner_mode: bool) -> str` (the install name `<version>-<12 hex>`): refuses `DIGEST_MISMATCH` unless the staged manifest's digest equals `approved_digest`; sets modes (dirs 0555, files 0444 or 0555); records; renames `.staging/<id>` to `<adapter>/<install>`; writes `.meta/<install>.manifest.json` and `.record.json`; removes `.staged/<id>.json`. With `owner_mode=False` it follows E20 and can refuse `CHANGED_DURING_PUBLISH`. A name that exists refuses `ALREADY_PUBLISHED`.
  - `store_core.verify(store, adapter, install) -> None`: rehash plus fast check; on a rehash that matches with only `dev` values changed, rewrites the record (E18) and returns.
  - `store_core.remove(store, adapter, install) -> None`: makes the tree writable by its owner, removes it bottom-up by fd, then the `.meta` files.
  - `store_core.sweep(store, now, hours) -> int`: removes staging older than `hours`.
  - `store_core.list_installs(store) -> list[dict[str, object]]`: name, adapter, version, digest, entries, size, from `.meta`.
  - `store_core.serve(conn: socket.socket, config: HelperConfig, clock) -> None`: one connection; `SO_PEERCRED` first (`PEER_REFUSED`); requests as E3. `store_core.main(argv)`: `serve` (`sys.stdin` is the accepted socket under `Accept=yes`) reading `/etc/heterodyne/store-helper.json`, or the path in `--config` for tests.
  - `harness.store.Store` protocol: `stage(adapter, layout, parts, fds, login_names) -> Staged`, `publish(staging_id, digest) -> str`, `verify(adapter, install) -> None`, `remove(adapter, install) -> None`, `list() -> list[InstallInfo]`, `staged_manifest(staging_id) -> Manifest`, `root: Path`, `fallback: bool`.
  - `HelperStore(socket_path, store_root, timeout)` and `LocalStore(root)`; `open_store(h: HarnessSettings) -> Store`. A helper error comes back as `StoreRefused(<constant>)` with the helper's constant, never its text.

- [ ] **Step 1: Write the failing tests**

`tests/test_harness_publish.py`:
- `test_publish_with_the_staged_digest_names_the_install_and_sets_its_modes`;
- `test_publish_with_another_digest_is_refused_and_staging_is_kept` (`DIGEST_MISMATCH`);
- `test_the_record_matches_a_fresh_walk_and_lies_outside_the_install`;
- `test_fallback_publish_refuses_a_same_size_rewrite_during_the_rehash` (`CHANGED_DURING_PUBLISH`, by a hook that rewrites a file between the first record and the rehash);
- `test_verify_finds_a_rewrite_and_a_matching_rehash_after_a_device_change_rerecords` (E18, with an injected `Stat` for the device);
- `test_remove_leaves_nothing_and_list_no_longer_shows_it`;
- `test_sweep_removes_only_staging_older_than_its_hours` (injected clock);
- `test_publishing_twice_is_refused` (`ALREADY_PUBLISHED`).

`tests/test_harness_helper.py`, over `socket.socketpair()` with `serve` in a thread and an injected peer check:
- `test_a_peer_with_another_uid_gets_peer_refused_and_nothing_is_read` (the injected `SO_PEERCRED` returns another uid; no fd is received);
- `test_stage_over_the_socket_takes_artifact_fds_and_returns_the_digest`;
- `test_a_request_over_the_size_limit_or_with_too_many_fds_is_refused`;
- `test_the_helper_never_executes_from_an_artifact` (an artifact with an executable named like a helper tool; `serve` is run under an audit hook, `sys.addaudithook`, that fails on `subprocess.Popen` and `os.exec*`);
- `test_main_reads_its_config_and_refuses_a_store_outside_it`.

`tests/test_harness_store.py`: `HelperStore` against a `serve` thread on a Unix socket in `short_dir()`, and `LocalStore` against a temp store, running one shared contract suite (`@pytest.mark.parametrize("kind", ["helper", "local"])`): stage, staged manifest, publish, list, verify, remove.

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 300 uv run pytest tests/test_harness_publish.py tests/test_harness_helper.py tests/test_harness_store.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`serve` reads with `socket.recv_fds(conn, MAX_REQUEST, MAX_FDS)`, decodes one JSON line, dispatches, and replies `{"ok": true, ...}` or `{"ok": false, "refused": "<constant>"}`. Any received fd is closed in a `finally`. An unexpected exception replies `{"ok": false, "refused": "HELPER_FAILED", "type": "<type name>"}`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 uv run pytest tests/test_harness_*.py -q`
Expected: PASS.

Then pyright and ruff.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/harness tests/test_harness_publish.py tests/test_harness_helper.py tests/test_harness_store.py
git commit -m "plan4c T4: publish, verify, remove, the helper socket and the store clients"
```

## Task 5: configuration, the selection file and the store's location checks

**Files:**
- Create: `src/heterodyne/harness/settings.py`, `src/heterodyne/harness/selection.py`, `src/heterodyne/harness/paths.py`
- Modify: `src/heterodyne/config/layers.py`, `src/heterodyne/cli.py`, `src/heterodyne/defaults/defaults.toml`, `src/heterodyne/sandbox/settings.py` (adds `harnesses: HarnessSettings`), `scripts/check_install_agnostic.py`
- Test: `tests/test_harness_settings.py`, `tests/test_harness_selection.py`, `tests/test_harness_paths.py` (new), `tests/test_config_check.py`

**Interfaces:**
- Consumes: `config.layers.table_at`, `string_list`; `config.paths.config_dir`, `state_dir`; `Accounts.login_paths`.
- Produces:
  - `HarnessSettings(store: Path, fallback: bool, owner: str, helper_socket: Path, helper_copy: Path, staging_hours: int, approval_workstream: str, recipes: Mapping[str, Recipe])`. Host keys (`[harnesses]`): `store` (default `/var/lib/heterodyne/harnesses`; under `fallback = true` the default is `${XDG_DATA_HOME:-~/.local/share}/heterodyne/harnesses`), `fallback` (default false), `owner` (default `heterodyne-store`), `helper_socket` (default `/run/heterodyne-store/helper.sock`), `helper_copy` (default `/usr/local/libexec/heterodyne/store_core.py`), `staging_hours` (1–168, default 24), `approval_workstream` (no default). A workstream layer that sets any of them is refused.
  - `Recipe(version: str, target: str, content: tuple[ContentSource, ...], defaults: str | None)` from `[harnesses.<adapter>]`; `ContentSource(kind, name, git: str | None, commit: str | None, npm: str | None, version: str | None)`. A commit must be 40 or 64 hex; a package version must be exact (`\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?`).
  - `selection.InstallPin(install: str, digest: str)`, `Selection(adapter, current: InstallPin | None, previous: tuple[InstallPin, ...])`, `load(config_dir) -> Mapping[str, Selection]`, `write(config_dir, selections)` (temp file 0600, `fsync`, rename, `fsync` the directory). The file is `harnesses.toml`: `[<adapter>] use`, `digest`, `previous = [{use, digest}, ...]`, at most 10 previous.
  - `paths.store_problems(store, *, login_dirs, worktrees, state_dir, service_uid, owner_uid, fallback) -> list[str]`: each problem is a constant: `STORE_IN_LOGIN_DIR`, `LOGIN_DIR_IN_STORE`, `STORE_IN_WORKTREE`, `STORE_IN_STATE_DIR`, `STORE_PATH_WRITABLE` (a component from `/` down not owned by root or the owner, or writable by group or others; under the fallback, the store itself must be the service user's and 0700), `HELPER_DRIFT` (the installed copy's sha256 differs from the packaged file).
  - `config check` prints the selection with its source (`harnesses.toml`), each problem, and under `fallback = true` the line `FALLBACK_WARNING` ("managed installs use the fallback: a host process of the service user can change an install after its checks (accepted risk, ADR §17 item 13)").

- [ ] **Step 1: Write the failing tests**

- `test_defaults_and_bounds`; `test_a_workstream_layer_cannot_set_harnesses`; `test_recipes_need_exact_versions_and_full_commits`;
- `test_the_selection_round_trips_and_is_written_0600_atomically` (a crash between the temp write and the rename leaves the old file, checked with an injected `os.replace` that raises);
- `test_previous_keeps_at_most_ten`;
- `test_store_problems` parametrized over every constant, each with the smallest tree that causes it, including a group-writable `/`-down component made by `tmp_path` modes and an injected owner lookup;
- `test_config_check_shows_the_fallback_warning_and_the_selection_source`;
- `test_install_agnostic_allows_only_the_store_default`.

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 300 uv run pytest tests/test_harness_settings.py tests/test_harness_selection.py tests/test_harness_paths.py tests/test_config_check.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the tests to verify they pass**

Same command; then `timeout 300 uv run python scripts/check_install_agnostic.py`, pyright and ruff.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/harness src/heterodyne/config/layers.py src/heterodyne/cli.py \
        src/heterodyne/defaults/defaults.toml src/heterodyne/sandbox/settings.py scripts/check_install_agnostic.py \
        tests/test_harness_settings.py tests/test_harness_selection.py tests/test_harness_paths.py tests/test_config_check.py
git commit -m "plan4c T5: [harnesses] config, harnesses.toml and the store location checks"
```

## Task 6: the adapters' layouts, versions, content and updaters

**Files:**
- Modify: `src/heterodyne/agents/base.py`, `src/heterodyne/agents/codex.py`, `src/heterodyne/agents/claude_code.py`, `src/heterodyne/sandbox/openshell_selftest.py` (`env_allowed`)
- Test: `tests/test_agents_adapters.py`, `tests/test_agents_content.py` (new)

**Interfaces:**
- Consumes: `store_core.Layout`, `store_core.Manifest`; Task 1's findings.
- Produces, in `agents/base.py`:
  - `Declared(tool_env: frozenset[str], presentation: frozenset[str])`.
  - `InstallLayout(artifact: Literal["github-release", "npm"], locator: str, layout: store_core.Layout, strip: str)`: for Codex `github-release`, `openai/codex`, the asset `codex-package-<target>.tar.gz` and the sums asset; for Claude Code `npm`, `@anthropic-ai/claude-code-<target>`.
  - `Presentation(argv: tuple[str, ...], binds: tuple[Bind, ...], env: Mapping[str, str])`.
  - `Adapter` gains `versions: Mapping[str, Declared]`, `install_layout: InstallLayout`, `content_kinds: frozenset[str]`, `owned_keys: frozenset[str]`, `check_content(kind: str, tree: Path) -> None` (raises `AdapterError(OWNS_KEY)` or `AdapterError(UNKNOWN_KIND)`), `present(layout: SessionLayout, install: Path, manifest: store_core.Manifest) -> Presentation`. `prepare_home` takes `install: Path` and merges `defaults/` under the launcher's keys.
  - `Cli(binary, root, version, install: str = "", digest: str = "")`.
  - Removed: `version_pin`, `tool_env`, `locate`, `find_binary`, `install_root`.
- `openshell_selftest.env_allowed(adapter, path, version)` reads `adapter.versions[version].tool_env`.

- [ ] **Step 1: Write the failing tests**

- `test_each_adapter_declares_its_s7_pin` (Codex `0.160.0`, Claude Code `2.1.286`) and `test_tool_env_is_per_declared_version`;
- `test_the_layouts_read_version_target_and_entry_point_from_fixture_releases`;
- `test_defaults_that_set_every_owned_key_lose_to_the_launcher` (one per adapter: a `defaults/` that sets every key in `owned_keys`; the generated file has the launcher's values);
- `test_the_updater_is_off_in_what_the_adapter_generates` (Codex `check_for_update_on_startup = false` and `in_app_updates = false`; Claude `DISABLE_AUTOUPDATER=1` and `"autoUpdates": false`);
- `test_content_that_sets_an_owned_key_is_refused` (`OWNS_KEY`: a Claude plugin with `hooks/`, a `hooks` key, a `settings.json`, an MCP server named like the launcher's; a Codex MCP file with a non-server key);
- `test_an_undeclared_content_kind_is_refused` (`UNKNOWN_KIND`);
- `test_presentation_uses_only_paths_inside_the_install` (every `--plugin-dir`, `--mcp-config` source and bind source is under the install root, and every bind is read-only);
- `test_the_login_names_union_is_what_wsd_sends_to_stage` (`harness.login_names()` equals the union of `ADAPTERS`' `login_names`).

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 300 uv run pytest tests/test_agents_adapters.py tests/test_agents_content.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Codex's `prepare_home` keeps plan 4's text and adds `check_for_update_on_startup = false` before the first table and `in_app_updates = false` under `[features]`; MCP servers from content go under `[mcp_servers.<name>]` before the launcher's own tables, and a server name the launcher uses refuses. Claude Code's `interactive_argv` appends the presentation's argv after plan 4's flags, so `--settings` (the launcher's) stays last among settings sources.

Keep `locate` callers compiling until Task 8 by leaving `SandboxRuntime._cli` on a private shim, `_legacy_locate`, that Task 8 deletes. The shim has no tests of its own.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 uv run pytest tests/test_agents_adapters.py tests/test_agents_content.py tests/test_sandbox_*.py -q`
Expected: PASS.

Then pyright and ruff.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/agents src/heterodyne/sandbox/openshell_selftest.py src/heterodyne/sandbox/runtime.py \
        tests/test_agents_adapters.py tests/test_agents_content.py
git commit -m "plan4c T6: adapter layouts, declared versions, content presentation and updaters off"
```

## Task 7: launch entries pin their install (D2)

**Files:**
- Modify: `src/heterodyne/wsd/launches.py`, `src/heterodyne/wsd/journal.py`, `src/heterodyne/wsd/upgrade.py`, `src/heterodyne/wsd/park.py` (`_pin_and_dispatch`), `src/heterodyne/wsd/runtime.py` (`LaunchSpec`, `INSTALL_UNPINNED`), `src/heterodyne/wsd/workstream.py` (`Deps.selections`)
- Test: `tests/test_wsd_launches.py`, `tests/test_wsd_journal.py`, `tests/test_wsd_upgrade.py`, `tests/test_wsd_dispatch.py`, `tests/wsd_env.py`

**Interfaces:**
- Consumes: `harness.selection.load` (Task 5) through an injected `Deps.selections: Callable[[str], InstallPin | None]` (adapter name to pin). Task 7 can merge before Task 5 with a fake; production wiring lands in Task 10.
- Produces:
  - `LaunchEntry.install: str = ""` and `.install_digest: str = ""` (not set-once: set when journaled, never changed).
  - Journal schema `"3"`: `launches.install TEXT NOT NULL DEFAULT ''`, `launches.install_digest TEXT NOT NULL DEFAULT ''`. `upgrade.run_2_to_3` adds the columns in one transaction, like the 1-to-2 upgrade.
  - `LaunchSpec.install: str = ""`, `.install_digest: str = ""`.
  - `wsd.runtime.INSTALL_UNPINNED`: the `LaunchFailed` text for an empty pin, and `NOT_SELECTED`: the guard's escalation detail when the profile's adapter has no selection (the bead escalates `CONFIG_INVALID`, nothing is journaled).

- [ ] **Step 1: Write the failing tests**

- `test_the_guard_journals_the_selected_pin_with_the_entry` (one transaction: a checkpoint crash after it finds both);
- `test_a_replay_after_a_crash_uses_the_journaled_pin_not_the_current_selection` (change the selection between the entry checkpoint and the replay);
- `test_a_selection_change_applies_to_the_next_entry_only`;
- `test_a_resume_takes_the_selection_at_its_own_launch`;
- `test_no_selection_escalates_config_invalid_and_journals_nothing` (`NOT_SELECTED`);
- `test_an_entry_from_schema_2_dispatches_as_install_unpinned` and the next generation pins;
- `test_the_upgrade_from_2_to_3_keeps_every_row_and_rolls_back_on_failure`;
- `test_the_bead_copy_carries_the_pin_and_rebuilds_with_it`.

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 600 uv run pytest tests/test_wsd_launches.py tests/test_wsd_journal.py tests/test_wsd_upgrade.py tests/test_wsd_dispatch.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `_pin_and_dispatch`, read `pin = self.d.selections(view.adapter)` before building the entry, and pass `pin.install, pin.digest` to `LaunchEntry`. The `LaunchSpec` built for dispatch copies them from the entry.

- [ ] **Step 4: Run the tests to verify they pass**

Same command, then the whole `tests/test_wsd_*.py` (`timeout 1800`), pyright and ruff.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/wsd tests/test_wsd_launches.py tests/test_wsd_journal.py tests/test_wsd_upgrade.py \
        tests/test_wsd_dispatch.py tests/wsd_env.py
git commit -m "plan4c T7: launch entries pin their install and digest (D2)"
```

## Task 8: the launcher — `verified_cli`, the bind, the probe, and plan 4b's migration (plan 4b's "launcher bead")

**Files:**
- Create: `src/heterodyne/harness/verify.py`
- Modify: `src/heterodyne/sandbox/runtime.py`, `src/heterodyne/sandbox/spec.py`, `src/heterodyne/sandbox/openshell.py`, `src/heterodyne/sandbox/settings.py` (removes `binaries`), `src/heterodyne/sandbox/build.py`, `src/heterodyne/sandbox/hostread.py`, `src/heterodyne/sandbox/selftest.py`, `src/heterodyne/sandbox/openshell_selftest.py`, `src/heterodyne/sandbox/resources/probes.py`
- Test: `tests/harness_env.py` (the store fixture), `tests/sandbox_env.py`, `tests/test_harness_verify.py` (new), `tests/test_sandbox_runtime.py`, `tests/test_sandbox_spec.py`, `tests/test_sandbox_settings.py`, `tests/test_sandbox_openshell_policy.py`, `tests/test_sandbox_probes.py`, `tests/test_sandbox_hostread.py`, `tests/test_sandbox_build.py`, `tests/live/harness.py`, `tests/live/conftest.py`, `tests/live/test_live_sandbox.py`

**Interfaces:**
- Consumes: Tasks 4–7; plan 4b's `HostUsageReader` and `build_usage_reader` (plan 4b Task 8).
- Produces:
  - `harness.verify.VerifiedInstall(adapter: str, name: str, digest: str, root: Path, manifest: Manifest, record: Record)`.
  - `harness.verify.verify_pin(store: Store, adapter: Adapter, pin: InstallPin, taint_dir: Path) -> VerifiedInstall`: the install exists; its manifest's digest equals `pin.digest`; no taint marker; `store_core.check_fast` passes; the version is in `adapter.versions`. Refusals, `REFUSALS`: `NOT_SELECTED`, `NO_SUCH_INSTALL`, `DIGEST_DIFFERS`, `INSTALL_TAINTED`, `INSTALL_CHANGED` (any `Mismatch`), `UNDECLARED_VERSION`, `STORE_UNSAFE` (Task 5's problems, checked again at each launch).
  - `SandboxRuntime.verified_cli(adapter: Adapter, pin: InstallPin | None = None) -> Cli`: raises `LaunchFailed(<one of REFUSALS>)`. It keeps the `VerifiedInstall` for the launch's probe.
  - `SandboxRuntime._cli(adapter, spec)`: `verified_cli(adapter, InstallPin(spec.install, spec.install_digest))`, or `LaunchFailed(INSTALL_UNPINNED)` for an empty pin.
  - `AgentFacts.cli_root` is the install root. `Bind` gains `private: bool = False`; the install's bind is `Bind(root, root, True, private=True)`, and the OpenShell policy writes it as a non-recursive bind with `rprivate` propagation.
  - `spec.INSIDE_LOGIN_DIR`: `check_credentials` refuses a non-login source inside the chosen account's login directory, too.
  - `ProbeContext.install: VerifiedInstall`; the check `managed-install` in `COMMON_CHECKS` (both paths); its input is a file `/run/hz/install-record.json` (read-only bind) with the install path and every entry's `(path, kind, size, nlink, ino, ctime_ns)`.
  - `probes.py`'s `managed_install(cfg)`: reads `/proc/self/mountinfo`; the install path must be exactly one mount, `ro`, with no `shared:` or `master:` tag, and no mount point below it; a walk by fd (`os.lstat` relative to dir fds, following no link) must give exactly the record's entries. Prints `PASS managed-install` or `FAIL managed-install [<reason>]`, the reason being one of `MI_NOT_ONE_MOUNT`, `MI_NOT_READ_ONLY`, `MI_PROPAGATES`, `MI_SUBMOUNT`, `MI_LISTING_DIFFERS`.
  - The plan 4b migration, exactly as plan 4b lists it:
    - `hostread.py`: `HostUsageReader(accounts, cli: Callable[[], Cli], credlocks, scratch_root, lock_wait_seconds, *, env, ...)`; `_locked` calls `cli()`; an exception from it logs `DIAG_READ_FAILED` with its type name and starts nothing.
    - `build.py`: `build_usage_reader(s, env)` returns a reader when `selection.load(...)` has a Codex selection, with `cli=lambda: runtime.verified_cli(ADAPTERS["codex"])`. The adapter is looked up by name in `build.py`, which plan 4b's constraint allows (`sandbox/` names adapters only through `ADAPTERS`).
    - `tests/test_sandbox_hostread.py`: `Rig` gets `cli=` from `harness_env.selected_install`; `test_a_missing_cli_stops_before_anything_starts` becomes `test_an_install_that_fails_verification_stops_before_anything_starts`, with the same assertions.
    - `tests/test_sandbox_build.py`: `test_the_reader_needs_the_sandbox_runtime_and_a_codex_binary` becomes `test_the_reader_needs_the_sandbox_runtime_and_a_selected_codex_install`.
    - Every test configuring `[adapters.<adapter>] binary` or `binaries`, including `tests/sandbox_env.py`'s rig and `tests/live/test_live_sandbox.py`, moves to the store fixture. `install_fake_cli` becomes `harness_env.store_fixture(root, adapter)`: it stages and publishes the fake CLI, at its declared version, into a `LocalStore` under `root`, and writes the selection.
    - `tests/test_sandbox_spec.py`: `test_credentials_pass_with_cli_inside_the_chosen_login_dir` becomes `test_a_cli_inside_the_chosen_login_dir_is_refused` (`INSIDE_LOGIN_DIR`).

- [ ] **Step 1: Write the failing tests**

`tests/test_harness_verify.py`, one test per refusal in `REFUSALS`, each through `verify_pin` and again through `SandboxRuntime.launch` with the fake backend (the receipt is `refused` with exactly that text), plus:
- `test_the_root_replaced_between_the_check_and_the_bind_fails_the_probe` (the fake backend's bind hook renames a copy over the root; the probe, run against the fake's view, reports `MI_LISTING_DIFFERS`);
- `test_a_mount_added_under_the_store_after_the_launch_is_not_visible_inside` (in the probe's own unit test, against a scripted `mountinfo`);
- `test_a_hard_link_into_an_install_from_a_login_dir_is_caught` (a link made after publish raises the file's `nlink`: `INSTALL_CHANGED`);
- `test_a_hard_link_to_a_login_file_is_impossible_in_an_install_before_and_after_a_refresh_by_rename` (stage copies, so the published file is a new inode; replacing the login file by rename changes nothing in the install);
- `test_the_trusted_read_verifies_the_current_selection` (`build_usage_reader`'s `cli` raises after the selection's install is tainted).

`tests/test_sandbox_probes.py` gains `managed_install` cases for every `MI_*` reason, with scripted `mountinfo` text and a temp tree.

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 900 uv run pytest tests/test_harness_verify.py tests/test_sandbox_*.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`verified_cli` holds no lock: the store is read-only to the service user, and the taint marker and selection are re-read on every call. The binary path is `root / manifest.entry_point`; `Cli.version` is `manifest.version`. Delete `_legacy_locate` (Task 6) and `SandboxSettings.binaries`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 3600 uv run pytest -q` (the full suite, in the foreground).
Expected: PASS. `grep -rn "binaries\|\.locate(\|version_pin" src tests` finds nothing but admind's own `adapters.<a>.binary` lookup.

Then pyright and ruff.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/harness src/heterodyne/sandbox tests
git commit -m "plan4c T8: verified managed installs at launch, the Managed install probe, plan 4b's migration"
```

## Task 9: the taint watch

**Files:**
- Modify: `src/heterodyne/sandbox/runtime.py`, `src/heterodyne/wsd/runtime.py` (`InstallTainted` in `Signal`)
- Test: `tests/test_sandbox_taint.py` (new), `tests/fakes/fake_runtime.py`

**Interfaces:**
- Consumes: `store_core.check_fast`; plan 4b's `AgentRuntime.signals`.
- Produces:
  - `wsd.runtime.InstallTainted(session_key: str, generation: int, adapter: str, install: str)`, added to `type Signal`.
  - `wsd.runtime.INSTALL_TAINTED`: the park's `why`.
  - `SandboxRuntime` re-walks at each session end (in `_end`) and from `expire` (`TAINT_SECONDS = 600`, injected clock). A mismatch writes the marker, then queues one signal per live session on the install.
  - `harness.verify.taint(taint_dir, adapter, install, reason)` and `cleared(taint_dir, adapter, install)`.

- [ ] **Step 1: Write the failing tests**

- `test_a_change_found_at_session_end_taints_and_signals_every_session_on_the_install`;
- `test_the_walk_repeats_every_ten_minutes_while_a_session_runs` (the clock moves 599 s: no walk; 600 s: a walk);
- `test_the_marker_is_written_before_the_signal_and_survives_a_restart` (a new runtime refuses with `INSTALL_TAINTED` before any signal is drained);
- `test_a_marker_that_cant_be_read_refuses` (a directory where the file should be);
- `test_a_login_file_copied_in_under_the_fallback_is_found_by_the_next_walk` (the §11 fallback control: write into a `LocalStore` install after both checks, then advance the clock).

- [ ] **Step 2–4: Run, implement, pass**

Run: `timeout 600 uv run pytest tests/test_sandbox_taint.py tests/test_sandbox_runtime.py -q`, then pyright and ruff.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/sandbox/runtime.py src/heterodyne/wsd/runtime.py src/heterodyne/harness/verify.py \
        tests/test_sandbox_taint.py tests/fakes/fake_runtime.py
git commit -m "plan4c T9: re-walk installs in use, taint and signal"
```

## Task 10: `wsd`'s harness commands, the approval and the taint handling

**Files:**
- Create: `src/heterodyne/wsd/harness.py`, `src/heterodyne/wsd/alerts.py`
- Modify: `src/heterodyne/wsd/ctl.py`, `src/heterodyne/wsd/cli.py`, `src/heterodyne/wsd/daemon.py`, `src/heterodyne/wsd/scheduler.py`, `src/heterodyne/wsd/park.py`, `src/heterodyne/wsd/journal.py`, `src/heterodyne/wsd/btq.py` (`QueueLike.policy`; the module's `ask_digest`)
- Test: `tests/test_wsd_harness.py` (new), `tests/test_wsd_ctl.py`, `tests/test_wsd_cli.py`, `tests/fakes/fake_btq.py`

**Interfaces:**
- Consumes: Tasks 4, 5, 7, 8 and 9; admind's `AskPost` contract over `ask.sock` (posted as JSON, without importing `admind`); btq's `Queue.bd`, `Queue.show`, `Queue.policy` and the module's `ask_digest`.
- Produces:
  - Journal tables (schema 3, with Task 7's columns; Task 10 adds them to the same version if Task 7 hasn't released, else schema 4): `harness_installs(staging_id PRIMARY KEY, adapter, digest, version, approval_bead, ask_id, state CHECK (state IN ('staged','posted','approved','published','denied','expired','failed')), install, created_at, updated_at)` and `harness_events(id, at, actor, action, adapter, install, detail)`.
  - `CtlRequest.op` gains `"harness"`, with `harness: HarnessRequest | None` (`action: Literal["list","install","verify","use","rollback","remove","prune"]`, `adapter`, `install`, `version`, `confirm`).
  - `HarnessOps`:
    - `list(adapter | None) -> list[InstallInfo]` with state `selected`, `previous`, `in use`, `tainted`, `staged`, `awaiting approval`, and the fallback flag.
    - `install(adapter, version | None) -> str` (the staging ID): recipe, fetch, `check_content`, `stage`, then the approval bead and the ask (E13). Replies at once; the work runs in a worker thread with its own deadline, and every step is journaled.
    - `advance(now)`: from the scheduler at each pickup: for each `posted` row, read the bead and decide (E13); on approval `publish`, then `published`; on denial `denied`; staging past `staging_hours` is `expired`.
    - `verify(adapter, install)`: `store.verify`, then the taint marker is removed.
    - `use(adapter, install)`: refuses `NOT_PUBLISHED`, `UNDECLARED_VERSION`, `INSTALL_TAINTED` and `OWNS_KEY` (E11's second check); writes the selection with the old one first in `previous`; journals the event.
    - `rollback(adapter)`: selects `previous[0]`, with the same checks.
    - `remove(adapter, install)` and `prune(confirm)`: refuse `IN_USE` (E16); `prune` without `confirm` lists what it would remove and returns a one-time confirmation code (`secrets.token_hex(4)`), which `prune --confirm <code>` must echo within 10 minutes.
    - On an `InstallTainted` signal: park the bead with `hold=True` and `why=INSTALL_TAINTED`, and write one alert per install (`harness-taint-<adapter>-<12 hex>`).
  - `wsctl harness list [<adapter>] | install <adapter> [<version>] | verify <adapter> <install> | use <adapter> <install> | rollback <adapter> | remove <adapter> <install> | prune [--confirm <code>]`.
  - `wsd.alerts.write(alerts_dir, id, text)`: admind's contract (dotfile, then rename).
  - Refusal constants: `NO_RECIPE`, `NO_APPROVAL_WORKSTREAM`, `NOT_APPROVED`, `DENIED`, `DIGEST_NOT_APPROVED`, `NOT_PUBLISHED`, `IN_USE`, `CONFIRM_REQUIRED`.

- [ ] **Step 1: Write the failing tests**

`tests/test_wsd_harness.py`, with a `LocalStore`, the fixture fetcher, `fake_btq` (which records `bd create` and serves `show`) and a fake ask socket:
- `test_install_stages_creates_the_approval_bead_and_posts_the_ask` (the bead's `metadata.ask` has exactly the five fields; the description holds no 64-hex run, checked with `secret_scan.identifier_kind`; the grouped digest parses back to the staged one);
- `test_nothing_is_published_until_the_approval_is_valid`, parametrized over each missing condition of E13 (open, denied, approver not in the policy, a `*_digest` that differs, a gap, the effect's digest differs, another bead): each leaves the row `posted` or `denied`, and the store has no install;
- `test_an_approved_install_is_published_with_the_approved_digest`;
- `test_a_crash_between_approval_and_publish_publishes_once` (checkpoint crash; replay finds `ALREADY_PUBLISHED` from the store and records `published`);
- `test_use_and_rollback_write_the_selection_and_take_effect_at_the_next_launch` (with Task 7's guard);
- `test_use_refuses_an_undeclared_version_a_tainted_install_and_content_owning_a_key`;
- `test_remove_and_prune_refuse_an_install_in_use`, parametrized over E16's four cases;
- `test_prune_needs_the_confirmation_code_and_it_expires`;
- `test_a_taint_signal_holds_every_bead_on_the_install_and_writes_one_alert`;
- `test_verify_clears_the_taint_only_after_a_matching_rehash`;
- `test_every_harness_op_is_journaled_with_its_actor`.

`tests/test_wsd_ctl.py` and `tests/test_wsd_cli.py`: the new op's `refusal` rules and the `wsctl harness` argument parsing and output.

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 900 uv run pytest tests/test_wsd_harness.py tests/test_wsd_ctl.py tests/test_wsd_cli.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

The approval bead's text, as `HarnessOps` writes it (the readout admind shows whole):

```
Title: Add the codex 0.160.0 managed install (0.160.0-<12 hex>)
Description:
  What: add a managed install of codex 0.160.0 to the store. It becomes selectable; nothing uses it until
  `wsctl harness use`.
  Manifest digest: sha256:<16 groups of 4 hex>
  Entries: <n>, size: <bytes>
  Sources:
    - cli: codex-package-x86_64-unknown-linux-musl.tar.gz, release rust-v0.160.0,
      checksum sha256:<16 groups of 4 hex> from codex-package_SHA256SUMS; signature not checked
    - content/skills/<name>: git <repo> at <commit>; trust on first install (unsigned)
  Store: helper (store owner) | fallback (accepted risk: a host process of the service user can change it)
ask.effect: publish exactly the staged install with this manifest digest, under this name.
ask.excludes: selecting it (`use`), and any other version or content.
ask.why: the recipe in [harnesses.codex] asks for it.
ask.risks: the sources' trust as listed; under the fallback, the accepted risk above.
ask.refs: [{kind: commit, repo: Epiphytic/heterodyne-metaharness, id: <the running harness commit>}]
```

- [ ] **Step 4: Run the tests to verify they pass**

Same command, then the whole `tests/test_wsd_*.py` (`timeout 1800`), pyright and ruff.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/wsd tests/test_wsd_harness.py tests/test_wsd_ctl.py tests/test_wsd_cli.py tests/fakes/fake_btq.py
git commit -m "plan4c T10: wsctl harness, the digest-pinned install approval, and taint holds"
```

## Task 11: documentation

**Files:**
- Modify: `docs/configuration.md` (`[harnesses]`, `[harnesses.<adapter>]`, `harnesses.toml`, the `[adapters.*] binary` key as admind's), `docs/wsd.md` (the harness commands, the approval, taint, in use), `docs/security-model.md` (the store owner, the fallback's accepted risk, the controls), `docs/install.md` (the host setup, pointing at `deploy/store-helper/README.md`)

- [ ] **Step 1: Write the sections**, each citing ADR r16 §7 and this plan's E-numbers, in the docs' existing style.
- [ ] **Step 2: Check them**: `timeout 300 uv run python scripts/check_install_agnostic.py`; every command shown runs against a test rig (`wsctl harness --help`).
- [ ] **Step 3: Commit**

```bash
git add docs/configuration.md docs/wsd.md docs/security-model.md docs/install.md
git commit -m "plan4c T11: document managed installs"
```

## Task 12: HOST CHANGE — the store owner, the helper's units and verity (waits for the operator's go)

> **Waits for the operator's go on ADR §17 item 13 (Q1).** This task needs root on the host. It does not start on this plan's approval: its bead blocks on a separate host-change approval bead, which shows the exact commands below. Nothing in Tasks 2–11 needs it. Until it is done, a host can use managed installs only with `[harnesses] fallback = true`.

**Files (in the repo; the host steps run them):**
- Create: `deploy/store-helper/heterodyne-store-helper.socket`, `deploy/store-helper/heterodyne-store-helper@.service`, `deploy/store-helper/setup-store.sh`, `deploy/store-helper/README.md`
- Test: `tests/test_deploy_store_helper.py` (new; offline: `systemd-analyze verify` on the units where it exists, else a parse of the keys, and `bash -n` plus a dry-run mode of the script)

**Interfaces:**
- Consumes: `store_core.py` at a merged commit (Task 4).
- Produces, on the host:
  - the system user and group `heterodyne-store` (`useradd --system --no-create-home --shell /usr/sbin/nologin`);
  - `/var/lib/heterodyne/` (root, 0755) and `/var/lib/heterodyne/harnesses/` (`heterodyne-store`, 0755);
  - `/usr/local/libexec/heterodyne/store_core.py` (root, 0644), copied from the pinned commit;
  - `/etc/heterodyne/store-helper.json` (root, 0644): the service user's uid and the store path;
  - the units, installed to `/etc/systemd/system/`:

```ini
# heterodyne-store-helper.socket
[Socket]
ListenStream=/run/heterodyne-store/helper.sock
SocketUser=heterodyne-store
SocketGroup=<the service user's group>
SocketMode=0660
DirectoryMode=0755
Accept=yes
MaxConnections=4

[Install]
WantedBy=sockets.target
```

```ini
# heterodyne-store-helper@.service
[Service]
User=heterodyne-store
Group=heterodyne-store
ExecStart=/usr/bin/python3 -I /usr/local/libexec/heterodyne/store_core.py serve
StandardInput=socket
StandardOutput=journal
NoNewPrivileges=yes
PrivateNetwork=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/heterodyne/harnesses
RestrictAddressFamilies=AF_UNIX
CapabilityBoundingSet=
SystemCallFilter=@system-service
UMask=0022
TimeoutSec=600
```

  - optionally, the ext4 `verity` feature on `/var/lib`'s filesystem.

- [ ] **Step 1: Write the files and their offline test, and merge them** (a normal PR; it changes no host).
- [ ] **Step 2: Ask for the go.** Post the host-change approval bead through admind's relay. Its description lists every command of steps 3–6 exactly, with the pinned commit, and says what each changes and how to undo it (README "Rollback": stop and disable the units, remove them, `userdel heterodyne-store`, remove `/var/lib/heterodyne` only if empty or after a `wsctl harness list` shows nothing in use). Stop here until the bead is approved.
- [ ] **Step 3 (after the go, as root): the user, the store and the helper copy**

```bash
sudo bash deploy/store-helper/setup-store.sh --commit <pinned commit> --service-user <service user>
```

The script is idempotent and prints each change. It refuses if `/var/lib/heterodyne/harnesses` exists with another owner, or if any path component from `/` is writable by the service user.

- [ ] **Step 4 (as root): the units**

```bash
sudo install -m 0644 deploy/store-helper/heterodyne-store-helper.socket deploy/store-helper/heterodyne-store-helper@.service \
     /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heterodyne-store-helper.socket
```

- [ ] **Step 5 (optional, as root): verity.** `tune2fs -l <device of /var/lib> | grep -i features` first. If `verity` is missing and the operator agreed to it, `tune2fs -O verity <device>` (ext4 allows it on a mounted filesystem; the kernel lists `verity` in `/sys/fs/ext4/features`, F11). Re-run `tune2fs -l` to confirm.
- [ ] **Step 6: Switch the host config** (with the operator's go for the config change): remove `[harnesses] fallback = true` if it was set, run `heterodyne config check` (no store problem, no `HELPER_DRIFT`), and restart `wsd`.
- [ ] **Step 7: Evidence.** Close the bead with: `id heterodyne-store`, `stat -c '%U %a %n'` of the store path from `/` down, `systemctl status heterodyne-store-helper.socket`, a `wsctl harness list` that says "store: helper", and the verity line. No checksum or digest beyond its 12-hex prefix.

## Task 13: the live test on managed installs

**Files:**
- Create: `tests/live/test_live_managed.py`
- Modify: `docs/wsd.md` (the live checks)

**Interfaces:**
- Consumes: everything above; Task 12 for the helper-mode tests.
- Produces: live evidence for §11's Managed installs controls that offline tests can't show.

Tests (each skipped with its reason when its variables are missing):
- `test_install_publish_use_and_launch_codex_and_claude` (real fetch, a real approval through admind on the live test's own bead workstream, `use`, then a launch whose self-test passes `managed-install`; one short prompt each, on the live-test model budget);
- `test_the_service_user_cannot_write_chmod_rename_or_link_in_the_store` (helper mode only; each attempt fails with `EACCES` or `EPERM`, by errno);
- `test_a_submount_hiding_a_file_and_a_bind_mount_inside_fail` (needs root: `HZ_LIVE_ROOT_HELPER`, a sudo rule for exactly the mount commands the test prints; otherwise skipped with that reason);
- `test_a_mount_added_under_the_store_after_launch_is_not_visible_inside`;
- `test_a_resume_after_use_takes_the_new_install`;
- `test_an_updater_write_into_the_synthetic_home_is_never_run` (writes a marker binary where Task 1 found the CLI writes; the launcher's argv and the process tree never name it).

- [ ] **Step 1: Write it.** **Step 2:** run it offline (every test skipped, none an error), pyright and ruff. **Step 3:** run it live, operator present:

```bash
HZ_LIVE=1 HZ_LIVE_SANDBOX=1 HZ_LIVE_STORE=helper timeout 3600 uv run pytest tests/live/test_live_managed.py -v -s
```

**Step 4:** paste the `EVIDENCE` lines into `docs/wsd.md` after checking them for paths, digests and tokens, and commit:

```bash
git add tests/live/test_live_managed.py docs/wsd.md
git commit -m "plan4c T13: live managed-install checks"
```

## Execution handoff

This plan is complete once it has passed its cross-model design review and the operator has approved it. Nothing is executed before then, except Task 1, which is research. After approval, there are two ways to run it:

1. **Subagent-driven (recommended).** Use superpowers:subagent-driven-development. Give each task a fresh worker session on its own `p4c-t<N>` branch, in the waves of the Parallelism section, and claim each worker's bead with that worker's own session. Each task ends with the cross-model review in Global Constraints before its PR merges.
2. **Inline.** Use superpowers:executing-plans. Run the tasks in wave order in one session, with a checkpoint and the same review after each task.

Either way, Task 8 starts only after plan 4b's Tasks 6 and 8 have merged, and Task 12 starts only after its own host-change approval. Plan 4b's Task 11b and Task 12 wait for Task 8.
