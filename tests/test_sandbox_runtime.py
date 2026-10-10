import contextlib
import json
import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from dataclasses import replace

import pytest
from sandbox_env import RuntimeRig, fresh_logins, launch_spec, runtime_rig, short_dir, wait_for
from tmux_guard import new_test_tmux
from wsd_env import Clock

from heterodyne.agents.base import HOOK_EVENTS
from heterodyne.sandbox.runtime import WIP_FAILED, Phase, SessionRecord, read_record, write_record
from heterodyne.sandbox.spec import SessionLayout, sandbox_name
from heterodyne.session.server import SessionServer
from heterodyne.wsd import gitwip
from heterodyne.wsd.runtime import LaunchFailed, LaunchUncertain, Liveness, RuntimeUnavailable

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


def test_commits_that_cant_land_refuse_the_relaunch(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    before = host_tip(rig, "btq/btq-1")
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    (rig.runtime.layout(spec.session_key).git / "objects" / "zz").symlink_to(rig.root)
    rig.runtime.stop(spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True)
    assert rec.error == "the session's commits could not be landed"
    assert host_tip(rig, "btq/btq-1") == before
    with pytest.raises(LaunchFailed, match="not landed"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert (rig.runtime.layout(spec.session_key).git / "objects" / "zz").is_symlink()     # kept for a human


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
    rig.runtime.stop(spec.session_key)
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
    assert rig.backend.reapers == [(rec.sandbox, rec.deadline)]
    assert rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key))
    rig.runtime.stop(spec.session_key)
    assert not rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key))


def test_a_launch_that_outlasts_its_window_is_refused_and_cleaned_up(rig: RuntimeRig) -> None:
    s = rig.runtime.c.settings
    rig.selftest.during_agent = lambda: rig.clock.advance(s.max_lifetime_seconds - s.stop_margin_seconds)
    spec = launch_spec(rig, "p-two")
    with pytest.raises(LaunchFailed, match="outlasted"):
        rig.runtime.launch(spec)
    assert rig.backend.boxes == {}
    assert record(rig, spec.session_key).phase is Phase.ENDED
    assert not rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key))


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
    rig.runtime.stop(spec.session_key)
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
    rig.runtime.stop(spec.session_key)
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
    rig.runtime.stop(spec.session_key)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True)
    with pytest.raises(LaunchFailed, match="not landed"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))


def wip_commits(rig: RuntimeRig) -> int:
    log = subprocess.run(["git", "-C", str(rig.repo), "log", "--format=%B", "btq/btq-1"], capture_output=True,
                         text=True, check=True).stdout
    return log.count(f"{gitwip.PARK_MARK}lifetime:")


def lifetime_end(rig: RuntimeRig, key: str) -> None:
    """The pane died and the deadline passed: the next listing ends the session for its lifetime."""
    rec = record(rig, key)
    rig.tmux.kill(rec.tmux_session)
    rig.clock.advance(rec.deadline - rig.clock.now)
    assert rig.runtime.sessions("alpha") == []


def test_a_lifetime_stop_whose_landing_fails_owes_its_wip_and_commits_none(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    before = host_tip(rig, "btq/btq-1")
    agent_git(rig, spec.session_key, "commit", "-q", "--allow-empty", "-m", "agent work")
    (rig.worktree / "edit.txt").write_text("unsaved work\n")
    (rig.runtime.layout(spec.session_key).git / "objects" / "zz").symlink_to(rig.root)
    lifetime_end(rig, spec.session_key)
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
