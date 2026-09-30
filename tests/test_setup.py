import os
import stat
from pathlib import Path

import pytest

from heterodyne import cli, platform
from heterodyne.config import load


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "cfg"
    monkeypatch.setenv("HETERODYNE_CONFIG_DIR", str(d))
    return d


def test_setup_writes_both_files_mode_0600_even_with_permissive_umask(
        target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[tuple[str, int]] = []
    real_open = os.open

    def spy(path: str, flags: int, mode: int = 0o777, *a: object, **kw: object) -> int:
        fd = real_open(path, flags, mode)
        if flags & os.O_CREAT:
            created.append((str(path), stat.S_IMODE(os.fstat(fd).st_mode)))
        return fd

    monkeypatch.setattr(os, "open", spy)
    old = os.umask(0)
    try:
        assert cli.main(["setup"]) == 0
    finally:
        os.umask(old)
    assert sorted(Path(p).name for p, _ in created) == ["config.toml", "policy.toml"]
    assert all(mode == 0o600 for _, mode in created)
    for name in ("config.toml", "policy.toml"):
        assert stat.S_IMODE((target / name).stat().st_mode) == 0o600


def test_setup_keeps_existing_file_unmodified(target: Path) -> None:
    target.mkdir()
    (target / "config.toml").write_text("# mine\n")
    (target / "config.toml").chmod(0o644)
    assert cli.main(["setup"]) == 0
    assert (target / "config.toml").read_text() == "# mine\n"
    assert stat.S_IMODE((target / "config.toml").stat().st_mode) == 0o644
    assert (target / "policy.toml").exists()


def test_setup_does_not_follow_dangling_symlink(target: Path, tmp_path: Path) -> None:
    target.mkdir()
    victim = tmp_path / "elsewhere" / "victim.toml"
    victim.parent.mkdir()
    (target / "config.toml").symlink_to(victim)
    cli.main(["setup"])
    assert not victim.exists()
    assert (target / "config.toml").is_symlink()


def test_setup_saves_platform_backends(target: Path) -> None:
    assert cli.main(["setup"]) == 0
    os_name = platform.detect()
    cfg = load(None, dict(os.environ))
    assert cfg.get("platform.os") == os_name
    for key, value in platform.backends(os_name).items():
        assert cfg.get(f"platform.{key}") == value
