# Install

heterodyne-metaharness is in development. Today you can install it from a source checkout, create a host configuration and validate it. The one service that exists is `admind` (below); the rest is not built yet. See the status section of the [README](../README.md).

## Platforms

| Platform | Status | Service manager | Sandbox backend |
|---|---|---|---|
| Linux | **Supported in v1** | systemd user units | OpenShell, gated on spike S5; bubblewrap only if S5 is closed as failed (ADR 0001 §7) |
| macOS | **Phase 2**, not supported in v1 | launchd agents | Seatbelt (`sandbox-exec`) |

The platform is detected once, when `heterodyne setup` runs, and the chosen backends are recorded in the host config (ADR 0001 §3.2). On macOS, `heterodyne platform` and `setup` already recognise the platform and record `launchd` and `seatbelt`, and the CI matrix includes macOS. Nothing has been run on a macOS host yet, and the backends themselves are phase-2 work. Any other platform is refused. The sandbox backend that `setup` records today is `bubblewrap` on Linux (see `[platform]` in [configuration](configuration.md)); that is the current setup default, not the v1 target above, and no sandbox runs yet (plan 4).

## Prerequisites

| Prerequisite | Needed for | Used by the code today? |
|---|---|---|
| Python 3.12 or newer | everything | yes |
| [uv](https://docs.astral.sh/uv/) | dependencies, running and testing | yes |
| git | the repository checks | yes (the install-agnostic checker lists tracked files with git) |
| OpenShell, or bubblewrap if S5 fails (Linux) | the agent sandbox | not yet (plan 4) |
| btq, the Beads task-queue client | beads integration | by `wsd`, from `config.toml` `[integrations.beads]` (`btq` is the checkout) |
| `wn-agent`, the Marmot client | the Marmot surface; `admind` runs its own private copy | by `admind`; the `wsd` surface goes in `config.toml` `[integrations.marmot]` later |
| tmux, `claude` | the `admind` admin agent | yes |

btq and `wn-agent` are external integrations. They are not vendored, and their locations and credentials come only from host config.

## Install from source

```sh
git clone <repository-url> heterodyne-metaharness
cd heterodyne-metaharness
uv sync
uv run heterodyne platform
```

The package's only runtime dependency is `msgspec`. `uv sync` also installs the development tools (pytest, hypothesis, ruff and pyright).

## `heterodyne setup`

```sh
uv run heterodyne setup
```

This is the first version of `setup`. The interactive version, which imports values from an existing install and generates service units, comes in a later plan. Today it does the following:

1. Finds the host config directory (`HETERODYNE_CONFIG_DIR`, else `${XDG_CONFIG_HOME:-~/.config}/heterodyne`), and creates it with mode 0700 if it doesn't exist.
2. Detects the platform and its backends.
3. Copies `examples/config.toml` and `examples/policy.toml` into the directory. It fills in three placeholders, `<linux-or-macos>`, `<service-manager>` and `<sandbox-backend>`, so `[platform]` records `os`, `service_manager` and `sandbox`. Every other `<placeholder>` is left for you to edit.
4. **Never overwrites.** Each file is created with `O_CREAT | O_EXCL | O_NOFOLLOW` and mode 0600, so it is private from the moment it exists, and creation fails if anything already has that name, including a symlink, which is never followed. An existing file is reported as `kept existing <path> (not modified)`, and its content and mode are left alone.
5. Loads and validates the resulting configuration. If it doesn't validate, `setup` prints `config error: <message>` and exits 1.

`setup` reads the examples from the source checkout, so run it from there (`uv run`). It does not create workstream files: copy `examples/workstreams/example.toml` to `workstreams/<ws>.toml` in the host config directory and edit it. Then check the result:

```sh
uv run heterodyne config check
uv run heterodyne config check --workstream <ws>
```

The configuration rules are in [configuration.md](configuration.md).

## Admin channel (`admind`)

`admind` is implemented. It is a separate service with its own Marmot identity, set up after `heterodyne setup` and a validated configuration: add `[admind]` to `config.toml` and `operators` to `policy.toml`, run `admind init`, render the systemd user unit with `admind unit`, and enable it. It needs `claude` and `wn-agent` on the `PATH` of the shell that renders the unit, and tmux. The full steps, the first-launch dialogs, the latch and the troubleshooting guide are in [admind.md](admind.md).

## Repository checks

The repository is install-agnostic (ADR 0001 §15): no committed file may contain an install-specific value. These checks enforce it, and they run the same way locally, in pre-commit and in CI.

### The install-agnostic checker

```sh
python3 scripts/check_install_agnostic.py [--root DIR] [--extra FILE] [paths...]
```

- With no paths, it scans every file tracked by git. It prints one `path:line: rule: excerpt` line per hit, and exits 1 if there are any hits, 2 on a usage error or if `git ls-files` fails, and 0 otherwise.
- **Rules:** `home-path` (absolute home directories), `ip-port` (address and port literals), `email` (any domain except `example.com`, `example.org` and `example.net`), `npub`, `nsec`, `did`, `radicle-id`, and, under `src/` only, `model-name` (ADR §4.1: the code names no models).
- **Exempt:** everything under `examples/` and `tests/fixtures/`, plus the exact paths `LICENSE` and `scripts/check_install_agnostic.py`.
- **Fails closed.** Nothing is silently skipped. Invalid UTF-8 is still scanned line by line; binary files are scanned byte by byte for every rule; a symlink is scanned as its target string; a submodule, or a missing or unreadable path, is reported as a hit of its own (`submodule`, `unreadable`).

### The leak-check deny-list

The regexes can't know your username or hostname, so each operator keeps a local deny-list of literal strings that must never appear in the repository:

- **Location:** `leakcheck.txt` in the host config directory, that is `$HETERODYNE_CONFIG_DIR/leakcheck.txt`, else `${XDG_CONFIG_HOME:-~/.config}/heterodyne/leakcheck.txt`. `--extra FILE` names a different file.
- **Format:** one literal per line, such as your username, hostname, home directory or group IDs. Blank lines and lines starting with `#` are ignored.
- The checker uses the default file automatically when it exists; hits are reported as `local-denylist`. An `--extra` file that doesn't exist is an error (exit 2), not a silent pass.
- **Never commit it.** It lives outside the repository, and `.gitignore` also excludes any `leakcheck.txt`.

### The per-line allow marker

A line that legitimately matches a rule can carry a marker that suppresses the named rules on that line only:

```text
install-agnostic: allow=<rule>[,<rule>...]
```

Say why in the same comment. For example, the sandbox spike uses a public anycast address as a fixed "egress must fail" probe target and marks that line `allow=ip-port`. The marker never suppresses `local-denylist`, and it has no effect in binary files.

### pre-commit

`.pre-commit-config.yaml` has three hooks: the install-agnostic checker on the staged files (with your default deny-list, if present), `ruff check`, and gitleaks (pinned by commit, scanning staged changes).

```sh
uvx pre-commit install          # once per clone
uvx pre-commit run --all-files  # on demand
```

### CI

`.github/workflows/ci.yml` runs on every push and pull request, with read-only permissions and actions pinned by commit SHA.

- **`test`**, on Ubuntu and macOS (the platform-seam matrix): `uv sync --locked`, `ruff check`, `pyright`, `pytest`, then the install-agnostic checker. CI runners have no deny-list, so only the regex rules apply there.
- **`secrets`**: a checksum-verified gitleaks release scans every v2 commit (`--redact`). History before the first v2 commit is out of scope by operator decision, and the job fails if the v2 base commit is missing, is not an ancestor, or the range is empty.

Before committing, run all of them locally:

```sh
uv run ruff check && uv run pyright && uv run pytest -q
python3 scripts/check_install_agnostic.py
uvx pre-commit run --all-files
```
