import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import World, factory
from wsd_env import WS, git_repo

from heterodyne.wsd import btq, ids
from heterodyne.wsd.beads import (
    PARKED,
    RECORD_KEY,
    BeadsAdapter,
    BeadsUnavailable,
    ClaimRefused,
    ClaimUncertain,
    ClaimView,
    NotOurs,
    RecordConflict,
    RecordUnreadable,
    RoutingChanged,
    SessionRecord,
    UnexpectedShape,
    WorktreeConflict,
    parse,
)


def raw(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "btq-1", "title": "t", "status": "open", "dependency_count": 0}
    return {**base, **kw}


def test_parse_omitted_labels_metadata_and_deps() -> None:
    bead = parse(raw(), detail=True)
    assert (bead.labels, bead.metadata, bead.deps) == ((), {}, ())


def test_parse_missing_dependencies_with_a_count_fails_closed() -> None:
    with pytest.raises(UnexpectedShape):
        parse(raw(dependency_count=1), detail=True)


def test_parse_missing_count_and_list_fails_closed() -> None:
    bead = raw()
    del bead["dependency_count"]
    with pytest.raises(UnexpectedShape):
        parse(bead, detail=True)


@pytest.mark.parametrize("count", [0, 2, "1", True, -1])
def test_parse_rejects_a_dependency_count_that_disagrees(count: Any) -> None:
    """Finding 17: a count that doesn't match the list, or isn't a count, is contradictory evidence."""
    with pytest.raises(UnexpectedShape):
        parse(raw(dependency_count=count, dependencies=[{"id": "x", "status": "open",
                                                         "dependency_type": "blocks"}]), detail=True)


@pytest.mark.parametrize("bad", [
    raw(labels="x"), raw(labels=[1]), raw(metadata=[]), raw(id=None), raw(dependencies={}),
    raw(dependency_count=1, dependencies=[{"id": "x", "status": "open"}]), [raw()], None,
])
def test_parse_rejects_unexpected_shapes(bad: Any) -> None:
    with pytest.raises(UnexpectedShape):
        parse(bad, detail=True)


def test_unknown_dependency_type_blocks() -> None:
    bead = parse(raw(dependency_count=2, dependencies=[
        {"id": "a", "status": "open", "dependency_type": "something-new"},
        {"id": "b", "status": "open", "dependency_type": "related"}]), detail=True)
    assert [d.id for d in bead.open_blockers()] == ["a"]


def test_operator_input_blocker_is_waiting_on_operator() -> None:
    bead = parse(raw(dependency_count=1, dependencies=[
        {"id": "a", "status": "open", "dependency_type": "blocks", "labels": ["kind:question"]}]),
        detail=True)
    assert bead.waits_on_operator()


def test_list_output_has_no_blocker_detail() -> None:
    with pytest.raises(ValueError, match="show"):
        parse(raw(), detail=False).open_blockers()


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path / "btq-state")


