import contextlib
import errno
import fcntl
import json
import logging
import os
import shutil
import socket
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import msgspec
import pytest
from fakes.fake_btq import World, factory
from sandbox_env import (
    RuntimeRig,
    WatchedGate,
    bead_worktree,
    fresh_logins,
    launch_spec,
    runtime_rig,
    short_dir,
    wait_for,
    with_caps,
    with_named,
    write_login,
)
from tmux_guard import new_test_tmux
from wsd_env import PROFILES, Clock, accounts_at, git_repo

from heterodyne.agents.base import HOOK_EVENTS
from heterodyne.agents.claude_code import STATUS_SOURCE
from heterodyne.agents.codex import IN_SESSION_SOURCE
from heterodyne.config.capabilities import Capabilities
from heterodyne.sandbox import reaper, sessiongit
from heterodyne.sandbox import runtime as runtime_mod
from heterodyne.sandbox.backend import ExecResult
from heterodyne.sandbox.runtime import (
    BRANCH_TAKEN,
    DIAG_USAGE_READ,
    MAX_QUEUED,
    RESOLVE_POINTS,
    WIP_FAILED,
    Phase,
    SandboxRuntime,
    SessionRecord,
    read_record,
    write_record,
)
from heterodyne.sandbox.spec import SessionLayout, sandbox_name, short_id
from heterodyne.session import server as session_server
from heterodyne.session import shim
from heterodyne.session.server import SessionServer
from heterodyne.tmux import TmuxError, TmuxPasteUncertain
from heterodyne.wsd import brief, gitwip, ids
from heterodyne.wsd.accounts import ConfiguredAccounts
from heterodyne.wsd.beads import Bead
from heterodyne.wsd.checkpoints import nothing
from heterodyne.wsd.credlock import LOCK_BUSY, credential_lock
from heterodyne.wsd.daemon import Wsd, assemble
from heterodyne.wsd.journal import Journal, JournalBusy
from heterodyne.wsd.runtime import (
    KEY_CHANGED,
    LOGIN_STALE,
    Action,
    EndCause,
    Ended,
    EventBatch,
    LaunchFailed,
    LaunchSpec,
    LaunchUncertain,
    LimitSeen,
    Liveness,
    Resolution,
    ResolveRefused,
    RuntimeUnavailable,
    SessionEvent,
    Signal,
    SteerRefused,
    SteerUncertain,
    UnlandedView,
    UsageSeen,
)
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.usage import Observation
from heterodyne.wsd.workstream import WorkstreamSettings

needs_tools = pytest.mark.skipif(shutil.which("tmux") is None or shutil.which("setsid") is None,
                                 reason="tmux and setsid are needed")
pytestmark = needs_tools


@pytest.fixture
def rig() -> Iterator[RuntimeRig]:
    with short_dir() as root:
        tmux = new_test_tmux()
        rig = runtime_rig(root, tmux, Clock())
        try:
            yield rig
        finally:
            rig.backend.delete_unconfirmed = 0
            for key in list(rig.runtime.servers):
                with contextlib.suppress(RuntimeUnavailable):
                    rig.runtime.stop(key)
            tmux.kill_server()
            assert tmux.socket_path is not None
            tmux.socket_path.unlink(missing_ok=True)


def record(rig: RuntimeRig, key: str) -> SessionRecord:
    rec = read_record(rig.runtime.layout(key))
    assert rec is not None
    return rec


def test_a_claude_launch_runs_and_is_listed(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    started = rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.ran, rec.generation, rec.native_id) == (Phase.RUNNING, True, 1, spec.session_key)
    assert started.native_id == spec.session_key and started.tmux_session == rec.tmux_session
    assert started.tmux_pane.startswith("%") and started.pane_pid > 1
    assert rec.deadline == rec.started_at + rig.runtime.c.settings.max_lifetime_seconds
    assert rig.selftest.exec_runs == rig.selftest.agent_runs == [rec.sandbox]
    [session] = rig.runtime.sessions("alpha")
    assert (session.key, session.bead, session.liveness) == (spec.session_key, "btq-1", Liveness.LIVE)
    assert rig.runtime.sessions("beta") == []
    run = rig.runtime.layout(spec.session_key).run(1)
    assert {p.name for p in run.iterdir()} >= {"token", "shim.py", "shim.json", "ws-request", "probes.py",
                                               "claude-settings.json", "s.sock", "install-record.json"}
    assert all(p.stat().st_mode & 0o077 == 0 for p in run.iterdir() if p.name not in ("s.sock", "empty"))
    empty = rig.runtime.layout(spec.session_key).empty(1)       # E25: 0555 and empty, whatever the adapter
    assert stat.S_IMODE(empty.stat().st_mode) == 0o555 and list(empty.iterdir()) == []


