import contextlib
import json
import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from dataclasses import replace

import msgspec
import pytest
from sandbox_env import RuntimeRig, fresh_logins, launch_spec, runtime_rig, short_dir, wait_for
from tmux_guard import new_test_tmux
from wsd_env import Clock, git_repo

from heterodyne.agents.base import HOOK_EVENTS
from heterodyne.sandbox import reaper
from heterodyne.sandbox.runtime import (
    BRANCH_TAKEN,
    WIP_FAILED,
    Phase,
    SandboxRuntime,
    SessionRecord,
    read_record,
    write_record,
)
from heterodyne.sandbox.spec import SessionLayout, sandbox_name, short_id
from heterodyne.session.server import SessionServer
from heterodyne.wsd import gitwip, ids
from heterodyne.wsd.runtime import (
    EndCause,
    Ended,
    LaunchFailed,
    LaunchUncertain,
    Liveness,
    RuntimeUnavailable,
)

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
                                               "claude-settings.json", "s.sock"}
    assert all(p.stat().st_mode & 0o077 == 0 for p in run.iterdir() if p.name != "s.sock")


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
    no park or defer makes a host WIP over it. The detail names the session and its private git
    directory relative to the state directory, never a home or absolute path."""
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
            assert str(exc) == (f"session {sid} generation 1: its commits were not landed; land them from "
                                f"s/{sid}/git in wsd's state directory by hand (docs/wsd.md §5)")
            assert str(rig.root) not in str(exc) and str(rig.home) not in str(exc)
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


def test_a_named_account_is_refused(rig: RuntimeRig) -> None:
    with pytest.raises(LaunchFailed, match="AU-6"):
        rig.runtime.launch(launch_spec(rig, "p-one", account="work"))
    assert rig.backend.created == []


def test_the_cli_pin_refuses_another_version(rig: RuntimeRig) -> None:
    package = rig.root / "i" / "cli" / "claude" / "package.json"
    package.write_text(json.dumps({"version": "9.9.9"}))
    with pytest.raises(LaunchFailed, match="pinned version"):
        rig.runtime.launch(launch_spec(rig, "p-one"))
    assert rig.backend.created == []


def test_the_freshness_gate_refuses_a_login_that_expires_too_soon(rig: RuntimeRig) -> None:
    s = rig.runtime.c.settings
    rig.clock.advance(86_400 - (s.max_lifetime_seconds + s.stop_margin_seconds))
    with pytest.raises(LaunchFailed, match="expires"):
        rig.runtime.launch(launch_spec(rig, "p-two"))
    assert rig.backend.created == []


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
