"""Live checks of the OpenShell sandbox runtime: plan 4's live gate. Needs HZ_LIVE=1 (the conftest) and
HZ_LIVE_SANDBOX=1, the S5 host setup, the agent image, a default login for each CLI, and each CLI's unpacked
release package at its declared version (HZ_LIVE_SANDBOX_CODEX_PACKAGE: the Codex package archive's tree;
HZ_LIVE_SANDBOX_CLAUDE_PACKAGE: the Claude Code platform package's `package/` tree), which the module
publishes into its own scratch store and selects (plan 4c T8; Task 10 adds the command that fetches one).
It makes real model calls: one per launch, plus one prompt. It prints nothing from any login.

Run it only while no wsd on this host uses the OpenShell runtime: that wsd would see these sandboxes
without a record and hold its workstreams until they are gone."""

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))     # tests/: sandbox_env, wsd_env

from harness_env import Fixture, defaults_part, host_part, store_fixture  # noqa: E402
from probe_verdicts import (  # noqa: E402
    CHANNEL,
    ENV_REJECTED,
    FILE_OPS,
    NAMESPACE_REJECTED,
    NOT_THE_PROBE,
    SELFTEST_FAILED,
    UNRECOGNISED,
    file_writes_refused,
    launch_shown,
    memory_access_refused,
    namespaces_refused,
    outputs_unreachable,
    overlay_outcome,
    rejection_shown,
    require,
    session_socket_checks,
    shown,
    tool_var_rejection,
)
from sandbox_env import config_env, gateway_listener, sandbox_teardown, unseal_all  # noqa: E402
from wsd_env import ADAPTERS, git_repo  # noqa: E402

from heterodyne.agents.registry import ADAPTERS as REGISTRY  # noqa: E402
from heterodyne.sandbox import channel as probe_channel  # noqa: E402
from heterodyne.sandbox import openshell_selftest  # noqa: E402
from heterodyne.sandbox.channel import ProbeChannel  # noqa: E402
from heterodyne.sandbox.openshell import OpenShellBackend, tool_env  # noqa: E402
from heterodyne.sandbox.openshell_selftest import (  # noqa: E402
    AGENT_CHECKS,
    EXEC_CHECKS,
    PROBE_ARGV,
    PROBES_SOURCE,
    VERDICT,
    OpenShellSelfTest,
    env_allowed,
    probe_config,
    ptrace_scope_refusal,
    read_ptrace_scope,
)
from heterodyne.sandbox.runtime import (  # noqa: E402
    Phase,
    RuntimeConfig,
    SandboxRuntime,
    SessionRecord,
    read_record,
)
from heterodyne.sandbox.selftest import ProbeContext, SelfTestFailed  # noqa: E402
from heterodyne.sandbox.settings import SandboxSettings, sandbox_settings  # noqa: E402
from heterodyne.sandbox.spec import sandbox_name  # noqa: E402
from heterodyne.tmux import Tmux  # noqa: E402
from heterodyne.wsd import ids  # noqa: E402
from heterodyne.wsd.accounts import ConfiguredAccounts  # noqa: E402
from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec, Liveness, Started  # noqa: E402
from heterodyne.wsd.workstream import utc_now  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("HZ_LIVE_SANDBOX") != "1",
                                reason="live sandbox checks: set HZ_LIVE_SANDBOX=1 (and HZ_LIVE=1)")
