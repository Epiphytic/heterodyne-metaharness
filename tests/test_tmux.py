import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from tmux_guard import new_test_tmux

from heterodyne.tmux import (
    ESCAPE_GROWTH,
    MIN_STEP,
    PASTE_SETTLE,
    Tmux,
    TmuxError,
    TmuxPasteUncertain,
    escape_controls,
    has_controls,
)

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


@pytest.fixture
def tmux() -> Iterator[Tmux]:
    t = new_test_tmux()
    yield t
    t.kill_server()
    assert t.socket_path is not None
    t.socket_path.unlink(missing_ok=True)


def wait_for(pred, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_paste_is_byte_for_byte_and_submits(tmux: Tmux, tmp_path: Path) -> None:
    out = tmp_path / "out"
    tmux.new_session("s", tmp_path, [sys.executable, "-c",
                                     f"import sys; open({str(out)!r}, 'w').write(sys.stdin.readline())"])
    assert tmux.has_session("s") and not tmux.has_session("s-other")
    text = 'héllo 👍 "quotes" $HOME `x` \\ tab\there'
    tmux.paste("s", text)
    wait_for(lambda: out.exists() and out.read_text() != "")
    assert out.read_text() == text + "\n"


def test_dead_pane_is_kept_for_capture(tmux: Tmux, tmp_path: Path) -> None:
    tmux.new_session("s", tmp_path, [sys.executable, "-c", "print('bye')"])
    wait_for(lambda: tmux.pane_dead("s"))
    assert "bye" in tmux.capture("s", 20)
    tmux.kill("s")
    assert not tmux.has_session("s")


def test_immediate_exit_keeps_output_deterministically(tmux: Tmux, tmp_path: Path) -> None:
    for i in range(15):   # the race was timing-dependent; repeat so a regression cannot slip through
        name = f"s{i}"
        tmux.new_session(name, tmp_path, ["sh", "-c", "echo diag; exit 3"])
        wait_for(lambda n=name: tmux.has_session(n) and tmux.pane_dead(n))
        assert "diag" in tmux.capture(name, 20)


def test_multiline_paste_is_one_bracketed_paste(tmux: Tmux, tmp_path: Path) -> None:
    out = tmp_path / "raw"
    script = (
        "import sys, os, termios, tty\n"
        "tty.setraw(0)\n"
        "sys.stdout.write('\\x1b[?2004h'); sys.stdout.flush()\n"
        "data = b''\n"
        "while not data.endswith(b'\\r'):\n"
        "    data += os.read(0, 4096)\n"
        f"open({str(out)!r}, 'wb').write(data)\n"
    )
    tmux.new_session("s", tmp_path, [sys.executable, "-c", script])
    time.sleep(0.5)
    tmux.paste("s", "line one\nline two\nline three")
    wait_for(lambda: out.exists() and out.stat().st_size > 0)
    # tmux turns LF into CR inside the paste
    assert out.read_bytes() == b"\x1b[200~line one\rline two\rline three\x1b[201~\r"


def test_pane_info_reports_the_pane_id_and_pid(tmux: Tmux, tmp_path: Path) -> None:
    tmux.new_session("pi", tmp_path, ["sh", "-c", "sleep 30"])
    pane, pid = tmux.pane_info("pi")
    assert pane.startswith("%") and pid > 1
    tmux.kill("pi")
    with pytest.raises(TmuxError):
        tmux.pane_info("pi")


@pytest.mark.safety
def test_session_absent_is_only_tmuxs_own_word_for_it(tmux: Tmux, tmp_path: Path) -> None:
    assert tmux.socket_path is not None
    assert tmux.session_absent("s")                       # no server has started on the socket yet
    tmux.new_session("s", tmp_path, ["sleep", "60"])
    assert not tmux.session_absent("s") and tmux.session_absent("s-other")
    tmux.socket_path.chmod(0)
    try:
        assert not tmux.has_session("s")                  # has_session reads any failure as absence
        with pytest.raises(TmuxError, match="Permission denied"):
            tmux.session_absent("s")                       # a server it can't reach says nothing
    finally:
        tmux.socket_path.chmod(0o700)
    tmux.kill_server()
    assert tmux.session_absent("s")


def test_a_socket_no_server_listens_on_is_absence(tmux: Tmux) -> None:
    assert tmux.socket_path is not None
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(tmux.socket_path))                      # a crashed server's socket, before any start
    stale.close()
    assert tmux.session_absent("s")


