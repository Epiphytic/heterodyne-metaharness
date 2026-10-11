import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_sandbox_openshell_policy import spec

from heterodyne.sandbox.backend import BackendError, BackendUnavailable
from heterodyne.sandbox.openshell import OpenShellBackend, policy, tool_env


class Script:
    """A runner that answers by argv prefix, in order, and records every call."""

    def __init__(self, *answers: tuple[tuple[str, ...], int, bytes]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[list[str], bytes | None, float]] = []
        self.now = 0.0

    def __call__(self, argv: Sequence[str], data: bytes | None,
                 timeout: float) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((list(argv), data, timeout))
        for n, (prefix, rc, out) in enumerate(self.answers):
            if tuple(argv[:len(prefix)]) == prefix:
                if len(self.answers) > 1:
                    del self.answers[n]
                return subprocess.CompletedProcess(list(argv), rc, out, b"")
        raise AssertionError(f"unexpected command {argv}")

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def backend(script: Script) -> OpenShellBackend:
    return OpenShellBackend("openshell", "podman", "img:1", {"PATH": "/usr/bin"}, runner=script,
                            clock=script.clock, sleep=script.sleep)


def test_create_writes_the_policy_privately_and_waits_until_exec_works(tmp_path: Path) -> None:
    script = Script((("openshell", "sandbox", "create"), 0, b""), (("openshell", "sandbox", "exec"), 1, b""),
                    (("openshell", "sandbox", "exec"), 0, b""))
    backend(script).create(spec(), tmp_path / "scratch")
    written = (tmp_path / "scratch" / "policy.yaml")
    assert written.stat().st_mode & 0o777 == 0o600 and (tmp_path / "scratch").stat().st_mode & 0o777 == 0o700
    assert json.loads(written.read_text()) == policy(spec())
    assert script.calls[0][0][:3] == ["openshell", "sandbox", "create"] and script.calls[0][2] == 300
    assert script.calls[-1][0][-2:] == ["--", "true"]


def test_create_failure_is_a_backend_error(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="create"):
        backend(Script((("openshell", "sandbox", "create"), 1, b""),)).create(spec(), tmp_path / "s")


def test_exec_passes_input_and_no_tty() -> None:
    script = Script((("openshell", "sandbox", "exec"), 3, b"out"),)
    r = backend(script).exec("hz0123456789abg1", Path("/w"), ["python3", "-"], input=b"{}", timeout=9)
    assert (r.returncode, r.stdout) == (3, b"out")
    argv, data, timeout = script.calls[0]
    assert argv == ["openshell", "sandbox", "exec", "-n", "hz0123456789abg1", "--no-tty", "--no-login-shell",
                    "--workdir", "/w", "--", "python3", "-"]
    assert (data, timeout) == (b"{}", 9)


def test_exec_timeout_is_a_backend_error() -> None:
    def runner(argv: Sequence[str], data: bytes | None, timeout: float) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(list(argv), timeout)

    b = OpenShellBackend("openshell", "podman", "img:1", {}, runner=runner)
    with pytest.raises(BackendError, match="timed out"):
        b.exec("hz0123456789abg1", Path("/w"), ["true"], timeout=1)


def test_names_keeps_only_our_names_and_fails_closed() -> None:
    listing = b"NAME STATUS\nhz0123456789abg1 Ready\nother-box Ready\nhzffffffffffffg22 Deleting\n"
    assert backend(Script((("openshell", "sandbox", "list"), 0, listing),)).names() == {
        "hz0123456789abg1", "hzffffffffffffg22"}
    with pytest.raises(BackendUnavailable):
        backend(Script((("openshell", "sandbox", "list"), 1, b""),)).names()

    def broken(argv: Sequence[str], data: bytes | None, timeout: float) -> subprocess.CompletedProcess[bytes]:
        raise OSError("no such file")

    with pytest.raises(BackendUnavailable):
        OpenShellBackend("openshell", "podman", "img:1", {}, runner=broken).names()


def test_delete_waits_until_unlisted() -> None:
    script = Script((("openshell", "sandbox", "delete"), 0, b""),
                    (("openshell", "sandbox", "list"), 0, b"hz0123456789abg1 Deleting\n"),
                    (("openshell", "sandbox", "list"), 0, b"\n"))
    assert backend(script).delete("hz0123456789abg1") is True


def test_delete_unconfirmed_after_the_deadline() -> None:
    script = Script((("openshell", "sandbox", "delete"), 0, b""),
                    (("openshell", "sandbox", "list"), 0, b"hz0123456789abg1 Deleting\n"))
    assert backend(script).delete("hz0123456789abg1") is False
    assert script.now >= 60


