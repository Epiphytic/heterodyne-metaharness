"""Plan 4c Task 2: the fd walk, the record and the fast verification (r16 §7 Verification, plan 4c E8).
Every refusal is asserted by `store_core`'s own constant."""

import dataclasses
import hashlib
import os
import socket
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from harness_env import (
    EXECUTABLE,
    fd_count,
    make_tree,
    manifest_of,
    open_dir,
    record_of,
    seal,
    unseal,
)

from heterodyne.harness.resources import store_core as sc

STORE_CORE = Path(sc.__file__)
TREE = {"cli/README": b"the codex package\n", "cli/bin/codex": EXECUTABLE + b"exit 0\n",
        "cli/codex": ("link", "bin/codex"), "content/skills/a/SKILL.md": b"# a\n", "defaults": ("dir",)}


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    """A sealed fixture tree; made writable again at teardown so it can be removed."""
    tree = make_tree(tmp_path / "install", TREE)
    seal(tree)
    yield tree
    unseal(tree)


@pytest.fixture
def fd(root: Path) -> Iterator[int]:
    opened = open_dir(root)
    yield opened
    os.close(opened)


def walk_refusal(root: Path) -> str:
    fd = open_dir(root)
    try:
        with pytest.raises(sc.WalkRefused) as e:
            list(sc.walk(fd))
    finally:
        os.close(fd)
    assert str(e.value) == e.value.constant
    return e.value.constant


def fast_refusal(root: Path, m: sc.Manifest, rec: sc.Record) -> str:
    fd = open_dir(root)
    try:
        with pytest.raises(sc.Mismatch) as e:
            sc.check_fast(fd, m, rec)
    finally:
        os.close(fd)
    assert str(e.value) == e.value.constant
    return e.value.constant


def test_a_tree_walks_to_its_manifest_and_the_fast_check_passes(root: Path, fd: int) -> None:
    m = manifest_of(root)
    by_path = {e.path: e for e in m.entries}
    assert sorted(by_path) == ["cli", "cli/README", "cli/bin", "cli/bin/codex", "cli/codex", "content",
                               "content/skills", "content/skills/a", "content/skills/a/SKILL.md", "defaults"]
    assert {p for p, e in by_path.items() if e.kind == "dir"} == {
        "cli", "cli/bin", "content", "content/skills", "content/skills/a", "defaults"}
    assert all(e.mode == 0o555 for e in m.entries if e.kind == "dir")
    readme, codex, link = by_path["cli/README"], by_path["cli/bin/codex"], by_path["cli/codex"]
    assert (readme.mode, readme.size, readme.nlink) == (0o444, len(TREE["cli/README"]), 1)
    assert readme.sha256 == hashlib.sha256(b"the codex package\n").hexdigest() and readme.target is None
    assert codex.mode == 0o555
    assert (link.kind, link.target, link.sha256) == ("link", "bin/codex", None)
    assert sc.parse(sc.canonical(m)) == m
    rec = sc.record(fd)
    assert set(rec) == {"", *by_path}
    sc.check_fast(fd, m, rec)
    sc.rehash(fd, m)


def flat(tmp_path: Path, n: int) -> Path:
    tree = make_tree(tmp_path / f"flat{n}", {f"f{i:03}": b"x" for i in range(n)})
    seal(tree)
    return tree


def test_exactly_at_the_entry_limit_is_accepted_and_one_more_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "MAX_ENTRIES", 50)
    at, over = flat(tmp_path, 50), flat(tmp_path, 51)
    fd = open_dir(at)
    try:
        assert len(list(sc.walk(fd))) == 50          # the root isn't an entry, and isn't counted
    finally:
        os.close(fd)
    assert walk_refusal(over) == sc.TOO_MANY_ENTRIES
    unseal(at)
    unseal(over)


def chain(tmp_path: Path, name: str, depth: int) -> Path:
    """A tree whose deepest entry is `depth` components deep."""
    tree = make_tree(tmp_path / name, {"/".join(["d"] * depth): ("dir",)})
    seal(tree)
    return tree


def test_exactly_at_the_depth_limit_is_accepted_and_one_deeper_is_refused(tmp_path: Path) -> None:
    assert sc.MAX_DEPTH == 64
    at, over = chain(tmp_path, "at", 64), chain(tmp_path, "over", 65)
    fd = open_dir(at)
    try:
        assert max(p.count("/") + 1 for p, _, _ in sc.walk(fd)) == 64
    finally:
        os.close(fd)
    assert walk_refusal(over) == sc.TOO_DEEP
    unseal(at)
    unseal(over)


