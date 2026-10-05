import json
import os
import stat
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import World, factory
from wsd_env import WS, git_repo

from heterodyne.wsd import btq, gitwip, ids
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


def test_routing_change_during_the_claim_is_not_uncertain(world: World) -> None:
    """btq's claim landed but its post-claim check failed: the bead is ours and must never run."""
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    world.fault("claim", RuntimeError("Routing/design changed during claim; ask Bel to reconcile"),
                after=True)
    with pytest.raises(RoutingChanged):
        adapter.claim(WS, "btq-1")
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OURS


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


@pytest.mark.parametrize("value", [{"state": "succeeded"}, ["succeeded"], None, 5, True])
def test_a_non_string_action_value_is_unsettled(world: World, value: Any) -> None:
    """An object, list, null or number in the action metadata is never settled, and never escapes as a
    TypeError past the fail-closed boundary."""
    world.add("btq-a", metadata={"action_state": value})
    adapter = BeadsAdapter(factory(world))
    settled = frozenset({"pending", "succeeded", "failed"})
    assert adapter.with_metadata(WS, "action_state", settled) == ["btq-a"]


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


FIELDS = {"role": "coder", "profile": "p-one", "session_key": "key-1", "repo": "/r",
          "worktree": "/r-btq-btq-1"}


@pytest.mark.parametrize("value", [None, 5, True, [], FIELDS])
def test_a_present_record_that_is_not_a_json_string_is_unreadable(world: World, value: Any) -> None:
    """Only an absent key means "no record". A null or any other non-string value is unreadable evidence,
    replaced only by the operator's release (`replace_unreadable`), never treated as missing."""
    world.add("btq-1", metadata={RECORD_KEY: value})
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    with pytest.raises(RecordUnreadable):
        adapter.show(WS, "btq-1").record()
    rec = SessionRecord("coder", "p-one", "key-1", "/r", "/r-btq-btq-1")
    with pytest.raises(RecordUnreadable):
        adapter.ensure_record(WS, "btq-1", rec)
    assert world.beads["btq-1"].metadata[RECORD_KEY] == value
    adapter.ensure_record(WS, "btq-1", rec, replace_unreadable=True)
    assert adapter.show(WS, "btq-1").record() == rec


def test_there_is_no_generic_metadata_writer() -> None:
    """btq's agent:wsd exception lets wsd write only `metadata.wsd_session`, through `ensure_record`."""
    assert not hasattr(BeadsAdapter, "ensure_metadata")


def test_owned_writes_wait_for_the_workers_exclusive_lock(world: World) -> None:
    """Every owned write runs under the per-bead worker's `exclusive()` lock: while another holder has it,
    a write neither checks ownership nor writes. Deterministic: the writer signals when it reaches the
    lock."""
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    queue = adapter.bead_queue(WS, "btq-1")

    class Door:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.reached = threading.Event()

        def __enter__(self) -> None:
            self.reached.set()
            self.lock.acquire()

        def __exit__(self, *exc: object) -> None:
            self.lock.release()

    door = Door()
    queue._exclusive = door  # pyright: ignore[reportAttributeAccessIssue]
    errors: list[BaseException] = []

    def write() -> None:
        try:
            adapter.ensure_label(WS, "btq-1", PARKED)
        except BaseException as exc:  # noqa: BLE001 - reported to the main thread
            errors.append(exc)

    assert door.lock.acquire(timeout=5)
    writer = threading.Thread(target=write)
    held = True
    try:
        calls = len(world.calls)        # before the writer starts: no ownership check may precede the lock
        writer.start()
        assert door.reached.wait(timeout=5), "the write never asked for the exclusive lock"
        assert PARKED not in world.beads["btq-1"].labels
        assert (queue.worker, "owned") not in world.calls[calls:]
        door.lock.release()
        held = False
        writer.join(timeout=5)
        assert not writer.is_alive()
    finally:
        if held:
            door.lock.release()
        writer.join(timeout=5)
    assert errors == []
    assert PARKED in world.beads["btq-1"].labels


