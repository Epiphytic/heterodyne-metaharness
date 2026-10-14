"""Plan 4b Task 5: what the CLIs' usage payloads mean (S7 (3), (5)), and the in-sandbox Codex read."""

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from escapes import Escapes
from sandbox_env import install_fake_cli, wait_for

from heterodyne.agents.base import STATUS_COMMAND, Cli, UsageReport
from heterodyne.agents.claude_code import SETTINGS_FILE, STATUS_SOURCE, ClaudeCode
from heterodyne.agents.codex import HOST_SOURCE, IN_SESSION_SOURCE, USAGE_SCRIPT, Codex, parse_rate_limits
from heterodyne.wsd.usage import LABEL, Observation

RESOURCES = Path(__file__).resolve().parents[1] / "src" / "heterodyne" / "sandbox" / "resources"
USAGE = RESOURCES / "codex_usage.py"
NOW = 1_800_000_000
SNAPSHOT = {"limitId": "codex",
            "primary": {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": NOW + 3600},
            "secondary": {"usedPercent": 17, "windowDurationMins": 10080, "resetsAt": NOW + 86400},
            "planType": "pro", "rateLimitReachedType": None}


# --- Claude ---

def test_a_status_line_gives_both_windows() -> None:
    report = ClaudeCode().usage_from_event({
        "hook_event_name": "StatusLine", "session_id": "s",
        "rate_limits": {"five_hour": {"used_percentage": 42, "resets_at": NOW + 3600},
                        "seven_day": {"used_percentage": 17, "resets_at": NOW + 86400}}})
    assert report == UsageReport((Observation("claude:5h", "5h", 42, NOW + 3600, STATUS_SOURCE),
                                  Observation("claude:7d", "7d", 17, NOW + 86400, STATUS_SOURCE)))


@pytest.mark.parametrize("limits", [None, [], {}, {"five_hour": "full"}, {"other": {"used_percentage": 1}}])
def test_a_status_line_without_windows_reports_nothing(limits: object) -> None:
    assert ClaudeCode().usage_from_event({"hook_event_name": "StatusLine", "rate_limits": limits}) is None


@pytest.mark.parametrize(("resets", "kept"), [(NOW + 5, NOW + 5), (float(NOW + 5), NOW + 5), (True, None),
                                              ("soon", None), (None, None), (float("nan"), None)])
def test_a_reset_that_is_not_a_number_is_unknown(resets: object, kept: int | None) -> None:
    limits = {"five_hour": {"used_percentage": 1, "resets_at": resets}}
    report = ClaudeCode().usage_from_event({"hook_event_name": "StatusLine", "rate_limits": limits})
    assert report is not None and report.windows[0].resets_at == kept


def test_a_percentage_is_passed_on_for_ingestion_to_judge() -> None:
    report = ClaudeCode().usage_from_event({
        "hook_event_name": "StatusLine", "rate_limits": {"five_hour": {"used_percentage": "lots"}}})
    assert report is not None and report.windows[0].used_percent == "lots"


def test_only_a_rate_limit_stop_failure_is_a_limit() -> None:
    c = ClaudeCode()
    assert c.usage_from_event({"hook_event_name": "StopFailure", "error": "rate_limit",
                               "last_assistant_message": "resets 12:10pm"}) == UsageReport(limited=True)
    for error in ("overloaded", "authentication_failed", "billing_error", None, 7):
        assert c.usage_from_event({"hook_event_name": "StopFailure", "error": error}) is None
    assert c.usage_from_event({"hook_event_name": "Stop", "error": "rate_limit"}) is None


def test_claude_has_no_in_sandbox_read() -> None:
    cli = Cli(Path("/opt/claude/bin/claude"), Path("/opt/claude"), "2.1.0")
    assert ClaudeCode().usage_argv(cli) is None
    assert ClaudeCode().usage_from_read(b"{}") is None


def test_the_status_line_is_installed_only_for_usage() -> None:
    plain = json.loads(ClaudeCode().run_files()[SETTINGS_FILE])
    assert "statusLine" not in plain
    usage = json.loads(ClaudeCode().run_files(usage=True)[SETTINGS_FILE])
    assert usage["statusLine"] == {"type": "command", "command": STATUS_COMMAND}
    assert usage["hooks"] == plain["hooks"]
    assert json.loads(ClaudeCode().run_files(limits=True)[SETTINGS_FILE]) == plain    # StopFailure is a hook


# --- Codex ---

def test_a_snapshot_gives_one_observation_per_window() -> None:
    report = parse_rate_limits(SNAPSHOT, IN_SESSION_SOURCE)
    src = IN_SESSION_SOURCE
    assert report == UsageReport((Observation("codex:codex:300m", "5h", 42, NOW + 3600, src),
                                  Observation("codex:codex:10080m", "7d", 17, NOW + 86400, src)))
    assert report is not None
    assert all(LABEL.fullmatch(o.window_id) and LABEL.fullmatch(o.kind) for o in report.windows)


def test_a_reached_limit_hints_the_latest_full_windows_reset() -> None:
    snap = {**SNAPSHOT, "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": NOW + 3600},
            "secondary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": NOW + 86400}}
    report = parse_rate_limits(snap, HOST_SOURCE)
    assert report is not None and report.limited and report.reset_hint == NOW + 86400
    partly = parse_rate_limits({**snap, "secondary": SNAPSHOT["secondary"]}, HOST_SOURCE)
    assert partly is not None and (partly.limited, partly.reset_hint) == (True, NOW + 3600)
    nothing_full = parse_rate_limits({**SNAPSHOT, "rateLimitReachedType": "workspace_owner_credits_depleted"},
                                     HOST_SOURCE)
    assert nothing_full is not None and (nothing_full.limited, nothing_full.reset_hint) == (True, None)


