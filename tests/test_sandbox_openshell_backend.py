import json
import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_sandbox_openshell_policy import install_spec, spec

from heterodyne.sandbox.backend import BackendError, BackendUnavailable, Watch
from heterodyne.sandbox.openshell import (
    NONRECURSIVE_VERSIONS,
    OpenShellBackend,
    driver_config,
    policy,
    tool_env,
)
from heterodyne.sandbox.openshell_selftest import ptrace_scope_refusal


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


def backend(script: Script, scope: int = 1,
            versions: frozenset[str] = NONRECURSIVE_VERSIONS) -> OpenShellBackend:
    return OpenShellBackend("openshell", "podman", "img:1", {"PATH": "/usr/bin"}, runner=script,
                            clock=script.clock, sleep=script.sleep, ptrace_scope=lambda: scope,
                            nonrecursive_versions=versions)


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
    watch = Watch(3, Path("/s/hz0/reaped-r3.json"), "hz-alpha-p", "tmux", "wsd", None,
                  Path("/creates"), Path("/alerts"), Path("/creates/.ready-n"))
    record = Path("/s/hz0/session.json")
    argv = backend(Script()).reaper_argv("hz0123456789abg1", 1_800_007_200, record, watch)
    assert argv[:3] == ["env", "-i", "PATH=/usr/bin"]
    assert argv[4:7] == ["-I", "-m", "heterodyne.sandbox.reaper"]
    assert argv[7:] == ["--deadline", "1800007200", "--openshell", "openshell", "--podman", "podman",
                        "--image", "img:1", "--record", "/s/hz0/session.json", "--generation", "3",
                        "--marker", "/s/hz0/reaped-r3.json", "--pane", "hz-alpha-p", "--tmux", "tmux",
                        "--tmux-socket", "wsd", "--ledger", "/creates", "--alerts", "/alerts",
                        "--ready", "/creates/.ready-n", "hz0123456789abg1"]
    pinned = Watch(3, Path("/s/m"), "p", "tmux", "wsd", Path("/run/wsd.sock"),
                   Path("/creates"), Path("/alerts"), Path("/creates/.ready-n"))
    assert pinned.argv()[10:12] == ["--tmux-socket-path", "/run/wsd.sock"]


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


@pytest.mark.parametrize("scope", [-1, 0, 2, 3])
def test_create_refuses_a_host_ptrace_scope_other_than_1(tmp_path: Path, scope: int) -> None:
    """OpenShell can't start at 2 or 3; 0 leaves the probe unguarded. Nothing is written or submitted."""
    script = Script()
    with pytest.raises(BackendError) as failed:
        backend(script, scope).create(spec(), tmp_path / "scratch")
    assert str(failed.value) == ptrace_scope_refusal(scope)
    assert script.calls == [] and not (tmp_path / "scratch").exists()


LISTED = frozenset({"0.2.0"})
STATUS = ("openshell", "status", "-o", "json")


def status(version: str | None = "0.2.0", state: str = "connected") -> bytes:
    out = {"gateway": "openshell", "status": state,
           "authentication": {"state": "authenticated"}}
    return json.dumps(out if version is None else {**out, "version": version}).encode()


def test_the_shipped_version_set_is_empty() -> None:
    """Nothing is enabled before the design's §3.4 review; that review edits this test (btq-wazo1)."""
    assert frozenset() == NONRECURSIVE_VERSIONS


@pytest.mark.parametrize(("rc", "out", "supported"), [
    (0, status(), True),
    (0, status("0.1.2"), False),                 # today's pin: never listed
    (0, status("0.2.1"), False),                 # newer: not reviewed, so not supported
    (0, status("0.1.9"), False),
    (0, status("0.2.0-rc1"), False),             # exact, not a prefix
    (1, status(), False),
    (0, b"Server Status\n  Version: 0.2.0\n", False),
    (0, b"[]", False),
    (0, status(None), False),
    (0, json.dumps({"status": "connected", "version": ["0.2.0"]}).encode(), False),   # unhashable
    (0, status(state="connected_http"), False),
    (0, status(state="disconnected"), False),
])
def test_support_needs_an_exact_listed_gateway_version(rc: int, out: bytes, supported: bool) -> None:
    script = Script((STATUS, rc, out),)
    assert backend(script, versions=LISTED).nonrecursive_supported() is supported
    assert [c[0] for c in script.calls] == [list(STATUS)]


