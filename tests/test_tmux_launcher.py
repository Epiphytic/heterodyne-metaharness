"""The launcher prefix wraps only the server start; no real tmux, systemd or systemd-run is run."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fakes.settings import make_settings

from heterodyne import tmux as tmux_mod
from heterodyne.admind.agent import TMUX_SCOPE, tmux_launcher
from heterodyne.tmux import LAUNCHER_FAILED, Tmux, TmuxError


class Recorder:
    def __init__(self, returncode: int = 0, exc: BaseException | None = None) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.exc = exc

    def __call__(self, argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, b"", b"secret detail")


def test_systemd_launcher_is_a_user_scope(tmp_path: Path) -> None:
    prefix = tmux_launcher(make_settings(tmp_path, service_manager="systemd"))
    assert prefix[0].endswith("systemd-run")
    assert {"--user", "--scope", "--collect", f"--unit={TMUX_SCOPE}"} <= set(prefix)
    assert any(a.startswith("--description=") for a in prefix)


def test_launchd_has_no_launcher(tmp_path: Path) -> None:
    assert tmux_launcher(make_settings(tmp_path, service_manager="launchd")) == ()


def test_new_session_prefixes_only_the_server_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = Recorder()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    t = Tmux("hz-test-x", launcher=("systemd-run", "--user", "--scope"))
    t.new_session("s", tmp_path, ["true"])
    t.has_session("s")
    t.pane_dead("s")
    t.paste("s", "hi")
    t.send_key("s", "Escape")
    t.capture("s", 5)
    t.kill("s")
    t.kill_server()
    start, *others = rec.calls
    assert start[:5] == ["systemd-run", "--user", "--scope", "tmux", "-L"]
    assert "start-server" in start and "new-session" in start
    assert others and all(c[0] == "tmux" for c in others)


def test_empty_launcher_is_unprefixed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = Recorder()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    Tmux("hz-test-x").new_session("s", tmp_path, ["true"])
    assert rec.calls[0][:3] == ["tmux", "-L", "hz-test-x"]


@pytest.mark.parametrize("rec", [Recorder(returncode=1), Recorder(exc=FileNotFoundError("nope")),
                                 Recorder(exc=subprocess.TimeoutExpired("x", 1))])
def test_failing_launcher_raises_fixed_error_without_fallback(monkeypatch: pytest.MonkeyPatch,
                                                              tmp_path: Path, rec: Recorder) -> None:
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    with pytest.raises(TmuxError) as info:
        Tmux("hz-test-x", launcher=("systemd-run",)).new_session("s", tmp_path, ["true"])
    assert str(info.value) == LAUNCHER_FAILED
    assert len(rec.calls) == 1 and rec.calls[0][0] == "systemd-run"


def test_unit_keeps_control_group_kill_mode() -> None:
    from heterodyne.admind import unit
    assert "\nKillMode=control-group\n" in unit.render("/opt/venv/bin/python", {"PATH": "/usr/bin"})
