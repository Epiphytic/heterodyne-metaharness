"""Private installation fixtures; no installed adapters or host stores are opened."""

import dataclasses
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from heterodyne.acceptance import install_projection as p
from heterodyne.acceptance.codec import Hold
from heterodyne.harness.resources import store_core as sc


@pytest.fixture
def install(tmp_path: Path) -> Iterator[tuple[int, sc.Manifest, bytes, Path]]:
    root = tmp_path / "private-install"
    root.mkdir()
    (root / "cli").write_bytes(b"offline fixture\n")
    (root / "cli").chmod(0o555)
    root.chmod(0o555)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        manifest = sc.Manifest("codex", "test-version", "", "cli", (), sc.entries_of(fd))
        yield fd, manifest, sc.canonical_record(sc.record(fd)), root
    finally:
        os.close(fd)
        root.chmod(0o755)


def verify(install: tuple[int, sc.Manifest, bytes, Path], **changes):
    fd, manifest, record, _ = install
    args = {
        "adapter": manifest.adapter,
        "versions": (manifest.version,),
        "manifest_digest": sc.digest(manifest),
        "manifest_bytes": sc.canonical(manifest),
        "record_bytes": record,
        "root_fd": fd,
    }
    args.update(changes)
    return p.verify(**args)


@pytest.mark.parametrize("adapter", ["codex", "claude"])
def test_exact_source_owned_install_projection(install, adapter: str) -> None:
    fd, manifest, record, root = install
    manifest = dataclasses.replace(manifest, adapter=adapter)
    result = verify((fd, manifest, record, root))
    assert result.adapter == adapter
    assert result.version == "test-version"
    assert result.manifest_digest == sc.digest(manifest)
    assert result.entries == 1 and result.file_bytes == len(b"offline fixture\n")
    with pytest.raises(Hold, match="install_acquisition_and_loaded_provenance_unavailable"):
        result.require_authority()


@pytest.mark.parametrize("change", ["digest", "adapter", "version", "malformed", "unknown", "record"])
def test_metadata_rejected_before_walk(install, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    def no_walk(*_args, **_kwargs):
        raise AssertionError("metadata must refuse before install access")

    monkeypatch.setattr(sc, "check_fast", no_walk)
    changes = {
        "digest": {"manifest_digest": "sha256:" + "0" * 64},
        "adapter": {"adapter": "claude"},
        "version": {"versions": ("other",)},
        "malformed": {"manifest_bytes": b"{}"},
        "unknown": {"manifest_bytes": sc.canonical(install[1])[:-1] + b',"auth":true}'},
        "record": {"record_bytes": b'{"record":{}}'},
    }
    with pytest.raises(Hold, match="install_projection_"):
        verify(install, **changes[change])


@pytest.mark.parametrize("change", ["added", "missing", "mode", "content", "hardlink", "symlink"])
def test_actual_source_owned_tree_changes_hold(install, change: str) -> None:
    _, _, _, root = install
    root.chmod(0o755)
    cli = root / "cli"
    if change == "added":
        (root / "extra").write_bytes(b"unexpected")
    elif change == "missing":
        cli.unlink()
    elif change == "mode":
        cli.chmod(0o444)
    elif change == "content":
        cli.chmod(0o755)
        cli.write_bytes(b"changed fixture\n")
        cli.chmod(0o555)
    elif change == "hardlink":
        os.link(cli, root / "extra")
    else:
        (root / "escape").symlink_to("../outside")
    root.chmod(0o555)
    with pytest.raises(Hold, match="install_projection_tree_unavailable"):
        verify(install)


def test_actual_hash_read_rejects_replacement(install, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, _, root = install
    original = os.read
    changed = False

    def mutate(fd: int, size: int) -> bytes:
        nonlocal changed
        value = original(fd, size)
        if not changed:
            changed = True
            (root / "cli").chmod(0o755)
            (root / "cli").write_bytes(b"changed fixture\n")
            (root / "cli").chmod(0o555)
        return value

    monkeypatch.setattr(p.os, "read", mutate)
    with pytest.raises(Hold, match="install_projection_tree_unavailable"):
        verify(install)
    assert changed


def test_fresh_capture_record_does_not_hide_wrong_content(install) -> None:
    fd, manifest, _, root = install
    cli = root / "cli"
    cli.chmod(0o755)
    cli.write_bytes(b"changed fixture\n")
    cli.chmod(0o555)
    fresh_record = sc.canonical_record(sc.record(fd))
    # The current inode metadata matches this capture; the independently pinned content does not.
    sc.check_fast(fd, manifest, sc.parse_record(fresh_record))
    with pytest.raises(Hold, match="install_projection_tree_unavailable"):
        verify(install, record_bytes=fresh_record)


@pytest.mark.parametrize("kind", ["duplicate", "noncanonical"])
def test_record_duplicate_or_noncanonical_input_holds_before_access(install, kind: str) -> None:
    record = install[2]
    record = (
        record.replace(b'{"format":1,', b'{"format":1,"format":1,') if kind == "duplicate" else record + b"\n"
    )
    assert sc.parse_record(record) == sc.parse_record(install[2])
    with pytest.raises(Hold, match="install_projection_metadata_unavailable"):
        verify(install, record_bytes=record)


@pytest.mark.parametrize("kind", ["manifest", "record", "entries", "file-bytes"])
def test_finite_metadata_and_hash_budget(install, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    changes = {}
    if kind in ("manifest", "record"):
        changes[kind + "_bytes"] = b" " * (p.MAX_META_BYTES + 1)
    elif kind == "entries":
        monkeypatch.setattr(p, "MAX_ENTRIES", 0)
    else:
        monkeypatch.setattr(p, "MAX_FILE_BYTES", 1)
    with pytest.raises(Hold, match="install_projection_bound"):
        verify(install, **changes)


def test_invalid_fd_clean_hold(install) -> None:
    with pytest.raises(Hold, match="install_projection_tree_unavailable"):
        verify(install, root_fd=-1)


def test_projection_never_creates_taint_or_opens_store(install, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("source projection must only read the supplied private fd")

    from heterodyne.harness import verify as owner

    monkeypatch.setattr(owner, "verify_pin", forbidden)
    monkeypatch.setattr(sc, "open_install", forbidden)
    monkeypatch.setattr(sc, "read_meta", forbidden)
    monkeypatch.setattr(os, "write", forbidden)
    monkeypatch.setattr(os, "mkdir", forbidden)
    monkeypatch.setattr(os, "unlink", forbidden)
    assert verify(install).entries == 1
