from pathlib import Path

from heterodyne.wsd.accounts import ConfiguredAccounts


def test_login_paths_are_configured_not_canonical(tmp_path: Path) -> None:
    accounts = ConfiguredAccounts({"p": "codex"}, {"HOME": str(tmp_path)})
    codex = tmp_path / ".codex"
    assert accounts.login_paths("codex", "default") == (codex, (codex / "auth.json",))
