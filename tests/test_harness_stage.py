"""Plan 4c Task 3: `stage` (r16 §7 Managed installs; plan 4c E2, E4, E7, E20, E21). Every refusal is
asserted by `store_core`'s own constant, and every artifact is built here at run time."""

import dataclasses
import errno
import json
import os
import stat
import sys
import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from harness_env import (
    CLAUDE_LAYOUT,
    CLAUDE_PACKAGE,
    CLAUDE_TREE,
    CODEX_LAYOUT,
    CODEX_PACKAGE,
    CODEX_STRIP,
    CODEX_TREE,
    EXECUTABLE,
    LOGIN_NAMES,
    Spec,
    claude_part,
    codex_part,
    fd_count,
    make_tree,
    member,
    open_dir,
    part_of,
    record_of,
    sha256_of,
    stage,
    staged_manifest,
    staged_root,
    store_dir,
    tar_of,
    unseal,
)

from heterodyne.harness.resources import store_core as sc

SPIKE = Path(__file__).resolve().parents[1] / "docs" / "spikes" / "s9-managed-installs.md"


@pytest.fixture(autouse=True)
def writable_store(tmp_path: Path) -> Iterator[None]:
    """Make the store writable again at teardown, so the temp dir can be removed."""
    yield
    unseal(tmp_path / "store")


def refusal(tmp_path: Path, layout: sc.Layout, parts: list[tuple[sc.Part, bytes]]) -> str:
    fds = fd_count()
    with pytest.raises(sc.Refused) as e:
        stage(tmp_path, layout, parts)
    assert str(e.value) == e.value.constant
    assert fd_count() == fds
    assert_nothing_staged(tmp_path)
    return e.value.constant


def assert_nothing_staged(tmp_path: Path) -> None:
    for name in (".staging", ".staged"):
        here = tmp_path / "store" / name
        assert not os.path.lexists(here) or list(here.iterdir()) == []


def with_cli(tree: dict[str, Spec]) -> dict[str, Spec]:
    """`tree` beside a minimal Codex release, so the version and the entry point can be read."""
    return {"codex-package.json": CODEX_TREE["codex-package.json"], "bin/codex": CODEX_TREE["bin/codex"],
            **tree}


def test_a_codex_like_release_stages_with_its_version_target_and_entry_point_from_its_metadata(
        tmp_path: Path) -> None:
    fds = fd_count()
    staged = stage(tmp_path, CODEX_LAYOUT, [codex_part()])
    assert fd_count() == fds
    m = staged_manifest(tmp_path, staged)
    assert (m.adapter, m.version, m.target, m.entry_point) == ("codex", CODEX_PACKAGE["version"],
                                                              CODEX_PACKAGE["target"], "cli/bin/codex")
    assert staged.digest == sc.digest(m)
    assert staged.entries == len(m.entries)
    assert staged.size == sum(e.size for e in m.entries if e.kind == "file")
    assert len(staged.id) == 32 and int(staged.id, 16) >= 0
    assert [s.part for s in m.sources] == ["cli"]
    paths = {e.path for e in m.entries}
    assert {f"cli/{p}" for p in CODEX_TREE} <= paths
    assert {"cli", "cli/bin", "cli/codex-resources/voice"} <= paths


def test_a_claude_like_package_stages_with_its_version_from_package_json_and_a_fixed_entry_point(
        tmp_path: Path) -> None:
    staged = stage(tmp_path, CLAUDE_LAYOUT, [claude_part()], adapter="claude-code")
    m = staged_manifest(tmp_path, staged)
    assert (m.adapter, m.version, m.target, m.entry_point) == ("claude-code", CLAUDE_PACKAGE["version"], "",
                                                              "cli/claude")
    assert {e.path for e in m.entries} == {"cli", "cli/claude", "cli/package.json"}


def test_content_and_defaults_parts_stage_beside_the_cli(tmp_path: Path) -> None:
    skill = tar_of({"SKILL.md": b"# a skill\n"})
    defaults = b'model = "x"\n'
    staged = stage(tmp_path, CODEX_LAYOUT, [codex_part(), (part_of("content/skills/a", skill), skill),
                                            (part_of("defaults", defaults, fmt="file", strip="config.toml"),
                                             defaults)])
    m = staged_manifest(tmp_path, staged)
    paths = {e.path for e in m.entries}
    assert {"content", "content/skills", "content/skills/a", "content/skills/a/SKILL.md", "defaults",
            "defaults/config.toml"} <= paths
    assert [s.part for s in m.sources] == ["cli", "content/skills/a", "defaults"]
    assert (staged_root(tmp_path, staged) / "defaults" / "config.toml").read_bytes() == defaults


