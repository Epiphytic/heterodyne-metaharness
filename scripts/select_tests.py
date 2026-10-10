#!/usr/bin/env python3
"""Pick the test files a change needs (docs/testing/efficiency-review.md, "Change-based selection").

Usage: select_tests.py [--base REF] [--explain]

Prints one line per selected test file, or the single word `FULL` when the change cannot be mapped
safely and the whole suite must run. With --explain, the reason for each decision goes to stderr.

The changed files are `git diff --name-only --no-renames <base>...HEAD` plus staged, unstaged and
untracked files, so a run before committing sees the working tree too. The default base is `origin/main`.

Every Python file under src/heterodyne and tests/ is a node of a dependency graph. A file depends on what
it imports (a package's `__init__` included), on a heterodyne module it names in a string
(`-m heterodyne.x`, `monkeypatch.setattr("heterodyne.x.y", ...)`), and on a test-side helper whose
multi-word file stem appears in a string (`fakes / "fake_claude.py"`, run as a subprocess). Docstrings
do not count. A test file reaches everything in its transitive closure.

- A changed module under src/heterodyne selects every test file that reaches it.
- A changed `tests/test_*.py` selects itself and every test file that imports it.
- Docs and prose (`docs/`, top-level `*.md`, `LICENSE`, `spikes/`, `examples/`) and the HZ_LIVE-only
  files under tests/live select nothing: the fast tier covers them.
- `scripts/X.py` selects the test files that mention `X`.

Anything else means FULL: what tests/conftest.py loads for every test (its plugins and their imports,
such as heterodyne.tmux), any other conftest, fakes, helpers and data under tests/, pyproject.toml, uv.lock,
CI config, non-Python files under src/, a deleted file, an unparsable module, a module no test reaches,
a script no test mentions, a module so shared that it reaches more than SHARED_FRACTION of the test
files, or a failing git command. The fallback is deliberately wide: a wrong FULL costs minutes, a wrong
subset can hide a regression until CI.
"""

import argparse
import ast
import functools
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TESTS = ROOT / "tests"
PACKAGE = "heterodyne"
SHARED_FRACTION = 0.5       # a module reaching more test files than this is "shared core": run everything
NO_TESTS = ("docs/", "spikes/", "examples/")
NO_TESTS_FILES = ("LICENSE",)
LIVE_OFFLINE = "tests/live/test_isolation_offline.py"   # the only tests/live file in the normal suite
NAME_IN_STRING = re.compile(rf"\b{PACKAGE}(?:\.\w+)+")


class Full(Exception):
    """The change cannot be mapped to a subset: run the whole suite."""


def git(*args: str) -> list[str]:
    """NUL-separated path output of a git command (`-z`, so no path is quoted or split)."""
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise Full(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return [path for path in proc.stdout.split("\0") if path]


def changed_files(base: str) -> list[str]:
    files = git("diff", "-z", "--name-only", "--no-renames", f"{base}...HEAD")
    files += git("diff", "-z", "--name-only", "--no-renames", "HEAD")
    files += git("ls-files", "-z", "--others", "--exclude-standard")
    return sorted(set(files))


def node_name(path: Path) -> str:
    """The name `path` is imported as: dotted under src/, relative to tests/ (which is on sys.path)
    for test-side modules, and bare for tests/live, which puts its own directory on sys.path."""
    if path.is_relative_to(SRC):
        root = SRC
    elif path.is_relative_to(TESTS / "live"):
        root = TESTS / "live"
    else:
        root = TESTS
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def references(path: Path, package: str) -> tuple[set[str], set[str]]:
    """(dotted names `path` imports, string constants in it). Relative imports resolve against `package`;
    `from a import b` yields both `a` and `a.b`, and the caller drops names that are not modules."""
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError, ValueError) as exc:
        raise Full(f"cannot parse {path.relative_to(ROOT)}: {exc}") from None
    names: set[str] = set()
    strings: set[str] = set()
    prose = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}    # docstrings
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose:
            strings.add(node.value)
    return names, strings


def with_parents(names: Iterable[str]) -> set[str]:
    """Importing `a.b.c` also runs `a/__init__` and `a/b/__init__`."""
    out: set[str] = set()
    for name in names:
        parts = name.split(".")
        out.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    return out


def python_files() -> list[Path]:
    return sorted([*(SRC / PACKAGE).rglob("*.py"), *TESTS.rglob("*.py")])


