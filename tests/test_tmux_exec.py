"""Pinning tests for the one `subprocess.run` seam in `Tmux` (spec Amendment 1, A2). They record every
call's argv and keyword arguments and every exception mapping, so extracting `_exec` provably changes
nothing. No tmux runs: `subprocess.run` is replaced by a recorder."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from heterodyne import tmux as tmux_mod
from heterodyne.tmux import LAUNCHER_FAILED, Tmux, TmuxError, TmuxPasteUncertain


class Run:
    """Fake subprocess.run: records (argv, kwargs); the subcommand named `fail_on` answers `result`
    (a return code) or raises it (an exception); everything else exits 0."""

    def __init__(self, fail_on: str | None = None, result: int | BaseException = 0) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.fail_on = fail_on
        self.result = result

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((list(argv), kw))
        code = 0
        if self.fail_on is not None and self.fail_on in argv:
            if isinstance(self.result, BaseException):
                raise self.result
            code = self.result
        return subprocess.CompletedProcess(argv, code, b"out", b" why \n")


RUN_KW = {"capture_output": True, "timeout": 15, "check": False}


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Run:
    rec = Run()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    monkeypatch.setattr(tmux_mod.time, "sleep", lambda _s: None)
    return rec


def test_run_passes_argv_input_and_fixed_keywords(run: Run) -> None:
    t = Tmux("hz-test-pin", binary="tmuxx")
    t.capture("s", 7)
    t.paste("s", "hé")
    capture, load, *_ = run.calls
    assert capture == (["tmuxx", "-L", "hz-test-pin", "capture-pane", "-p", "-J", "-S", "-7", "-t", "=s:"],
                       {"input": None, **RUN_KW})
    assert load[0][3:5] == ["load-buffer", "-b"] and load[0][-1] == "-"
    assert load[1] == {"input": "hé".encode(), **RUN_KW}


def test_new_session_without_a_launcher_goes_through_run(run: Run, tmp_path: Path) -> None:
    Tmux("hz-test-pin").new_session("s", tmp_path, ["true"])
    [(argv, kw)] = run.calls
    assert argv[:4] == ["tmux", "-L", "hz-test-pin", "start-server"] and argv[-2:] == ["--", "true"]
    assert kw == {"input": None, **RUN_KW}


def test_the_launcher_branch_has_no_input_and_a_30_second_timeout(run: Run, tmp_path: Path) -> None:
    Tmux("hz-test-pin", launcher=lambda: ("pre", "fix")).new_session("s", tmp_path, ["true"])
    [(argv, kw)] = run.calls
    assert argv[:5] == ["pre", "fix", "tmux", "-L", "hz-test-pin"]
    assert {"input": None, **kw} == {"input": None, "capture_output": True, "timeout": 30, "check": False}


def test_a_failing_checked_run_raises_with_the_subcommand_and_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_mod.subprocess, "run", Run("send-keys", 1))
    with pytest.raises(TmuxError) as info:
        Tmux("hz-test-pin").send_key("s", "Enter")
    assert type(info.value) is TmuxError and str(info.value) == "tmux send-keys failed: why"


def test_an_unchecked_run_returns_the_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_mod.subprocess, "run", Run("has-session", 1))
    assert Tmux("hz-test-pin").has_session("s") is False


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired("tmux", 15), OSError("x")])
def test_run_lets_timeouts_and_os_errors_propagate(monkeypatch: pytest.MonkeyPatch,
                                                   exc: BaseException) -> None:
    monkeypatch.setattr(tmux_mod.subprocess, "run", Run("capture-pane", exc))
    with pytest.raises(type(exc)):
        Tmux("hz-test-pin").capture("s", 1)


@pytest.mark.parametrize("result", [1, OSError("x"), subprocess.TimeoutExpired("pre", 30)])
def test_the_launcher_branch_maps_every_failure_to_one_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                             result: int | BaseException) -> None:
    monkeypatch.setattr(tmux_mod.subprocess, "run", Run("new-session", result))
    with pytest.raises(TmuxError) as info:
        Tmux("hz-test-pin", launcher=lambda: ("pre",)).new_session("s", tmp_path, ["true"])
    assert type(info.value) is TmuxError and str(info.value) == LAUNCHER_FAILED


@pytest.mark.parametrize("result", [1, OSError("x"), subprocess.TimeoutExpired("tmux", 15)])
def test_a_load_buffer_failure_is_a_plain_error(monkeypatch: pytest.MonkeyPatch,
                                                result: int | BaseException) -> None:
    rec = Run("load-buffer", result)
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    with pytest.raises(TmuxError) as info:
        Tmux("hz-test-pin").paste("s", "hi")
    assert type(info.value) is TmuxError
    assert [argv[3] for argv, _ in rec.calls] == ["load-buffer", "delete-buffer"]


@pytest.mark.parametrize("step", ["paste-buffer", "send-keys"])
@pytest.mark.parametrize("result", [1, OSError("x"), subprocess.TimeoutExpired("tmux", 15)])
def test_a_failure_from_paste_buffer_on_is_uncertain(monkeypatch: pytest.MonkeyPatch, step: str,
                                                      result: int | BaseException) -> None:
    monkeypatch.setattr(tmux_mod.subprocess, "run", Run(step, result))
    monkeypatch.setattr(tmux_mod.time, "sleep", lambda _s: None)
    with pytest.raises(TmuxPasteUncertain):
        Tmux("hz-test-pin").paste("s", "hi")
