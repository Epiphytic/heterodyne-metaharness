"""Private scanner construction and evidence; never starts a scanner gate."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import tempfile
from pathlib import Path
from typing import Any

MINIMUM_FREE = 2 * 1024**3
MAXIMUM_ARCHIVE = MINIMUM_FREE
CHILDREN = ("h", "t", "r", "y", "p")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def private_directory(path: Path) -> dict[str, int | str]:
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode), "Real private directory required")
    require(
        info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
        "Private directory owner/mode mismatch",
    )
    require(path.resolve() == path, "Private directory path must be canonical")
    return {
        "path": str(path),
        "device": info.st_dev,
        "inode": info.st_ino,
        "uid": info.st_uid,
        "mode": stat.S_IMODE(info.st_mode),
    }


class LayoutCreationError(OSError):
    """Construction failed after allocation; caller must retain the partial root."""

    def __init__(self, layout: dict[str, Any], cause: Exception):
        super().__init__(str(cause))
        self.layout = layout


def create_layout() -> dict[str, Any]:
    parent = Path("/tmp")  # noqa: S108 - approved exact root-owned01777 parent; exclusive mkdtemp below
    info = parent.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == 0
        and stat.S_IMODE(info.st_mode) == 0o1777
        and parent.resolve() == parent,
        "Real root-owned /tmp mode01777 required",
    )
    root = Path(tempfile.mkdtemp(prefix="hz", dir=parent))
    rows: dict[str, Any] = {"root": {"path": str(root)}}
    try:
        root.chmod(0o700)
        require(len(os.fsencode(root)) <= 16, "Private root exceeds16bytes")
        rows["root"] = private_directory(root)
        for name in CHILDREN:
            child = root / name
            child.mkdir(mode=0o700)
            require(len(os.fsencode(child)) <= 18, "Private child exceeds18bytes")
            rows[name] = private_directory(child)
    except (OSError, ValueError) as error:
        raise LayoutCreationError(rows, error) from error
    return rows


def filesystem_preflight(layout: dict[str, Any]) -> dict[str, Any]:
    root = Path(layout["root"]["path"])
    require(private_directory(root) == layout["root"], "Scratch identity changed")
    capacity = os.statvfs(root)
    free = capacity.f_bavail * capacity.f_frsize
    require(free >= MINIMUM_FREE, "Private scratch requires2GiB available")
    # Execution permission is tested on a private file, without invoking a shell.
    executable = root / "exec-check"
    descriptor = os.open(executable, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    try:
        os.write(descriptor, b"#!/bin/sh\nexit 0\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    import subprocess

    result = subprocess.run(
        [str(executable)], env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=5, check=False
    )
    require(result.returncode == 0, "Scratch executable preflight failed")
    mounts = Path("/proc/self/mountinfo").read_bytes()
    return {
        "free_bytes": free,
        "executable_exit": result.returncode,
        "mountinfo_sha256": digest(mounts),
        "mountinfo": mounts.decode(),
        "parent": tmp_parent_evidence(root.parent),
    }


def environment(
    layout: dict[str, Any],
    python: Path,
    active: Path,
    plugins: Path,
    collection: Path,
    contract: dict[str, Any],
) -> dict[str, str]:
    for key, row in layout.items():
        require(private_directory(Path(row["path"])) == row, "Scratch identity changed: " + key)
    runtime = Path(layout["r"]["path"])
    require(not list(runtime.iterdir()), "Private runtime must initially be empty")
    values = {
        "HOME": layout["h"]["path"],
        "PATH": str(python.parent) + ":/usr/bin:/bin",
        "TMPDIR": layout["t"]["path"],
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "HZ_REQUIRE_TMUX": "1",
        "HYPOTHESIS_STORAGE_DIRECTORY": layout["y"]["path"],
        "PYTHONPATH": str(active / "src") + os.pathsep + str(plugins),
        "SCANNER_COLLECTION": str(collection),
        "XDG_RUNTIME_DIR": str(runtime),
    }
    validate_environment(values, contract)
    return values


def validate_environment(values: dict[str, str], contract: dict[str, Any]) -> None:
    expected = contract["environment_allowlist"]
    require(
        len(expected) == len(set(expected)) == 14 and set(values) == set(expected),
        "Exactly the approved14environment keys required",
    )


def validate_result(result: dict[str, Any], contract: dict[str, Any]) -> None:
    fields = contract["required_fields"]
    require(len(fields) == len(set(fields)) == 57, "Exactly57required fields in supplement")
    missing = [name for name in fields if result.get(name) is None]
    require(not missing, "Missing required evidence: " + ",".join(missing))


def construction_probes(contract: dict[str, Any]) -> dict[str, Any]:
    """Four recovery-only rejections, separate from the preserved19 verifier probes."""
    valid = dict.fromkeys(contract["environment_allowlist"], "construction-fixture")
    validate_environment(valid, contract)
    fields = dict.fromkeys(contract["required_fields"], "construction-fixture")
    validate_result(fields, contract)
    thirteen = valid.copy()
    del thirteen["XDG_RUNTIME_DIR"]
    missing = fields.copy()
    del missing["scratch_archive_inventory_sha256"]
    cases = (
        ("thirteen-keys", lambda: validate_environment(thirteen, contract)),
        (
            "fifteen-keys",
            lambda: validate_environment(valid | {"DBUS_SESSION_BUS_ADDRESS": "forbidden"}, contract),
        ),
        ("missing-added-field", lambda: validate_result(missing, contract)),
        ("socket101-before-bind", lambda: socket_budget(Path("/" + "x" * 100))),
    )
    rejected = []
    for name, probe in cases:
        try:
            probe()
        except ValueError:
            rejected.append(name)
        else:
            raise ValueError("Construction probe accepted: " + name)
    return {"rejected": rejected, "count": 4, "support_source_sha256": digest(Path(__file__).read_bytes())}


def socket_budget(path: Path) -> int:
    length = len(os.fsencode(path))
    require(length <= 100, "Socket path exceeds100encodedbytes")
    return length


def socket_construction_probe(layout: dict[str, Any]) -> dict[str, Any]:
    root = Path(layout["root"]["path"])
    path = root / ("s" * (100 - len(os.fsencode(root)) - 1))
    require(socket_budget(path) == 100, "Exact100byte fixture required")
    with socket.socket(socket.AF_UNIX) as endpoint:
        endpoint.bind(str(path))
    path.unlink()
    refused = False
    try:
        socket_budget(Path(str(path) + "x"))
    except ValueError:
        refused = True
    require(refused, "Over-budget socket must be refused before bind")
    return {"bound_encoded_bytes": 100, "over_budget_rejected_before_bind": True}


def tmp_parent_evidence(parent: Path) -> dict[str, Any]:
    info = parent.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and parent.resolve() == parent
        and info.st_uid == 0
        and stat.S_IMODE(info.st_mode) == 0o1777,
        "Scratch parent changed",
    )
    return {
        "path": str(parent),
        "uid": info.st_uid,
        "mode": stat.S_IMODE(info.st_mode),
        "device": info.st_dev,
        "inode": info.st_ino,
    }


def walk_error(error: OSError) -> None:
    raise error


def source_candidates(source: Path) -> dict[str, str]:
    """Candidates are prompts for full manual reachability review, never a completeness proof.

    Include all shell/config lines and indirect Python endpoint calls anywhere in the tree,
    including scripts reached from tests. Refuse links rather than inspecting external targets.
    """
    candidates = {}
    tokens = (
        b".sock",
        b"AF_UNIX",
        b"tmux",
        b"short_dir",
        b".bind(",
        b".connect(",
        b"unix_connection",
        b"unix_server",
        b"socket_path",
        b"socketpath",
        b"listen_url",
    )
    for parent, dirs, files in os.walk(source, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in (".git", ".venv", "__pycache__", ".pytest_cache", ".hypothesis")
        )
        for name in dirs:
            require(not (Path(parent) / name).is_symlink(), "Source inventory refuses symlinked directories")
        for name in sorted(files):
            path = Path(parent) / name
            selected = path.suffix in (
                ".py",
                ".sh",
                ".bash",
                ".toml",
                ".yaml",
                ".yml",
                ".ini",
                ".cfg",
                ".service",
                ".socket",
            )
            # Extensionless launchers must not evade shell/config inventory.
            if not selected and path.suffix:
                continue
            require(not path.is_symlink(), "Source inventory refuses symlinked files")
            data = path.read_bytes()
            if not selected and not data.startswith(b"#!"):
                continue
            for number, line in enumerate(data.splitlines(), 1):
                if line.strip() and (path.suffix != ".py" or any(token in line for token in tokens)):
                    candidates[f"{path.relative_to(source)}:{number}"] = digest(line)
    return candidates


def validate_inventory(
    source: Path,
    layout: dict[str, Any],
    inventory: dict[str, Any],
    *,
    raw: bytes | None = None,
    measured: bool = False,
    candidates: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Require reviewed derivations for every source candidate, without guessing bounds."""
    candidates = source_candidates(source) if candidates is None else candidates
    require(inventory.get("source_candidates") == candidates, "Source inventory coverage changed")
    require(bool(candidates), "Empty source inventory cannot admit execution")
    require(inventory.get("source_root") == str(source), "Inventory source root mismatch")
    rows = inventory.get("derivations")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Source-backed derivations required")
    covered: set[str] = set()
    measured_paths = []
    source_lines: dict[str, list[bytes]] = {}
    basetemp = Path(layout["p"]["path"])
    for row in rows:
        require(isinstance(row, dict) and bool(row.get("reason")), "Missing source derivation")
        references = row.get("references")
        if not isinstance(references, list) or not references:
            raise ValueError("Source references required")
        require(
            all(isinstance(ref, str) and ref in candidates for ref in references), "Unknown source reference"
        )
        lines = []
        for reference in references:
            filename, number = reference.rsplit(":", 1)
            if filename not in source_lines:
                source_lines[filename] = (source / filename).read_bytes().splitlines()
            line = source_lines[filename][int(number) - 1]
            require(digest(line) == candidates[reference], "Inventory reference bytes changed")
            lines.append(line)
        covered.update(references)
        kind = row.get("kind")
        require(kind in ("socket", "tmux", "non-endpoint"), "Unknown inventory derivation kind")
        if any(b"tmux" in line.lower() for line in lines):
            require(kind == "tmux", "tmux endpoint requires isolation classification")
        elif any(
            any(
                token in line.lower()
                for token in (
                    b".sock",
                    b"af_unix",
                    b".bind(",
                    b".connect(",
                    b"unix_connection",
                    b"unix_server",
                    b"socket_path",
                    b"socketpath",
                    b"listenstream=",
                )
            )
            for line in lines
        ):
            require(kind == "socket", "socket endpoint requires bounded classification")
        if kind == "socket":
            require(row.get("base") in ("h", "p", "t", "r", "root"), "Unbound socket base")
            component = row.get("case_component_bytes")
            suffix = row.get("suffix_bytes")
            if type(component) is not int or type(suffix) is not int:
                raise ValueError("Unbounded socket derivation")
            require(component >= 0 and suffix > 0, "Invalid socket derivation")
            base = Path(layout[row["base"]]["path"])
            require(len(os.fsencode(base)) <= (16 if row["base"] == "root" else 18), "Socket base too long")
            maximum = (16 if row["base"] == "root" else 18) + (1 if component else 0) + component + suffix
            require(
                maximum <= 100 and row.get("maximum_encoded_bytes") == maximum,
                "Socket derivation exceeds budget or does not match",
            )
            if measured:
                measured_paths.append(measured_socket_path(row, base))
        if kind == "tmux":
            require(
                row.get("isolation") in ("fixture-owned-S", "fixture-owned-L", "audited-guard"),
                "Unbound default tmux server",
            )
            require(
                bool(row.get("guard_references"))
                and all(ref in candidates for ref in row["guard_references"]),
                "Missing tmux guard source",
            )
    require(covered == set(candidates), "Unreviewed socket/tmux inventory candidates")
    require(inventory.get("reviewed_by_controller") is True, "Controller inventory review required")
    return {
        "inventory_sha256": digest(
            raw if raw is not None else json.dumps(inventory, sort_keys=True).encode()
        ),
        "measured_paths": measured_paths,
        "candidates": len(candidates),
        "derivations": len(rows),
        "basetemp": str(basetemp),
        "receipt": inventory,
    }