# Test-only, for cost (Liam, T14): a cheap model with a small context on every live launch, never the
# operator's default. Overridable by environment; production defaults are untouched.
PROFILES = {"claude": "p-one", "codex": "p-live-codex"}
MODELS = {"claude": os.environ.get("HZ_LIVE_SANDBOX_CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
          "codex": os.environ.get("HZ_LIVE_SANDBOX_CODEX_MODEL", "gpt-6-luna")}
CODEX_EFFORT = 'model_reasoning_effort = "low"\n'
CODEX_HEADER = f"{MODELS['codex'].lower()} low"   # the TUI status line, lowercased
CLIS = {profile: cli for cli, profile in PROFILES.items()}
PIN_SECONDS = 90
PROMPT_SECONDS = 180
EXEC_SECONDS = 60
# D23's precondition, the inspected artifact (plan 4 Task 9B): OpenShell 0.1.2 on the podman driver with no
# image or supervisor override, so the driver runs its default images, each named by tag. The running
# gateway is checked and recorded before any launch, and the containers each launch runs on after it.
# The driver pulls an image it lacks, so a missing one fails here first.
OPENSHELL_VERSION = "openshell 0.1.2"
GATEWAY_PACKAGE = ("openshell", "0.1.2-1", "/usr/bin/openshell-gateway")
GATEWAY_ENV = {"OPENSHELL_COMPUTE_DRIVER", "OPENSHELL_GATEWAY_CONFIG", "OPENSHELL_LOCAL_TLS_DIR"}
RUNTIME_IMAGE = "ghcr.io/nvidia/openshell/sandbox:0.1.2"
SUPERVISOR_IMAGE = "ghcr.io/nvidia/openshell/supervisor:0.1.2"
DRIVER_IMAGES = (RUNTIME_IMAGE, SUPERVISOR_IMAGE)
# The workload's runtime binary (it applies the prelude, the broker and the child filters D23 relies on) is
# not an image mount: on every create the driver copies /openshell-sandbox out of the runtime image into a
# host cache keyed by the image's ID, reused once there (v0.1.2 driver.rs:1124-1134, :1895-1960), and binds
# that file at SUPERVISOR_CONTAINER_BINARY (container.rs:1365-1375; driver_utils.rs:94-102, :906-923).
RUNTIME_BINARY = "/opt/openshell/bin/openshell-sandbox"
CACHE_RESIDUAL = ("the cache is host-side and same-UID, keyed by the image ID and checked only as ELF; it "
                  "is not compared with the image's own /openshell-sandbox")
PREFLIGHT_SECONDS = 30


@dataclass(frozen=True)
class Artifact:
    podman: tuple[str, ...]          # podman on the running gateway's own endpoint
    images: dict[str, str]           # image -> its ID there
    runtime_binary: Path             # the driver's cache of the runtime image's binary
    runtime_sha256: str              # its digest at the preflight
    lines: tuple[str, ...]           # the evidence: versions, key names and IDs, never a value or a path


@dataclass
class Live:
    root: Path
    runtime: SandboxRuntime
    backend: OpenShellBackend
    tmux: Tmux
    repo: Path
    artifact: Artifact
    installs: dict[str, Fixture]                            # adapter -> its published, selected install
    keys: list[str] = field(default_factory=list[str])      # every session this module launched
    first: dict[str, tuple[LaunchSpec, Started]] = field(
        default_factory=dict[str, tuple[LaunchSpec, Started]])     # cli -> its first launch
    owners: dict[str, SandboxRuntime] = field(
        default_factory=dict[str, SandboxRuntime])      # key -> the control's runtime it was launched through
    uncertain: list[str] = field(default_factory=list[str])     # what a control could not confirm gone

    def worktree(self, bead: str) -> Path:
        """The bead's linked worktree on `btq/<bead>`, as btq makes it."""
        path = self.root / f"w-{bead}"
        if not path.exists():
            git(self.repo, "worktree", "add", "-q", "-b", f"btq/{bead}", str(path))
        return path

    def spec(self, cli: str, bead: str, role: str = "coder") -> LaunchSpec:
        profile = PROFILES[cli]
        key = ids.role_session(bead, role, profile)
        adapter = self.runtime.c.accounts.adapter(profile) or ""
        pin = self.installs[adapter].pin                    # the guard pins the selection (plan 4c D2)
        return LaunchSpec("alpha", bead, role, profile, key, f"{bead} · {role}", self.worktree(bead),
                          resume=False, native_id=key if cli == "claude" else None, model=MODELS[cli],
                          repo=self.repo,
                          credential_key=self.runtime.c.accounts.current_key(adapter, "default"),
                          install=pin.install, install_digest=pin.digest)

    def launch(self, spec: LaunchSpec) -> Started:
        self.keys.append(spec.session_key)
        started = self.runtime.launch(spec)
        self.pinned(spec, started)
        return started

    def pinned(self, spec: LaunchSpec, started: Started) -> None:
        """Every model call so far in this session, the self-test's included, ran on the pinned model."""
        cli = CLIS[spec.profile]
        assert started.native_id is not None
        used = models_used(self.runtime.layout(spec.session_key).home, cli, started.native_id)
        assert used == {MODELS[cli]}, f"{cli} ran on {sorted(used)}, not the pinned {MODELS[cli]}"
        attempts = getattr(self.runtime.selftest, "freeze_attempts", None)
        print(f"EVIDENCE {spec.session_key} g{spec.generation} models={sorted(used)} "
              f"freeze-attempts={attempts} {self.ran_on(spec.session_key)}")     # for docs/wsd.md

    def ran_on(self, key: str) -> str:
        """The images the session's containers run, through the gateway's endpoint: the workload on the
        agent image, its supervisor on the driver's supervisor image, each the ID the preflight recorded;
        the runtime binary bound from the driver's cache for the preflight's runtime image ID, unchanged
        through the run; and any image the workload mounts one the preflight recorded too."""
        name, images = self.record(key).sandbox, self.artifact.images

        def podman(*argv: str) -> str:
            return tool(self.backend.env, *self.artifact.podman, *argv)

        prefix = f"openshell-default--{name}-"
        found = podman("ps", "--filter", f"name=^{prefix}", "--format", "{{.Names}}").split()
        assert len(found) == 1, f"{name} runs {len(found)} workload containers, not one"
        workload = podman("container", "inspect", "--format", "{{.Image}}", found[0])
        supervisor = podman("container", "inspect", "--format", "{{.Image}}",
                            f"openshell-supervisor-{found[0].removeprefix(prefix)}")
        assert workload == images[self.backend.image], f"{name}'s workload runs image {workload[:12]}"
        assert supervisor == images[SUPERVISOR_IMAGE], f"{name}'s supervisor runs image {supervisor[:12]}"
        listed = json.loads(podman("container", "inspect", "--format", "{{json .Mounts}}", found[0])) or []
        mounts = [m for m in listed if isinstance(m, dict)]
        runtime = [m for m in mounts if m.get("Destination") == RUNTIME_BINARY]
        assert len(runtime) == 1 and runtime[0].get("Type") == "bind", \
            f"{name}'s runtime binary is not one bind mount at {RUNTIME_BINARY}"
        require(runtime[0].get("Source") == str(self.artifact.runtime_binary),
                f"{name}'s runtime binary is not the driver's cache for the preflight's {RUNTIME_IMAGE}")
        digest = sha256(self.artifact.runtime_binary)
        assert digest == self.artifact.runtime_sha256, f"{name}'s runtime binary changed in the run"
        mounted: list[str] = []
        for m in mounts:
            if m.get("Type") != "image":
                continue
            said = {str(m.get(k, "")).removeprefix("sha256:") for k in ("Name", "Source")} - {""}
            known = [i for i, iid in images.items()
                     if i in said or any(len(x) >= 12 and iid.startswith(x) for x in said)]
            assert known, f"{name} mounts an image the preflight didn't see, at {m.get('Destination')}"
            mounted += known
        return (f"workload-image={workload[:12]} supervisor-image={supervisor[:12]} runtime-binary="
                f"cache-of-{images[RUNTIME_IMAGE][:12]}:sha256-{digest[:12]} image-mounts={mounted}")

    def record(self, key: str) -> SessionRecord:
        rec = read_record(self.runtime.layout(key))
        assert rec is not None
        return rec

    def run(self, key: str, *argv: str) -> int:
        rec = self.record(key)
        return self.backend.exec(rec.sandbox, Path(rec.worktree), list(argv), timeout=EXEC_SECONDS).returncode


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def wait_until(pred: Callable[[], bool], seconds: float) -> bool:
    end = time.monotonic() + seconds
    while not pred():
        if time.monotonic() > end:
            return False
        time.sleep(1)
    return True


def models_used(home: Path, cli: str, native_id: str) -> set[str]:
    """The models a session's own log in its synthetic home records: Codex's rollout turn contexts, or
    Claude's assistant messages (less its local "<synthetic>" ones). Nothing recorded fails the pin."""
    if cli == "codex":
        logs = (home / ".codex" / "sessions").glob(f"*/*/*/rollout-*-{native_id}.jsonl")
        kind, holder = "turn_context", "payload"
    else:
        logs = (home / ".claude" / "projects").glob(f"*/{native_id}.jsonl")
        kind, holder = "assistant", "message"
    used: set[str] = set()
    for log in logs:
        for line in log.read_text().splitlines():
            entry = json.loads(line) if line.strip() else {}
            if isinstance(entry, dict) and entry.get("type") == kind and isinstance(entry.get(holder), dict):
                model = entry[holder].get("model")
                used |= {model} if isinstance(model, str) and model != "<synthetic>" else set()
    return used


BYPASSED = ("never", "danger-full-access")      # the approval and sandbox policy the first launch's flag sets


def codex_policies(home: Path, native_id: str) -> list[tuple[str, str]]:
    """Each Codex turn's effective (approval_policy, sandbox_policy type), from the thread's rollout."""
    found: list[tuple[str, str]] = []
    for log in (home / ".codex" / "sessions").glob(f"*/*/*/rollout-*-{native_id}.jsonl"):
        for line in log.read_text().splitlines():
            entry = json.loads(line) if line.strip() else {}
            payload = entry.get("payload") if isinstance(entry, dict) else None
            if entry.get("type") == "turn_context" and isinstance(payload, dict):
                sandbox = payload.get("sandbox_policy")
                found.append((str(payload.get("approval_policy")),
                              str(sandbox.get("type") if isinstance(sandbox, dict) else sandbox)))
    return found


class PinnedSelfTest(OpenShellSelfTest):
    """Test-only: the self-test's own prompt is each launch's first model call, and Codex's app-server is
    started without the TUI's --model. So the model and effort the TUI's status line shows for the thread,
    as the server configured it, are required before that prompt is pasted: a mismatch fails the launch
    with no call. The line shows the model's display name ("GPT-6-Luna"), so it is compared lowercased."""

    def agent_path(self, ctx: ProbeContext) -> None:
        if ctx.adapter.name == "codex" and not wait_until(
                lambda: CODEX_HEADER in ctx.tmux.capture(ctx.tmux_session, 50).lower(), PIN_SECONDS):
            raise SelfTestFailed("live-model-pin")
        super().agent_path(ctx)


PACKAGES = {"claude-code": "HZ_LIVE_SANDBOX_CLAUDE_PACKAGE", "codex": "HZ_LIVE_SANDBOX_CODEX_PACKAGE"}


def live_installs(root: Path, config_dir: Path) -> dict[str, Fixture]:
    """Each CLI's unpacked release package, published into a scratch store under `root` and selected in
    `config_dir`. Codex's effort is the install's `defaults` part (`config.toml`), merged under the
    launcher's keys by `prepare_install_home`, since Q4 refuses profile args and no argv carries `-c`."""
    installs: dict[str, Fixture] = {}
    for adapter, var in sorted(PACKAGES.items()):
        source = Path(os.environ.get(var, ""))
        require(bool(os.environ.get(var)) and source.is_dir(),
                f"preflight: {var} names no unpacked {adapter} package")
        parts = [host_part(adapter, source)]
        if adapter == "codex":
            parts.append(defaults_part("config.toml", CODEX_EFFORT.encode()))
        installs[adapter] = store_fixture(root, adapter, config_dir, parts=parts)
    return installs


def host_config() -> str:
    tool_env = {"PATH": os.environ["HZ_LIVE_SANDBOX_PATH"]}
    if os.environ.get("HZ_LIVE_SANDBOX_CONTAINERS_CONF"):
        tool_env["CONTAINERS_CONF"] = os.environ["HZ_LIVE_SANDBOX_CONTAINERS_CONF"]
    pairs = ", ".join(f'{k} = "{v}"' for k, v in tool_env.items())
    image = os.environ["HZ_LIVE_SANDBOX_IMAGE"]
    return ('\n[profiles.p-live-codex]\nadapter = "codex"\n'
            f'\n[platform]\nsandbox = "openshell"\n\n[sandbox]\nimage = "{image}"\n'
            f'egress_approved = ["github.com", "pypi.org"]\ntool_env = {{ {pairs} }}\n')


def tiny_repo(path: Path) -> Path:
    """The scratch repository, kept small for the agents' context: a README and one file, and no
    instructions file (AGENTS.md, CLAUDE.md)."""
    git_repo(path)
    (path / "README.md").write_text("hz live scratch\n")
    (path / "hello.txt").write_text("hello\n")
    git(path, "add", "README.md", "hello.txt")
    git(path, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "files")
    return path


def tool(env: dict[str, str], *argv: str) -> str:
    """A host tool's stdout, as the backend runs it; a failure or a timeout fails the module."""
    try:
        proc = subprocess.run(list(argv), stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
                              timeout=PREFLIGHT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.fail(f"preflight: {Path(argv[0]).name} {argv[1]}: {type(exc).__name__}")
    if proc.returncode != 0:
        pytest.fail(f"preflight: {Path(argv[0]).name} {argv[1]} exited {proc.returncode}")
    return proc.stdout.strip()


def keys_of(table: dict[str, object], prefix: str = "") -> list[str]:
    found: list[str] = []
    for key, value in table.items():
        found.append(f"{prefix}{key}")
        if isinstance(value, dict):
            found += keys_of(cast(dict[str, object], value), f"{prefix}{key}.")
    return found


def gateway_process(openshell: str, env: dict[str, str]) -> tuple[int, str]:
    """The running gateway the CLI talks to: `openshell status` names its endpoint and the version it
    reports, and the process listening there is found by its socket (gateway_listener). Its PID and that
    version."""
    status = dict(line.strip().split(": ", 1) for line in tool(env, openshell, "status").splitlines()
                  if ": " in line)
    endpoint = re.fullmatch(r"https://127\.0\.0\.1:(\d+)", status.get("Server", ""))
    assert endpoint and status.get("Status") == "Connected", "preflight: the gateway is not up on loopback"
    port = int(endpoint[1])
    try:
        pid = gateway_listener(tool(env, "ss", "-ltnpeH", f"sport = :{port}"), port)
    except (OSError, ValueError) as exc:
        pytest.fail(f"preflight: the gateway's endpoint: {type(exc).__name__}")
    return pid, status.get("Version", "")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def started_at(pid: int) -> float:
    """A process's start, in seconds since the epoch: /proc/<pid>/stat's starttime after the boot time."""
    ticks = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    boot = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                if line.startswith("btime "))
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def preflight(settings: SandboxSettings, env: dict[str, str], installs: dict[str, Fixture]) -> Artifact:
    """The artifact the launches run on, checked against D23's precondition: a mismatch fails the module
    before any sandbox exists. Every check is of the running gateway, read-only: its binary is the
    package's, unchanged; its configuration is what it was started with (its own environment, and a
    gateway.toml not changed since); its images are the ones in the store its podman endpoint serves.
    Nothing is pulled, and no value or path is recorded."""
    version = tool(env, settings.openshell, "--version")
    assert version == OPENSHELL_VERSION, f"preflight: {version!r} is not the inspected {OPENSHELL_VERSION}"
    pid, reported = gateway_process(settings.openshell, env)
    assert f"openshell {reported}" == OPENSHELL_VERSION, f"preflight: the gateway reports {reported!r}"
    package, release, binary = GATEWAY_PACKAGE
    assert tool(env, "dpkg-query", "-W", "-f", "${Version}", package) == release, \
        f"preflight: the {package} package is not {release}"
    sums = Path(f"/var/lib/dpkg/info/{package}.md5sums").read_text().split()
    packaged = sums[sums.index(binary.lstrip("/")) - 1]
    exe = Path(f"/proc/{pid}/exe")
    digest = hashlib.md5(exe.read_bytes(), usedforsecurity=False).hexdigest()     # dpkg records md5
    require(str(exe.readlink()) == binary and digest == packaged,
            f"preflight: the running gateway is not the packaged {binary}")
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]
    assert len(argv) == 1, "preflight: the gateway was started with arguments"
    penv = dict(e.split("=", 1) for e in Path(f"/proc/{pid}/environ").read_text().split("\0") if "=" in e)
    named = sorted(k for k in penv if k.startswith("OPENSHELL_"))
    require(set(named) <= GATEWAY_ENV and penv.get("OPENSHELL_COMPUTE_DRIVER") == "podman",
            f"preflight: the gateway's environment sets "
            f"{[n if n in GATEWAY_ENV else UNRECOGNISED for n in named]}")
    config = Path(penv["OPENSHELL_GATEWAY_CONFIG"])
    unit_env = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "openshell" / "gateway.env"
    start = started_at(pid)
    assert all(f.stat().st_mtime < start for f in (config, unit_env)), \
        "preflight: the gateway's configuration changed after it started"
    gateway = tomllib.loads(config.read_text())
    keys = sorted(keys_of(gateway))
    assert gateway.get("openshell", {}).get("gateway", {}).get("compute_driver") == "podman", \
        "preflight: the gateway is not on the podman compute driver"
    overrides = [k for k in keys if "image" in k.lower() or "supervisor" in k.lower()]
    assert not overrides, f"preflight: the gateway configuration overrides {overrides}"
    socket = gateway.get("openshell", {}).get("drivers", {}).get("podman", {}).get("socket_path")
    assert isinstance(socket, str) and socket, "preflight: the gateway names no podman socket"
    podman = (settings.podman, "--url", f"unix://{socket}")
    server = tool(env, *podman, "version", "--format", "{{.Server.Version}}")
    assert server.startswith("5."), f"preflight: the gateway's podman is {server!r}, not podman 5"
    lines = [f"EVIDENCE artifact {version}; gateway reports {reported}, the packaged {package} {release} "
             f"binary, no arguments, environment {named}, toml keys {keys}; its podman endpoint {server}"]
    # The image carries no agent CLI: each is a managed install, verified, bound read-only into the sandbox.
    for name, adapter in sorted(REGISTRY.items()):
        cli = installs[name].cli()
        said = tool(env, str(cli.binary), "--version")
        assert cli.version in adapter.versions and cli.version in said.split(), \
            f"preflight: the {name} CLI is {cli.version} ({said!r}), not a version the adapter declares"
        lines.append(f"EVIDENCE artifact {name} {said} install {cli.install}")
    images: dict[str, str] = {}
    for image in (settings.image, *DRIVER_IMAGES):
        found = tool(env, *podman, "image", "inspect", "--format", "{{.Id}} {{json .RepoDigests}}", image)
        iid, digests = found.split(maxsplit=1)
        images[image] = iid
        short = [d.split("@", 1)[0] + "@sha256:" + d.rsplit(":", 1)[1][:12]
                 for d in json.loads(digests) or []]
        lines.append(f"EVIDENCE artifact image {image} id {iid.removeprefix('sha256:')[:12]} {short}")
    # driver_utils.rs supervisor_cache_path: the gateway's XDG data directory, then the driver's subdirectory
    # and the image ID with ':' made '-'. The gateway's own environment resolves it.
    data = Path(penv.get("XDG_DATA_HOME") or Path(penv["HOME"]) / ".local" / "share")
    cache = (data / "openshell" / "podman-sandbox" / images[RUNTIME_IMAGE].replace(":", "-")
             / "openshell-sandbox")
    require(cache.is_file(), f"preflight: the runtime binary of {RUNTIME_IMAGE} isn't cached yet: the "
                             "gateway extracts it on its first sandbox create")
    cached = sha256(cache)
    lines.append(f"EVIDENCE artifact runtime binary: bind-mounted from the driver's cache for "
                 f"{RUNTIME_IMAGE} id {images[RUNTIME_IMAGE][:12]}, sha256 {cached[:12]}; "
                 f"residual: {CACHE_RESIDUAL}")
    return Artifact(podman, images, cache, cached, tuple(lines))


