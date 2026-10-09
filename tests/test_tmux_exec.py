"""Pinning tests for the one `subprocess.run` seam in `Tmux` (spec Amendment 1, A2). They record every
call's argv and keyword arguments and every exception mapping, so extracting `_exec` provably changes
nothing. No tmux runs: `subprocess.run` is replaced by a recorder."""

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.tmux_marker import started

from heterodyne import tmux as tmux_mod
from heterodyne.tmux import LAUNCHER_FAILED, NO_SESSION, Tmux, TmuxError, TmuxPasteUncertain


class Run:
    """Fake subprocess.run: records (argv, kwargs); the subcommand named `fail_on` answers `result`
    (a return code) or raises it (an exception); everything else exits 0, a start with its marker."""

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
        out = started(argv) if code == 0 else b""
        return subprocess.CompletedProcess(argv, code, out or b"out", b" why \n")


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
    new = argv.index("new-session")
    assert argv[new:new + 4] == ["new-session", "-d", "-P", "-F"]
    assert re.fullmatch(r"hz-started [0-9a-f]{32} #\{session_id\} #\{session_name\}", argv[new + 4])
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


# --- a start is confirmed by its own marker line (btq-n27uo) ---

NONCE = "0123abcd"


class Start:
    """Fake subprocess.run: `new-session` exits 0 with `stdout` and `stderr`; everything else exits 0."""

    def __init__(self, stdout: str, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.stdout = stdout.encode()
        self.stderr = stderr.encode()

    def __call__(self, argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        if "new-session" in argv:
            return subprocess.CompletedProcess(argv, 0, self.stdout, self.stderr)
        return subprocess.CompletedProcess(argv, 0, b"", b"")


def start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rec: Start, name: str = "admin",
          launcher: bool = False) -> None:
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    monkeypatch.setattr(tmux_mod.uuid, "uuid4", lambda: SimpleNamespace(hex=NONCE))
    t = Tmux("hz-test-pin", launcher=(lambda: ("pre",)) if launcher else None)
    t.new_session(name, tmp_path, ["true"])


@pytest.mark.parametrize("launcher", [False, True], ids=["direct", "launcher"])
def test_the_literal_probe_line_confirms_the_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                   launcher: bool) -> None:
    rec = Start("hz-started 0123abcd $0 admin\n")              # F1, as tmux 3.4 printed it
    start(monkeypatch, tmp_path, rec, launcher=launcher)
    assert len(rec.calls) == 1


@pytest.mark.parametrize("stdout", ["noise\nhz-started 0123abcd $0 admin\nother\n",
                                    "hz-started 0123abcd $7 admin"], ids=["noise", "no-newline"])
def test_other_output_around_the_marker_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                   stdout: str) -> None:
    rec = Start(stdout)
    start(monkeypatch, tmp_path, rec)
    assert len(rec.calls) == 1


def test_a_name_with_spaces_stays_whole(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = Start("hz-started 0123abcd $0 my admin\n")
    start(monkeypatch, tmp_path, rec, name="my admin")
    assert len(rec.calls) == 1


def test_a_start_that_exits_0_without_a_session_is_an_error(monkeypatch: pytest.MonkeyPatch,
                                                           tmp_path: Path) -> None:
    rec = Start("", "error creating /tmp/x/nodir/sock (No such file or directory)\n")   # A1
    with pytest.raises(TmuxError) as info:
        start(monkeypatch, tmp_path, rec)
    assert type(info.value) is TmuxError
    assert str(info.value) == ("tmux new-session did not create 'admin': "
                               "error creating /tmp/x/nodir/sock (No such file or directory)")
    assert len(rec.calls) == 1


def test_the_launcher_path_reports_a_missing_session_without_detail(monkeypatch: pytest.MonkeyPatch,
                                                                    tmp_path: Path) -> None:
    rec = Start("", "secret detail")
    with pytest.raises(TmuxError) as info:
        start(monkeypatch, tmp_path, rec, launcher=True)
    assert type(info.value) is TmuxError and str(info.value) == NO_SESSION
    assert len(rec.calls) == 1


def test_no_detail_says_no_session_was_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(TmuxError, match=r"^tmux new-session did not create 'admin': no session reported$"):
        start(monkeypatch, tmp_path, Start(""))


@pytest.mark.parametrize("launcher", [False, True], ids=["direct", "launcher"])
def test_a_renamed_session_is_killed_by_its_reported_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                         launcher: bool) -> None:
    rec = Start("hz-started 0123abcd $3 a_b\n")                # F3: tmux renamed a:b
    with pytest.raises(TmuxError) as info:
        start(monkeypatch, tmp_path, rec, name="a:b", launcher=launcher)
    assert str(info.value) == (NO_SESSION if launcher else "tmux new-session did not create 'a:b': "
                                                             "renamed to 'a_b'")
    assert len(rec.calls) == 2 and rec.calls[1] == ["tmux", "-L", "hz-test-pin", "kill-session", "-t", "$3"]


@pytest.mark.parametrize("stdout", [
    "admin\nother\n",                                          # no marker at all
    "hz-started wrong $5 other\n",                              # another call's marker
    "hz-started 0123abcd $0 admin\nhz-started 0123abcd $1 admin\n",  # two markers
    "hz-started 0123abcd 5 admin\n",                            # an id that is not $N
    "hz-started 0123abcd $0\n",                                 # no name
    "hz-started 0123abcd $0 \n",                                # an empty name
    "hz-started 0123abcd \n",                                   # no id
    "hz-started 0123abcd $0 a_b\nhz-started 0123abcd $1 a_b\n",  # two renames: neither is certain
], ids=["no-marker", "wrong-nonce", "two", "bad-id", "no-name", "empty-name", "no-id", "two-renames"])
def test_an_unverified_line_is_never_a_cleanup_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                      stdout: str) -> None:
    rec = Start(stdout)
    with pytest.raises(TmuxError):
        start(monkeypatch, tmp_path, rec)
    assert len(rec.calls) == 1                                  # in particular, no kill-session


@pytest.mark.parametrize("launcher", [False, True], ids=["direct", "launcher"])
def test_a_missing_working_directory_runs_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                  launcher: bool) -> None:
    rec = Start("hz-started 0123abcd $0 admin\n")
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    t = Tmux("hz-test-pin", launcher=(lambda: ("pre",)) if launcher else None)
    with pytest.raises(TmuxError, match=r"^tmux session directory is missing: "):
        t.new_session("admin", tmp_path / "missing", ["true"])
    assert rec.calls == []


@pytest.mark.parametrize(("stdout", "dead"), [(b"0\n", False), (b"1\n", True), (b"", True), (b"x\n", True)])
def test_only_an_explicit_0_is_a_live_pane(monkeypatch: pytest.MonkeyPatch, stdout: bytes,
                                           dead: bool) -> None:
    monkeypatch.setattr(tmux_mod.subprocess, "run",
                        lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, stdout, b""))
    assert Tmux("hz-test-pin").pane_dead("s") is dead