def measured_socket_path(row: dict[str, Any], base: Path) -> dict[str, Any]:
    component = row.get("worst_case_component")
    suffix = row.get("worst_case_suffix")
    if not isinstance(component, str) or not isinstance(suffix, str):
        raise ValueError("Source-backed reconstructed maximum path required")
    require(
        len(os.fsencode(component)) == row["case_component_bytes"]
        and len(os.fsencode(suffix)) == row["suffix_bytes"],
        "Reconstructed path lengths differ from reviewed bounds",
    )
    require(
        not component.startswith("/")
        and ".." not in component.split("/")
        and suffix.startswith("/")
        and ".." not in suffix.split("/")
        and "\0" not in component + suffix,
        "Reconstructed path must remain in its private base",
    )
    path = Path(str(base) + ("/" + component if component else "") + suffix)
    length = socket_budget(path)
    return {
        "path": str(path),
        "encoded_bytes": length,
        "reviewed_maximum": row["maximum_encoded_bytes"],
        "margin_to_100": 100 - length,
        "references": row["references"],
    }


def directory_entries(root_fd: int, parent: str = "."):
    """Borrow root_fd; traverse depth-first with one owned descriptor per level."""
    names = sorted(os.listdir(root_fd))  # noqa: PTH208 - descriptor-relative; Path would reopen by name
    yield parent, names, root_fd
    for name in names:
        info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                opened = os.fstat(child)
                require(
                    (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), "Scratch directory changed"
                )
                yield from directory_entries(child, str(Path(parent) / name))
            finally:
                os.close(child)