def test_a_fifo_a_socket_and_a_device_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fifo = make_tree(tmp_path / "fifo", {"cli/a": b"a"})
    os.mkfifo(fifo / "cli" / "pipe")
    assert walk_refusal(fifo) == sc.BAD_TYPE

    sock_tree = make_tree(tmp_path / "sock", {"cli/a": b"a"})
    with socket.socket(socket.AF_UNIX) as s:
        here = Path.cwd()
        os.chdir(sock_tree / "cli")         # a relative bind keeps the path inside sun_path's limit
        try:
            s.bind("s")
        finally:
            os.chdir(here)
        assert walk_refusal(sock_tree) == sc.BAD_TYPE

    dev = make_tree(tmp_path / "dev", {"cli/a": b"a", "cli/null": b""})
    try:
        (dev / "cli" / "null").unlink()
        os.mknod(dev / "cli" / "null", stat.S_IFCHR | 0o600, os.makedev(1, 3))
    except PermissionError:         # unprivileged: the walk is shown a character device instead
        (dev / "cli" / "null").write_bytes(b"")
        real = sc.statx_at

        def as_device(dirfd: int, name: str) -> sc.Stat:
            st = real(dirfd, name)
            return dataclasses.replace(st, kind="char") if name == "null" else st

        monkeypatch.setattr(sc, "statx_at", as_device)
    assert walk_refusal(dev) == sc.BAD_TYPE


def test_a_hard_link_is_refused(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "t", {"cli/a": b"a"})
    os.link(tree / "cli" / "a", tree / "cli" / "b")
    assert walk_refusal(tree) == sc.HARD_LINK


@pytest.mark.parametrize("links", [
    {"cli/x": ("link", "nowhere")},                                     # dangles
    {"cli/x": ("link", "y"), "cli/y": ("link", "x")},                   # a loop
    {"cli/x": ("link", "../../outside")},                               # leaves the install
    {"cli/x": ("link", "../..")},                                       # the root, then out of it
    {"cli/x": ("link", "a/more")},                                      # through a file
    {"cli/x": ("link", "/etc/hostname")},                               # absolute
], ids=["dangling", "loop", "leaves", "parent-of-root", "through-a-file", "absolute"])
def test_links_that_dangle_loop_leave_or_are_absolute_are_refused(tmp_path: Path,
                                                                   links: dict[str, tuple[str, str]]) -> None:
    good = make_tree(tmp_path / "good", {"cli/a": b"a", "cli/d/f": b"f", "cli/l1": ("link", "d"),
                                         "cli/l2": ("link", "l1/f"), "cli/l3": ("link", "../cli/l2"),
                                         "top": ("link", ".")})
    fd = open_dir(good)
    try:
        targets = {p: t for p, st, t in sc.walk(fd) if st.kind == "link"}     # a link to a link resolves
    finally:
        os.close(fd)
    assert targets == {"cli/l1": "d", "cli/l2": "l1/f", "cli/l3": "../cli/l2", "top": "."}
    assert walk_refusal(make_tree(tmp_path / "bad", {"cli/a": b"a", **links})) == sc.BAD_LINK


def test_a_loop_longer_than_the_hop_limit_is_refused(tmp_path: Path) -> None:
    hops = sc.MAX_LINK_HOPS
    ring = {f"l{i}": ("link", f"l{(i + 1) % (hops + 1)}") for i in range(hops + 1)}
    assert walk_refusal(make_tree(tmp_path / "ring", ring)) == sc.BAD_LINK
    line = {f"l{i}": ("link", f"l{i + 1}") for i in range(hops)} | {f"l{hops}": b"end"}
    tree = make_tree(tmp_path / "line", line)          # a chain of exactly MAX_LINK_HOPS links: accepted
    fd = open_dir(tree)
    try:
        assert len(list(sc.walk(fd))) == hops + 1
    finally:
        os.close(fd)
    longer = {f"l{i}": ("link", f"l{i + 1}") for i in range(hops + 1)} | {f"l{hops + 1}": b"end"}
    assert walk_refusal(make_tree(tmp_path / "longer", longer)) == sc.BAD_LINK


