# DRAFT: OpenShell proposal, a non-recursive Podman bind

**Status: draft only, not sent.** Opening an issue, PR or fork outside our org needs a separate go from Liam. The design is btq-wazo1 (`docs/superpowers/specs/2026-10-14-openshell-nonrecursive-bind-design.md` on design/wazo1), and it is written against OpenShell v0.1.2 (commit 6648bd0c290e).

## Title

podman driver: optional `recursive` on `bind` mounts (default `true`)

## Problem

Every user bind in the Podman driver is recursive. `podman_user_mounts` (`crates/openshell-driver-podman/src/container.rs:797-800`) always passes `"rbind"`. Any mount below `source` at create time is therefore carried into the sandbox, and it keeps its own flags. A `read_only: true` bind can show a read-write mount below it. A caller that wants exactly the source's own mount has no way to ask for it: `PodmanDriverMountConfig::Bind` refuses any other key (`deny_unknown_fields`).

## Change

`PodmanDriverMountConfig::Bind` (`container.rs:131-140`) gains one field:

```rust
#[serde(default = "default_true")]
recursive: bool,
```

In `podman_user_mounts`, the `"rbind"` literal (line 799) becomes:

```rust
if recursive { "rbind" } else { "bind" }
```

- The field is optional and defaults to `true`, so every existing config keeps its meaning.
- `deny_unknown_fields` stays, so `volume`, `tmpfs` and `image` still refuse `recursive`.
- Propagation and `selinux_label` are unchanged, and so are the driver's own binds (`container.rs:1324`, `1354`, `1366`, `1380`).

## Docs

In the `bind` row of the mount table (`docs/how-it-works/sandboxes/runtimes.mdx:112`), add: "optional `recursive` (default `true`; `false` binds only the source's own mount, without the mounts below it)".

## Tests

These go in `container.rs`'s test module.

- `container_spec_bind_mount_non_recursive`: with `"recursive": false`, the options contain `"bind"` and not `"rbind"`.
- `container_spec_includes_bind_mounts_when_enabled` (:3110) and `container_spec_defaults_enabled_bind_mounts_to_read_only` (:3150) also assert that `"bind"` is absent.
- `driver_config_rejects_non_boolean_recursive`: `"recursive": "no"` is refused.
- `driver_config_rejects_recursive_on_volume_mounts`: the field is unknown on `volume`.

## Not proposed

- A capability string. Callers can detect support from the gateway version, which is available without the admin role.
- `rro`. It makes carried submounts read-only, but they are still carried in.