def test_claim_read_back_views(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    assert adapter.read_claim(WS, "btq-1") is ClaimView.FREE
    adapter.claim(WS, "btq-1")
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OURS
    world.beads["btq-1"].assignee = "someone:else"
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OTHER


def test_claim_errors_are_classified(world: World) -> None:
    world.add("btq-1", status="closed")
    adapter = BeadsAdapter(factory(world))
    with pytest.raises(ClaimRefused):
        adapter.claim(WS, "btq-1")
    world.add("btq-2")
    world.fault("claim", RuntimeError("timeout"), after=True)
    with pytest.raises(ClaimUncertain):
        adapter.claim(WS, "btq-2")
    assert adapter.read_claim(WS, "btq-2") is ClaimView.OURS


def test_ours_lists_only_per_bead_workers(world: World) -> None:
    world.add("btq-1")
    world.add("btq-2", status="in_progress", assignee="claude:host:x")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    assert [b.id for b in adapter.ours(WS)] == ["btq-1"]


def test_ours_is_found_by_assignee_whatever_the_labels(world: World) -> None:
    """Finding 12: a claimed bead that lost its routing labels, or went back to open or blocked under our
    worker, is still ours. A closed one is not."""
    for bead in ("btq-1", "btq-2", "btq-3", "btq-4"):
        world.add(bead)
    adapter = BeadsAdapter(factory(world))
    for bead in ("btq-1", "btq-2", "btq-3", "btq-4"):
        adapter.claim(WS, bead)
    world.beads["btq-1"].labels = ["kind:task"]
    world.beads["btq-2"].status = "blocked"
    world.beads["btq-3"].status = "open"
    world.close("btq-4")
    assert [b.id for b in adapter.ours(WS)] == ["btq-1", "btq-2", "btq-3"]


def test_unsettled_actions_include_closed_beads_and_unknown_values(world: World) -> None:
    """Finding 9: closing a bead settles nothing; a value wsd doesn't know is never read as settled."""
    world.add("btq-a", metadata={"action_state": "executing"})
    world.add("btq-b", status="closed", metadata={"action_state": "uncertain"})
    world.add("btq-c", status="closed", metadata={"action_state": "succeeded"})
    world.add("btq-d", metadata={"action_state": "half-done"})
    world.add("btq-e")
    adapter = BeadsAdapter(factory(world))
    found = adapter.with_metadata(WS, "action_state", frozenset({"pending", "succeeded", "failed"}))
    assert sorted(found) == ["btq-a", "btq-b", "btq-d"]


def test_validate_reruns_btqs_post_claim_checks(world: World) -> None:
    world.add("btq-1")
    world.add("btq-2", kind="research", metadata={"design_approval": ""})
    world.add("btq-3")
    adapter = BeadsAdapter(factory(world))
    with pytest.raises(NotOurs):
        adapter.validate(WS, "btq-1")
    for bead in ("btq-1", "btq-2", "btq-3"):
        adapter.claim(WS, bead)
    assert adapter.validate(WS, "btq-1").id == "btq-1"
    assert adapter.validate(WS, "btq-2").id == "btq-2"         # no design gate on research
    del world.beads["btq-1"].metadata["design_approval"]
    with pytest.raises(RoutingChanged):
        adapter.validate(WS, "btq-1")
    world.beads["btq-3"].labels.append("session:someone-else")
    with pytest.raises(RoutingChanged):
        adapter.validate(WS, "btq-3")


def test_session_record_round_trips_and_never_guesses(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    assert adapter.show(WS, "btq-1").record() is None
    rec = SessionRecord("coder", "p-one", "key-1", "/r", "/r-btq-btq-1")
    adapter.ensure_record(WS, "btq-1", rec)
    assert adapter.show(WS, "btq-1").record() == rec
    world.beads["btq-1"].metadata[RECORD_KEY] = '{"role": "coder"}'
    with pytest.raises(RecordUnreadable):
        adapter.show(WS, "btq-1").record()


def test_ensure_record_never_rewrites_an_existing_record(world: World) -> None:
    """r2 finding 2: the record of the same launch is left exactly as it is, fields wsd doesn't know
    included; a record of another launch, or one that doesn't parse, is never overwritten silently."""
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    rec = SessionRecord("coder", "p-one", "key-1", "/r", "/r-btq-btq-1")
    adapter.ensure_record(WS, "btq-1", rec)
    meta = world.beads["btq-1"].metadata
    enriched = json.dumps({**json.loads(meta[RECORD_KEY]), "thread_id": "th-1", "extra": {"n": [1, 2]}})
    meta[RECORD_KEY] = enriched
    adapter.ensure_record(WS, "btq-1", rec)
    assert meta[RECORD_KEY] == enriched
    with pytest.raises(RecordConflict):
        adapter.ensure_record(WS, "btq-1", SessionRecord("coder", "p-two", "key-2", "/r", "/r-btq-btq-1"))
    assert meta[RECORD_KEY] == enriched
    meta[RECORD_KEY] = '{"role": "coder"}'
    with pytest.raises(RecordUnreadable):
        adapter.ensure_record(WS, "btq-1", rec)
    assert meta[RECORD_KEY] == '{"role": "coder"}'
    adapter.ensure_record(WS, "btq-1", rec, replace_unreadable=True)
    assert adapter.show(WS, "btq-1").record() == rec


def test_worktree_needs_btqs_provenance(world: World, tmp_path: Path) -> None:
    """Finding 15: a worktree btq made for this bead and repository is verified; the same path without
    the provenance note (btq died between the two), or a worktree of another repository, is a conflict."""
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    assert adapter.verify_worktree(WS, "btq-1", repo, path) == path
    other = git_repo(tmp_path / "other" / "proj")
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", other, path)        # not a worktree of that repository
    world.beads["btq-1"].notes = ""
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", repo, path)
    with pytest.raises(WorktreeConflict):
        adapter.worktree(WS, "btq-1", repo)                       # and never reused


def test_writes_are_idempotent_and_owned(world: World) -> None:
    world.add("btq-1")
    world.add("btq-2")
    adapter = BeadsAdapter(factory(world))
    with pytest.raises(NotOurs):
        adapter.ensure_label(WS, "btq-1", PARKED)
    adapter.claim(WS, "btq-1")
    for _ in range(2):
        adapter.ensure_label(WS, "btq-1", PARKED)
        adapter.ensure_blocker(WS, "btq-1", "btq-2")
        adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)")
    bead = world.beads["btq-1"]
    assert bead.labels.count(PARKED) == 1
    assert bead.deps == [("btq-2", "blocks")]
    assert bead.comments == ["parked (m-1)"]


def test_uncertain_comment_is_not_repeated(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    world.fault("comments add", RuntimeError("timeout"), after=True)
    with pytest.raises(BeadsUnavailable):
        adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)")
    adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)")
    assert world.beads["btq-1"].comments == ["parked (m-1)"]


