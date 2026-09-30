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
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

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
EXEMPT_DIRS = ("examples/", "tests/fixtures/")
EXEMPT_FILES = ("LICENSE", "scripts/check_install_agnostic.py")


def tracked(root: Path) -> list[str]:
    cmd = ["git", "-C", str(root), "ls-files", "-z"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [p for p in out.stdout.split("\0") if p]


def default_extra() -> Path | None:
    base = os.environ.get("HETERODYNE_CONFIG_DIR")
    if base:
        config_dir = Path(base)
    else:
        config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "heterodyne"
    path = config_dir / "leakcheck.txt"
    return path if path.exists() else None


def load_literals(path: Path | None) -> list[str]:
    if path is None:
        return []
    lines = (s.strip() for s in path.read_text().splitlines())
    return [s for s in lines if s and not s.startswith("#")]


def exempt(rel: str) -> bool:
    return rel.startswith(EXEMPT_DIRS) or rel in EXEMPT_FILES


def scan_line(line: str, rules: dict[str, re.Pattern[str]], literals: list[str]) -> list[str]:
    allowed = {r for m in ALLOW.finditer(line) for r in m.group(1).split(",")} - {LOCAL}
    found = [name for name, rx in rules.items() if name not in allowed and rx.search(line)]
    return found + [LOCAL for lit in literals if lit in line]


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
    hits = 0
    for rel in args.paths or tracked(root):
        rel = Path(rel).as_posix()
        if exempt(rel):
            continue
        try:
            lines = (root / rel).read_text().splitlines()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        rules = {**RULES, **(SRC_RULES if rel.startswith("src/") else {})}
        for n, line in enumerate(lines, 1):
            for rule in scan_line(line, rules, literals):
                print(f"{rel}:{n}: {rule}: {line.strip()[:100]}")
                hits += 1
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
