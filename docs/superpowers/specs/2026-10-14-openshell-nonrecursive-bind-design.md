# btq-wazo1: a non-recursive bind in OpenShell's Podman driver

Base: main 5a9ec58. Follow-up to plan 4c Amendment 1, Q1 option C (`2026-10-14-plan4c-amendment1-s9-design.md` §3.2, on branch design/p4c-amend1). That amendment's option A ships first and is unchanged by this design. The facts behind it are in spike S9-8 (`docs/spikes/s9-managed-installs.md`).

**Scope:** a design only. It proposes one upstream change and how our backend would use it. Nothing is sent upstream (no issue, PR or comment) without Liam's go. Using the change also needs an OpenShell version bump past r15's 0.1.2 pin, which is its own review (§3.4).

Source read: the OpenShell source at tag v0.1.2 (commit 6648bd0c290e, the version `/usr/bin/openshell` reports), read-only. Paths below are relative to that tree.

## 1. Where 0.1.2 makes a bind recursive

- **The config type:** `crates/openshell-driver-podman/src/container.rs:131–140`. `PodmanDriverMountConfig::Bind` has exactly `source`, `target`, `read_only` (`default_true`) and `selinux_label`. The enum is `#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]`, so any other key is refused by name. S9-8 saw this as `unknown field ..., expected one of source, target, read_only, selinux_label`.
- **The recursion:** `container.rs:797–800`, in `podman_user_mounts`. Every user bind gets `options = [ro|rw, "rbind"]`, plus `z`/`Z` for `selinux_label`. The `"rbind"` literal at **line 799** is the only place a user bind's recursion is decided. Podman receives it as a libpod `Mount` (`container.rs:312–319`), and S9-8 saw it in `HostConfig.Binds` as `ro,rbind,rprivate`.
- **Not user binds:** `container.rs:1324`, `1354`, `1366` and `1380` are the driver's own binds (TLS files, proxy CA bundle, supervisor binary). They are out of scope and unchanged.
- **Validation:** `validate_podman_driver_mounts` (`container.rs:891–950`) checks only sources and targets. It needs no change.
- **Pinned behaviour:** two upstream tests pin `rbind` for user binds: `container_spec_includes_bind_mounts_when_enabled` (`container.rs:3110`) and `container_spec_defaults_enabled_bind_mounts_to_read_only` (`container.rs:3150`).
- **The Docker driver** (`crates/openshell-driver-docker`) builds its binds separately. It is out of scope, because we run only the Podman driver (r15).

## 2. The upstream change

**API.** `PodmanDriverMountConfig::Bind` gains one field:

```rust
#[serde(default = "default_true")]
recursive: bool,
```

- In JSON it is `"recursive": false` on a `"type": "bind"` mount.
- It is optional and defaults to `true`, so every existing config means what it means today.
- It is a JSON boolean; any other type is a deserialization error.
- `deny_unknown_fields` stays, so the `volume`, `tmpfs` and `image` variants refuse `recursive` by name.