def test_logs_filters_by_stamp() -> None:
    out = b"[100.5] old\n[199.0] edge\n[250.0] new\nnot a stamp\n"
    assert backend(Script((("openshell", "logs"), 0, out),)).logs("hz0123456789abg1", 201.0) == [
        "[199.0] edge", "[250.0] new"]


def test_network_mode_and_pid_from_the_one_workload_container() -> None:
    script = Script((("podman", "ps"), 0, b"abc123\n"), (("podman", "inspect"), 0, b"none\n"))
    assert backend(script).network_mode("hz0123456789abg1") == "none"
    assert script.calls[0][0] == ["podman", "ps", "--filter", "name=^openshell-default--hz0123456789abg1-",
                                  "--format", "{{.ID}}"]
    two = Script((("podman", "ps"), 0, b"a\nb\n"),)
    assert backend(two).network_mode("hz0123456789abg1") == "containers=2"
    pid = Script((("podman", "ps"), 0, b"abc\n"), (("podman", "inspect"), 0, b"4242\n"))
    assert backend(pid).workload_pid("hz0123456789abg1") == 4242
    with pytest.raises(BackendError):
        backend(Script((("podman", "ps"), 0, b""),)).workload_pid("hz0123456789abg1")


def test_tty_argv_carries_its_own_environment() -> None:
    argv = backend(Script()).tty_argv("hz0123456789abg1", Path("/w"), ["claude", "--resume", "x"])
    assert argv == ["env", "-i", "PATH=/usr/bin", "openshell", "sandbox", "exec", "-n", "hz0123456789abg1",
                    "--tty", "--no-login-shell", "--workdir", "/w", "--", "claude", "--resume", "x"]


def test_the_reaper_runs_outside_wsd_with_its_own_environment() -> None:
    argv = backend(Script()).reaper_argv("hz0123456789abg1", 1_800_007_200, Path("/s/hz0/session.json"))
    assert argv[:3] == ["env", "-i", "PATH=/usr/bin"]
    assert argv[4:7] == ["-I", "-m", "heterodyne.sandbox.reaper"]
    assert argv[7:] == ["--deadline", "1800007200", "--openshell", "openshell", "--podman", "podman",
                        "--image", "img:1", "--record", "/s/hz0/session.json", "hz0123456789abg1"]


def test_kill_uses_podman_alone() -> None:
    script = Script((("podman", "ps"), 0, b"c1\n"), (("podman", "kill"), 0, b"c1\n"))
    backend(script).kill("hz0123456789abg1")
    assert [c[0] for c in script.calls][1] == ["podman", "kill", "c1"]
    assert not any(c[0][0] == "openshell" for c in script.calls)


def test_tool_env_takes_only_what_the_tools_need() -> None:
    base = {"HOME": "/h", "PATH": "/usr/bin", "XDG_RUNTIME_DIR": "/run/user/1", "SECRET_TOKEN": "x",
            "LANG": "C"}
    assert tool_env(base, {"PATH": "/opt/podman5/bin:/usr/bin", "CONTAINERS_CONF": "/opt/c.conf"}) == {
        "HOME": "/h", "PATH": "/opt/podman5/bin:/usr/bin", "XDG_RUNTIME_DIR": "/run/user/1", "LANG": "C",
        "CONTAINERS_CONF": "/opt/c.conf"}


def test_a_failed_log_query_is_an_error_even_with_matching_lines() -> None:
    """The self-test takes these lines as the supervisor's corroboration: output from a failed query isn't."""
    out = b"[250.0] new\n"
    with pytest.raises(BackendError, match="openshell logs failed"):
        backend(Script((("openshell", "logs"), 1, out),)).logs("hz0123456789abg1", 201.0)


def test_a_failed_container_query_is_an_error_not_an_empty_listing() -> None:
    for call in (OpenShellBackend.kill, OpenShellBackend.network_mode, OpenShellBackend.workload_pid):
        script = Script((("podman", "ps"), 125, b""),)
        with pytest.raises(BackendError, match="podman ps failed"):
            call(backend(script), "hz0123456789abg1")
        assert [c[0][:2] for c in script.calls] == [["podman", "ps"]]


def test_kill_with_no_container_kills_nothing() -> None:
    script = Script((("podman", "ps"), 0, b""),)
    backend(script).kill("hz0123456789abg1")
    assert [c[0][:2] for c in script.calls] == [["podman", "ps"]]
