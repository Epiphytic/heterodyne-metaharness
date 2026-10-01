import asyncio
import os
import stat
import sys
from pathlib import Path

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent
from fakes.settings import make_settings

from heterodyne.admind import cli, unit
from heterodyne.admind.audit import Audit
from heterodyne.admind.store import Store
from heterodyne.admind.wnagent import WnAgent, WnAgentError
from heterodyne.marmot.control import ControlClient

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