**Semantics.** In `podman_user_mounts`, `"rbind"` (line 799) becomes `if recursive { "rbind" } else { "bind" }`. With `recursive: false`:
- Podman makes a plain bind (no `MS_REC`): only the mount that holds `source` is bound.
- A mount below `source` at create time is not carried in. The target shows what lies under that mount point in the namespace where Podman resolves the bind (Podman's own, for rootless). For an empty mount point, that is the empty directory.
- `read_only` then covers the whole visible tree, because there is only the one mount. S9-8's read-write carried submount can't occur.
- Propagation is unchanged (Podman's default `rprivate`), and so is `selinux_label`.

**Docs.** The `bind` row of the driver-config mount table (`docs/how-it-works/sandboxes/runtimes.mdx:112`) gains "optional `recursive` (default `true`; `false` binds only the source's own mount, without the mounts below it)".

**Upstream tests** (in `container.rs`'s test module, next to line 3110):
- `container_spec_bind_mount_non_recursive`: `"recursive": false` gives options containing `"bind"` and not `"rbind"`.
- The two existing tests also assert that `"bind"` is absent. The default stays recursive.
- `driver_config_rejects_non_boolean_recursive`: `"recursive": "no"` is refused.
- `driver_config_rejects_recursive_on_volume_mounts`: unknown on `volume`.

**Not proposed:**
- A capability string in the driver's `extension_metadata(..., [])` (`crates/openshell-driver-podman/src/driver.rs:582–587`). It would be visible only through `GetGatewayInfo`, which is a platform-admin RPC (`crates/openshell-server/src/auth/authz.rs:208`). Our detection doesn't need it (§3.1). Upstream may ask for one; it costs one line (`"openshell.podman.bind-non-recursive"`, valid under `valid_capability`, `crates/openshell-core/src/extension_protocol.rs:326`).
- Podman's `rro` (recursive read-only). It would make carried submounts read-only, but they would still be carried in and visible, so it doesn't give the ADR's property.

## 3. Our backend

### 3.1 Detecting support

- **Where:** `OpenShellBackend.create` (`src/heterodyne/sandbox/openshell.py`) runs `openshell status -o json` before it builds the create argv. This is the `QUERY_SECONDS` call the backend already makes for `sandbox list`.
- **What it reads:** the gateway's `version`, which the CLI takes from the unauthenticated `Health` RPC (`crates/openshell-cli/src/commands/gateway.rs:80–94`; `proto/openshell.proto:875–880`). It needs no admin role. The Podman driver runs inside the gateway and reports the same `openshell_core::VERSION` (`driver.rs:585`).
- **The rule:** support is `status == "connected"` and `version` is a member of `NONRECURSIVE_VERSIONS`. That is a frozenset in `openshell.py` of exact versions, each one added only by the review in §3.4. It is not a "≥" comparison: r15 pins exact versions, and a version nobody has reviewed is treated as unsupported.
- **Shipped empty:** the set ships empty, so the backend code can land before any upstream release. With the set empty, every launch behaves exactly as under option A.
- **Any failure means unsupported:** a non-zero exit, a timeout, output that isn't JSON, a missing key, or a version not in the set. The launch then proceeds as under option A. Nothing is skipped: the probe (§4) refuses a submount either way.

### 3.2 Using it

- `driver_config(spec, nonrecursive: bool)`: when `nonrecursive` is true, the **install bind** gets `"recursive": false` and becomes exactly five keys: `type`, `source`, `target`, `read_only: true`, `recursive: false`.
- Every other bind (`spec.binds`, `spec.logins`) keeps today's four keys. Whether those should be non-recursive too is a separate question, not decided here.
- With `nonrecursive` false, the output is byte-identical to option A's E24 object.
- `create_argv` passes the flag through.
- The backend writes the mode it used in its create log line (`bind=nonrecursive` or `bind=recursive`).
- **A gateway that changes between detection and create:**
  - A downgrade makes the create fail. Old OpenShell refuses `recursive` by name, so the backend raises `BackendError` naming the field, and the launch fails. It does not retry without the field. A silent fallback would hide a gateway that doesn't match what it reported.
  - An upgrade only means the field isn't sent, which is option A.

### 3.3 Fail-closed: what stays

- **The probe stays as written.** Task 8's Managed install probe keeps its first check unchanged in both modes: exactly one mount at the install, `ro`, no `shared:`/`master:` tag, and no line below it (`MI_SUBMOUNT`) (Amendment 1 §3.3).
- **The probe gets no mode input.** There is no setting that turns `MI_SUBMOUNT` off. Under the unsupported mode it is the control that keeps submounts out. Under the supported mode it is the second control: a submount line can only appear if the field was ignored or mis-implemented, and then the launch fails.
- **`check_fast` stays:** `CROSSES_MOUNT` still refuses a host-namespace mount under the install before create, in both modes. A host record can't describe a tree that crosses a mount.
- **The walk stays:** the probe's second check compares the tree with the host record. With a non-recursive bind over a Podman-namespace submount, the sandbox sees the directory underneath, which is what the host's own namespace sees. So the walk matches, and the launch succeeds without the mount.
- **Unchanged scope:** the exclusion of mutations made from inside the container's own namespaces after the probe stays as in Amendment 1 §3.2.

### 3.4 Turning it on (not part of this bead)

Adding a version to `NONRECURSIVE_VERSIONS` needs a reviewed change that:
1. bumps r15's 0.1.2 pin, with the renewed D23 review that `openshell_selftest.py` requires for any OpenShell version change;
2. re-runs S9-8 on that version: `HostConfig.Binds` shows `bind`, not `rbind`, for the install bind, and a Podman-namespace submount present at create time is absent inside;
3. adds the Task 13 controls in §4 to the live run.

The ADR's stronger wording ("before or after the launch, never appears") can come back only with that change, through an ADR amendment.

## 4. Tests

**Unit tests (offline), `tests/test_sandbox_openshell_backend.py` and `tests/test_sandbox_openshell_policy.py`:**
- `test_the_install_bind_is_exactly_the_four_s9_8_fields`: Amendment 1's test, now with `nonrecursive=False`. It is unchanged.
- `test_a_supported_gateway_adds_recursive_false_to_the_install_bind_only`: five keys on the install bind; the other binds keep four.
- `test_support_needs_an_exact_listed_gateway_version`: a fake runner gives `status` output with a listed version (true); an unlisted, a newer and an older version (false); and a non-zero exit, a timeout, non-JSON output, a missing `version` and `status != "connected"` (all false).
- `test_the_shipped_version_set_is_empty`: pins that nothing is enabled before §3.4's review. That review edits this test, so turning the field on is a visible diff.
- `test_a_gateway_that_refuses_recursive_fails_the_launch`: `create` returns `unknown field `recursive``; the backend raises `BackendError` naming it and makes no second create.
- `test_the_create_log_names_the_bind_mode`.

**Probe tests (`tests/test_sandbox_probes.py`):**
- Amendment 1's `MI_*` cases are unchanged.
- `test_the_probe_config_has_no_submount_switch`: `probe_config` carries no key that disables `MI_SUBMOUNT`.

**Older OpenShell (0.1.2, today's pin):**
- Detection is always false: 0.1.2 is not in the set, and the set is empty.
- `driver_config` emits option A's four keys, and a submount is refused by `MI_SUBMOUNT`, exactly as Amendment 1 specifies.
- If the field were forced, 0.1.2 refuses the create by name, so the launch fails without creating a sandbox.

**Live (Task 13, only after §3.4, with the lead's go for Podman):**
- On a supported version, a Podman-namespace `tmpfs` mounted under the install before create:
  - the launch succeeds;
  - the sandbox's `mountinfo` has exactly one line at or below the install;
  - the walk matches the host record.
- A host-namespace mount under the install is still refused as `INSTALL_CHANGED` (`CROSSES_MOUNT`) before create.
- The 0.1.2 controls from Amendment 1 §4.4 stay in the run for as long as 0.1.2 is a supported pin.