@pytest.mark.parametrize("part", ["", "cli/x", "content/skills", "content/skills/a/b", "content/../a",
                                  "other"])
def test_a_part_name_outside_the_three_shapes_is_refused(tmp_path: Path, part: str) -> None:
    data = tar_of({"x": b"x"})
    assert refusal(tmp_path, CODEX_LAYOUT, [codex_part(), (part_of(part, data), data)]) == sc.BAD_PART


def test_a_missing_cli_a_duplicate_part_or_a_wrong_fd_count_is_refused(tmp_path: Path) -> None:
    skill = tar_of({"SKILL.md": b"x"})
    content = (part_of("content/skills/a", skill), skill)
    assert refusal(tmp_path, CODEX_LAYOUT, [content]) == sc.BAD_PART
    assert refusal(tmp_path, CODEX_LAYOUT, [codex_part(), content, content]) == sc.BAD_PART
    part, data = codex_part()
    with pytest.raises(sc.Refused) as e:
        stage(tmp_path, CODEX_LAYOUT, [(part, [data, data])])
    assert e.value.constant == sc.BAD_PART
    assert_nothing_staged(tmp_path)


def test_a_source_that_disagrees_with_its_part_is_refused(tmp_path: Path) -> None:
    part, data = codex_part()
    other = dataclasses.replace(part, source=dataclasses.replace(part.source, checksum=sha256_of(b"other")))
    assert refusal(tmp_path, CODEX_LAYOUT, [(other, data)]) == sc.BAD_PART
    other = dataclasses.replace(part, source=dataclasses.replace(part.source, part="defaults"))
    assert refusal(tmp_path, CODEX_LAYOUT, [(other, data)]) == sc.BAD_PART


def test_a_member_outside_the_strip_prefix_is_refused(tmp_path: Path) -> None:
    data = tar_of(CLAUDE_TREE, prefix="package/", dirs=False, extra=[member("elsewhere/x")])
    part = part_of("cli", data, fmt="tgz", strip="package/", kind="npm")
    assert refusal(tmp_path, CLAUDE_LAYOUT, [(part, data)]) == sc.BAD_PART


def test_every_regular_file_in_staging_is_a_new_inode_with_one_link(tmp_path: Path) -> None:
    unpacked = make_tree(tmp_path / "unpacked", CODEX_TREE)
    staged = stage(tmp_path, CODEX_LAYOUT, [codex_part()])
    root = staged_root(tmp_path, staged) / "cli"
    theirs = {(p.stat().st_dev, p.stat().st_ino) for p in unpacked.rglob("*")}
    artifact = tmp_path / "artifact-0"
    theirs.add((artifact.stat().st_dev, artifact.stat().st_ino))
    files = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()]
    assert len(files) == len([v for v in CODEX_TREE.values() if isinstance(v, bytes)])
    for f in files:
        st = f.lstat()
        assert st.st_nlink == 1
        assert (st.st_dev, st.st_ino) not in theirs


@pytest.mark.parametrize("name", ["/abs", "../up", "a/../../up", "a//b", "a/./b", "a\x00b" * 60, "", "d/"],
                         ids=["absolute", "dotdot", "inner-dotdot", "empty", "dot", "nul", "blank", "slash"])
def test_absolute_dotdot_empty_and_nul_names_are_refused(tmp_path: Path, name: str) -> None:
    data = tar_of(with_cli({}), extra=[member(name)])
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", data), data)]) == sc.BAD_NAME


def test_a_name_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    data = tar_of(with_cli({}), extra=[member(os.fsdecode(b"bad\xff"))])
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", data), data)]) == sc.BAD_NAME


@pytest.mark.parametrize("kind", [tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE,
                                  tarfile.CONTTYPE],
                         ids=["hard-link", "char", "block", "fifo", "contiguous"])
def test_tar_hard_links_devices_fifos_and_other_member_types_are_refused(tmp_path: Path, kind: bytes) -> None:
    extra = member("odd", kind, linkname="bin/codex" if kind == tarfile.LNKTYPE else "")
    data = tar_of(with_cli({}), extra=[extra])
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", data), data)]) == sc.BAD_MEMBER


