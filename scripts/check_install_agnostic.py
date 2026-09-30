#!/usr/bin/env python3
"""Fail if tracked files contain install-specific values (ADR 0001 §15) or model names in src (§4.1).

Usage: check_install_agnostic.py [--root DIR] [--extra FILE] [paths...]

- With no paths, scans `git ls-files` under --root. Prints `path:line: rule: excerpt` per hit and
  exits 1 if there were any (2 on usage errors).
- `examples/`, `tests/fixtures/` and `LICENSE` are exempt (§15). So is this script, whose regex source
  necessarily spells the patterns it looks for.
- --extra FILE (default `$HETERODYNE_CONFIG_DIR/leakcheck.txt`, host-local, never committed) lists literal
  strings, one per line (blank lines and `#` comments ignored), such as a username, hostname or group IDs.
- A line may carry `install-agnostic: allow=<rule>[,<rule>...]` to suppress the named rules on that line
  only. Each use must say why in the same comment. It cannot suppress `local-denylist`.
- Fails closed: nothing is silently skipped.
  - Invalid UTF-8 is decoded with surrogateescape, so every other line is still scanned.
  - Binary files (a NUL byte in the first 8 KiB, as git decides) are scanned byte-wise as latin-1 for
    every rule and literal; allow markers do not apply in them.
  - A symlink is scanned as its target string (which is what git stores).
  - Submodules, unreadable or missing paths are reported as their own rule (`submodule`, `unreadable`).
  - A failing `git ls-files` exits 2.
"""

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

# The default deny-list lives in the same host config dir `heterodyne` uses. Load paths.py by file so
# this script also runs under a bare system python3 (pre-commit) without the package installed.
_PATHS_FILE = Path(__file__).resolve().parent.parent / "src" / "heterodyne" / "config" / "paths.py"
_PATHS_SPEC = importlib.util.spec_from_file_location("_heterodyne_paths", _PATHS_FILE)
if _PATHS_SPEC is None or _PATHS_SPEC.loader is None:
    raise ImportError(f"cannot load {_PATHS_FILE}")
_paths = importlib.util.module_from_spec(_PATHS_SPEC)
_PATHS_SPEC.loader.exec_module(_paths)

RULES = {
    "home-path": re.compile(r"(?<![\w$])/(?:home|Users)/[A-Za-z0-9._-]+|(?<![\w$])/root/"),
    "ip-port": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b"),
    "email": re.compile(r"\b[\w.+-]+@(?!example\.(?:com|org|net)\b)[\w-]+\.[\w.-]+\b"),
    "npub": re.compile(r"\bnpub1[02-9ac-hj-np-z]{58}\b"),
    "nsec": re.compile(r"\bnsec1[02-9ac-hj-np-z]{58}\b"),
    "did": re.compile(r"\bdid:key:z6Mk[1-9A-HJ-NP-Za-km-z]{40,}"),
    "radicle-id": re.compile(r"\brad:z[1-9A-HJ-NP-Za-km-z]{20,}"),
}
SRC_RULES = {
    "model-name": re.compile(r"\b(?:gpt-\d[\w.-]*|claude-(?:opus|sonnet|haiku|fable)[\w.-]*|o\d-[\w.-]+)\b"),
}
LOCAL = "local-denylist"
ALLOW = re.compile(r"install-agnostic: allow=([\w,-]+)")
BINARY_SNIFF = 8000  # bytes; git's buffer_is_binary() heuristic
EXEMPT_DIRS = ("examples/", "tests/fixtures/")
EXEMPT_FILES = ("LICENSE", "scripts/check_install_agnostic.py")


class GitError(Exception):
    pass


def tracked(root: Path) -> list[tuple[str, str]]:
    """(mode, path) for every index entry. Raises GitError if git fails."""
    cmd = ["git", "-C", str(root), "ls-files", "-z", "--stage"]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip()
        raise GitError(f"git ls-files failed in {root}: {stderr}") from exc
    except OSError as exc:
        raise GitError(f"git ls-files failed in {root}: {exc}") from exc
    entries: list[tuple[str, str]] = []
    for rec in out.stdout.split(b"\0"):
        if rec:
            meta, _, path = rec.partition(b"\t")
            entries.append((meta.split(b" ")[0].decode(), os.fsdecode(path)))
    return entries


def default_extra() -> Path | None:
    path = Path(_paths.config_dir(os.environ)) / "leakcheck.txt"
    return path if path.exists() else None


def load_literals(path: Path | None) -> list[str]:
    if path is None:
        return []
    lines = (s.strip() for s in path.read_text().splitlines())
    return [s for s in lines if s and not s.startswith("#")]


def exempt(rel: str) -> bool:
    return rel.startswith(EXEMPT_DIRS) or rel in EXEMPT_FILES


def scan_line(
    line: str, rules: dict[str, re.Pattern[str]], literals: list[str], markers: bool = True
) -> list[str]:
    allowed = {r for m in ALLOW.finditer(line) for r in m.group(1).split(",")} - {LOCAL} if markers else set()
    found = [name for name, rx in rules.items() if name not in allowed and rx.search(line)]
    return found + [LOCAL for lit in literals if lit in line]


def read_lines(path: Path, literals: list[str]) -> tuple[list[str], list[str], bool]:
    """Return (lines, literals-in-matching-encoding, is_binary). Raises OSError if unreadable."""
    if path.is_symlink():
        return [str(path.readlink())], literals, False
    data = path.read_bytes()
    if b"\0" in data[:BINARY_SNIFF]:
        # Byte-level scan: latin-1 maps each byte to one code point, so ASCII patterns still match and
        # UTF-8 literals match once re-expressed the same way.
        return data.decode("latin-1").splitlines(), [lit.encode().decode("latin-1") for lit in literals], True
    return data.decode("utf-8", errors="surrogateescape").splitlines(), literals, False


def excerpt(line: str) -> str:
    text = line.strip()[:100].encode("utf-8", errors="backslashreplace").decode("utf-8", errors="replace")
    return "".join(c if c.isprintable() else "?" for c in text)


def scan_file(root: Path, rel: str, mode: str | None, literals: list[str]) -> list[str]:
    if mode == "160000":
        return [f"{rel}:0: submodule: contents live outside this repo and are not scanned"]
    try:
        lines, lits, binary = read_lines(root / rel, literals)
    except OSError as exc:
        return [f"{rel}:0: unreadable: {type(exc).__name__}: {exc.strerror or exc}"]
    rules = {**RULES, **(SRC_RULES if rel.startswith("src/") else {})}
    kind = " (binary)" if binary else ""
    return [f"{rel}:{n}: {rule}{kind}: {excerpt(line)}"
            for n, line in enumerate(lines, 1)
            for rule in scan_line(line, rules, lits, markers=not binary)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail on install-specific values (ADR 0001 §15).")
    parser.add_argument("--root", default=".")
    parser.add_argument("--extra", type=Path, default=None)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    root = Path(args.root)
    if args.extra is not None and not args.extra.is_file():
        parser.error(f"--extra file not found: {args.extra}")
    literals = load_literals(args.extra or default_extra())
    try:
        entries = [(None, p) for p in args.paths] if args.paths else tracked(root)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    hits = 0
    for mode, rel in entries:
        rel = Path(rel).as_posix()
        if exempt(rel):
            continue
        for hit in scan_file(root, rel, mode, literals):
            print(hit)
            hits += 1
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
