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
| Task 6 (adapters) | Stopped for both adapters (S9-5, S9-6, S9-7) | Resumes with §4's text. It does not wait for the S9-8 decision. |
| Task 8 (launcher) | Stopped (S9-8) | Resumes only once Q1 is decided and that option's text is in the plan. Under option A (recommended) that is this amendment. Under C or D it needs that option's own review first. |
| Tasks 9, 10, 11, 13 | Wait for Task 8, as before | As before |
| Plan 4b Tasks 11b and 12 | Wait for Task 8, as before | As before |
| Tasks 2, 3, 4, 5, 7, 12 | Not affected | Not affected |

Q6's launch-path check (S9-10), if chosen, runs before Task 6's PR merges, not before Task 6 starts.

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
- For: it works on 0.1.2, needs no new dependency and no version bump, and it's checked on the exact mount table the agent gets. That table is fixed after create, because the bind is `rprivate` (S9-8), and the probe runs in the workload's own user and mount namespaces (plan 4's `channel.py` check). There is no window between the check and the use. Task 8 resumes at once.
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

| E24 | The bind's options. | OpenShell 0.1.2's Podman driver accepts only `type`, `source`, `target`, `read_only` and `selinux_label` for a bind, and always gives Podman `ro,rbind,rprivate` (S9-8). `driver_config` emits the install bind with exactly `type`, `source`, `target` and `read_only: true`, the same four fields as every other bind. A test pins the emitted object. The bind is private and read-only at the top, but recursive. A mount below the install at create time is carried in, so the Managed install probe refuses any mount below the install's path (`MI_SUBMOUNT`), and the launch fails. A non-recursive bind field upstream (S9-8 option C) is a follow-up. When it lands, `driver_config` emits it and the probe stays. | S9-8 refuted non-recursion on 0.1.2. Refusing on the sandbox's own mount table is fail-closed, with no window between check and use, because the table is fixed after create (`rprivate`). ADR r16 Amendment 1 (Q2) records this. |

**Goal**, the "The launch." bullet (replaces it):

> **The launch.** The launcher binds the install's root read-only as one private bind. The Managed install probe proves from inside that the sandbox sees exactly that tree, with no mount below it. A launch entry pins its install when the guard journals it.

**Task 8, Interfaces**, the `Bind` item (replaces its last sentence):

> `driver_config` writes it with exactly `type`, `source`, `target` and `read_only: true` (E24, S9-8). A test pins the emitted object.

**Task 8, Interfaces**, `probes.py`'s `managed_install(cfg)`, first check (replaces it):

> - `/proc/self/mountinfo`, decoded (the octal escapes `\040`, `\011`, `\012` and `\134` in the mount point): exactly one line has the install path as its mount point (`MI_NOT_ONE_MOUNT` otherwise). That line is `ro` (`MI_NOT_READ_ONLY`) and has no `shared:` or `master:` tag (`MI_PROPAGATES`). No line has a mount point that starts with the install path plus `/` (`MI_SUBMOUNT`). The backend's bind is recursive (E24), so this is the check that keeps a submount out of a session. It is the only one that sees a mount made only in Podman's namespace (S9-8).

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

| E9 | Updater switches and the launcher's keys. | Codex (S9-5, S9-7) writes `check_for_update_on_startup = false` at the top level of the generated `config.toml`, and `daemon_auto_start = false`, `plugins = false` and `in_app_updates = false` under `[features]`, beside plan 4's `apps = false`. All of them are written after the defaults. `check_for_update_on_startup` is the update switch. `daemon_auto_start` stops the CLI copying itself into `$CODEX_HOME/packages/` and running the copy. `plugins` stops the curated plugin sync. `in_app_updates` had no observable effect on 0.160.0. It is kept so that a later version's in-app updater stays off, but nothing relies on it. No launch, test or probe relies on `-c`: any `-c`, even an unknown key, hides the daemon as a side effect (S9-5). Claude Code (S9-6) gets `DISABLE_AUTOUPDATER=1` and `DISABLE_UPDATES=1` in `extra_env`, plus `"env": {"DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1"}` in the launcher's settings file. The launcher no longer writes `"autoUpdates": false`: under `--setting-sources=` it is migrated once and then ignored, and in `--settings` it does nothing. A `defaults/` value for it is inert, because the CLI checks the variables first. Each adapter's `owned_keys` lists these with the approval and sandbox modes, the hooks, the status line and the connectors. The launcher writes its own values last, and a test per adapter proves that a default setting every owned key loses. | F4, F9, F10; S9-5, S9-6, S9-7. The launcher's values always win (§7). |

**E25** (new row, after E24):

| E25 | The home paths the CLI runs from. | An adapter declares `empty_read_only`: home-relative directories the sandbox presents as empty and read-only. For Codex they are `.codex/tmp` (the PATH aliases under `tmp/arg0/`) and `.codex/packages` (the daemon copy). Claude Code declares none. The launcher binds one empty directory, made fresh for each launch, read-only at each of them. The probe checks from inside that each is empty and read-only. Codex then prints `could not create PATH aliases: Read-only file system` and runs normally (S9-7). Whether `apply_patch` still works without its alias is open until Task 13 (Q3). | S9-7: the CLI runs what it writes into its home, and putting the aliases first on `PATH` would run anything written under a common command name. An empty read-only directory leaves nothing to run, in this session or a later one on the same home. |

**F4** (replaces the last column): "E9: the generated `config.toml` sets `check_for_update_on_startup = false`, and `[features] daemon_auto_start = false`, `plugins = false` and `in_app_updates = false` (S9-5)."

**F9** (replaces the last column): "E9: `DISABLE_AUTOUPDATER=1` and `DISABLE_UPDATES=1` in the launcher's environment and in its settings file's `env`. `autoUpdates` isn't a switch under `--setting-sources=` (S9-6)."

**F10** (replaces the last column): "E9 and E25. The aliases go to `$CODEX_HOME/tmp/arg0/`, first on the children's `PATH`, and the daemon copy goes to `$CODEX_HOME/packages/` (S9-7). The sandbox presents both as empty and read-only, and `daemon_auto_start = false` stops the copy."

### 4.2 Task 6

**Interfaces**, the `Adapter` item (adds, after `owned_keys: frozenset[str]`):

> `empty_read_only: tuple[PurePosixPath, ...]` (E25): relative to the home. Codex has `(".codex/tmp", ".codex/packages")`, Claude Code has `()`. `prepare_install_home` creates each one empty, mode 0700, and refuses (`AdapterError`) if one exists and isn't an empty directory, so nothing written by an earlier session is hidden underneath the bind on the host side. `check_profile_args(args: Sequence[str]) -> None` raises `AdapterError(OWNS_KEY)` for a profile argument that would override an owned key (Q4).

**Interfaces**, the `prepare_home` sentence (replaces "The existing `prepare_home(layout, worktree)` keeps its signature and gains only the updater switches (E9)."):

> The existing `prepare_home(layout, worktree)` keeps its signature and gains only E9's keys. It doesn't create E25's directories: until Task 8 there is no bind to put on them.

**Step 1**, the updater test (replaces it):

> - `test_the_updater_is_off_in_what_the_adapter_generates`: for Codex, `check_for_update_on_startup = false` comes before the first table, and `[features]` holds `daemon_auto_start = false`, `plugins = false`, `in_app_updates = false` and `apps = false`. For Claude Code, `extra_env` has `DISABLE_AUTOUPDATER=1` and `DISABLE_UPDATES=1`, and the settings file's `env` has both as `"1"`. Nothing is asserted about `autoUpdates`.

**Step 1** (adds):

> - `test_codex_argv_carries_no_config_override`: the launcher's own Codex argv (`server_argv`, `tui_argv` for a new session and for a resume) has no `-c`, `--config`, `--enable` or `--disable`. A `-c` would hide a missing daemon key (S9-5).
> - `test_a_profile_arg_that_overrides_an_owned_key_is_refused` (Q4): `-c check_for_update_on_startup=true`, `--config features.daemon_auto_start=true`, `--enable daemon_auto_start`, and the `-c key=value` and `-ckey=value` spellings. A profile `-c` for a key that isn't owned passes.
> - `test_install_home_creates_the_empty_read_only_dirs_and_refuses_a_non_empty_one`.

**Step 1**, the owned-keys test (adds to its parenthesis):

> the Codex default sets `features.daemon_auto_start = true`, `features.plugins = true` and `check_for_update_on_startup = true`. The Claude default sets `env.DISABLE_AUTOUPDATER = ""` and `env.DISABLE_UPDATES = ""`.

**Step 3**, Codex (replaces its first clause):

> Codex's `prepare_home` keeps plan 4's text. It adds `check_for_update_on_startup = false` before the first table, and `daemon_auto_start = false`, `plugins = false` and `in_app_updates = false` under the existing `[features]`, after `apps = false`.

**Step 3**, Claude Code (adds):

> Claude Code's `facts` adds `DISABLE_AUTOUPDATER=1` and `DISABLE_UPDATES=1` to `extra_env`, beside `ENABLE_CLAUDEAI_MCP_SERVERS=false`. The launcher's settings file gets both under `env`.

`wsd`'s profile check calls `check_profile_args` when the configuration is loaded, so a bad profile shows up in `config check`, not at launch.

### 4.3 Task 8

**Interfaces** (adds, after the `Bind` item):

> - E25's binds: `SessionLayout.empty` is a host directory under the session's run directory. The launcher removes and remakes it, mode 0555 and empty, before every create. For each of the adapter's `empty_read_only` paths, the spec gets `Bind(layout.empty, <home inside>/<path>, True)`. `driver_config` writes them with the same four fields as any bind.
> - The record file gains `empty_read_only`: the inside paths of those binds.
> - The check `empty-read-only` in `COMMON_CHECKS` (both paths): each listed path is a directory with no entries and `ST_RDONLY` in `statvfs`. It prints `PASS empty-read-only` or `FAIL empty-read-only [<reason>]`, where the reason is `ER_MISSING`, `ER_NOT_EMPTY` or `ER_NOT_READ_ONLY`.

**Step 1** (adds):

> - `tests/test_sandbox_spec.py`: `test_the_codex_home_binds_tmp_and_packages_to_one_empty_read_only_dir`, and `test_claude_code_gets_no_empty_binds`.
> - `tests/test_sandbox_probes.py`: one case for each `ER_*` reason.

### 4.4 Task 13

The test list replaces these entries. Each Codex run here uses no `-c` (E9).

> - `test_install_publish_use_and_launch_codex_and_claude`: unchanged, plus a Codex prompt that makes it edit a file in the worktree with `apply_patch`. This is E25's editing check (Q3). The test passes only if the file changed.
> - `test_a_submount_hiding_a_file_and_a_bind_mount_inside_fail` is split by namespace (S9-8). Each half covers both a `tmpfs` hiding a file and a same-filesystem bind mount over a file:
>   - `test_a_host_namespace_submount_is_refused_before_create` needs root (`HZ_LIVE_ROOT_HELPER`, a sudo rule for exactly the mount commands the test prints; otherwise skipped with that reason). The launch is refused with `INSTALL_CHANGED` (`CROSSES_MOUNT`) and the install is tainted.
>   - `test_a_podman_namespace_submount_is_refused_by_the_probe` uses `podman unshare mount` and needs no root. Verification passes, the probe reports `FAIL managed-install [MI_SUBMOUNT]`, the launch is refused, and no taint marker is written.
> - `test_a_mount_added_under_the_store_after_launch_is_not_visible_inside` adds one mount in each namespace (the host's one needs `HZ_LIVE_ROOT_HELPER`). Neither appears in the running sandbox's `mountinfo`.
> - `test_an_updater_write_into_the_synthetic_home_is_never_run`:
>   - Codex: after a TUI launch and exit there is no `.codex/packages/` content and no `.codex/tmp/arg0/` on the host side of the home. `strace` isn't available inside, so the process tree and the launcher's argv are checked: neither names a path under `.codex/tmp` or `.codex/packages`. The egress log has no `api.github.com` request from the update check. `shell_snapshots/` holds only files written in that session.
>   - Claude Code: on the second launch of the same home, `claude --setting-sources= doctor` reports `Auto-updates: disabled (set by env: DISABLE_UPDATES)`, since the CLI checks `DISABLE_UPDATES` first. Under Q5's other choice it reports `(set by env: DISABLE_AUTOUPDATER)`.
>   - Both: a marker binary written where Task 1 found the CLI writes is never run.

### 4.5 Parallelism

The Task 6 row's "Depends on" becomes "1 (S9-2 to S9-7, as amended by Amendment 1), 2, 3, 5". The Task 8 row's becomes "1 (S9-8 as decided by Amendment 1, S9-9), 4, 5, 6, 7, plan 4b T6 and T8". The wave diagram gets the same notes.

## 5. Codex's review notes on the spike

- **CROSSES_MOUNT is per namespace.** T2's `check_fast` sees mounts only in the namespace it runs in, the host's. Under A it stays as it is: the earlier, tainting check for host mounts. The probe is the check for mounts that only Podman's namespace has. §3.3's probe text and Task 8's record note say this, and §4.4 splits Task 13's controls by namespace. T2 itself doesn't change.
- **`apply_patch` is pending Task 13.** S9-7 showed that startup works with an empty read-only `tmp`. No run used `apply_patch`. §4.4's editing check is the validation, and Q3 gives the fallback if it fails.

## 6. ADR r16: the plan alone isn't enough

The plan can't change what the ADR states, and under A two sentences are no longer true:

- §7 Managed installs: "The launcher binds the install's root read-only at its own path, as one non-recursive, private bind. So a mount added under the store on the host, before or after the launch, never appears inside the sandbox."
- A.3 item 2: "The bind is one non-recursive, private mount."

The updater sentence in §7 Updates, "the launcher always runs the install's own entry point, so anything an updater writes into a synthetic home is never run by the launcher", stays true. It no longer covers what it was meant to, though: S9-7 shows the CLI itself runs what it wrote. The plan's fix (E9, E25) meets the intent without an ADR change, but the ADR's argument would be incomplete.

Proposed "ADR 0001 r16 Amendment 1" (Q2), as replacement text:

- §7, the bind: "The launcher binds the install's root read-only at its own path, as one private bind, so a mount added under the store after the launch never appears inside the sandbox. The backend's bind is recursive (OpenShell 0.1.2), so a mount below the install at launch time is carried in. The Managed install probe refuses the launch when it sees any mount below the install's path, so no session runs with one."
- A.3 item 2: "The bind is one private mount, read-only at the top. The backend's bind is recursive, so the Managed install probe checks from inside that the bind has no propagation and no mount below it, and the launch fails otherwise."
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

**Q3. Codex's `$CODEX_HOME/tmp`, if Task 13 finds that `apply_patch` needs its alias:**
- **An empty read-only bind now (E25). If Task 13's editing check fails, switch `tmp` (not `packages`) to a fresh `tmpfs` for each launch, through OpenShell's `tmpfs` mount type, via a short amendment. Recommended.** The aliases would then exist only for that launch, pointing at the store binary, and nothing would carry over to a later session.
- A `tmpfs` for `tmp` from the start. Easier for `apply_patch`, but the stop rule asks for read-only or empty, and the session could write files under command names onto its own `PATH`.

**Q4. Codex profile arguments that would override an owned key:**
- **Refuse `-c`, `--config`, `--enable` and `--disable` when they name an owned key (`OWNS_KEY`, found at `config check`). Recommended.**
- Refuse every `-c` in profile arguments.
- Allow them, since the operator writes profiles. Not recommended: the launcher's values must win (§7).

**Q5. Claude Code's second variable:**
- **Set `DISABLE_UPDATES=1` beside `DISABLE_AUTOUPDATER=1`. Recommended.** It also blocks a manual `claude update` inside the session.
- Set only `DISABLE_AUTOUPDATER=1`, the switch S9-6 names.

**Q6. Checking the launcher's own Codex launch path:** S9-5 tested a bare TUI. The launcher runs `codex app-server --listen` and then the TUI with `--remote`, and it relies on the socket under `/tmp/codex-daemon-<uid>/`.
- **A short no-Podman check, S9-10 (`unshare -rn`, as S9-5 was run): `app-server --listen` plus `--remote` with E9's keys starts, nothing appears in `packages/`, and there's no `api.github.com` lookup. Recorded in the spike doc before Task 6's PR merges. Recommended.**
- Leave it to Task 13.

## 8. Not changed

- S9-1 to S9-4 and S9-9 are confirmed, and their consumers proceed as written.
- T2 (merged) and its `CROSSES_MOUNT` check.
- E10 and everything else in plan 4c not named here.
- `/tmp/codex-daemon-<uid>/` stays bound from the session, as in plan 4 (S5).
