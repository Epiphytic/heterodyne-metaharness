"""Plan 4c Task 10: `wsctl harness`'s arguments and output. Each command sends one `harness` request; bad
arguments exit 2 and send nothing."""

from pathlib import Path

import pytest
from test_wsd_daemon import settings

from heterodyne.wsd import cli, ctl

INSTALL = "0.160.0-0123456789ab"


def harness(action: ctl.HarnessAction, **kw: str) -> ctl.CtlRequest:
    return ctl.CtlRequest("harness", harness=ctl.HarnessRequest(action, **kw))


@pytest.mark.parametrize("argv, sent", [
    (["harness", "list"], harness("list")),
    (["harness", "list", "codex"], harness("list", adapter="codex")),
    (["harness", "install", "codex"], harness("install", adapter="codex")),
    (["harness", "install", "codex", "0.160.0"], harness("install", adapter="codex", version="0.160.0")),
    (["harness", "verify", "codex", INSTALL], harness("verify", adapter="codex", install=INSTALL)),
    (["harness", "use", "codex", INSTALL], harness("use", adapter="codex", install=INSTALL)),
    (["harness", "rollback", "codex"], harness("rollback", adapter="codex")),
    (["harness", "remove", "codex", INSTALL], harness("remove", adapter="codex", install=INSTALL)),
    (["harness", "prune"], harness("prune")),
    (["harness", "prune", "--confirm", "0a1b2c3d"], harness("prune", confirm="0a1b2c3d")),
    (["harness"], None),
    (["harness", "select", "codex"], None),
    (["harness", "install"], None),
    (["harness", "verify", "codex"], None),
    (["harness", "use", "codex"], None),
    (["harness", "use", "codex", INSTALL, "extra"], None),
    (["harness", "rollback"], None),
    (["harness", "remove", "codex"], None),
    (["harness", "prune", "codex"], None),
    (["harness", "prune", "--confirm"], None),
    (["harness", "list", "codex", "claude-code"], None),
])
def test_wsctl_harness_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str],
                                 sent: ctl.CtlRequest | None) -> None:
    asked: list[ctl.CtlRequest] = []
    monkeypatch.setattr(cli, "_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(cli, "_ask", lambda s, req: asked.append(req) or ctl.CtlReply("ok", "done"))
    if sent is None:
        with pytest.raises(SystemExit) as exited:
            cli.wsctl_main(argv)
        assert exited.value.code == 2 and asked == []
    else:
        assert cli.wsctl_main(argv) == 0 and asked == [sent]


def test_wsctl_harness_list_shows_each_install_and_its_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    other = "0.160.0-ba9876543210"
    reply = ctl.CtlReply("ok", "codex: 2 install(s) (fallback store)", {
        INSTALL: {"adapter": "codex", "version": "0.160.0", "state": "selected"},
        other: {"adapter": "codex", "version": "0.160.0", "state": "tainted"}})
    monkeypatch.setattr(cli, "_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(cli, "_ask", lambda s, req: reply)
    assert cli.wsctl_main(["harness", "list"]) == 0
    out = capsys.readouterr().out
    assert "fallback" in out
    lines = out.splitlines()
    assert any(INSTALL in line and "selected" in line for line in lines)
    assert any(other in line and "tainted" in line for line in lines)


def test_wsctl_harness_prune_prints_the_code_and_a_refusal_exits_1(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    s = settings(tmp_path)
    monkeypatch.setattr(cli, "_settings", lambda: s)
    monkeypatch.setattr(cli, "_ask", lambda s, req: ctl.CtlReply(
        "ok", "prune would remove 1 install(s); run wsctl harness prune --confirm 0a1b2c3d within 10 minutes",
        {INSTALL: {"adapter": "codex"}}))
    assert cli.wsctl_main(["harness", "prune"]) == 0
    out = capsys.readouterr().out
    assert "--confirm 0a1b2c3d" in out and INSTALL in out
    monkeypatch.setattr(cli, "_ask", lambda s, req: ctl.CtlReply("refused", "the install is in use"))
    assert cli.wsctl_main(["harness", "remove", "codex", INSTALL]) == 1
    assert "in use" in capsys.readouterr().out