@pytest.mark.parametrize("window", [None, "x", {"usedPercent": 1},
                                    {"usedPercent": 1, "windowDurationMins": 0},
                                    {"usedPercent": 1, "windowDurationMins": True},
                                    {"usedPercent": 1, "windowDurationMins": "300"}])
def test_a_window_without_a_duration_is_skipped(window: object) -> None:
    snap = {"limitId": "codex", "primary": window, "rateLimitReachedType": None}
    report = parse_rate_limits(snap, HOST_SOURCE)
    assert report == UsageReport()


@pytest.mark.parametrize("data", [None, [], "x", 1])
def test_a_snapshot_that_is_not_an_object_is_nothing(data: object) -> None:
    assert parse_rate_limits(data, HOST_SOURCE) is None


@pytest.mark.parametrize("output", [b"", b"not json", b"[" * 200 + b"]" * 200, b"{" + b" " * 70_000 + b"}"])
def test_read_output_that_cannot_be_trusted_to_parse_is_nothing(output: bytes) -> None:
    assert Codex().usage_from_read(output) is None


def test_codex_hooks_carry_no_usage() -> None:
    assert Codex().usage_from_event({"hook_event_name": "Stop", "rate_limits": {}}) is None


def test_the_usage_script_ships_for_usage_or_limits() -> None:
    assert USAGE_SCRIPT.name not in Codex().run_files()
    assert Codex().run_files(usage=True)[USAGE_SCRIPT.name] == USAGE.read_text()
    assert Codex().run_files(limits=True)[USAGE_SCRIPT.name] == USAGE.read_text()


# --- codex_usage.py, run against the fake app-server ---

def diagnostic(proc: subprocess.CompletedProcess[bytes]) -> str:
    """The script's one stderr line, if it is `codex_usage: <type>`; never its raw text otherwise."""
    found = re.fullmatch(rb"codex_usage: ([A-Za-z]+)\n", proc.stderr)
    return found.group(1).decode() if found else "<unrecognised>"


def gone(pid_file: Path) -> bool:
    """The fake app-server that wrote `pid_file` has ended and been reaped: no such process is left."""
    try:
        os.kill(int(pid_file.read_text()), 0)
    except ProcessLookupError:
        return True
    return False