def test_an_added_removed_renamed_or_rewritten_entry_fails_the_fast_check(tmp_path: Path) -> None:
    def fresh(name: str) -> tuple[Path, sc.Manifest, sc.Record]:
        tree = make_tree(tmp_path / name, TREE)
        seal(tree)
        return tree, manifest_of(tree), record_of(tree)

    tree, m, rec = fresh("added")
    unseal(tree)
    (tree / "cli" / "extra").write_bytes(b"x")
    assert fast_refusal(tree, m, rec) == sc.ENTRY_ADDED

    tree, m, rec = fresh("removed")
    unseal(tree)
    (tree / "cli" / "README").unlink()
    assert fast_refusal(tree, m, rec) == sc.ENTRY_MISSING

    tree, m, rec = fresh("renamed")
    unseal(tree)
    (tree / "cli" / "README").rename(tree / "cli" / "README2")
    assert fast_refusal(tree, m, rec) == sc.ENTRY_ADDED

    tree, m, rec = fresh("rewritten")
    readme = tree / "cli" / "README"
    before = readme.stat()
    readme.chmod(0o644)
    readme.write_bytes(b"THE CODEX PACKAGE\n")          # the same size
    readme.chmod(0o444)
    assert readme.stat().st_size == before.st_size and readme.stat().st_ctime_ns != before.st_ctime_ns
    assert fast_refusal(tree, m, rec) == sc.ENTRY_CHANGED

    tree, m, rec = fresh("root")
    tree.chmod(0o755)                                   # the root's mode
    assert fast_refusal(tree, m, rec) == sc.ENTRY_CHANGED

    tree, m, _ = fresh("rerecorded")                    # the manifest still binds what a record can't
    (tree / "cli" / "README").chmod(0o644)
    assert fast_refusal(tree, m, record_of(tree)) == sc.ENTRY_CHANGED

    tree, m, _ = fresh("root-mode")                     # a writable root, even freshly recorded
    tree.chmod(0o755)
    assert fast_refusal(tree, m, record_of(tree)) == sc.ENTRY_CHANGED
    for name in ("added", "removed", "renamed", "rewritten", "root", "rerecorded", "root-mode"):
        unseal(tmp_path / name)