def test_a_member_that_would_replace_or_pass_through_another_is_refused(tmp_path: Path) -> None:
    twice = tar_of(with_cli({}), extra=[member("bin/codex")])
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", twice), twice)]) == sc.BAD_MEMBER
    through = tar_of(with_cli({"l": ("link", "bin")}), extra=[member("l/x")])
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", through), through)]) == sc.BAD_MEMBER
    under_file = tar_of(with_cli({}), extra=[member("bin/codex/x")])
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", under_file), under_file)]) == sc.BAD_MEMBER


@pytest.mark.parametrize("login", sorted(LOGIN_NAMES))
@pytest.mark.parametrize("where", ["{}", "deep/er/{}", "{}/inside"])
def test_a_member_named_as_any_login_file_is_refused(tmp_path: Path, login: str, where: str) -> None:
    data = tar_of(with_cli({where.format(login): b"{}"}))
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", data), data)]) == sc.LOGIN_NAME


def test_a_part_named_as_a_login_file_is_refused(tmp_path: Path) -> None:
    data = tar_of({"x": b"x"})
    part = part_of("content/skills/auth.json", data)
    assert refusal(tmp_path, CODEX_LAYOUT, [codex_part(), (part, data)]) == sc.LOGIN_NAME


def test_a_link_that_leaves_the_install_is_refused(tmp_path: Path) -> None:
    data = tar_of(with_cli({"out": ("link", "../../x")}))
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", data), data)]) == sc.BAD_LINK


def test_a_checksum_mismatch_refuses_and_leaves_no_staging(tmp_path: Path) -> None:
    part, data = codex_part()
    assert refusal(tmp_path, CODEX_LAYOUT, [(part, data + b"\0")]) == sc.CHECKSUM_MISMATCH
    claude, tarball = claude_part()
    assert refusal(tmp_path, CLAUDE_LAYOUT, [(claude, tarball[:-1])]) == sc.CHECKSUM_MISMATCH


def test_a_refusal_after_the_tree_is_sealed_removes_it(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object) -> None:
        raise sc.Refused(sc.ENTRY_CHANGED)

    monkeypatch.setattr(sc, "_commit", refuse)          # the last step: the tree is already 0555
    assert refusal(tmp_path, CODEX_LAYOUT, [codex_part()]) == sc.ENTRY_CHANGED


@pytest.mark.parametrize("version", ['""', "1", '"../x"', '"a/b"', '".hidden"', '"0.1 beta"'])
def test_a_version_that_is_missing_or_not_a_plain_name_is_refused(tmp_path: Path, version: str) -> None:
    doc = CODEX_TREE["codex-package.json"]
    assert isinstance(doc, bytes)
    bad = doc.replace(b'"version": "0.160.0"', b'"version": ' + version.encode())
    data = tar_of({**CODEX_TREE, "codex-package.json": bad})
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", data), data)]) == sc.NO_VERSION


@pytest.mark.parametrize("change", ["no-file", "not-json", "link", "no-entry", "entry-is-a-dir",
                                    "entry-not-executable"])
def test_metadata_that_cant_be_read_or_an_entry_point_that_isnt_an_executable_is_refused(
        tmp_path: Path, change: str) -> None:
    tree = dict(CODEX_TREE)
    if change == "no-file":
        del tree["codex-package.json"]
    elif change == "not-json":
        tree["codex-package.json"] = b"{"
    elif change == "link":
        tree["real.json"] = tree.pop("codex-package.json")
        tree["codex-package.json"] = ("link", "real.json")
    elif change == "no-entry":
        del tree["bin/codex"]
    elif change == "entry-is-a-dir":
        del tree["bin/codex"]
        tree["bin/codex/x"] = b"x"
    else:
        tree["bin/codex"] = b"not executable\n"
    data = tar_of(tree)
    assert refusal(tmp_path, CODEX_LAYOUT, [(part_of("cli", data), data)]) == sc.NO_VERSION


def test_the_staged_manifest_is_readable_by_others_and_the_staging_dir_is_not(tmp_path: Path) -> None:
    staged = stage(tmp_path, CODEX_LAYOUT, [codex_part()])
    store = tmp_path / "store"
    assert stat.S_IMODE((store / ".staged" / f"{staged.id}.json").stat().st_mode) == 0o444
    assert stat.S_IMODE((store / ".staging").stat().st_mode) == 0o700
    assert stat.S_IMODE((store / ".staged").stat().st_mode) == 0o755
    assert sorted(p.name for p in (store / ".staging").iterdir()) == [staged.id]   # no lock, no copies


