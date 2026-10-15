import json
import os
import re
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import ASK_FIELDS, FakeQueue, World, factory
from wsd_env import WS, git_repo

from heterodyne.wsd import approvals, btq, gitwip, ids
from heterodyne.wsd import btq as wbtq
from heterodyne.wsd.beads import (
    FENCE_SECONDS,
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
    live_creators,
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


def adapter_for(world: World, **kw: Any) -> BeadsAdapter:
    """An adapter whose create fence asks the fake world which `bd create` processes still run."""
    return BeadsAdapter(factory(world), **{"creators": world.live_creators, **kw})


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
    assert re.fullmatch(r"btq-[0-9a-f]{16}", bead.id)      # the ID is the ref's, in the database's prefix


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


def test_a_ref_held_by_two_beads_is_refused(world: World) -> None:
    adapter = adapter_for(world)
    ask(adapter)
    world.beads[ask(adapter, ref="q:alpha:btq-1:1:2")].metadata[REF_KEY] = "q:alpha:btq-1:1:1"
    with pytest.raises(CreateUnverified):
        ask(adapter)
    assert len(world.created) == 2


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


# --- crash-safe create: the ID is the ref's, and a fence waits out an orphaned `bd create` (c1 B1) ---

def ref_id(world: World, ref: str = "q:alpha:btq-1:1:1") -> str:
    adapter = adapter_for(world)
    return adapter.create_id(WS, ref)


def holders(world: World, ref: str = "q:alpha:btq-1:1:1") -> list[str]:
    return sorted(b.id for b in world.beads.values() if b.metadata.get(REF_KEY) == ref)


class Clock:
    """The fence's clock and pause: each pause moves time on and runs `on_pause` (what happens meanwhile)."""

    def __init__(self, on_pause: Any = None) -> None:
        self.now = 0.0
        self.pauses = 0
        self.on_pause = on_pause

    def __call__(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.pauses += 1
        self.now += seconds
        if self.on_pause is not None:
            self.on_pause()


def dies_mid_create(world: World) -> None:
    """wsd dies inside `bd create`: its lock is gone, and the bd child it started is still running."""
    world.orphan_next_create = True
    with pytest.raises(BeadsUnavailable):
        ask(adapter_for(world))
    assert world.created == [] and len(world.orphans) == 1


def test_the_fence_waits_for_an_orphaned_create_before_listing(world: World) -> None:
    """The replacement takes the lock while the dead creator's child still runs: it waits for the child to
    commit, then finds that bead, and makes none of its own."""
    dies_mid_create(world)
    clock = Clock(on_pause=world.commit_orphans)
    replacement = adapter_for(world, clock=clock, pause=clock.pause)
    since = len(world.calls)
    bead = ask(replacement)
    assert clock.pauses == 1
    assert holders(world) == [bead] == world.created
    assert writes(world, since) == []


@pytest.mark.parametrize("call, nth", [("list", 1), ("show", 1), ("create", 1), ("show", 2)],
                         ids=["before the list", "after the list", "after the existence check",
                              "after the create"])
def test_an_orphan_the_fence_misses_still_leaves_one_bead(world: World, call: str, nth: int) -> None:
    """Even if the fence were blind, the replacement lists before the child commits, and both write the
    same ID: bd's upsert leaves exactly one bead holding the ref, as asked."""
    dies_mid_create(world)
    world.before(call, world.commit_orphans, nth=nth)
    bead = ask(adapter_for(world, creators=lambda worker: 0))
    assert world.orphans == []
    assert holders(world) == [bead]
    assert len(world.created) == 1


def test_a_live_orphan_times_out_closed_with_no_create(world: World) -> None:
    dies_mid_create(world)
    clock = Clock()
    seen: list[str] = []

    def creators(worker: str) -> int:
        seen.append(worker)
        return 1

    since = len(world.calls)
    with pytest.raises(BeadsUnavailable, match="still running"):
        ask(adapter_for(world, creators=creators, clock=clock, pause=clock.pause))
    assert clock.now >= FENCE_SECONDS
    assert [c for _, c in world.calls[since:] if c in ("list", "create")] == []
    assert set(seen) == {BeadsAdapter(factory(world)).ws_queue(WS).worker}
    assert holders(world) == []


def test_a_failed_probe_is_never_clear(world: World) -> None:
    def creators(worker: str) -> int:
        raise BeadsUnavailable("ps failed")

    with pytest.raises(BeadsUnavailable):
        ask(adapter_for(world, creators=creators))
    assert [c for _, c in world.calls if c in ("list", "create")] == []


def test_the_prefix_is_the_databases(world: World) -> None:
    world.prefix = "zq"
    bead = ask(adapter_for(world))
    assert re.fullmatch(r"zq-[0-9a-f]{16}", bead)


@pytest.mark.parametrize("value", ["", "Not A Prefix", "a-", 7, None])
def test_an_unknown_prefix_never_creates(world: World, monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    real = FakeQueue._bd      # pyright: ignore[reportPrivateUsage]

    def config(self: FakeQueue, args: list[str]) -> Any:
        if args[0] == "config":
            return {"key": "issue_prefix", "schema_version": 1, "value": value}
        return real(self, args)

    monkeypatch.setattr(FakeQueue, "_bd", config)
    with pytest.raises(BeadsUnavailable):
        ask(adapter_for(world))
    assert writes(world) == []


def test_a_bead_already_at_the_id_with_other_content_is_never_overwritten(world: World) -> None:
    taken = world.add(ref_id(world), kind="question", title="someone else's")
    since = len(world.calls)
    with pytest.raises(CreateUnverified):
        ask(adapter_for(world))
    assert writes(world, since) == [] and taken.title == "someone else's"


def test_the_id_depends_on_the_workstream_and_the_ref(world: World) -> None:
    adapter = adapter_for(world)
    ids_ = {adapter.create_id(ws, ref) for ws in ("alpha", "beta") for ref in ("a:1", "a:2")}
    assert len(ids_) == 4
    assert adapter.create_id("alpha", "a:1") == adapter_for(world).create_id("alpha", "a:1")


APPROVER = "operator-a"


def _approved(bead: str, status: str, digest: str) -> dict[str, Any]:
    return {"format": 1, "busy": False, "id": bead, "status": status, "labels": ["kind:approval"],
            "kind_approval": True, "digest": digest, "posted_digest": digest, "gaps": [],
            "design_review_valid": None, "adr_revision": None, "approvers": [APPROVER], "decision": "approve",
            "approved_by": APPROVER, "approved_digest": digest, "denied_by": None, "denied_digest": None,
            "via": "marmot", "via_ref": None, "decided": True, "gate_valid": True, "gate_reasons": [],
            "links": [], "readout": {"title": [], "description": [], "ask": []}}


def _readout(fields: dict[str, Any]) -> wbtq.Readout:
    readout = wbtq.parse_readout(json.dumps(fields).encode())
    assert isinstance(readout, wbtq.Readout)
    return readout


@pytest.mark.parametrize("readout", ["decision gone", "decision still read"])
def test_an_approval_reopened_by_a_late_orphan_is_never_approved(world: World, readout: str) -> None:
    """The residual of bd's upsert: an orphaned create that commits after the approval was decided reopens
    the bead and drops the decision's metadata. approve-bead then reads it open, so `verdict` waits (or, on
    a readout that still claims a decision, is Unverified): it is never Approved."""
    asked = json.dumps({f: ([] if f == "refs" else f"the {f}") for f in ASK_FIELDS})
    bead = ask(adapter_for(world), ref="a:1", kind="kind:approval", metadata={"ask": asked})
    late = [world.beads[bead].title, f"--description={world.beads[bead].description}", "--type=task",
            f"--labels=kind:approval,ws:{WS}", "--id", bead, "--metadata",
            json.dumps({"ask": asked, REF_KEY: "a:1"})]
    queue = factory(world)(WS, "approve-bead")
    digest, gaps = queue.ask_digest(queue.show(bead))
    assert digest is not None and gaps == []
    expect = approvals.Expected(bead, digest, lambda value: value == "the effect")
    world.beads[bead].metadata["approved_by"] = APPROVER          # approve-bead decides, and closes it
    world.close(bead)
    shown = queue.show(bead)
    assert approvals.verdict(_readout(_approved(bead, "closed", digest)),
                             shown, queue.ask_digest(shown), expect, None) == approvals.Approved()
    world.orphans.append((queue, late))         # an orphaned create of the same ID, committing only now
    world.commit_orphans()
    shown = queue.show(bead)
    assert shown["status"] == "open" and "approved_by" not in shown["metadata"]
    fields = _approved(bead, shown["status"], digest)
    if readout == "decision gone":
        fields.update(decision=None, approved_by=None, approved_digest=None, decided=False)
    found = approvals.verdict(_readout(fields), shown,
                              queue.ask_digest(shown), expect, None)
    assert found == (approvals.Waiting() if readout == "decision gone" else approvals.Unverified(4))


# --- the fence's probe: `ps -ww -A -o pid=,args=`, matched by exact argv tokens ---

W = "wsd:testhost:alpha-ws"


@pytest.mark.parametrize("out, count", [
    (f"  1 /sbin/init\n 42 bd -C /repo --actor {W} create title --json\n", 1),
    (f" 42 /opt/beads/bin/bd -C /my repo --actor {W} create t --json\n", 1),
    (f" 42 bd -C /r --actor {W} create a\n 43 bd -C /r --actor {W} create b\n", 2),
    (f" 42 bd -C /r --actor {W} show btq-1 --json\n", 0),
    (f" 42 bd -C /r --actor {W}-other create t\n", 0),
    (f" 42 bd -C /r --actor x{W} create t\n", 0),
    (f" 42 bdx -C /r --actor {W} create t\n", 0),
    (f" 42 python3 btq --actor {W} create t\n", 0),
    (f" 42 bd -C /r --actor {W}\n", 0),
    (" 42 bd -C /r --actor\n 7 [kthreadd]\n 8\n", 0),
], ids=["one", "full path", "two", "another verb", "longer worker", "prefixed worker", "another binary",
        "not bd", "no verb", "no worker"])
def test_live_creators_matches_exact_tokens(out: str, count: int) -> None:
    assert live_creators(W, run=lambda: out) == count


@pytest.mark.parametrize("out", ["", "  \n", "abc def\n", "42bd create\n", "-1 bd\n"],
                         ids=["empty", "blank", "no pid", "glued", "negative"])
def test_live_creators_refuses_output_it_cannot_parse(out: str) -> None:
    with pytest.raises(BeadsUnavailable):
        live_creators(W, run=lambda: out)


@pytest.mark.parametrize("exc", [OSError("no ps"), subprocess.TimeoutExpired(["ps"], 10),
                                 subprocess.CalledProcessError(1, ["ps"])],
                         ids=["missing", "timeout", "failed"])
def test_live_creators_fails_closed_when_ps_fails(exc: Exception) -> None:
    def run() -> str:
        raise exc

    with pytest.raises(BeadsUnavailable):
        live_creators(W, run=run)


def test_live_creators_sees_a_real_process_through_ps() -> None:
    """The real probe, on this host's ps: a process whose argv is a `bd` create of the worker is counted
    while it lives, and not once it is gone."""
    worker = f"wsd:testhost:probe-{os.getpid()}"
    assert live_creators(worker) == 0
    child = subprocess.Popen(["bd", "-c", "import sys; sys.stdin.read()", "--actor", worker, "create", "t"],
                             executable=sys.executable, stdin=subprocess.PIPE)
    try:
        for _ in range(500):          # until ps shows it: a condition, never a fixed sleep
            if live_creators(worker) == 1:
                break
        assert live_creators(worker) == 1
    finally:
        assert child.stdin is not None
        child.stdin.close()
        child.wait(timeout=30)
    assert live_creators(worker) == 0