def teardown(live: Live) -> list[str]:
    return [*live.uncertain, *sandbox_teardown(live.runtime, live.keys, live.backend.names, live.owners)]


@pytest.fixture(scope="module")
def live() -> Iterator[Live]:
    real_home = Path.home()
    canary = real_home / ".heterodyne-live-canary"
    root = Path(tempfile.mkdtemp(prefix="hz", dir=tempfile.gettempdir()))   # short_dir's, kept when unsure
    live: Live | None = None
    try:
        env = config_env(root / "c", host_config(),
                         {"alpha": '[sandbox]\nextra_egress = ["github.com", "pypi.org"]\n'})
        settings = sandbox_settings(env)
        tmux = Tmux(f"hz-live-sb-{uuid.uuid4().hex[:8]}")
        backend = OpenShellBackend(settings.openshell, settings.podman, settings.image,
                                   tool_env(os.environ, settings.tool_env))     # as wsd's build_runtime
        config_dir = Path(env["HETERODYNE_CONFIG_DIR"])
        installs = live_installs(root / "i", config_dir)
        artifact = preflight(settings, backend.env, installs)
        print("\n".join(artifact.lines))
        # accounts_at's profiles, plus the cheap Codex profile, each on its default login in the real home
        accounts = ConfiguredAccounts({**ADAPTERS, PROFILES["codex"]: "codex"}, {"HOME": str(real_home)})
        config = RuntimeConfig(
            sessions=root / "s", credlocks=root / "k", creates=root / "creates", alerts=root / "alerts",
            settings=settings, accounts=accounts, real_home=real_home,
            real_home_canary=canary, wsd_socket=root / "ctl.sock", uid=os.getuid(), gid=os.getgid(),
            tmux=tmux, clock=utc_now, store=installs["codex"].store, taint_dir=installs["codex"].taint_dir,
            config_dir=config_dir, store_problems=list)     # the scratch store: not the configured location
        live = Live(root, SandboxRuntime(config, backend, PinnedSelfTest()), backend, tmux,
                    tiny_repo(root / "r"), artifact, installs)
        yield live
    finally:
        uncertain = teardown(live) if live is not None else []
        if uncertain:      # the records, the reapers' tmux server and the scratch stay for recovery
            pytest.fail(f"live teardown is uncertain; kept the scratch {root} and tmux server "
                        f"{live.tmux.socket_name if live else None}: " + "; ".join(uncertain))
        if live is not None:
            live.tmux.kill_server()
        canary.unlink(missing_ok=True)
        unseal_all(root)                    # the published installs are 0555
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_both_shapes_launch_through_the_full_self_test(live: Live, cli: str) -> None:
    # One session per bead, as btq claims are: two on one bead branch can't both land.
    spec = live.spec(cli, {"claude": "btq-live-1", "codex": "btq-live-4"}[cli])
    started = live.launch(spec)                 # the model pin is checked first: no later prompt without it
    live.first[cli] = (spec, started)
    rec = live.record(spec.session_key)
    assert rec.phase is Phase.RUNNING and started.native_id is not None
    assert live.tmux.has_session(live.runtime.reaper_name(spec.session_key, rec.generation))  # D13's backstop
    assert {s.key: s.liveness for s in live.runtime.sessions("alpha")}[spec.session_key] is Liveness.LIVE
    curl = live.backend.exec(rec.sandbox, Path(rec.worktree), ["curl", "-V"], timeout=EXEC_SECONDS)
    print(f"EVIDENCE {cli} workload {curl.stdout.decode(errors='replace').splitlines()[:1]}")


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_egress_is_refused_and_no_push_credential_is_inside(live: Live, cli: str) -> None:
    key = live.first[cli][0].session_key
    require(live.run(key, "curl", "-sS", "-m", "10", "-o", "/dev/null", "https://example.org") != 0,
            f"{cli}: egress to example.org was not refused")
    require(live.run(key, "sh", "-c", 'test ! -e "$HOME/.git-credentials" && test ! -e "$HOME/.config/gh" '
                                      '&& test -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}"') == 0,
            f"{cli}: a push credential is inside")