def test_staged_modes_are_the_final_modes_and_the_manifest_records_them(tmp_path: Path) -> None:
    tree = {**CODEX_TREE, "codex": ("link", "bin/codex"), "notes.txt": b"plain\n"}
    staged = stage(tmp_path, CODEX_LAYOUT, [(part_of("cli", tar_of(tree)), tar_of(tree))])
    root = staged_root(tmp_path, staged)
    assert stat.S_IMODE(root.stat().st_mode) == 0o555
    m = staged_manifest(tmp_path, staged)
    for e in m.entries:
        path = root / e.path
        mode = stat.S_IMODE(path.lstat().st_mode)
        assert e.mode == mode
        if e.kind == "dir":
            assert mode == 0o555
        elif e.kind == "file":
            content = tree[e.path.removeprefix("cli/")]
            assert isinstance(content, bytes)
            assert mode == (0o555 if content.startswith(EXECUTABLE) else 0o444)
    fd = open_dir(root)
    try:
        sc.check_fast(fd, m, record_of(root))
        sc.rehash(fd, m)
    finally:
        os.close(fd)


def test_the_codex_fixture_has_the_layout_s9_1_found() -> None:
    spike = SPIKE.read_text()
    row = next(line for line in spike.splitlines() if line.startswith("| S9-1 |"))
    assert "**confirmed**" in row
    assert f'`strip = "{CODEX_STRIP}"`' in row
    assert "files and dirs only" in row
    assert {name.split("/")[0] for name in CODEX_TREE} == {"bin", "codex-package.json", "codex-path",
                                                          "codex-resources"}
    assert all(isinstance(v, bytes) for v in CODEX_TREE.values())          # files (and their dirs) only
    package = json.loads(CODEX_TREE["codex-package.json"])  # type: ignore[arg-type]
    assert package == CODEX_PACKAGE
    assert json.dumps(package, separators=(",", ":")) in spike


def test_bounds_apply_at_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "MAX_ENTRIES", 50)
    base = with_cli({})                 # cli, cli/codex-package.json, cli/bin, cli/bin/codex: 4 entries
    at_limit = {**base, **{f"f{i}": b"x" for i in range(46)}}
    data = tar_of(at_limit)
    stage(tmp_path / "a", CODEX_LAYOUT, [(part_of("cli", data), data)])
    over = {**at_limit, "one-more": b"x"}
    data = tar_of(over)
    assert refusal(tmp_path / "b", CODEX_LAYOUT, [(part_of("cli", data), data)]) == sc.TOO_MANY_ENTRIES

    deep = "/".join(["d"] * (sc.MAX_DEPTH - 2)) + "/f"      # with `cli/`, exactly MAX_DEPTH components
    data = tar_of(with_cli({deep: b"x"}))
    stage(tmp_path / "c", CODEX_LAYOUT, [(part_of("cli", data), data)])
    data = tar_of(with_cli({"d/" + deep: b"x"}))
    assert refusal(tmp_path / "d", CODEX_LAYOUT, [(part_of("cli", data), data)]) == sc.TOO_DEEP


PACKAGE_SCRIPT_RAN: list[str] = []
WATCHING: list[bool] = []


def _no_exec(event: str, _args: tuple[object, ...]) -> None:
    if WATCHING and (event in ("subprocess.Popen", "os.system", "os.posix_spawn")
                     or event.startswith("os.exec")):
        PACKAGE_SCRIPT_RAN.append(event)


sys.addaudithook(_no_exec)


def test_no_package_script_runs(tmp_path: Path) -> None:
    ran = tmp_path / "postinstall-ran"
    package = {**CLAUDE_PACKAGE, "scripts": {"postinstall": f"touch {ran}", "preinstall": f"touch {ran}"}}
    tree = {**CLAUDE_TREE, "package.json": json.dumps(package).encode(), "install.cjs": b"require('fs')\n"}
    part, data = claude_part(tree)
    WATCHING.append(True)
    try:
        stage(tmp_path, CLAUDE_LAYOUT, [(part, data)], adapter="claude-code")
    finally:
        WATCHING.clear()
    assert PACKAGE_SCRIPT_RAN == []
    assert not os.path.lexists(ran)


