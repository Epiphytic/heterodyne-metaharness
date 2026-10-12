"""Live checks of the OpenShell sandbox runtime: plan 4's live gate. Needs HZ_LIVE=1 (the conftest) and
HZ_LIVE_SANDBOX=1, the S5 host setup, the agent image and both pinned CLIs with a default login. It makes
real model calls: one per launch, plus one prompt. It prints nothing from any login.

Run it only while no wsd on this host uses the OpenShell runtime: that wsd would see these sandboxes
without a record and hold its workstreams until they are gone."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))     # tests/: sandbox_env, wsd_env

from sandbox_env import config_env, gateway_listener, sandbox_teardown  # noqa: E402
from wsd_env import ADAPTERS, git_repo  # noqa: E402

from heterodyne.agents.registry import ADAPTERS as REGISTRY  # noqa: E402
from heterodyne.sandbox.openshell import OpenShellBackend, tool_env  # noqa: E402
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest  # noqa: E402
from heterodyne.sandbox.runtime import (  # noqa: E402
    Phase,
    RuntimeConfig,
    SandboxRuntime,
    SessionRecord,
    read_record,
)
from heterodyne.sandbox.selftest import ProbeContext, SelfTestFailed  # noqa: E402
from heterodyne.sandbox.settings import SandboxSettings, sandbox_settings  # noqa: E402
from heterodyne.tmux import Tmux  # noqa: E402
from heterodyne.wsd import ids  # noqa: E402
from heterodyne.wsd.accounts import ConfiguredAccounts  # noqa: E402
from heterodyne.wsd.runtime import LaunchSpec, Liveness, Started  # noqa: E402
from heterodyne.wsd.workstream import utc_now  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("HZ_LIVE_SANDBOX") != "1",
                                reason="live sandbox checks: set HZ_LIVE_SANDBOX=1 (and HZ_LIVE=1)")
# Test-only, for cost (Liam, T14): a cheap model with a small context on every live launch, never the
# operator's default. Overridable by environment; production defaults are untouched.
PROFILES = {"claude": "p-one", "codex": "p-live-codex"}
MODELS = {"claude": os.environ.get("HZ_LIVE_SANDBOX_CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
          "codex": os.environ.get("HZ_LIVE_SANDBOX_CODEX_MODEL", "gpt-6-luna")}
CODEX_EFFORT = 'model_reasoning_effort="low"'
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
    runtime_sha256: str              # its digest at the preflight ("" if no create has cached it yet)
    lines: tuple[str, ...]           # the evidence: versions, key names and IDs, never a value or a path


@dataclass
class Live:
    root: Path
    runtime: SandboxRuntime
    backend: OpenShellBackend
    tmux: Tmux
    repo: Path
    artifact: Artifact
    keys: list[str] = field(default_factory=list[str])      # every session this module launched
    first: dict[str, tuple[LaunchSpec, Started]] = field(
        default_factory=dict[str, tuple[LaunchSpec, Started]])     # cli -> its first launch

    def worktree(self, bead: str) -> Path:
        """The bead's linked worktree on `btq/<bead>`, as btq makes it."""
        path = self.root / f"w-{bead}"
        if not path.exists():
            git(self.repo, "worktree", "add", "-q", "-b", f"btq/{bead}", str(path))
        return path

    def spec(self, cli: str, bead: str, role: str = "coder") -> LaunchSpec:
        profile = PROFILES[cli]
        key = ids.role_session(bead, role, profile)
        return LaunchSpec("alpha", bead, role, profile, key, f"{bead} · {role}", self.worktree(bead),
                          resume=False, native_id=key if cli == "claude" else None, model=MODELS[cli],
                          repo=self.repo)

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
        assert workload == images[self.backend.image], f"{name}'s workload runs image {workload}"
        assert supervisor == images[SUPERVISOR_IMAGE], f"{name}'s supervisor runs image {supervisor}"
        listed = json.loads(podman("container", "inspect", "--format", "{{json .Mounts}}", found[0])) or []
        mounts = [m for m in listed if isinstance(m, dict)]
        runtime = [m for m in mounts if m.get("Destination") == RUNTIME_BINARY]
        assert len(runtime) == 1 and runtime[0].get("Type") == "bind", \
            f"{name}'s runtime binary is not one bind mount at {RUNTIME_BINARY}"
        assert runtime[0].get("Source") == str(self.artifact.runtime_binary), \
            f"{name}'s runtime binary is not the driver's cache for the preflight's {RUNTIME_IMAGE}"
        digest = sha256(self.artifact.runtime_binary)
        assert digest == (self.artifact.runtime_sha256 or digest), \
            f"{name}'s runtime binary changed in the run"
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


