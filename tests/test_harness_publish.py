"""Plan 4c Task 4: `publish`, recovery, `verify`, `remove` and the sweep (r16 §7 Managed installs; plan 4c
E2, E18, E20, E21). Every refusal is asserted by `store_core`'s own constant. A crash is an `os._exit` in a
child process at one of `publish`'s hook points, which proves the recovery logic, not power-loss
durability; the fsync order is checked with a recorder instead."""

import contextlib
import dataclasses
import fcntl
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from harness_env import (
    CODEX_LAYOUT,
    CODEX_TREE,
    FIRST_FILE,
    RewriteAfterFirstFile,
    codex_part,
    fd_count,
    install_name,
    meta_path,
    open_dir,
    published_root,
    record_of,
    rewrite,
    sample_manifest,
    stage,
    staged_root,
    store_of,
    unseal,
)

from heterodyne.harness.resources import store_core as sc

CRASHED = 17
CHILD = """
import json, os, sys
from pathlib import Path
from heterodyne.harness.resources import store_core as sc
op, store, args, point, ready, go = json.loads(sys.argv[1])
def hook(p):
    if p != point:
        return
    if ready < 0:
        os._exit(17)
    os.write(ready, b"x")
    os.read(go, 1)
if op == "publish":
    sc.publish(Path(store), *args, hook=hook)
else:
    sc.remove(Path(store), *args, hook=hook)
"""


@pytest.fixture(autouse=True)
def writable_store(tmp_path: Path) -> Iterator[None]:
    """Make the store writable again at teardown, so the temp dir can be removed."""
    yield
    unseal(tmp_path / "store")


def staged_codex(tmp_path: Path) -> sc.Staged:
    return stage(tmp_path, CODEX_LAYOUT, [codex_part()])


def published(tmp_path: Path) -> tuple[sc.Staged, str]:
    staged = staged_codex(tmp_path)
    return staged, sc.publish(store_of(tmp_path), staged.id, staged.digest)


def crash(tmp_path: Path, op: str, args: list[str], point: str) -> None:
    """`op` (`publish` or `remove`) in a child that dies at `point`."""
    out = subprocess.run([sys.executable, "-c", CHILD, json.dumps([op, str(store_of(tmp_path)), args, point,
                                                                    -1, -1])],
                         capture_output=True, timeout=120, check=False)
    assert out.returncode == CRASHED, out.stderr.decode(errors="replace")[-2000:]


@contextlib.contextmanager
def paused(tmp_path: Path, args: list[str], point: str) -> Iterator[tuple[subprocess.Popen[bytes], int]]:
    """A child `publish` held at `point` until the test writes to the yielded fd (or kills the child)."""
    ready_r, ready_w = os.pipe()
    go_r, go_w = os.pipe()
    proc = subprocess.Popen([sys.executable, "-c", CHILD,
                             json.dumps(["publish", str(store_of(tmp_path)), args, point, ready_w, go_r])],
                            pass_fds=(ready_w, go_r))
    os.close(ready_w)
    os.close(go_r)
    try:
        assert os.read(ready_r, 1) == b"x"         # b"" if the child died before the barrier
        yield proc, go_w
    finally:
        os.close(ready_r)
        os.close(go_w)
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=120)


def refused(fn: Callable[[], object]) -> str:
    with pytest.raises(sc.Refused) as e:
        fn()
    assert str(e.value) == e.value.constant
    return e.value.constant


def modes(root: Path) -> dict[str, int]:
    fd = open_dir(root)
    try:
        with contextlib.closing(sc.walk(fd)) as entries:
            return {path: st.mode for path, st, _ in entries} | {"": sc.statx_at(fd, "").mode}
    finally:
        os.close(fd)


def stored_record(tmp_path: Path, name: str) -> sc.Record:
    doc = json.loads(meta_path(tmp_path, name, "record").read_bytes())
    return {k: (v[0], v[1], v[2]) for k, v in doc["record"].items()}


def check_published(tmp_path: Path, name: str) -> None:
    """`check_fast` on the install against its meta manifest and record."""
    m = sc.parse(meta_path(tmp_path, name, "manifest").read_bytes())
    fd = open_dir(published_root(tmp_path, name))
    try:
        sc.check_fast(fd, m, stored_record(tmp_path, name))
    finally:
        os.close(fd)


def assert_gone(tmp_path: Path, name: str) -> None:
    assert not os.path.lexists(published_root(tmp_path, name))
    assert not os.path.lexists(meta_path(tmp_path, name, "manifest"))
    assert not os.path.lexists(meta_path(tmp_path, name, "record"))
    assert sc.list_installs(store_of(tmp_path)) == []


