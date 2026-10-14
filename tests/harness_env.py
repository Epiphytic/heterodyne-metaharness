"""Managed install test helpers (plan 4c): fixture trees built in a temp dir, a fixed sample manifest, and
the process's open file-descriptor count. Checksums are computed here at run time; none is committed."""

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path

from heterodyne.harness.resources import store_core as sc

Spec = bytes | tuple[str] | tuple[str, str]
EXECUTABLE = b"#!/bin/sh\n"     # a file whose content starts like this is made executable


def make_tree(root: Path, spec: Mapping[str, Spec]) -> Path:
    """`root` and, under it, each path of `spec`: bytes are a file (executable if they start with
    EXECUTABLE), `("link", target)` a symbolic link, `("dir",)` a directory. Parents are created."""
    root.mkdir(parents=True, exist_ok=True)
    for name, what in spec.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(what, bytes):
            path.write_bytes(what)
            path.chmod(0o755 if what.startswith(EXECUTABLE) else 0o644)
        elif len(what) == 2:
            path.symlink_to(what[1])
        else:
            path.mkdir(exist_ok=True)
    return root


def seal(root: Path) -> None:
    """The final modes `stage` gives a tree (plan 4c E20): directories 0555, files 0555 if any execute bit
    was set, else 0444. Bottom-up, ending with the root."""
    for here, dirs, files in os.walk(root, topdown=False):
        for name in files:
            path = Path(here, name)
            if not path.is_symlink():
                path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
        for name in dirs:
            path = Path(here, name)
            if not path.is_symlink():
                path.chmod(0o555)
    root.chmod(0o555)


def unseal(root: Path) -> None:
    """Make every directory under `root` writable again, so a test can change the tree or remove it."""
    if not root.is_dir() or root.is_symlink():
        return
    root.chmod(0o755)
    for here, dirs, _ in os.walk(root):
        for name in dirs:
            path = Path(here, name)
            if not path.is_symlink():
                path.chmod(0o755)


def open_dir(root: Path) -> int:
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


def sources() -> tuple[sc.Source, ...]:
    return (sc.Source("cli", "release", "codex-package-x86_64-unknown-linux-musl.tar.gz", "rust-v0.160.0",
                      "sha256:" + hashlib.sha256(b"artifact").hexdigest(), "codex-package_SHA256SUMS", False,
                      False),
            sc.Source("content", "npm", "@anthropic-ai/claude-code-linux-x64", "2.1.286",
                      "sha512-" + hashlib.sha512(b"tarball").hexdigest(), "registry.npmjs.org", True, False))


def manifest_of(root: Path) -> sc.Manifest:
    """The manifest `store_core` reads from the tree at `root`."""
    fd = open_dir(root)
    try:
        entries = sc.entries_of(fd)
    finally:
        os.close(fd)
    return sc.Manifest("codex", "0.160.0", "x86_64-unknown-linux-musl", "cli/bin/codex", sources(), entries)


def record_of(root: Path) -> sc.Record:
    fd = open_dir(root)
    try:
        return sc.record(fd)
    finally:
        os.close(fd)


def _file(path: str, content: bytes, mode: int) -> sc.Entry:
    return sc.Entry(path, "file", mode, len(content), 1, hashlib.sha256(content).hexdigest(), None)


def sample_manifest(paths: list[str] | None = None) -> sc.Manifest:
    """A fixed manifest with two sources and five entries, in canonical order: a file, a directory, an
    executable, a link and another directory; or, with `paths`, those paths as files."""
    if paths is not None:
        entries = tuple(_file(p, p.encode(), 0o444) for p in sorted(paths, key=lambda p: p.encode()))
    else:
        entries = (_file("cli/README", b"the codex package\n", 0o444),
                   sc.Entry("cli/bin", "dir", 0o555, 4096, 2, None, None),
                   _file("cli/bin/codex", EXECUTABLE + b"exit 0\n", 0o555),
                   sc.Entry("cli/codex", "link", 0o777, len("bin/codex"), 1, None, "bin/codex"),
                   sc.Entry("content", "dir", 0o555, 4096, 2, None, None))
    return sc.Manifest("codex", "0.160.0", "x86_64-unknown-linux-musl", "cli/bin/codex", sources(), entries)


def fd_count() -> int:
    return len(list(Path("/proc/self/fd").iterdir()))