def host_config() -> str:
    tool_env = {"PATH": os.environ["HZ_LIVE_SANDBOX_PATH"]}
    if os.environ.get("HZ_LIVE_SANDBOX_CONTAINERS_CONF"):
        tool_env["CONTAINERS_CONF"] = os.environ["HZ_LIVE_SANDBOX_CONTAINERS_CONF"]
    pairs = ", ".join(f'{k} = "{v}"' for k, v in tool_env.items())
    image = os.environ["HZ_LIVE_SANDBOX_IMAGE"]
    return (f'\n[profiles.p-live-codex]\nadapter = "codex"\nargs = ["-c", {CODEX_EFFORT!r}]\n'
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
        pytest.fail(f"preflight: the gateway's endpoint: {exc}")
    return pid, status.get("Version", "")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def started_at(pid: int) -> float:
    """A process's start, in seconds since the epoch: /proc/<pid>/stat's starttime after the boot time."""
    ticks = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    boot = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                if line.startswith("btime "))
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def preflight(settings: SandboxSettings, env: dict[str, str]) -> Artifact:
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
    assert str(exe.readlink()) == binary and digest == packaged, \
        f"preflight: the running gateway is not the packaged {binary}"
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]
    assert len(argv) == 1, "preflight: the gateway was started with arguments"
    penv = dict(e.split("=", 1) for e in Path(f"/proc/{pid}/environ").read_text().split("\0") if "=" in e)
    named = sorted(k for k in penv if k.startswith("OPENSHELL_"))
    assert set(named) <= GATEWAY_ENV and penv.get("OPENSHELL_COMPUTE_DRIVER") == "podman", \
        f"preflight: the gateway's environment sets {named}"
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
    # The image carries no agent CLI: each is the host's pinned install, bound read-only into the sandbox.
    for name, adapter in sorted(REGISTRY.items()):
        cli = adapter.locate(settings.binaries[name], os.environ.get("PATH", os.defpath))
        said = tool(env, str(cli.binary), "--version")
        assert cli.version == adapter.version_pin and adapter.version_pin in said.split(), \
            f"preflight: the {name} CLI is {cli.version} ({said!r}), not the pinned {adapter.version_pin}"
        lines.append(f"EVIDENCE artifact {name} {said}")
    images: dict[str, str] = {}
    for image in (settings.image, *DRIVER_IMAGES):
        found = tool(env, *podman, "image", "inspect", "--format", "{{.Id}} {{json .RepoDigests}}", image)
        images[image] = found.split()[0]
        lines.append(f"EVIDENCE artifact image {image} id {found}")
    # driver_utils.rs supervisor_cache_path: the gateway's XDG data directory, then the driver's subdirectory
    # and the image ID with ':' made '-'. The gateway's own environment resolves it.
    data = Path(penv.get("XDG_DATA_HOME") or Path(penv["HOME"]) / ".local" / "share")
    cache = (data / "openshell" / "podman-sandbox" / images[RUNTIME_IMAGE].replace(":", "-")
             / "openshell-sandbox")
    cached = sha256(cache) if cache.is_file() else ""
    lines.append(f"EVIDENCE artifact runtime binary: bind-mounted from the driver's cache for "
                 f"{RUNTIME_IMAGE} id {images[RUNTIME_IMAGE][:12]}, "
                 f"sha256 {cached[:12] or 'not yet cached'}; "
                 f"residual: {CACHE_RESIDUAL}")
    return Artifact(podman, images, cache, cached, tuple(lines))