def test_an_agent_commits_on_its_bead_branch_and_nothing_else_moves(live: Live) -> None:
    """Gap 13, D26: git works in a btq linked worktree, and only the bead branch lands on the host."""
    spec = live.spec("claude", "btq-live-3")
    live.launch(spec)
    main = git(live.repo, "rev-parse", "main")
    key = spec.session_key
    try:
        require(live.run(key, "git", "-c", "user.name=agent", "-c", "user.email=agent@example.org", "commit",
                         "--allow-empty", "-q", "-m", "hz-live") == 0, "the agent could not commit")
        require(live.run(key, "git", "update-ref", "refs/heads/main", "HEAD") == 0,       # its own copy only
                "the agent could not move its own main")
        require(live.run(key, "test", "!", "-e", str(live.repo / ".git" / "config")) == 0,
                "the main repository's git config is visible inside")
        dot_git, marker = spec.worktree / ".git", spec.worktree / "hz-ran"
        pointer = dot_git.read_text()
        require(live.run(key, "sh", "-c", f"echo 'gitdir: /tmp' > {dot_git}") != 0,      # bound read-only
                "the worktree's .git pointer was writable")
        require(live.run(key, "mv", str(dot_git), "/tmp/hz-git") != 0,  # noqa: S108 - a mount point
                "the worktree's .git pointer could be moved")
        require(live.run(key, "git", "config", "core.fsmonitor", f"touch {marker}") == 0,  # its own config
                "the agent could not set its own git config")
        git(spec.worktree, "status", "--porcelain")                     # unpinned, as btq's git runs
        require(dot_git.read_text() == pointer and not marker.exists(),
                "the worktree's .git pointer changed, or the agent's fsmonitor ran on the host")
    finally:
        live.runtime.stop(key)
    assert git(live.repo, "log", "-1", "--format=%s", "btq/btq-live-3") == "hz-live"
    assert git(live.repo, "rev-parse", "main") == main


# From the agent's own tool: the workstream's extra egress is granted to the CLI's process tree only. The
# push goes to the same host and repository the fetch reads, so its failure can only be the missing
# credential: git reports the server's demand for a username, which it can't supply.
AGENT_NET = ('git rev-parse HEAD >/dev/null; echo "git=$?"; '
             'git ls-remote https://github.com/git/git.git HEAD >/dev/null; echo "fetch=$?"; '
             'python3 -I -c "import urllib.request; '
             'urllib.request.urlopen(\'https://pypi.org/simple/pip/\', timeout=20).read(1)"; '
             'echo "registry=$?"; '
             'GIT_TERMINAL_PROMPT=0 git push https://github.com/git/git.git HEAD:refs/heads/hz-live-push; '
             'echo "push=$?"')


def test_fetch_and_registry_work_and_push_fails_for_want_of_a_credential(live: Live) -> None:
    """r15 §7 "still to verify" and gap 7, on the managed agent path, in a linked worktree."""
    spec, _ = live.first["claude"]
    rec = live.record(spec.session_key)
    out = live.runtime.layout(spec.session_key).home / ".hz-live-net.out"
    target = '"$HOME/.hz-live-net.out"'
    live.tmux.paste(rec.tmux_session, "Run exactly this shell command, then reply with only the word done: "
                    f"( {AGENT_NET} ) > {target} 2>&1")
    assert wait_until(lambda: out.exists() and "push=" in out.read_text(), PROMPT_SECONDS)
    text = out.read_text()
    statuses = [w for w in text.split() if re.fullmatch(r"(git|fetch|registry|push)=\d{1,3}", w)]
    require({"git=0", "fetch=0", "registry=0"} <= set(statuses), f"the agent's network statuses: {statuses}")
    require("push=0" not in statuses, "the push succeeded")
    require("could not read Username for 'https://github.com'" in text,
            f"the push failed, but not for want of a credential: {statuses}")


def test_the_reviewer_worktree_is_read_only(live: Live) -> None:
    spec = live.spec("claude", "btq-live-2", role="reviewer")
    live.launch(spec)
    require(live.run(spec.session_key, "touch", str(spec.worktree / "hz-live-ro")) != 0,
            "the reviewer could write its worktree")
    require(not (spec.worktree / "hz-live-ro").exists(), "the reviewer's write reached the host")
    live.runtime.stop(spec.session_key)


