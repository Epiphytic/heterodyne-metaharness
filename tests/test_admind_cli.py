import asyncio
import dataclasses
import io
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent
from fakes.settings import make_settings

from heterodyne.admind import cli, unit
from heterodyne.admind.audit import Audit
from heterodyne.admind.store import Store
from heterodyne.admind.wnagent import WnAgent, WnAgentError
from heterodyne.marmot.control import ControlClient, ControlError
from heterodyne.marmot.nip19 import hex_to_npub

FAKE_WN = """#!{python}
import asyncio, sys
sys.path.insert(0, {tests!r})
from pathlib import Path
from fakes.fake_wn_agent import FakeWnAgent
args = sys.argv[1:]
if args and args[0] == "bootstrap":
    # Like the real CLI, bootstrap is a client of the running daemon's socket, not a second home opener.
    import json, socket
    token = Path(args[args.index("--auth-token-file") + 1]).read_text().strip()
    with socket.socket(socket.AF_UNIX) as conn:
        conn.connect(args[args.index("--socket") + 1])
        conn.sendall(json.dumps({{"marmot_agent_control": "marmot.agent-control.v2", "id": "b",
                                  "type": "fake_bootstrap", "auth_token": token}}).encode() + b"\\n")
        ok = b'"ack"' in conn.recv(65536)
    with Path({log!r}).open("a") as log:
        log.write("bootstrap " + " ".join(args[1:]) + "\\n")
    sys.exit(0 if ok else 1)
sock = Path(args[args.index("--socket") + 1])
token = Path(args[args.index("--auth-token-file") + 1]).read_text().strip()
async def main():
    fake = FakeWnAgent(sock, token, bootstrapped={bootstrapped})
    await fake.start()
    await asyncio.Event().wait()
asyncio.run(main())
"""


def fake_wn_agent(tmp_path: Path, bootstrapped: bool = True) -> tuple[str, Path]:
    log = tmp_path / "wn.log"
    script = tmp_path / "wn-agent"
    tests = str(Path(__file__).parent)
    script.write_text(FAKE_WN.format(python=sys.executable, tests=tests, log=str(log),
                                     bootstrapped=bootstrapped))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), log


def test_wn_agent_argv_token_and_private_home(tmp_path: Path) -> None:
    wn = WnAgent("wn-agent", tmp_path / "home", ("wss://a", "wss://b"), Audit(tmp_path / "audit.jsonl"))
    wn.prepare()
    assert stat.S_IMODE((tmp_path / "home").stat().st_mode) == 0o700
    assert stat.S_IMODE(wn.token_path.stat().st_mode) == 0o600
    token = wn.token()
    wn.prepare()
    assert wn.token() == token and len(token) == 64
    assert stat.S_IMODE(wn.socket_path.parent.stat().st_mode) == 0o700
    assert wn.argv() == ["wn-agent", "--home", str(tmp_path / "home"), "--socket", str(wn.socket_path),
                         "--auth-token-file", str(wn.token_path), "--relay", "wss://a", "--relay", "wss://b"]
    assert "--invite-policy" in wn.bootstrap_argv("heterodyne-admind")
    assert "deny" in wn.bootstrap_argv("heterodyne-admind")