def test_a_codex_launch_reports_its_thread_and_trusts_its_hooks(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    started = rig.runtime.launch(spec)
    assert started.native_id is not None and started.native_id == record(rig, spec.session_key).native_id
    config = (rig.runtime.layout(spec.session_key).home / ".codex" / "config.toml").read_text()
    assert config.count("trusted_hash") == len(HOOK_EVENTS)
    trust_runs = [argv for argv in rig.backend.execs if any(a.endswith("codex_trust.py") for a in argv)]
    assert len(trust_runs) == 2                         # seed, then confirm nothing is left untrusted


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_the_exec_path_is_the_first_run_in_a_new_sandbox(rig: RuntimeRig, profile: str) -> None:
    """D23 (Amendment 1): nothing runs in the sandbox before the exec path's probe, so no process but
    OpenShell's own can reach the pipes its `sandbox exec` uses."""
    before: list[list[list[str]]] = []
    rig.selftest.during_exec = lambda: before.append(list(rig.backend.execs))
    rig.runtime.launch(launch_spec(rig, profile))
    assert before == [[]]


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_the_pane_command_has_no_empty_argument(rig: RuntimeRig, profile: str) -> None:
    """An empty argument is lost wherever argv is joined into one command line (T7: Claude's isolated
    setting sources are passed as the single token `--setting-sources=`)."""
    rig.runtime.launch(launch_spec(rig, profile))
    [tui] = rig.backend.ttys
    assert "" not in tui
    assert ("--setting-sources=" in tui) is (profile == "p-one")


def test_launch_is_idempotent_on_a_live_session(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    assert rig.runtime.launch(spec) == first
    assert len(rig.backend.created) == 1


def test_stop_ends_the_session_and_is_idempotent(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    rig.runtime.stop(spec.session_key)
    assert record(rig, spec.session_key).phase is Phase.ENDED
    assert not rig.tmux.has_session(rec.tmux_session) and rec.sandbox in rig.backend.deleted
    assert rig.runtime.sessions("alpha") == []
    rig.runtime.stop(spec.session_key)
    rig.runtime.stop("never-launched")


def test_an_unconfirmed_stop_is_unavailable_and_stays_listed(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.backend.delete_unconfirmed = 2
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.stop(spec.session_key)
    [session] = rig.runtime.sessions("alpha")
    assert session.liveness is Liveness.UNKNOWN
    assert rig.runtime.sessions("alpha") == []          # the next cleanup is confirmed


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_a_relaunch_resumes_the_same_native_session(rig: RuntimeRig, profile: str) -> None:
    spec = launch_spec(rig, profile)
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    second = rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert second.native_id == first.native_id
    assert record(rig, spec.session_key).sandbox.endswith("g2")


def test_a_resume_that_holds_no_record_starts_fresh(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one", resume=True)
    assert rig.runtime.launch(spec).native_id == spec.session_key


def test_state_that_ran_but_is_gone_is_unavailable(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    sessions = rig.runtime.layout(spec.session_key).home / ".codex" / "sessions"
    for rollout in sessions.rglob("rollout-*.jsonl"):
        rollout.rename(rollout.with_name("rollout-other.jsonl"))       # some state, but not this thread's
    with pytest.raises(RuntimeUnavailable, match="state"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))


@pytest.mark.parametrize("profile, state", [("p-one", ".claude"), ("p-two", ".codex")])
def test_a_session_that_ran_with_its_whole_state_dir_deleted_is_unavailable(
        rig: RuntimeRig, profile: str, state: str) -> None:
    """Plan 3's contract: a record that says a generation ran proves state existed. Deleting all of it
    is not evidence that none ever did, and Codex must never be handed a different thread."""
    spec = launch_spec(rig, profile)
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    shutil.rmtree(rig.runtime.layout(spec.session_key).home / state)
    with pytest.raises(RuntimeUnavailable, match="state"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert record(rig, spec.session_key).generation == 1        # nothing new was recorded


def test_a_home_link_planted_by_the_agent_refuses_the_relaunch(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    conf = rig.runtime.layout(spec.session_key).home / ".claude"
    shutil.rmtree(conf)
    outside = rig.root / "outside"
    outside.mkdir()
    conf.symlink_to(outside)
    with pytest.raises(LaunchFailed, match="won't follow"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert list(outside.iterdir()) == []
    assert not record(rig, spec.session_key).unlanded       # it failed before its git was seeded


def agent_git(rig: RuntimeRig, key: str, *args: str) -> str:
    """git as the agent runs it inside: the worktree's `.git` file finds the private git directory."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {"GIT_DIR": str(rig.runtime.layout(key).git), "GIT_WORK_TREE": str(rig.worktree)}
    r = subprocess.run(["git", "-c", "user.name=agent", "-c", "user.email=agent@example.org", *args],
                       cwd=rig.worktree, env=env, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def host_tip(rig: RuntimeRig, ref: str) -> str:
    return subprocess.run(["git", "-C", str(rig.repo), "rev-parse", ref], capture_output=True, text=True,
                          check=True).stdout.strip()


def test_the_sandbox_gets_a_private_git_dir_and_its_commits_land_on_stop(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    layout = rig.runtime.layout(spec.session_key)
    [box] = rig.backend.boxes.values()
    by_target = {b.target: b for b in box.spec.binds}
    git_dir = rig.repo / ".git" / "worktrees" / "w"
    assert by_target[git_dir.resolve()].source == layout.git and not by_target[git_dir.resolve()].read_only
    assert by_target[(rig.repo / ".git" / "objects").resolve()].read_only
    assert (rig.repo / ".git").resolve() not in by_target
    assert by_target[rig.worktree.resolve() / ".git"].read_only           # btq's git follows it on the host
    main = host_tip(rig, "main")
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    made = agent_git(rig, spec.session_key, "rev-parse", "HEAD")
    agent_git(rig, spec.session_key, "update-ref", "refs/heads/main", made)
    rig.runtime.stop(spec.session_key)
    assert host_tip(rig, "btq/btq-1") == made and host_tip(rig, "main") == main


def stop_unlanded(rig: RuntimeRig, key: str) -> None:
    """The stop ends the session, then refuses: its caller must not commit over unlanded work."""
    with pytest.raises(RuntimeUnavailable, match="were not landed"):
        rig.runtime.stop(key)


def test_commits_that_cant_land_refuse_the_relaunch(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    before = host_tip(rig, "btq/btq-1")
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    (rig.runtime.layout(spec.session_key).git / "objects" / "zz").symlink_to(rig.root)
    stop_unlanded(rig, spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True)
    assert rec.error == "the session's commits could not be landed"
    assert host_tip(rig, "btq/btq-1") == before
    with pytest.raises(LaunchFailed, match="not landed"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert (rig.runtime.layout(spec.session_key).git / "objects" / "zz").is_symlink()     # kept for a human


def test_unlanded_commits_hold_the_workstream_and_name_where_to_look(rig: RuntimeRig) -> None:
    """T10 r3: an unlanded record makes `sessions()` and `stop()` raise, durably and across a restart, so
    no park or defer makes a host WIP over it. The detail names the session and the command that shows
    it (btq-xb56t), never a path."""
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    made = agent_git(rig, spec.session_key, "rev-parse", "HEAD")
    (rig.runtime.layout(spec.session_key).git / "objects" / "zz").symlink_to(rig.root)
    stop_unlanded(rig, spec.session_key)
    restarted = SandboxRuntime(rig.runtime.c, rig.backend, rig.selftest)
    for runtime in (rig.runtime, restarted):
        with pytest.raises(RuntimeUnavailable) as listed:
            runtime.sessions("alpha")
        with pytest.raises(RuntimeUnavailable) as stopped:
            runtime.stop(spec.session_key)
        sid = short_id(spec.session_key)
        for exc in (listed.value, stopped.value):
            assert str(exc) == (f"session {sid} generation 1: its commits were not landed; see "
                                "`wsctl unlanded alpha` (docs/wsd.md §5)")
            assert "/" not in str(exc).removesuffix("(docs/wsd.md §5)")
        assert runtime.sessions("beta") == []                         # another workstream is not held
    assert agent_git_tip(rig, spec.session_key) == made               # the private commits are kept


@pytest.mark.parametrize("lands", [True, False])
def test_a_record_write_lost_around_landing_is_recovered(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch,
                                                        lands: bool) -> None:
    """The write after a landing (`ended`, or `unlanded`) never reaches the disk. The last durable state is
    `stopping`; the next end replays the idempotent landing and records the outcome it would have."""
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    before = host_tip(rig, "btq/btq-1")
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    made = agent_git(rig, spec.session_key, "rev-parse", "HEAD")
    if not lands:
        (rig.runtime.layout(spec.session_key).git / "objects" / "zz").symlink_to(rig.root)

    def lost(layout: SessionLayout, rec: SessionRecord) -> None:
        if rec.phase is Phase.ENDED or rec.unlanded:
            raise OSError("the disk went away")
        write_record(layout, rec)

    with monkeypatch.context() as m:
        m.setattr("heterodyne.sandbox.runtime.write_record", lost)
        with pytest.raises(RuntimeUnavailable):
            rig.runtime.stop(spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.STOPPING, False)
    assert host_tip(rig, "btq/btq-1") == (made if lands else before)
    if lands:
        rig.runtime.stop(spec.session_key)
    else:
        stop_unlanded(rig, spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, not lands)
    assert host_tip(rig, "btq/btq-1") == (made if lands else before)
    if not lands:
        with pytest.raises(LaunchFailed, match="not landed"):
            rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))


@pytest.mark.parametrize("change", ["no repo", "main worktree", "head moved"])
def test_a_worktree_host_git_cant_pin_is_refused(rig: RuntimeRig, change: str) -> None:
    spec = launch_spec(rig, "p-one")
    if change == "no repo":
        spec = replace(spec, repo=None)
    elif change == "main worktree":
        subprocess.run(["git", "-C", str(rig.repo), "checkout", "-q", "-b", "btq/btq-1-main"], check=True,
                       capture_output=True)
        spec = replace(spec, bead="btq-1-main", worktree=rig.repo)
    else:
        (rig.repo / ".git" / "worktrees" / "w" / "HEAD").write_text("ref: refs/heads/main\n")
    with pytest.raises(LaunchFailed):
        rig.runtime.launch(spec)
    assert rig.backend.created == []


DAY = 86_400


def logins_bound(rig: RuntimeRig, key: str) -> list[Path]:
    return [b.source for b in rig.backend.boxes[record(rig, key).sandbox].spec.logins]


@pytest.mark.parametrize(("profile", "adapter", "login"), [("p-one", "claude-code", ".credentials.json"),
                                                           ("p-two", "codex", "auth.json")])
def test_a_named_account_binds_only_its_own_login(rig: RuntimeRig, profile: str, adapter: str,
                                                  login: str) -> None:
    folder = with_named(rig, adapter, "work", rig.clock() + DAY)
    spec = launch_spec(rig, profile, account="work")
    rig.runtime.launch(spec)
    assert logins_bound(rig, spec.session_key) == [(folder / login).resolve()]
    default = rig.home / (".claude" if adapter == "claude-code" else ".codex") / login
    assert rig.selftest.others == [(("default", default),)]     # §7: the default login is another account


def test_the_default_launch_still_protects_every_named_account(rig: RuntimeRig) -> None:
    folder = with_named(rig, "codex", "work", rig.clock() + DAY)
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    assert logins_bound(rig, spec.session_key) == [(rig.home / ".codex" / "auth.json").resolve()]
    assert rig.selftest.others == [(("work", folder / "auth.json"),)]


@pytest.mark.parametrize("pinned", ["", "ck1-" + "0" * 32])
def test_a_key_that_is_not_the_accounts_refuses_the_launch(rig: RuntimeRig, pinned: str) -> None:
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(replace(launch_spec(rig, "p-two"), credential_key=pinned))
    assert str(caught.value) == KEY_CHANGED
    assert rig.backend.created == []


def test_a_login_repointed_after_the_pin_refuses_the_launch(rig: RuntimeRig) -> None:
    """D2: the account's configured path now resolves to other files, so its key is not the pinned one."""
    write_login(rig.root / "login-a", "codex", rig.clock() + DAY)
    write_login(rig.root / "login-b", "codex", rig.clock() + DAY)
    (rig.home / ".hz-work").symlink_to(rig.root / "login-a")
    with_named(rig, "codex", "work", rig.clock() + DAY)         # writes through the link, into login-a
    spec = launch_spec(rig, "p-two", account="work")
    (rig.home / ".hz-work").unlink()
    (rig.home / ".hz-work").symlink_to(rig.root / "login-b")
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(spec)
    assert str(caught.value) == KEY_CHANGED
    assert rig.backend.created == []


def test_the_key_and_the_login_are_read_under_the_accounts_lock(rig: RuntimeRig,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    spec = launch_spec(rig, "p-two")
    lock = rig.runtime.c.credlocks / f"{spec.credential_key}.lock"
    held: list[int | None] = []
    real = ConfiguredAccounts.current_key

    def probing(self: ConfiguredAccounts, adapter: str, account: str) -> str:
        fd = os.open(lock, os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held.append(0)                                      # taken: the runtime did not hold it
        except BlockingIOError as exc:
            held.append(exc.errno)
        finally:
            os.close(fd)
        return real(self, adapter, account)

    monkeypatch.setattr(ConfiguredAccounts, "current_key", probing)
    rig.runtime.launch(spec)
    assert held == [errno.EWOULDBLOCK]


def test_a_held_lock_refuses_the_launch_within_its_bound(rig: RuntimeRig) -> None:
    rig.runtime.c = replace(rig.runtime.c, settings=replace(rig.runtime.c.settings, lock_wait_seconds=1))
    spec = launch_spec(rig, "p-two")
    with credential_lock(rig.runtime.c.credlocks, spec.credential_key, 0):
        with pytest.raises(LaunchFailed) as caught:
            rig.runtime.launch(spec)
    assert str(caught.value) == LOCK_BUSY
    assert rig.backend.created == []
    rig.runtime.launch(spec)                                    # released: the next pickup launches


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_the_freshness_gate_refuses_a_login_that_expires_too_soon(rig: RuntimeRig, profile: str) -> None:
    s = rig.runtime.c.settings
    rig.clock.advance(DAY - (s.max_lifetime_seconds + s.stop_margin_seconds))
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(launch_spec(rig, profile))
    assert str(caught.value) == LOGIN_STALE
    assert rig.backend.created == []


def after_the_check(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, change: Callable[[], None]) -> None:
    """Run `change` as soon as the locked check has returned, before the spec and the bind exist."""
    real = SandboxRuntime._pinned

    def pinned_then_change(self: SandboxRuntime, *args: Any) -> Any:
        checked = real(self, *args)
        change()
        return checked

    monkeypatch.setattr(SandboxRuntime, "_pinned", pinned_then_change)


def during_the_create(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, change: Callable[[], None]) -> None:
    """Run `change` just before the backend makes the sandbox, after the spec was checked."""
    real = rig.backend.create

    def change_then_create(spec: Any, scratch: Path) -> None:
        change()
        real(spec, scratch)

    monkeypatch.setattr(rig.backend, "create", change_then_create)


def repoint_at_default(rig: RuntimeRig, folder: Path) -> Callable[[], None]:
    """Codex r1 (PR #74): the named account's login replaced by a link to the default account's."""
    def change() -> None:
        (folder / "auth.json").unlink()
        (folder / "auth.json").symlink_to(rig.home / ".codex" / "auth.json")
    return change


def replace_with_short_lived(rig: RuntimeRig, folder: Path) -> Callable[[], None]:
    """The named account's login replaced, by rename, with one that expires inside the session's window."""
    def change() -> None:
        s = rig.runtime.c.settings
        write_login(rig.root / "short", "codex", rig.clock() + s.max_lifetime_seconds)
        (rig.root / "short" / "auth.json").replace(folder / "auth.json")
    return change


def rewrite_short_lived_in_place(rig: RuntimeRig, folder: Path) -> Callable[[], None]:
    """The same file rewritten in place (same inode) with a token that expires inside the window."""
    def change() -> None:
        s = rig.runtime.c.settings
        write_login(folder, "codex", rig.clock() + s.max_lifetime_seconds)
    return change


@pytest.mark.parametrize("when", [after_the_check, during_the_create])
def test_a_login_repointed_after_the_locked_check_is_never_bound(rig: RuntimeRig,
                                                                 monkeypatch: pytest.MonkeyPatch,
                                                                 when: Any) -> None:
    folder = with_named(rig, "codex", "work", rig.clock() + DAY)
    when(rig, monkeypatch, repoint_at_default(rig, folder))
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(launch_spec(rig, "p-two", account="work"))
    assert str(caught.value) == KEY_CHANGED
    assert rig.backend.boxes == {}
    if when is after_the_check:
        assert rig.backend.created == []                        # refused before any sandbox is made


@pytest.mark.parametrize("when", [after_the_check, during_the_create])
@pytest.mark.parametrize("swap", [replace_with_short_lived, rewrite_short_lived_in_place])
def test_a_login_made_stale_after_the_locked_check_refuses_the_launch(rig: RuntimeRig,
                                                                      monkeypatch: pytest.MonkeyPatch,
                                                                      when: Any, swap: Any) -> None:
    folder = with_named(rig, "codex", "work", rig.clock() + DAY)
    when(rig, monkeypatch, swap(rig, folder))
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(launch_spec(rig, "p-two", account="work"))
    assert str(caught.value) == LOGIN_STALE
    assert rig.backend.boxes == {}


def test_the_bound_login_is_read_again_under_the_lock(rig: RuntimeRig,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    spec = launch_spec(rig, "p-two")
    lock = rig.runtime.c.credlocks / f"{spec.credential_key}.lock"
    adapter = type(rig.runtime.c.adapters["codex"])
    held: list[int | None] = []
    real = adapter.access_expiry

    def probing(self: Any, login_files: Any) -> float:
        fd = os.open(lock, os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held.append(0)                                      # taken: the runtime did not hold it
        except BlockingIOError as exc:
            held.append(exc.errno)
        finally:
            os.close(fd)
        return real(self, login_files)

    monkeypatch.setattr(adapter, "access_expiry", probing)
    rig.runtime.launch(spec)
    assert held == [errno.EWOULDBLOCK, errno.EWOULDBLOCK]       # the freshness gate, then the bound check


def test_the_freshness_gate_reads_the_chosen_accounts_login(rig: RuntimeRig) -> None:
    s = rig.runtime.c.settings
    soon = rig.clock() + s.max_lifetime_seconds + s.stop_margin_seconds
    with_named(rig, "codex", "work", soon)
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(launch_spec(rig, "p-two", account="work"))
    assert str(caught.value) == LOGIN_STALE
    write_login(rig.home / ".codex", "codex", soon)             # now the default is the stale one
    write_login(rig.home / ".hz-work", "codex", rig.clock() + DAY)
    rig.runtime.launch(launch_spec(rig, "p-two", account="work"))
    assert len(rig.backend.created) == 1


def test_a_claude_handoff_starts_the_d7_session_fresh(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    second = rig.runtime.launch(replace(spec, generation=2, resume=False, handoff=True,
                                        native_id=first.native_id))
    assert second.native_id == ids.handoff_session(spec.session_key, 2) != first.native_id
    pane = rig.backend.ttys[-1]
    assert "--resume" not in pane and pane[pane.index("--session-id") + 1] == second.native_id


def test_a_codex_handoff_starts_a_new_thread(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    second = rig.runtime.launch(replace(spec, generation=2, resume=False, handoff=True,
                                        native_id=first.native_id))
    assert second.native_id is not None and second.native_id != first.native_id
    assert "resume" not in rig.backend.ttys[-1]


def test_the_deadline_runs_from_the_logins_exposure_not_from_a_slow_start(rig: RuntimeRig) -> None:
    s = rig.runtime.c.settings
    t0 = rig.clock.now
    fresh_logins(rig.home, t0 + s.max_lifetime_seconds + s.stop_margin_seconds + 1)
    # the agent path takes longer than the whole margin
    rig.selftest.during_agent = lambda: rig.clock.advance(s.stop_margin_seconds + 60)
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    assert rec.started_at == t0 + s.stop_margin_seconds + 60
    assert rec.deadline == t0 + s.max_lifetime_seconds           # not started_at + the lifetime
    assert rig.backend.reapers == [(rec.sandbox, rec.deadline, rig.runtime.layout(rec.key).record)]
    assert rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))
    rig.runtime.stop(spec.session_key)
    assert not rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))


def test_a_launch_that_outlasts_its_window_is_refused_and_cleaned_up(rig: RuntimeRig) -> None:
    s = rig.runtime.c.settings
    rig.selftest.during_agent = lambda: rig.clock.advance(s.max_lifetime_seconds - s.stop_margin_seconds)
    spec = launch_spec(rig, "p-two")
    with pytest.raises(LaunchFailed, match="outlasted"):
        rig.runtime.launch(spec)
    assert rig.backend.boxes == {}
    assert record(rig, spec.session_key).phase is Phase.ENDED
    assert not rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))


@pytest.mark.parametrize("fail", ["exec", "agent", "create"])
def test_a_failed_step_deletes_the_sandbox(rig: RuntimeRig, fail: str) -> None:
    if fail == "exec":
        rig.selftest.exec_fail = "direct-network-blocked"
    elif fail == "agent":
        rig.selftest.agent_fail = "agent-path-channel: no verified probe run"
    else:
        rig.backend.create_failures = 1
    spec = launch_spec(rig, "p-two")
    with pytest.raises(LaunchFailed) as exc:
        rig.runtime.launch(spec)
    assert {"exec": "direct-network-blocked", "agent": "no verified probe run",
            "create": "sandbox create failed"}[fail] in str(exc.value)
    rec = record(rig, spec.session_key)
    assert rec.phase is Phase.ENDED and rec.error == str(exc.value) and not rec.ran
    assert rig.backend.boxes == {} and not rig.tmux.has_session(rec.tmux_session)
    assert not rig.runtime.layout(spec.session_key).socket(1).exists()
    assert rig.runtime.sessions("alpha") == []


def test_a_failed_step_whose_cleanup_is_unconfirmed_is_uncertain(rig: RuntimeRig) -> None:
    rig.selftest.exec_fail = "outer-fence-network-none"
    rig.backend.delete_unconfirmed = 2                 # the launch's cleanup, then the next listing's
    spec = launch_spec(rig, "p-one")
    with pytest.raises(LaunchUncertain):
        rig.runtime.launch(spec)
    assert record(rig, spec.session_key).phase is Phase.STOPPING
    [session] = rig.runtime.sessions("alpha")
    assert session.liveness is Liveness.UNKNOWN


def test_a_dead_pane_is_cleaned_up(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    rig.tmux.send_key(rec.tmux_session, "C-d")                   # the fake CLI exits at end of input
    wait_for(lambda: rig.tmux.pane_dead(rec.tmux_session))
    assert rig.runtime.sessions("alpha") == []
    assert record(rig, spec.session_key).phase is Phase.ENDED and rig.backend.boxes == {}


def test_a_launch_a_dead_wsd_left_behind_is_cleaned_up_first(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    layout = rig.runtime.layout(spec.session_key)
    stale = SessionRecord(key=spec.session_key, ws="alpha", bead="btq-1", role="coder", profile="p-one",
                          adapter="claude-code", generation=1, phase=Phase.TESTING,
                          sandbox=sandbox_name(spec.session_key, 1),
                          tmux_session=rig.runtime.tmux_name(spec.session_key), worktree=str(rig.worktree))
    write_record(layout, stale)
    rig.backend.extra.add(stale.sandbox)
    assert rig.runtime.sessions("alpha") == []
    assert stale.sandbox in rig.backend.deleted


def test_an_unattributable_sandbox_holds_every_workstream(rig: RuntimeRig) -> None:
    rig.backend.extra.add("hz00000000abcdg1")
    with pytest.raises(RuntimeUnavailable, match="no session record"):
        rig.runtime.sessions("alpha")
    rig.backend.extra = {"someone-elses"}
    assert rig.runtime.sessions("alpha") == []


def test_an_unlistable_backend_or_record_is_unavailable(rig: RuntimeRig) -> None:
    rig.backend.list_failures = 1
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.sessions("alpha")
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.runtime.layout(spec.session_key).record.write_text("{not json")
    with pytest.raises(RuntimeUnavailable, match="record"):
        rig.runtime.sessions("alpha")


def test_a_restarted_runtime_rebinds_the_live_session(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    old = rig.runtime.servers.pop(spec.session_key)
    old.close()                                                   # wsd died: the socket went with it
    [session] = rig.runtime.sessions("alpha")
    assert session.liveness is Liveness.LIVE
    server = rig.runtime.servers[spec.session_key]
    rec = record(rig, spec.session_key)
    rig.tmux.paste(rec.tmux_session, "after the restart")
    wait_for(lambda: server.turns().stops == 1)


def test_a_repository_pin_refuses_is_a_failed_launch(rig: RuntimeRig) -> None:
    """T7A: pin refuses a repository whose config could run code; the launch fails cleanly, not a crash."""
    subprocess.run(["git", "-C", str(rig.repo), "config", "filter.x.clean", "cat"], check=True)
    with pytest.raises(LaunchFailed, match="driver"):
        rig.runtime.launch(launch_spec(rig, "p-one"))
    assert rig.backend.created == []


def test_a_pin_refused_at_the_end_keeps_the_commits_unlanded(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    before = host_tip(rig, "btq/btq-1")
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    subprocess.run(["git", "-C", str(rig.repo), "config", "filter.x.clean", "cat"], check=True)
    stop_unlanded(rig, spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True)
    assert host_tip(rig, "btq/btq-1") == before
    with pytest.raises(LaunchFailed, match="driver"):           # the pin refuses before anything else
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    subprocess.run(["git", "-C", str(rig.repo), "config", "--unset", "filter.x.clean"], check=True)
    with pytest.raises(LaunchFailed, match="not landed"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))


def test_a_worktree_gone_before_the_landing_keeps_the_commits_unlanded(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    made = agent_git(rig, spec.session_key, "rev-parse", "HEAD")
    shutil.rmtree(rig.worktree)
    stop_unlanded(rig, spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True)
    assert host_tip(rig, "btq/btq-1") != made
    assert agent_git_tip(rig, spec.session_key) == made            # kept for a human


def agent_git_tip(rig: RuntimeRig, key: str) -> str:
    return subprocess.run(["git", "--git-dir", str(rig.runtime.layout(key).git), "rev-parse", "btq/btq-1"],
                          capture_output=True, text=True, check=True).stdout.strip()


# --- Codex r1: journal failures, landing evidence, the owed WIP, SessionStart, stale sockets ---


def gone(rig: RuntimeRig, key: str, generation: int = 1) -> bool:
    """Every physical part of the generation is gone: sandbox, pane and socket."""
    return (rig.backend.boxes == {} and not rig.tmux.has_session(rig.runtime.tmux_name(key))
            and not os.path.lexists(rig.runtime.layout(key).socket(generation)))


def test_a_record_unreadable_after_create_still_cleans_up(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")

    def corrupt() -> None:
        rig.runtime.layout(spec.session_key).record.write_text("{not json")

    rig.selftest.during_exec = corrupt
    rig.selftest.exec_fail = "outer-fence-network-none"
    with pytest.raises(LaunchUncertain):
        rig.runtime.launch(spec)
    assert gone(rig, spec.session_key)
    with pytest.raises(RuntimeUnavailable, match="record"):
        rig.runtime.sessions("alpha")                         # nothing is assumed: the key stays held


def test_record_writes_lost_after_create_still_clean_up_but_never_land(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = launch_spec(rig, "p-one")
    before = host_tip(rig, "btq/btq-1")
    down: list[bool] = []

    def journal(layout: SessionLayout, rec: SessionRecord) -> None:
        if down:
            raise OSError("the disk went away")
        write_record(layout, rec)

    def during() -> None:
        agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
        down.append(True)

    monkeypatch.setattr("heterodyne.sandbox.runtime.write_record", journal)
    rig.selftest.during_exec = during
    rig.selftest.exec_fail = "outer-fence-network-none"
    with pytest.raises(LaunchUncertain):
        rig.runtime.launch(spec)
    assert gone(rig, spec.session_key)
    assert record(rig, spec.session_key).phase is Phase.TESTING          # the last durable state
    assert host_tip(rig, "btq/btq-1") == before                          # nothing landed unrecorded
    made = agent_git(rig, spec.session_key, "rev-parse", "HEAD")
    down.clear()
    assert rig.runtime.sessions("alpha") == []                           # the next end lands it
    assert record(rig, spec.session_key).phase is Phase.ENDED
    assert host_tip(rig, "btq/btq-1") == made


def test_a_private_git_dir_gone_after_seeding_is_unlanded(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    shutil.rmtree(rig.runtime.layout(spec.session_key).git)
    stop_unlanded(rig, spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True)
    with pytest.raises(LaunchFailed, match="not landed"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))


def wip_commits(rig: RuntimeRig) -> int:
    log = subprocess.run(["git", "-C", str(rig.repo), "log", "--format=%B", "btq/btq-1"], capture_output=True,
                         text=True, check=True).stdout
    return log.count(f"{gitwip.PARK_MARK}lifetime:")


def lifetime_end(rig: RuntimeRig, key: str, *, unlanded: bool = False) -> None:
    """The pane died and the deadline passed: the next listing ends the session for its lifetime."""
    rec = record(rig, key)
    rig.tmux.kill(rec.tmux_session)
    rig.clock.advance(rec.deadline - rig.clock.now)
    if unlanded:
        with pytest.raises(RuntimeUnavailable, match="were not landed"):
            rig.runtime.sessions("alpha")
    else:
        assert rig.runtime.sessions("alpha") == []


def test_a_lifetime_stop_whose_landing_fails_owes_its_wip_and_commits_none(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    before = host_tip(rig, "btq/btq-1")
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    (rig.worktree / "edit.txt").write_text("unsaved work\n")
    (rig.runtime.layout(spec.session_key).git / "objects" / "zz").symlink_to(rig.root)
    lifetime_end(rig, spec.session_key, unlanded=True)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded, rec.stop_reason) == (Phase.ENDED, True, "lifetime")
    assert rec.wip_mark == f"lifetime:{spec.session_key}:1"                # still owed, not dropped
    assert host_tip(rig, "btq/btq-1") == before and wip_commits(rig) == 0
    with pytest.raises(LaunchFailed, match="not landed"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert host_tip(rig, "btq/btq-1") == before and record(rig, spec.session_key).wip_mark


@pytest.mark.parametrize("when", ["before", "after"])
def test_a_failed_lifetime_wip_is_kept_and_retried_once(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch,
                                                        when: str) -> None:
    """A transient failure keeps the mark; the next launch retries it. A failure after the commit was made
    replays to the same commit (it is idempotent by its mark), so exactly one lands."""
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    (rig.worktree / "edit.txt").write_text("unsaved work\n")
    real = gitwip.wip_commit
    fails = [True]

    def flaky(p: gitwip.Pinned, mark: str, summary: str) -> str:
        if fails:
            fails.clear()
            if when == "after":
                real(p, mark, summary)
            raise gitwip.GitFailed("git timed out")
        return real(p, mark, summary)

    monkeypatch.setattr("heterodyne.wsd.gitwip.wip_commit", flaky)
    lifetime_end(rig, spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.error) == (Phase.ENDED, WIP_FAILED) and rec.wip_mark
    assert wip_commits(rig) == (1 if when == "after" else 0)
    fresh_logins(rig.home, rig.clock() + 86_400)
    rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert wip_commits(rig) == 1
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.wip_mark, rec.error) == (Phase.RUNNING, "", "")


def test_an_owed_wip_that_still_fails_refuses_the_relaunch(rig: RuntimeRig,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)

    def broken(p: gitwip.Pinned, mark: str, summary: str) -> str:
        raise gitwip.GitFailed("git timed out")

    monkeypatch.setattr("heterodyne.wsd.gitwip.wip_commit", broken)
    lifetime_end(rig, spec.session_key)
    fresh_logins(rig.home, rig.clock() + 86_400)
    with pytest.raises(LaunchFailed, match="still owed"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert rig.backend.created == [sandbox_name(spec.session_key, 1)]


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_a_resume_that_reports_no_session_start_fails(rig: RuntimeRig, profile: str) -> None:
    """D8/D24: only the generation's own SessionStart says which session the CLI resumed."""
    spec = launch_spec(rig, profile)
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    rig.backend.env_extra["HZ_FAKE_NO_SESSION_START"] = "1"
    with pytest.raises(LaunchFailed, match="no session ID"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert rig.selftest.agent_runs[-1].endswith("g2")                  # the self-test itself passed
    assert record(rig, spec.session_key).phase is Phase.ENDED


def test_a_crashed_wsds_socket_path_is_removed(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.runtime.servers.pop(spec.session_key).close()           # wsd died ...
    path = rig.runtime.layout(spec.session_key).socket(1)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))                                        # ... and left its socket's path behind
    stale.close()
    rig.tmux.kill(record(rig, spec.session_key).tmux_session)
    assert rig.runtime.sessions("alpha") == []
    assert not os.path.lexists(path)


# --- Codex r2: a socket that won't close, a vanished record, a tmux server that can't be reached ---


def test_a_socket_that_wont_close_still_cleans_up_the_rest(rig: RuntimeRig,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    spec = launch_spec(rig, "p-one")
    real = SessionServer.close
    fails = [True]

    def close(self: SessionServer) -> None:
        if fails:
            raise PermissionError("the socket can't be unlinked")
        real(self)

    monkeypatch.setattr(SessionServer, "close", close)
    rig.selftest.exec_fail = "outer-fence-network-none"
    with pytest.raises(LaunchUncertain):
        rig.runtime.launch(spec)
    assert rig.backend.boxes == {} and not rig.tmux.has_session(rig.runtime.tmux_name(spec.session_key))
    assert record(rig, spec.session_key).phase is not Phase.ENDED       # unconfirmed, so not ended
    assert spec.session_key in rig.runtime.servers                      # kept, so the next end closes it
    fails.clear()
    assert rig.runtime.sessions("alpha") == []
    assert record(rig, spec.session_key).phase is Phase.ENDED and rig.runtime.servers == {}


def test_a_record_that_vanishes_after_create_leaves_a_hold(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    layout = rig.runtime.layout(spec.session_key)

    def vanish() -> None:
        agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
        layout.record.unlink()

    rig.selftest.during_exec = vanish
    rig.selftest.exec_fail = "outer-fence-network-none"
    before = host_tip(rig, "btq/btq-1")
    with pytest.raises(LaunchUncertain):
        rig.runtime.launch(spec)
    assert gone(rig, spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True)              # rewritten as a hold, never landed
    made = agent_git_tip(rig, spec.session_key)
    assert host_tip(rig, "btq/btq-1") == before != made
    with pytest.raises(LaunchFailed, match="not landed"):
        rig.runtime.launch(spec)
    assert agent_git_tip(rig, spec.session_key) == made                  # the evidence is kept


def test_a_git_directory_without_its_record_refuses_a_fresh_launch(rig: RuntimeRig) -> None:
    """Even when no hold could be written: the private git directory may hold commits, and a fresh launch
    would reseed it."""
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    made = agent_git_tip(rig, spec.session_key)
    rig.runtime.stop(spec.session_key)
    rig.runtime.layout(spec.session_key).record.unlink()
    with pytest.raises(LaunchFailed, match="git directory"):
        rig.runtime.launch(spec)
    assert agent_git_tip(rig, spec.session_key) == made and rig.backend.created == [
        sandbox_name(spec.session_key, 1)]


def test_a_tmux_server_it_cant_reach_never_confirms_the_pane_gone(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    assert rig.tmux.socket_path is not None
    rig.tmux.socket_path.chmod(0)
    try:
        with pytest.raises(RuntimeUnavailable, match="could not be confirmed"):
            rig.runtime.stop(spec.session_key)
        assert record(rig, spec.session_key).phase is Phase.STOPPING
    finally:
        rig.tmux.socket_path.chmod(0o700)
    rig.runtime.stop(spec.session_key)
    assert record(rig, spec.session_key).phase is Phase.ENDED
    assert not rig.tmux.has_session(rig.runtime.tmux_name(spec.session_key))


# --- g08sd: how each generation ended ---

def cause(rig: RuntimeRig, key: str) -> str:
    rec = record(rig, key)
    assert rec.phase is Phase.ENDED
    return rec.end_cause


def kill_pane(rig: RuntimeRig, key: str) -> None:
    rec = record(rig, key)
    rig.tmux.kill(rec.tmux_session)
    assert not rig.tmux.has_session(rec.tmux_session)


def reaper_acts(rig: RuntimeRig, key: str) -> bool:
    """What the reaper does at its first pass past the deadline: decide, once, then kill and delete."""
    rec, layout = record(rig, key), rig.runtime.layout(key)

    def pane_live() -> bool:
        return rig.tmux.has_session(rec.tmux_session) and not rig.tmux.pane_dead(rec.tmux_session)

    marked = reaper.mark_live(layout.record, layout.reaped(rec.generation), rec.sandbox, rec.generation,
                              rig.backend.names, pane_live)
    rig.tmux.kill(rec.tmux_session)
    rig.backend.delete(rec.sandbox)
    return marked


def at_deadline(rig: RuntimeRig, key: str) -> int:
    deadline = record(rig, key).deadline
    rig.clock.advance(deadline - rig.clock.now)
    return deadline


@pytest.mark.parametrize("how", ["stop", "expire", "found dead", "refused", "stop dead", "expire dead"])
def test_end_cause_needs_a_live_generation(rig: RuntimeRig, how: str) -> None:
    spec = launch_spec(rig, "p-one")
    if how == "refused":
        rig.selftest.exec_fail = "direct-network-blocked"
        with pytest.raises(LaunchFailed):
            rig.runtime.launch(spec)
        assert cause(rig, spec.session_key) == EndCause.REFUSED
        return
    rig.runtime.launch(spec)
    if how.endswith("dead") or how == "found dead":
        kill_pane(rig, spec.session_key)
    if how.startswith("stop"):
        rig.runtime.stop(spec.session_key)
    elif how.startswith("expire"):
        rig.runtime.expire("alpha", at_deadline(rig, spec.session_key))
    else:
        assert rig.runtime.sessions("alpha") == []
    expected = {"stop": EndCause.STOPPED, "expire": EndCause.LIFETIME}.get(how, EndCause.DIED)
    assert cause(rig, spec.session_key) == expected
    assert record(rig, spec.session_key).ended_at == rig.clock.now


class Crash(Exception):
    pass


def crash_once_stopping(rig: RuntimeRig, key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The next teardown crashes after its `stopping` write: the backend's listing raises from then on."""
    listed = rig.backend.names

    def names() -> set[str]:
        if record(rig, key).phase is Phase.STOPPING:
            raise Crash
        return listed()

    monkeypatch.setattr(rig.backend, "names", names)


@pytest.mark.parametrize("first", [EndCause.DIED, EndCause.STOPPED])
def test_an_end_cause_is_never_changed(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, first: str) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    key = spec.session_key
    with monkeypatch.context() as m:
        crash_once_stopping(rig, key, m)
        with pytest.raises(Crash):
            if first == EndCause.DIED:
                kill_pane(rig, key)
                rig.runtime.sessions("alpha")
            else:
                rig.runtime.stop(key)
    rec = record(rig, key)
    assert (rec.phase, rec.end_cause, rec.stop_reason) == (Phase.STOPPING, first, "")
    deadline = at_deadline(rig, key)
    assert rig.runtime.sessions("alpha") == []              # the replay crosses the deadline
    rec = record(rig, key)
    assert (rec.phase, rec.end_cause, rec.stop_reason) == (Phase.ENDED, first, "lifetime")
    assert rec.wip_mark == "" and wip_commits(rig) == 1     # the lifetime WIP landed, once
    rig.runtime.expire("alpha", deadline)
    assert wip_commits(rig) == 1 and cause(rig, key) == first


@pytest.mark.parametrize("how", ["stop", "expire"])
def test_a_healthy_stop_whose_cause_cant_be_written_stops_nothing(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    """Code r1: the `stopping` write carries the healthy cause. If it fails, the session is left running
    and the stop is unconfirmed, so a restart lists it live and the retry records the healthy cause; a
    teardown first would leave a RUNNING record with no cause, which the restart would end `died`."""
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    key = spec.session_key
    deadline = at_deadline(rig, key)
    real = SandboxRuntime._phase

    def failing(self: SandboxRuntime, layout: SessionLayout, rec: SessionRecord,
                **changes: object) -> SessionRecord:
        if changes.get("phase") is Phase.STOPPING:
            raise OSError("disk full")
        return real(self, layout, rec, **changes)

    with monkeypatch.context() as m:
        m.setattr(SandboxRuntime, "_phase", failing)
        with pytest.raises(RuntimeUnavailable):
            if how == "stop":
                rig.runtime.stop(key)
            else:
                rig.runtime.expire("alpha", deadline)
    rec = record(rig, key)
    assert (rec.phase, rec.end_cause) == (Phase.RUNNING, "")
    assert rig.tmux.has_session(rec.tmux_session) and rec.sandbox in rig.backend.names()
    restarted = SandboxRuntime(rig.runtime.c, rig.backend, rig.selftest)
    assert [s.key for s in restarted.sessions("alpha")] == [key]   # still live, not ended `died`
    if how == "stop":
        restarted.stop(key)
    else:
        restarted.expire("alpha", deadline)
    assert cause(rig, key) == {"stop": EndCause.STOPPED, "expire": EndCause.LIFETIME}[how]


def test_delayed_discovery_after_the_deadline_is_a_death(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    kill_pane(rig, spec.session_key)
    rig.backend.up = False
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.sessions("alpha")
    at_deadline(rig, spec.session_key)
    rig.clock.advance(3600)
    rig.backend.up = True
    assert rig.runtime.sessions("alpha") == []
    rec = record(rig, spec.session_key)
    assert (rec.end_cause, rec.stop_reason) == (EndCause.DIED, "lifetime") and wip_commits(rig) == 1


@pytest.mark.parametrize("found", ["sessions", "racing stop", "expire"])
@pytest.mark.parametrize("late", ["long earlier job", "lane contention", "stale now"])
def test_a_late_pickup_still_ends_lifetime(rig: RuntimeRig, late: str, found: str) -> None:
    """However pickup misses the deadline, the reaper acts first, finds the generation live and marks it;
    whichever of wsd's reads then finds it gone ends it `lifetime`. A stale `now` expires nothing."""
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    key = spec.session_key
    now = rig.clock.now                                     # read before a slow listing
    deadline = at_deadline(rig, key)
    if late != "stale now":
        rig.clock.advance(600)                              # the job or the lane holds pickup this long
    assert reaper_acts(rig, key) is True
    if late == "stale now":
        rig.runtime.expire("alpha", now)
        assert record(rig, key).phase is Phase.RUNNING
    if found == "sessions":
        assert rig.runtime.sessions("alpha") == []
    elif found == "racing stop":
        rig.runtime.stop(key)
    else:
        rig.runtime.expire("alpha", max(deadline, rig.clock.now))
    assert cause(rig, key) == EndCause.LIFETIME
    assert not rig.runtime.layout(key).reaped(1).exists()   # the cause is in the record now
    ended = rig.runtime.ended(key)
    assert ended is not None and ended.cause is EndCause.LIFETIME and ended.generation == 1


def test_a_generation_dead_at_the_deadline_has_no_marker_and_died(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    kill_pane(rig, spec.session_key)
    at_deadline(rig, spec.session_key)
    assert reaper_acts(rig, spec.session_key) is False
    assert rig.runtime.sessions("alpha") == []
    assert cause(rig, spec.session_key) == EndCause.DIED


@pytest.mark.parametrize("then", ["hard stop live", "later death"])
def test_a_crash_after_the_marker_and_before_the_kill_is_lifetime(rig: RuntimeRig, then: str) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    key = spec.session_key
    rec, layout = record(rig, key), rig.runtime.layout(key)
    deadline = at_deadline(rig, key)
    assert reaper.mark_live(layout.record, layout.reaped(1), rec.sandbox, 1, rig.backend.names,
                            lambda: True)                   # then the reaper crashed: nothing killed
    if then == "hard stop live":
        rig.runtime.expire("alpha", deadline)
    else:
        kill_pane(rig, key)
        assert rig.runtime.sessions("alpha") == []
    assert cause(rig, key) == EndCause.LIFETIME


def test_a_crash_before_the_link_leaves_only_a_temporary_file(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    layout = rig.runtime.layout(spec.session_key)
    at_deadline(rig, spec.session_key)
    (layout.root / f"{layout.reaped(1).name}.4242.tmp").write_text(json.dumps(
        {"sandbox": record(rig, spec.session_key).sandbox, "generation": 1, "at": 1}))
    kill_pane(rig, spec.session_key)
    assert rig.runtime.sessions("alpha") == []
    assert cause(rig, spec.session_key) == EndCause.DIED


def test_a_marker_left_after_the_end_is_removed_by_the_next_launch(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    layout = rig.runtime.layout(spec.session_key)
    rig.runtime.stop(spec.session_key)
    left = [layout.reaped(1), layout.root / f"{layout.reaped(1).name}.7.tmp"]
    for path in left:                                       # a crash after `ended`, before the removal
        path.write_text("{}")
    rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert not any(p.exists() for p in left)
    assert not list(layout.root.glob("reaped-r*"))


@pytest.mark.parametrize("forged", ["wrong sandbox", "wrong generation", "malformed", "not an object",
                                    "a link", "a directory", "a fifo", "in a bound directory"])
def test_the_marker_cannot_be_forged(rig: RuntimeRig, forged: str) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    key = spec.session_key
    rec, layout = record(rig, key), rig.runtime.layout(key)
    path = layout.reaped(1)
    good = json.dumps({"sandbox": rec.sandbox, "generation": 1, "at": 1})
    if forged == "wrong sandbox":
        path.write_text(json.dumps({"sandbox": "hz0000000000aag1", "generation": 1, "at": 1}))
    elif forged == "wrong generation":
        path.write_text(json.dumps({"sandbox": rec.sandbox, "generation": 2, "at": 1}))
    elif forged == "malformed":
        path.write_text("{nope")
    elif forged == "not an object":
        path.write_text("[]")
    elif forged == "a link":
        (layout.root / "elsewhere.json").write_text(good)
        path.symlink_to(layout.root / "elsewhere.json")
    elif forged == "a directory":
        path.mkdir()
    elif forged == "a fifo":
        os.mkfifo(path)
    else:
        bound = [d for d in (layout.home, layout.bridge, layout.run(1), layout.git) if d.is_dir()]
        assert len(bound) >= 3
        for folder in bound:
            (folder / path.name).write_text(good)
    at_deadline(rig, key)
    kill_pane(rig, key)
    assert rig.runtime.sessions("alpha") == []
    assert cause(rig, key) == EndCause.DIED


def test_the_marker_is_outside_every_bind(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    layout = rig.runtime.layout(spec.session_key)
    [box] = rig.backend.boxes.values()
    marker = layout.reaped(1).resolve()
    assert marker.parent == layout.record.resolve().parent
    for bind in box.spec.binds:
        assert not marker.is_relative_to(bind.source.resolve()), bind
    assert rig.backend.watches[-1].marker == layout.reaped(1)


def test_an_unreadable_marker_is_unavailable(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    path = rig.runtime.layout(spec.session_key).reaped(1)
    path.write_text("{}")
    path.chmod(0)
    try:
        kill_pane(rig, spec.session_key)
        with pytest.raises(RuntimeUnavailable, match="reaper's record"):
            rig.runtime.sessions("alpha")
        assert record(rig, spec.session_key).phase is Phase.RUNNING      # nothing recorded
    finally:
        path.chmod(0o600)
    assert rig.runtime.sessions("alpha") == []
    assert cause(rig, spec.session_key) == EndCause.DIED


def test_ended_reports_generation_evidence(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    key = spec.session_key
    assert rig.runtime.ended(key) is None                   # no record
    first = rig.runtime.launch(spec)
    assert rig.runtime.ended(key) is None                   # running
    rig.clock.advance(41)
    rig.runtime.stop(key)
    assert rig.runtime.ended(key) == Ended(1, EndCause.STOPPED, True, 41)
    layout = rig.runtime.layout(key)

    def rewrite(**changes: object) -> None:
        data = json.loads(layout.record.read_text())
        for name, value in changes.items():
            if value is None:
                data.pop(name, None)
            else:
                data[name] = value
        layout.record.write_text(json.dumps(data))

    rewrite(ended_at=None)                                  # a record written before g08sd
    assert rig.runtime.ended(key) == Ended(1, EndCause.STOPPED, True, None)
    rewrite(end_cause=None, stop_reason="lifetime")
    assert rig.runtime.ended(key) == Ended(1, EndCause.LEGACY_LIFETIME, True, None)
    rewrite(stop_reason="")
    assert rig.runtime.ended(key) == Ended(1, EndCause.DIED, True, None)
    rewrite(started_at=0)
    ended = rig.runtime.ended(key)
    assert ended is not None and ended.reached_running is False
    layout.record.write_text("{nope")
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.ended(key)
    rewrite_ok = SessionRecord(key=key, ws="alpha", bead="btq-1", role="coder", profile="p-one",
                               adapter="claude-code", generation=1, phase=Phase.ENDED,
                               sandbox=sandbox_name(key, 1), tmux_session=rig.runtime.tmux_name(key),
                               worktree=str(rig.worktree), native_id=first.native_id)
    write_record(layout, rewrite_ok)
    assert rig.runtime.ended(key) == Ended(1, EndCause.DIED, False, None)


@pytest.mark.parametrize("reason", ["lifetime", ""])
def test_a_legacy_stopping_record_is_finished_by_its_cause(rig: RuntimeRig, reason: str) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    layout = rig.runtime.layout(spec.session_key)
    write_record(layout, msgspec.structs.replace(
        record(rig, spec.session_key), phase=Phase.STOPPING, stop_reason=reason,
        wip_mark=f"lifetime:{spec.session_key}:1" if reason else ""))
    assert rig.runtime.ended(spec.session_key) is None      # listed `unknown` until an end finishes it
    assert rig.runtime.sessions("alpha") == []
    expected = EndCause.LEGACY_LIFETIME if reason else EndCause.DIED
    assert cause(rig, spec.session_key) == expected
    ended = rig.runtime.ended(spec.session_key)
    assert ended is not None and ended.cause is expected


# btq-jjnqm: a bead's branch holds one session at a time.

def other_record(rig: RuntimeRig, key: str, phase: Phase, *, ws: str = "alpha",
                 **changes: object) -> SessionRecord:
    """A record of another key on bead btq-1 in the rig's repository, written as a dead wsd left it."""
    rec = SessionRecord(key=key, ws=ws, bead="btq-1", role="coder", profile="p-two", adapter="codex",
                        generation=1, phase=phase, sandbox=sandbox_name(key, 1),
                        tmux_session=rig.runtime.tmux_name(key), worktree=str(rig.worktree),
                        repo=str(rig.repo))
    rec = msgspec.structs.replace(rec, **changes)
    write_record(rig.runtime.layout(key), rec)
    return rec


def keys_sorted(first_low: bool) -> tuple[str, str]:
    """Two session keys whose record directories sort (short ID) as asked: `first_low` puts the first one
    first."""
    keys = [ids.role_session("btq-1", "coder", f"p-k{i}") for i in range(2)]
    if (short_id(keys[0]) < short_id(keys[1])) != first_low:
        keys.reverse()
    return keys[0], keys[1]


def owe_wip(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    """End `key` for its lifetime with its WIP commit failing, so its record is ENDED and owes the WIP."""
    real = gitwip.wip_commit

    def broken(p: gitwip.Pinned, mark: str, summary: str) -> str:
        raise gitwip.GitFailed("git timed out")

    (rig.worktree / "edit.txt").write_text("unsaved work\n")
    monkeypatch.setattr("heterodyne.wsd.gitwip.wip_commit", broken)
    lifetime_end(rig, key)
    monkeypatch.setattr("heterodyne.wsd.gitwip.wip_commit", real)
    rec = record(rig, key)
    assert (rec.phase, rec.error) == (Phase.ENDED, WIP_FAILED) and rec.wip_mark
    fresh_logins(rig.home, rig.clock() + 86_400)


@pytest.mark.parametrize("phase", [p for p in Phase if p is not Phase.ENDED])
def test_a_second_session_on_the_same_bead_is_refused(rig: RuntimeRig, phase: Phase) -> None:
    second = launch_spec(rig, "p-one")
    if phase is Phase.RUNNING:
        first = launch_spec(rig, "p-two")
        rig.runtime.launch(first)
        holder = first.session_key
    else:
        holder = ids.role_session("btq-1", "coder", "p-two")
        other_record(rig, holder, phase)
    created = list(rig.backend.created)
    with pytest.raises(LaunchFailed) as refused:
        rig.runtime.launch(second)
    assert str(refused.value) == BRANCH_TAKEN
    assert read_record(rig.runtime.layout(second.session_key)) is None
    assert rig.backend.created == created
    assert record(rig, holder).phase is phase


def test_the_branch_is_free_once_the_first_session_ends(rig: RuntimeRig) -> None:
    first, second = launch_spec(rig, "p-one"), launch_spec(rig, "p-two")
    rig.runtime.launch(first)
    rig.runtime.stop(first.session_key)
    assert record(rig, first.session_key).phase is Phase.ENDED
    rig.runtime.launch(second)
    assert record(rig, second.session_key).phase is Phase.RUNNING


def test_a_dead_but_not_ended_session_still_holds_the_branch(rig: RuntimeRig) -> None:
    first, second = launch_spec(rig, "p-one"), launch_spec(rig, "p-two")
    rig.runtime.launch(first)
    rec = record(rig, first.session_key)
    rig.tmux.send_key(rec.tmux_session, "C-d")                   # the fake CLI exits at end of input
    wait_for(lambda: rig.tmux.pane_dead(rec.tmux_session))
    with pytest.raises(LaunchFailed, match="still holds its branch"):
        rig.runtime.launch(second)
    assert record(rig, first.session_key).phase is Phase.RUNNING
    assert rig.runtime.sessions("alpha") == []
    rig.runtime.launch(second)
    assert record(rig, second.session_key).phase is Phase.RUNNING


def test_an_unlanded_session_of_another_key_holds_the_branch(rig: RuntimeRig) -> None:
    first, second = launch_spec(rig, "p-one"), launch_spec(rig, "p-two")
    rig.runtime.launch(first)
    agent_git(rig, first.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    (rig.runtime.layout(first.session_key).git / "objects" / "zz").symlink_to(rig.root)
    stop_unlanded(rig, first.session_key)
    before = record(rig, first.session_key)
    with pytest.raises(LaunchFailed, match="still holds its branch"):
        rig.runtime.launch(second)
    assert record(rig, first.session_key) == before
    assert read_record(rig.runtime.layout(second.session_key)) is None


def test_an_owed_wip_of_another_key_is_settled_first(rig: RuntimeRig,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = launch_spec(rig, "p-one"), launch_spec(rig, "p-two")
    rig.runtime.launch(first)
    owe_wip(rig, monkeypatch, first.session_key)
    rig.runtime.launch(second)
    assert wip_commits(rig) == 1
    assert record(rig, first.session_key).wip_mark == ""
    assert record(rig, second.session_key).phase is Phase.RUNNING


def test_an_owed_wip_of_another_key_that_still_fails_holds_the_branch(rig: RuntimeRig,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = launch_spec(rig, "p-one"), launch_spec(rig, "p-two")
    rig.runtime.launch(first)
    owe_wip(rig, monkeypatch, first.session_key)

    def broken(p: gitwip.Pinned, mark: str, summary: str) -> str:
        raise gitwip.GitFailed("git timed out")

    monkeypatch.setattr("heterodyne.wsd.gitwip.wip_commit", broken)
    created = list(rig.backend.created)
    with pytest.raises(LaunchFailed, match="still holds its branch"):
        rig.runtime.launch(second)
    assert record(rig, first.session_key).wip_mark and wip_commits(rig) == 0
    assert rig.backend.created == created


def test_a_settled_wip_whose_mark_cant_be_cleared_holds_then_lands_once(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    """The WIP commit is made but the record write that clears its mark fails: the launch is refused with
    nothing created for the new key; a retry finds the commit by its mark and lands no second one."""
    first, second = launch_spec(rig, "p-one"), launch_spec(rig, "p-two")
    rig.runtime.launch(first)
    owe_wip(rig, monkeypatch, first.session_key)
    real = SandboxRuntime._phase
    fails = [True]

    def flaky(self: SandboxRuntime, layout: SessionLayout, rec: SessionRecord,
              **changes: object) -> SessionRecord:
        if fails and changes.get("wip_mark") == "":
            fails.clear()
            raise OSError("disk full")
        return real(self, layout, rec, **changes)

    monkeypatch.setattr(SandboxRuntime, "_phase", flaky)
    created = list(rig.backend.created)
    with pytest.raises(LaunchFailed, match="still holds its branch"):
        rig.runtime.launch(second)
    assert read_record(rig.runtime.layout(second.session_key)) is None and rig.backend.created == created
    assert record(rig, first.session_key).wip_mark and wip_commits(rig) == 1
    rig.runtime.launch(second)
    assert wip_commits(rig) == 1 and record(rig, first.session_key).wip_mark == ""


@pytest.mark.parametrize("hold", ["running", "unlanded"])
@pytest.mark.parametrize("wip_first", [True, False])
def test_a_refusal_settles_nothing(rig: RuntimeRig, hold: str, wip_first: bool) -> None:
    """Codex jjnqm r2: with one record owing a WIP and another holding the branch, the launch is refused
    before any WIP is committed, whichever record the directory listing returns first."""
    owing, holding = keys_sorted(wip_first)
    other_record(rig, owing, Phase.ENDED, wip_mark=f"lifetime:{owing}:1", stop_reason="lifetime")
    if hold == "running":
        other_record(rig, holding, Phase.RUNNING)
    else:
        other_record(rig, holding, Phase.ENDED, unlanded=True)
    (rig.worktree / "edit.txt").write_text("unsaved work\n")
    tip = host_tip(rig, "btq/btq-1")
    records = {k: record(rig, k) for k in (owing, holding)}
    with pytest.raises(LaunchFailed, match="still holds its branch"):
        rig.runtime.launch(launch_spec(rig, "p-one"))
    assert host_tip(rig, "btq/btq-1") == tip and wip_commits(rig) == 0
    assert (rig.worktree / "edit.txt").read_text() == "unsaved work\n"
    status = subprocess.run(["git", "-C", str(rig.worktree), "status", "--porcelain"], capture_output=True,
                            text=True, check=True).stdout
    assert "edit.txt" in status
    assert {k: record(rig, k) for k in records} == records


def test_a_hold_in_another_workstream_clears_through_that_workstream(rig: RuntimeRig) -> None:
    first, second = launch_spec(rig, "p-one"), replace(launch_spec(rig, "p-two"), ws="beta")
    rig.runtime.launch(first)
    rec = record(rig, first.session_key)
    rig.tmux.send_key(rec.tmux_session, "C-d")
    wait_for(lambda: rig.tmux.pane_dead(rec.tmux_session))
    with pytest.raises(LaunchFailed, match="still holds its branch"):
        rig.runtime.launch(second)
    assert rig.runtime.sessions("beta") == []
    assert record(rig, first.session_key).phase is Phase.RUNNING          # not another workstream's to end
    assert rig.runtime.sessions("alpha") == []
    rig.runtime.launch(second)
    assert record(rig, second.session_key).phase is Phase.RUNNING


def test_a_stale_same_key_record_is_ended_before_the_check(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    stale = SessionRecord(key=spec.session_key, ws="alpha", bead="btq-1", role="coder", profile="p-one",
                          adapter="claude-code", generation=1, phase=Phase.TESTING,
                          sandbox=sandbox_name(spec.session_key, 1),
                          tmux_session=rig.runtime.tmux_name(spec.session_key), worktree=str(rig.worktree),
                          repo=str(rig.repo))
    write_record(rig.runtime.layout(spec.session_key), stale)
    rig.backend.extra.add(stale.sandbox)
    rig.runtime.launch(replace(spec, generation=2))
    assert stale.sandbox in rig.backend.deleted
    assert record(rig, spec.session_key).phase is Phase.RUNNING


def test_a_dead_running_same_key_session_is_ended_before_the_check(rig: RuntimeRig) -> None:
    """Design test 6: a session of this key that ran and whose pane died, with no listing or stop since,
    is ended by its own relaunch (landing what it owes), and the relaunch then succeeds."""
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    rig.tmux.send_key(rec.tmux_session, "C-d")                   # the fake CLI exits at end of input
    wait_for(lambda: rig.tmux.pane_dead(rec.tmux_session))
    assert record(rig, spec.session_key).phase is Phase.RUNNING
    rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert rec.sandbox in rig.backend.deleted
    after = record(rig, spec.session_key)
    assert (after.phase, after.generation) == (Phase.RUNNING, 2)


def test_a_repo_alias_is_the_same_repo(rig: RuntimeRig) -> None:
    rig.runtime.launch(launch_spec(rig, "p-one"))
    alias = rig.root / "alias"
    alias.symlink_to(rig.repo)
    with pytest.raises(LaunchFailed, match="still holds its branch"):
        rig.runtime.launch(replace(launch_spec(rig, "p-two"), repo=alias))


def test_other_beads_and_other_repos_are_not_refused(rig: RuntimeRig) -> None:
    rig.runtime.launch(launch_spec(rig, "p-one"))
    w2 = rig.root / "w2"
    subprocess.run(["git", "-C", str(rig.repo), "worktree", "add", "-q", "-b", "btq/btq-2", str(w2)],
                   check=True, capture_output=True)
    other_bead = replace(launch_spec(rig, "p-two", "btq-2"), worktree=w2)
    rig.runtime.launch(other_bead)
    repo2 = git_repo(rig.root / "r2")
    w3 = rig.root / "w3"
    subprocess.run(["git", "-C", str(repo2), "worktree", "add", "-q", "-b", "btq/btq-1", str(w3)],
                   check=True, capture_output=True)
    other_repo = replace(launch_spec(rig, "p-two"), worktree=w3, repo=repo2,
                         session_key=ids.role_session("btq-1", "reviewer", "p-two"))
    rig.runtime.launch(other_repo)
    assert {record(rig, k).phase for k in (other_bead.session_key, other_repo.session_key)} == {Phase.RUNNING}


# --- btq-xb56t: the operator's view and resolve of unlanded sessions (docs/wsd.md §5) ---


def host_git(rig: RuntimeRig, *args: str) -> str:
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-C", str(rig.repo),
                        *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def make_unlanded(rig: RuntimeRig, profile: str = "p-one", *, diverge: bool = False, bead: str = "btq-1",
                  messages: tuple[bytes, ...] = (b"agent work",)) -> tuple[str, str]:
    """A session whose commits were left unlanded, as its key and its tip. Its landing fails because the
    bead branch moved (`diverge`), or else because the pin refused it once, so it is a fast-forward now.
    A bead other than btq-1 gets a worktree of its own: a branch holds one session at a time."""
    spec = launch_spec(rig, profile, bead)
    if bead != "btq-1":
        spec = replace(spec, worktree=bead_worktree(rig, bead))
    rig.runtime.launch(spec)
    for n, message in enumerate(messages):
        (rig.root / "msg").write_bytes(message)
        agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "--cleanup=verbatim",
                  "-F", str(rig.root / "msg"), f"--date=@{1_700_000_000 + n}")
    made = agent_git(rig, spec.session_key, "rev-parse", "HEAD")
    if diverge:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-C", str(rig.worktree),
                        "commit", "-q", "--allow-empty", "-m", "host work"], check=True, capture_output=True)
        stop_unlanded(rig, spec.session_key)
    else:
        host_git(rig, "config", "filter.x.clean", "cat")
        stop_unlanded(rig, spec.session_key)
        host_git(rig, "config", "--unset", "filter.x.clean")
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True)
    return spec.session_key, made


def intent_path(rig: RuntimeRig, key: str, generation: int = 1) -> Path:
    return rig.runtime.c.sessions.parent / "resolutions" / f"{short_id(key)}-g{generation}.json"


def trash_path(rig: RuntimeRig, key: str, generation: int = 1) -> Path:
    return rig.runtime.c.sessions.parent / "resolutions" / f"{short_id(key)}-g{generation}.trash"


def resolved_views(rig: RuntimeRig) -> dict[str, UnlandedView]:
    return {v.short_id: v for v in rig.runtime.unlanded("alpha")}


def landings(rig: RuntimeRig) -> int:
    log = host_git(rig, "reflog", "show", "--format=%gs", "refs/heads/btq/btq-1")
    return log.count("resolve the session's unlanded commits")


def unchanged(rig: RuntimeRig, key: str, branch: str) -> None:
    """A refused resolve changed nothing: the record, the bead branch, and no intent or trash."""
    rec = record(rig, key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True)
    assert host_tip(rig, "btq/btq-1") == branch
    assert not os.path.lexists(intent_path(rig, key)) and not os.path.lexists(trash_path(rig, key))
    assert host_git(rig, "for-each-ref", "refs/heads/unlanded") == ""


def break_tip(rig: RuntimeRig, key: str, how: str, bead: str = "btq-1") -> None:
    git = rig.runtime.layout(key).git
    loose = git / "refs" / "heads" / "btq" / bead
    tip = loose.read_text().strip()
    if how == "missing ref":
        loose.unlink()
    elif how == "malformed packed-refs":
        loose.unlink()
        (git / "packed-refs").write_text(f"# pack-refs with: peeled\nnot a sha refs/heads/btq/{bead}\n")
    else:
        (git / "objects" / tip[:2] / tip[2:]).unlink()


@pytest.mark.parametrize("how", ["missing ref", "malformed packed-refs", "objects gone"])
def test_unlanded_view(rig: RuntimeRig, how: str) -> None:
    """Fast-forwardable and unreadable tips, side by side: one failing record doesn't hide another. The
    subjects are untrusted bytes, shown as printable ASCII of at most 72 characters."""
    long = b"x" * 100
    key, made = make_unlanded(rig, messages=(b"plain\n", b"bad \xff byte\n", b"\x1b[31mred\x07\n",
                                             b"two\nlines\n", long + b"\n"))
    landed = host_tip(rig, "btq/btq-1")
    other, _ = make_unlanded(rig, "p-two", bead="btq-2")
    break_tip(rig, other, how, "btq-2")
    views = resolved_views(rig)
    assert set(views) == {short_id(key), short_id(other)}
    good = views[short_id(key)]
    assert (good.generation, good.bead, good.branch, good.running) == (1, "btq-1", "btq/btq-1", False)
    assert (good.landed, good.tip, good.ahead, good.behind, good.fast_forward) == (landed, made, 5, 0, True)
    assert good.subjects == ("x" * 72, "two lines", "?[31mred?", "bad ? byte", "plain")
    bad = views[short_id(other)]
    assert (bad.bead, bad.branch) == ("btq-2", "btq/btq-2")
    assert bad.tip is None and bad.error and bad.ahead is None and bad.subjects == ()


def test_unlanded_view_shows_a_diverged_tip(rig: RuntimeRig) -> None:
    key, made = make_unlanded(rig, diverge=True)
    view = resolved_views(rig)[short_id(key)]
    assert (view.tip, view.ahead, view.behind, view.fast_forward) == (made, 1, 1, False)
    assert view.landed == host_tip(rig, "btq/btq-1") != made


def test_resolve_land(rig: RuntimeRig) -> None:
    key, made = make_unlanded(rig)
    sid = short_id(key)
    end = rig.runtime.ended(key)
    done = rig.runtime.resolve_unlanded("alpha", sid, 1, "land", made)
    assert done == Resolution("alpha", "btq-1", sid, 1, "land", made, "", key, end)
    assert host_tip(rig, "btq/btq-1") == made and landings(rig) == 1
    assert not os.path.lexists(rig.runtime.layout(key).root) and not os.path.lexists(trash_path(rig, key))
    rig.runtime.finish_resolution("alpha", sid, 1)
    assert rig.runtime.sessions("alpha") == [] and rig.runtime.unlanded("alpha") == []


def test_resolve_land_refuses_a_diverged_branch(rig: RuntimeRig) -> None:
    key, made = make_unlanded(rig, diverge=True)
    branch = host_tip(rig, "btq/btq-1")
    with pytest.raises(ResolveRefused, match="can't be fast-forwarded"):
        rig.runtime.resolve_unlanded("alpha", short_id(key), 1, "land", made)
    unchanged(rig, key, branch)


def test_resolve_keep(rig: RuntimeRig) -> None:
    key, made = make_unlanded(rig, diverge=True)
    sid, branch = short_id(key), host_tip(rig, "btq/btq-1")
    ref = f"refs/heads/unlanded/btq-1/{sid}-g1"
    end = rig.runtime.ended(key)
    done = rig.runtime.resolve_unlanded("alpha", sid, 1, "keep", made)
    assert done == Resolution("alpha", "btq-1", sid, 1, "keep", made, ref, key, end)
    assert host_tip(rig, ref) == made and host_tip(rig, "btq/btq-1") == branch
    assert not os.path.lexists(rig.runtime.layout(key).root)


@pytest.mark.parametrize("taken", ["same tip", "another sha", "parent ref", "namespace ref"])
def test_resolve_keep_with_its_ref_taken(rig: RuntimeRig, taken: str) -> None:
    """A keep ref already at the tip is a replay's; any other taken name refuses with the fixed reason."""
    key, made = make_unlanded(rig, diverge=True)
    sid, branch = short_id(key), host_tip(rig, "btq/btq-1")
    ref = f"refs/heads/unlanded/btq-1/{sid}-g1"
    if taken == "same tip":
        host_git(rig, "fetch", "-q", str(rig.runtime.layout(key).git), f"refs/heads/btq/btq-1:{ref}")
        assert rig.runtime.resolve_unlanded("alpha", sid, 1, "keep", made).ref == ref
        assert host_tip(rig, ref) == made
        return
    name = {"another sha": ref, "parent ref": "refs/heads/unlanded/btq-1",
            "namespace ref": "refs/heads/unlanded"}[taken]
    host_git(rig, "update-ref", name, branch)
    with pytest.raises(ResolveRefused, match="^the keep ref can't be created$"):
        rig.runtime.resolve_unlanded("alpha", sid, 1, "keep", made)
    assert host_tip(rig, name) == branch and record(rig, key).unlanded
    assert not os.path.lexists(intent_path(rig, key))


def test_resolve_discard(rig: RuntimeRig) -> None:
    key, made = make_unlanded(rig, diverge=True)
    sid, branch = short_id(key), host_tip(rig, "btq/btq-1")
    with pytest.raises(ResolveRefused, match="--tip"):
        rig.runtime.resolve_unlanded("alpha", sid, 1, "discard", None)       # readable: it must be confirmed
    unchanged(rig, key, branch)
    assert rig.runtime.resolve_unlanded("alpha", sid, 1, "discard", made).tip == made
    assert host_tip(rig, "btq/btq-1") == branch and not os.path.lexists(rig.runtime.layout(key).root)


def test_resolve_discards_an_unreadable_tip_without_tip(rig: RuntimeRig) -> None:
    key, made = make_unlanded(rig)
    sid, branch = short_id(key), host_tip(rig, "btq/btq-1")
    break_tip(rig, key, "objects gone")
    for action in ("land", "keep"):
        with pytest.raises(ResolveRefused, match="can't be read"):
            rig.runtime.resolve_unlanded("alpha", sid, 1, action, made)  # type: ignore[arg-type]
    with pytest.raises(ResolveRefused, match="can't be read"):
        rig.runtime.resolve_unlanded("alpha", sid, 1, "discard", made)
    unchanged(rig, key, branch)
    assert rig.runtime.resolve_unlanded("alpha", sid, 1, "discard", None).tip is None
    assert not os.path.lexists(rig.runtime.layout(key).root)


REFUSALS = ["moved tip", "wrong generation", "another workstream", "not ended", "not unlanded",
            "bad short ID", "pane", "older reaper", "older sandbox", "orphan"]


@pytest.mark.parametrize("case", REFUSALS + ["tmux listing", "backend listing"])
def test_resolve_refuses(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    key, made = make_unlanded(rig)
    sid, branch = short_id(key), host_tip(rig, "btq/btq-1")
    layout = rig.runtime.layout(key)
    ws, generation, tip = "alpha", 1, made
    if case == "moved tip":
        tip = branch
    elif case == "wrong generation":
        generation = 2
    elif case == "another workstream":
        ws = "beta"
    elif case in ("not ended", "not unlanded"):
        change = {"phase": Phase.STOPPING} if case == "not ended" else {"unlanded": False}
        write_record(layout, msgspec.structs.replace(record(rig, key), **change))
    elif case == "bad short ID":
        sid = sid.upper()
    elif case == "pane":
        rig.tmux.new_session(f"wsd-{sid}", rig.root, ["sleep", "60"])
    elif case in ("older reaper", "older sandbox"):
        # The record is of generation 3; the actor left over is generation 1's, so only a match across
        # generations finds it.
        generation = 3
        write_record(layout, msgspec.structs.replace(record(rig, key), generation=3))
        if case == "older reaper":
            rig.tmux.new_session(f"wsd-{sid}-r1", rig.root, ["sleep", "60"])
        else:
            rig.backend.extra.add(f"{sid}g1")
    elif case == "orphan":
        layout.record.unlink()
    elif case == "tmux listing":
        real = rig.tmux._exec

        def lost(argv: list[str], *, input: bytes | None,
                 timeout: float) -> subprocess.CompletedProcess[bytes]:
            if "list-sessions" in argv:                     # tmux's own words for a dropped connection
                return subprocess.CompletedProcess(argv, 1, b"", b"server exited unexpectedly\n")
            return real(argv, input=input, timeout=timeout)
        monkeypatch.setattr(rig.tmux, "_exec", lost)
    else:
        rig.backend.list_failures = 1
    raises = RuntimeUnavailable if case.endswith("listing") else ResolveRefused
    with pytest.raises(raises) as refused:
        rig.runtime.resolve_unlanded(ws, sid, generation, "land", tip)
    if case == "orphan":
        assert str(refused.value) == "the session has no record; see docs/wsd.md §5"
        assert host_tip(rig, "btq/btq-1") == branch and not os.path.lexists(intent_path(rig, key))
        return
    rec = record(rig, key)
    assert host_tip(rig, "btq/btq-1") == branch and os.path.lexists(layout.record)
    for g in (1, 3):
        assert not os.path.lexists(intent_path(rig, key, g)) and not os.path.lexists(trash_path(rig, key, g))
    inspect = rig.runtime.c.sessions.parent / "inspect"
    assert not inspect.exists() or not any(inspect.iterdir())
    assert rec.unlanded == (case != "not unlanded")
    assert rec.generation == (3 if case.startswith("older") else 1)


def crash_at(rig: RuntimeRig, point: str) -> None:
    def cp(name: str) -> None:
        if name == point:
            raise Crash(name)
    rig.runtime.cp = cp


def restarted(rig: RuntimeRig) -> SandboxRuntime:
    return SandboxRuntime(rig.runtime.c, rig.backend, rig.selftest)


def test_a_clean_resolve_passes_every_point(rig: RuntimeRig) -> None:
    key, made = make_unlanded(rig)
    passed: list[str] = []
    rig.runtime.cp = passed.append
    rig.runtime.resolve_unlanded("alpha", short_id(key), 1, "land", made)
    assert tuple(passed) == RESOLVE_POINTS


def record_durability(runtime: SandboxRuntime, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Every fsync of the import, every directory sync and intent write of the runtime, and every point,
    in order."""
    events: list[tuple[str, str]] = []
    fsync, sync_dir, write = sessiongit._fsync, runtime_mod._sync_dir, runtime_mod.write_private

    def fsync_(path: Path, *, directory: bool = False) -> None:
        events.append(("fsync", str(path)))
        fsync(path, directory=directory)

    def sync_dir_(path: Path) -> None:
        events.append(("sync", str(path)))
        sync_dir(path)

    def write_(path: Path, text: str) -> None:
        events.append(("write", str(path)))
        write(path, text)
    monkeypatch.setattr(sessiongit, "_fsync", fsync_)
    monkeypatch.setattr(runtime_mod, "_sync_dir", sync_dir_)
    monkeypatch.setattr(runtime_mod, "write_private", write_)
    runtime.cp = lambda name: events.append(("cp", name))
    return events


def host_common(rig: RuntimeRig) -> Path:
    return Path(host_git(rig, "rev-parse", "--path-format=absolute", "--git-common-dir"))


@pytest.mark.safety
@pytest.mark.parametrize("run", ["first", "replay"])
@pytest.mark.parametrize("action", ["land", "keep"])
def test_the_import_is_durable_before_it_is_recorded(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch,
                                                      action: Action, run: str) -> None:
    key, made = make_unlanded(rig)
    sid, common = short_id(key), host_common(rig)
    runtime = rig.runtime
    if run == "replay":                       # the import happened, then a crash before its checkpoint:
        crash_at(rig, "imported")             # the replay finds it done and must still make it durable
        with pytest.raises(Crash):
            rig.runtime.resolve_unlanded("alpha", sid, 1, action, made)
        runtime = restarted(rig)
    events = record_durability(runtime, monkeypatch)
    done = runtime.resolve_unlanded("alpha", sid, 1, action, made)
    ref = "refs/heads/btq/btq-1" if action == "land" else done.ref
    recorded = events.index(("cp", "imported"))
    synced = {path for kind, path in events[:recorded] if kind == "fsync"}
    packs = {str(e) for e in (common / "objects" / "pack").iterdir()}
    parts = ref.split("/")
    dirs = {str(common.joinpath(*parts[:n])) for n in range(len(parts))}
    assert packs and packs <= synced
    loose = common.joinpath(*parts)
    assert (str(loose) in synced) == loose.exists()
    assert dirs | {str(common / "objects"), str(common / "objects" / "pack")} <= synced


@pytest.mark.safety
def test_the_state_directory_is_synced_before_the_first_intent(rig: RuntimeRig,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    key, made = make_unlanded(rig)
    state = rig.runtime.c.sessions.parent
    assert not os.path.lexists(state / "resolutions")
    events = record_durability(rig.runtime, monkeypatch)
    rig.runtime.resolve_unlanded("alpha", short_id(key), 1, "land", made)
    first = events.index(("write", str(intent_path(rig, key))))
    assert ("sync", str(state)) in events[:first]
    assert not any(kind in ("fsync", "cp") for kind, _ in events[:first])    # nothing done before it


@pytest.mark.parametrize("action", ["land", "keep", "discard"])
@pytest.mark.parametrize("point", RESOLVE_POINTS)
def test_resolve_replays_from_each_step(rig: RuntimeRig, point: str, action: Action) -> None:
    key, made = make_unlanded(rig)
    sid, branch = short_id(key), host_tip(rig, "btq/btq-1")
    crash_at(rig, point)
    with pytest.raises(Crash):
        rig.runtime.resolve_unlanded("alpha", sid, 1, action, made)
    runtime = restarted(rig)
    for held in (rig.runtime, runtime):
        with pytest.raises(RuntimeUnavailable, match=f"resolve of session {sid} is unfinished"):
            held.sessions("alpha")
        with pytest.raises(RuntimeUnavailable, match="unfinished"):
            held.stop(key)
    done = runtime.resolve_unlanded("alpha", sid, 1, action, made)
    assert (done.action, done.tip) == (action, made)
    assert runtime.resolve_unlanded("alpha", sid, 1, action, made) == done        # a replay of the end
    with pytest.raises(RuntimeUnavailable, match="unfinished"):
        runtime.sessions("alpha")
    runtime.finish_resolution("alpha", sid, 1)
    assert runtime.sessions("alpha") == []
    assert host_tip(rig, "btq/btq-1") == (made if action == "land" else branch)
    assert landings(rig) == (action == "land")
    if action == "keep":
        assert host_tip(rig, done.ref) == made
    assert not os.path.lexists(rig.runtime.layout(key).root) and not os.path.lexists(trash_path(rig, key))


@pytest.mark.parametrize("state", ["both present", "confirmed without S", "S replaced", "T replaced"])
def test_resolve_replay_refuses_inconsistent_state(rig: RuntimeRig, state: str) -> None:
    key, made = make_unlanded(rig)
    sid = short_id(key)
    here, trash = rig.runtime.layout(key).root, trash_path(rig, key)
    crash_at(rig, {"both present": "imported.recorded", "confirmed without S": "imported",
                   "S replaced": "imported", "T replaced": "moved"}[state])
    with pytest.raises(Crash):
        rig.runtime.resolve_unlanded("alpha", sid, 1, "discard", made)
    aside = rig.root / "aside"
    if state == "both present":
        trash.mkdir()
    elif state == "confirmed without S":
        here.rename(aside)
    else:
        moved = here if state == "S replaced" else trash
        moved.rename(aside)
        shutil.copytree(aside, moved, symlinks=True)
    before = intent_path(rig, key).read_bytes()
    present = (os.path.lexists(here), os.path.lexists(trash))
    with pytest.raises(ResolveRefused, match=r"^the resolve's state is inconsistent; see docs/wsd.md §5$"):
        restarted(rig).resolve_unlanded("alpha", sid, 1, "discard", made)
    assert intent_path(rig, key).read_bytes() == before
    assert (os.path.lexists(here), os.path.lexists(trash)) == present


@pytest.mark.parametrize("point", RESOLVE_POINTS)
@pytest.mark.parametrize("retry", ["workstream", "action", "tip", "generation"])
def test_resolve_replay_binds_the_intent_identity(rig: RuntimeRig, point: str, retry: str) -> None:
    key, made = make_unlanded(rig)
    sid, branch = short_id(key), host_tip(rig, "btq/btq-1")
    crash_at(rig, point)
    with pytest.raises(Crash):
        rig.runtime.resolve_unlanded("alpha", sid, 1, "land", made)
    before = intent_path(rig, key).read_bytes()
    here, trash = rig.runtime.layout(key).root, trash_path(rig, key)
    present = (os.path.lexists(here), os.path.lexists(trash))
    tips = host_tip(rig, "btq/btq-1")
    ws, action, tip, generation = "alpha", "land", made, 1
    if retry == "workstream":
        ws = "beta"
    elif retry == "action":
        action = "keep"
    elif retry == "tip":
        tip = branch
    else:
        generation = 2
    with pytest.raises(ResolveRefused, match="unfinished resolve"):
        restarted(rig).resolve_unlanded(ws, sid, generation, action, tip)  # type: ignore[arg-type]
    assert intent_path(rig, key).read_bytes() == before and host_tip(rig, "btq/btq-1") == tips
    assert (os.path.lexists(here), os.path.lexists(trash)) == present


def test_the_hold_clears_after_resolve(rig: RuntimeRig) -> None:
    """Only `finish_resolution` ends the hold: the journal entry comes between, in the daemon."""
    key, made = make_unlanded(rig)
    sid = short_id(key)
    rig.runtime.resolve_unlanded("alpha", sid, 1, "land", made)
    for _ in range(2):
        with pytest.raises(RuntimeUnavailable, match="unfinished"):
            rig.runtime.sessions("alpha")
    assert rig.runtime.sessions("beta") == []                          # another workstream is not held
    rig.runtime.finish_resolution("alpha", sid, 1)
    assert rig.runtime.sessions("alpha") == [] and not os.path.lexists(intent_path(rig, key))
    rig.runtime.finish_resolution("alpha", sid, 1)                       # idempotent


# --- the resolve job with the real runtime and the real journal (Codex code r1) ---


def wsd_settings(rig: RuntimeRig) -> WsdSettings:
    repo = rig.root / "wsd" / "proj"
    if not repo.exists():
        git_repo(repo)
    accounts = accounts_at(rig.home)
    ws = WorkstreamSettings("alpha", {"default": repo}, "coder", "p-one", PROFILES, accounts=accounts)
    base = rig.root / "wsd"
    return WsdSettings(base / "state", 0.05, 3600, 3, base / "btq", {}, (ws,), accounts,
                       config_dir=rig.installs["codex"].config_dir)


def journal_at(rig: RuntimeRig) -> Path:
    return wsd_settings(rig).journal


@contextlib.contextmanager
def resolving_wsd(rig: RuntimeRig, runtime: SandboxRuntime, cp: Any = nothing) -> Iterator[Wsd]:
    """A wsd on `runtime` and a journal kept under the rig's root across restarts, closed on exit (an open
    one would fail test_wsd_gate's descriptor count)."""
    s = wsd_settings(rig)
    j = Journal(s.journal)
    try:
        yield Wsd(s, assemble(s, j, factory(World(rig.root / "wsd" / "btq-state")), runtime, cp=cp))
    finally:
        j.close()


def crash_ends(rig: RuntimeRig) -> list[tuple[str, str]]:
    """btq-wpyv5: every end a resolve carried into the journal."""
    j = Journal(journal_at(rig))
    try:
        rows = j.db.execute("SELECT key, value FROM meta WHERE key LIKE 'crash_end:%' ORDER BY key")
        return [(str(k), str(v)) for k, v in rows.fetchall()]
    finally:
        j.close()


def resolved(rig: RuntimeRig) -> list[tuple[str, str | None, str | None, str]]:
    j = Journal(journal_at(rig))
    try:
        return [(e.ws, e.bead, e.ref, e.detail) for e in j.events_since(0) if e.kind == "unlanded_resolved"]
    finally:
        j.close()


@pytest.mark.parametrize("point", [*RESOLVE_POINTS, "resolve.resolved", "resolve.journaled"])
def test_a_resolve_job_replays_from_each_point_with_one_journal_entry(rig: RuntimeRig, point: str) -> None:
    key, made = make_unlanded(rig)
    sid = short_id(key)

    def crash(name: str) -> None:
        if name == point:
            raise Crash(name)
    rig.runtime.cp = crash
    with resolving_wsd(rig, rig.runtime, cp=crash) as wsd, pytest.raises(Crash):
        wsd.resolve_one("alpha", sid, 1, "keep", made)
    assert os.path.lexists(intent_path(rig, key))
    assert len(resolved(rig)) == (point == "resolve.journaled")
    runtime = restarted(rig)                                # a new wsd: a new runtime, the same journal
    with pytest.raises(RuntimeUnavailable, match="unfinished"):
        runtime.sessions("alpha")
    with resolving_wsd(rig, runtime) as wsd:
        reply = wsd.resolve_one("alpha", sid, 1, "keep", made)
        assert reply.result == "ok", reply.message
        assert wsd.resolve_one("alpha", sid, 1, "keep", made).result == "refused"    # nothing left to resolve
    ref = f"refs/heads/unlanded/btq-1/{sid}-g1"
    assert resolved(rig) == [("alpha", "btq-1", f"{sid}-g1", f"action=keep tip={made} ref={ref}")]
    assert [k for k, _ in crash_ends(rig)] == [f"crash_end:alpha:btq-1:{key}:1"]
    assert runtime.sessions("alpha") == [] and not os.path.lexists(intent_path(rig, key))
    assert host_tip(rig, ref) == made and not os.path.lexists(rig.runtime.layout(key).root)


def test_a_failed_journal_entry_keeps_the_removed_intent_and_the_hold(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    key, made = make_unlanded(rig)
    sid = short_id(key)
    with resolving_wsd(rig, rig.runtime) as wsd:
        def busy(*args: object) -> bool:
            raise JournalBusy("SQLITE_BUSY")
        monkeypatch.setattr(wsd.parts.journal, "emit_resolved", busy)
        reply = wsd.resolve_one("alpha", sid, 1, "land", made)
        assert reply.result == "failed" and "JournalBusy" in reply.message
        intent = msgspec.json.decode(intent_path(rig, key).read_bytes())
        assert intent["step"] == "removed" and not os.path.lexists(rig.runtime.layout(key).root)
        assert crash_ends(rig) == []
        assert host_tip(rig, "btq/btq-1") == made
        for runtime in (rig.runtime, restarted(rig)):
            with pytest.raises(RuntimeUnavailable, match=f"resolve of session {sid} is unfinished"):
                runtime.sessions("alpha")
            with pytest.raises(RuntimeUnavailable, match="unfinished"):
                runtime.stop(key)
        monkeypatch.undo()
        assert wsd.resolve_one("alpha", sid, 1, "land", made).result == "ok"
    assert resolved(rig) == [("alpha", "btq-1", f"{sid}-g1", f"action=land tip={made}")]
    assert rig.runtime.sessions("alpha") == [] and landings(rig) == 1


@pytest.mark.parametrize("action", ["land", "keep", "discard"])
def test_a_resolved_generation_has_no_end_to_read(rig: RuntimeRig, action: Action) -> None:
    """g08sd: the record, and so its end cause, goes with the directory, as in manual step 4, and the next
    guard counts the generation as a death (docs/wsd.md §5). Its reaper marker goes too."""
    key, made = make_unlanded(rig)
    layout = rig.runtime.layout(key)
    before = rig.runtime.ended(key)
    assert before is not None and before.generation == 1
    layout.reaped(1).write_text("{}")                        # a marker left beside the record
    done = rig.runtime.resolve_unlanded("alpha", short_id(key), 1, action, made)
    assert (done.key, done.end) == (key, before)               # wpyv5: carried to the journal instead
    rig.runtime.finish_resolution("alpha", short_id(key), 1)
    assert rig.runtime.ended(key) is None and not os.path.lexists(layout.reaped(1))


# --- btq-wpyv5: the resolve carries the generation's end ---

ENDINGS = {"stopped": ("stopped", ""), "lifetime": ("lifetime", ""), "died": ("died", ""),
           "legacy lifetime": ("", "lifetime"), "legacy death": ("", "")}


def ended_as(rig: RuntimeRig, key: str, ending: str) -> Ended:
    """The record rewritten to end as `ending` (its end cause and stop reason), and `ended()` of it."""
    cause, reason = ENDINGS[ending]
    layout = rig.runtime.layout(key)
    write_record(layout, msgspec.structs.replace(record(rig, key), end_cause=cause, stop_reason=reason))
    end = rig.runtime.ended(key)
    assert end is not None and end.generation == 1
    return end


def intent_end(rig: RuntimeRig, key: str) -> Ended:
    raw = msgspec.json.decode(intent_path(rig, key).read_bytes())
    return Ended(raw["generation"], EndCause(raw["end_cause"]), raw["reached_running"], raw["ran_seconds"])


@pytest.mark.parametrize("ending", list(ENDINGS))
@pytest.mark.parametrize("action", ["land", "keep", "discard"])
def test_the_confirmed_intent_carries_the_records_end(rig: RuntimeRig, action: Action, ending: str) -> None:
    key, made = make_unlanded(rig)
    sid = short_id(key)
    end = ended_as(rig, key, ending)
    crash_at(rig, "imported")
    with pytest.raises(Crash):
        rig.runtime.resolve_unlanded("alpha", sid, 1, action, made)
    assert msgspec.json.decode(intent_path(rig, key).read_bytes())["step"] == "confirmed"
    assert intent_end(rig, key) == end
    done = restarted(rig).resolve_unlanded("alpha", sid, 1, action, made)
    assert (done.key, done.end) == (key, end)


@pytest.mark.parametrize("point", RESOLVE_POINTS)
def test_a_replay_takes_the_end_from_the_intent(rig: RuntimeRig, point: str) -> None:
    """Before step 3 the record is still there, and a rewritten end cause is not read again; after it, the
    record is in the trash or gone."""
    key, made = make_unlanded(rig)
    sid = short_id(key)
    end = ended_as(rig, key, "stopped")
    crash_at(rig, point)
    with pytest.raises(Crash):
        rig.runtime.resolve_unlanded("alpha", sid, 1, "land", made)
    layout = rig.runtime.layout(key)
    if os.path.lexists(layout.record):
        write_record(layout, msgspec.structs.replace(record(rig, key), end_cause="died"))
    done = restarted(rig).resolve_unlanded("alpha", sid, 1, "land", made)
    assert end.cause is EndCause.STOPPED and done.end == end


@pytest.mark.parametrize("point", RESOLVE_POINTS)
def test_an_intent_from_before_wpyv5_has_no_end(rig: RuntimeRig, point: str) -> None:
    key, made = make_unlanded(rig)
    sid = short_id(key)
    crash_at(rig, point)
    with pytest.raises(Crash):
        rig.runtime.resolve_unlanded("alpha", sid, 1, "discard", made)
    path = intent_path(rig, key)
    raw = msgspec.json.decode(path.read_bytes())
    for field in ("end_cause", "reached_running", "ran_seconds"):
        raw.pop(field, None)
    path.write_bytes(msgspec.json.encode(raw))
    runtime = restarted(rig)
    done = runtime.resolve_unlanded("alpha", sid, 1, "discard", made)
    assert (done.key, done.end) == (key, None)
    runtime.finish_resolution("alpha", sid, 1)
    assert runtime.sessions("alpha") == [] and not os.path.lexists(path)


def test_an_unknown_cause_in_an_intent_is_a_death(rig: RuntimeRig) -> None:
    """Required by the wpyv5 approval: an intent's end cause the runtime doesn't know is carried as a
    death, as `ended()` maps an unknown record cause."""
    key, made = make_unlanded(rig)
    sid = short_id(key)
    crash_at(rig, "imported")
    with pytest.raises(Crash):
        rig.runtime.resolve_unlanded("alpha", sid, 1, "discard", made)
    path = intent_path(rig, key)
    raw = msgspec.json.decode(path.read_bytes())
    path.write_bytes(msgspec.json.encode({**raw, "end_cause": "healthy", "reached_running": True}))
    done = restarted(rig).resolve_unlanded("alpha", sid, 1, "discard", made)
    assert done.end is not None and (done.end.cause, done.end.reached_running) == (EndCause.DIED, True)


def test_a_manual_land_then_resolve_land_carries_the_end(rig: RuntimeRig) -> None:
    """docs/wsd.md §5, manual steps 2 and 3, then `wsctl resolve --land` with wsd running: the branch is
    already at the tip, so nothing is imported, and the end is carried."""
    key, made = make_unlanded(rig)
    sid, branch = short_id(key), host_tip(rig, "btq/btq-1")
    end = ended_as(rig, key, "stopped")
    host_git(rig, "fetch", "-q", "--no-tags", str(rig.runtime.layout(key).git), "btq/btq-1")
    host_git(rig, "update-ref", "refs/heads/btq/btq-1", "FETCH_HEAD", branch)
    assert host_tip(rig, "btq/btq-1") == made
    objects = host_git(rig, "count-objects", "-v")
    done = rig.runtime.resolve_unlanded("alpha", sid, 1, "land", made)
    rig.runtime.finish_resolution("alpha", sid, 1)
    assert host_git(rig, "count-objects", "-v") == objects and landings(rig) == 0
    assert host_tip(rig, "btq/btq-1") == made and (done.key, done.end) == (key, end)
    assert end.cause is EndCause.STOPPED and rig.runtime.sessions("alpha") == []


# --- plan 4b Task 7: the runtime's signals ---

USAGE = Capabilities(in_session_usage=True)
LIMITS = Capabilities(limit_signal=True)
BOTH = Capabilities(in_session_usage=True, limit_signal=True)
RESET = 1_800_003_600
STATUS = {"five_hour": {"used_percentage": 42, "resets_at": RESET}}
SNAPSHOT = {"limitId": "codex", "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": RESET}}


def drained(rig: RuntimeRig, want: int) -> list[Signal]:
    """Drain until `want` signals have come, then once more to prove no more are queued."""
    got: list[Signal] = []

    def enough() -> bool:
        got.extend(rig.runtime.signals("alpha"))
        return len(got) >= want

    wait_for(enough)
    got.extend(rig.runtime.signals("alpha"))
    return got


def reads(rig: RuntimeRig) -> int:
    return sum(1 for argv in rig.backend.execs if any(a.endswith("codex_usage.py") for a in argv))


def codex_login_inside(rig: RuntimeRig, key: str) -> None:
    """The fake has no login bind: put a fake login where the session's CODEX_HOME is on the host."""
    home = Path(rig.backend.host_env(record(rig, key).sandbox)["CODEX_HOME"])
    (home / "auth.json").write_text("fake-not-a-token")


def test_with_every_capability_off_no_producer_runs(rig: RuntimeRig) -> None:
    rig.backend.env_extra |= {"HZ_FAKE_STATUS_LINE": json.dumps(STATUS),
                              "HZ_FAKE_STOP_FAILURE": "rate_limit",
                              "HZ_FAKE_RATE_LIMITS": json.dumps(SNAPSHOT)}
    one = launch_spec(rig, "p-one")
    two = replace(launch_spec(rig, "p-two", "btq-2"), worktree=bead_worktree(rig, "btq-2"))  # its own branch
    rig.runtime.launch(one)                                     # the self-test's turn ends on StopFailure
    rig.runtime.launch(two)
    settings = json.loads((rig.runtime.layout(one.session_key).run(1) / "claude-settings.json").read_text())
    assert "statusLine" not in settings and "StopFailure" in settings["hooks"]
    assert not (rig.runtime.layout(two.session_key).run(1) / "codex_usage.py").exists()
    rig.clock.advance(rig.runtime.c.settings.usage_poll_seconds)
    assert rig.runtime.signals("alpha") == [] and reads(rig) == 0


def ws_request(rig: RuntimeRig, key: str, *words: str, gen: int = 1) -> tuple[int, dict[str, Any]]:
    """What the agent's `/run/hz/ws-request` does, from the host side of the session's run directory."""
    rc, out = shim.run_request(list(words), {"HZ_SESSION_SOCKET": str(rig.runtime.layout(key).socket(gen))})
    return rc, json.loads(out)


def pulled(rig: RuntimeRig, want: int, since: Mapping[str, tuple[int, int]] | None = None,
           runtime: SandboxRuntime | None = None) -> EventBatch:
    """Read the workstream's events until `want` have come (the hooks run asynchronously)."""
    rt = runtime or rig.runtime
    got = EventBatch()

    def enough() -> bool:
        nonlocal got
        got = rt.session_events("alpha", since or {}, 1000)
        return len(got.events) >= want

    wait_for(enough)
    return got


def briefed(rig: RuntimeRig, ops: frozenset[str] = frozenset(), profile: str = "p-one") -> LaunchSpec:
    rig.runtime.subscribe(lambda _ws: None, {"alpha": ops})
    spec = replace(launch_spec(rig, profile), brief="start")
    rig.runtime.launch(spec)
    return spec


def test_a_stop_is_logged_and_wakes_its_workstream(rig: RuntimeRig) -> None:
    woken: list[str] = []
    rig.runtime.subscribe(woken.append, {"alpha": frozenset({"close"})})
    spec = replace(launch_spec(rig, "p-one"), brief="start")
    rig.runtime.launch(spec)
    rig.tmux.paste(record(rig, spec.session_key).tmux_session, "another turn")
    got = pulled(rig, 2).events
    assert [(e.kind, e.turn, e.text) for e in got] == [("stop", 1, "echo: start"),
                                                       ("stop", 2, "echo: another turn")]
    assert got[0].event_id == f"{spec.session_key}:g1:e1" and (got[0].ws, got[0].bead) == ("alpha", "btq-1")
    wait_for(lambda: woken == ["alpha", "alpha"])       # each wake follows its event's append
    assert not any(isinstance(s, SessionEvent) for s in rig.runtime.signals("alpha"))   # never queued


def test_the_probe_turn_is_never_an_event(rig: RuntimeRig) -> None:
    briefed(rig)
    assert [e.text for e in pulled(rig, 1).events] == ["echo: start"]


def test_a_launch_without_a_brief_forwards_from_the_probe_turns_end(rig: RuntimeRig) -> None:
    # The lead's ruling: forwarding starts once the probe turn has ended, brief or none.
    rig.runtime.subscribe(lambda _ws: None, {"alpha": frozenset()})
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    assert rig.runtime.logs[spec.session_key].forwarding
    rig.tmux.paste(record(rig, spec.session_key).tmux_session, "an operator's turn")
    got = pulled(rig, 1).events
    assert [(e.kind, e.turn, e.text) for e in got] == [("stop", 1, "echo: an operator's turn")]


def test_a_close_request_is_logged_before_it_is_accepted(rig: RuntimeRig) -> None:
    spec = briefed(rig, frozenset({"close"}))
    evidence = rig.runtime.layout(spec.session_key).run(1) / "ev.txt"     # the fake's paths are host paths
    evidence.write_text("12 passed")
    rc, reply = ws_request(rig, spec.session_key, "close", "--evidence-file", str(evidence))
    assert rc == 0 and reply["ref"] == f"{spec.session_key}:g1:e2"
    log = rig.runtime.layout(spec.session_key).session_events(1)
    assert json.loads(log.read_text().splitlines()[-1])["kind"] == "close"     # durable before the reply
    [close] = [e for e in pulled(rig, 2).events if e.kind == "close"]
    assert close.text == "12 passed"


def test_ops_are_enabled_per_workstream(rig: RuntimeRig) -> None:
    rig.runtime.subscribe(lambda _ws: None, {"alpha": frozenset({"close"}),
                                             "beta": frozenset({"question"})})     # D6: not alpha's
    spec = replace(launch_spec(rig, "p-one"), brief="start")
    rig.runtime.launch(spec)
    assert ws_request(rig, spec.session_key, "question", '{"text": "hm"}') == (
        1, {"ok": False, "error": "unsupported"})
    evidence = rig.runtime.layout(spec.session_key).run(1) / "ev.txt"
    evidence.write_text("12 passed")
    rc, reply = ws_request(rig, spec.session_key, "close", "--evidence-file", str(evidence))
    assert rc == 0 and reply["ref"] == f"{spec.session_key}:g1:e2"     # e1 is the brief's Stop


def test_a_stopped_generations_log_refuses_a_late_request(rig: RuntimeRig) -> None:
    """Codex T2 c2: the server's close waits only so long for a callback; a callback that outlasts it
    reaches a log the teardown has closed, and is refused at commit, never logged after the end."""
    rig.runtime.subscribe(lambda _ws: None, {"alpha": frozenset({"close"})})
    spec = replace(launch_spec(rig, "p-one"), brief="start")
    rig.runtime.launch(spec)
    late = rig.runtime.logs[spec.session_key]
    rig.runtime.stop(spec.session_key)
    assert late.request({"op": "close", "rid": "0123456789abcdef", "evidence": "x"},
                        frozenset({"close"})) == (None, {"ok": False, "error": "unavailable"})
    assert [json.loads(line)["kind"] for line in late.path.read_text().splitlines()] == ["stop"]


def test_a_request_in_flight_across_the_teardown_is_refused_at_commit(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex T3 c2 NB: a request's callback is held past the server's bounded wait, the session ends and
    its log is closed, then the callback commits: it is refused, and nothing is logged after the end."""
    monkeypatch.setattr(session_server, "CLOSE_SECONDS", 0.1)
    rig.runtime.subscribe(lambda _ws: None, {"alpha": frozenset({"close"})})
    spec = replace(launch_spec(rig, "p-one"), brief="start")
    rig.runtime.launch(spec)
    layout = rig.runtime.layout(spec.session_key)
    late = rig.runtime.logs[spec.session_key]
    real = late.request
    entered, release = threading.Event(), threading.Event()
    got: list[tuple[SessionEvent | None, dict[str, Any]]] = []

    def held(payload: Mapping[str, Any], ops: frozenset[str]) -> tuple[SessionEvent | None, dict[str, Any]]:
        entered.set()
        release.wait(10)
        got.append(real(payload, ops))
        return got[-1]

    monkeypatch.setattr(late, "request", held)
    token = (layout.run(1) / shim.TOKEN_FILE).read_text().strip()
    client = socket.socket(socket.AF_UNIX)
    stopper = threading.Thread(target=rig.runtime.stop, args=(spec.session_key,))
    try:
        client.connect(str(layout.socket(1)))
        client.sendall((json.dumps({"token": token, "type": "ws_request", "payload": {
            "op": "close", "rid": "0123456789abcdef", "evidence": "x"}}) + "\n").encode())
        assert entered.wait(5)                          # the callback is in flight
        stopper.start()
        wait_for(lambda: not late.writable)             # the teardown has closed the log
    finally:
        release.set()
        stopper.join(30)
        client.close()
    wait_for(lambda: bool(got))
    assert got == [(None, {"ok": False, "error": "unavailable"})]
    assert [json.loads(line)["kind"] for line in late.path.read_text().splitlines()] == ["stop"]
    assert not stopper.is_alive()


def test_a_cursor_reads_only_what_follows_it_and_the_limit_pages(rig: RuntimeRig) -> None:
    spec = briefed(rig)
    pane = record(rig, spec.session_key).tmux_session
    pulled(rig, 1)
    rig.tmux.paste(pane, "two")
    pulled(rig, 2)
    rig.tmux.paste(pane, "three")
    pulled(rig, 3)
    page = rig.runtime.session_events("alpha", {spec.session_key: (1, 1)}, 1)
    assert [e.seq for e in page.events] == [2] and page.more and page.ends == {}


def test_a_restarted_runtime_reads_the_same_events(rig: RuntimeRig) -> None:
    briefed(rig)
    first = pulled(rig, 1).events
    assert pulled(rig, 1, runtime=restarted(rig)).events == first       # read from disk: no rebind needed


def test_every_unread_generation_is_read_after_the_next_launch(rig: RuntimeRig) -> None:
    # Codex cycle 1 #3: generation 1's last event, never read before generation 2 started, still comes.
    rig.runtime.subscribe(lambda _ws: None, {})
    spec = replace(launch_spec(rig, "p-one"), brief="start")
    first = rig.runtime.launch(spec)
    pulled(rig, 1)
    rig.runtime.stop(spec.session_key)
    second = replace(spec, generation=2, resume=True, native_id=first.native_id)
    rig.runtime.launch(second)
    batch = pulled(rig, 2)
    assert [e.event_id for e in batch.events] == [f"{spec.session_key}:g1:e1", f"{spec.session_key}:g2:e1"]
    assert batch.ends == {(spec.session_key, 1): 1}                     # g1 is final; g2 runs on
    later = pulled(rig, 1, {spec.session_key: (2, 0)}, runtime=restarted(rig))
    assert [e.event_id for e in later.events] == [f"{spec.session_key}:g2:e1"]


def test_an_ended_generation_is_final(rig: RuntimeRig) -> None:
    spec = briefed(rig)
    pulled(rig, 1)
    rig.runtime.stop(spec.session_key)
    assert rig.runtime.session_events("alpha", {}, 10).ends == {(spec.session_key, 1): 1}
    assert spec.session_key not in rig.runtime.logs                     # popped once its socket closed


def test_an_unreadable_session_folder_is_unavailable(rig: RuntimeRig,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_self: SandboxRuntime) -> list[SessionRecord]:
        raise RuntimeUnavailable("the session records can't be listed")

    monkeypatch.setattr(SandboxRuntime, "_records", broken)
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.session_events("alpha", {}, 10)


def test_a_claude_status_line_is_usage_from_its_own_session(rig: RuntimeRig) -> None:
    with_caps(rig, "claude-code", USAGE)
    rig.backend.env_extra["HZ_FAKE_STATUS_LINE"] = json.dumps(STATUS)
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    pane = record(rig, spec.session_key).tmux_session
    rig.tmux.paste(pane, "another turn")
    # The fake echoes only after its status line has run: the unchanged limits were not sent again.
    wait_for(lambda: "echo: another turn" in rig.tmux.capture(pane, 50))
    assert drained(rig, 1) == [UsageSeen(spec.session_key, 1,
                                         (Observation("claude:5h", "5h", 42, RESET, STATUS_SOURCE),))]


def test_a_claude_rate_limit_is_a_limit_and_ends_the_turn(rig: RuntimeRig) -> None:
    with_caps(rig, "claude-code", LIMITS)
    rig.backend.env_extra["HZ_FAKE_STOP_FAILURE"] = "rate_limit"
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)                                    # the self-test waits for a turn end
    assert rig.runtime.servers[spec.session_key].turns().idle
    assert drained(rig, 1) == [LimitSeen(spec.session_key, 1, None)]


@pytest.mark.parametrize(("caps", "error"), [(USAGE, "rate_limit"), (LIMITS, "overloaded")])
def test_a_stop_failure_is_a_limit_only_with_e_and_rate_limit(rig: RuntimeRig, caps: Capabilities,
                                                              error: str) -> None:
    with_caps(rig, "claude-code", caps)
    rig.backend.env_extra["HZ_FAKE_STOP_FAILURE"] = error
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.tmux.paste(record(rig, spec.session_key).tmux_session, "again")
    wait_for(lambda: rig.runtime.servers[spec.session_key].turns().stops >= 2)
    assert rig.runtime.signals("alpha") == []


def test_the_codex_read_is_polled_when_due(rig: RuntimeRig) -> None:
    with_caps(rig, "codex", BOTH)
    rig.backend.env_extra["HZ_FAKE_RATE_LIMITS"] = json.dumps(SNAPSHOT)
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    codex_login_inside(rig, spec.session_key)
    window = Observation("codex:codex:300m", "5h", 100, RESET, IN_SESSION_SOURCE)
    assert rig.runtime.signals("alpha") == [UsageSeen(spec.session_key, 1, (window,)),
                                            LimitSeen(spec.session_key, 1, RESET)]
    assert rig.runtime.signals("alpha") == [] and reads(rig) == 1      # not due again yet
    rig.clock.advance(rig.runtime.c.settings.usage_poll_seconds)
    assert len(rig.runtime.signals("alpha")) == 2 and reads(rig) == 2
    assert rig.runtime.signals("beta") == []                           # another workstream's: none


def test_draining_signals_reads_no_login(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only `_pinned` reads a login, under its lock: the capability lookup behind each due read
    reads none."""
    with_caps(rig, "codex", BOTH)
    rig.backend.env_extra["HZ_FAKE_RATE_LIMITS"] = json.dumps(SNAPSHOT)
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    codex_login_inside(rig, spec.session_key)
    logins: list[str] = []

    def reading(self: ConfiguredAccounts, adapter: str, account: str) -> str:
        logins.append(account)
        raise AssertionError("a login was read")

    monkeypatch.setattr(ConfiguredAccounts, "current_key", reading)
    assert len(rig.runtime.signals("alpha")) == 2 and reads(rig) == 1
    rig.clock.advance(rig.runtime.c.settings.usage_poll_seconds)
    assert len(rig.runtime.signals("alpha")) == 2 and reads(rig) == 2
    assert logins == []


@pytest.mark.parametrize(("caps", "kinds"), [(LIMITS, [LimitSeen]), (USAGE, [UsageSeen])])
def test_the_codex_read_serves_each_capability_alone(rig: RuntimeRig, caps: Capabilities,
                                                     kinds: list[type]) -> None:
    """(e) without (c) still installs and polls the read; each signal comes only under its own one."""
    with_caps(rig, "codex", caps)
    rig.backend.env_extra["HZ_FAKE_RATE_LIMITS"] = json.dumps(SNAPSHOT)
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    assert (rig.runtime.layout(spec.session_key).run(1) / "codex_usage.py").exists()
    codex_login_inside(rig, spec.session_key)
    assert [type(sig) for sig in rig.runtime.signals("alpha")] == kinds and reads(rig) == 1


def read_failures(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(DIAG_USAGE_READ)]


def pid_gone(pid_file: Path) -> bool:
    try:
        os.kill(int(pid_file.read_text()), 0)
    except ProcessLookupError:
        return True
    return False


def failing_read(rig: RuntimeRig, fault: str | None, login: bool = True) -> tuple[LaunchSpec, Path]:
    """A Codex session with (c) and (e) on, whose read will meet `fault`. The PID file is emptied after
    the launch, whose own app-server writes it too, so only the read's app-server can fill it."""
    with_caps(rig, "codex", BOTH)
    pid_file = rig.root / "read.pid"
    rig.backend.env_extra |= {"HZ_FAKE_RATE_LIMITS": json.dumps(SNAPSHOT), "HZ_FAKE_PID_FILE": str(pid_file)}
    if fault is not None:
        rig.backend.env_extra["HZ_FAKE_RATE_FAULT"] = fault
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    if login:
        codex_login_inside(rig, spec.session_key)
    pid_file.unlink(missing_ok=True)
    return spec, pid_file


@pytest.mark.parametrize(("fault", "login", "reason"), [
    ("error", True, "AnswerError"), ("garbage", True, "JSONDecodeError"), ("close", True, "ClosedOutput"),
    ("exit", True, "ExitStatus"), (None, False, "AnswerError")])
def test_a_read_that_fails_reports_nothing_and_names_why(rig: RuntimeRig, caplog: pytest.LogCaptureFixture,
                                                         fault: str | None, login: bool, reason: str) -> None:
    caplog.set_level(logging.WARNING, logger=runtime_mod.__name__)
    _, pid_file = failing_read(rig, fault, login)
    assert rig.runtime.signals("alpha") == []
    assert reads(rig) == 1                                      # it ran and failed: not skipped
    assert read_failures(caplog) == [f"{DIAG_USAGE_READ}: {reason}"]
    assert pid_file.is_file() and pid_gone(pid_file)           # its app-server started, and was reaped


def test_a_read_past_its_bound_is_a_failure_and_its_server_ends(rig: RuntimeRig,
                                                                caplog: pytest.LogCaptureFixture,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.WARNING, logger=runtime_mod.__name__)
    monkeypatch.setattr(runtime_mod, "USAGE_SECONDS", 2)
    _, pid_file = failing_read(rig, "hang")
    assert rig.runtime.signals("alpha") == []
    assert read_failures(caplog) == [f"{DIAG_USAGE_READ}: BackendError"]
    assert pid_file.is_file()                                   # the read's own server started
    wait_for(lambda: pid_gone(pid_file))       # the script was killed: its server reads EOF and exits


def test_a_read_whose_script_is_missing_is_its_exit_status(rig: RuntimeRig,
                                                           caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=runtime_mod.__name__)
    spec, pid_file = failing_read(rig, None)
    (rig.runtime.layout(spec.session_key).run(1) / "codex_usage.py").unlink()
    assert rig.runtime.signals("alpha") == []
    assert read_failures(caplog) == [f"{DIAG_USAGE_READ}: exit 2"]      # python3 can't open the file
    assert not pid_file.exists()


@pytest.mark.parametrize(("status", "reason"), [(127, "exit 127"), (1, "exit 1"), (0, "unparsed")])
def test_only_a_clean_exit_with_a_reading_is_a_reading(rig: RuntimeRig, caplog: pytest.LogCaptureFixture,
                                                       monkeypatch: pytest.MonkeyPatch, status: int,
                                                       reason: str) -> None:
    """127 is a command that never ran, and its output is no reading, even one that parses."""
    caplog.set_level(logging.WARNING, logger=runtime_mod.__name__)
    failing_read(rig, None)
    out = json.dumps(SNAPSHOT).encode() if status else b"not a reading"

    def answered(name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
                 timeout: float) -> ExecResult:
        return ExecResult(status, out, b"")

    monkeypatch.setattr(rig.backend, "exec", answered)
    assert rig.runtime.signals("alpha") == []
    assert read_failures(caplog) == [f"{DIAG_USAGE_READ}: {reason}"]


def test_a_rebound_session_still_reports(rig: RuntimeRig) -> None:
    with_caps(rig, "claude-code", LIMITS)
    rig.backend.env_extra["HZ_FAKE_STOP_FAILURE"] = "rate_limit"
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    assert drained(rig, 1) == [LimitSeen(spec.session_key, 1, None)]
    rig.runtime.servers.pop(spec.session_key).close()          # wsd died
    rig.runtime.sessions("alpha")                              # rebinds
    rig.tmux.paste(record(rig, spec.session_key).tmux_session, "after the restart")
    assert drained(rig, 1) == [LimitSeen(spec.session_key, 1, None)]


def test_a_flood_of_reports_keeps_only_the_newest(rig: RuntimeRig) -> None:
    with_caps(rig, "claude-code", LIMITS)
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    on_event = rig.runtime.servers[spec.session_key].on_event
    assert on_event is not None
    for _ in range(MAX_QUEUED + 10):
        on_event({"hook_event_name": "StopFailure", "error": "rate_limit"})
    assert len(rig.runtime.signals("alpha")) == MAX_QUEUED


def test_signals_never_raise(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    with_caps(rig, "claude-code", LIMITS)
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    on_event = rig.runtime.servers[spec.session_key].on_event
    assert on_event is not None
    on_event({"hook_event_name": "StopFailure", "error": "rate_limit"})

    def unlistable() -> list[SessionRecord]:
        raise RuntimeUnavailable("the session records can't be listed")

    monkeypatch.setattr(rig.runtime, "_records", unlistable)
    assert rig.runtime.signals("alpha") == [LimitSeen(spec.session_key, 1, None)]


# --- plan 5i T5: steer ---------------------------------------------------------------------------
ADAPTERS = pytest.mark.parametrize("profile", ["p-one", "p-two"])


def idle_session(rig: RuntimeRig, profile: str) -> tuple[LaunchSpec, str]:
    """A launched session at its first turn boundary: the operator's prompt has run and its turn ended."""
    spec = launch_spec(rig, profile)
    rig.runtime.launch(spec)
    pane = record(rig, spec.session_key).tmux_session
    server = rig.runtime.servers[spec.session_key]
    stops = server.turns().stops                # the self-test's turn has ended: wait for this one's end
    rig.tmux.paste(pane, "start")
    wait_for(lambda: server.turns().stops == stops + 1 and server.turns().idle)
    return spec, pane


@ADAPTERS
def test_steer_pastes_into_an_idle_session(rig: RuntimeRig, profile: str) -> None:
    spec, pane = idle_session(rig, profile)
    rig.runtime.steer(spec.session_key, 1, "use the blue one")
    wait_for(lambda: "echo: use the blue one" in rig.tmux.capture(pane, 50))


@ADAPTERS
@pytest.mark.parametrize(("generation", "text", "why"), [
    (2, "x", "generation"),
    (1, "x" * (runtime_mod.STEER_MAX + 1), "too long"),
    (1, "é" * (runtime_mod.STEER_MAX // 2 + 1), "too long"),         # counted in UTF-8 bytes
])
def test_steer_refuses_the_wrong_generation_and_long_text(rig: RuntimeRig, profile: str, generation: int,
                                                          text: str, why: str) -> None:
    spec, pane = idle_session(rig, profile)
    with pytest.raises(SteerRefused, match=why):
        rig.runtime.steer(spec.session_key, generation, text)
    assert "echo: x" not in rig.tmux.capture(pane, 50)


@ADAPTERS
def test_steer_refuses_a_second_steer_until_the_first_turn_ends(rig: RuntimeRig, profile: str,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    spec, pane = idle_session(rig, profile)
    server = rig.runtime.servers[spec.session_key]
    stops = server.turns().stops
    pasted: list[str] = []
    monkeypatch.setattr(rig.runtime.c.tmux, "paste", lambda _name, text, **_: pasted.append(text))
    rig.runtime.steer(spec.session_key, 1, "first")         # delivered; its turn hasn't started yet
    with pytest.raises(SteerRefused, match="mid-turn"):     # the boundary it pasted at is spent
        rig.runtime.steer(spec.session_key, 1, "second")
    assert pasted == ["first"]
    monkeypatch.undo()
    rig.tmux.paste(pane, "next")                            # a turn ends: a fresh boundary
    wait_for(lambda: server.turns().stops == stops + 1 and server.turns().idle)
    rig.runtime.steer(spec.session_key, 1, "third")
    wait_for(lambda: "echo: third" in rig.tmux.capture(pane, 50))


@ADAPTERS
def test_steer_refuses_after_a_rebind_until_a_turn_ends(rig: RuntimeRig, profile: str) -> None:
    spec, pane = idle_session(rig, profile)
    rig.runtime.servers.pop(spec.session_key).close()       # wsd died: the socket went with it
    rig.runtime.sessions("alpha")                           # rebinds it, with no turn end seen yet
    server = rig.runtime.servers[spec.session_key]
    with pytest.raises(SteerRefused, match="mid-turn"):
        rig.runtime.steer(spec.session_key, 1, "hello")
    rig.tmux.paste(pane, "one more turn")       # the operator types; its Stop reaches the new socket
    wait_for(lambda: server.turns().stops == 1 and server.turns().idle)
    rig.runtime.steer(spec.session_key, 1, "now steer")
    wait_for(lambda: "echo: now steer" in rig.tmux.capture(pane, 50))


def test_an_unknown_session_is_refused(rig: RuntimeRig) -> None:
    with pytest.raises(SteerRefused, match="not running"):
        rig.runtime.steer("sk-none", 1, "hello")


def test_an_ended_session_is_refused(rig: RuntimeRig) -> None:
    spec, _pane = idle_session(rig, "p-one")
    rig.runtime.stop(spec.session_key)
    with pytest.raises(SteerRefused, match="not running"):
        rig.runtime.steer(spec.session_key, 1, "hello")


def test_an_unreadable_record_is_refused(rig: RuntimeRig) -> None:
    spec, _pane = idle_session(rig, "p-one")
    rig.runtime.layout(spec.session_key).record.write_text("{not json")
    with pytest.raises(SteerRefused, match="record"):
        rig.runtime.steer(spec.session_key, 1, "hello")


@ADAPTERS
def test_an_uncertain_paste_is_uncertain(rig: RuntimeRig, profile: str,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _pane = idle_session(rig, profile)

    def uncertain(_name: str, _text: str, **_: object) -> None:
        raise TmuxPasteUncertain("maybe")

    monkeypatch.setattr(rig.runtime.c.tmux, "paste", uncertain)
    with pytest.raises(SteerUncertain):
        rig.runtime.steer(spec.session_key, 1, "hello")
    with pytest.raises(SteerRefused, match="uncertain"):    # it may have landed: nothing is pasted again
        rig.runtime.steer(spec.session_key, 1, "again")


@ADAPTERS
def test_after_an_uncertain_steer_the_generation_is_never_pasted_into_again(
        rig: RuntimeRig, profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # T5 c3 B1: tmux can't cancel a command a timed-out client sent, so a paste may land, or its Enter
    # arrive, after the gate is gone. Nothing more is pasted into that generation: no later Enter can
    # submit what a late paste left in the input line, and the note is never delivered twice.
    spec, pane = idle_session(rig, profile)
    server = rig.runtime.servers[spec.session_key]

    def uncertain(_name: str, _text: str, **_: object) -> None:
        raise TmuxPasteUncertain("maybe")

    with monkeypatch.context() as m:
        m.setattr(rig.runtime.c.tmux, "paste", uncertain)
        with pytest.raises(SteerUncertain):
            rig.runtime.steer(spec.session_key, 1, "hello")
    stops = server.turns().stops
    rig.tmux.paste(pane, "one more turn")                   # a boundary a steer could otherwise use
    wait_for(lambda: server.turns().stops == stops + 1 and server.turns().idle)
    before, spent = server.turns(), getattr(server, "_spent")  # noqa: B009 - a private seam
    pasted: list[str] = []
    with monkeypatch.context() as m:
        m.setattr(rig.runtime.c.tmux, "paste", lambda _name, t, **_: pasted.append(t))
        with pytest.raises(SteerRefused, match="an earlier steer to this session is uncertain"):
            rig.runtime.steer(spec.session_key, 1, "again")
    assert pasted == []                                     # nothing pasted, and nothing spent or consumed
    assert getattr(server, "_spent") == spent and server.turns() == before  # noqa: B009
    first = record(rig, spec.session_key)
    rig.runtime.stop(spec.session_key)
    rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    pane = record(rig, spec.session_key).tmux_session
    server = rig.runtime.servers[spec.session_key]
    stops = server.turns().stops
    rig.tmux.paste(pane, "start")
    wait_for(lambda: server.turns().stops == stops + 1 and server.turns().idle)
    rig.runtime.steer(spec.session_key, 2, "a fresh generation")    # its own input line: steerable
    wait_for(lambda: "echo: a fresh generation" in rig.tmux.capture(pane, 50))


def test_a_paste_that_failed_before_the_pane_is_refused(rig: RuntimeRig,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _pane = idle_session(rig, "p-one")
    calls: list[str] = []

    def failed(_name: str, text: str, **_: object) -> None:
        calls.append(text)
        if len(calls) == 1:
            raise TmuxError("tmux paste failed (OSError)")

    monkeypatch.setattr(rig.runtime.c.tmux, "paste", failed)
    with pytest.raises(SteerRefused, match="paste failed"):
        rig.runtime.steer(spec.session_key, 1, "hello")
    rig.runtime.steer(spec.session_key, 1, "hello")     # nothing reached the pane: the boundary holds
    assert calls == ["hello", "hello"]


def hook(server: SessionServer, event: str) -> None:
    """Send a hook event to the session's socket, as the session's shim does."""
    line = json.dumps({"token": server.token.decode(), "type": "hook_event",
                       "payload": {"hook_event_name": event}}) + "\n"
    with socket.socket(socket.AF_UNIX) as c:
        c.settimeout(10)
        c.connect(str(server.path))
        c.sendall(line.encode())
        c.makefile("rb").readline()


class BlockedPaste:
    """A tmux paste that blocks until released, so a test can act while a steer is delivering."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.pasted: list[str] = []

    def __call__(self, _name: str, text: str, **_: object) -> None:
        self.pasted.append(text)
        self.entered.set()
        assert self.release.wait(10)


@ADAPTERS
def test_steer_refuses_a_running_turn(rig: RuntimeRig, profile: str, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    hold = tmp_path / "hold"
    rig.backend.env_extra["HZ_FAKE_HOLD_FILE"] = str(hold)
    spec, pane = idle_session(rig, profile)
    server = rig.runtime.servers[spec.session_key]
    before = server.turns()
    hold.touch()                                            # the next turn runs until the file goes
    rig.tmux.paste(pane, "work")
    wait_for(lambda: server.turns().prompts == before.prompts + 1)
    assert not server.turns().idle
    pasted: list[str] = []
    with monkeypatch.context() as m:
        m.setattr(rig.runtime.c.tmux, "paste", lambda _name, text, **_: pasted.append(text))
        with pytest.raises(SteerRefused, match="mid-turn"):
            rig.runtime.steer(spec.session_key, 1, "hello")
    assert pasted == []
    hold.unlink()
    wait_for(lambda: server.turns().idle)
    rig.runtime.steer(spec.session_key, 1, "after the turn")
    wait_for(lambda: "echo: after the turn" in rig.tmux.capture(pane, 50))


@ADAPTERS
def test_concurrent_steers_share_no_boundary(rig: RuntimeRig, profile: str,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _pane = idle_session(rig, profile)
    paste = BlockedPaste()
    monkeypatch.setattr(rig.runtime.c.tmux, "paste", paste)
    first = threading.Thread(target=rig.runtime.steer, args=(spec.session_key, 1, "first"))
    first.start()
    try:
        assert paste.entered.wait(10)                       # the first steer is delivering
        with pytest.raises(SteerRefused, match="mid-turn"):
            rig.runtime.steer(spec.session_key, 1, "second")
    finally:
        paste.release.set()
        first.join(10)
    assert not first.is_alive()
    assert paste.pasted == ["first"]
    with pytest.raises(SteerRefused, match="mid-turn"):     # and the boundary it used stays spent
        rig.runtime.steer(spec.session_key, 1, "third")


def test_a_turn_event_waits_for_a_delivering_steer(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _pane = idle_session(rig, "p-one")
    server = rig.runtime.servers[spec.session_key]
    before = server.turns()
    paste = BlockedPaste()
    monkeypatch.setattr(rig.runtime.c.tmux, "paste", paste)
    gate = WatchedGate(server)
    monkeypatch.setattr(server, "_turn_gate", gate)
    steer = threading.Thread(target=rig.runtime.steer, args=(spec.session_key, 1, "first"))
    steer.start()
    prompt = threading.Thread(target=hook, args=(server, "UserPromptSubmit"))
    try:
        assert paste.entered.wait(10)
        prompt.start()
        assert gate.waiting.wait(10)                        # the prompt's handler is at the gate
        assert prompt.is_alive()                            # held until the delivery is over
        assert server.turns() == before
    finally:
        paste.release.set()
        steer.join(10)
        if prompt.ident is not None:                        # started
            prompt.join(10)
    assert not steer.is_alive() and not prompt.is_alive()
    assert server.turns().prompts == before.prompts + 1 and not server.turns().idle


@pytest.mark.parametrize("text", [
    "\x1b[201~\rsecond\r", "a\rb", "\x1b[2J", "\x07", "\x9b31m", "\x7f", "a\x00b"])
def test_steer_refuses_terminal_controls(rig: RuntimeRig, text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _pane = idle_session(rig, "p-one")
    pasted: list[str] = []
    monkeypatch.setattr(rig.runtime.c.tmux, "paste", lambda _name, t, **_: pasted.append(t))
    with pytest.raises(SteerRefused, match="terminal control"):
        rig.runtime.steer(spec.session_key, 1, text)
    assert pasted == []
    rig.runtime.steer(spec.session_key, 1, "plain\ttext")  # the boundary wasn't spent
    assert pasted == ["plain\ttext"]


def test_steer_refuses_text_that_is_not_utf8(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _pane = idle_session(rig, "p-one")
    pasted: list[str] = []
    monkeypatch.setattr(rig.runtime.c.tmux, "paste", lambda _name, t, **_: pasted.append(t))
    with pytest.raises(SteerRefused, match="not valid UTF-8"):
        rig.runtime.steer(spec.session_key, 1, "lone \ud800 surrogate")
    assert pasted == []


@ADAPTERS
def test_a_multi_line_steer_is_one_prompt(rig: RuntimeRig, profile: str) -> None:
    rig.backend.env_extra["HZ_FAKE_BRACKETED"] = "1"
    spec, _pane = idle_session(rig, profile)
    server = rig.runtime.servers[spec.session_key]
    stops = server.turns().stops
    rig.runtime.steer(spec.session_key, 1, "line one\n\tline two\nline three")
    wait_for(lambda: server.turns().stops == stops + 1)
    events = rig.runtime.layout(spec.session_key).events(1)
    prompts = [row["payload"].get("prompt") for row in map(json.loads, events.read_text().splitlines())
               if row["payload"].get("hook_event_name") == "UserPromptSubmit"]
    assert prompts[-1] == "line one\n\tline two\nline three"
    assert server.turns().prompts == len(prompts)


@ADAPTERS
def test_a_rebind_after_a_spent_boundary_steers_after_a_turn_end(rig: RuntimeRig, profile: str,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    spec, pane = idle_session(rig, profile)
    pasted: list[str] = []
    with monkeypatch.context() as m:
        m.setattr(rig.runtime.c.tmux, "paste", lambda _name, t, **_: pasted.append(t))
        rig.runtime.steer(spec.session_key, 1, "spent")     # pasted nowhere: the boundary is spent
    rig.runtime.servers.pop(spec.session_key).close()
    rig.runtime.sessions("alpha")                           # rebinds it
    server = rig.runtime.servers[spec.session_key]
    with pytest.raises(SteerRefused, match="mid-turn"):
        rig.runtime.steer(spec.session_key, 1, "too soon")
    rig.tmux.paste(pane, "one more turn")
    wait_for(lambda: server.turns().stops == 1 and server.turns().idle)
    rig.runtime.steer(spec.session_key, 1, "now steer")
    wait_for(lambda: "echo: now steer" in rig.tmux.capture(pane, 50))
    assert pasted == ["spent"]


def hook_wait(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """The daemon's hook wait from now on (what a launch installs in the generation's shim.json)."""
    c = rig.runtime.c
    monkeypatch.setattr(rig.runtime, "c", replace(c, settings=replace(c.settings, hook_wait_seconds=seconds)))


@ADAPTERS
def test_a_rebound_steer_is_bounded_by_the_generations_installed_hook_wait(
        rig: RuntimeRig, profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # T5 c3 B2: a rebind keeps the shim.json the generation was launched with, so its shim gives up after
    # that wait, not after the one wsd's settings say since a restart.
    hook_wait(rig, monkeypatch, 2.0)
    spec, pane = idle_session(rig, profile)
    hook_wait(rig, monkeypatch, 10.0)                       # wsd restarts with a longer wait
    rig.runtime.servers.pop(spec.session_key).close()
    rig.runtime.sessions("alpha")                           # rebinds it, its shim still waiting 2s
    server = rig.runtime.servers[spec.session_key]
    rig.tmux.paste(pane, "one more turn")
    wait_for(lambda: server.turns().stops == 1 and server.turns().idle)
    bounds: list[float | None] = []
    monkeypatch.setattr(rig.runtime.c.tmux, "paste",
                        lambda _name, _t, *, seconds=None: bounds.append(seconds))
    rig.runtime.steer(spec.session_key, 1, "bounded")
    assert bounds == [1.0]                                  # half the installed 2s, not min(5, 10 / 2)


@ADAPTERS
def test_a_steer_is_refused_when_the_installed_hook_wait_is_too_short_or_unreadable(
        rig: RuntimeRig, profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _pane = idle_session(rig, profile)
    config = rig.runtime.servers[spec.session_key].path.parent / shim.CONFIG_FILE
    installed = json.loads(config.read_text())
    pasted: list[str] = []
    monkeypatch.setattr(rig.runtime.c.tmux, "paste", lambda _name, t, **_: pasted.append(t))
    config.write_text(json.dumps({**installed, "wait_seconds": 1.5}))
    with pytest.raises(SteerRefused, match="the hook wait is too short to steer"):
        rig.runtime.steer(spec.session_key, 1, "short")
    config.write_text("{")
    with pytest.raises(SteerRefused, match="the session's hook wait can't be read"):
        rig.runtime.steer(spec.session_key, 1, "unreadable")
    assert pasted == []


@ADAPTERS
def test_a_new_generation_after_a_spent_boundary_can_be_steered(rig: RuntimeRig, profile: str,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _pane = idle_session(rig, profile)
    with monkeypatch.context() as m:
        m.setattr(rig.runtime.c.tmux, "paste", lambda _name, _t, **_: None)
        rig.runtime.steer(spec.session_key, 1, "spent")
    first = record(rig, spec.session_key)
    rig.runtime.stop(spec.session_key)
    rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    pane = record(rig, spec.session_key).tmux_session
    server = rig.runtime.servers[spec.session_key]
    stops = server.turns().stops
    rig.tmux.paste(pane, "start")
    wait_for(lambda: server.turns().stops == stops + 1 and server.turns().idle)
    rig.runtime.steer(spec.session_key, 2, "second generation")
    wait_for(lambda: "echo: second generation" in rig.tmux.capture(pane, 50))


# --- plan 5i T4: the brief -----------------------------------------------------------------------
def test_the_brief_is_pasted_after_the_probe_turn_and_confirmed(rig: RuntimeRig) -> None:
    spec = replace(launch_spec(rig, "p-one"), brief="Your task is btq-1.")
    rig.runtime.launch(spec)
    pane = record(rig, spec.session_key).tmux_session
    assert rig.runtime.servers[spec.session_key].turns().prompts == 2       # the probe, then the brief
    wait_for(lambda: "echo: Your task is btq-1." in rig.tmux.capture(pane, 50))


def paste_after_probe(tmux: Any, after: Callable[[Callable[[str, str], None], str, str], None]
                      ) -> Callable[[str, str], None]:
    """A paste that delivers the self-test's probe (the first paste) and hands each later one to `after`,
    with the real paste."""
    real, pasted = tmux.paste, []

    def paste(name: str, text: str) -> None:
        pasted.append(text)
        if len(pasted) == 1:
            real(name, text)
        else:
            after(real, name, text)

    return paste


def test_a_brief_the_agent_never_takes_fails_the_launch(rig: RuntimeRig,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_mod, "BRIEF_SECONDS", 0.5)
    monkeypatch.setattr(rig.runtime.c.tmux, "paste",
                        paste_after_probe(rig.runtime.c.tmux, lambda _real, _name, _text: None))
    with pytest.raises(LaunchFailed, match="did not take its brief"):
        rig.runtime.launch(replace(launch_spec(rig, "p-one"), brief="hello"))


def test_a_brief_that_cannot_be_pasted_fails_the_launch(rig: RuntimeRig,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(_real: Callable[[str, str], None], _name: str, _text: str) -> None:
        raise TmuxError("tmux paste failed (OSError)")

    monkeypatch.setattr(rig.runtime.c.tmux, "paste", paste_after_probe(rig.runtime.c.tmux, failed))
    with pytest.raises(LaunchFailed, match="brief could not be pasted"):
        rig.runtime.launch(replace(launch_spec(rig, "p-one"), brief="hello"))


def test_an_uncertain_brief_paste_is_settled_by_the_prompt_count(rig: RuntimeRig,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    def uncertain(real: Callable[[str, str], None], name: str, text: str) -> None:
        real(name, text)
        raise TmuxPasteUncertain("maybe")            # it landed; the prompt count says so

    monkeypatch.setattr(rig.runtime.c.tmux, "paste", paste_after_probe(rig.runtime.c.tmux, uncertain))
    spec = replace(launch_spec(rig, "p-one"), brief="hello")
    rig.runtime.launch(spec)
    assert rig.runtime.servers[spec.session_key].turns().prompts == 2


def test_the_event_log_forwards_from_the_brief_on(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    # T3 into T4: the probe's turn is never an event; the brief's turn, and every one after, is.
    key = launch_spec(rig, "p-one").session_key
    seen: list[bool] = []
    real = rig.runtime.c.tmux.paste

    def noted(name: str, text: str, **kw: Any) -> None:
        seen.append(rig.runtime.logs[key].forwarding)
        real(name, text, **kw)

    monkeypatch.setattr(rig.runtime.c.tmux, "paste", noted)
    rig.runtime.launch(replace(launch_spec(rig, "p-one"), brief="start"))
    assert seen == [False, True]                    # the probe's paste, then the brief's


def test_no_brief_pastes_nothing(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    assert rig.runtime.servers[spec.session_key].turns().prompts == 1


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_a_multi_line_brief_arrives_as_one_prompt(rig: RuntimeRig, profile: str) -> None:
    # D5: a CLI that asks for bracketed paste gets the whole brief as one prompt, newlines and all.
    rig.backend.env_extra["HZ_FAKE_BRACKETED"] = "1"
    text = "You are working on bead btq-1: Blue widget\n\nMake the widget blue.\n\nHow to work:\n- commit"
    spec = replace(launch_spec(rig, profile), brief=text)
    rig.runtime.launch(spec)
    prompts = [row["payload"]["prompt"] for row in spool(rig, spec.session_key)
               if row["payload"].get("hook_event_name") == "UserPromptSubmit"]
    assert prompts[1:] == [text]                   # the probe's prompt, then exactly one: the brief
    assert rig.runtime.servers[spec.session_key].turns().prompts == 2


def spool(rig: RuntimeRig, key: str) -> list[dict[str, Any]]:
    """The session server's spool of hook payloads (`SessionLayout.events`)."""
    path = rig.runtime.layout(key).events(1)
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_a_hostile_bead_is_briefed_as_one_prompt(rig: RuntimeRig, profile: str) -> None:
    # T4 c1 B1: a paste terminator and CRs in the bead's text or a note can't split the brief.
    rig.backend.env_extra["HZ_FAKE_BRACKETED"] = "1"
    hostile = "a\x1b[201~\rsecond\rthird\x1b[200~"
    shown = Bead("btq-1", "Blue " + hostile, "in_progress", None, (), {}, (),
                 raw={"description": hostile, "acceptance_criteria": hostile})
    got = brief.fit(brief.NoteBatch(((1, "note " + hostile),)))
    text = brief.compose(shown, resume=False, ops=frozenset(), notes=got.texts, more=got.more)
    spec = replace(launch_spec(rig, profile), brief=text)
    rig.runtime.launch(spec)
    prompts = [row["payload"]["prompt"] for row in spool(rig, spec.session_key)
               if row["payload"].get("hook_event_name") == "UserPromptSubmit"]
    assert prompts[1:] == [text]                   # the probe's prompt, then exactly one: the whole brief
    assert rig.runtime.servers[spec.session_key].turns().prompts == 2


@pytest.mark.parametrize(("text", "why"), [
    ("a\x1b[201~\rsecond\r", "terminal control"),
    ("lone \ud800", "not valid UTF-8"),
    ("x" * (brief.BRIEF_MAX + 1), "too long"),
], ids=["controls", "surrogate", "too-long"])
def test_a_brief_that_is_unsafe_to_paste_fails_the_launch_unpasted(rig: RuntimeRig, text: str, why: str,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    pasted: list[str] = []

    def noted(_real: Callable[[str, str], None], _name: str, t: str) -> None:
        pasted.append(t)

    monkeypatch.setattr(rig.runtime.c.tmux, "paste", paste_after_probe(rig.runtime.c.tmux, noted))
    with pytest.raises(LaunchFailed, match=why):
        rig.runtime.launch(replace(launch_spec(rig, "p-one"), brief=text))
    assert pasted == []
@ADAPTERS
def test_a_prompt_hook_during_the_longest_steer_is_answered_and_recorded_in_time(
        rig: RuntimeRig, profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # T5 c2 B1: the shim gives up after its hook wait and lets the turn start, so a prompt that waits for
    # a paste must still be answered within it. The paste is bounded to half the hook wait; here it takes
    # all of that bound, and the real shim's UserPromptSubmit, sent mid-paste, is still recorded in time.
    spec, _pane = idle_session(rig, profile)
    server = rig.runtime.servers[spec.session_key]
    before = server.turns()
    wait = json.loads((server.path.parent / shim.CONFIG_FILE).read_text())["wait_seconds"]
    entered, bounds = threading.Event(), list[float | None]()
    gate = WatchedGate(server)
    contended: list[bool] = []

    def slow(_name: str, _text: str, *, seconds: float | None = None) -> None:
        start = time.monotonic()
        bounds.append(seconds)
        entered.set()
        # The paste holds the gate: the prompt's blocking take, seen before the paste ends, is contention.
        contended.append(gate.waiting.wait(10))
        bound = seconds if seconds is not None else 15.0       # the whole bound (unbounded: one tmux step)
        time.sleep(max(0.0, bound - (time.monotonic() - start)))

    monkeypatch.setattr(rig.runtime.c.tmux, "paste", slow)
    monkeypatch.setattr(server, "_turn_gate", gate)
    steer = threading.Thread(target=rig.runtime.steer, args=(spec.session_key, 1, "first"))
    steer.start()
    answer: list[tuple[int, str]] = []
    try:
        assert entered.wait(10)
        raw = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": None, "prompt": "typed"})
        env = {"HZ_SESSION_SOCKET": str(server.path), "HOME": str(rig.home)}
        start = time.monotonic()
        answer.append(shim.run_hook(raw.encode(), env, event="UserPromptSubmit"))
        took = time.monotonic() - start
        prompts = server.turns().prompts                    # as the shim answers: the agent's turn starts
    finally:
        steer.join(30)
    assert contended == [True]                              # it did wait for the paste
    assert prompts == before.prompts + 1                    # recorded before the shim gave up
    assert answer == [(0, "")] and took < wait
    assert bounds == [min(runtime_mod.STEER_SECONDS, wait / 2)]
