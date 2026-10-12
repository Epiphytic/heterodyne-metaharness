"""Live checks of the OpenShell sandbox runtime: plan 4's live gate. Needs HZ_LIVE=1 (the conftest) and
HZ_LIVE_SANDBOX=1, the S5 host setup, the agent image and both pinned CLIs with a default login. It makes
real model calls: one per launch, plus one prompt. It prints nothing from any login.

Run it only while no wsd on this host uses the OpenShell runtime: that wsd would see these sandboxes
without a record and hold its workstreams until they are gone."""

import contextlib
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))     # tests/: sandbox_env, wsd_env

from sandbox_env import config_env, short_dir  # noqa: E402
from wsd_env import ADAPTERS, git_repo  # noqa: E402

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
from heterodyne.sandbox.settings import sandbox_settings  # noqa: E402
from heterodyne.tmux import Tmux  # noqa: E402
from heterodyne.wsd import ids  # noqa: E402
from heterodyne.wsd.accounts import ConfiguredAccounts  # noqa: E402
from heterodyne.wsd.runtime import LaunchSpec, Liveness, RuntimeUnavailable, Started  # noqa: E402
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


@dataclass
class Live:
    root: Path
    runtime: SandboxRuntime
    backend: OpenShellBackend
    tmux: Tmux
    repo: Path
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
        print(f"EVIDENCE {spec.session_key} g{spec.generation} models={sorted(used)}")     # for docs/wsd.md

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


@pytest.fixture(scope="module")
def live() -> Iterator[Live]:
    real_home = Path.home()
    canary = real_home / ".heterodyne-live-canary"
    with short_dir() as root:
        env = config_env(root / "c", host_config(),
                         {"alpha": '[sandbox]\nextra_egress = ["github.com", "pypi.org"]\n'})
        settings = sandbox_settings(env)
        tmux = Tmux(f"hz-live-sb-{uuid.uuid4().hex[:8]}")
        backend = OpenShellBackend(settings.openshell, settings.podman, settings.image,
                                   tool_env(os.environ, settings.tool_env))     # as wsd's build_runtime
        # accounts_at's profiles, plus the cheap Codex profile, each on its default login in the real home
        accounts = ConfiguredAccounts({**ADAPTERS, PROFILES["codex"]: "codex"}, {"HOME": str(real_home)})
        config = RuntimeConfig(
            sessions=root / "s", settings=settings, accounts=accounts, real_home=real_home,
            real_home_canary=canary, wsd_socket=root / "ctl.sock", uid=os.getuid(), gid=os.getgid(),
            tmux=tmux, path=os.environ.get("PATH", os.defpath), clock=utc_now)
        live = Live(root, SandboxRuntime(config, backend, PinnedSelfTest()), backend, tmux,
                    tiny_repo(root / "r"))
        try:
            yield live
        finally:
            for key in live.keys:
                with contextlib.suppress(RuntimeUnavailable):
                    live.runtime.stop(key)
            tmux.kill_server()
            canary.unlink(missing_ok=True)


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
    live.runtime.stop(spec.session_key)
    resumed = replace(spec, generation=2, resume=True, native_id=first.native_id)
    second = live.runtime.launch(resumed)
    live.pinned(resumed, second)
    assert second.native_id == first.native_id
    assert live.record(spec.session_key).sandbox.endswith("g2")


def test_the_lifetime_stop_ends_a_real_sandbox(live: Live) -> None:
    spec, _ = live.first["claude"]
    rec = live.record(spec.session_key)
    live.runtime.expire("alpha", rec.deadline)
    assert live.record(spec.session_key).phase is Phase.ENDED
    assert rec.sandbox not in live.backend.names()
    assert not live.tmux.has_session(live.runtime.reaper_name(spec.session_key, rec.generation))
