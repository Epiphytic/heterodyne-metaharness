# Plan 4c Amendment 1: the S9 findings

Base: plan 4c, branch `plan/4c` at `ffd7a64` (`docs/superpowers/plans/2026-10-13-plan4c-managed-installs.md`, approved). The spike's evidence is `docs/spikes/s9-managed-installs.md` on main (PR #80, `a913622`). The spec is ADR 0001 r16 (design repo `hermes-workstreams-v2`, branch `adr-r16`, text at `93d0e90`).

**Status:** draft for review. It needs a cross-model design review, then operator approval. Task 1's stop rules hold the stopped work until then (below). Once approved, the replacement text is applied to plan 4c on `plan/4c` as one new commit, and plan 4c's header lists this amendment. The questions in §6 are decided first.

## 1. Summary

Task 1 confirmed S9-1 to S9-4 and S9-9. Four findings were not confirmed:

| Finding | Verdict | What changes |
|---|---|---|
| S9-5 (Codex's updater switch) | changed | The switch is `check_for_update_on_startup = false`. Two more Codex keys are needed: `[features] daemon_auto_start = false` (the app-server daemon copy) and `[features] plugins = false` (the curated plugin sync). `in_app_updates` has no observable effect. A `-c` on the command line also hides the daemon, so no check may rely on it. |
| S9-6 (Claude Code's updater switch) | changed | The switch is the environment variable `DISABLE_AUTOUPDATER=1`. `"autoUpdates": false` is migrated once and then ignored under `--setting-sources=`, and it does nothing in a `--settings` file. |
| S9-7 (Codex's PATH aliases) | changed | The aliases are written to `$CODEX_HOME/tmp/arg0/codex-arg0<random>/` and that directory is put first on the children's `PATH`. With the default daemon setting, the CLI also runs a copy of itself from `$CODEX_HOME/packages/`. The sandbox presents both directories as empty and read-only. |
| S9-8 (the install bind) | refuted | OpenShell 0.1.2 has no field for a non-recursive bind. The bind is always `ro,rbind,rprivate`, so a submount that exists at create time is carried in, writable. §3 gives the options. The recommendation is that the probe refuses any mount below the install, and that ADR r16 §7 is amended to match. |

## 2. What stays stopped, and what resumes

| Work | State until this amendment is approved | After approval |
|---|---|---|
| Task 6 (adapters) | Stopped for both adapters (S9-5, S9-6, S9-7) | Resumes with §4's text. It does not wait for Q1. Its PR merges only after S9-10 (§4.6) has passed and is recorded in the spike doc. |
| Task 8 (launcher) | Stopped (S9-8) | Resumes only when three things hold: Q1 is decided, that option's text is in the plan, and the ADR amendment (§6, Q2) is approved. Under option A (recommended) the plan text is this amendment. Under C or D the option needs its own review first. |
| Tasks 9, 10, 11, 13 | Wait for Task 8, as before | As before |
| Plan 4b Tasks 11b and 12 | Wait for Task 8, as before | As before |
| Tasks 2, 3, 4, 5, 7, 12 | Not affected | Not affected |

Task 6 changes the existing `prepare_home`, so E9's keys reach production launches as soon as its PR merges. That's why S9-10 must pass before the merge. It is not needed before the work starts.

## 3. S9-8: the install bind

### 3.1 What the spike showed

- OpenShell 0.1.2's Podman driver accepts these `bind` fields and no others: `source`, `target`, `read_only` (default `true`) and `selinux_label`. Unknown fields are refused by name (`deny_unknown_fields`). `propagation`, `bind_propagation`, `nonrecursive`, `bind_nonrecursive` and `options` were all refused.
- Podman always gets `ro,rbind,rprivate`.
- The top mount is `ro` and private: it has no `shared:` or `master:` tag. A mount made under the source after create never appears inside.
- The bind is recursive. A submount that exists at create time is carried in, and it is **read-write**, because `ro` applies only to the top mount. Only Landlock's read-only rule stopped the test write.
- Which side sees a submount depends on the namespace. A mount made only in Podman's namespace (`podman unshare mount`, which the service user can run) is invisible to a walk in the host's namespace. So T2's `check_fast` (`CROSSES_MOUNT`, which compares each entry's `mnt_id` with the root's) can't see it. Inside the sandbox it has its own mount line and its own `st_dev`.

So two of r16 §7's three bind properties hold: read-only at the top, and private. Non-recursion fails. The ADR says what that is for: "a mount added under the store on the host, before or after the launch, never appears inside the sandbox". The "after" half holds. The "before" half doesn't.

### 3.2 Options

**A. Fail closed: the probe refuses any mount below the install (recommended).** `driver_config` keeps the four fields S9-8 showed work. The Managed install probe, which Task 8 already specifies, refuses the launch if `mountinfo` shows anything below the install's path (`MI_SUBMOUNT`). `check_fast` keeps catching host-namespace mounts before the sandbox is created.
- For: it works on 0.1.2, needs no new dependency and no version bump, and it checks the exact mount table the agent gets: the probe runs in the workload's own user and mount namespaces (plan 4's `channel.py` check). `rprivate` doesn't make that table immutable. It stops propagation from the source: a mount carried in at create is caught by the probe that follows, and a mount added under the source later can't propagate in (S9-8). The one remaining way to change the table is a direct mutation of the container's own namespaces, which is excluded below and left to the existing sandbox protections. Within that boundary there is no window between the check and the use. Task 8 can resume as soon as Q1 and the ADR amendment are approved.
- Against: a submount at launch time is refused, not hidden, so anyone who can mount under the store can stop launches: root on the host, or the service user in Podman's namespace. Both can stop launches today in other ways. The ADR's sentence has to change (§5).

**B. A plus a check from Podman's namespace before create.** Before `sandbox create`, the launcher reads the mount table in Podman's namespace (`podman unshare cat /proc/self/mountinfo`) and refuses any mount at or below the install.
- For: the refusal comes earlier, before a sandbox exists, with its own constant.
- Against: it adds a `podman` call to every launch, and it ties the launcher to rootless Podman's namespace. There's a window between the check and create, so it guarantees nothing beyond what A gives. At most, it would be a diagnostic.

**C. Upstream: a non-recursive bind field in OpenShell.** Add a field to the Podman driver's `bind` variant, for example `recursive: bool`, default `true`, that emits `bind` instead of `rbind`. Then pin the release that has it.
- For: it gives the property the ADR states, and the hidden file underneath is what the sandbox sees (the walk verifies it). Nothing is refused.
- Against: it's slow (an upstream PR, a release, then a version bump past r15's 0.1.2 pin, which is its own review). It needs S9-8 run again on the new version. Task 8 would wait for all of it.

**D. A named volume with a non-recursive bind (`type: volume`).** 0.1.2 accepts `volume` mounts: `source` (a volume name), `target`, `read_only`. A local-driver volume created with `--opt type=none --opt o=bind,ro --opt device=<install>` is mounted with a plain bind, which doesn't recurse.
- For: it might give non-recursion on 0.1.2.
- Against: none of it has been tested. It needs a new spike with Podman (the lead's go), covering the volume's mount options, whether `st_dev` and `st_ino` survive, and whether the gateway allows it. It adds a volume per install, created and removed through `podman`. A mount under the volume's `_data` path in Podman's namespace is still possible, so the probe is needed anyway.

**Recommendation: A**, and C as a follow-up bead that doesn't block anything. When C lands, `driver_config` emits the field, the probe stays, and the ADR's stronger wording can come back. B adds nothing to A's guarantee. D costs a spike and more moving parts to reach what C gives more simply.

Out of scope for every option: a mount added from inside the container's own namespaces after the probe. The workload doing it is a sandbox-escape question (plan 4's seccomp policy). The service user doing it, by entering the container's namespaces, already holds the logins. Non-recursion wouldn't change either case.

### 3.3 Replacement text under A

**E24** (replaces the row):

| E24 | The bind's options. | OpenShell 0.1.2's Podman driver accepts only `type`, `source`, `target`, `read_only` and `selinux_label` for a bind, and always gives Podman `ro,rbind,rprivate` (S9-8). `driver_config` emits the install bind with exactly `type`, `source`, `target` and `read_only: true`, the same four fields as every other bind. A test pins the emitted object. The bind is private and read-only at the top, but recursive. A mount below the install at create time is carried in, so the Managed install probe refuses any mount below the install's path (`MI_SUBMOUNT`), and the launch fails. A non-recursive bind field upstream (S9-8 option C) is a follow-up. When it lands, `driver_config` emits it and the probe stays. | S9-8 refuted non-recursion on 0.1.2. Refusing on the sandbox's own mount table is fail-closed. A mount carried in at create is seen by the probe that follows, and `rprivate` keeps later source mounts out. A direct mutation of the container's own namespaces after the probe is excluded (§3.2) and left to the sandbox's existing protections. ADR r16 Amendment 1 (Q2) records this. |

**Goal**, the "The launch." bullet (replaces it):

> **The launch.** The launcher binds the install's root read-only as one private bind. The Managed install probe proves from inside that the sandbox sees exactly that tree, with no mount below it. A launch entry pins its install when the guard journals it.

**Task 8, Interfaces**, the `Bind` item (replaces its last sentence):

> `driver_config` writes it with exactly `type`, `source`, `target` and `read_only: true` (E24, S9-8). A test pins the emitted object.

**Task 8, Interfaces**, `probes.py`'s `managed_install(cfg)`, first check (replaces it):

> - `/proc/self/mountinfo`, decoded (the octal escapes `\040`, `\011`, `\012` and `\134` in the mount point): exactly one line has the install path as its mount point (`MI_NOT_ONE_MOUNT` otherwise). That line is `ro` (`MI_NOT_READ_ONLY`) and has no `shared:` or `master:` tag (`MI_PROPAGATES`). No line has a mount point that starts with the install path plus `/` (`MI_SUBMOUNT`). The backend's bind is recursive (E24), so this is the check that keeps a submount out of a session. It is the only one that sees a mount made only in Podman's namespace (S9-8). The comparison is by whole path component, after decoding. A bind over the install root itself, made in Podman's namespace before create, is not below the target: the container can still show exactly one mount there. That case is left to the probe's second check, the walk. A different tree fails `MI_ROOT_DIFFERS` or `MI_LISTING_DIFFERS`. Binding the same verified tree again is harmless. Task 13 has a control for it (§4.4).

**Task 8, Interfaces**, the record file, after "The record holds only the fields S9-9 confirmed are the same inside" (adds):

> S9-9 confirmed all seven (`dev`, `ino`, `mode`, `nlink`, `size`, `ctime_ns` and the link target), so none is dropped. The mount ID is not in the record. Each namespace numbers its own mounts, so `mnt_id` is compared only within one side: `check_fast` on the host, the probe's `mountinfo` inside.

**Task 8, Step 1**, `tests/test_sandbox_probes.py` (adds, after the `MI_*` cases):

> `test_a_mount_point_that_shares_only_a_prefix_is_not_a_submount` (`/s/inst` and `/s/inst2`), and `test_an_escaped_mount_point_below_the_install_is_a_submount` (a space in the path, written `\040`).

**Task 8, Step 1**, `tests/test_sandbox_openshell_policy.py` (replaces the sentence):

> `tests/test_sandbox_openshell_policy.py` gains `test_the_install_bind_is_exactly_the_four_s9_8_fields` (`type`, `source`, `target`, `read_only: true`, and no other key).

**Task 8, Step 1**, `tests/test_harness_verify.py` (adds):

> `test_a_submount_refusal_writes_no_taint_marker`. `MI_SUBMOUNT` is a launch refusal. The install's own bytes are unchanged, so it doesn't taint. A host-namespace submount is already `INSTALL_CHANGED` (`CROSSES_MOUNT`) before create, and that one taints.

**Task 1 stop rules**, the S9-8 row's last column (replaces it):

> Task 8 stopped. Amendment 1 decided the bind (option A: E24, the probe's `MI_SUBMOUNT`). Task 8 resumed after its approval.

**Execution handoff** (replaces "and Task 1's S9-8 and S9-9 are confirmed"):

> and Amendment 1 is approved (S9-8 option A; S9-9 confirmed)

## 4. S9-5, S9-6, S9-7: replacement text

### 4.1 Decisions and findings rows

**E9** (replaces the row; written with Q4 and Q5 as recommended):

| E9 | Updater switches and the launcher's keys. | Codex (S9-5, S9-7) writes `check_for_update_on_startup = false` at the top level of the generated `config.toml`, and `daemon_auto_start = false`, `plugins = false` and `in_app_updates = false` under `[features]`, beside plan 4's `apps = false`. All of them are written after the defaults. `check_for_update_on_startup` is the update switch. `daemon_auto_start` stops the CLI copying itself into `$CODEX_HOME/packages/` and running the copy. `plugins` stops the curated plugin sync. `in_app_updates` had no observable effect on 0.160.0. It is kept so that a later version's in-app updater stays off, but nothing relies on it. No launch, test or probe relies on `-c`: any `-c`, even an unknown key, hides the daemon as a side effect (S9-5). Claude Code (S9-6) gets `DISABLE_AUTOUPDATER=1` and `DISABLE_UPDATES=1` in `extra_env`, plus `"env": {"DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1"}` in the launcher's settings file. The launcher no longer writes `"autoUpdates": false`: under `--setting-sources=` it is migrated once and then ignored, and in `--settings` it does nothing. A `defaults/` value for it is inert, because the CLI checks the variables first. Each adapter's `owned_keys` lists these with the approval and sandbox modes, the hooks, the status line and the connectors. Codex's `owned_keys` also lists `profile` and `profiles`. The launcher writes neither and drops both from the defaults, so no Codex config profile can turn a key back on. Profile `args` can't override any of these keys either (Q4). The launcher writes its own values last, and a test per adapter proves that a default setting every owned key loses. | F4, F9, F10; S9-5, S9-6, S9-7. The launcher's values always win (§7). |

**E25** (new row, after E24):

| E25 | The home paths the CLI runs from. | An adapter declares `empty_read_only`: home-relative directories the sandbox presents as empty and read-only. For Codex they are `.codex/tmp` (the PATH aliases under `tmp/arg0/`) and `.codex/packages` (the daemon copy). Claude Code declares none. The launcher binds one empty directory, made fresh for each launch, read-only at each of them. The probe checks from inside that each is empty and read-only. Codex then prints `could not create PATH aliases: Read-only file system` and runs normally (S9-7). S9-10 checks that on the launcher's own launch path, and Task 8's live test checks it in the sandbox. Whether `apply_patch` still works without its alias is open until Task 13 (Q3). | S9-7: the CLI runs what it writes into its home, and putting the aliases first on `PATH` would run anything written under a common command name. An empty read-only directory leaves nothing to run, in this session or a later one on the same home. |

**F4** (replaces the last column): "E9: the generated `config.toml` sets `check_for_update_on_startup = false`, and `[features] daemon_auto_start = false`, `plugins = false` and `in_app_updates = false` (S9-5)."

**F9** (replaces the last column): "E9: `DISABLE_AUTOUPDATER=1` and `DISABLE_UPDATES=1` in the launcher's environment and in its settings file's `env`. `autoUpdates` isn't a switch under `--setting-sources=` (S9-6)."

**F10** (replaces the last column): "E9 and E25. The aliases go to `$CODEX_HOME/tmp/arg0/`, first on the children's `PATH`, and the daemon copy goes to `$CODEX_HOME/packages/` (S9-7). The sandbox presents both as empty and read-only, and `daemon_auto_start = false` stops the copy."

### 4.2 Task 6

**Interfaces**, the `Adapter` item (adds, after `owned_keys: frozenset[str]`):

> `empty_read_only: tuple[PurePosixPath, ...]` (E25), relative to the home. Codex has `(".codex/tmp", ".codex/packages")`. Claude Code has `()`. For each path, `prepare_install_home` does the following, working by fd from the home and following no link:
>   - It refuses if the path exists and isn't a directory: a link, a file or anything else. The refusal is `AdapterError(EXEC_DIR_NOT_A_DIRECTORY)`, with the text `the home's <path> isn't a directory`.
>   - If the path is a directory with content, it removes that content (`shutil.rmtree` with `dir_fd`, which doesn't follow links). Homes from before this amendment hold a plan-4 daemon copy and alias directories there, and refusing them would stop every resume. Inside the sandbox the bind hides that content anyway. Clearing it keeps the host side honest.
>   - It creates the path if it's missing, mode 0700, so it ends up as an empty directory.
>
> `profile_flags: Mapping[str, bool]`: the flags a profile's `args` may carry for this adapter, each mapped to whether it takes a value (Q4). Both adapters start with an empty mapping.

**Interfaces** (adds, after `agents.registry.login_names()`; Q4):

> - `config/profile_args.py` (new, standard library only, so `config` still imports no adapter code):
>   - `FLAGS: Mapping[str, Mapping[str, bool]]`, the allowlist per adapter name;
>   - `check(adapter: str, args: Sequence[str], where: str) -> None`, which raises `ConfigError`.
>   - `check` reads `args` left to right and fails closed:
>     - a token equal to an allowed flag that takes no value passes;
>     - a token equal to an allowed flag that takes a value consumes the next token as its value, and a missing value is refused;
>     - a long allowed flag that takes a value may also be written `--flag=value`.
>   - Every other token is refused, naming its index and the adapter: an unknown flag, a short-option cluster, an attached short value such as `-cX`, `--`, a positional argument, and an empty string. So `-c`, `--config`, `--enable`, `--disable`, `-p` and `--profile` are refused in every spelling. Codex's `--config 'features={plugins=true}'` and `-c 'features.plugins=true'` are refused alike, and an override of a parent table needs no key parsing. For Claude Code, `--settings`, `--setting-sources`, `--mcp-config` and `--plugin-dir` are refused, so a profile can't override the launcher's settings file, whose `env` carries E9's variables.
> - `config/layers.py`: `check_profiles` calls `profile_args.check(adapter, profile.get("args", []), f"profiles.{name}.args")` for every profile. A non-list `args` is refused. Because `config.load` calls `check_profiles`, `config check`, wsd's start and admind's start all refuse such a profile.
> - `agents/base.py`: `profile_args(profile)` calls the same `check` and turns its error into `AdapterError(OWNS_KEY)`, so a configuration loaded before an upgrade still fails closed at launch. `claude_code.interactive_argv` uses `profile_args` in place of its own copy.
> - A test keeps the two sides in step: `adapter.profile_flags == profile_args.FLAGS[adapter.name]` for every adapter.
> - Adding a flag to an allowlist is a reviewed change. The review must show that the flag can't set configuration, choose a config profile, or change approvals, the sandbox, settings sources, MCP or plugins.

**Interfaces**, the `prepare_home` sentence (replaces "The existing `prepare_home(layout, worktree)` keeps its signature and gains only the updater switches (E9)."):

> The existing `prepare_home(layout, worktree)` keeps its signature and gains only E9's keys. It doesn't create E25's directories: until Task 8 there is no bind to put on them.

**Step 1**, the updater test (replaces it):

> - `test_the_updater_is_off_in_what_the_adapter_generates`: for Codex, `check_for_update_on_startup = false` comes before the first table, and `[features]` holds `daemon_auto_start = false`, `plugins = false`, `in_app_updates = false` and `apps = false`. For Claude Code, `extra_env` has `DISABLE_AUTOUPDATER=1` and `DISABLE_UPDATES=1`, and the settings file's `env` has both as `"1"`. Nothing is asserted about `autoUpdates`.

**Step 1** (adds):

> - `test_codex_argv_carries_no_config_override`: the launcher's own Codex argv (`server_argv`, `tui_argv` for a new session and for a resume) has no `-c`, `--config`, `--enable` or `--disable`. A `-c` would hide a missing daemon key (S9-5).
> - `tests/test_config_profile_args.py` (new, Q4). Each of these is parametrized and refused with the token's index:
>   - Codex: `["-c", "check_for_update_on_startup=true"]`, `["-ccheck_for_update_on_startup=true"]`, `["--config", "features={plugins=true}"]`, `["--config=features.daemon_auto_start=true"]`, `["--enable", "daemon_auto_start"]`, `["--enable=plugins"]`, `["--disable", "apps"]`, `["-p", "x"]`, `["--profile=x"]`, a cluster `["-xc", "k=v"]`, `["--"]`, `["prompt"]` and `[""]`.
>   - Claude Code: `["--settings", "{}"]`, `["--settings={}"]`, `["--setting-sources", "user"]`, `["--mcp-config", "f"]` and `["--plugin-dir=d"]`.
>   - Also: a value-taking allowed flag with no value; an `args` that isn't a list. An allowed flag, added to a test-only allowlist, passes in its `--flag value` and `--flag=value` forms.
> - `tests/test_config_layers.py`: `test_config_load_refuses_a_profile_with_a_refused_arg`, run through `config.load`, so the caller can't be skipped.
> - `test_profile_args_at_launch_refuses_what_config_refuses` (`agents.base.profile_args`), and `test_the_adapters_allowlists_match_the_config_table`.
> - `test_install_home_clears_leftover_exec_dir_content`: a file, a nested directory and a link that points out of the home are each removed, and the link's target is untouched. Also `test_install_home_refuses_an_exec_dir_that_is_not_a_directory`: a link or a file at `.codex/tmp` or `.codex/packages` gives the exact `EXEC_DIR_NOT_A_DIRECTORY` text.

**Step 1**, the owned-keys test (adds to its parenthesis):

> the Codex default sets `features.daemon_auto_start = true`, `features.plugins = true`, `check_for_update_on_startup = true`, a top-level `profile = "x"` and a `[profiles.x]` table that turns `features.plugins` back on. The generated file has the launcher's values and neither `profile` nor `profiles`. The Claude default sets `env.DISABLE_AUTOUPDATER = ""` and `env.DISABLE_UPDATES = ""`.

**Step 3**, Codex (replaces its first clause):

> Codex's `prepare_home` keeps plan 4's text. It adds `check_for_update_on_startup = false` before the first table, and `daemon_auto_start = false`, `plugins = false` and `in_app_updates = false` under the existing `[features]`, after `apps = false`.

**Step 3**, Claude Code (adds):

> Claude Code's `facts` adds `DISABLE_AUTOUPDATER=1` and `DISABLE_UPDATES=1` to `extra_env`, beside `ENABLE_CLAUDEAI_MCP_SERVERS=false`. The launcher's settings file gets both under `env`.

**Files** (adds): `src/heterodyne/config/profile_args.py` (new), `src/heterodyne/config/layers.py`, `tests/test_config_profile_args.py` (new), `tests/test_config_layers.py`.

**"No caller changes in this task"** (replaces the sentence):

> One caller change only: `config/layers.py`'s `check_profiles` calls `profile_args.check` (Q4). `claude_code.interactive_argv` and `codex.tui_argv` reach profile args only through `agents.base.profile_args`. Every other caller waits for Task 8.

**Before the PR merges** (adds two gates):

> 1. S9-10 has passed and is recorded in the spike doc (§4.6).
> 2. The team lead runs `heterodyne config check` against the deployed configuration. If a profile uses `args`, either the allowlist gains those flags in this PR, under the review rule above, or the operator removes them before the live checkout moves past this merge.

### 4.3 Task 8

**Interfaces** (adds, after the `Bind` item):

> - E25's binds: `SessionLayout.empty` is a host directory under the session's run directory. The launcher removes and remakes it, mode 0555 and empty, before every create. For each of the adapter's `empty_read_only` paths, the spec gets `Bind(layout.empty, <home inside>/<path>, True)`. `driver_config` writes them with the same four fields as any bind.
> - The record file gains `empty_read_only`: the inside paths of those binds.
> - The check `empty-read-only` in `COMMON_CHECKS` (both paths): each listed path is a directory with no entries and `ST_RDONLY` in `statvfs`. It prints `PASS empty-read-only` or `FAIL empty-read-only [<reason>]`, where the reason is `ER_MISSING`, `ER_NOT_EMPTY` or `ER_NOT_READ_ONLY`.

**Step 1** (adds):

> - `tests/test_sandbox_spec.py`: `test_the_codex_home_binds_tmp_and_packages_to_one_empty_read_only_dir`, and `test_claude_code_gets_no_empty_binds`.
> - `tests/test_sandbox_probes.py`: one case for each `ER_*` reason.
> - `tests/live/test_live_sandbox.py`: `test_codex_new_and_resumed_remote_launch_with_empty_read_only_exec_dirs`. It uses the launcher's real shape (`app-server --listen`, then the TUI with `--remote`, new and resumed) in the real sandbox, with E25's binds and no `-c`. It passes when:
>   - both launches reach a ready server (`server_ready`);
>   - the probe passes `empty-read-only`;
>   - after exit, the host side of `.codex/tmp` and `.codex/packages` is empty.
>
>   It runs on the live-test model budget before Task 8's PR merges. A failure stops Task 8, and E25 then needs an amendment.

### 4.4 Task 13

The test list replaces these entries. Each Codex run here uses no `-c` (E9), and each control states its own pass condition.

> - `test_install_publish_use_and_launch_codex_and_claude`: unchanged.
> - `test_codex_apply_patch_works_with_empty_read_only_exec_dirs`: E25's editing check (Q3). The prompt asks Codex to change one line of a worktree file with `apply_patch`. The test passes only if both of these hold:
>   - the session's rollout file (`sessions/…/rollout-…-<thread>.jsonl` in the home) records an `apply_patch` call whose result isn't an error;
>   - the file holds the new line.
>
>   If the rollout shows no `apply_patch` call, for example because the model edited through the shell, the test fails with `apply_patch not invoked`, never passes. A failure means E25 needs an amendment (Q3).
> - `test_codex_exec_dirs_are_empty_and_read_only_inside`: a new launch succeeds with E25's binds, and the probe passes `empty-read-only`. From inside, as the agent's user, creating a file in `.codex/tmp` and creating a directory in `.codex/packages` both fail with `errno == EROFS`. After exit, the host side of both directories is empty.
> - `test_leftover_exec_dir_content_is_cleared_and_a_non_directory_is_refused`. A marker executable is placed in the host side of `.codex/packages`; when run, it writes a sentinel file outside the home. After a launch and a TUI exit, the marker is gone and the sentinel doesn't exist. With a link at `.codex/tmp`, the launch is refused with the exact text `the home's .codex/tmp isn't a directory`.
> - `test_a_submount_hiding_a_file_and_a_bind_mount_inside_fail` is split by namespace (S9-8). Each half covers both a `tmpfs` hiding a file and a same-filesystem bind mount over a file:
>   - `test_a_host_namespace_submount_is_refused_before_create`. It needs root: `HZ_LIVE_ROOT_HELPER`, with a sudo rule for exactly the mount commands the test prints; otherwise it's skipped with that reason. The launch is refused with `INSTALL_CHANGED` (`CROSSES_MOUNT`), and the install is tainted.
>   - `test_a_podman_namespace_submount_is_refused_by_the_probe`. It uses `podman unshare mount` and needs no root. Verification passes, the probe reports `FAIL managed-install [MI_SUBMOUNT]`, the launch is refused, and no taint marker is written.
> - `test_a_podman_namespace_bind_over_the_install_root_fails_the_walk` (§3.3). In Podman's namespace, a copy of the install with one changed file is bound over the install's root, so the target still has exactly one mount. The probe reports `MI_ROOT_DIFFERS` (or `MI_LISTING_DIFFERS`), and the launch is refused. Binding the same tree over itself gives `PASS managed-install`.
> - `test_a_mount_added_under_the_store_after_launch_is_not_visible_inside` adds one mount in each namespace. The host's one needs `HZ_LIVE_ROOT_HELPER`. Neither appears in the running sandbox's `mountinfo`.
> - `test_an_updater_write_into_the_synthetic_home_is_never_run`. The checks are on state and the egress log, not a snapshot of the process tree:
>   - Codex: after a TUI launch and exit, the host side of `.codex/packages` and `.codex/tmp` is empty. The gateway's egress log for the sandbox has no `api.github.com` request. `shell_snapshots/` holds only files whose change time falls inside that launch.
>   - Claude Code: a marker executable at the native installer's path, `$HOME/.local/bin/claude`, is never run. Task 1 saw no write there, but it is where an update would land. The marker writes a sentinel when run: no sentinel exists after two launches of the same home. On the second launch, `claude --setting-sources= doctor` reports `Auto-updates: disabled (set by env: DISABLE_UPDATES)`, because the CLI checks `DISABLE_UPDATES` first. Under Q5's other choice it reports `(set by env: DISABLE_AUTOUPDATER)`.

### 4.6 S9-10: the launcher's own Codex launch shape (before Task 6 merges)

S9-5 tested a bare TUI. The launcher runs `codex app-server --listen` and then the TUI with `--remote`, new and resumed, and relies on the socket under `/tmp/codex-daemon-<uid>/`. S9-10 is added to Task 1's table and recorded in the spike doc. It needs no Podman.

- **Isolation.** Each run happens in a fresh user, mount and network namespace (`unshare -rmn`) with a private `tmpfs` on `/tmp`. The socket state can't meet the host's own Codex or an earlier run's, and nothing is left behind in the host's `/tmp`. The home is made with `mktemp -d -p "$XDG_RUNTIME_DIR"`. Its `config.toml` is exactly what Task 6's `prepare_home` writes, E9's keys included. No argv has `-c`.
- **Runs.**
  - A new launch: `app-server --listen <socket>`, then the TUI with `--remote <socket>`.
  - A resumed launch: `resume <thread> --remote <socket>`, for a thread the first run created.
  - The same two again, with `.codex/tmp` and `.codex/packages` each read-only bound to an empty directory (`bwrap --ro-bind`, as in S9-7).
- **Pass.**
  - Every run reaches a ready server and a TUI connected to it.
  - `.codex/packages` stays empty, and `strace -f -e trace=execve` shows no exec of a path under the home.
  - No `api.github.com` lookup is attempted.
  - The resume opens the thread.
- **Login.** If making or resuming a thread needs a login, the agent stops at that step and reports. The team lead then runs it with the live-test login and model budget. The agent never touches a login file.
- **If it fails.** Task 6 doesn't merge, and an amendment states the working shape.

### 4.5 Parallelism

The Task 6 row's "Depends on" becomes "1 (S9-2 to S9-7, as amended by Amendment 1), 2, 3, 5; merges after S9-10". The Task 8 row's becomes "1 (S9-8 as decided by Amendment 1, S9-9), ADR r16 Amendment 1 approved, 4, 5, 6, 7, plan 4b T6 and T8". The wave diagram gets the same notes.

## 5. Codex's review notes on the spike

- **CROSSES_MOUNT is per namespace.** T2's `check_fast` sees mounts only in the namespace it runs in, the host's. Under A it stays as it is: the earlier, tainting check for host mounts. The probe is the check for mounts that only Podman's namespace has. §3.3's probe text and Task 8's record note say this, and §4.4 splits Task 13's controls by namespace. T2 itself doesn't change.
- **`apply_patch` is pending Task 13.** S9-7 showed that startup works with an empty read-only `tmp`. No run used `apply_patch`. The validation is §4.4's editing check, which needs evidence from the rollout that `apply_patch` was actually called. If it fails, E25 needs a new amendment (Q3).

## 6. ADR r16: the plan alone isn't enough

The plan can't change what the ADR states, and under A two sentences are no longer true. The reviewer's gating: Q1 and this amendment's approval gate Task 8. Task 6 doesn't need them.

- §7 Managed installs: "The launcher binds the install's root read-only at its own path, as one non-recursive, private bind. So a mount added under the store on the host, before or after the launch, never appears inside the sandbox."
- A.3 item 2: "The bind is one non-recursive, private mount."

The updater sentence in §7 Updates, "the launcher always runs the install's own entry point, so anything an updater writes into a synthetic home is never run by the launcher", stays true. It no longer covers what it was meant to, though: S9-7 shows the CLI itself runs what it wrote. The plan's fix (E9, E25) meets the intent without an ADR change, but the ADR's argument would be incomplete.

Proposed "ADR 0001 r16 Amendment 1" (Q2), as replacement text:

- §7, the bind: "The launcher binds the install's root read-only at its own path, as one private bind, so a mount added under the store after the launch never appears inside the sandbox. The backend's bind is recursive (OpenShell 0.1.2), so a mount below the install at launch time is carried in. The Managed install probe refuses the launch when it sees any mount below the install's path, so no session runs with one."
- A.3 item 2: "The host walk refuses an entry whose mount ID differs from the root's, a same-filesystem bind mount included. The bind is one private mount, read-only at the top. The backend's bind is recursive, so the Managed install probe checks from inside three things: the bind has no propagation, there is no mount below it, and its listing equals the host's. The launch fails otherwise." The host mount-ID check and the inside listing check stay exactly as r16 has them.
- §7 Updates, added after the sentence above: "The CLI may also run files it writes into its own home (Codex 0.160.0's PATH aliases and its app-server daemon copy). The adapter turns those writers off where a switch exists, and the sandbox presents the directories they write to as empty and read-only, so nothing written there is run."
- §11 Managed installs controls, added: "a write into a home directory the CLI runs from, which must fail", and "a submount made only in the backend's namespace, which must fail the probe".

## 7. Questions

**Q1. How does Task 8 handle S9-8's recursive bind?**
- **A. The probe refuses any mount below the install (`MI_SUBMOUNT`). Recommended.**
- B. A, plus a check from Podman's namespace before create.
- C. Wait for an upstream non-recursive bind field and a new OpenShell pin.
- D. Spike a named-volume bind on 0.1.2.

Recommended: A now, with C filed as a follow-up bead that blocks nothing.

**Q2. Where does the ADR change go?**
- **A standalone "ADR 0001 r16 Amendment 1" on the design repo's `adr-r16` line, with §6's four edits. Recommended.** It's small, and Task 8 doesn't wait on r17.
- Fold it into r17.
- No ADR change, only the plan. Not recommended: the plan would contradict §7 and A.3.

**Q3. Codex's `$CODEX_HOME/tmp`:**
- **An empty read-only bind (E25), the only presentation this amendment approves. Recommended.** If Task 13's `apply_patch` check fails, Task 13 stops, and a new amendment, with its own review and approval, chooses what replaces it. One candidate for that amendment is a fresh `tmpfs` per launch through OpenShell's `tmpfs` mount type. It is not approved here.
- A writable `tmpfs` for `tmp` from the start. Not recommended: the stop rule asks for read-only or empty, the session could put files under command names onto its own `PATH`, and it would need that separate amendment anyway.

**Q4. Profile `args` that could override an owned key:**
- **An allowlist per adapter, checked in `config/layers.py`'s `check_profiles` and again at launch (§4.2). Any token the checker can't place is refused, so every override spelling and parent-table override is refused without parsing keys. Both allowlists start empty. Recommended.**
- A denylist parser: refuse `-c`, `--config`, `--enable`, `--disable`, `-p` and `--profile` only when their key path is an owned key, a parent of one (including `features={...}` and root inline tables) or a child of one. This needs TOML dotted-key parsing, a list of attached and equals spellings, and a fail-closed rule for anything unparsed. It is more code and more ways to be wrong, for flags nobody uses yet.
- Allow them, since the operator writes profiles. Not recommended: the launcher's values must win (§7).

**Q5. Claude Code's second variable:**
- **Set `DISABLE_UPDATES=1` beside `DISABLE_AUTOUPDATER=1`. Recommended.** It also blocks a manual `claude update` inside the session.
- Set only `DISABLE_AUTOUPDATER=1`, the switch S9-6 names.

S9-10 is no longer a question. The review made it a requirement before Task 6 merges (§4.6).

## 8. Not changed

- S9-1 to S9-4 and S9-9 are confirmed, and their consumers proceed as written.
- T2 (merged) and its `CROSSES_MOUNT` check.
- E10 and everything else in plan 4c not named here.
- `/tmp/codex-daemon-<uid>/` stays bound from the session, as in plan 4 (S5).