@pytest.mark.safety
def test_session_names_never_reads_a_failure_as_no_sessions(tmux: Tmux, tmp_path: Path) -> None:
    assert tmux.socket_path is not None
    assert tmux.session_names() == set()                  # no server has started on the socket yet
    tmux.new_session("s", tmp_path, ["sleep", "60"])
    tmux.new_session("s-r1", tmp_path, ["sleep", "60"])
    assert tmux.session_names() == {"s", "s-r1"}
    tmux.socket_path.chmod(0)
    try:
        with pytest.raises(TmuxError, match="Permission denied"):
            tmux.session_names()
    finally:
        tmux.socket_path.chmod(0o700)
    tmux.kill_server()

    def listed_empty() -> bool:
        try:                                               # a server still exiting is a lost connection,
            return tmux.session_names() == set()           # never an empty listing
        except TmuxError as e:
            assert "server exited unexpectedly" in str(e) or "lost server" in str(e)
            return False
    wait_for(listed_empty)


@pytest.mark.safety
@pytest.mark.parametrize(("stderr", "absent"), [
    ("no server running on /tmp/tmux-1/x", True),
    ("error connecting to /tmp/tmux-1/x (No such file or directory)", True),
    ("lost server", False),
    ("server exited unexpectedly", False),
    ("error connecting to /tmp/tmux-1/x (Permission denied)", False),
    ("can't find session: s", False),
    ("", False),
])
def test_session_names_reads_only_a_missing_server_as_no_sessions(
        tmux: Tmux, monkeypatch: pytest.MonkeyPatch, stderr: str, absent: bool) -> None:
    def exec_(argv: list[str], *, input: bytes | None, timeout: float) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, b"", stderr.encode() + b"\n")
    monkeypatch.setattr(tmux, "_exec", exec_)
    if absent:
        assert tmux.session_names() == set()
    else:
        with pytest.raises(TmuxError, match="list-sessions failed"):
            tmux.session_names()


@pytest.mark.parametrize("text", [
    "\x1b[201~\rsecond\r", "a\rb", "\x1b[2J", "\x07", "\x9b31m", "\x7f", "a\x00b", "\x0b", "\x0c"])
def test_terminal_controls_are_found(text: str) -> None:
    assert has_controls(text)


@pytest.mark.parametrize("text", ["plain", "tab\there", "line\nbreak", "é ✓    "])
def test_tab_newline_and_printable_text_are_not_controls(text: str) -> None:
    assert not has_controls(text)


@pytest.mark.parametrize(("text", "shown"), [
    ("a\x1b[201~\rsecond\r", "a\\x1b[201~\nsecond\n"),
    ("one\r\ntwo\rthree\n", "one\ntwo\nthree\n"),
    ("bell\x07 del\x7f csi\x9b nul\x00", "bell\\x07 del\\x7f csi\\x9b nul\\x00"),
    ("lone \ud800 surrogate", "lone \\ud800 surrogate"),
    ("tab\tkept é", "tab\tkept é"),
])
def test_escaped_text_shows_its_controls_and_carries_none(text: str, shown: str) -> None:
    assert escape_controls(text) == shown
    assert not has_controls(escape_controls(text))
    escape_controls(text).encode("utf-8")                   # strict: always pasteable


def unescape(shown: str) -> str:
    """escape_controls undone (its CR to newline aside): a test that no two texts are shown the same."""
    out, i = [], 0
    while i < len(shown):
        if shown[i] != "\\":
            out.append(shown[i])
            i += 1
        elif shown[i + 1] == "\\":
            out.append("\\")
            i += 2
        elif shown[i + 1] == "x":
            out.append(chr(int(shown[i + 2:i + 4], 16)))
            i += 4
        else:
            assert shown[i + 1] == "u"
            out.append(chr(int(shown[i + 2:i + 6], 16)))
            i += 6
    return "".join(out)


