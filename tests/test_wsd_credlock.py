"""Plan 4b Task 2: the D8 lock, one per credential key. Keys are obvious fakes."""

import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest

from heterodyne.wsd import credlock
from heterodyne.wsd.credlock import BAD_KEY, LOCK_BUSY, LOCK_FAILED, CredentialBusy, credential_lock

KEY = "ck1-" + "a" * 32
OTHER = "ck1-" + "b" * 32
HOLDER = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX)
print("locked", flush=True)
sys.stdin.read()
"""


class Ticks:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def free(path: Path) -> bool:
    """Whether another open file description can take the lock now."""
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def test_the_lock_is_held_for_the_body_and_released_after(tmp_path: Path) -> None:
    locks = tmp_path / "credlocks"
    with credential_lock(locks, KEY, 0):
        assert not free(locks / f"{KEY}.lock")
    assert free(locks / f"{KEY}.lock")
    assert (locks.stat().st_mode & 0o777) == 0o700
    assert ((locks / f"{KEY}.lock").stat().st_mode & 0o777) == 0o600


def test_a_second_holder_waits_its_bound_then_is_busy(tmp_path: Path) -> None:
    ticks = Ticks()
    with credential_lock(tmp_path, KEY, 0), pytest.raises(CredentialBusy) as busy, \
            credential_lock(tmp_path, KEY, 1.0, clock=ticks.clock, sleep=ticks.sleep):
        pytest.fail("the lock was taken twice")
    assert str(busy.value) == LOCK_BUSY
    assert ticks.slept and sum(ticks.slept) == pytest.approx(1.0)
    assert max(ticks.slept) <= credlock.POLL_SECONDS
    with credential_lock(tmp_path, OTHER, 0):          # another key is another lock
        pass


def test_a_holder_in_another_process_excludes_this_one(tmp_path: Path) -> None:
    lock = tmp_path / f"{KEY}.lock"
    with credential_lock(tmp_path, KEY, 0):            # creates the file
        pass
    proc = subprocess.Popen([sys.executable, "-I", "-c", HOLDER, str(lock)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and proc.stdout.readline() == "locked\n"
        with pytest.raises(CredentialBusy) as busy, credential_lock(tmp_path, KEY, 0):
            pytest.fail("the lock was taken while another process held it")
        assert str(busy.value) == LOCK_BUSY
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0
    with credential_lock(tmp_path, KEY, 0):
        pass


def test_the_lock_is_released_when_the_body_raises(tmp_path: Path) -> None:
    with pytest.raises(ZeroDivisionError), credential_lock(tmp_path, KEY, 0):
        _ = 1 / 0
    assert free(tmp_path / f"{KEY}.lock")


@pytest.mark.parametrize("key", ["", "ck1-" + "A" * 32, "ck1-" + "a" * 31, "../" + KEY, KEY + "/x",
                                 "ck2-" + "a" * 32])
def test_a_key_that_is_not_a_key_is_refused_before_any_path(tmp_path: Path, key: str) -> None:
    with pytest.raises(CredentialBusy) as bad, credential_lock(tmp_path / "credlocks", key, 0):
        pytest.fail("a bad key was locked")
    assert str(bad.value) == BAD_KEY
    assert not (tmp_path / "credlocks").exists()


def test_a_symlinked_lock_file_is_refused_and_its_target_untouched(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    locks = tmp_path / "credlocks"
    locks.mkdir(mode=0o700)
    (locks / f"{KEY}.lock").symlink_to(victim)
    with pytest.raises(CredentialBusy) as failed, credential_lock(locks, KEY, 0):
        pytest.fail("a symlinked lock was taken")
    assert str(failed.value) == LOCK_FAILED
    assert victim.read_text() == "keep"


def test_a_symlinked_lock_dir_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "credlocks").symlink_to(real)
    with pytest.raises(CredentialBusy) as failed, credential_lock(tmp_path / "credlocks", KEY, 0):
        pytest.fail("a symlinked lock directory was used")
    assert str(failed.value) == LOCK_FAILED
    assert list(real.iterdir()) == []


def swap_for_link(locks: Path, elsewhere: Path) -> Path:
    """Move the checked lock directory aside and leave a link to `elsewhere` in its place."""
    moved = locks.with_name("credlocks.checked")
    locks.rename(moved)
    locks.symlink_to(elsewhere)
    return moved


def test_a_lock_dir_swapped_for_a_link_after_its_check_is_refused(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    locks = tmp_path / "credlocks"
    real_private_dir = credlock.private_dir

    def check_then_swap(path: Path) -> None:
        real_private_dir(path)
        swap_for_link(path, elsewhere)

    monkeypatch.setattr(credlock, "private_dir", check_then_swap)
    with pytest.raises(CredentialBusy) as failed, credential_lock(locks, KEY, 0):
        pytest.fail("the lock was taken through a swapped directory")
    assert str(failed.value) == LOCK_FAILED
    assert list(elsewhere.iterdir()) == []


def test_a_lock_dir_swapped_after_it_is_opened_keeps_the_lock_in_the_checked_dir(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    locks = tmp_path / "credlocks"
    real_open_lock_file = credlock.open_lock_file
    moved: list[Path] = []

    def swap_then_open(path: Path, *, dir_fd: int | None = None) -> int:
        moved.append(swap_for_link(locks, elsewhere))
        return real_open_lock_file(path, dir_fd=dir_fd)

    monkeypatch.setattr(credlock, "open_lock_file", swap_then_open)
    with credential_lock(locks, KEY, 0):
        assert not free(moved[0] / f"{KEY}.lock")
        assert list(elsewhere.iterdir()) == []


def test_a_second_holder_after_a_swap_is_refused_not_given_another_lock(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    locks = tmp_path / "credlocks"
    with credential_lock(locks, KEY, 0):
        moved = swap_for_link(locks, elsewhere)
        with pytest.raises(CredentialBusy) as failed, credential_lock(locks, KEY, 0):
            pytest.fail("two holders took the same key")
        assert str(failed.value) == LOCK_FAILED
        assert not free(moved / f"{KEY}.lock")
    assert list(elsewhere.iterdir()) == []


def test_the_texts_name_no_key_or_path() -> None:
    for text in (LOCK_BUSY, LOCK_FAILED, BAD_KEY):
        assert "ck1-" not in text and "/" not in text