def test_queue_down_is_unavailable_never_empty(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.ws_queue(WS)
    world.down = True
    with pytest.raises(BeadsUnavailable):
        adapter.ready(WS)
    with pytest.raises(BeadsUnavailable):
        adapter.ours(WS)
    with pytest.raises(BeadsUnavailable):
        adapter.exists(WS, "btq-1")


def test_missing_bead_is_the_only_absent(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    assert adapter.exists(WS, "btq-9") is False


def test_unreadable_pause_flag_counts_as_paused(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    state = adapter.ws_queue(WS).state
    assert adapter.paused(WS) is False
    state.rmdir()
    state.write_text("not a directory")       # lstat of state/paused now fails with ENOTDIR
    assert adapter.paused(WS) is True


def test_pause_flag_reads_back(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    adapter.set_paused(WS, True)
    assert (adapter.ws_queue(WS).state / "paused").exists()
    adapter.set_paused(WS, False)
    assert adapter.paused(WS) is False


def test_worktree_is_idempotent_and_conflicts_fail_closed(world: World, tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    first = adapter.worktree(WS, "btq-1", repo)
    assert adapter.worktree(WS, "btq-1", repo) == first
    assert world.worktrees == ["btq-1"]
    world.add("btq-2")
    adapter.claim(WS, "btq-2")
    (tmp_path / "proj-btq-btq-2").mkdir()
    (tmp_path / "proj-btq-btq-2" / "keep").write_text("x")
    with pytest.raises(WorktreeConflict):
        adapter.worktree(WS, "btq-2", repo)
    assert (tmp_path / "proj-btq-btq-2" / "keep").exists()


FAKE_BD = """#!{python}
import json, sys
log = {log!r}
beads = json.loads({beads!r})
with open(log, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if "show" in args:
    bead = beads.get(args[args.index("show") + 1])
    if bead is None:
        sys.exit("no issue found")
    print(json.dumps([bead]))
else:
    print("[]")
"""


@pytest.mark.skipif(not os.environ.get("BTQ_REPO"), reason="needs $BTQ_REPO (a beads-task-queue checkout)")
def test_contract_with_real_btq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real btq Queue, loaded as wsd does, against a fake `bd` executable: no Dolt, no network. Also
    the guard's re-run of btq's post-claim checks: `matches` and the design gate, as btq implements them."""
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bindir.mkdir()
    config = tmp_path / "btq-config"
    config.mkdir()
    (config / "credentials.json").write_text(json.dumps({"wsd": "test-only"}))

    def bead(bead_id: str, kind: str, **extra: Any) -> dict[str, Any]:
        worker = f"wsd:fakehost:{ids.bead_session(WS, bead_id)}"
        return {"id": bead_id, "title": "t", "status": "in_progress", "assignee": worker,
                "labels": ["agent:wsd", f"ws:{WS}", f"kind:{kind}"], "dependency_count": 0, **extra}

    beads = {"btq-1": bead("btq-1", "task"),                                  # no design approval
             "btq-2": bead("btq-2", "research"),                              # no design gate
             "btq-3": bead("btq-3", "task", metadata={"design_approval": "btq-2"})}   # not an approval
    bd = bindir / "bd"
    bd.write_text(FAKE_BD.format(python=sys.executable, log=str(tmp_path / "bd.log"),
                                 beads=json.dumps(beads)))
    bd.chmod(bd.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr("socket.gethostname", lambda: "fakehost")
    module = btq.load(Path(os.environ["BTQ_REPO"]))
    adapter = BeadsAdapter(btq.factory(module, {"config_dir": str(config), "repo": str(tmp_path)}))
    assert adapter.show(WS, "btq-1").labels == ("agent:wsd", f"ws:{WS}", "kind:task")
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OURS
    assert adapter.paused(WS) is False
    assert str(adapter.ws_queue(WS).state).startswith(str(home))

    def actors() -> set[str]:
        logged = [json.loads(line) for line in (tmp_path / "bd.log").read_text().splitlines()]
        (tmp_path / "bd.log").unlink()
        assert all(argv[-1] == "--json" for argv in logged)
        return {argv[argv.index("--actor") + 1] for argv in logged}

    assert actors() == {f"wsd:fakehost:{ids.ws_session(WS)}"}       # reads use the workstream worker
    assert adapter.validate(WS, "btq-2").id == "btq-2"
    assert actors() == {beads["btq-2"]["assignee"]}                   # validation, the bead's own worker
    for gated in ("btq-1", "btq-3"):
        with pytest.raises(RoutingChanged):
            adapter.validate(WS, gated)


def test_btq_without_wsd_agent_is_refused(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "btq").write_text("AGENTS = ('claude',)\nclass Queue: pass\n")
    with pytest.raises(btq.BtqUnavailable):
        btq.load(tmp_path)


def test_missing_btq_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(btq.BtqUnavailable):
        btq.load(tmp_path)
