"""The test tiers (docs/testing/efficiency-review.md): the change-based selector, the slow list and the
safety guard, the JUnit parsing behind the runner, and the RAM base temp. Offline; reads the repository's
own files, runs no git."""

import os
import subprocess
import sys
from pathlib import Path

import basetemp
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
    ["tests/fakes/fake_wn_agent.py"], ["tests/wsd_env.py"], ["tests/basetemp.py"], ["pyproject.toml"],
    ["uv.lock"],
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


# Safety tests that were once on the slow list because no name matched (Codex review r1).
NAMED_SAFETY = ["tests/test_admind_r13_replies.py::test_a_hex_value_in_a_reply_never_reaches_the_chat",
                "tests/test_admind_r13_operators.py::test_a_stranger_is_dropped",
                "tests/test_admind_r13_membership.py::"
                "test_a_revoked_operators_queued_message_is_not_acted_on_after_a_rearm"]


def test_safety_is_the_default_outside_tmux_and_wsd_files() -> None:
    for nodeid in [*NAMED_SAFETY, "tests/test_redaction.py::test_anything",
                   "tests/test_sandbox_runtime.py::test_x[a]",
                   "tests/test_admind_reactions.py::test_a_reaction_answers_a_question_or_merge_card",
                   "tests/test_brand_new_file.py::test_anything",          # a new file is safety until listed
                   "tests/test_tmux_watchdog.py::test_sigkill_of_pytest_kills_its_servers_and_panes",
                   "tests/test_wsd_park.py::test_anything",
                   "tests/test_wsd_defer.py::test_a_digest_mismatch_refuses"]:  # eligible file, safety name
        assert tier_marks.is_safety(nodeid), nodeid
    assert not tier_marks.is_safety("tests/test_wsd_defer.py::test_defer_and_regate_replay")
    assert not tier_marks.is_safety("tests/test_tmux.py::test_multiline_paste_is_one_bracketed_paste[x]")


# Safety tests in eligible files whose names SAFETY_WORDS misses: only the explicit mark keeps them in.
MARKED_SAFETY = ["tests/test_wsd_daemon.py::test_corrupt_journal_refuses_to_start",
                 "tests/test_wsd_pickup.py::test_one_coder_session_at_a_time",
                 "tests/test_wsd_defer.py::test_a_deferred_bead_with_no_row_is_journal_lost_and_never_launched"]


def test_the_slow_list_names_no_safety_test() -> None:
    slow = tier_marks.load()
    assert slow, "tests/slow_tests.txt is missing or empty"
    assert not [name for name in slow if tier_marks.is_safety(name)]
    marked = run_tiers.marked_safety()
    assert not tier_marks.is_safety(MARKED_SAFETY[0]) and set(MARKED_SAFETY) <= marked
    assert not (set(NAMED_SAFETY) | marked) & slow


def collected(*args: str) -> set[str]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                           "-p", "no:xdist", *args], cwd=ROOT, capture_output=True, text=True, check=False,
                          env={**os.environ, "PYTEST_ADDOPTS": ""})
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    return {line for line in proc.stdout.splitlines() if "::" in line}


def test_the_fast_tier_collects_every_safety_test() -> None:
    everything = collected("tests")
    fast = collected("-m", "not slow", "tests")
    safety = {nodeid for nodeid in everything if tier_marks.is_safety(nodeid)}
    assert len(safety) > 1000
    assert safety <= fast, sorted(safety - fast)[:20]
    marked = collected("-m", "safety", "tests")      # file, name and explicit marks alike
    assert safety <= marked and marked <= fast, sorted(marked - fast)[:20]
    for name in [*NAMED_SAFETY, *MARKED_SAFETY]:
        assert any(tier_marks.function_id(nodeid) == name for nodeid in marked), name
    assert everything - fast, "the fast tier leaves nothing out"


class Item:
    """The parts of a pytest.Item that tier_marks uses."""

    def __init__(self, nodeid: str, *marks: str) -> None:
        self.nodeid = nodeid
        self.marks = set(marks)

    def add_marker(self, mark: pytest.MarkDecorator) -> None:
        self.marks.add(mark.name)

    def get_closest_marker(self, name: str) -> object | None:
        return name if name in self.marks else None


def modify(items: list[Item]) -> None:
    tier_marks.pytest_collection_modifyitems(pytest.Config.__new__(pytest.Config), items)  # type: ignore[arg-type]