@functools.cache          # one process sees one tree
def build_graph() -> dict[Path, set[Path]]:
    """File -> files it depends on: what it imports, plus what it names in a string. A heterodyne module
    named in a string (`-m heterodyne.x`, `monkeypatch.setattr("heterodyne.x.y", ...)`) or a test-side
    module whose file stem appears in one (`fakes / "fake_claude.py"`, run as a subprocess) counts."""
    files = python_files()
    by_name = {node_name(path): path for path in files}
    # Helpers a test may run by path. Only multi-word stems: `settings` or `harness` in a string is prose.
    test_side = {path.stem: path for path in files
                 if not path.is_relative_to(SRC) and "_" in path.stem.strip("_")
                 and not path.stem.startswith("test_")}
    stem_re = re.compile(r"\b(" + "|".join(sorted(map(re.escape, test_side), key=len, reverse=True)) + r")\b")
    graph: dict[Path, set[Path]] = {}
    for path in files:
        name = node_name(path)
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        names, strings = references(path, package)
        for text in strings:
            names.update(NAME_IN_STRING.findall(text))
        deps = {by_name[n] for n in with_parents(names) if n in by_name}
        for text in strings:
            deps.update(test_side[stem] for stem in stem_re.findall(text))
        graph[path] = deps - {path}
    return graph


def closure(graph: dict[Path, set[Path]], start: Iterable[Path]) -> set[Path]:
    seen: set[Path] = set()
    todo = list(start)
    while todo:
        path = todo.pop()
        if path not in seen:
            seen.add(path)
            todo.extend(graph[path] - seen)
    return seen


def suite_files() -> list[Path]:
    return [*sorted(TESTS.glob("test_*.py")), ROOT / LIVE_OFFLINE]


def select(changed: list[str], explain: bool = False) -> list[str]:
    """Test files (relative to the repo root) for `changed`; raises Full when the whole suite must run."""
    def why(msg: str) -> None:
        if explain:
            print(msg, file=sys.stderr)

    graph = build_graph()
    tests = suite_files()
    # What tests/conftest.py loads (its plugins, the watchdog and their imports) runs under every test.
    every = closure(graph, [TESTS / "conftest.py", TESTS / "tmux_guard.py", TESTS / "tmux_watchdog.py",
                            TESTS / "tier_marks.py"])
    # tests/live/conftest.py is loaded only when tests/live is collected: it belongs to the offline file.
    reach = {test: closure(graph, [test]) for test in tests}
    reach[ROOT / LIVE_OFFLINE] |= closure(graph, [TESTS / "live" / "conftest.py"])
    picked: set[Path] = set()
    for rel in changed:
        path = ROOT / rel
        if rel.startswith(NO_TESTS) or rel in NO_TESTS_FILES or (rel.endswith(".md") and "/" not in rel):
            why(f"{rel}: docs, fast tier only")
            continue
        if rel.startswith("tests/live/test_") and rel != LIVE_OFFLINE:
            why(f"{rel}: live suite, runs only with HZ_LIVE=1")
            continue
        if not path.exists():
            raise Full(f"{rel}: deleted (what imported it is not in the graph any more)")
        is_test = path in reach
        if path in every:
            raise Full(f"{rel}: loaded by tests/conftest.py for every test")
        if path not in graph:
            if rel.startswith("scripts/") and rel.endswith(".py") and rel.count("/") == 1:
                hits = {test for test in tests
                        if path.stem in test.read_text(encoding="utf-8", errors="replace")}
                if not hits:
                    raise Full(f"{rel}: no test file mentions {path.stem}")
                picked |= hits
                why(f"{rel}: {len(hits)} test files mention {path.stem}")
                continue
            raise Full(f"{rel}: not mapped (config, data or CI)")
        if not is_test and not path.is_relative_to(SRC):
            raise Full(f"{rel}: shared test helper or fake")
        hits = {test for test, files in reach.items() if path in files}
        if not hits:
            raise Full(f"{rel}: no test file reaches it")
        if not is_test and len(hits) > SHARED_FRACTION * len(reach):
            raise Full(f"{rel}: shared core ({len(hits)}/{len(reach)} test files reach it)")
        picked |= hits
        why(f"{rel}: {len(hits)} test file(s)")
    return sorted(str(path.relative_to(ROOT)) for path in picked)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--base", default="origin/main", help="diff base (default: origin/main)")
    ap.add_argument("--explain", action="store_true", help="print the reason for each file to stderr")
    args = ap.parse_args(argv)
    try:
        selected = select(changed_files(args.base), explain=args.explain)
    except Full as exc:
        print(f"full suite: {exc}", file=sys.stderr)
        print("FULL")
        return 0
    print("\n".join(selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
