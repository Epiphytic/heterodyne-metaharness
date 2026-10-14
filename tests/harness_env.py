"""Managed install test helpers (plan 4c): fixture trees built in a temp dir, a fixed sample manifest, and
the process's open file-descriptor count. Checksums are computed here at run time; none is committed."""

import base64
import contextlib
import gc
import gzip
import hashlib
import io
import json
import os
import subprocess
import tarfile
from collections.abc import Iterator, Mapping, Sequence
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
    """This process's open fds, after a collection: garbage another test left (its sockets, pipes or files)
    is closed now, not by a collection inside the count."""
    gc.collect()
    return len(list(Path("/proc/self/fd").iterdir()))


# --- artifacts and parts for `stage` (plan 4c Task 3) ---

# S9-1: the Codex package archive has no top-level prefix, and only files and directories.
CODEX_STRIP = ""
CODEX_PACKAGE = {"layoutVersion": 1, "version": "0.160.0", "target": "x86_64-unknown-linux-musl",
                 "variant": "codex", "entrypoint": "bin/codex", "resourcesDir": "codex-resources",
                 "pathDir": "codex-path"}
CODEX_TREE: dict[str, Spec] = {
    "codex-package.json": json.dumps(CODEX_PACKAGE).encode(),
    "bin/codex": EXECUTABLE + b"echo codex\n",
    "bin/codex-code-mode-host": EXECUTABLE + b"echo host\n",
    "codex-path/rg": EXECUTABLE + b"echo rg\n",
    "codex-resources/bwrap": EXECUTABLE + b"echo bwrap\n",
    "codex-resources/zsh": EXECUTABLE + b"echo zsh\n",
    "codex-resources/voice/README": b"voice\n",
}
CODEX_LAYOUT = sc.Layout("codex-package.json", "version", "target", "entrypoint", None)

# F6: the Claude Code platform package's tarball, whose tree is under `package/`.
CLAUDE_STRIP = "package/"
CLAUDE_PACKAGE = {"name": "@anthropic-ai/claude-code-linux-x64", "version": "2.1.286", "os": ["linux"],
                  "cpu": ["x64"], "libc": ["glibc"]}
CLAUDE_TREE: dict[str, Spec] = {"package.json": json.dumps(CLAUDE_PACKAGE).encode(),
                                "claude": EXECUTABLE + b"echo claude\n"}
CLAUDE_LAYOUT = sc.Layout("package.json", "version", None, None, "claude")
CLAUDE_TARGET = "linux-x64"             # the recipe's (plan 4c Q6): package.json names no target

LOGIN_NAMES = frozenset({"auth.json", ".credentials.json"})


def tar_of(spec: Mapping[str, Spec], *, prefix: str = "", gz: bool = True,
           extra: Sequence[tarfile.TarInfo] = (), dirs: bool = True) -> bytes:
    """A tar (gzipped unless `gz` is false) of `spec`, each name under `prefix`, with a directory member
    before each directory's contents (unless `dirs` is false), then the members in `extra` (empty)."""
    out = io.BytesIO()
    # The gzip header's mtime is 0, not now: the same spec always makes the same bytes, and checksum.
    zipped = gzip.GzipFile(fileobj=out, mode="wb", mtime=0) if gz else contextlib.nullcontext(out)
    with zipped as z, tarfile.open(fileobj=z, mode="w", format=tarfile.PAX_FORMAT) as t:
        made: set[str] = set()
        for name, what in spec.items():
            parts = name.split("/")
            for i in range(1, len(parts)):
                d = "/".join(parts[:i])
                if dirs and d not in made:
                    made.add(d)
                    t.addfile(_info(prefix + d, tarfile.DIRTYPE, 0o755))
            if isinstance(what, bytes):
                info = _info(prefix + name, tarfile.REGTYPE, 0o755 if what.startswith(EXECUTABLE) else 0o644)
                info.size = len(what)
                t.addfile(info, io.BytesIO(what))
            elif len(what) == 2:
                info = _info(prefix + name, tarfile.SYMTYPE, 0o777)
                info.linkname = what[1]
                t.addfile(info)
            elif dirs and name not in made:
                made.add(name)
                t.addfile(_info(prefix + name, tarfile.DIRTYPE, 0o755))
        for info in extra:
            t.addfile(info, io.BytesIO(b"") if info.isreg() else None)
    return out.getvalue()


