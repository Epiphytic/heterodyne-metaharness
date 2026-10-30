"""Fixed sealed admind runtime checks; no host effect adapter or caller-selected roots."""

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from heterodyne.actions.codec import InvalidAction

# A host-qualified profile, never an ambient install/root override.
RELEASE = Path(  # install-agnostic: allow=home-path - fixed dormant H profile
    "/home/openclaw/.local/share/heterodyne/releases/"  # install-agnostic: allow=home-path - fixed H
    "5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156"
)
SOURCE = RELEASE / "src"
SITE = RELEASE / ".venv/lib/python3.12/site-packages"
PYTHON = RELEASE / ".venv/bin/python"
LIMIT = 128 * 1024 * 1024
TREE_LIMIT = 512 * 1024 * 1024
_CACHE = re.compile(r"[A-Za-z0-9_]+\.cpython-[0-9]{2,3}(?:\.opt-[0-2])?\.pyc")


def stream_hash(path: Path, *, limit: int = LIMIT) -> str:
    """Bounded streaming hash, with bytes and custody bound to one descriptor."""
    for entry in path.parents:
        if entry.is_symlink() or entry.stat().st_mode & 0o022:
            raise InvalidAction("unsafe-runtime-ancestor")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_mode & 0o022
            or before.st_uid not in {0, os.geteuid()}
            or before.st_size > limit
        ):
            raise InvalidAction("unsafe-runtime-file")
        count, digest = 0, hashlib.sha256()
        while chunk := os.read(fd, 65536):
            count += len(chunk)
            if count > limit:
                raise InvalidAction("runtime-file-bound")
            digest.update(chunk)
        fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_uid",
            "st_mode",
            "st_nlink",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        for after in (os.fstat(fd), path.stat(follow_symlinks=False)):
            if count != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields):
                raise InvalidAction("runtime-file-changed")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _walk_error(error: OSError) -> None:
    raise error


def _checked_entries(parent: str, dirs: list[str], files: list[str]) -> None:
    for name in (*dirs, *files):
        if (Path(parent) / name).is_symlink():
            raise InvalidAction("runtime-symlink-refused")
    if Path(parent).name == "__pycache__":
        # Canonical cache entries cannot be imported as legacy sourceless modules.
        # The child uses a fresh private prefix, so it never reads these caches.
        if dirs or any(not _CACHE.fullmatch(name) for name in files):
            raise InvalidAction("runtime-bytecode-refused")
    elif any(name.endswith((".pyc", ".pyo")) for name in files):
        raise InvalidAction("runtime-bytecode-refused")


def tree_digest(root: Path) -> str:
    """Pinned source/native inventory; canonical caches are checked but never executed."""
    if not root.is_dir() or root.is_symlink():
        raise InvalidAction("runtime-package-missing")
    rows: list[tuple[str, str]] = []
    total = count = 0
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
        count += len(dirs) + len(files)
        if count > 40000:
            raise InvalidAction("runtime-package-bound")
        _checked_entries(parent, dirs, files)
        dirs.sort()
        for name in sorted(files):
            path = Path(parent) / name
            total += path.stat(follow_symlinks=False).st_size
            if len(rows) >= 20000 or total > TREE_LIMIT:
                raise InvalidAction("runtime-package-bound")
            observed = stream_hash(path)
            if Path(parent).name != "__pycache__":
                rows.append((path.relative_to(root).as_posix(), observed))
    if not rows:
        raise InvalidAction("runtime-package-empty")
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def fetch(ask_id: str, executable: Path) -> bytes:
    """Source-only isolated CLI readback: no site startup, .pth or cached bytecode.

    A new private cache prefix hides all pre-existing interpreter/stdlib caches;
    -B prevents writes there. -S prevents .pth/sitecustomize execution. Only the
    fixed, sealed source and site roots are added. The caller has inventoried both
    trees, refusing legacy sourceless bytecode; canonical caches are ignored.
    The executable is the exact resolved path captured once by the fixed reader.
    """
    code = (
        "import sys; sys.path.insert(0,"
        + repr(str(SITE))
        + "); sys.path.insert(0,"
        + repr(str(SOURCE))
        + "); "
        "from heterodyne.admind.cli import main; sys.exit(main())"
    )
    with tempfile.TemporaryDirectory(prefix="heterodyne-native-read-") as cache:
        result = subprocess.run(
            [
                str(executable),
                "-I",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=" + cache,
                "-c",
                code,
                "ask",
                "get",
                ask_id,
                "--json",
            ],
            env={
                "HOME": str(RELEASE.parents[4]),
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C.UTF-8",
            },
            capture_output=True,
            timeout=30,
            check=False,
        )
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024 or len(result.stderr) > 4 * 1024 * 1024:
        raise InvalidAction("native-cli-unavailable")
    return result.stdout