def other_digest() -> str:
    return sc.digest(sample_manifest())


# --- publish ---

def test_publish_with_the_staged_digest_names_the_install_and_changes_no_mode(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    before = modes(staged_root(tmp_path, staged))
    fds = fd_count()
    name = sc.publish(store_of(tmp_path), staged.id, staged.digest)
    assert fd_count() == fds
    version = sc.parse(meta_path(tmp_path, name, "manifest").read_bytes()).version
    assert name == f"{version}-{staged.digest.removeprefix('sha256:')[:12]}"
    assert name == sc.install_name(version, staged.digest)
    assert modes(published_root(tmp_path, name)) == before
    assert before[""] == 0o555
    assert not os.path.lexists(staged_root(tmp_path, staged))
    assert not os.path.lexists(store_of(tmp_path) / ".staged" / f"{staged.id}.json")


def test_the_published_tree_passes_check_fast_at_once_and_its_manifest_digest_is_the_approved_one(
        tmp_path: Path) -> None:
    staged, name = published(tmp_path)
    check_published(tmp_path, name)
    assert sc.digest(sc.parse(meta_path(tmp_path, name, "manifest").read_bytes())) == staged.digest
    assert sc.state(store_of(tmp_path), "codex", name) == "published"
    assert sc.outcome(store_of(tmp_path), staged.id) == f"published:{name}"
    [info] = sc.list_installs(store_of(tmp_path))
    assert (info["name"], info["adapter"], info["digest"], info["state"]) == (name, "codex", staged.digest,
                                                                              "published")
    assert (info["entries"], info["size"]) == (staged.entries, staged.size)


def test_publish_with_another_digest_is_refused_and_staging_is_kept(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    assert refused(lambda: sc.publish(store, staged.id, other_digest())) == sc.DIGEST_MISMATCH
    assert staged_root(tmp_path, staged).is_dir()
    assert sc.outcome(store, staged.id) == "staged"
    assert sc.list_installs(store) == []
    assert sc.publish(store, staged.id, staged.digest)


def test_publish_of_an_unknown_staging_is_refused(tmp_path: Path) -> None:
    staged_codex(tmp_path)
    store = store_of(tmp_path)
    assert refused(lambda: sc.publish(store, secrets.token_hex(16), other_digest())) == sc.NO_SUCH_STAGING
    assert refused(lambda: sc.publish(store, "../x", other_digest())) == sc.NO_SUCH_STAGING
    assert sc.outcome(store, secrets.token_hex(16)) is None


def test_the_record_matches_a_fresh_walk_and_lies_outside_the_install(tmp_path: Path) -> None:
    _, name = published(tmp_path)
    root = published_root(tmp_path, name)
    assert stored_record(tmp_path, name) == record_of(root)
    record = meta_path(tmp_path, name, "record").resolve()
    assert root.resolve() not in record.parents
    assert meta_path(tmp_path, name, "record").stat().st_mode & 0o777 == 0o444
    assert meta_path(tmp_path, name, "manifest").stat().st_mode & 0o777 == 0o444


GAPS = {
    # after R1, before the rehash: the rehash sees the bytes
    "R1": sc.CONTENT_CHANGED,
    # after a file was rehashed: R2 is not R1
    "rehash": sc.CHANGED_DURING_PUBLISH,
    # between R2 and the rename, and between the rename and the final check_fast: the check against R2
    "R2": sc.CHANGED_DURING_PUBLISH,
    "P1.intent": sc.CHANGED_DURING_PUBLISH,
    "P2": sc.CHANGED_DURING_PUBLISH,
}


@pytest.mark.parametrize("gap", list(GAPS))
def test_a_same_size_rewrite_at_any_point_of_publish_is_refused(tmp_path: Path, gap: str) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    staging_file = staged_root(tmp_path, staged) / FIRST_FILE
    installed_file = published_root(tmp_path, name) / FIRST_FILE
    read: sc.Reader = os.read

    def hook(point: str) -> None:
        if point == gap:
            rewrite(installed_file if gap == "P2" else staging_file)

    if gap == "rehash":
        read = RewriteAfterFirstFile(staging_file)
    fds = fd_count()
    assert refused(lambda: sc.publish(store, staged.id, staged.digest, hook=hook, read=read)) == GAPS[gap]
    assert fd_count() == fds
    assert_gone(tmp_path, name)
    assert not os.path.lexists(staged_root(tmp_path, staged))
    assert sc.outcome(store, staged.id) == f"failed:{GAPS[gap]}"
    assert refused(lambda: sc.publish(store, staged.id, staged.digest)) == GAPS[gap]
    assert sc.recover(store) == []


def test_a_chmod_and_restore_during_publish_is_refused(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    name = install_name(tmp_path, staged)
    path = staged_root(tmp_path, staged) / "cli" / "codex-resources" / "voice" / "README"

    def hook(point: str) -> None:
        if point == "R2":
            path.chmod(0o644)
            path.chmod(0o444)

    store = store_of(tmp_path)
    assert refused(lambda: sc.publish(store, staged.id, staged.digest, hook=hook)) == (
        sc.CHANGED_DURING_PUBLISH)
    assert_gone(tmp_path, name)


def test_only_the_roots_change_time_may_move_at_the_rename(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    name = install_name(tmp_path, staged)

    def statx(dirfd: int, entry: str) -> sc.Stat:
        st = sc.statx_at(dirfd, entry)
        return dataclasses.replace(st, ino=st.ino + 1) if entry == name else st

    store = store_of(tmp_path)
    assert refused(lambda: sc.publish(store, staged.id, staged.digest, statx=statx)) == (
        sc.CHANGED_DURING_PUBLISH)
    assert_gone(tmp_path, name)
    assert sc.outcome(store, staged.id) == f"failed:{sc.CHANGED_DURING_PUBLISH}"


# --- crashes and recovery ---

CRASH_POINTS = {
    "P1.record": "restarted",   # the intent's record, not yet the intent
    "P1.intent": "restarted",   # the intent; the tree is still in .staging
    "P2.renamed": "published",  # the rename, before the root is 0555 again
    "P2": "published",          # the rename
    "P3": "published",
    "P4": "published",
    "P5": "published",          # the receipt
    "P6.staged": "published",   # the staged manifest removed, not the intent
    "P6.staging": "published",
    "P6.record": "published",   # the intent's record removed, not the intent
}


@pytest.mark.parametrize("point", list(CRASH_POINTS))
def test_a_crash_after_each_write_recovers_to_one_published_install_or_none(tmp_path: Path,
                                                                           point: str) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], point)
    if published_root(tmp_path, name).exists() or meta_path(tmp_path, name, "manifest").exists():
        assert sc.state(store, "codex", name) != "published"     # an intent is still there
    assert sc.recover(store) == [(staged.id, CRASH_POINTS[point])]
    assert sc.recover(store) == []
    assert sc.publish(store, staged.id, staged.digest) == name
    assert sc.state(store, "codex", name) == "published"
    check_published(tmp_path, name)
    assert [i["name"] for i in sc.list_installs(store)] == [name]
    assert list((store / ".publishing").iterdir()) == []
    assert not os.path.lexists(store / ".staged" / f"{staged.id}.json")
    assert [p for p in (store / ".staging").iterdir() if p.name.startswith(staged.id)] == []


def test_recovery_after_the_staged_manifest_is_gone_uses_the_committed_one(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], "P6.staged")
    assert not os.path.lexists(store / ".staged" / f"{staged.id}.json")
    assert sc.state(store, "codex", name) == "publishing"
    assert sc.recover(store) == [(staged.id, "published")]
    assert sc.state(store, "codex", name) == "published"
    assert sc.outcome(store, staged.id) == f"published:{name}"


def test_a_retry_after_publish_completed_returns_the_install_from_the_receipt(tmp_path: Path) -> None:
    staged, name = published(tmp_path)
    store = store_of(tmp_path)
    assert sc.outcome(store, staged.id) == f"published:{name}"
    assert sc.publish(store, staged.id, staged.digest) == name
    assert refused(lambda: sc.publish(store, staged.id, other_digest())) == sc.DIGEST_MISMATCH


def test_publishing_twice_with_the_same_staging_id_returns_the_same_install(tmp_path: Path) -> None:
    staged, name = published(tmp_path)
    store = store_of(tmp_path)
    record = meta_path(tmp_path, name, "record").read_bytes()
    assert sc.publish(store, staged.id, staged.digest) == name
    assert meta_path(tmp_path, name, "record").read_bytes() == record
    assert [i["name"] for i in sc.list_installs(store)] == [name]


@pytest.mark.parametrize("when", ["before", "rehash", "after"])
def test_a_rewrite_during_recovery_is_refused_and_never_becomes_the_baseline(tmp_path: Path,
                                                                             when: str) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], "P3")
    path = published_root(tmp_path, name) / FIRST_FILE
    read: sc.Reader = os.read

    def hook(point: str) -> None:
        if point == "RECOVER.REHASHED" and when == "after":
            rewrite(path)

    if when == "before":
        rewrite(path)
    elif when == "rehash":
        read = RewriteAfterFirstFile(path)
    assert sc.recover(store, hook=hook, read=read) == [(staged.id, f"failed:{sc.RECOVERY_FAILED}")]
    assert_gone(tmp_path, name)
    assert sc.outcome(store, staged.id) == f"failed:{sc.RECOVERY_FAILED}"
    assert refused(lambda: sc.publish(store, staged.id, staged.digest)) == sc.RECOVERY_FAILED