def run_usage(tmp_path: Path, limit: str = "30", **env: str) -> subprocess.CompletedProcess[bytes]:
    codex = install_fake_cli(tmp_path, "codex")
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    scratch = tmp_path / "tmp"
    scratch.mkdir(exist_ok=True)
    full = {"PATH": os.environ["PATH"], "CODEX_HOME": str(home), "TMPDIR": str(scratch),
            "HZ_FAKE_PID_FILE": str(tmp_path / "app-server.pid"), **env}
    return subprocess.run([sys.executable, "-I", str(USAGE), str(codex), limit], env=full,
                          capture_output=True, timeout=60, check=False)


def test_the_read_prints_the_snapshot_and_leaves_nothing(tmp_path: Path) -> None:
    (tmp_path / "codex-home").mkdir()
    (tmp_path / "codex-home" / "auth.json").write_text("fake-not-a-token")
    proc = run_usage(tmp_path, HZ_FAKE_RATE_LIMITS=json.dumps(SNAPSHOT))
    assert (proc.returncode, diagnostic(proc) if proc.stderr else "") == (0, "")
    assert Codex().usage_from_read(proc.stdout) == parse_rate_limits(SNAPSHOT, IN_SESSION_SOURCE)
    assert gone(tmp_path / "app-server.pid")
    assert list((tmp_path / "tmp").iterdir()) == []
    assert (tmp_path / "codex-home" / "auth.json").read_text() == "fake-not-a-token"


@pytest.mark.parametrize(("fault", "reason"), [
    ("error", "AnswerError"), ("garbage", "JSONDecodeError"), ("hang", "TimeoutError"),
    ("close", "ClosedOutput"), ("exit", "ExitStatus"), ("no-login", "AnswerError"),
    ("no-limits", "NoRateLimits")])
def test_a_failed_read_exits_1_with_its_own_diagnostic(tmp_path: Path, fault: str, reason: str) -> None:
    (tmp_path / "codex-home").mkdir()
    if fault != "no-login":
        (tmp_path / "codex-home" / "auth.json").write_text("fake-not-a-token")
    env = {"HZ_FAKE_RATE_LIMITS": "null" if fault == "no-limits" else json.dumps(SNAPSHOT)}
    if fault in ("error", "garbage", "hang", "close", "exit"):
        env["HZ_FAKE_RATE_FAULT"] = fault
    proc = run_usage(tmp_path, limit="2", **env)      # a TimeoutExpired here fails the test
    assert (proc.returncode, proc.stdout, diagnostic(proc)) == (1, b"", reason)
    assert gone(tmp_path / "app-server.pid")
    assert list((tmp_path / "tmp").iterdir()) == []


def test_a_missing_codex_is_its_own_error_never_an_answer(tmp_path: Path) -> None:
    (tmp_path / "codex-home").mkdir()
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    proc = subprocess.run([sys.executable, "-I", str(USAGE), str(tmp_path / "nowhere" / "codex")],
                          env={"PATH": os.environ["PATH"], "CODEX_HOME": str(tmp_path / "codex-home"),
                               "TMPDIR": str(scratch)}, capture_output=True, timeout=60, check=False)
    assert (proc.returncode, proc.stdout, diagnostic(proc)) == (1, b"", "FileNotFoundError")
    assert list(scratch.iterdir()) == []


# --- codex_usage.read and main, in this process, with their seams forced ---

POPEN = subprocess.Popen        # the class itself, before the `started` fixture wraps it

