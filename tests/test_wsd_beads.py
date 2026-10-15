import json
import os
import stat
import sys
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import ASK_FIELDS, FakeQueue, World, factory
from wsd_env import WS, git_repo

from heterodyne.wsd import approvals, btq, gitwip, ids
from heterodyne.wsd import btq as wbtq
from heterodyne.wsd.beads import (
    PARKED,
    QUESTION_ANSWERED,
    RECORD_KEY,
    REF_KEY,
    BeadsAdapter,
    BeadsUnavailable,
    ClaimRefused,
    ClaimUncertain,
    ClaimView,
    CreateUnverified,
    NotAQuestion,
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
    del world.beads["btq-1"]
    assert adapter.read_claim(WS, "btq-1") is ClaimView.GONE


def test_an_unreadable_claim_is_never_gone(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    world.fault("show", RuntimeError("dolt down"))
    with pytest.raises(BeadsUnavailable):         # the show failed, and the bead still exists
        adapter.read_claim(WS, "btq-1")
    world.down = True
    del world.beads["btq-1"]
    with pytest.raises(BeadsUnavailable):         # gone, but bd can't confirm it
        adapter.read_claim(WS, "btq-1")


def test_a_session_pin_is_listed_but_never_claimable(world: World) -> None:
    """btq's ready() for the workstream worker lists a bead pinned to that worker's session; the claim, by
    the per-bead worker, refuses it. `claimable` gives the claim's answer before trying."""
    world.add("btq-1", labels=[f"session:{ids.ws_session(WS)}"])
    world.add("btq-2")
    world.add("btq-3", labels=[f"session:{ids.bead_session(WS, 'btq-3')}"])
    adapter = BeadsAdapter(factory(world))
    listed = {b.id: b for b in adapter.ready(WS)}
    assert sorted(listed) == ["btq-1", "btq-2"]           # btq-3 is pinned to its own per-bead worker
    assert [adapter.claimable(WS, b) for b in listed.values()] == [False, True]
    with pytest.raises(ClaimRefused):
        adapter.claim(WS, "btq-1")
    assert adapter.claim(WS, "btq-2").status == "in_progress"


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
    assert gitwip.git(path, "rev-parse", "--abbrev-ref", "HEAD") == "btq/btq-1"
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


def test_separators_inside_the_paths_never_shift_the_fields(world: World, tmp_path: Path) -> None:
    """Paths may contain `; base=` and `; worktree=`: the note is matched against the exact repository and
    worktree, never split on the separators, so btq's genuine note still verifies."""
    repo = git_repo(tmp_path / "proj; base=short; worktree=x")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    assert "; base=" in str(path) and "; worktree=" in str(path)
    assert adapter.verify_worktree(WS, "btq-1", repo, path) == path


def test_notes_for_other_repositories_and_paths_do_not_count(world: World, tmp_path: Path) -> None:
    """Notes whose repository or worktree has ours as a prefix or a suffix, each with a base that is not a
    commit, are another worktree's: they neither verify nor block ours."""
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    worker = adapter.bead_queue(WS, "btq-1").worker
    r = repo.resolve()
    bead = world.beads["btq-1"]
    genuine = bead.notes
    others = [(f"{r}2", path), (f"/x{r}", path), (r, f"{path}-old"), (r, f"/x{path}")]
    bead.notes = "\n".join(f"worker={worker}; repository={o}; base=short; worktree={w}" for o, w in others)
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", repo, path)      # none of them is ours
    bead.notes = genuine + "\n" + bead.notes
    assert adapter.verify_worktree(WS, "btq-1", repo, path) == path
    # A note that opens and closes exactly as ours does is ours, whatever lies between: its base is the whole
    # middle, here not a commit, so it fails closed rather than being re-split on the separators.
    bead.notes = genuine + f"\nworker={worker}; repository={r}; base=short; worktree={path}; repository={r}" \
        f"; base=short; worktree={path}"
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", repo, path)


def test_a_worktree_ahead_of_its_base_is_verified(world: World, tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    gitwip.wip_commit(gitwip.pin(repo, path, "btq/btq-1"), "op1", "parked btq-1")
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
ready = json.loads({ready!r})
if "show" in args:
    bead = beads.get(args[args.index("show") + 1])
    if bead is None:
        sys.exit("no issue found")
    print(json.dumps([bead]))
elif "ready" in args:
    print(json.dumps(ready))
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
                                 beads=json.dumps(beads), ready="[]"))
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


@pytest.mark.skipif(not os.environ.get("BTQ_REPO"), reason="needs $BTQ_REPO (a beads-task-queue checkout)")
def test_contract_ready_and_claim_agree_with_real_btq(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex r1 finding 1, against the real btq Queue and a fake `bd`: the workstream worker's ready()
    lists a bead pinned to its own session, the per-bead worker's claim refuses exactly that bead, and
    `claimable` agrees. The unpinned bead passes eligibility (the fake bd never records the claim, so
    btq's ownership read-back fails after it: uncertain, not refused)."""
    home, bindir, config = tmp_path / "home", tmp_path / "bin", tmp_path / "btq-config"
    for d in (home, bindir, config):
        d.mkdir()
    (config / "credentials.json").write_text(json.dumps({"wsd": "test-only"}))
    (config / "policy.json").write_text("{}")
    checkout = Path(os.environ["BTQ_REPO"])
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("BTQ_POLICY", str(config / "policy.json"))
    locations = {"config_dir": str(config), "repo": str(tmp_path), "dolt_host": "127.0.0.1",
                 "dolt_port": "1", "dolt_database": "test-only",
                 "credentials": str(config / "credentials.json"), "tls_cert": str(config / "server.crt")}

    def bead(bead_id: str, *labels: str) -> dict[str, Any]:
        return {"id": bead_id, "title": "t", "status": "open", "dependency_count": 0,
                "labels": ["agent:wsd", f"ws:{WS}", "kind:research", *labels]}

    ready = [bead("btq-1", f"session:{ids.ws_session(WS)}"), bead("btq-2")]
    bd = bindir / "bd"
    bd.write_text(FAKE_BD.format(python=sys.executable, log=str(tmp_path / "bd.log"),
                                 beads=json.dumps({b["id"]: b for b in ready}), ready=json.dumps(ready)))
    bd.chmod(bd.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr("socket.gethostname", lambda: "fakehost")
    adapter = BeadsAdapter(btq.factory(btq.load(checkout), locations))
    listed = adapter.ready(WS)
    assert [b.id for b in listed] == ["btq-1", "btq-2"]
    assert [adapter.claimable(WS, b) for b in listed] == [False, True]
    with pytest.raises(ClaimRefused, match="not eligible"):
        adapter.claim(WS, "btq-1")
    with pytest.raises(ClaimUncertain):
        adapter.claim(WS, "btq-2")
    logged = [json.loads(line) for line in (tmp_path / "bd.log").read_text().splitlines()]
    assert [a for a in logged if "update" in a and "--claim" in a] != []
    assert all("btq-1" not in a for a in logged if "update" in a)


def test_a_bead_id_that_is_not_a_slug_is_never_claimable(world: World) -> None:
    world.add("BTQ-1")
    adapter = BeadsAdapter(factory(world))
    [listed] = adapter.ready(WS)
    assert adapter.claimable(WS, listed) is False


# --- beads wsd creates: question and approval beads (plan 5i part 2, Task 4) ---

WRITES = frozenset({"create", "comments add", "close", "label", "dep", "update"})


def adapter_for(world: World) -> BeadsAdapter:
    """An adapter of `world`, with the world's ref pins: a new one is a wsd restarted on the same journal."""
    return BeadsAdapter(factory(world), pins=world.pins)


# The fixed log texts, as literals so the tests read the same against code that predates them.
DUPLICATE_OF = "duplicate of {canonical}"
DUPLICATE_DECIDED = "a decision on a duplicate bead is ignored (%d)"
DUPLICATE_NOT_CLOSED = "a duplicate question bead could not be closed (%d)"
DUPLICATE_LEFT_OPEN = "a duplicate approval bead is left open and ignored (%d)"


def writes(world: World, since: int = 0) -> list[str]:
    return [call for _, call in world.calls[since:] if call in WRITES]


def ask(adapter: BeadsAdapter, ref: str = "q:alpha:btq-1:1:1", kind: str = "kind:question", **kw: Any) -> str:
    fields: dict[str, Any] = {"title": "Question from btq-1: which?", "description": "the agent asks",
                              "labels": (kind,), "metadata": {"round": "1"}, **kw}
    return adapter.create(WS, ref=ref, **fields)


def test_create_is_idempotent_by_ref(world: World) -> None:
    """A crash between `bd create` and the journal: the bead exists, the caller never learned its ID. The
    same ref again finds it and makes no second bead."""
    adapter = adapter_for(world)
    world.fault("create", RuntimeError("bd: timed out"), after=True)
    with pytest.raises(BeadsUnavailable):
        ask(adapter)
    assert len(world.created) == 1
    assert ask(adapter) == world.created[0]
    assert ask(adapter) == world.created[0]
    assert len(world.created) == 1
    assert writes(world) == ["create"]
    assert ask(adapter, ref="q:alpha:btq-1:1:2") != world.created[0]      # another ref is another bead
    assert len(world.created) == 2


def test_create_records_what_was_asked(world: World) -> None:
    adapter = adapter_for(world)
    bead = world.beads[ask(adapter)]
    assert (bead.title, bead.description) == ("Question from btq-1: which?", "the agent asks")
    assert sorted(bead.labels) == ["kind:question", f"ws:{WS}"]
    assert bead.metadata == {"round": "1", REF_KEY: "q:alpha:btq-1:1:1"}
    assert [call for _, call in world.calls].count("create") == 1


def _alter(world: World, change: str) -> None:
    [bead] = [world.beads[b] for b in world.created[-1:]]
    if change == "title":
        bead.title = "something else"
    elif change == "description":
        bead.description = ""
    elif change == "label dropped":
        bead.labels.remove("kind:question")
    elif change == "label added":
        bead.labels.append("agent:wsd")
    elif change == "metadata":
        bead.metadata["round"] = "2"
    elif change == "metadata dropped":
        del bead.metadata["round"]
    else:
        bead.metadata[REF_KEY] = "q:alpha:btq-1:1:9"


CHANGES = ["title", "description", "label dropped", "label added", "metadata", "metadata dropped", "ref"]


@pytest.mark.parametrize("change", CHANGES)
def test_create_reads_back_and_refuses_a_mismatch(world: World, monkeypatch: pytest.MonkeyPatch,
                                                  change: str) -> None:
    created = FakeQueue._create      # pyright: ignore[reportPrivateUsage]

    def lossy(self: FakeQueue, args: list[str]) -> Any:
        out = created(self, args)
        _alter(world, change)        # bd stored something other than what it was asked to
        return out

    monkeypatch.setattr(FakeQueue, "_create", lossy)
    with pytest.raises(CreateUnverified):
        ask(adapter_for(world))


@pytest.mark.parametrize("change", [c for c in CHANGES if c != "ref"])
def test_a_ref_found_with_other_content_is_refused(world: World, change: str) -> None:
    """The ref is the create's identity: a bead under it that isn't what is asked now is never returned."""
    adapter = adapter_for(world)
    ask(adapter)
    _alter(world, change)
    since = len(world.calls)
    with pytest.raises(CreateUnverified):
        ask(adapter)
    assert len(world.created) == 1
    assert writes(world, since) == []         # a mismatch is never overwritten


def test_a_ref_held_by_two_beads_gives_the_canonical_one(world: World) -> None:
    """Two holders of a ref: the earliest created is returned, and the other, a question, is closed as its
    duplicate. Nothing is created."""
    adapter = adapter_for(world)
    first = ask(adapter)
    second = ask(adapter, ref="q:alpha:btq-1:1:2")
    world.beads[second].metadata[REF_KEY] = "q:alpha:btq-1:1:1"
    assert ask(adapter) == first
    assert len(world.created) == 2
    assert world.closed_reasons == {second: DUPLICATE_OF.format(canonical=first)}
    assert world.beads[second].comments == [DUPLICATE_OF.format(canonical=first)]


def test_an_unreadable_ref_lookup_never_creates(world: World) -> None:
    adapter = adapter_for(world)
    world.fault("list", RuntimeError("dolt: connection refused"))
    with pytest.raises(BeadsUnavailable):
        ask(adapter)
    assert writes(world) == []


@pytest.mark.parametrize("kind", ["kind:question", "kind:approval"])
def test_created_beads_are_never_claimable(world: World, kind: str) -> None:
    adapter = adapter_for(world)
    bead = adapter.show(WS, ask(adapter, kind=kind))
    assert "agent:wsd" not in bead.labels
    assert adapter.claimable(WS, bead) is False
    assert adapter.ready(WS) == []


@pytest.mark.parametrize("labels", [
    (), ("kind:task",), ("kind:review",), ("kind:question", "kind:approval"), ("kind:question", "agent:wsd"),
    ("kind:question", "agent:codex"), ("kind:question", "ws:beta"), ("kind:question", f"ws:{WS}"),
    ("kind:question", "session:x"), ("kind:question", "needs-human"),
])
def test_create_refuses_labels_that_are_not_one_operator_kind(world: World, labels: tuple[str, ...]) -> None:
    """wsd creates only question and approval beads, and never routes one to an agent: anything else is
    refused before bd is called. The workstream label is wsd's to add."""
    with pytest.raises(ValueError, match="labels"):
        adapter_for(world).create(WS, ref="q:alpha:btq-1:1:1", title="t", description="d",
                                            labels=labels, metadata={})
    assert world.calls == []


def test_create_refuses_metadata_that_names_the_ref(world: World) -> None:
    with pytest.raises(ValueError, match=REF_KEY):
        ask(adapter_for(world), metadata={REF_KEY: "other"})
    assert world.calls == []


def test_close_question_comments_then_closes(world: World) -> None:
    adapter = adapter_for(world)
    bead = ask(adapter)
    since = len(world.calls)
    adapter.close_question(WS, bead, "Use the second option.")
    assert writes(world, since) == ["comments add", "close"]
    assert world.beads[bead].status == "closed"
    assert world.closed_reasons == {bead: QUESTION_ANSWERED}
    [comment] = world.beads[bead].comments
    assert "Use the second option." in comment
    since = len(world.calls)
    adapter.close_question(WS, bead, "Use the second option.")      # replayed: nothing written again
    assert writes(world, since) == []


def test_close_question_after_a_failed_close_never_comments_twice(world: World) -> None:
    adapter = adapter_for(world)
    bead = ask(adapter)
    world.fault("close", RuntimeError("bd: timed out"))
    with pytest.raises(BeadsUnavailable):
        adapter.close_question(WS, bead, "Yes.")
    assert world.beads[bead].status == "open"
    adapter.close_question(WS, bead, "Yes.")
    assert len(world.beads[bead].comments) == 1
    assert world.beads[bead].status == "closed"


def test_close_question_that_does_not_read_back_closed_fails(world: World,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = adapter_for(world)
    bead = ask(adapter)
    real = FakeQueue._bd      # pyright: ignore[reportPrivateUsage]

    def ignored(self: FakeQueue, args: list[str]) -> Any:
        return [] if args[0] == "close" else real(self, args)       # bd said yes, and closed nothing

    monkeypatch.setattr(FakeQueue, "_bd", ignored)
    with pytest.raises(BeadsUnavailable, match="close"):
        adapter.close_question(WS, bead, "Yes.")


def _others(world: World, adapter: BeadsAdapter) -> dict[str, str]:
    world.add("btq-task")                                             # a task: never touched
    world.add("btq-q", kind="question")                               # a question wsd did not create
    world.add("btq-qt", labels=["kind:question"], metadata={REF_KEY: "q:x"})      # kind:task as well
    world.add("btq-other-ws", ws="beta", kind="question", metadata={REF_KEY: "q:y"})
    approval = ask(adapter, ref="a:1", kind="kind:approval")           # wsd's, but an approval
    return {"task": "btq-task", "foreign question": "btq-q", "task and question": "btq-qt",
            "other workstream": "btq-other-ws", "approval": approval}


@pytest.mark.parametrize("which", ["task", "foreign question", "task and question", "other workstream",
                                   "approval"])
def test_close_question_refuses_any_other_bead(world: World, which: str) -> None:
    adapter = adapter_for(world)
    bead = _others(world, adapter)[which]
    since = len(world.calls)
    with pytest.raises(NotAQuestion):
        adapter.close_question(WS, bead, "Yes.")
    assert writes(world, since) == []
    assert world.closed_reasons == {}


# --- a crash inside `bd create`: the canonical holder and its duplicates (c2) ---

REF = "q:alpha:btq-1:1:1"
APPROVER = "operator-a"


def holders(world: World, ref: str = REF, status: str | None = None) -> list[str]:
    return sorted(b.id for b in world.beads.values()
                  if b.metadata.get(REF_KEY) == ref and status in (None, b.status))


def dies_mid_create(world: World, **kw: Any) -> None:
    """wsd dies inside `bd create`: its lock is gone, and the bd child it started is still running."""
    world.orphan_next_create = True
    with pytest.raises(BeadsUnavailable):
        ask(adapter_for(world), **kw)
    assert world.created == [] and len(world.orphans) == 1


def earliest(world: World, ref: str = REF) -> str:
    return min(holders(world, ref), key=lambda b: (world.beads[b].created_at, b))


WHEN = {"before the list": ("list", 1), "after the list": ("create", 1), "after the create": ("list", 2),
        "after the replacement returned": None}


@pytest.mark.parametrize("tick", [1, 0], ids=["later second", "same second"])
@pytest.mark.parametrize("when", list(WHEN))
def test_a_crash_mid_create_leaves_one_open_holder_and_a_stable_canonical(world: World, when: str,
                                                                          tick: int) -> None:
    """The dead wsd's `bd create` commits at any point around the replacement's create. The first create to
    resolve the ref pins its bead (the earliest created, then the lowest ID, of the holders it saw), and
    every later create and lookup, by any adapter (a restart), gives that bead, whenever the orphan
    commits. Exactly one holder is still open, and nothing is ever created over another bead."""
    world.tick = timedelta(seconds=tick)
    dies_mid_create(world)
    hook = WHEN[when]
    if hook is not None:
        world.before(hook[0], world.commit_orphans, nth=hook[1])
    first = ask(adapter_for(world))
    assert world.pins.pinned(WS, REF) == first
    world.commit_orphans()
    if when != "after the replacement returned":      # both were there at the first resolution
        assert first == earliest(world)
    for _ in range(3):            # restarts
        assert ask(adapter_for(world)) == first
        assert adapter_for(world).holder(WS, REF) == first
    assert holders(world, status="open") == [first]
    assert len(holders(world)) == (1 if when == "before the list" else 2)


def test_holder_finds_nothing_for_an_unknown_ref(world: World) -> None:
    adapter = adapter_for(world)
    ask(adapter)
    assert adapter.holder(WS, "q:alpha:btq-1:1:9") is None
    assert writes(world) == ["create"]


def _approval_pair(world: World) -> tuple[str, str]:
    """An approval asked twice for one ref, by a crash inside `bd create`: (canonical, duplicate)."""
    asked = json.dumps({f: ([] if f == "refs" else f"the {f}") for f in ASK_FIELDS})
    dies_mid_create(world, ref="a:1", kind="kind:approval", metadata={"ask": asked})
    world.before("create", world.commit_orphans)
    canonical = ask(adapter_for(world), ref="a:1", kind="kind:approval", metadata={"ask": asked})
    [duplicate] = [b for b in holders(world, "a:1") if b != canonical]
    return canonical, duplicate


def _readout(queue: FakeQueue, bead: str) -> tuple[wbtq.Readout, Any, tuple[str | None, list[str]]]:
    """approve-bead's readout of `bead` as the fake holds it, with its show and recomputed digest."""
    shown = queue.show(bead)
    digest, gaps = queue.ask_digest(shown)
    assert digest is not None and gaps == []
    decided = "approved_by" in shown.get("metadata", {})
    fields = {"format": 1, "busy": False, "id": bead, "status": shown["status"], "labels": ["kind:approval"],
              "kind_approval": True, "digest": digest, "posted_digest": digest, "gaps": [],
              "design_review_valid": None, "adr_revision": None, "approvers": [APPROVER],
              "decision": "approve" if decided else None, "approved_by": APPROVER if decided else None,
              "approved_digest": digest if decided else None, "denied_by": None, "denied_digest": None,
              "via": "marmot", "via_ref": None, "decided": decided, "gate_valid": True, "gate_reasons": [],
              "links": [], "readout": {"title": [], "description": [], "ask": []}}
    readout = wbtq.parse_readout(json.dumps(fields).encode())
    assert isinstance(readout, wbtq.Readout)
    return readout, shown, (digest, gaps)


def test_a_verdict_never_uses_a_duplicate_even_an_approved_one(world: World,
                                                               caplog: pytest.LogCaptureFixture) -> None:
    """The operator approves the duplicate approval. Read through `holder`, as every verdict is, the
    canonical one is still open: Waiting, and the operator is asked again. The duplicate's approval is
    logged as a fixed text and a count, and the duplicate is never written to."""
    canonical, duplicate = _approval_pair(world)
    assert canonical == earliest(world, "a:1")
    queue = factory(world)(WS, "approve-bead")
    world.beads[duplicate].metadata["approved_by"] = APPROVER        # approve-bead decides, and closes it
    world.close(duplicate)
    world.closed_reasons[duplicate] = "approved"
    readout, shown, recomputed = _readout(queue, duplicate)
    expect = approvals.Expected(duplicate, recomputed[0] or "", lambda value: value == "the effect")
    assert approvals.verdict(readout, shown, recomputed, expect, None) == approvals.Approved()  # if used
    since = len(world.calls)
    with caplog.at_level("WARNING"):
        bead = adapter_for(world).holder(WS, "a:1")
    assert bead is not None and bead == canonical
    readout, shown, recomputed = _readout(queue, bead)
    expect = approvals.Expected(bead, recomputed[0] or "", lambda value: value == "the effect")
    assert approvals.verdict(readout, shown, recomputed, expect, None) == approvals.Waiting()
    assert writes(world, since) == []
    assert DUPLICATE_DECIDED % 1 in caplog.messages
    assert all("a:1" not in m for m in caplog.messages)


def test_a_duplicate_approval_is_left_open_and_never_written(world: World,
                                                            caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        canonical, duplicate = _approval_pair(world)
    assert holders(world, "a:1", status="open") == sorted([canonical, duplicate])
    assert world.beads[duplicate].comments == [] and world.closed_reasons == {}
    assert DUPLICATE_LEFT_OPEN % 1 in caplog.messages


def test_a_duplicate_whose_close_fails_stays_a_duplicate_and_is_never_used(
        world: World, caplog: pytest.LogCaptureFixture) -> None:
    """Closing the duplicate fails: create still returns the canonical bead, and so does every later
    create and lookup. The duplicate is closed by a later one, once bd lets it."""
    dies_mid_create(world)
    world.before("create", world.commit_orphans)
    world.fault("close", RuntimeError("bd: timed out"), times=3)
    with caplog.at_level("WARNING"):
        canonical = ask(adapter_for(world))
    [duplicate] = [b for b in holders(world) if b != canonical]
    assert canonical == earliest(world)
    assert world.beads[duplicate].status == "open"
    assert DUPLICATE_NOT_CLOSED % 1 in caplog.messages
    for _ in range(2):
        assert adapter_for(world).holder(WS, REF) == canonical
        assert ask(adapter_for(world)) == canonical
    assert world.beads[duplicate].status == "closed"      # the fourth close went through
    assert len(world.beads[duplicate].comments) == 1
    adapter_for(world).close_question(WS, canonical, "Yes.")
    assert world.beads[canonical].status == "closed"
    assert holders(world, status="open") == []


# --- the canonical holder is pinned at its first resolution (c3) ---

def _approve(world: World, bead: str) -> None:
    world.beads[bead].metadata["approved_by"] = APPROVER        # approve-bead decides, and closes it
    world.close(bead)
    world.closed_reasons[bead] = "approved"


@pytest.mark.parametrize("kind", ["kind:question", "kind:approval"])
def test_a_late_orphan_created_earlier_never_takes_the_canonical_over(
        world: World, kind: str, caplog: pytest.LogCaptureFixture) -> None:
    """The review's repro. The replacement resolves the ref while the dead wsd's `bd create` still runs, so
    its own bead is the only holder, and is pinned. The orphan then commits with an earlier created_at:
    by created_at alone it would be canonical. It is a duplicate, and an approval of it is never Approved."""
    asked = json.dumps({f: ([] if f == "refs" else f"the {f}") for f in ASK_FIELDS})
    fields: dict[str, Any] = {"ref": "a:1", "kind": kind, "metadata": {"ask": asked}}
    dies_mid_create(world, **fields)
    first = ask(adapter_for(world), **fields)
    world.commit_orphans()
    [orphan] = [b for b in holders(world, "a:1") if b != first]
    assert earliest(world, "a:1") == orphan
    if kind == "kind:approval":
        _approve(world, orphan)
    with caplog.at_level("WARNING"):
        for _ in range(3):            # restarts
            assert ask(adapter_for(world), **fields) == first
            assert adapter_for(world).holder(WS, "a:1") == first
    if kind == "kind:question":
        assert world.closed_reasons == {orphan: DUPLICATE_OF.format(canonical=first)}
        assert holders(world, "a:1", status="open") == [first]
        return
    assert DUPLICATE_DECIDED % 1 in caplog.messages
    assert writes(world) == ["create", "create"]        # the dead wsd's and the replacement's: nothing after
    queue = factory(world)(WS, "approve-bead")
    bead = adapter_for(world).holder(WS, "a:1")
    assert bead is not None
    readout, shown, recomputed = _readout(queue, bead)
    expect = approvals.Expected(bead, recomputed[0] or "", lambda value: value == "the effect")
    assert approvals.verdict(readout, shown, recomputed, expect, None) == approvals.Waiting()


def test_a_same_second_tie_never_moves_the_canonical(world: World) -> None:
    """Whole-second created_at: the replacement's btq-h9 is pinned, and the orphan's btq-h10, committed in
    the same second, sorts first by ID. It is still the duplicate."""
    world.tick = timedelta(0)
    for n in range(8):
        ask(adapter_for(world), ref=f"q:alpha:btq-2:1:{n}")
    world.orphan_next_create = True
    with pytest.raises(BeadsUnavailable):
        ask(adapter_for(world))
    first = ask(adapter_for(world))
    world.commit_orphans()
    assert (first, earliest(world)) == ("btq-h9", "btq-h10")
    for _ in range(3):
        assert ask(adapter_for(world)) == first
        assert adapter_for(world).holder(WS, REF) == first
    assert holders(world, status="open") == [first]


@pytest.mark.parametrize("via", ["create", "holder"])
def test_a_crash_before_the_pin_resolves_again_and_then_pins(world: World, via: str) -> None:
    """The first resolution dies before its pin commits: nothing was returned. The next one, a create or a
    lookup, resolves by created_at and pins; from then on no holder, however early, takes over."""
    dies_mid_create(world)
    world.before("create", world.commit_orphans)
    world.pins.fail = 1
    with pytest.raises(BeadsUnavailable):
        ask(adapter_for(world))
    assert world.pins.pinned(WS, REF) is None and len(holders(world)) == 2
    canonical = earliest(world)
    got = ask(adapter_for(world)) if via == "create" else adapter_for(world).holder(WS, REF)
    assert got == canonical and world.pins.pinned(WS, REF) == canonical
    [other] = [b for b in holders(world) if b != canonical]
    world.beads[other].created_at = "2026-01-01T00:00:00Z"       # earlier than any: it changes nothing
    assert ask(adapter_for(world)) == canonical
    assert adapter_for(world).holder(WS, REF) == canonical


@pytest.mark.parametrize("change", ["ref edited", "bead gone", "workstream label dropped"])
def test_a_pinned_bead_that_no_longer_holds_its_ref_fails_closed(world: World, change: str) -> None:
    """The pin is never re-picked: a pinned bead no longer listed with its ref is refused by create and
    lookup alike, with no bd write, even with another holder there to choose."""
    first = ask(adapter_for(world))
    second = ask(adapter_for(world), ref="q:alpha:btq-1:1:2")
    world.beads[second].metadata[REF_KEY] = REF       # another holder, which must not be picked
    if change == "ref edited":
        world.beads[first].metadata[REF_KEY] = "q:alpha:btq-1:1:9"
    elif change == "bead gone":
        del world.beads[first]
    else:
        world.beads[first].labels.remove(f"ws:{WS}")
    since = len(world.calls)
    with pytest.raises(CreateUnverified, match="pinned"):
        ask(adapter_for(world))
    with pytest.raises(CreateUnverified, match="pinned"):
        adapter_for(world).holder(WS, REF)
    assert writes(world, since) == []
    assert world.pins.pinned(WS, REF) == first


def test_without_a_pin_store_nothing_is_created(world: World) -> None:
    with pytest.raises(BeadsUnavailable, match="pin"):
        ask(BeadsAdapter(factory(world)))
    with pytest.raises(BeadsUnavailable, match="pin"):
        BeadsAdapter(factory(world)).holder(WS, REF)
    assert world.calls == []


def test_a_duplicate_closed_before_its_cleanup_gets_no_comment(world: World,
                                                              caplog: pytest.LogCaptureFixture) -> None:
    """The operator closes the duplicate question between the list and the cleanup's show: nothing is
    written to it, and its close counts as a decision on a duplicate, ignored."""
    first = ask(adapter_for(world))
    second = ask(adapter_for(world), ref="q:alpha:btq-1:1:2")
    world.beads[second].metadata[REF_KEY] = REF
    world.before("show", lambda: world.close(second), nth=2)      # 1: the canonical's read-back
    since = len(world.calls)
    with caplog.at_level("WARNING"):
        assert ask(adapter_for(world)) == first
    assert writes(world, since) == []
    assert world.beads[second].comments == []
    assert DUPLICATE_DECIDED % 1 in caplog.messages


@pytest.mark.parametrize("via", ["create", "holder"])
def test_a_pin_refused_for_another_bead_fails_closed(world: World, via: str) -> None:
    """The store already pins the ref to another bead when wsd pins its pick (here set just after wsd read
    no pin): the pin refuses, nothing is returned, and the next call finds the other pin unlisted: PinLost,
    never a re-pick."""
    if via == "holder":
        dies_mid_create(world)
        world.commit_orphans()            # a holder, never pinned
    world.before("list", lambda: world.pins.pins.__setitem__((WS, REF), "btq-elsewhere"))
    with pytest.raises(BeadsUnavailable, match="pin"):
        ask(adapter_for(world)) if via == "create" else adapter_for(world).holder(WS, REF)
    since = len(world.calls)
    with pytest.raises(CreateUnverified, match="pinned"):
        ask(adapter_for(world)) if via == "create" else adapter_for(world).holder(WS, REF)
    assert writes(world, since) == []
    assert world.pins.pinned(WS, REF) == "btq-elsewhere"