def teardown(live: Live) -> list[str]:
    return sandbox_teardown(live.runtime, live.keys, live.backend.names)


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
        artifact = preflight(settings, backend.env)
        print("\n".join(artifact.lines))
        # accounts_at's profiles, plus the cheap Codex profile, each on its default login in the real home
        accounts = ConfiguredAccounts({**ADAPTERS, PROFILES["codex"]: "codex"}, {"HOME": str(real_home)})
        config = RuntimeConfig(
            sessions=root / "s", settings=settings, accounts=accounts, real_home=real_home,
            real_home_canary=canary, wsd_socket=root / "ctl.sock", uid=os.getuid(), gid=os.getgid(),
            tmux=tmux, path=os.environ.get("PATH", os.defpath), clock=utc_now)
        live = Live(root, SandboxRuntime(config, backend, PinnedSelfTest()), backend, tmux,
                    tiny_repo(root / "r"), artifact)
        yield live
    finally:
        uncertain = teardown(live) if live is not None else []
        if uncertain:      # the records, the reapers' tmux server and the scratch stay for recovery
            pytest.fail(f"live teardown is uncertain; kept the scratch {root} and tmux server "
                        f"{live.tmux.socket_name if live else None}: " + "; ".join(uncertain))
        if live is not None:
            live.tmux.kill_server()
        canary.unlink(missing_ok=True)
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_both_shapes_launch_through_the_full_self_test(live: Live, cli: str) -> None:
    spec = live.spec(cli, "btq-live-1")
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
    assert live.run(key, "curl", "-sS", "-m", "10", "-o", "/dev/null", "https://example.org") != 0
    assert live.run(key, "sh", "-c", 'test ! -e "$HOME/.git-credentials" && test ! -e "$HOME/.config/gh" '
                                     '&& test -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}"') == 0


def test_an_agent_commits_on_its_bead_branch_and_nothing_else_moves(live: Live) -> None:
    """Gap 13, D26: git works in a btq linked worktree, and only the bead branch lands on the host."""
    spec = live.spec("claude", "btq-live-3")
    live.launch(spec)
    main = git(live.repo, "rev-parse", "main")
    key = spec.session_key
    try:
        assert live.run(key, "git", "-c", "user.name=agent", "-c", "user.email=agent@example.org", "commit",
                        "--allow-empty", "-q", "-m", "hz-live") == 0
        assert live.run(key, "git", "update-ref", "refs/heads/main", "HEAD") == 0      # its own copy only
        assert live.run(key, "test", "!", "-e", str(live.repo / ".git" / "config")) == 0
        dot_git, marker = spec.worktree / ".git", spec.worktree / "hz-ran"
        pointer = dot_git.read_text()
        assert live.run(key, "sh", "-c", f"echo 'gitdir: /tmp' > {dot_git}") != 0      # bound read-only
        assert live.run(key, "mv", str(dot_git), "/tmp/hz-git") != 0  # noqa: S108 - a mount point
        assert live.run(key, "git", "config", "core.fsmonitor", f"touch {marker}") == 0  # its own config
        git(spec.worktree, "status", "--porcelain")                     # unpinned, as btq's git runs
        assert dot_git.read_text() == pointer and not marker.exists()
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
    assert {"git=0", "fetch=0", "registry=0"} <= set(text.split()), text
    assert "push=0" not in text.split()
    assert "could not read Username for 'https://github.com'" in text


def test_the_reviewer_worktree_is_read_only(live: Live) -> None:
    spec = live.spec("claude", "btq-live-2", role="reviewer")
    live.launch(spec)
    assert live.run(spec.session_key, "touch", str(spec.worktree / "hz-live-ro")) != 0
    assert not (spec.worktree / "hz-live-ro").exists()
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


def test_the_lifetime_stop_ends_a_real_sandbox(live: Live) -> None:
    spec, _ = live.first["claude"]
    rec = live.record(spec.session_key)
    live.runtime.expire("alpha", rec.deadline)
    assert live.record(spec.session_key).phase is Phase.ENDED
    assert rec.sandbox not in live.backend.names()
    assert not live.tmux.has_session(live.runtime.reaper_name(spec.session_key, rec.generation))