def usage_module() -> Any:
    spec = importlib.util.spec_from_file_location("codex_usage_under_test", USAGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[str]]:
    """Every app-server the read starts, recorded so a test can prove each was reaped."""
    made: list[subprocess.Popen[str]] = []

    def recording(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        proc = POPEN(*args, **kwargs)
        made.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", recording)
    return made


def ended(made: list[subprocess.Popen[str]]) -> bool:
    """One app-server was started, and it was reaped and its pipes closed."""
    return len(made) == 1 and all(p.returncode is not None and p.stdin is not None and p.stdin.closed
                                  and p.stdout is not None and p.stdout.closed for p in made)


def read_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text("fake-not-a-token")
    return {"PATH": os.environ["PATH"], "CODEX_HOME": str(home), "HZ_FAKE_RATE_LIMITS": json.dumps(SNAPSHOT)}


def test_read_answers_and_reaps_the_app_server(tmp_path: Path, started: list[subprocess.Popen[str]]) -> None:
    codex = install_fake_cli(tmp_path, "codex")
    assert usage_module().read(str(codex), read_env(tmp_path), 5) == SNAPSHOT
    assert ended(started)


def test_a_timer_that_cannot_start_still_ends_the_app_server(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: list[subprocess.Popen[str]]) -> None:
    module = usage_module()
    codex = install_fake_cli(tmp_path, "codex")

    def refuse(_self: threading.Timer) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module.threading.Timer, "start", refuse)
    with pytest.raises(RuntimeError, match="^can't start new thread$"):
        module.read(str(codex), read_env(tmp_path), 5)
    assert ended(started)


class LateTimer:
    """A deadline that passes just as the answer arrives: it fires when it is cancelled."""

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.function = function

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        self.function()

    def join(self, timeout: float | None = None) -> None:
        pass


def test_an_answer_that_races_the_deadline_is_a_timeout(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: list[subprocess.Popen[str]]) -> None:
    module = usage_module()
    codex = install_fake_cli(tmp_path, "codex")
    monkeypatch.setattr(module.threading, "Timer", LateTimer)
    with pytest.raises(TimeoutError):
        module.read(str(codex), read_env(tmp_path), 5)
    assert ended(started)


def test_an_app_server_that_cannot_be_reaped_fails_the_read(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: list[subprocess.Popen[str]]) -> None:
    module = usage_module()
    codex = install_fake_cli(tmp_path, "codex")
    real_wait = POPEN.wait

    # quoted: the `started` fixture has replaced subprocess.Popen with a function by now
    def stuck(self: "subprocess.Popen[str]", timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired(self.args, timeout or 0)

    monkeypatch.setattr(POPEN, "wait", stuck)
    with pytest.raises(module.NotGone):
        module.read(str(codex), read_env(tmp_path), 5)
    monkeypatch.setattr(POPEN, "wait", real_wait)
    assert [real_wait(p, 10) for p in started] == [-signal.SIGKILL]      # it was killed; now reaped here
    assert ended(started)


def test_a_scratch_home_that_stays_is_no_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str],
                                                 started: list[subprocess.Popen[str]]) -> None:
    module = usage_module()
    codex = install_fake_cli(tmp_path, "codex")
    env = read_env(tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(module.tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(module.shutil, "rmtree", lambda *_a, **_k: None)
    with pytest.raises(module.ScratchKept):
        module.main([str(codex), "5"])
    assert capsys.readouterr().out == ""                   # nothing that reads as an answer
    assert ended(started)
    assert len(list(scratch.iterdir())) == 1               # the home it could not remove


@pytest.mark.parametrize("fault", ["orphan", "escaped"])
def test_a_descendant_holding_the_output_open_never_holds_the_read(tmp_path: Path, escapes: Escapes,
                                                                    fault: str) -> None:
    """The deadline bounds the read itself, not just the app-server's life: a descendant that keeps the
    output pipe open can't stretch it. One left in the app-server's group is killed with it; one that
    left the group is outside the read's reach, but still can't hold it."""
    (tmp_path / "codex-home").mkdir()
    (tmp_path / "codex-home" / "auth.json").write_text("fake-not-a-token")
    child = tmp_path / "child.pid"
    proc = run_usage(tmp_path, limit="1", HZ_FAKE_RATE_LIMITS=json.dumps(SNAPSHOT),
                     HZ_FAKE_RATE_FAULT=fault, HZ_FAKE_CHILD_PID_FILE=str(child), **escapes.env)
    assert (proc.returncode, proc.stdout, diagnostic(proc)) == (1, b"", "TimeoutError")
    assert gone(tmp_path / "app-server.pid")
    assert list((tmp_path / "tmp").iterdir()) == []
    if fault == "orphan":
        wait_for(lambda: gone(child))           # reaped by whoever inherited it, once killed