def test_a_change_during_the_walk_fails_it(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m, rec = manifest_of(root), record_of(root)
    real = sc.statx_at
    changed: list[str] = []

    def rewriting(dirfd: int, name: str) -> sc.Stat:
        if name and not changed:                        # the first entry: the root is already listed
            changed.append(name)
            later = root / "content" / "skills" / "a" / "SKILL.md"
            later.chmod(0o644)
            later.write_bytes(b"# b\n")
            later.chmod(0o444)
        return real(dirfd, name)

    monkeypatch.setattr(sc, "statx_at", rewriting)
    assert fast_refusal(root, m, rec) == sc.ENTRY_CHANGED
    assert changed == ["cli"]


def test_an_entry_added_to_a_directory_already_listed_fails_the_walk(root: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    real = sc.statx_at
    added: list[str] = []

    def adding(dirfd: int, name: str) -> sc.Stat:
        if name == "defaults" and not added:            # the root was listed before this
            added.append(name)
            root.chmod(0o755)
            (root / "zz").write_bytes(b"late")
            root.chmod(0o555)
        return real(dirfd, name)

    monkeypatch.setattr(sc, "statx_at", adding)
    assert walk_refusal(root) == sc.ENTRY_CHANGED
    assert added == ["defaults"]


def userns() -> bool:
    return subprocess.run(["unshare", "-rm", "true"], capture_output=True, timeout=30).returncode == 0


WALK_IN_CHILD = """
import os, sys
from heterodyne.harness.resources import store_core as sc
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    list(sc.walk(fd))
    print("walked")
except sc.WalkRefused as e:
    print(e.constant)
"""


def test_a_same_filesystem_bind_mount_inside_is_refused(tmp_path: Path) -> None:
    if not userns():
        pytest.skip("user namespaces are unavailable")
    tree = make_tree(tmp_path / "t", {"cli/a": b"a", "cli/mnt": ("dir",)})
    elsewhere = make_tree(tmp_path / "elsewhere", {"b": b"b"})
    assert tree.stat().st_dev == elsewhere.stat().st_dev           # the same filesystem: only the mount ID
    script = 'mount --bind "$1" "$2" && exec "$3" -c "$4" "$5"'
    run = [sys.executable, "-c", WALK_IN_CHILD, str(tree)]
    plain = subprocess.run(run, capture_output=True, text=True, timeout=60, check=True)
    assert plain.stdout.strip() == "walked"
    bound = subprocess.run(["unshare", "-rm", "sh", "-c", script, "sh", str(elsewhere),
                            str(tree / "cli" / "mnt"), sys.executable, WALK_IN_CHILD, str(tree)],
                           capture_output=True, text=True, timeout=60)
    assert bound.returncode == 0, bound.stderr
    assert bound.stdout.strip() == sc.CROSSES_MOUNT


def test_rehash_finds_content_that_kept_its_size_and_metadata(root: Path, fd: int) -> None:
    m, rec = manifest_of(root), record_of(root)

    def tampered(f: int, n: int) -> bytes:
        data = os.read(f, n)
        return data.replace(b"exit 0", b"exit 1")       # what a rewrite that restored the times would hold

    sc.check_fast(fd, m, rec)                           # the fast check can't see it
    with pytest.raises(sc.Mismatch) as e:
        sc.rehash(fd, m, read=tampered)
    assert e.value.constant == sc.CONTENT_CHANGED
    sc.rehash(fd, m)


def test_rehash_refuses_a_file_that_is_no_longer_a_regular_file(root: Path, fd: int) -> None:
    m = manifest_of(root)
    unseal(root)
    readme = root / "cli" / "README"
    readme.unlink()
    os.mkfifo(readme)                                   # never opened for a blocking read
    with pytest.raises(sc.Mismatch) as e:
        sc.rehash(fd, m)
    assert e.value.constant == sc.ENTRY_CHANGED


def test_the_walk_closes_every_fd_it_opens(tmp_path: Path, root: Path) -> None:
    before = fd_count()
    fd = open_dir(root)
    try:
        m = manifest_of(root)
        rec = sc.record(fd)
        list(sc.walk(fd))
        sc.check_fast(fd, m, rec)
        sc.rehash(fd, m)
        partial = sc.walk(fd)
        next(partial)
        next(partial)
        partial.close()                                 # a walk left part way
    finally:
        os.close(fd)
    assert fd_count() == before
    refusing = {"deep": {"/".join(["d"] * 66): ("dir",)}, "link": {"a/b/c": b"c", "a/x": ("link", "/x")},
                "type": {"a/b/c": b"c"}}
    for name, spec in refusing.items():
        tree = make_tree(tmp_path / name, spec)
        if name == "type":
            os.mkfifo(tree / "a" / "b" / "p")
        walk_refusal(tree)
    assert fd_count() == before
    m = manifest_of(root)
    unseal(root)
    (root / "cli" / "README").unlink()
    fast_refusal(root, m, record_of(root))
    fd = open_dir(root)
    try:
        with pytest.raises(sc.Mismatch):
            sc.rehash(fd, m)
    finally:
        os.close(fd)
    assert fd_count() == before


def test_statx_without_a_mount_id_refuses(root: Path, fd: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "STATX_MNT_ID", 0)          # the mask then never carries it
    with pytest.raises(sc.WalkRefused) as e:
        sc.statx_at(fd, "cli")
    assert e.value.constant == sc.NO_MOUNT_ID


def test_store_core_imports_only_the_standard_library() -> None:
    code = ("import sys\n"
            "before = set(sys.modules)\n"
            "import store_core\n"
            "assert store_core.__file__ == sys.argv[1], store_core.__file__\n"
            "print('\\n'.join(sorted(set(sys.modules) - before)))\n")
    child = (f"import sys; sys.path[:] = [{str(STORE_CORE.parent)!r}] + "
             "[p for p in sys.path if 'site-packages' not in p and 'dist-packages' not in p]\n" + code)
    out = subprocess.run([sys.executable, "-I", "-S", "-c", child, str(STORE_CORE)], capture_output=True,
                         text=True, timeout=60, check=True)
    loaded = set(out.stdout.split())
    assert "store_core" in loaded
    foreign = {m for m in loaded - {"store_core"} if m.split(".")[0] not in sys.stdlib_module_names}
    assert foreign == set()
    assert not any(m.split(".")[0] in ("heterodyne", "msgspec") for m in loaded)
