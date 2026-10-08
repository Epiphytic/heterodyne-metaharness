"""Offline tests must not leak tmux servers (spec 2026-10-08-test-tmux-leak-design.md, btq-q1r4p)."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from tmux_watchdog import Sweeper

from heterodyne import tmux as tmux_mod
from heterodyne.tmux import Tmux


class Calls:
    """Fake subprocess.run that records argv and always succeeds."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"", b"")


def _drive(t: Tmux, tmp_path: Path) -> None:
    t.new_session("s", tmp_path, ["true"])
    t.has_session("s")
    t.capture("s", 5)
    t.kill_server()


@pytest.mark.parametrize("launcher", [None, ("systemd-run", "--user")])
def test_socket_name_selects_with_dash_l(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                         launcher: tuple[str, ...] | None) -> None:
    rec = Calls()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    _drive(Tmux("hz-test-x", launcher=(lambda: launcher) if launcher else None), tmp_path)
    prefix = list(launcher or ())
    assert rec.calls[0][:len(prefix) + 3] == [*prefix, "tmux", "-L", "hz-test-x"]
    assert all(c[:3] == ["tmux", "-L", "hz-test-x"] for c in rec.calls[1:])


@pytest.mark.parametrize("launcher", [None, ("systemd-run", "--user")])
def test_socket_path_selects_with_dash_s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                         launcher: tuple[str, ...] | None) -> None:
    rec = Calls()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    path = tmp_path / "sock"
    t = Tmux("hz-test-x", launcher=(lambda: launcher) if launcher else None, socket_path=path)
    _drive(t, tmp_path)
    prefix = list(launcher or ())
    assert rec.calls[0][:len(prefix) + 3] == [*prefix, "tmux", "-S", str(path)]
    assert all(c[:3] == ["tmux", "-S", str(path)] for c in rec.calls[1:])
    assert not any("-L" in c for c in rec.calls)


# --- the sweep, with fake probes (test 9 and the closure loop) ---

class FakeSweeper(Sweeper):
    """kill-server always "succeeds"; connect, PID and lock answers are scripted; time is fake."""

    def __init__(self, dead: Path, *, pid: int | None = 4242, connect: str = "refused",
                 running: bool = False, lock_free: bool = True, deadline: float = 30.0) -> None:
        self.clock = 0.0
        super().__init__(dead, deadline=deadline, log=lambda _line: None)
        self.pid, self.state, self.running, self.lock_free = pid, connect, running, lock_free
        self.commands: list[tuple[str, ...]] = []

    def now(self) -> float:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def tmux(self, path: Path, *args: str) -> tuple[int, str]:
        self.commands.append(args)
        if args[0] == "display-message":
            return (0, f"{self.pid}\n") if self.pid is not None else (1, "")
        return (0, "")

    def connect(self, path: Path) -> str:
        return self.state if path.exists() else "missing"

    def pid_running(self, pid: int) -> bool:
        return self.running

    def try_lock(self, lock: Path) -> tuple[bool, int | None]:
        return self.lock_free, None


def _dead_with(tmp_path: Path, *names: str) -> Path:
    dead = tmp_path / "dead"
    dead.mkdir()
    for n in names:
        (dead / n).touch()
    return dead


@pytest.mark.parametrize(("connect", "running"), [("refused", True), ("accept", False)])
def test_a_kill_that_cannot_be_proven_is_survived(tmp_path: Path, connect: str, running: bool) -> None:
    dead = _dead_with(tmp_path, "aa")
    s = FakeSweeper(dead, connect=connect, running=running)
    closed = s.run()
    summary = s.summary(closed)
    assert not closed and (dead / "aa").exists()
    assert summary["survived"] == [{"path": str(dead / "aa"), "pid": 4242}]
    assert summary["killed"] == summary["stale"] == summary["unresolved"] == []
    assert ("kill-server",) in s.commands


def test_a_proven_kill_is_unlinked_and_closes(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa")
    s = FakeSweeper(dead)
    closed = s.run()
    assert closed and not dead.exists()
    assert s.summary(closed) == {"killed": [{"path": str(dead / "aa"), "pid": 4242}], "stale": [],
                                 "survived": [], "unresolved": [], "closed": True}


@pytest.mark.parametrize(("lock_free", "outcome"), [(True, "stale"), (False, "unresolved")])
def test_unknown_pid_and_refused_needs_the_startup_lock(tmp_path: Path, lock_free: bool,
                                                        outcome: str) -> None:
    dead = _dead_with(tmp_path, "aa", "aa.lock")
    s = FakeSweeper(dead, pid=None, lock_free=lock_free, deadline=2.0)
    closed = s.run()
    summary = s.summary(closed)
    assert summary[outcome] == [{"path": str(dead / "aa"), "pid": None}]
    assert closed == lock_free and (dead / "aa").exists() != lock_free
    assert ("kill-server",) not in s.commands


def test_a_held_lock_with_no_socket_is_unresolved_at_the_deadline(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa.lock")
    s = FakeSweeper(dead, lock_free=False, deadline=2.0)
    closed = s.run()
    assert not closed and s.summary(closed)["unresolved"] == [{"path": str(dead / "aa.lock"), "pid": None}]


def test_an_entry_bound_after_the_first_sweep_is_still_swept(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path)

    class LateBind(FakeSweeper):
        def sweep(self) -> None:
            super().sweep()
            if not (dead / "late").exists() and not self.summary(False)["killed"]:
                (dead / "late").touch()            # a bind that resolved the root before the rename

    s = LateBind(dead)
    closed = s.run()
    assert closed and not dead.exists()
    assert s.summary(closed)["killed"] == [{"path": str(dead / "late"), "pid": 4242}]


def test_a_later_pass_resolves_an_earlier_survivor(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa")

    class SlowDeath(FakeSweeper):
        def pid_running(self, pid: int) -> bool:
            return self.clock < 6.0                # outlives the first 5 s death wait only

    s = SlowDeath(dead)
    closed = s.run()
    summary = s.summary(closed)
    assert closed and summary["survived"] == [] and len(summary["killed"]) == 1