def test_codex_tool_calls_fail_closed_while_wsd_is_down(live: Live) -> None:
    spec, _ = live.first["codex"]
    rec = live.record(spec.session_key)
    live.runtime.servers.pop(spec.session_key).close()          # wsd gone: the hook gets no answer
    live.tmux.paste(rec.tmux_session, "Run the shell command `echo hz-live-probe` and show its output.")
    assert wait_until(lambda: "control plane unavailable" in live.tmux.capture(rec.tmux_session, 200),
                      PROMPT_SECONDS)


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_a_new_generation_resumes_the_same_session(live: Live, cli: str) -> None:
    spec, first = live.first[cli]
    assert first.native_id is not None
    home = live.runtime.layout(spec.session_key).home
    before = codex_policies(home, first.native_id) if cli == "codex" else []
    live.runtime.stop(spec.session_key)
    resumed = replace(spec, generation=2, resume=True, native_id=first.native_id)
    second = live.runtime.launch(resumed)
    live.pinned(resumed, second)
    assert second.native_id == first.native_id
    assert live.record(spec.session_key).sandbox.endswith("g2")
    if cli == "codex":
        # The remote resume carries no permission override (0.160.0 refuses one), so the resumed thread
        # must keep the policy the first launch set: every turn, g2's self-test turn included.
        after = codex_policies(home, first.native_id)
        print(f"EVIDENCE codex policies g1={before} g1+g2={after}")
        assert before and set(before) == {BYPASSED}
        assert len(after) > len(before) and set(after) == {BYPASSED}


def exec_dirs_on_the_host(live: Live, key: str) -> dict[str, list[str]]:
    """What the host side of each of Codex's E25 exec directories holds."""
    home = live.runtime.layout(key).home
    return {str(p): sorted(e.name for e in (home / p).iterdir()) for p in REGISTRY["codex"].empty_read_only}


def test_codex_new_and_resumed_remote_launch_with_empty_read_only_exec_dirs(live: Live) -> None:
    """Plan 4c Amendment 1 §4.3 (E25): the launcher's real Codex shape (`app-server --listen`, then the TUI
    with `--remote`), new and then resumed, in the real sandbox with `.codex/tmp` and `.codex/packages`
    bound to one empty read-only directory and no `-c`. A launch returns only once the server is ready
    and the self-test, whose checks include `empty-read-only`, has passed; after each exit the host side
    of both directories is empty."""
    assert "empty-read-only" in EXEC_CHECKS and "empty-read-only" in AGENT_CHECKS
    codex = REGISTRY["codex"]
    spec = live.spec("codex", "btq-live-9")
    first = live.launch(spec)
    layout = live.runtime.layout(spec.session_key)
    assert first.native_id is not None and codex.server_ready(layout)
    live.runtime.stop(spec.session_key)
    assert exec_dirs_on_the_host(live, spec.session_key) == {str(p): [] for p in codex.empty_read_only}
    resumed = replace(spec, generation=2, resume=True, native_id=first.native_id)
    second = live.runtime.launch(resumed)
    live.pinned(resumed, second)
    assert second.native_id == first.native_id and codex.server_ready(layout)
    live.runtime.stop(spec.session_key)
    assert exec_dirs_on_the_host(live, spec.session_key) == {str(p): [] for p in codex.empty_read_only}
    print(f"EVIDENCE codex E25 new+resumed: server ready, empty-read-only passed, host exec dirs empty "
          f"(install {live.installs['codex'].pin.install})")


def test_the_lifetime_stop_ends_a_real_sandbox(live: Live) -> None:
    spec, _ = live.first["claude"]
    rec = live.record(spec.session_key)
    live.runtime.expire("alpha", rec.deadline)
    assert live.record(spec.session_key).phase is Phase.ENDED
    assert rec.sandbox not in live.backend.names()
    assert not live.tmux.has_session(live.runtime.reaper_name(spec.session_key, rec.generation))


class Hooked(OpenShellSelfTest):
    """The real self-test, with an action before the exec path or beside the agent path."""

    def __init__(self, before_exec: Callable[[ProbeContext], None] | None = None,
                 beside_agent: Callable[[ProbeContext], None] | None = None) -> None:
        super().__init__()
        self.before_exec = before_exec
        self.beside_agent = beside_agent
        self.seen: list[ProbeContext] = []

    def exec_path(self, ctx: ProbeContext) -> None:
        self.seen.append(ctx)
        if self.before_exec is not None:
            self.before_exec(ctx)
        super().exec_path(ctx)

    def agent_path(self, ctx: ProbeContext) -> None:
        if self.beside_agent is None:
            super().agent_path(ctx)
            return
        t = threading.Thread(target=self.beside_agent, args=(ctx,), daemon=True)
        t.start()
        try:
            super().agent_path(ctx)
        finally:
            t.join(EXEC_SECONDS)


class FenceDown(OpenShellBackend):
    """Reports a bridge whatever the container's network is; `real` keeps what the container had."""

    real: list[str]

    def network_mode(self, name: str) -> str:
        self.real.append(super().network_mode(name))
        return "bridge"


# A control passes only on the self-test's own words (probe_verdicts) for the refusal it injects.
CHANNEL_REJECTED = f"{CHANNEL}a peer that is not the probe connected"
FREEZES = r"( \(freeze attempts: \d+(, \d+)*\))?"


def runtime(live: Live, *, backend: OpenShellBackend | None = None, selftest: OpenShellSelfTest | None = None,
            config: RuntimeConfig | None = None) -> SandboxRuntime:
    return SandboxRuntime(config or live.runtime.c, backend or live.backend, selftest or OpenShellSelfTest())


def launch_with(live: Live, rt: SandboxRuntime, spec: LaunchSpec) -> Started:
    """A launch through a control's own runtime, which then owns the session: only it holds the session's
    server, so the session is stopped through it (`stop`, and the fixture's teardown)."""
    live.keys.append(spec.session_key)
    live.owners[spec.session_key] = rt
    return rt.launch(spec)


def stop(live: Live, key: str) -> None:
    live.owners.get(key, live.runtime).stop(key)


def refused(live: Live, rt: SandboxRuntime, spec: LaunchSpec, reason: str) -> None:
    """The launch fails for exactly `reason`, and leaves no sandbox."""
    with pytest.raises(LaunchFailed) as caught:
        launch_with(live, rt, spec)
    said = str(caught.value)
    require(re.fullmatch(re.escape(f"{SELFTEST_FAILED}{reason}") + FREEZES, said),
            f"the launch failed for another reason: {launch_shown(said)}")
    assert sandbox_name(spec.session_key, spec.generation) not in live.backend.names(), "the sandbox was left"


def recorded_channels(monkeypatch: pytest.MonkeyPatch) -> list[ProbeChannel]:
    """Every result channel the self-test makes from here on, kept so a control can read why it rejected a
    peer (the channel keeps its reasons for the audit). Nothing the channel does changes."""
    made: list[ProbeChannel] = []

    class Recorded(ProbeChannel):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(probe_channel, "ProbeChannel", Recorded)
    return made


def rejections(made: list[ProbeChannel]) -> list[str]:
    return [reason for c in made for reason in c.rejected]


def rejections_shown(reasons: list[str], tool_vars: frozenset[str] = frozenset()) -> list[str]:
    return [rejection_shown(r, tool_vars) for r in reasons]


