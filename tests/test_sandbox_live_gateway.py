"""The live sandbox module's gateway binding (tests/live/test_live_sandbox.py), offline on parsed `ss`
text and a scratch /proc: only one identified listener, ours, on the endpoint's loopback address passes."""

import os
from pathlib import Path

import pytest
from sandbox_env import gateway_listener

PORT = 17670
USERS = 'users:(("openshell-gatew",pid=4242,fd=22))'
ROW = f"LISTEN 0 128 127.0.0.1:{PORT} 0.0.0.0:* {USERS} uid:1001 ino:5150 sk:1 <->"


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    """A /proc with process 4242: ours, holding socket 5150 on fd 22."""
    (tmp_path / "4242" / "fd").mkdir(parents=True)
    uid = os.getuid()
    (tmp_path / "4242" / "status").write_text(f"Name:\topenshell-gatew\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    (tmp_path / "4242" / "fd" / "1").symlink_to("/dev/null")
    (tmp_path / "4242" / "fd" / "22").symlink_to("socket:[5150]")
    return tmp_path


def test_the_one_loopback_listener_we_own_is_the_gateway(proc: Path) -> None:
    assert gateway_listener(ROW + "\n", PORT, proc) == 4242


@pytest.mark.parametrize("other", [f"0.0.0.0:{PORT}", f"[::]:{PORT}", f"127.0.0.1:{PORT}"])
def test_a_second_listener_on_the_port_fails(proc: Path, other: str) -> None:
    second = f"LISTEN 0 128 {other} 0.0.0.0:* uid:1002 ino:6160 sk:2 <->"
    with pytest.raises(ValueError, match="2 listeners"):
        gateway_listener(f"{second}\n{ROW}\n", PORT, proc)


def test_a_listener_on_another_address_fails(proc: Path) -> None:
    with pytest.raises(ValueError, match="not on 127.0.0.1"):
        gateway_listener(ROW.replace("127.0.0.1:", "0.0.0.0:"), PORT, proc)


@pytest.mark.parametrize("users", ["", 'users:(("a",pid=4242,fd=22),("b",pid=4343,fd=3))'])
def test_a_listener_without_exactly_one_pid_fails(proc: Path, users: str) -> None:
    with pytest.raises(ValueError, match="names [02] processes"):
        gateway_listener(ROW.replace(USERS, users), PORT, proc)


def test_another_users_listener_fails(proc: Path) -> None:
    other = os.getuid() + 1
    (proc / "4242" / "status").write_text(f"Uid:\t{other}\t{other}\t{other}\t{other}\n")
    with pytest.raises(ValueError, match="another user's"):
        gateway_listener(ROW, PORT, proc)


def test_a_pid_not_holding_the_socket_fails(proc: Path) -> None:
    with pytest.raises(ValueError, match="does not hold"):
        gateway_listener(ROW.replace("ino:5150", "ino:5151"), PORT, proc)


def test_a_listener_without_an_inode_fails(proc: Path) -> None:
    with pytest.raises(ValueError, match="no socket inode"):
        gateway_listener(ROW.replace(" ino:5150", ""), PORT, proc)
