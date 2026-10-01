import shutil
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from heterodyne.tmux import Tmux

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


@pytest.fixture
def tmux() -> Iterator[Tmux]:
    t = Tmux(f"hz-test-{uuid.uuid4().hex[:8]}")
    yield t
    t.kill_server()


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