def outputs_shown(outputs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Each check's exit status, and its stdout only as `shown` renders it (Codex T15 r3)."""
    return [(rc, shown(out)) for rc, out in outputs]


def in_workload(ctx: ProbeContext, script: str, *args: str) -> tuple[int, str]:
    """A check script's exit status and stdout from the workload, as the probes run. Its stderr is never
    kept: it may hold paths."""
    r = ctx.backend.exec(ctx.spec.name, ctx.spec.workdir, ["python3", "-I", "-c", script, *args],
                         timeout=EXEC_SECONDS)
    return r.returncode, r.stdout.decode("utf-8", "replace").strip()


def test_host_ptrace_scope_allows_probe_protection() -> None:
    assert ptrace_scope_refusal(read_ptrace_scope()) == "", "set kernel.yama.ptrace_scope = 1 (gap 15)"


def test_a_canary_inside_a_mount_refuses_the_launch(live: Live) -> None:
    canary = live.runtime.c.real_home_canary
    canary.write_text("x")
    host = host_config() + f'ro_mounts_approved = ["{canary}"]\n'      # [sandbox] is host_config's last table
    env = config_env(live.root / "c2", host, {"beta": f'[sandbox]\nextra_ro_mounts = ["{canary}"]\n'})
    config = replace(live.runtime.c, settings=sandbox_settings(env))
    spec = replace(live.spec("claude", "btq-live-n1"), ws="beta")
    seen: list[tuple[int, str]] = []
    # The injection: the canary is readable inside before the probes run.
    hooked = Hooked(before_exec=lambda ctx: seen.append(in_workload(ctx, READABLE, str(canary))))
    refused(live, runtime(live, config=config, selftest=hooked), spec, "real-home-canary-unreadable")
    require(seen == [(0, "readable")], f"the canary was not readable inside: {outputs_shown(seen)}")


READABLE = "import sys\nopen(sys.argv[1], 'rb').close()\nprint('readable')"
HAS_LANG = "import os\nprint('LANG' in os.environ)"


def test_a_wrong_pinned_login_hash_refuses_the_launch(live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    real = openshell_selftest.probe_config
    wrong_for: list[str] = []

    def wrong(ctx: ProbeContext, path: str) -> dict[str, Any]:
        cfg = real(ctx, path)
        assert cfg["chosen"], "no chosen login to give a wrong hash"
        cfg["chosen"] = [{**c, "sha256": "not-the-pinned-hash"} for c in cfg["chosen"]]
        wrong_for.append(path)
        return cfg

    monkeypatch.setattr(openshell_selftest, "probe_config", wrong)
    refused(live, runtime(live), live.spec("claude", "btq-live-n2"), "other-accounts")
    assert wrong_for == ["exec"], f"the wrong hash went to {wrong_for}"


def test_a_leaked_variable_refuses_the_launch(live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    real = openshell_selftest.env_allowed
    monkeypatch.setattr(openshell_selftest, "env_allowed", lambda a, p, v: real(a, p, v) - {"LANG"})
    seen: list[tuple[int, str]] = []
    hooked = Hooked(before_exec=lambda ctx: seen.append(in_workload(ctx, HAS_LANG)))   # it is there
    spec = live.spec("claude", "btq-live-n3")
    refused(live, runtime(live, selftest=hooked), spec, "host-env-not-inherited")
    require(seen == [(0, "True")], f"LANG was not in the workload's environment: {outputs_shown(seen)}")


def test_a_dead_session_socket_refuses_the_launch(live: Live) -> None:
    spec = live.spec("claude", "btq-live-n4")
    holder: list[SandboxRuntime] = []
    closed: list[bool] = []

    def close(ctx: ProbeContext) -> None:
        holder[0].servers[spec.session_key].close()
        closed.append(not ctx.layout.socket(ctx.generation).exists())

    rt = runtime(live, selftest=Hooked(before_exec=close))
    holder.append(rt)
    # Codex T15 r2: every check whose request goes over the session socket fails, so the reason is
    # exactly those, as the probes name them.
    refused(live, rt, spec, ", ".join(session_socket_checks(PROBES_SOURCE.read_text())))
    assert closed == [True], "the session socket was not closed before the probes ran"


def test_a_failed_outer_fence_refuses_a_launch_right_after_a_pass(live: Live) -> None:
    spec = live.spec("claude", "btq-live-n5")
    first = live.launch(spec)
    live.runtime.stop(spec.session_key)
    s = live.runtime.c.settings
    # The backend's environment, not the raw tool_env: that holds only the overrides, without HOME or XDG_*.
    down = FenceDown(s.openshell, s.podman, s.image, live.backend.env)
    down.real = []
    again = replace(spec, generation=2, resume=True, native_id=first.native_id)
    refused(live, runtime(live, backend=down), again, "outer-fence-network-none")
    assert down.real == ["none"], f"the real fence was {down.real}"       # it was up; only the report lied


def plain_probes(live: Live, ctx: ProbeContext, net: list[str], data: bytes) -> tuple[int, list[str]]:
    """The exec-path probes in a plain podman container on the agent image, without OpenShell's runtime,
    on the gateway's own podman endpoint (the preflight's). The container is named, and its ID kept in a
    CID file; whatever the run did, it is removed and confirmed gone before its CID file goes. When it
    can't be, the module's scratch, where its /run/hz bind comes from, is kept. Only the exit status and
    the names of the failed checks come back: the probes' output holds paths."""
    name = f"hz-live-plain-{uuid.uuid4().hex[:8]}"
    scratch = Path(tempfile.mkdtemp(prefix="cid", dir=live.root))
    cidfile = scratch / "cid"
    argv = [*live.artifact.podman, "run", "--rm", "-i", "--pull=never", f"--name={name}",
            f"--cidfile={cidfile}", "--userns=keep-id", *net,
            "-v", f"{ctx.layout.run(ctx.generation)}:/run/hz:ro", live.runtime.c.settings.image,
            "python3", "-I", "/run/hz/probes.py"]
    r: subprocess.CompletedProcess[bytes] | None = None
    ran = ""
    try:
        with contextlib.suppress(subprocess.TimeoutExpired):
            r = subprocess.run(argv, input=data, capture_output=True, timeout=PROMPT_SECONDS,
                               env=live.backend.env, check=False)
    except OSError as exc:              # Codex T15 r2: its text may name a path
        ran = type(exc).__name__
    finally:
        why = remove_container(live, name, cidfile)
        if why:
            live.uncertain.append(why)
        else:
            shutil.rmtree(scratch)
    assert not why, why
    if ran:
        pytest.fail(f"the plain probes ({net or 'a network'}) could not run: {ran}")
    if r is None:
        pytest.fail(f"the plain probes ({net or 'a network'}) timed out")
    lines = r.stdout.decode("utf-8", "replace").splitlines()
    failed = [m[2] for m in map(VERDICT.fullmatch, lines) if m and m[1] == "FAIL" and m[2] in EXEC_CHECKS]
    return r.returncode, failed


def remove_container(live: Live, name: str, cidfile: Path) -> str:
    """"" once the container is gone by its ID (when the CID file was written) and its name, else why
    not. `--rm` removes it only when it exits, not when its client is killed."""
    def podman(*argv: str) -> int:
        return subprocess.run([*live.artifact.podman, *argv], stdin=subprocess.DEVNULL, capture_output=True,
                              env=live.backend.env, timeout=PREFLIGHT_SECONDS, check=False).returncode

    try:
        refs = [*([cid] if cidfile.exists() and (cid := cidfile.read_text().strip()) else []), name]
        for ref in refs:
            podman("rm", "--force", "--ignore", "--time", "0", ref)
        left = [ref for ref in refs if podman("container", "exists", ref) != 1]      # 1: no such container
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"the plain container {name}: {type(exc).__name__}"
    return f"the plain container {name} may still exist" if left else ""


def test_the_probes_fail_without_the_runtime(live: Live) -> None:
    hooked = Hooked()
    spec = live.spec("claude", "btq-live-n6")
    launch_with(live, runtime(live, selftest=hooked), spec)
    try:
        ctx = hooked.seen[0]
        data = json.dumps(probe_config(ctx, "exec")).encode()
        for net in (["--network=none"], []):        # no broker, then a connected network
            rc, failed = plain_probes(live, ctx, net, data)
            assert rc != 0 and "direct-network-blocked" in failed, \
                f"{net or 'a network'}: exit {rc}, failed checks {failed}"
    finally:
        stop(live, spec.session_key)


# A forged result on the result socket: it reports the connect, then holds the connection until the host
# ends it, so the host's checks see it alive and reject it for what it is.
FORGER = ("import socket, sys\ns = socket.socket(socket.AF_UNIX)\ns.connect('/run/hz/p.sock')\n"
          "print('connected', flush=True)\ns.settimeout(60)\n"
          "try:\n    s.sendall(sys.stdin.buffer.read())\n    s.recv(1)\nexcept OSError:\n    pass\n")


def test_a_forged_agent_path_result_refuses_the_launch(live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    made = recorded_channels(monkeypatch)
    forged = b"".join((json.dumps(m) + "\n").encode() for m in
                      [*({"check": c, "ok": True, "evidence": "forged"} for c in AGENT_CHECKS), {"done": 0}])
    forger: list[tuple[int, str]] = []

    def forge(ctx: ProbeContext) -> None:
        if wait_until(lambda: ctx.layout.probe_socket(ctx.generation).exists(), PROMPT_SECONDS):
            r = ctx.backend.exec(ctx.spec.name, ctx.spec.workdir, ["python3", "-I", "-c", FORGER],
                                 input=forged, timeout=EXEC_SECONDS)
            forger.append((r.returncode, r.stdout.decode("utf-8", "replace").strip()))

    refused(live, runtime(live, selftest=Hooked(beside_agent=forge)), live.spec("claude", "btq-live-n7"),
            CHANNEL_REJECTED)
    require(forger == [(0, "connected")], f"the forger did not reach the channel: {outputs_shown(forger)}")
    require(rejections(made) == [NOT_THE_PROBE], f"the channel rejected {rejections_shown(rejections(made))}")


def test_a_tool_path_variable_is_caught_on_the_agent_path(live: Live,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent path is verified against the exec path's allowlist: the probe the agent runs inherits the
    CLI's tool variables, so the channel must reject it for them, and for nothing else."""
    made = recorded_channels(monkeypatch)
    real = openshell_selftest.env_allowed
    monkeypatch.setattr(openshell_selftest, "env_allowed", lambda a, p, v: real(a, "exec", v))
    hooked = Hooked()
    refused(live, runtime(live, selftest=hooked), live.spec("claude", "btq-live-n8"), CHANNEL_REJECTED)
    adapter, version = hooked.seen[0].adapter, hooked.seen[0].install.manifest.version
    tool_only = frozenset(real(adapter, "agent", version) - real(adapter, "exec", version))
    reasons = rejections(made)
    # Codex T15 r2, r3: only the CLI's own tool variables are named; any other is shown as unrecognised.
    why = tool_var_rejection(reasons, tool_only)
    require(not why, why)
    print(f"EVIDENCE n8 the probe was rejected for the tool-path variables "
          f"{sorted(reasons[0].removeprefix(ENV_REJECTED).split(' '))}")


# Every process the check can see but itself, its own child included, gets each operation once: the
# ptrace attach, process_vm_readv and process_vm_writev (no iovec: they move nothing) and a mem write open.
# pidfd_getfd, with no pidfd to hand (pidfd_open gives ENOSYS), is tried once: unfiltered it gives EBADF.
MEMORY_CHECK = r"""
import ctypes, errno, os, subprocess, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.ptrace.restype = ctypes.c_long
libc.ptrace.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
for call in (libc.process_vm_readv, libc.process_vm_writev):
    call.restype = ctypes.c_ssize_t
    call.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                     ctypes.c_ulong]
libc.pidfd_getfd.restype = ctypes.c_int
libc.pidfd_getfd.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint]

def answer(ok):
    code = ctypes.get_errno()
    return 'OK' if ok else errno.errorcode.get(code, str(code))

def mem(pid):
    try:
        os.close(os.open(f'/proc/{pid}/mem', os.O_RDWR))
        return 'OK'
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))

child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'])
try:
    targets = sorted({int(p) for p in os.listdir('/proc') if p.isdigit()} - {os.getpid()})
    print('targets', *targets)
    print('child', child.pid)
    print('getfd', answer(libc.pidfd_getfd(-1, 0, 0) != -1))
    for pid in targets:
        attached = libc.ptrace(16, pid, None, None) == 0
        print('attach', pid, answer(attached))
        if attached:
            libc.ptrace(17, pid, None, None)
        print('readv', pid, answer(libc.process_vm_readv(pid, None, 0, None, 0, 0) != -1))
        print('writev', pid, answer(libc.process_vm_writev(pid, None, 0, None, 0, 0) != -1))
        print('mem', pid, mem(pid))
finally:
    child.kill()
    child.wait()
print('done')
"""
# Each write /run/hz could take, on the probe, its configuration, the token, the hook shim, and the
# directory both sockets live in, as a syscall whose errno is reported: no shell, so no command's status
# can pass for a refusal. A target must be there (or, to be made, absent) for its answer to count.
FILE_CHECK = f"FILE_OPS = {FILE_OPS!r}\n" + r"""
import errno, os, socket

def create(path):
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))

def append(path):                     # opened for writing; nothing is written
    os.close(os.open(path, os.O_WRONLY | os.O_APPEND))

def bind(path):
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(path)

OPS = {'create': (create, False), 'mkdir': (os.mkdir, False), 'bind': (bind, False), 'append': (append, True),
       'rename': (lambda path: os.rename(path, path + '2'), True), 'unlink': (os.unlink, True)}
for op, name in FILE_OPS:
    run, needs = OPS[op]
    path = f'/run/hz/{name}'
    if os.path.lexists(path) != needs:
        print(op, name, 'MISSING' if needs else 'PRESENT')
        continue
    try:
        run(path)
        print(op, name, 'OK')
    except OSError as e:
        print(op, name, errno.errorcode.get(e.errno, str(e.errno)))
print('done')
"""
# unshare(0) is a no-op unless the filter denies unshare outright; the second is the namespace an overlay
# needs.
NAMESPACE_CHECK = r"""
import errno, os

def answer(flags):
    try:
        os.unshare(flags)
        return 'OK'
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))

print('mnt', os.readlink('/proc/self/ns/mnt'))
print('unshare-none', answer(0))
print('unshare-user-mount', answer(os.CLONE_NEWUSER | os.CLONE_NEWNS))
print('mnt', os.readlink('/proc/self/ns/mnt'))
print('done')
"""


# The overlay check from a namespace the agent makes: a forged probe over /run/hz/probes.py, and a shell
# at the CLI's path that starts it, so the forged probe has a "CLI" parent in the same namespaces and the
# probe's allowed environment: the channel can then reject it only for its namespaces. Each step reports
# its errno, the forged probe reports its connect and send, and the shell keeps its exit status.
NESTED = r"""
import ctypes, errno, os, subprocess, sys
cli, allowed = sys.argv[1], sys.argv[2].split(',')
uid, gid = os.getuid(), os.getgid()
libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]
MS_BIND = 4096
with open('/tmp/hz-forged.py', 'w') as fh:
    fh.write(sys.stdin.read())
print('forged=written', flush=True)

def maps():
    for name, value in (('setgroups', 'deny'), ('uid_map', f'0 {uid} 1'), ('gid_map', f'0 {gid} 1')):
        with open(f'/proc/self/{name}', 'w') as fh:
            fh.write(value)

def bind(source, target):
    def run():
        if libc.mount(source.encode(), target.encode(), None, MS_BIND, None) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    return run

def step(name, run):
    try:
        run()
    except OSError as e:
        print(f'{name}={errno.errorcode.get(e.errno, e.errno)}', flush=True)
        return False
    print(f'{name}=OK', flush=True)
    return True

if (step('unshare', lambda: os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNS)) and step('maps', maps)
        and step('bind-probe', bind('/tmp/hz-forged.py', '/run/hz/probes.py'))
        and step('bind-cli', bind('/bin/sh', cli))):
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    r = subprocess.run([cli, '-c', 'python3 -I /run/hz/probes.py --agent; exit $?'], env=env, check=False)
    print(f'run={r.returncode}', flush=True)
print('done', flush=True)
"""
# The forged probe: a full set of passing results, sent on the result socket; it holds the connection until
# the host ends it, so the host's checks see it alive.
FORGED_PROBE = r"""
import errno, socket
s = socket.socket(socket.AF_UNIX)
try:
    s.connect('/run/hz/p.sock')
except OSError as e:
    print(f'connect={errno.errorcode.get(e.errno, e.errno)}', flush=True)
    raise SystemExit(1)
print('connect=OK', flush=True)
try:
    s.settimeout(60)
    s.sendall(RESULTS)
    s.recv(1)
    print('sent=OK', flush=True)
except TimeoutError:
    print('sent=TIMEOUT', flush=True)
except OSError as e:
    print(f'sent={errno.errorcode.get(e.errno, e.errno)}', flush=True)
"""


def test_an_overlaid_probe_from_a_namespace_the_agent_made_is_never_counted(
        live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    made = recorded_channels(monkeypatch)
    results = b"".join((json.dumps(m) + "\n").encode() for m in
                       [*({"check": c, "ok": True, "evidence": "forged"} for c in AGENT_CHECKS), {"done": 0}])
    forged = f"RESULTS = {results!r}\n{FORGED_PROBE}"
    out: list[tuple[int, str]] = []

    def nest(ctx: ProbeContext) -> None:
        if wait_until(lambda: ctx.layout.probe_socket(ctx.generation).exists(), PROMPT_SECONDS):
            allowed = ",".join(sorted(env_allowed(ctx.adapter, "agent", ctx.install.manifest.version)))
            r = ctx.backend.exec(ctx.spec.name, ctx.spec.workdir,
                                 ["python3", "-I", "-c", NESTED, str(ctx.cli.binary), allowed],
                                 input=forged.encode(), timeout=EXEC_SECONDS)
            out.append((r.returncode, r.stdout.decode("utf-8", "replace")))

    spec = live.spec("claude", "btq-live-n9")
    failed = ""
    try:
        try:
            launch_with(live, runtime(live, selftest=Hooked(beside_agent=nest)), spec)
        except LaunchFailed as exc:
            failed = str(exc)
        # Codex T15 r2: the launch's reason and the channel's are shown only in their fixed wording.
        launch = launch_shown(failed) if failed else "passed"
        assert wait_until(lambda: bool(out), EXEC_SECONDS), \
            f"the overlay check never reported; launch: {launch}"
        outcome = overlay_outcome(*out[0])
        assert outcome in ("refused", "ran"), outcome
        reasons = rejections(made)
        shown = rejections_shown(reasons)
        print(f"EVIDENCE n9 overlay {outcome}; launch {launch}; channel rejected {shown}")
        if outcome == "ran":
            rejected = re.escape(f"{SELFTEST_FAILED}{CHANNEL_REJECTED}") + FREEZES
            require(re.fullmatch(rejected, failed), f"an overlaid probe ran and was not rejected: {launch}")
            require(reasons == [NAMESPACE_REJECTED], f"the overlaid probe was rejected for {shown}")
        else:
            require(not failed and not reasons,
                    f"the overlay was refused, yet the launch failed or a peer was rejected: {launch}; "
                    f"{shown}")
    finally:
        stop(live, spec.session_key)


# Finds the agent-path probe by its argv, then, until it exits, each curl that descends from it, and reports
# what that curl's output descriptors are and whether a write open of each is refused. It never writes: an
# open that succeeds is closed at once. Only a process that is gone (ENOENT, ESRCH) counts as gone. A
# descriptor whose link the sandbox hides (EACCES: the watcher is outside the probe's domain) is `hidden`, and
# its write open is still tried; any other error is reported, and fails the check.
OUTPUT_CHECK = r"""
import errno, os, sys, time
deadline = time.monotonic() + float(sys.argv[1])
PROBE = [a.encode() for a in sys.argv[2:]]
GONE = (errno.ENOENT, errno.ESRCH)

def name(code):
    return errno.errorcode.get(code, str(code))

def read(pid, what):
    with open(f'/proc/{pid}/{what}', 'rb') as fh:
        return fh.read()

def live(pid):                          # running: there and not a zombie
    try:
        return read(pid, 'stat').rsplit(b')', 1)[1].split()[0] != b'Z'
    except OSError as e:
        if e.errno in GONE:
            return False
        raise

def descends(pid):                      # True or False; None when it ended before it could be placed
    q = pid
    for _ in range(64):
        try:
            q = read(q, 'stat').rsplit(b')', 1)[1].split()[1].decode()
        except OSError as e:
            if e.errno in GONE:
                return None
            raise
        if q == probe:
            return True
        if q in ('0', '1'):
            return False
    return False

def state(pid, fd):
    path = f'/proc/{pid}/fd/{fd}'
    try:
        kind = os.readlink(path).partition(':')[0]
    except OSError as e:
        if e.errno in GONE:
            return 'gone'
        if e.errno != errno.EACCES:
            return f'error:{name(e.errno)}'
        kind = 'hidden'
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
        return f'{kind}:OPENED'
    except OSError as e:
        return 'gone' if e.errno in GONE else f'{kind}:{name(e.errno)}'

def argv(pid):
    try:
        return read(pid, 'cmdline').split(b'\0')[:-1]
    except OSError as e:
        if e.errno in GONE:
            return None
        raise

probe = None
while probe is None and time.monotonic() < deadline:
    probe = next((p for p in os.listdir('/proc') if p.isdigit() and argv(p) == PROBE), None)
    time.sleep(0.005)
if probe is None:
    print('probe none', flush=True)
else:
    print('probe', probe, flush=True)
    seen = set()
    while live(probe) and time.monotonic() < deadline:
        for pid in os.listdir('/proc'):
            if not pid.isdigit() or pid in seen or (argv(pid) or [b''])[0] != b'/usr/bin/curl':
                continue
            seen.add(pid)
            placed = descends(pid)
            if placed is None:
                print('curl', pid, 'gone', flush=True)
            elif placed:
                print('curl', pid, state(pid, 1), state(pid, 2), flush=True)
        time.sleep(0.005)
    print('probe', probe, 'running' if live(probe) else 'exited', flush=True)
print('done', flush=True)
"""


def test_a_running_probe_s_output_channels_cannot_be_reopened(live: Live) -> None:
    """Task 9C: while the agent-path probe runs, a separate workload process finds each curl the probe
    starts. Neither of its output descriptors may be reopened for writing: each is a socket whose open is
    refused (ENXIO), or one whose link and open the sandbox both refuse (EACCES)."""
    out: list[tuple[int, str]] = []

    def watch(ctx: ProbeContext) -> None:
        if wait_until(lambda: ctx.layout.probe_socket(ctx.generation).exists(), PROMPT_SECONDS):
            r = ctx.backend.exec(ctx.spec.name, ctx.spec.workdir,
                                 ["python3", "-I", "-c", OUTPUT_CHECK, str(PROMPT_SECONDS), *PROBE_ARGV],
                                 timeout=PROMPT_SECONDS + EXEC_SECONDS)
            out.append((r.returncode, r.stdout.decode("utf-8", "replace")))

    spec = live.spec("claude", "btq-live-n10")
    launch_with(live, runtime(live, selftest=Hooked(beside_agent=watch)), spec)
    try:
        assert wait_until(lambda: bool(out), PROMPT_SECONDS + EXEC_SECONDS), \
            "the output-channel check never reported"
        why = outputs_unreachable(*out[0])
        assert why == "", why
        curls = [line.split()[2:] for line in out[0][1].splitlines() if line.startswith("curl ")]
        whole = sum(len(c) == 2 and "gone" not in c for c in curls)
        fds = [fd for c in curls for fd in c]
        print(f"EVIDENCE n10 curls seen {len(curls)}, whole {whole}; fds socket:ENXIO "
              f"{fds.count('socket:ENXIO')}, hidden:EACCES {fds.count('hidden:EACCES')}")
    finally:
        stop(live, spec.session_key)


def test_nothing_in_the_workload_can_tamper_with_a_probe(live: Live) -> None:
    spec = live.spec("claude", "btq-live-adv")
    live.launch(spec)
    rec = live.record(spec.session_key)
    try:
        checks = (("memory", memory_access_refused, MEMORY_CHECK), ("files", file_writes_refused, FILE_CHECK),
                  ("namespaces", namespaces_refused, NAMESPACE_CHECK))
        for what, verdict, script in checks:
            r = live.backend.exec(rec.sandbox, Path(rec.worktree), ["python3", "-I", "-c", script],
                                  timeout=EXEC_SECONDS)
            why = verdict(r.returncode, r.stdout.decode("utf-8", "replace"))
            assert why == "", f"{what}: {why}"
            if what == "memory":
                targets = r.stdout.decode("utf-8", "replace").splitlines()[0].split()[1:]
                print(f"EVIDENCE adv memory: {len(targets)} targets, each refused every operation")
    finally:
        live.runtime.stop(spec.session_key)