def test_recovery_never_touches_an_install_the_intent_does_not_identify(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], "P2")
    root = published_root(tmp_path, name)
    root.chmod(0o755)
    root.rename(tmp_path / "moved")
    root.mkdir()
    (root / "theirs").write_bytes(b"another install\n")
    assert sc.recover(store) == [(staged.id, f"failed:{sc.RECOVERY_FAILED}")]
    assert (root / "theirs").read_bytes() == b"another install\n"
    assert (tmp_path / "moved" / FIRST_FILE).is_file()
    unseal(tmp_path / "moved")


def test_overlapping_publish_and_recovery_are_serialized(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    intent = store / ".publishing" / f"{staged.id}.json"
    with paused(tmp_path, [staged.id, staged.digest], "P1.intent") as (proc, go):
        before = intent.read_bytes()
        assert refused(lambda: sc.recover(store, deadline=0.2)) == sc.STORE_BUSY
        assert refused(lambda: sc.state(store, "codex", name, deadline=0.2)) == sc.STORE_BUSY
        assert intent.read_bytes() == before
        results: list[list[tuple[str, str]]] = []
        waiter = threading.Thread(target=lambda: results.append(sc.recover(store, deadline=120)))
        waiter.start()
        os.write(go, b"g")
        assert proc.wait(timeout=120) == 0
        waiter.join(timeout=120)
    assert results == [[]]
    assert sc.state(store, "codex", name) == "published"

    again = staged_codex(tmp_path / "again")
    store2 = store_of(tmp_path / "again")
    with paused(tmp_path / "again", [again.id, again.digest], "P1.intent") as (proc, _):
        proc.kill()
        assert proc.wait(timeout=120) < 0
    assert sc.recover(store2) == [(again.id, "restarted")]
    assert sc.recover(store2) == []
    unseal(store2)


def test_two_stagings_of_the_same_name_publish_one_and_refuse_the_other(tmp_path: Path) -> None:
    first = staged_codex(tmp_path)
    second = staged_codex(tmp_path)
    assert first.digest == second.digest
    store = store_of(tmp_path)
    name = install_name(tmp_path, first)
    errors: list[str] = []

    def other() -> None:
        try:
            sc.publish(store, second.id, second.digest, deadline=120)
        except sc.Refused as exc:
            errors.append(exc.constant)

    with paused(tmp_path, [first.id, first.digest], "P1.intent") as (proc, go):
        waiter = threading.Thread(target=other)
        waiter.start()
        os.write(go, b"g")
        assert proc.wait(timeout=120) == 0
        waiter.join(timeout=120)
    assert errors == [sc.NAME_CONFLICT]
    assert sc.state(store, "codex", name) == "published"
    assert sc.outcome(store, first.id) == f"published:{name}"
    assert sc.outcome(store, second.id) == f"failed:{sc.NAME_CONFLICT}"
    check_published(tmp_path, name)


def test_a_conflicting_install_name_is_refused(tmp_path: Path) -> None:
    store = store_of(tmp_path)
    first, name = published(tmp_path)
    record = meta_path(tmp_path, name, "record").read_bytes()
    second = staged_codex(tmp_path)
    assert refused(lambda: sc.publish(store, second.id, second.digest)) == sc.NAME_CONFLICT
    assert sc.state(store, "codex", name) == "published"
    assert meta_path(tmp_path, name, "record").read_bytes() == record

    # a name taken between the check and the rename: RENAME_NOREPLACE refuses, and the taker is untouched
    third = staged_codex(tmp_path / "third")
    name3 = install_name(tmp_path / "third", third)
    theirs = published_root(tmp_path / "third", name3)

    def hook(point: str) -> None:
        if point == "P1.intent":
            theirs.mkdir(parents=True)
            (theirs / "theirs").write_bytes(b"x")

    store3 = store_of(tmp_path / "third")
    assert refused(lambda: sc.publish(store3, third.id, third.digest, hook=hook)) == sc.NAME_CONFLICT
    assert (theirs / "theirs").read_bytes() == b"x"
    assert sc.outcome(store3, third.id) == f"failed:{sc.NAME_CONFLICT}"
    assert sc.recover(store3) == []
    unseal(store3)
    assert first.id != second.id


def test_meta_files_left_by_an_unfinished_remove_are_a_name_conflict(tmp_path: Path) -> None:
    store = store_of(tmp_path)
    _, name = published(tmp_path)
    crash(tmp_path, "remove", ["codex", name], "REMOVE.tree")
    again = staged_codex(tmp_path)
    assert refused(lambda: sc.publish(store, again.id, again.digest)) == sc.NAME_CONFLICT


def test_the_store_lock_times_out_as_store_busy(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    with sc.store_lock(store):
        assert refused(lambda: sc.publish(store, staged.id, staged.digest, deadline=0.1)) == sc.STORE_BUSY
        assert refused(lambda: sc.outcome(store, staged.id, deadline=0.1)) == sc.STORE_BUSY
    with sc.store_lock(store, shared=True):
        assert sc.outcome(store, staged.id, deadline=0.1) == "staged"
        assert refused(lambda: sc.recover(store, deadline=0.1)) == sc.STORE_BUSY
    assert sc.outcome(store, staged.id) == "staged"


def test_an_install_whose_meta_disagrees_with_its_intent_is_broken_never_published(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], "P6.staged")
    manifest = meta_path(tmp_path, name, "manifest")
    manifest.chmod(0o644)
    manifest.write_bytes(sc.canonical(sample_manifest()))
    assert sc.state(store, "codex", name) == "publishing"
    sc.recover(store)
    assert sc.state(store, "codex", name) == "broken"
    assert [i["state"] for i in sc.list_installs(store)] == ["broken"]
    assert refused(lambda: sc.publish(store, staged.id, staged.digest)) == sc.INSTALL_BROKEN


def test_every_phase_fsyncs_the_file_and_its_directory(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    real = os.fsync
    base = str(store.resolve())
    seen: list[str] = []

    def fsync(fd: int) -> None:
        path = str(Path(f"/proc/self/fd/{fd}").readlink())
        seen.append(path.removeprefix(base).lstrip("/"))
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    sc.publish(store, staged.id, staged.digest)
    monkeypatch.setattr(os, "fsync", real)
    i, m = staged.id, f"codex/.meta/{name}"
    assert seen == [
        "",                                                     # .publishing made
        f".publishing/{i}.record.json.tmp", ".publishing",      # P1
        f".publishing/{i}.json.tmp", ".publishing",
        "",                                                     # codex made
        "codex", ".staging", f"codex/{name}",                   # P2: both parents, then the root's mode
        "codex",                                                # .meta made
        f"{m}.manifest.json.tmp", "codex/.meta",                # P3
        f"{m}.record.json.tmp", "codex/.meta",                  # P4
        "",                                                     # .published made
        f".published/{i}.json.tmp", ".published",               # P5
        ".staged", ".staging", ".publishing", ".publishing",    # P6
    ]


# --- verify ---

def test_verify_finds_a_rewrite_and_a_matching_rehash_after_a_device_change_rerecords(tmp_path: Path) -> None:
    store = store_of(tmp_path)
    _, name = published(tmp_path)
    # a reboot that renumbers the disk: every recorded device differs from the tree's
    record = meta_path(tmp_path, name, "record")
    doc = json.loads(record.read_bytes())
    doc["record"] = {k: [v[0] + 1, v[1], v[2]] for k, v in doc["record"].items()}
    record.chmod(0o644)
    record.write_bytes(json.dumps(doc).encode())
    receipt = next((store / ".published").iterdir())
    rdoc = json.loads(receipt.read_bytes())
    rdoc["root"][0] += 1
    receipt.chmod(0o644)
    receipt.write_bytes(json.dumps(rdoc).encode())
    with pytest.raises(sc.Mismatch):
        check_published(tmp_path, name)
    assert sc.state(store, "codex", name) == "broken"
    sc.verify(store, "codex", name)
    check_published(tmp_path, name)
    assert sc.state(store, "codex", name) == "published"

    rewrite(published_root(tmp_path, name) / FIRST_FILE)
    before = record.read_bytes()
    assert refused(lambda: sc.verify(store, "codex", name)) == sc.CONTENT_CHANGED
    assert record.read_bytes() == before


def test_verify_rerecords_a_tree_whose_bytes_were_restored(tmp_path: Path) -> None:
    store = store_of(tmp_path)
    _, name = published(tmp_path)
    path = published_root(tmp_path, name) / FIRST_FILE
    rewrite(path)
    original = CODEX_TREE["bin/codex"]
    assert isinstance(original, bytes)
    rewrite(path, original)
    with pytest.raises(sc.Mismatch) as e:
        check_published(tmp_path, name)
    assert e.value.constant == sc.ENTRY_CHANGED
    assert sc.state(store, "codex", name) == "broken"
    fds = fd_count()
    sc.verify(store, "codex", name)
    assert fd_count() == fds
    check_published(tmp_path, name)
    assert sc.state(store, "codex", name) == "published"


@pytest.mark.parametrize("when", ["rehash", "after"])
def test_a_rewrite_during_verify_is_refused_and_the_record_is_kept(tmp_path: Path, when: str) -> None:
    store = store_of(tmp_path)
    _, name = published(tmp_path)
    path = published_root(tmp_path, name) / FIRST_FILE
    before = meta_path(tmp_path, name, "record").read_bytes()
    read: sc.Reader = RewriteAfterFirstFile(path) if when == "rehash" else os.read

    def hook(point: str) -> None:
        if point == "VERIFY.REHASHED" and when == "after":
            rewrite(path)

    assert refused(lambda: sc.verify(store, "codex", name, hook=hook, read=read)) == sc.CHANGED_DURING_VERIFY
    assert meta_path(tmp_path, name, "record").read_bytes() == before


def test_verify_of_no_install_is_refused(tmp_path: Path) -> None:
    staged_codex(tmp_path)
    store = store_of(tmp_path)
    assert refused(lambda: sc.verify(store, "codex", "0.160.0-000000000000")) == sc.NO_SUCH_INSTALL
    assert refused(lambda: sc.verify(store, "codex", "../x")) == sc.NO_SUCH_INSTALL


# --- remove ---

@pytest.mark.parametrize("point", ["REMOVE.record", "REMOVE.manifest", "REMOVE.tree"])
def test_remove_completes_a_partial_removal(tmp_path: Path, point: str) -> None:
    store = store_of(tmp_path)
    _, name = published(tmp_path)
    crash(tmp_path, "remove", ["codex", name], point)
    assert sc.state(store, "codex", name) == "broken"
    sc.remove(store, "codex", name)
    assert_gone(tmp_path, name)
    assert list((store / ".published").iterdir()) == []
    again = staged_codex(tmp_path)
    assert sc.publish(store, again.id, again.digest) == name
    assert sc.state(store, "codex", name) == "published"


def test_remove_leaves_nothing_and_list_no_longer_shows_it(tmp_path: Path) -> None:
    store = store_of(tmp_path)
    staged, name = published(tmp_path)
    fds = fd_count()
    sc.remove(store, "codex", name)
    assert fd_count() == fds
    assert_gone(tmp_path, name)
    assert list((store / ".published").iterdir()) == []
    assert sc.outcome(store, staged.id) is None
    assert refused(lambda: sc.state(store, "codex", name)) == sc.NO_SUCH_INSTALL
    assert refused(lambda: sc.remove(store, "codex", name)) == sc.NO_SUCH_INSTALL


# --- the sweep ---

def test_sweep_removes_only_staging_older_than_its_hours(tmp_path: Path) -> None:
    store = store_of(tmp_path)
    staged = staged_codex(tmp_path)
    now = time.time()
    assert sc.sweep(store, now, 1) == 0
    assert staged_root(tmp_path, staged).is_dir()
    assert sc.sweep(store, now + 3600 + 60, 1) == 1
    assert not os.path.lexists(staged_root(tmp_path, staged))
    assert not os.path.lexists(store / ".staged" / f"{staged.id}.json")
    assert sc.outcome(store, staged.id) is None


def test_sweep_skips_a_staging_that_is_still_being_built(tmp_path: Path) -> None:
    store = store_of(tmp_path)
    staged_codex(tmp_path)
    building = secrets.token_hex(16)
    staging = store / ".staging"
    (staging / building).mkdir()
    (staging / f"{building}.src").mkdir()
    fd = os.open(staging / f"{building}.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert sc.sweep(store, time.time() + 10 * 86400, 24) == 1      # only the finished, old one
        assert (staging / building).is_dir()
    finally:
        os.close(fd)
    # its builder died (the lock is free) and it never committed: a crash leftover, removed at any age
    assert sc.sweep(store, time.time(), 24) == 1
    assert [p for p in staging.iterdir() if p.name.startswith(building)] == []


def test_a_temp_file_left_by_a_crash_mid_write_is_removed_by_recovery(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    publishing = store / ".publishing"
    publishing.mkdir()
    (publishing / f"{staged.id}.record.json.tmp").write_bytes(b"{")
    assert sc.recover(store) == []
    assert list(publishing.iterdir()) == []
    assert sc.outcome(store, staged.id) == "staged"


def test_a_crash_between_the_rename_and_the_roots_seal_recovers_to_a_0555_root(tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], "P2.renamed")
    assert modes(published_root(tmp_path, name))[""] == 0o755
    assert sc.state(store, "codex", name) == "publishing"
    assert sc.recover(store) == [(staged.id, "published")]
    assert modes(published_root(tmp_path, name))[""] == 0o555
    assert sc.state(store, "codex", name) == "published"
    check_published(tmp_path, name)


def test_a_root_that_cant_be_sealed_after_the_rename_is_left_for_recovery(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    real = os.fchmod
    opened = [False]

    def fchmod(fd: int, mode: int) -> None:
        if mode == 0o755 and sc.statx_at(fd, "").mode == 0o555 and not opened[0]:
            opened[0] = True                    # the publish's own opening of the root for the rename
        elif mode == 0o555 and opened[0]:
            raise PermissionError(1, "fchmod")
        real(fd, mode)

    monkeypatch.setattr(os, "fchmod", fchmod)
    assert refused(lambda: sc.publish(store, staged.id, staged.digest)) == sc.PUBLISH_INTERRUPTED
    monkeypatch.setattr(os, "fchmod", real)
    assert modes(published_root(tmp_path, name))[""] == 0o755
    assert not os.path.lexists(meta_path(tmp_path, name, "manifest"))
    assert sc.state(store, "codex", name) == "publishing"
    assert sc.outcome(store, staged.id) == "staged"
    assert sc.recover(store) == [(staged.id, "published")]
    assert modes(published_root(tmp_path, name))[""] == 0o555
    check_published(tmp_path, name)


def test_an_install_whose_root_is_not_0555_is_never_published_or_verified(tmp_path: Path) -> None:
    store = store_of(tmp_path)
    _, name = published(tmp_path)
    published_root(tmp_path, name).chmod(0o755)
    assert sc.state(store, "codex", name) == "broken"
    assert [i["state"] for i in sc.list_installs(store)] == ["broken"]
    assert refused(lambda: sc.verify(store, "codex", name)) == sc.ENTRY_CHANGED
    with pytest.raises(sc.Mismatch) as e:
        check_published(tmp_path, name)
    assert e.value.constant == sc.ENTRY_CHANGED


@pytest.mark.parametrize("point", ["P2.renamed", "P2"])
def test_recovery_makes_the_roots_seal_durable_before_it_commits_the_meta(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], point)
    real = os.fsync
    base = str(store.resolve())
    seen: list[str] = []

    def fsync(fd: int) -> None:
        path = str(Path(f"/proc/self/fd/{fd}").readlink())
        seen.append(path.removeprefix(base).lstrip("/"))
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    assert sc.recover(store) == [(staged.id, "published")]
    monkeypatch.setattr(os, "fsync", real)
    m = f"codex/.meta/{name}"
    assert f"codex/{name}" in seen
    assert seen.index(f"codex/{name}") < seen.index(f"{m}.manifest.json.tmp")
    assert modes(published_root(tmp_path, name))[""] == 0o555


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_recovery_never_changes_a_root_that_replaced_the_install_after_its_check(
        tmp_path: Path, replacement: str) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], "P2.renamed")
    root = published_root(tmp_path, name)
    theirs = tmp_path / "theirs"
    theirs.mkdir(mode=0o755)
    theirs.chmod(0o755)

    def hook(point: str) -> None:
        if point == "RECOVER.FOUND":
            root.rename(tmp_path / "moved")
            if replacement == "directory":
                theirs.rename(root)
            else:
                root.symlink_to(theirs)

    assert sc.recover(store, hook=hook) == [(staged.id, f"failed:{sc.RECOVERY_FAILED}")]
    kept = root if replacement == "directory" else theirs
    assert modes(kept)[""] == 0o755
    assert modes(tmp_path / "moved")[""] == 0o755
    assert sc.outcome(store, staged.id) == f"failed:{sc.RECOVERY_FAILED}"
    unseal(tmp_path / "moved")


@pytest.mark.parametrize("what", ["manifest", "record"])
def test_recovery_refuses_committed_meta_that_conflicts_with_its_intent(tmp_path: Path, what: str) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], "P4")
    path = meta_path(tmp_path, name, what)
    if what == "manifest":
        conflicting = sc.canonical(sample_manifest())
    else:
        doc = json.loads(path.read_bytes())
        doc["record"][""][2] += 1
        conflicting = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    path.chmod(0o644)
    path.write_bytes(conflicting)
    assert sc.recover(store) == [(staged.id, f"failed:{sc.RECOVERY_FAILED}")]
    assert sc.outcome(store, staged.id) == f"failed:{sc.RECOVERY_FAILED}"
    assert not os.path.lexists(published_root(tmp_path, name))
    if what == "manifest":      # not the intent's, so abandonment keeps it, and it holds the name
        assert path.read_bytes() == conflicting
        assert not os.path.lexists(meta_path(tmp_path, name, "record"))
    else:                       # R2 but for the root's change time: the intent's, so it goes
        assert_gone(tmp_path, name)
        assert sc.list_installs(store) == []