def test_verity_is_enabled_where_supported_and_recorded_off_where_not(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    enabled: list[int] = []

    def supported(fd: int, request: int, arg: bytes) -> bytes:
        assert request == sc.FS_IOC_ENABLE_VERITY
        assert os.fstat(fd).st_mode & 0o200          # still writable by its owner: verity needs that
        enabled.append(fd)
        return arg

    monkeypatch.setattr(sc, "_ioctl", supported)
    staged = stage(tmp_path / "a", CODEX_LAYOUT, [codex_part()], verity=True)
    assert staged.verity is True
    assert len(enabled) == len(CODEX_TREE)

    def unsupported(fd: int, request: int, arg: bytes) -> bytes:
        raise OSError(errno.EOPNOTSUPP, "ioctl")

    monkeypatch.setattr(sc, "_ioctl", unsupported)
    assert stage(tmp_path / "b", CODEX_LAYOUT, [codex_part()], verity=True).verity is False
    enabled.clear()
    monkeypatch.setattr(sc, "_ioctl", supported)
    assert stage(tmp_path / "c", CODEX_LAYOUT, [codex_part()], verity=False).verity is False
    assert enabled == []

    def failing(fd: int, request: int, arg: bytes) -> bytes:
        raise OSError(errno.EIO, "ioctl")

    monkeypatch.setattr(sc, "_ioctl", failing)
    with pytest.raises(OSError) as e:
        stage(tmp_path / "d", CODEX_LAYOUT, [codex_part()], verity=True)
    assert e.value.errno == errno.EIO
    assert_nothing_staged(tmp_path / "d")


def test_enable_verity_says_false_only_where_unsupported(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    fd = os.open(path, os.O_RDONLY)
    try:
        for code in (errno.EOPNOTSUPP, errno.ENOTTY):
            def raising(fd: int, request: int, arg: bytes, code: int = code) -> bytes:
                raise OSError(code, "ioctl")

            monkeypatch.setattr(sc, "_ioctl", raising)
            assert sc.enable_verity(fd) is False
        monkeypatch.setattr(sc, "_ioctl", lambda fd, request, arg: arg)
        assert sc.enable_verity(fd) is True
    finally:
        os.close(fd)


def test_every_file_and_directory_is_fsynced_before_the_staged_manifest(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = store_dir(tmp_path)
    synced: list[str] = []
    real = os.fsync

    def recording(fd: int) -> None:
        path = str(Path(f"/proc/self/fd/{fd}").readlink())
        st = os.fstat(fd)
        synced.append(os.path.relpath(path, store) + ("/" if stat.S_ISDIR(st.st_mode) else ""))
        if stat.S_ISREG(st.st_mode) and synced[-1].startswith(".staging/") and \
                synced[-1].split("/")[1] == synced[-1].split("/")[1][:32]:
            assert stat.S_IMODE(st.st_mode) in (0o444, 0o555)          # after its fchmod
        real(fd)

    monkeypatch.setattr(os, "fsync", recording)
    tree = {**CODEX_TREE, "codex": ("link", "bin/codex")}
    data = tar_of(tree)
    staged = stage(tmp_path, CODEX_LAYOUT, [(part_of("cli", data), data)])
    sid = staged.id
    root = f".staging/{sid}"
    m = staged_manifest(tmp_path, staged)
    order = {name: i for i, name in enumerate(synced)}

    files = [f"{root}/{e.path}" for e in m.entries if e.kind == "file"]
    dirs = [f"{root}/{e.path}/" for e in m.entries if e.kind == "dir"]
    for f in files:
        assert f in order
    for d in [*dirs, f"{root}/"]:
        below = [p for p in [*files, *dirs] if p.startswith(d.rstrip("/") + "/") and p != d]
        assert all(order[p] < order[d] for p in below), d
    assert order[f"{root}/"] > max(order[p] for p in [*files, *dirs])
    assert order[".staging/"] > order[f"{root}/"]
    assert order[f".staged/{sid}.json"] > order[".staging/"]
    assert order[".staged/"] > order[f".staged/{sid}.json"]
    # each directory the store created has its parent fsynced after it
    assert order["./"] < order[f".staged/{sid}.json"]
    first_parent = synced.index("./")
    assert first_parent < order[f"{root}/"]