def archive_scratch(
    layout: dict[str, Any], destination: Path, maximum_bytes: int = MAXIMUM_ARCHIVE
) -> dict[str, Any]:
    """Copy bounded regular files through nofollow descriptors; retain link metadata only."""
    root = Path(layout["root"]["path"])
    require(private_directory(root) == layout["root"], "Scratch root identity changed")
    require(
        type(maximum_bytes) is int and 0 <= maximum_bytes <= MAXIMUM_ARCHIVE, "Invalid archive byte limit"
    )
    require(
        destination.is_absolute()
        and destination.resolve() == destination
        and not destination.is_relative_to(root),
        "Archive must be outside scratch",
    )
    destination.mkdir(mode=0o700)
    private_directory(destination)
    inventory: list[dict[str, Any]] = []
    total = 0
    entries = 0
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for parent, names, parent_fd in directory_entries(root_fd):
            relative_parent = Path(parent)
            target_parent = destination / relative_parent
            if relative_parent.parts:
                target_parent.mkdir(mode=0o700)
            for name in names:
                entries += 1
                require(entries <= 65536, "Scratch archive exceeds entry limit")
                info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                relative = str(relative_parent / name)
                row: dict[str, Any] = {
                    "path": relative,
                    "mode": info.st_mode,
                    "device": info.st_dev,
                    "inode": info.st_ino,
                }
                if stat.S_ISLNK(info.st_mode):
                    row.update(kind="symlink", target=os.readlink(name, dir_fd=parent_fd))
                elif stat.S_ISDIR(info.st_mode):
                    row["kind"] = "directory"
                elif stat.S_ISREG(info.st_mode):
                    require(total + info.st_size <= maximum_bytes, "Scratch archive exceeds byte limit")
                    source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
                    try:
                        opened = os.fstat(source_fd)
                        require(
                            stat.S_ISREG(opened.st_mode)
                            and (opened.st_dev, opened.st_ino, opened.st_size)
                            == (info.st_dev, info.st_ino, info.st_size),
                            "Scratch file changed",
                        )
                        hasher = hashlib.sha256()
                        count = 0
                        with (target_parent / name).open("xb") as output:
                            Path(output.name).chmod(0o600)
                            while block := os.read(source_fd, min(1024 * 1024, maximum_bytes - total + 1)):
                                total += len(block)
                                count += len(block)
                                require(total <= maximum_bytes, "Scratch archive grew beyond byte limit")
                                output.write(block)
                                hasher.update(block)
                            output.flush()
                            os.fsync(output.fileno())
                        after = os.fstat(source_fd)
                        require(
                            count == info.st_size and after.st_mtime_ns == info.st_mtime_ns,
                            "Scratch changed during archive",
                        )
                        row.update(kind="regular", bytes=count, sha256=hasher.hexdigest())
                    finally:
                        os.close(source_fd)
                else:
                    row["kind"] = "excluded-special"
                    if stat.S_ISSOCK(info.st_mode):
                        length = len(os.fsencode(root / relative))
                        row.update(socket_encoded_bytes=length, margin_to_100=100 - length)
                inventory.append(row)
    finally:
        os.close(root_fd)
    data = json.dumps({"bytes": total, "entries": inventory}, sort_keys=True).encode()
    with (destination / "inventory.json").open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    destination_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for _, _, descriptor in directory_entries(destination_fd):
            os.fsync(descriptor)
    finally:
        os.close(destination_fd)
    return {
        "sha256": digest(data),
        "bytes": total,
        "entries": len(inventory),
        "surviving_socket_paths": [row for row in inventory if "socket_encoded_bytes" in row],
    }