def test_token_file_must_be_private_and_not_a_symlink(tmp_path: Path) -> None:
    wn = WnAgent("wn-agent", tmp_path / "home", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
    wn.prepare()
    wn.token_path.chmod(0o644)
    with pytest.raises(WnAgentError, match="0600"):
        wn.token()
    wn.token_path.unlink()
    (tmp_path / "elsewhere").write_text("x" * 64)
    (tmp_path / "elsewhere").chmod(0o600)
    wn.token_path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(WnAgentError, match="cannot open"):
        wn.token()


def test_start_supervise_and_stop_a_fake_wn_agent(tmp_path: Path) -> None:
    binary, _ = fake_wn_agent(tmp_path)

    async def body() -> None:
        wn = WnAgent(binary, tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        wn.prepare()
        client = ControlClient(wn.socket_path, wn.token(), timeout=2)
        await wn.start(client, wait=15)
        assert wn.alive() and await wn.account(client) == ACCOUNT
        await wn.stop()
        assert not wn.alive()
    asyncio.run(body())


def test_start_fails_when_the_binary_exits(tmp_path: Path) -> None:
    async def body() -> None:
        wn = WnAgent("false", tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        wn.prepare()
        with pytest.raises(WnAgentError, match="exited"):
            await wn.start(ControlClient(wn.socket_path, wn.token(), timeout=1), wait=5)
    asyncio.run(body())


def test_account_requires_exactly_one_local_signing_account(tmp_path: Path) -> None:
    async def body() -> None:
        fake = FakeWnAgent(tmp_path / "s.sock", None)
        fake.accounts = []
        await fake.start()
        wn = WnAgent("wn-agent", tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        with pytest.raises(WnAgentError, match="0 local-signing"):
            await wn.account(ControlClient(tmp_path / "s.sock", None))
        await fake.stop()
    asyncio.run(body())


def test_init_creates_identity_and_group_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    binary, log = fake_wn_agent(tmp_path, bootstrapped=False)   # a fresh home: init must bootstrap
    s = make_settings(tmp_path, wn_agent=binary)
    store = Store(s.state_dir / "admind.db")
    assert asyncio.run(cli.init(s, store, Audit(s.state_dir / "audit.jsonl"))) == 0
    assert store.get("group_id_hex") == "b2" * 32 and store.get("account_id_hex") == ACCOUNT
    out = capsys.readouterr().out
    assert "Accept the invite" in out and s.operator_npub not in out
    assert "bootstrap" in log.read_text()
    assert asyncio.run(cli.init(s, store, Audit(s.state_dir / "audit.jsonl"))) == 1


def test_rearm_clears_the_latch(tmp_path: Path) -> None:
    s = make_settings(tmp_path)
    store = Store(s.state_dir / "admind.db")
    store.set("latched", "group has 3 members, not 2")
    assert cli.rearm(s, store, Audit(s.state_dir / "audit.jsonl")) == 0
    assert store.get("latched") is None
    assert "rearm" in (s.state_dir / "audit.jsonl").read_text()


def test_unit_rendering_escapes_and_refuses_bad_paths() -> None:
    text = unit.render("/opt/venv/bin/python", {"PATH": "/usr/bin:/opt/50%/bin",
                                                "HETERODYNE_CONFIG_DIR": "/etc/hz", "OTHER": "x"})
    assert "ExecStart=/opt/venv/bin/python -m heterodyne.admind run" in text
    assert "Environment=PATH=/usr/bin:/opt/50%%/bin" in text
    assert "Environment=HETERODYNE_CONFIG_DIR=/etc/hz" in text and "OTHER" not in text
    assert "RestartPreventExitStatus=78" in text and "UMask=0077" in text
    for bad in ("/opt/my venv/python", '/opt/"q"/python'):
        with pytest.raises(ValueError):
            unit.render(bad, {"PATH": "/usr/bin"})


def test_hook_subcommand_exits_zero_even_when_admind_is_down(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"hook_event_name":"Stop"}')))
    assert cli.main(["hook", "--socket", str(tmp_path / "absent.sock")]) == 0


def test_run_without_init_is_a_config_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("HETERODYNE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("HETERODYNE_STATE_DIR", str(tmp_path / "state"))
    assert cli.main(["run"]) == cli.EX_CONFIG      # no [admind] profile configured
    assert os.environ["HETERODYNE_CONFIG_DIR"] == str(cfg)


def test_token_fifo_is_rejected_promptly(tmp_path: Path) -> None:
    wn = WnAgent("wn-agent", tmp_path / "home", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
    wn.prepare()
    wn.token_path.unlink()
    os.mkfifo(wn.token_path, 0o600)
    outcome: list[BaseException | None] = []

    def attempt() -> None:
        try:
            wn.token()
            outcome.append(None)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            outcome.append(exc)
    thread = threading.Thread(target=attempt, daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():   # regression: release the blocked open so the thread can end
        fd = os.open(wn.token_path, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
        thread.join(2)
        pytest.fail("token() blocked on a FIFO")
    assert len(outcome) == 1 and isinstance(outcome[0], WnAgentError)


@pytest.mark.parametrize(("mode", "ok"), [(0o600, True), (0o400, False), (0o700, False), (0o640, False)])
def test_token_mode_must_be_exactly_0600(tmp_path: Path, mode: int, ok: bool) -> None:
    wn = WnAgent("wn-agent", tmp_path / "home", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
    wn.prepare()
    wn.token_path.chmod(mode)
    if ok:
        assert len(wn.token()) == 64
    else:
        with pytest.raises(WnAgentError, match="0600"):
            wn.token()


@pytest.mark.parametrize("bad", ["\r", "\x0b", "\x0c", "\x00", "\x1b", "\x01", "\x7f", "\x85", "\x9f"])
def test_unit_rejects_control_characters(bad: str) -> None:
    with pytest.raises(ValueError, match="PATH"):
        unit.render("/opt/venv/bin/python", {"PATH": f"/usr/bin{bad}/x"})
    with pytest.raises(ValueError, match="python"):
        unit.render(f"/opt/py{bad}", {"PATH": "/usr/bin"})


@pytest.mark.parametrize("var", ["HETERODYNE_CONFIG_DIR", "HETERODYNE_STATE_DIR", "PATH"])
@pytest.mark.parametrize("make", [lambda: hex_to_npub("ab" * 32), lambda: "cd" * 32])
def test_unit_refuses_identifier_values_naming_only_the_variable(var: str, make: object) -> None:
    ident = make()  # type: ignore[operator]
    with pytest.raises(ValueError) as info:
        unit.render("/opt/venv/bin/python", {var: f"/srv/{ident}/x"})
    assert var in str(info.value) and ident not in str(info.value)


@pytest.mark.parametrize("var", ["HETERODYNE_CONFIG_DIR", "HETERODYNE_STATE_DIR"])
def test_unit_subcommand_exits_config_without_printing_the_identifier(
        var: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    npub = hex_to_npub("ab" * 32)
    monkeypatch.setenv(var, str(tmp_path / npub))
    assert cli.main(["unit"]) == cli.EX_CONFIG
    seen = capsys.readouterr()
    assert npub not in seen.out + seen.err and "ab" * 32 not in seen.out + seen.err
    assert var in seen.err and seen.out == ""


@pytest.mark.parametrize("make", [lambda: hex_to_npub("ab" * 32), lambda: "cd" * 32])
def test_init_repeat_does_not_print_an_identifier_in_the_state_dir(
        make: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ident = make()  # type: ignore[operator]
    s = make_settings(tmp_path, state_dir=tmp_path / str(ident) / "admind")
    store = Store(s.state_dir / "admind.db")
    store.set("group_id_hex", "b2" * 32)
    assert asyncio.run(cli.init(s, store, Audit(s.state_dir / "audit.jsonl"))) == 1
    seen = capsys.readouterr()
    assert "already initialised" in seen.out and str(ident) not in seen.out + seen.err


# supervise() against the fake wn-agent. Sleeps are patched to a bare yield (recording the
# supervisor's backoff delays, i.e. those >= 1s) so the tests take as long as the fake's startup.

class Sleeps:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.delays: list[float] = []
        real = asyncio.sleep

        async def fast(delay: float, *a: object) -> None:
            if delay >= 1:
                self.delays.append(delay)
            await real(0.01)
        monkeypatch.setattr(asyncio, "sleep", fast)


async def until(predicate: object, timeout: float = 20.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not predicate():  # type: ignore[operator]
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.05)


def supervised(tmp_path: Path, binary: str, **kw: float) -> tuple[WnAgent, ControlClient]:
    wn = WnAgent(binary, tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"), **kw)
    wn.prepare()
    return wn, ControlClient(wn.socket_path, wn.token(), timeout=2)


def test_supervise_restarts_a_child_that_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary, _ = fake_wn_agent(tmp_path)
    sleeps = Sleeps(monkeypatch)

    async def body() -> None:
        wn, client = supervised(tmp_path, binary)
        await wn.start(client, wait=15)
        first = wn.proc
        assert first is not None
        task = asyncio.create_task(wn.supervise(client))
        first.kill()
        await until(lambda: wn.proc is not first and wn.alive())
        assert wn.proc is not None and wn.proc.pid != first.pid
        ready: list[str] = []

        async def answered() -> None:
            while not ready:
                try:
                    ready.append(await wn.account(client))
                except (WnAgentError, ControlError):
                    await asyncio.sleep(0.05)
        await asyncio.wait_for(answered(), 20)
        assert ready == [ACCOUNT] and wn.alive()
        task.cancel()
        await wn.stop()
        assert not wn.alive()
    asyncio.run(body())
    assert sleeps.delays[0] == 1.0
    log = (tmp_path / "audit.jsonl").read_text()
    assert '"exited"' in log and log.count('"start"') == 2


def test_supervise_backs_off_when_restarts_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary, _ = fake_wn_agent(tmp_path)
    sleeps = Sleeps(monkeypatch)

    async def body() -> None:
        wn, client = supervised(tmp_path, binary)
        await wn.start(client, wait=15)
        first = wn.proc
        assert first is not None
        wn.binary = str(tmp_path / "missing-binary")      # every restart now fails to spawn
        task = asyncio.create_task(wn.supervise(client))
        first.kill()
        await until(lambda: len(sleeps.delays) >= 4)
        task.cancel()
        assert wn.proc is first and not wn.alive()
    asyncio.run(body())
    assert sleeps.delays[:4] == [1.0, 2.0, 4.0, 8.0]
    assert (tmp_path / "audit.jsonl").read_text().count("restart-failed") >= 3


class _Exits:
    """A stand-in child whose life ends immediately."""
    pid = 1
    returncode: int | None = None

    async def wait(self) -> int:
        return 1


def _flapping(tmp_path: Path, **kw: float) -> tuple[WnAgent, ControlClient]:
    wn, client = supervised(tmp_path, "unused", **kw)

    async def start(_client: ControlClient, wait: float = 30.0) -> None:    # ready, then dies again
        wn.proc = _Exits()  # type: ignore[assignment]
    wn.start = start  # type: ignore[method-assign]
    wn.proc = _Exits()  # type: ignore[assignment]
    return wn, client


def test_supervise_does_not_reset_backoff_on_mere_readiness(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = Sleeps(monkeypatch)

    async def body() -> None:
        wn, client = _flapping(tmp_path)
        task = asyncio.create_task(wn.supervise(client))
        await until(lambda: len(sleeps.delays) >= 5)
        task.cancel()
    asyncio.run(body())
    assert sleeps.delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_supervise_resets_backoff_only_after_a_child_outlives_the_threshold(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = Sleeps(monkeypatch)
    now = [0.0]
    lifetimes = iter([1.0, 1.0, 1.0, 61.0, 1.0, 1.0])  # seconds each child lives, on the fake clock

    class Lives(_Exits):
        async def wait(self) -> int:
            now[0] += next(lifetimes, 1.0)
            return 1

    async def body() -> None:
        wn, client = _flapping(tmp_path, healthy_reset=60.0)

        async def start(_client: ControlClient, wait: float = 30.0) -> None:
            wn.proc = Lives()  # type: ignore[assignment]
        wn.start = start  # type: ignore[method-assign]
        wn.proc = Lives()  # type: ignore[assignment]
        task = asyncio.create_task(wn.supervise(client, clock=lambda: now[0]))
        await until(lambda: len(sleeps.delays) >= 6)
        task.cancel()
    asyncio.run(body())
    # grows while children die young; the 61s child (>= 60) resets it; then it grows again
    assert sleeps.delays[:6] == [1.0, 2.0, 4.0, 1.0, 2.0, 4.0]


def test_unit_refuses_secret_values_naming_only_the_variable() -> None:
    token = "sk-" + "A1b2" * 6
    with pytest.raises(ValueError) as info:
        unit.render("/opt/venv/bin/python", {"HETERODYNE_CONFIG_DIR": f"/srv/{token}/x"})
    assert "HETERODYNE_CONFIG_DIR" in str(info.value) and token not in str(info.value)


def test_unit_subcommand_exits_config_without_printing_a_token(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    token = "sk-" + "Zy9x" * 6
    monkeypatch.setenv("HETERODYNE_CONFIG_DIR", str(tmp_path / token))
    assert cli.main(["unit"]) == cli.EX_CONFIG
    seen = capsys.readouterr()
    assert token not in seen.out + seen.err and "HETERODYNE_CONFIG_DIR" in seen.err and seen.out == ""


def test_supervise_cancellation_leaves_a_child_that_stop_ends(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    binary, _ = fake_wn_agent(tmp_path)
    Sleeps(monkeypatch)

    async def body() -> None:
        wn, client = supervised(tmp_path, binary)
        await wn.start(client, wait=15)
        task = asyncio.create_task(wn.supervise(client))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert wn.alive()                       # cancelling supervision never orphans or kills silently
        proc = wn.proc
        await wn.stop()
        assert proc is not None and proc.returncode is not None and not wn.alive()
    asyncio.run(body())


# --- review R3: filesystem failures and the no-traceback boundary ---------------------------------

NPUB = hex_to_npub("ab" * 32)
needs_nonroot = pytest.mark.skipif(os.geteuid() == 0, reason="chmod 0500 does not bind root")


def _assert_quiet(capsys: pytest.CaptureFixture[str]) -> str:
    out = capsys.readouterr()
    text = out.out + out.err
    assert NPUB not in text and "ab" * 32 not in text and "Traceback" not in text
    return text


def _via_main(monkeypatch: pytest.MonkeyPatch, s: object) -> None:
    monkeypatch.setattr(cli.hconfig, "load", lambda: None)
    monkeypatch.setattr(cli, "resolve", lambda cfg, env: s)


@needs_nonroot
def test_init_unwritable_marmot_home_is_a_safe_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        s = make_settings(tmp_path, marmot_home=locked / NPUB / "marmot")
        _via_main(monkeypatch, s)
        assert cli.main(["init"]) != 0
    finally:
        locked.chmod(0o700)
    assert "PermissionError" in _assert_quiet(capsys)


@needs_nonroot
def test_unwritable_state_dir_is_a_safe_config_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        _via_main(monkeypatch, make_settings(tmp_path, state_dir=locked / NPUB / "admind"))
        assert cli.main(["init"]) == cli.EX_CONFIG
    finally:
        locked.chmod(0o700)
    assert "PermissionError" in _assert_quiet(capsys)


@pytest.mark.parametrize("command", ["init", "rearm", "unit", "run"])
def test_unexpected_exception_prints_one_safe_line(command: str, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError(f"leaked {NPUB} at /x/{NPUB}")
    monkeypatch.setattr(cli, "_with_settings", boom)
    monkeypatch.setattr(cli.unit, "render", boom)
    assert cli.main([command]) == 1
    text = _assert_quiet(capsys)
    assert text.strip() == f"admind: {command} failed (RuntimeError)"


def test_unexpected_exception_in_hook_still_exits_zero(monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    import heterodyne.admind.hook as hook

    def boom(*_a: object, **_k: object) -> int:
        raise OSError(f"cannot open {NPUB}")
    monkeypatch.setattr(hook, "hook_main", boom)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    assert cli.main(["hook"]) == 0
    assert _assert_quiet(capsys).strip() == "admind: hook failed (OSError)"


def test_socket_errors_do_not_echo_the_path(tmp_path: Path) -> None:
    client = ControlClient(tmp_path / NPUB / "missing.sock", "t" * 32)
    with pytest.raises(ControlError) as info:
        asyncio.run(client.account_list())
    assert NPUB not in str(info.value) and "missing.sock" not in str(info.value)


@pytest.mark.parametrize("make_argv", [
    lambda npub, tok: ["init", "--operator", npub, "--bogus"],
    lambda npub, tok: ["init", tok],
    lambda npub, tok: ["init", "--token", tok],
    lambda npub, tok: [npub],
    lambda npub, tok: ["hook2", tok, npub],
    lambda npub, tok: ["unit", f"--x={npub}"],
])
def test_argparse_errors_are_value_free(make_argv: object, capsys: pytest.CaptureFixture[str]) -> None:
    npub = hex_to_npub("ab" * 32)
    token = "sk-" + "Q7w8" * 6
    argv = make_argv(npub, token)  # type: ignore[operator]
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2
    seen = capsys.readouterr()
    text = seen.out + seen.err
    assert npub not in text and "ab" * 32 not in text and token not in text
    assert "usage: admind" in seen.err and "invalid arguments" in seen.err


def test_stop_tolerates_child_exit_race(tmp_path: Path) -> None:
    class RacyProc:
        returncode = None
        reaped = False

        def terminate(self) -> None:
            raise ProcessLookupError

        def kill(self) -> None:
            raise ProcessLookupError

        async def wait(self) -> int:
            self.reaped = True
            self.returncode = 0  # type: ignore[assignment]
            return 0

    proc = RacyProc()

    async def body() -> None:
        wn = WnAgent("wn-agent", tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        wn.proc = proc  # type: ignore[assignment]
        await wn.stop()
    asyncio.run(body())
    assert proc.reaped  # a stop() that swallows the race and returns without waiting must fail


def test_stop_kills_and_reaps_when_terminate_times_out(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class StubbornProc:
        returncode = None

        def terminate(self) -> None:
            calls.append("terminate")

        def kill(self) -> None:
            calls.append("kill")
            raise ProcessLookupError  # it exited between the timeout and the kill

        async def wait(self) -> int:
            calls.append("wait")
            if calls.count("wait") == 1:
                await asyncio.sleep(3600)  # outlives the (patched) grace period
            self.returncode = -9  # type: ignore[assignment]
            return -9

    real_wait_for = asyncio.wait_for

    async def short_wait_for(aw: object, timeout: float) -> object:
        return await real_wait_for(aw, 0.01)  # type: ignore[arg-type]
    monkeypatch.setattr(asyncio, "wait_for", short_wait_for)
    proc = StubbornProc()

    async def body() -> None:
        wn = WnAgent("wn-agent", tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        wn.proc = proc  # type: ignore[assignment]
        await wn.stop()
    asyncio.run(body())
    assert calls == ["terminate", "wait", "kill", "wait"] and proc.returncode == -9


def test_stop_terminates_and_reaps(tmp_path: Path) -> None:
    calls: list[str] = []

    class Proc:
        returncode = None

        def terminate(self) -> None:
            calls.append("terminate")

        async def wait(self) -> int:
            calls.append("wait")
            self.returncode = -15  # type: ignore[assignment]
            return -15
    proc = Proc()

    async def body() -> None:
        wn = WnAgent("wn-agent", tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        wn.proc = proc  # type: ignore[assignment]
        await wn.stop()
    asyncio.run(body())
    assert calls == ["terminate", "wait"] and proc.returncode == -15


def test_child_output_goes_to_a_private_log_not_admind_streams(tmp_path: Path,
                                                                capfd: pytest.CaptureFixture[str]) -> None:
    npub = hex_to_npub("ab" * 32)
    token = "sk-" + "Q7w8" * 6
    script = tmp_path / "noisy"
    script.write_text(f"#!{sys.executable}\nimport sys, time\n"
                      f"print({npub!r}); print({token!r}, file=sys.stderr); sys.stdout.flush()\n"
                      "time.sleep(60)\n")
    script.chmod(0o700)
    log = tmp_path / "h" / "wn-agent.log"

    async def body() -> None:
        wn = WnAgent(str(script), tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        wn.prepare()
        log.write_text("stale " * 1000)  # a previous run's output is truncated at the next start
        log.chmod(0o644)
        wn.proc = None
        client = ControlClient(wn.socket_path, wn.token(), timeout=0.2)
        with pytest.raises(WnAgentError):
            await wn.start(client, wait=1.5)  # the noisy child never serves the socket
        await wn.stop()
    asyncio.run(body())
    seen = capfd.readouterr()
    assert npub not in seen.out + seen.err and token not in seen.out + seen.err
    text = log.read_text()
    assert npub in text and token in text and "stale" not in text
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_init_failure_audit_omits_the_peers_detail(tmp_path: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    s = make_settings(tmp_path)
    token = "bearer-" + "Z9y8" * 8
    audit = Audit(s.state_dir / "audit.jsonl")

    class Leaky(WnAgent):
        def prepare(self) -> None:
            super().prepare()

        async def start(self, client: ControlClient, wait: float = 30.0) -> None:
            raise ControlError("wn-agent returned error unauthorized", "unauthorized", True,
                               detail=f"bad token {token}")
    import heterodyne.admind.cli as mod
    orig = mod.WnAgent
    mod.WnAgent = Leaky  # type: ignore[misc,assignment]
    try:
        assert asyncio.run(cli.init(s, Store(s.state_dir / "admind.db"), audit)) == 1
    finally:
        mod.WnAgent = orig  # type: ignore[misc]
    seen = capsys.readouterr()
    record = audit.path.read_text()
    assert token not in record + seen.out + seen.err
    assert '"code": "unauthorized"' in record and '"retryable": true' in record


def test_config_error_for_an_npub_env_key_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("HETERODYNE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv(f"HETERODYNE_{NPUB}_TOKEN", "plain")
    assert cli.main(["init"]) == cli.EX_CONFIG
    _assert_quiet(capsys)


def test_config_error_for_a_nested_npub_key_is_safe() -> None:
    from heterodyne.config import ConfigError
    from heterodyne.config.secret_scan import check
    for tree in ({"a": {NPUB: {"token": "plain"}}}, {"a": {"b" + "ab" * 32: {"token": "plain"}}}):
        with pytest.raises(ConfigError) as info:
            check(tree, "config.toml")
        assert NPUB not in str(info.value) and "ab" * 32 not in str(info.value)


def test_cli_boundary_redacts_and_escapes_a_config_error(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    from heterodyne.config import ConfigError

    def boom() -> None:
        raise ConfigError(f"x: {NPUB} \x1b[31m")
    monkeypatch.setattr(cli.hconfig, "load", boom)
    assert cli.main(["init"]) == cli.EX_CONFIG
    _assert_quiet(capsys)


@pytest.mark.parametrize("bad", ["\x1b[2J", "a\x00b", "\x7f", "\x9b", "a\nb"])
def test_human_output_escapes_controls(bad: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = make_settings(tmp_path)
    store = Store(s.state_dir / "admind.db")
    store.set("group_id_hex", "b2" * 32)
    audit = Audit(s.state_dir / "audit.jsonl")
    shown = dataclasses.replace(s, state_dir=tmp_path / f"d{bad}" / "admind")  # only printed, never opened
    assert asyncio.run(cli.init(shown, store, audit)) == 1
    store.set("latched", f"reason{bad}")
    assert cli.rearm(s, store, audit) == 0
    out = capsys.readouterr().out
    assert not any(ord(c) < 32 and c != "\n" or 0x7f <= ord(c) <= 0x9f for c in out)
    assert out.count("\n") == 2  # only the two prints' own newlines
    assert "\\x" in out


def test_log_open_rejects_a_fifo_promptly(tmp_path: Path) -> None:
    home = tmp_path / "h"
    wn = WnAgent("wn-agent", home, ("wss://a",), Audit(tmp_path / "audit.jsonl"))
    wn.prepare()
    os.mkfifo(wn.log_path)

    # A regression blocks synchronously in os.open, which no asyncio timeout can interrupt, so a
    # watchdog opens a reader after 3s to unblock it; the test then fails on the elapsed time.
    def release() -> None:
        reader = os.open(wn.log_path, os.O_RDONLY | os.O_NONBLOCK)
        threading.Timer(1, os.close, (reader,)).start()
    watchdog = threading.Timer(3, release)
    watchdog.start()
    began = time.monotonic()
    try:
        with pytest.raises(WnAgentError, match="cannot open the wn-agent log file"):
            asyncio.run(wn.start(ControlClient(home / "x.sock", None)))
    finally:
        watchdog.cancel()
    assert time.monotonic() - began < 2
    assert wn.proc is None


def test_log_fd_is_closed_when_fchmod_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import heterodyne.admind.wnagent as mod
    wn = WnAgent("wn-agent", tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
    wn.prepare()
    opened: list[int] = []
    closed: list[int] = []
    real_open, real_close = os.open, os.close

    def spy_open(path: object, *a: object, **k: object) -> int:
        fd = real_open(path, *a, **k)  # type: ignore[call-overload]
        if str(path) == str(wn.log_path):
            opened.append(fd)
        return fd

    def spy_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    real_fchmod = os.fchmod

    def bad_fchmod(fd: int, mode: int) -> None:
        if fd not in opened:        # the audit log's own fchmod must keep working
            real_fchmod(fd, mode)
            return
        raise PermissionError
    monkeypatch.setattr(mod.os, "open", spy_open)
    monkeypatch.setattr(mod.os, "close", spy_close)
    monkeypatch.setattr(mod.os, "fchmod", bad_fchmod)
    with pytest.raises(WnAgentError, match="cannot open the wn-agent log file"):
        asyncio.run(wn.start(ControlClient(tmp_path / "x.sock", None)))
    assert len(opened) == 1 and opened[0] in closed