def _info(name: str, kind: bytes, mode: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    return info


def member(name: str, kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    info = _info(name, kind, 0o644)
    info.linkname = linkname
    return info


def sha256_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sri_of(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def part_of(part: str, data: bytes, *, fmt: sc.PartFormat = "tar.gz", strip: str = "",
            kind: sc.SourceKind = "release", checksum: str | None = None,
            git_commit: str | None = None) -> sc.Part:
    """A part whose source records `data`'s sha256 (or `checksum`), as `wsd` would build it."""
    checksum = checksum if checksum is not None else sha256_of(data)
    origin = "fixture SHA256SUMS"
    source = sc.Source(part, kind, part.rsplit("/", 1)[-1], "fixture", checksum, origin, False, False)
    return sc.Part(part, fmt, strip, checksum, origin, False, source, git_commit)


def codex_part(tree: Mapping[str, Spec] | None = None) -> tuple[sc.Part, bytes]:
    data = tar_of(CODEX_TREE if tree is None else tree, prefix=CODEX_STRIP)
    return part_of("cli", data, strip=CODEX_STRIP), data


def claude_part(tree: Mapping[str, Spec] | None = None) -> tuple[sc.Part, bytes]:
    data = tar_of(CLAUDE_TREE if tree is None else tree, prefix=CLAUDE_STRIP, dirs=False)
    return part_of("cli", data, fmt="tgz", strip=CLAUDE_STRIP, kind="npm", checksum=sri_of(data)), data


def store_dir(tmp_path: Path) -> Path:
    store = tmp_path / "store"
    store.mkdir(mode=0o755, exist_ok=True)
    return store


@contextlib.contextmanager
def artifact_fds(tmp_path: Path, blobs: Sequence[bytes]) -> Iterator[list[int]]:
    """Each of `blobs` as a file in `tmp_path`, opened read-only, as `wsd` hands artifacts over."""
    fds: list[int] = []
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        for i, data in enumerate(blobs):
            path = tmp_path / f"artifact-{i}"
            path.write_bytes(data)
            fds.append(os.open(path, os.O_RDONLY | os.O_CLOEXEC))
        yield fds
    finally:
        for fd in fds:
            os.close(fd)


def stage(tmp_path: Path, layout: sc.Layout, parts: Sequence[tuple[sc.Part, bytes | Sequence[bytes]]], *,
          adapter: str = "codex", login_names: frozenset[str] = LOGIN_NAMES,
          verity: bool = False, target: str | None = None) -> sc.Staged:
    """`store_core.stage` into `tmp_path / "store"` with each part's artifact (or a git part's two), and the
    recipe's target: Codex's from its metadata unless `target` is given, else Claude's."""
    if target is None:
        target = str(CODEX_PACKAGE["target"]) if layout.target_field else CLAUDE_TARGET
    blobs: list[bytes] = []
    for _, data in parts:
        blobs.extend([data] if isinstance(data, bytes) else data)
    with artifact_fds(tmp_path, blobs) as fds:
        return sc.stage(store_dir(tmp_path), adapter, layout, [p for p, _ in parts], fds, login_names,
                        verity=verity, target=target)


def staged_root(tmp_path: Path, staged: sc.Staged) -> Path:
    return tmp_path / "store" / ".staging" / staged.id


def staged_manifest(tmp_path: Path, staged: sc.Staged) -> sc.Manifest:
    return sc.parse((tmp_path / "store" / ".staged" / f"{staged.id}.json").read_bytes())


# --- git repositories for git content sources (plan 4c E6) ---

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], env=GIT_ENV, capture_output=True, check=True,
                          timeout=60).stdout


def make_repo(root: Path, *, object_format: str = "sha1") -> tuple[Path, str]:
    subprocess.run(["git", "init", "-q", f"--object-format={object_format}", str(root)], env=GIT_ENV,
                   check=True, capture_output=True, timeout=60)
    (root / "README.md").write_bytes(b"# a skill\n")
    (root / "run.sh").write_bytes(EXECUTABLE + b"exit 0\n")
    (root / "run.sh").chmod(0o755)
    (root / "nested" / "deeper").mkdir(parents=True)
    (root / "nested" / "deeper" / "file.txt").write_bytes(b"deep\n")
    (root / "nested" / "a b").write_bytes(b"spaced\n")
    (root / "nested-sibling").write_bytes(b"sorts after the tree nested/\n")
    (root / "link").symlink_to("nested/deeper/file.txt")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root, git(root, "rev-parse", "HEAD").decode().strip()


# --- publish, recovery and the stores (plan 4c Task 4) ---

def store_of(tmp_path: Path) -> Path:
    return tmp_path / "store"


def install_name(tmp_path: Path, staged: sc.Staged) -> str:
    m = staged_manifest(tmp_path, staged)
    return sc.install_name(m.version, staged.digest)


def install_root(tmp_path: Path, name: str, adapter: str = "codex") -> Path:
    return store_of(tmp_path) / adapter / name


def meta_path(tmp_path: Path, name: str, what: str, adapter: str = "codex") -> Path:
    """The install's meta `manifest` or `record` file (plan 4c E2)."""
    return store_of(tmp_path) / adapter / ".meta" / f"{name}.{what}.json"


def rewrite(path: Path, data: bytes | None = None) -> None:
    """`path` rewritten in place with the same size: its first byte flipped, or exactly `data`. The mode is
    lifted for the write and then restored, as a writer that owns the file would."""
    mode = path.stat().st_mode & 0o7777
    old = path.read_bytes()
    new = data if data is not None else bytes([old[0] ^ 1]) + old[1:]
    assert len(new) == len(old)
    path.chmod(0o644)
    with path.open("r+b") as f:
        f.write(new)
    path.chmod(mode)


class RewriteAfterFirstFile:
    """A `read` for `rehash` that rewrites `path` (the first file hashed) once the next file is read: a
    change made after that file was checked."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.eofs = 0
        self.done = False

    def __call__(self, fd: int, n: int) -> bytes:
        if self.eofs == 1 and not self.done:
            self.done = True
            rewrite(self.path)
        chunk = os.read(fd, n)
        if not chunk:
            self.eofs += 1
        return chunk


FIRST_FILE = "cli/bin/codex"          # the first file of CODEX_TREE in canonical order, the first one hashed
