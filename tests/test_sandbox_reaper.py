"""g08sd: the reaper's evidence (`reaper.mark_live`), with its reads injected: it marks a generation it
found live at the deadline, once and before its first kill, and nothing else."""

import json
import subprocess
from pathlib import Path

import pytest

from heterodyne.sandbox import reaper
from heterodyne.sandbox.backend import BackendUnavailable
from heterodyne.tmux import TmuxError

BOX = "hz0123456789abg3"


def running(path: Path, **changes: object) -> Path:
    fields: dict[str, object] = {"sandbox": BOX, "generation": 3, "phase": "running", "started_at": 1000,
                                 "end_cause": "", **changes}
    path.write_text(json.dumps(fields))
    return path


def mark(tmp_path: Path, listed: set[str] | Exception | None = None, live: bool | Exception = True,
         generation: int = 3) -> bool:
    def names() -> set[str]:
        if isinstance(listed, Exception):
            raise listed
        return {BOX} if listed is None else listed

    def pane_live() -> bool:
        if isinstance(live, Exception):
            raise live
        return live

    return reaper.mark_live(tmp_path / "session.json", tmp_path / "reaped-r3.json", BOX, generation, names,
                            pane_live, clock=lambda: 2000.5)


def test_a_live_generation_is_marked(tmp_path: Path) -> None:
    running(tmp_path / "session.json")
    assert mark(tmp_path) is True
    marker = tmp_path / "reaped-r3.json"
    assert json.loads(marker.read_text()) == {"sandbox": BOX, "generation": 3, "at": 2000}
    assert marker.stat().st_mode & 0o077 == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reaped-r3.json", "session.json"]   # no temp left


@pytest.mark.parametrize("dead", ["sandbox", "pane"])
def test_a_generation_dead_before_the_deadline_is_not_marked(tmp_path: Path, dead: str) -> None:
    running(tmp_path / "session.json")
    assert mark(tmp_path, listed=set() if dead == "sandbox" else None, live=dead != "pane") is False
    assert not (tmp_path / "reaped-r3.json").exists()


@pytest.mark.parametrize("change", [{"phase": "stopping"}, {"phase": "ended"}, {"end_cause": "stopped"},
                                    {"generation": 4}, {"sandbox": "hz0123456789abg4"}, {"started_at": 0},
                                    {"started_at": None}])
def test_only_a_running_record_of_this_generation_is_marked(tmp_path: Path,
                                                            change: dict[str, object]) -> None:
    running(tmp_path / "session.json", **change)
    assert mark(tmp_path) is False
    assert not (tmp_path / "reaped-r3.json").exists()


@pytest.mark.parametrize("record", ["missing", "malformed", "a list"])
def test_an_unreadable_record_is_not_marked(tmp_path: Path, record: str) -> None:
    if record != "missing":
        (tmp_path / "session.json").write_text("{nope" if record == "malformed" else "[]")
    assert mark(tmp_path) is False


@pytest.mark.parametrize("fails", ["names", "tmux", "tmux timeout"])
def test_a_read_that_fails_is_not_marked(tmp_path: Path, fails: str) -> None:
    running(tmp_path / "session.json")
    if fails == "names":
        assert mark(tmp_path, listed=BackendUnavailable("down")) is False
    else:
        err = TmuxError("no server") if fails == "tmux" else subprocess.TimeoutExpired("tmux", 15)
        assert mark(tmp_path, live=err) is False
    assert not (tmp_path / "reaped-r3.json").exists()


def test_an_existing_marker_is_kept(tmp_path: Path) -> None:
    running(tmp_path / "session.json")
    (tmp_path / "reaped-r3.json").write_text("first")
    assert mark(tmp_path) is True
    assert (tmp_path / "reaped-r3.json").read_text() == "first"


def test_an_unwritable_directory_is_no_marker(tmp_path: Path) -> None:
    running(tmp_path / "session.json")
    tmp_path.chmod(0o500)
    try:
        assert mark(tmp_path) is False
    finally:
        tmp_path.chmod(0o700)
    assert not (tmp_path / "reaped-r3.json").exists()


@pytest.mark.parametrize("marked", [True, False])
def test_the_decision_is_made_once_before_the_first_kill(marked: bool) -> None:
    """Whatever the mark gives, the kill and delete go ahead, and later passes never mark again."""
    now = [0.0]
    events: list[str] = []
    deletes = iter([False, False, True])

    def sleep(seconds: float) -> None:
        now[0] += seconds

    def delete(_: str) -> bool:
        events.append("delete")
        gone = next(deletes, True)
        if gone:
            now[0] += reaper.LINGER_SECONDS
        return gone

    def decide() -> bool:
        events.append("mark")
        return marked

    reaper.reap(BOX, 100, delete, lambda _: events.append("kill"), clock=lambda: now[0], sleep=sleep,
                mark=decide)
    assert events[:2] == ["mark", "kill"] and events.count("mark") == 1
    assert events.count("kill") == events.count("delete") == 3
    assert now[0] >= 100