@pytest.mark.parametrize("replaced", ["manifest", "record", "both"])
def test_abandonment_keeps_meta_files_another_install_wrote(tmp_path: Path, replaced: str) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], "P4")
    root = published_root(tmp_path, name)
    root.chmod(0o755)
    root.rename(tmp_path / "moved")
    root.mkdir()
    (root / "theirs").write_bytes(b"another install\n")
    theirs: dict[str, bytes] = {}
    if replaced in ("manifest", "both"):
        theirs["manifest"] = sc.canonical(sample_manifest())
    if replaced in ("record", "both"):
        doc = json.loads(meta_path(tmp_path, name, "record").read_bytes())
        doc["record"][""][:2] = [root.stat().st_dev, root.stat().st_ino]
        theirs["record"] = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    for what, data in theirs.items():
        path = meta_path(tmp_path, name, what)
        path.chmod(0o644)
        path.write_bytes(data)
    assert sc.recover(store) == [(staged.id, f"failed:{sc.RECOVERY_FAILED}")]
    assert (root / "theirs").read_bytes() == b"another install\n"
    for what in ("manifest", "record"):
        path = meta_path(tmp_path, name, what)
        if what in theirs:
            assert path.read_bytes() == theirs[what]
        else:                   # the intent's own, written at P3 or P4
            assert not os.path.lexists(path)
    assert sc.outcome(store, staged.id) == f"failed:{sc.RECOVERY_FAILED}"
    unseal(tmp_path / "moved")