def test_comments_that_read_as_null_are_unavailable(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """bd answers [] when a bead has no comments; null (btq's reading of empty output) is not evidence."""
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    monkeypatch.setattr(adapter.ws_queue(WS), "bd", lambda *args: None)
    with pytest.raises(UnexpectedShape):
        adapter.comments(WS, "btq-1")


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


def test_replaced_worktree_directory_is_a_conflict(world: World, tmp_path: Path) -> None:
    """D22: provenance alone is not enough. A separate repository put at the recorded path, on the
    bead's branch, does not share the repository's git common directory and is never used."""
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    gitwip.git(repo, "worktree", "remove", "--force", str(path))
    git_repo(path)
    gitwip.git(path, "checkout", "-q", "-b", "btq/btq-1")
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", repo, path)
    with pytest.raises(WorktreeConflict):
        adapter.worktree(WS, "btq-1", repo)


def test_same_branch_with_unrelated_history_is_a_conflict(world: World, tmp_path: Path) -> None:
    """D22: the worktree must hold the base btq recorded. The same repository, path and branch name with
    history that does not descend from the base is not this bead's worktree."""
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    ident = ("-c", "user.name=t", "-c", "user.email=t@example.org")
    gitwip.git(path, "checkout", "-q", "--orphan", "elsewhere")
    gitwip.git(path, *ident, "commit", "-q", "--allow-empty", "-m", "unrelated")
    gitwip.git(path, "branch", "-q", "-D", "btq/btq-1")
    gitwip.git(path, "branch", "-q", "-m", "btq/btq-1")
    assert gitwip.branch(path) == "btq/btq-1"
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", repo, path)
    with pytest.raises(WorktreeConflict):
        adapter.worktree(WS, "btq-1", repo)


@pytest.mark.parametrize("base", ["0" * 40, "short", "tree"])
def test_a_recorded_base_that_is_not_a_commit_is_a_conflict(world: World, tmp_path: Path, base: str) -> None:
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    bead = world.beads["btq-1"]
    real = gitwip.git(repo, "rev-parse", "HEAD")
    if base == "tree":        # an object that exists, but is not a commit
        base = gitwip.git(repo, "rev-parse", "HEAD^{tree}")
    bead.notes = bead.notes.replace(f"base={real}", f"base={base}")
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", repo, path)


@pytest.mark.parametrize("note", ["other-path", "other-repo", "second-bad-base", "second-short-base",
                                  "second-ref-base"])
def test_provenance_must_name_this_repository_path_and_base(
        world: World, tmp_path: Path, note: str) -> None:
    """The note must be btq's for this repository and this path; every note for the path must hold."""
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    bead = world.beads["btq-1"]
    if note == "other-path":
        bead.notes = bead.notes.replace(f"worktree={path}", f"worktree={path}-old")
    elif note == "other-repo":
        bead.notes = bead.notes.replace(f"repository={repo.resolve()};", f"repository={tmp_path / 'x'};")
    else:
        line = bead.notes.splitlines()[-1]
        base = gitwip.git(repo, "rev-parse", "HEAD")
        bad = {"second-bad-base": "1" * 40, "second-short-base": "short", "second-ref-base": "HEAD"}[note]
        bead.notes += "\n" + line.replace(f"base={base}", f"base={bad}")
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", repo, path)


def test_a_worktree_ahead_of_its_base_is_verified(world: World, tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    gitwip.wip_commit(path, "op1", "parked btq-1")
    assert adapter.verify_worktree(WS, "btq-1", repo, path) == path


@pytest.mark.parametrize("write", ["label", "unlabel", "blocker", "comment", "record"])
def test_a_write_that_does_not_read_back_is_unavailable(
        world: World, monkeypatch: pytest.MonkeyPatch, write: str) -> None:
    """S6: `bd label add` on a missing bead exits 0 and writes nothing. A write is only trusted once it
    reads back, so a bd that reports success without changing the bead is BeadsUnavailable."""
    world.add("btq-1", labels=["old"])
    world.add("btq-2")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    monkeypatch.setattr(adapter.bead_queue(WS, "btq-1"), "bd", lambda *args: [])
    rec = SessionRecord("coder", "p-one", "k", "/r", "/w")
    writes = {
        "label": lambda: adapter.ensure_label(WS, "btq-1", PARKED),
        "unlabel": lambda: adapter.ensure_label(WS, "btq-1", "old", present=False),
        "blocker": lambda: adapter.ensure_blocker(WS, "btq-1", "btq-2"),
        "comment": lambda: adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)"),
        "record": lambda: adapter.ensure_record(WS, "btq-1", rec),
    }
    with pytest.raises(BeadsUnavailable):
        writes[write]()


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
    (config / "policy.json").write_text("{}")
    checkout = Path(os.environ["BTQ_REPO"])
    for name in list(os.environ):      # btq reads BTQ_* locations and BTQ_POLICY; bd inherits BEADS_*
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("BTQ_POLICY", str(config / "policy.json"))
    locations = {"config_dir": str(config), "repo": str(tmp_path), "dolt_host": "127.0.0.1",
                 "dolt_port": "1", "dolt_database": "test-only",
                 "credentials": str(config / "credentials.json"), "tls_cert": str(config / "server.crt")}

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
    module = btq.load(checkout)
    assert module.locations() == module.LOCATION_DEFAULTS      # no inherited override is left
    assert module.LOCATION_DEFAULTS["config_dir"].startswith(str(home))
    adapter = BeadsAdapter(btq.factory(module, locations))
    assert adapter.show(WS, "btq-1").labels == ("agent:wsd", f"ws:{WS}", "kind:task")
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OURS
    assert adapter.paused(WS) is False
    assert str(adapter.ws_queue(WS).state).startswith(str(home))
    opened: Any = adapter.ws_queue(WS)
    assert (opened.env["BEADS_DOLT_PASSWORD"], opened.policy) == ("test-only", {})

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
