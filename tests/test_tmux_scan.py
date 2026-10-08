"""The source scan that keeps every test-side tmux start behind the launch lock (spec
2026-10-08-test-tmux-leak-design.md, Amendment 1, A2; btq-q1r4p). Its cases are the specimens in
tests/data/tmux_scan_specimens.txt, so this module holds no flaggable source of its own."""

from pathlib import Path

import pytest
import tmux_guard

REPO = Path(__file__).resolve().parent.parent
SPECIMENS = REPO / "tests" / "data" / "tmux_scan_specimens.txt"
PLAIN = "tests/test_x.py"
WATCHDOG = "tests/tmux_watchdog.py"
GUARD = "tests/tmux_guard.py"
SELECTORS = "tests/test_tmux_watchdog.py"


def specimens() -> dict[str, tuple[str, str, str]]:
    """name -> (rule id, flag|pass, source)."""
    out: dict[str, tuple[str, str, str]] = {}
    head: list[str] | None = None
    body: list[str] = []
    for line in [*SPECIMENS.read_text().splitlines(), "# specimen: end pass end"]:
        if line.startswith("# specimen: "):
            if head is not None:
                out[head[2]] = (head[0], head[1], "\n".join(body) + "\n")
            head, body = line.removeprefix("# specimen: ").split(), []
        elif head is not None:
            body.append(line)
    return out


SPECIMEN = specimens()
FLAGGED = sorted(n for n, (_, expect, _) in SPECIMEN.items() if expect == "flag")


def rules(relpath: str, source: str) -> list[str]:
    return [f.rule for f in tmux_guard.scan(relpath, source)]


def test_every_rule_has_a_specimen_in_a_module_and_in_a_child_script() -> None:
    for rule in ("alias-socket-path", "kwargs", "raw-tmux-S", "private-ref"):
        named = [n for n in FLAGGED if SPECIMEN[n][0] == rule]
        assert any(n.startswith("child-") for n in named) and any(not n.startswith("child-") for n in named)


@pytest.mark.parametrize("name", FLAGGED)
def test_a_flag_specimen_gives_exactly_its_rule(name: str) -> None:
    rule, _, source = SPECIMEN[name]
    assert set(rules(PLAIN, source)) == {rule}


@pytest.mark.parametrize("name", FLAGGED)
def test_a_flag_specimen_under_tests_data_is_still_flagged(name: str) -> None:
    rule, _, source = SPECIMEN[name]
    assert set(rules("tests/data/untrusted.py", source)) == {rule}


@pytest.mark.parametrize("name", sorted(n for n, (_, e, _) in SPECIMEN.items() if e == "pass"))
def test_a_pass_specimen_passes(name: str) -> None:
    assert rules(PLAIN, SPECIMEN[name][2]) == []


def test_the_specimen_file_itself_is_skipped_by_name() -> None:
    text = SPECIMENS.read_text()
    assert rules("tests/data/tmux_scan_specimens.txt", text) == []
    assert rules("tests/data/other_specimens.txt", text)


def test_the_watchdogs_raw_tmux_passes_only_in_the_watchdog() -> None:
    source = SPECIMEN["watchdog-kill"][2]
    assert rules(WATCHDOG, source) == [] and rules(PLAIN, source) == ["raw-tmux-S"]
    assert rules("tests/sub/tmux_watchdog.py", source) == ["raw-tmux-S"]


def test_the_private_helpers_pass_only_in_the_guard() -> None:
    source = SPECIMEN["guard-helper"][2]
    assert rules(GUARD, source) == [] and set(rules(PLAIN, source)) == {"private-ref"}


def test_the_exemption_covers_exactly_the_listed_mocked_function() -> None:
    selector = SPECIMEN["mocked-selector"][2]
    assert rules(SELECTORS, selector) == []                    # listed, and it patches subprocess.run
    assert rules(PLAIN, selector) == ["alias-socket-path"]     # the same body in another module
    renamed = rules(SELECTORS, SPECIMEN["mocked-selector-renamed"][2])
    assert sorted(renamed) == ["alias-socket-path", "exempt-missing"]
    assert rules(SELECTORS, SPECIMEN["mocked-selector-unpatched"][2]) == ["exempt-unpatched"]


def test_the_test_tree_passes_the_scan() -> None:
    assert tmux_guard.scan_tree(REPO) == []