def swap_in_theirs(root: Path, moved: Path) -> Callable[[str], None]:
    """A hook that, once the tree at `root` is open for removal, moves it to `moved` and puts another
    directory with a file at `root`."""
    def hook(point: str) -> None:
        if point == "TREE.OPENED" and not moved.exists():
            root.chmod(0o755)               # a move to another parent rewrites the directory's `..`
            root.rename(moved)
            root.mkdir()
            (root / "theirs").write_bytes(b"another install\n")
    return hook


def test_abandonment_never_removes_a_tree_that_replaced_the_intents_after_it_was_opened(
        tmp_path: Path) -> None:
    staged = staged_codex(tmp_path)
    store = store_of(tmp_path)
    name = install_name(tmp_path, staged)
    crash(tmp_path, "publish", [staged.id, staged.digest], "P3")
    root = published_root(tmp_path, name)
    rewrite(root / FIRST_FILE)                  # recovery refuses, so the publish is abandoned
    hook = swap_in_theirs(root, tmp_path / "moved")
    assert sc.recover(store, hook=hook) == [(staged.id, f"failed:{sc.RECOVERY_FAILED}")]
    assert (root / "theirs").read_bytes() == b"another install\n"
    assert modes(root)[""] == 0o755
    assert list((tmp_path / "moved").iterdir()) == []     # the intent's own tree, emptied through its fd
    unseal(tmp_path / "moved")