def test_a_status_query_that_cannot_answer_is_unsupported() -> None:
    def timeout(argv: Sequence[str], data: bytes | None, t: float) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(list(argv), t)

    def missing(argv: Sequence[str], data: bytes | None, t: float) -> subprocess.CompletedProcess[bytes]:
        raise OSError("no such file")

    for runner in (timeout, missing):
        b = OpenShellBackend("openshell", "podman", "img:1", {}, runner=runner, nonrecursive_versions=LISTED)
        assert b.nonrecursive_supported() is False


def test_an_empty_version_set_asks_nothing() -> None:
    script = Script()
    assert backend(script).nonrecursive_supported() is False
    assert script.calls == []


def created_config(script: Script) -> dict[str, object]:
    [create] = [c[0] for c in script.calls if c[0][:3] == ["openshell", "sandbox", "create"]]
    return json.loads(create[create.index("--driver-config-json") + 1])


def test_a_supported_gateway_creates_with_the_non_recursive_install_bind(tmp_path: Path) -> None:
    script = Script((STATUS, 0, status()), (("openshell", "sandbox", "create"), 0, b""),
                    (("openshell", "sandbox", "exec"), 0, b""))
    backend(script, versions=LISTED).create(install_spec(), tmp_path / "scratch")
    assert [c[0][:2] for c in script.calls][:2] == [["openshell", "status"], ["openshell", "sandbox"]]
    assert created_config(script) == driver_config(install_spec(), nonrecursive=True)


def test_an_unsupported_gateway_creates_option_a_s_object(tmp_path: Path) -> None:
    script = Script((STATUS, 0, status("0.1.2")), (("openshell", "sandbox", "create"), 0, b""),
                    (("openshell", "sandbox", "exec"), 0, b""))
    backend(script, versions=LISTED).create(install_spec(), tmp_path / "scratch")
    assert created_config(script) == driver_config(install_spec())


def test_a_gateway_that_refuses_recursive_fails_the_launch(tmp_path: Path) -> None:
    """Detection said yes, then create refused the field by name (a downgrade in between): no retry."""
    refusal = (b"Error: invalid driver config: unknown field `recursive`, expected one of `source`, "
               b"`target`, `read_only`, `selinux_label`")
    calls: list[list[str]] = []

    def runner(argv: Sequence[str], data: bytes | None, t: float) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(argv))
        if list(argv[:2]) == ["openshell", "status"]:
            return subprocess.CompletedProcess(list(argv), 0, status(), b"")
        return subprocess.CompletedProcess(list(argv), 1, b"", refusal)

    b = OpenShellBackend("openshell", "podman", "img:1", {}, runner=runner, ptrace_scope=lambda: 1,
                         nonrecursive_versions=LISTED)
    with pytest.raises(BackendError, match="`recursive`"):
        b.create(install_spec(), tmp_path / "scratch")
    assert [c[:3] for c in calls] == [["openshell", "status", "-o"], ["openshell", "sandbox", "create"]]


@pytest.mark.parametrize(("version", "mode"), [("0.2.0", "nonrecursive"), ("0.1.2", "recursive")])
def test_the_create_log_names_the_bind_mode(tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                            version: str, mode: str) -> None:
    script = Script((STATUS, 0, status(version)), (("openshell", "sandbox", "create"), 0, b""),
                    (("openshell", "sandbox", "exec"), 0, b""))
    with caplog.at_level(logging.INFO, logger="heterodyne.sandbox.openshell"):
        backend(script, versions=LISTED).create(install_spec(), tmp_path / "scratch")
    [line] = [r.getMessage() for r in caplog.records if "bind=" in r.getMessage()]
    assert line == f"openshell sandbox create hz0123456789abg1 bind={mode}"
