import shutil
import socket
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from tmux_guard import new_test_tmux

from heterodyne.tmux import Tmux, TmuxError

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


def test_a_socket_no_server_listens_on_is_absence(tmp_path: Path) -> None:
    path = tmp_path / "stale"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    assert Tmux("unused", socket_path=path).session_absent("s")