COLLIDING = ["\x1b", "\\x1b", "\x9b", "\\x9b", "\ud800", "\\ud800", "\\", "\\\\", "\\\x1b", "\\\\x1b",
             "a\\x1b[201~\\nb", "\\u0041", "\x7f\\x7f\x00\\x00"]


def test_escaping_is_unambiguous() -> None:
    # T4 c2 B2: a real ESC and the text `\x1b` were shown the same; so were C1 and lone surrogates.
    shown = [escape_controls(t) for t in COLLIDING]
    assert len(set(shown)) == len(COLLIDING)
    assert [unescape(s) for s in shown] == COLLIDING
    assert escape_controls("\x1b") == "\\x1b" and escape_controls("\\x1b") == "\\\\x1b"


@pytest.mark.parametrize("code", [*range(0x20), *range(0x7f, 0xa0), 0x5c, 0xd800, 0xdfff, 0x41, 0xe9, 0x2713])
def test_escaping_grows_a_code_point_at_most_fourfold(code: int) -> None:
    text = chr(code)
    raw = len(text.encode("utf-8", "surrogatepass"))
    assert len(escape_controls(text).encode("utf-8")) <= ESCAPE_GROWTH * raw


class SlowTmux:
    """tmux commands that take `delay[command]` seconds, or time out (TimeoutExpired once their timeout
    passes) when that is longer than the timeout they were given. Notes (command, timeout, when run)."""

    def __init__(self, delay: dict[str, float]) -> None:
        self.delay = delay
        self.calls: list[tuple[str, float, float]] = []

    def __call__(self, argv: list[str], *, input: bytes | None,
                 timeout: float) -> subprocess.CompletedProcess[bytes]:
        command = next(a for a in argv if a in ("load-buffer", "paste-buffer", "send-keys", "delete-buffer"))
        self.calls.append((command, timeout, time.monotonic()))
        took = self.delay.get(command, 0.0)
        if took > timeout:
            time.sleep(timeout)
            raise subprocess.TimeoutExpired(argv, timeout)
        time.sleep(took)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def commands(self) -> list[str]:
        return [c for c, _, _ in self.calls]


def bounded_paste(tmux: Tmux, monkeypatch: pytest.MonkeyPatch, delay: dict[str, float],
                  seconds: float) -> tuple[SlowTmux, float, BaseException | None]:
    slow = SlowTmux(delay)
    monkeypatch.setattr(tmux, "_exec", slow)
    start, raised = time.monotonic(), None
    try:
        tmux.paste("s", "hello", seconds=seconds)
    except TmuxError as exc:
        raised = exc
    end = start + seconds
    for command, timeout, at in slow.calls:     # no command may run past the bound (cleanup: MIN_STEP)
        assert at + timeout <= end + (MIN_STEP if command == "delete-buffer" else 0) + 0.05, command
    return slow, time.monotonic() - start, raised


def test_a_bounded_paste_gives_each_command_only_what_is_left(tmux: Tmux,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    slow, took, raised = bounded_paste(tmux, monkeypatch, {}, 2.0)
    assert raised is None and slow.commands()[:3] == ["load-buffer", "paste-buffer", "send-keys"]
    assert took < 2.0


def test_a_bounded_paste_with_no_time_left_after_the_load_pastes_nothing(
        tmux: Tmux, monkeypatch: pytest.MonkeyPatch) -> None:
    slow, took, raised = bounded_paste(tmux, monkeypatch, {"load-buffer": 0.6}, 1.0)
    assert type(raised) is TmuxError                    # not uncertain: nothing reached the pane
    assert "paste-buffer" not in slow.commands() and took < 1.0 + MIN_STEP + 0.1
    assert 1.0 - 0.6 < PASTE_SETTLE + 2 * MIN_STEP      # the case is the one it claims


@pytest.mark.parametrize("hung", ["paste-buffer", "send-keys"])
def test_a_bounded_paste_that_hangs_ends_within_its_bound(tmux: Tmux, monkeypatch: pytest.MonkeyPatch,
                                                         hung: str) -> None:
    slow, took, raised = bounded_paste(tmux, monkeypatch, {hung: 60.0}, 1.5)
    assert isinstance(raised, TmuxPasteUncertain)
    assert took < 1.5 + MIN_STEP + 0.1
    if hung == "paste-buffer":
        assert "send-keys" not in slow.commands()
