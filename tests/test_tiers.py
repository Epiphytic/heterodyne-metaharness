"""The test tiers (docs/testing/efficiency-review.md): the change-based selector, the slow list and the
JUnit parsing behind the runner. Offline; reads the repository's own files, runs no git."""

import sys
from pathlib import Path

import pytest
import tier_marks

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import run_tiers  # noqa: E402
import select_tests  # noqa: E402


def full(changed: list[str]) -> str:
    with pytest.raises(select_tests.Full) as info:
        select_tests.select(changed)
    return str(info.value)


def test_docs_select_nothing() -> None:
    assert select_tests.select(["docs/wsd.md", "README.md", "LICENSE", "spikes/x/notes.txt"]) == []


def test_a_test_file_selects_itself_and_its_importers() -> None:
    picked = select_tests.select(["tests/test_admind_daemon.py"])
    assert "tests/test_admind_daemon.py" in picked
    assert "tests/test_admind_reactions.py" in picked       # imports Harness from it


def test_a_leaf_module_selects_the_tests_that_reach_it() -> None:
    picked = select_tests.select(["src/heterodyne/sandbox/openshell.py"])
    assert "tests/test_sandbox_openshell_backend.py" in picked
    assert "tests/test_redaction.py" not in picked


def test_a_transitive_import_counts() -> None:
    # test_admind_cli imports admind.cli, which reaches admind.redact only through other modules.
    assert "tests/test_admind_cli.py" in select_tests.select(["src/heterodyne/admind/redact.py"])


def test_a_module_named_in_a_string_counts() -> None:
    graph = select_tests.build_graph()
    daemon = ROOT / "tests" / "test_admind_daemon.py"
    assert ROOT / "tests" / "fakes" / "fake_claude.py" in graph[daemon]     # run by path, never imported


@pytest.mark.parametrize("changed", [
    ["tests/conftest.py"], ["tests/tmux_guard.py"], ["tests/tier_marks.py"], ["tests/slow_tests.txt"],
    ["tests/fakes/fake_wn_agent.py"], ["tests/wsd_env.py"], ["pyproject.toml"], ["uv.lock"],
    [".github/workflows/ci.yml"], ["src/heterodyne/tmux.py"], ["src/heterodyne/platform.py"],
    ["src/heterodyne/config/paths.py"], ["src/heterodyne/no_such_module.py"],
    ["docs/wsd.md", "tests/conftest.py"],
])
def test_unmappable_changes_run_everything(changed: list[str]) -> None:
    assert full(changed)


def test_a_shared_core_module_runs_everything() -> None:
    assert "shared core" in full(["src/heterodyne/config/paths.py"])


def test_a_script_selects_the_tests_that_name_it() -> None:
    picked = select_tests.select(["scripts/check_install_agnostic.py"])
    assert "tests/test_check_install_agnostic.py" in picked
    assert "tests/test_tiers.py" in select_tests.select(["scripts/select_tests.py"])


def test_safety_tests_are_recognised() -> None:
    for nodeid in ["tests/test_redaction.py::test_anything", "tests/test_sandbox_runtime.py::test_x[a]",
                   "tests/test_admind_approvals.py::test_approve_happy_path",
                   "tests/test_admind_daemon.py::test_member_count_latches_and_stops_all_posting",
                   "tests/test_wsd_park.py::test_a_digest_mismatch_refuses"]:
        assert tier_marks.is_safety(nodeid), nodeid
    assert not tier_marks.is_safety("tests/test_admind_reactions.py::test_a_reaction_answers_a_question")


def test_the_slow_list_names_no_safety_test() -> None:
    slow = tier_marks.load()
    assert slow, "tests/slow_tests.txt is missing or empty"
    assert not [name for name in slow if tier_marks.is_safety(name)]


def test_listing_a_safety_test_as_slow_fails_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier_marks, "load", lambda: {"tests/test_redaction.py::test_anything"})
    with pytest.raises(pytest.UsageError, match="safety tests"):
        tier_marks.pytest_collection_modifyitems(pytest.Config.__new__(pytest.Config), [])


def test_junit_cases_map_to_node_ids(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_tiers" name="test_docs_select_nothing" time="0.25"/>'
        '<testcase classname="tests.test_tiers" name="test_x[a]" time="1.5">'
        '<failure message="boom"/></testcase>'
        '</testsuite></testsuites>')
    assert run_tiers.cases(report) == [("tests/test_tiers.py::test_docs_select_nothing", 0.25, False),
                                       ("tests/test_tiers.py::test_x[a]", 1.5, True)]
    assert run_tiers.cases(tmp_path / "missing.xml") == []