def test_listing_a_safety_test_as_slow_fails_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier_marks, "load", lambda: {NAMED_SAFETY[1]})
    with pytest.raises(pytest.UsageError, match="lists safety tests"):
        modify([])


def test_marking_a_safety_test_slow_by_hand_fails_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier_marks, "load", set)
    with pytest.raises(pytest.UsageError, match="marked slow"):
        modify([Item(NAMED_SAFETY[0] + "[x]", "slow")])
    ok, safe = Item("tests/test_wsd_defer.py::test_defer_and_regate_replay", "slow"), Item(NAMED_SAFETY[2])
    modify([ok, safe])
    assert ok.marks == {"slow"} and safe.marks == {"safety"}


def test_a_renamed_safety_test_keeps_its_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    """A safety test in an eligible file whose name has lost every SAFETY_WORDS hit is still safety by its
    explicit mark: the list cannot name it, even for a file test-changed keeps (Codex review r2)."""
    renamed = "tests/test_wsd_daemon.py::test_a_bad_journal_refuses_to_start"
    assert not tier_marks.is_safety(renamed)
    monkeypatch.setattr(tier_marks, "load", lambda: {renamed})
    for keep in ["", "tests/test_wsd_daemon.py"]:
        monkeypatch.setenv(tier_marks.KEEP_ENV, keep)
        with pytest.raises(pytest.UsageError, match="lists tests marked safety"):
            modify([Item(renamed + "[x]", "safety")])


def test_an_item_marked_both_safety_and_slow_fails_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier_marks, "load", set)
    with pytest.raises(pytest.UsageError, match="marked slow"):
        modify([Item("tests/test_wsd_defer.py::test_defer_and_regate_replay", "safety", "slow")])


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


AUTO = ["-n", "auto", "--dist", "worksteal"]


@pytest.mark.parametrize(("extra", "added"), [
    ([], AUTO), (["-x"], AUTO), (["--no-header"], AUTO),
    (["-n", "0"], []), (["-n4"], []), (["--numprocesses=2"], []), (["-p", "no:xdist"], []),
])
def test_runs_are_parallel_unless_the_caller_chooses(extra: list[str], added: list[str]) -> None:
    assert run_tiers.parallel(extra) == added


def mounts(tmp_path: Path, path: Path, fstype: str = "tmpfs") -> Path:
    table = tmp_path / "mounts"
    table.write_text(f"proc /proc proc rw 0 0\nshm {path} {fstype} rw,nosuid 0 0\n")
    return table


def test_the_ram_base_is_a_private_directory_on_a_roomy_writable_tmpfs(tmp_path: Path) -> None:
    ram = tmp_path / "shm"
    ram.mkdir()
    base = basetemp.ram_base(ram, mounts(tmp_path, ram), min_free=1)
    assert base is not None and base.parent == ram.resolve() and base.name.startswith("hz")


@pytest.mark.parametrize("case", ["absent", "not tmpfs", "no mounts table", "unwritable", "too small",
                                  "mkdtemp fails"])
def test_otherwise_the_base_temp_stays_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               case: str) -> None:
    ram = tmp_path / "shm"
    ram.mkdir()
    table = mounts(tmp_path, ram, "ext4" if case == "not tmpfs" else "tmpfs")
    min_free = 1
    if case == "absent":
        ram.rmdir()
    elif case == "no mounts table":
        table = tmp_path / "missing"
    elif case == "unwritable":
        monkeypatch.setattr(basetemp.os, "access", lambda path, mode: False)
    elif case == "too small":
        min_free = 1 << 62
    elif case == "mkdtemp fails":
        def refuse(**kw: object) -> str:
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(basetemp.tempfile, "mkdtemp", refuse)
    assert basetemp.ram_base(ram, table, min_free) is None


def test_the_files_a_change_reaches_keep_their_slow_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    listed = "tests/test_wsd_defer.py::test_defer_and_regate_replay"
    other = "tests/test_tmux.py::test_multiline_paste_is_one_bracketed_paste"
    monkeypatch.setattr(tier_marks, "load", lambda: {listed, other})
    monkeypatch.setenv(tier_marks.KEEP_ENV, os.pathsep.join(["tests/test_wsd_defer.py", "tests/test_x.py"]))
    kept, dropped = Item(listed + "[a]"), Item(other)
    modify([kept, dropped])
    assert kept.marks == set() and dropped.marks == {"slow"}
