"""The launcher prefix wraps only the server start; no real tmux, systemd or systemd-run is run."""

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fakes.settings import make_settings
from fakes.tmux_marker import started

from heterodyne import tmux as tmux_mod
from heterodyne.admind.agent import TMUX_SCOPE, tmux_launcher
from heterodyne.tmux import LAUNCHER_FAILED, Tmux, TmuxError


class Recorder:
    """Fake subprocess.run. The list-sessions probe answers `probe` (returncode, stderr) or raises
    `probe_exc`; every other call answers `returncode` or raises `exc`, a successful start with its
    marker."""

    def __init__(self, returncode: int = 0, exc: BaseException | None = None,
                 probe: tuple[int, bytes] = (1, b"no server running on /tmp/x"),
                 probe_exc: BaseException | None = None) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.exc = exc
        self.probe = probe
        self.probe_exc = probe_exc

    def __call__(self, argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        if "list-sessions" in argv:
            if self.probe_exc:
                raise self.probe_exc
            return subprocess.CompletedProcess(argv, self.probe[0], b"", self.probe[1])
        if self.exc:
            raise self.exc
        out = started(argv) if self.returncode == 0 else b""
        return subprocess.CompletedProcess(argv, self.returncode, out, b"secret detail")

    @property
    def starts(self) -> list[list[str]]:
        return [c for c in self.calls if "new-session" in c]


def test_systemd_launcher_is_a_user_scope_with_a_unique_name(tmp_path: Path) -> None:
    launcher = tmux_launcher(make_settings(tmp_path, service_manager="systemd"))
    assert launcher is not None
    a, b = launcher(), launcher()
    assert a[0].endswith("systemd-run")
    assert {"--user", "--scope", "--collect"} <= set(a)
    assert any(x.startswith("--description=") for x in a)
    units = [next(x for x in p if x.startswith("--unit=")) for p in (a, b)]
    assert all(re.fullmatch(rf"--unit={TMUX_SCOPE}-[0-9a-f]{{8,}}", u) for u in units)
    assert units[0] != units[1]


def test_launchd_has_no_launcher(tmp_path: Path) -> None:
    assert tmux_launcher(make_settings(tmp_path, service_manager="launchd")) is None


def test_new_session_prefixes_only_the_server_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = Recorder()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    t = Tmux("hz-test-x", launcher=lambda: ("systemd-run", "--user", "--scope"))
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
        Tmux("hz-test-x", launcher=lambda: ("systemd-run",)).new_session("s", tmp_path, ["true"])
    assert str(info.value) == LAUNCHER_FAILED
    assert [c[0] for c in rec.starts] == ["systemd-run"]


def test_unit_keeps_control_group_kill_mode() -> None:
    from heterodyne.admind import unit
    assert "\nKillMode=control-group\n" in unit.render("/opt/venv/bin/python", {"PATH": "/usr/bin"})


def _start(rec: Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, n: int = 1) -> list[list[str]]:
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    seq = iter(f"u{i}" for i in range(10))
    t = Tmux("hz-test-x", launcher=lambda: ("systemd-run", f"--unit=s-{next(seq)}"))
    for _ in range(n):
        t.new_session("s", tmp_path, ["true"])
    return rec.starts


def test_start_always_uses_the_launcher_and_never_probes(monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    # Even with a live server (probe would say 0) the start is wrapped: no probe race, and the wrapped
    # client only asks the server; its scope holds just that short-lived client.
    rec = Recorder(probe=(0, b""))
    first, second = _start(rec, monkeypatch, tmp_path, n=2)
    assert first[:2] == ["systemd-run", "--unit=s-u0"] and second[:2] == ["systemd-run", "--unit=s-u1"]
    assert not any("list-sessions" in c for c in rec.calls)
