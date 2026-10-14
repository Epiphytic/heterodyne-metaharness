"""Plan 4c Task 3: git content sources checked without git (plan 4c E6). The helper rebuilds the tree ID
from `git archive`'s entries and hashes the commit object itself; these tests compare it with a real `git`
on repositories built in a temp dir. Commit and tree IDs are compared as values and never printed."""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from harness_env import (
    CODEX_LAYOUT,
    CODEX_TREE,
    GIT_ENV,
    git,
    make_repo,
    part_of,
    stage,
    staged_manifest,
    tar_of,
    unseal,
)

from heterodyne.harness.resources import store_core as sc


def blobs(repo: Path, commit: str) -> tuple[bytes, bytes]:
    return git(repo, "archive", "--format=tar", commit), git(repo, "cat-file", "commit", commit)


@pytest.fixture
def fds(tmp_path: Path) -> Iterator[list[int]]:
    opened: list[int] = []
    yield opened
    for fd in opened:
        os.close(fd)


def fd_of(tmp_path: Path, name: str, data: bytes, fds: list[int]) -> int:
    path = tmp_path / name
    path.write_bytes(data)
    fd = os.open(path, os.O_RDONLY)
    fds.append(fd)
    return fd


def test_the_tree_id_matches_git_for_a_real_repo(tmp_path: Path, fds: list[int]) -> None:
    repo, commit = make_repo(tmp_path / "repo")
    archive, commit_object = blobs(repo, commit)
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    archive_fd = fd_of(tmp_path, "archive", archive, fds)
    assert sc.git_tree_id(sc.git_members(archive_fd, "sha1"), algo="sha1") == tree
    sc.check_git(archive_fd, fd_of(tmp_path, "commit", commit_object, fds), commit)


def test_a_changed_archive_or_commit_object_is_refused(tmp_path: Path, fds: list[int]) -> None:
    repo, commit = make_repo(tmp_path / "repo")
    archive, commit_object = blobs(repo, commit)
    changed_archive = tar_of({"README.md": b"# another skill\n"}, gz=False)
    cases = [(changed_archive, commit_object, commit),
             (archive, commit_object.replace(b"fixture", b"fixturf"), commit),
             (archive, commit_object, commit[:-1] + ("0" if commit[-1] != "0" else "1")),
             (archive, commit_object, commit[:12]),
             (archive, commit_object, commit.upper())]
    for i, (a, c, cid) in enumerate(cases):
        with pytest.raises(sc.Refused) as e:
            sc.check_git(fd_of(tmp_path, f"a{i}", a, fds), fd_of(tmp_path, f"c{i}", c, fds), cid)
        assert e.value.constant == sc.GIT_MISMATCH


def test_an_archive_with_a_file_dropped_or_a_mode_changed_is_refused(tmp_path: Path, fds: list[int]) -> None:
    repo, commit = make_repo(tmp_path / "repo")
    _, commit_object = blobs(repo, commit)
    tree = {"README.md": b"# a skill\n", "run.sh": b"exit 0\n"}        # run.sh lost its execute bit
    with pytest.raises(sc.Refused) as e:
        sc.check_git(fd_of(tmp_path, "a", tar_of(tree, gz=False), fds),
                     fd_of(tmp_path, "c", commit_object, fds), commit)
    assert e.value.constant == sc.GIT_MISMATCH


def test_a_submodule_is_refused(tmp_path: Path, fds: list[int]) -> None:
    inner, _ = make_repo(tmp_path / "inner")
    repo, _ = make_repo(tmp_path / "repo")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner), "vendored")
    git(repo, "commit", "-q", "-m", "with a submodule")
    commit = git(repo, "rev-parse", "HEAD").decode().strip()
    archive, commit_object = blobs(repo, commit)
    with pytest.raises(sc.Refused) as e:
        sc.check_git(fd_of(tmp_path, "a", archive, fds), fd_of(tmp_path, "c", commit_object, fds), commit)
    assert e.value.constant == sc.GIT_SUBMODULE


def test_a_sha256_repository_is_checked_too(tmp_path: Path, fds: list[int]) -> None:
    probe = subprocess.run(["git", "init", "-q", "--object-format=sha256", str(tmp_path / "probe")],
                           env=GIT_ENV, capture_output=True, timeout=60)
    if probe.returncode != 0:
        pytest.skip("this host's git can't create a sha256 repository")
    repo, commit = make_repo(tmp_path / "repo", object_format="sha256")
    assert len(commit) == 64
    archive, commit_object = blobs(repo, commit)
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    archive_fd = fd_of(tmp_path, "archive", archive, fds)
    assert sc.git_tree_id(sc.git_members(archive_fd, "sha256"), algo="sha256") == tree
    sc.check_git(archive_fd, fd_of(tmp_path, "commit", commit_object, fds), commit)
    with pytest.raises(sc.Refused) as e:
        sc.check_git(archive_fd, fd_of(tmp_path, "c2", commit_object + b"\n", fds), commit)
    assert e.value.constant == sc.GIT_MISMATCH


def test_a_git_part_stages_and_records_its_commit(tmp_path: Path) -> None:
    repo, commit = make_repo(tmp_path / "repo")
    archive, commit_object = blobs(repo, commit)
    cli = tar_of(CODEX_TREE)
    content = part_of("content/skills/a", archive, fmt="tar", kind="git", checksum=f"git:{commit}",
                      git_commit=commit)
    try:
        staged = stage(tmp_path, CODEX_LAYOUT, [(part_of("cli", cli), cli),
                                                (content, [archive, commit_object])])
        m = staged_manifest(tmp_path, staged)
        paths = {e.path for e in m.entries}
        assert {"content/skills/a/run.sh", "content/skills/a/nested/deeper/file.txt",
                "content/skills/a/link"} <= paths
        assert next(s for s in m.sources if s.kind == "git").checksum == f"git:{commit}"
        bad = part_of("content/skills/a", archive, fmt="tar", kind="git", checksum=f"git:{commit}",
                      git_commit=commit)
        with pytest.raises(sc.Refused) as e:
            stage(tmp_path / "b", CODEX_LAYOUT, [(part_of("cli", cli), cli),
                                                 (bad, [archive, commit_object + b" "])])
        assert e.value.constant == sc.GIT_MISMATCH
        wrong = part_of("content/skills/a", archive, fmt="tar", kind="git", git_commit=commit)
        with pytest.raises(sc.Refused) as e:
            stage(tmp_path / "c", CODEX_LAYOUT, [(part_of("cli", cli), cli),
                                                 (wrong, [archive, commit_object])])
        assert e.value.constant == sc.BAD_PART                   # a git part's checksum is its commit
    finally:
        for d in ("store", "b/store", "c/store"):
            unseal(tmp_path / d)