def test_remove_never_removes_a_tree_that_replaced_the_install_after_it_was_opened(tmp_path: Path) -> None:
    store = store_of(tmp_path)
    _, name = published(tmp_path)
    root = published_root(tmp_path, name)
    hook = swap_in_theirs(root, tmp_path / "moved")
    assert refused(lambda: sc.remove(store, "codex", name, hook=hook)) == sc.INSTALL_BROKEN
    assert (root / "theirs").read_bytes() == b"another install\n"
    assert modes(root)[""] == 0o755
    assert list((tmp_path / "moved").iterdir()) == []
    assert sc.state(store, "codex", name) == "broken"      # the receipt stays, so it is still visible
    unseal(tmp_path / "moved")


@pytest.mark.parametrize("theirs", ["directory", "file"])
def test_remove_refuses_and_changes_nothing_when_the_install_path_is_not_the_installs(
        tmp_path: Path, theirs: str) -> None:
    store = store_of(tmp_path)
    _, name = published(tmp_path)
    root = published_root(tmp_path, name)
    root.chmod(0o755)
    root.rename(tmp_path / "moved")
    if theirs == "directory":
        root.mkdir()
        (root / "theirs").write_bytes(b"another install\n")
    else:
        root.write_bytes(b"another install\n")
    record = meta_path(tmp_path, name, "record").read_bytes()
    assert refused(lambda: sc.remove(store, "codex", name)) == sc.INSTALL_BROKEN
    assert meta_path(tmp_path, name, "record").read_bytes() == record
    assert (root / "theirs" if theirs == "directory" else root).read_bytes() == b"another install\n"
    unseal(tmp_path / "moved")
