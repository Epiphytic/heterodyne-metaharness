"""Offline tests must not leak tmux servers (spec 2026-10-08-test-tmux-leak-design.md, btq-q1r4p)."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

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
