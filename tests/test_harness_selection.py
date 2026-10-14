"""Plan 4c T5: `harnesses.toml`, the host's selected installs (ADR 0001 §7 Managed installs)."""

import os
import stat
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from heterodyne.config import ConfigError
from heterodyne.harness import selection
from heterodyne.harness.selection import InstallPin, Selection

# Built at runtime so this file carries no 64-hex literal (gitleaks).
HEX = "0123456789abcdef"


def pin(n: int) -> InstallPin:
    return InstallPin(f"0.160.{n}-{HEX[:12]}", "sha256:" + (HEX[n % 16] + HEX[:15]) * 4)


SELECTIONS = {"codex": Selection("codex", pin(1), (pin(0),)),
              "claude-code": Selection("claude-code", None, (pin(2), pin(3)))}


def test_the_selection_round_trips_and_is_written_0600_atomically(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    assert selection.load(tmp_path) == {}
    synced: list[str] = []
    real_fsync: Callable[[int], None] = os.fsync

    def fsync(fd: int) -> None:
        synced.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    selection.write(tmp_path, SELECTIONS)
    assert synced == ["file", "dir"]          # the temp file before the rename, the directory after it
    path = tmp_path / selection.FILE
    assert selection.FILE == "harnesses.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert selection.load(tmp_path) == SELECTIONS
    raw = tomllib.loads(path.read_text())
    assert raw["codex"] == {"use": pin(1).install, "digest": pin(1).digest,
                            "previous": [{"use": pin(0).install, "digest": pin(0).digest}]}
    assert "use" not in raw["claude-code"] and len(raw["claude-code"]["previous"]) == 2

    # A crash between the temp write and the rename leaves the old file and no temp file.
    def crash(src: object, dst: object) -> None:
        raise OSError("crash before the rename")

    changed = {"codex": Selection("codex", pin(5), (pin(1), pin(0)))}
    with pytest.raises(OSError, match="crash"):
        selection.write(tmp_path, changed, replace=crash)
    assert selection.load(tmp_path) == SELECTIONS
    assert [p.name for p in tmp_path.iterdir()] == [selection.FILE]
    selection.write(tmp_path, changed)
    assert selection.load(tmp_path) == changed


@pytest.mark.parametrize("text, key", [
    ('[codex]\nuse = "0.160.1-0123456789ab"\n', "digest"),
    ('[codex]\ndigest = "sha256:00"\n', "digest"),
    ('[codex]\nuse = "latest"\ndigest = "sha256:{d}"\n', "use"),
    ('[codex]\nuse = "0.160.1-0123456789ab"\ndigest = "sha1:{d}"\n', "digest"),
    ('[codex]\nprevious = "x"\n', "previous"),
    ('[codex]\nprevious = [{{ use = "0.1.0-0123456789ab" }}]\n', "previous"),
    ('[codex]\nchannel = 1\n', "unknown"),
    ('codex = 1\n', "codex"),
    ('["Bad Adapter"]\n', "adapter"),
    ('[codex\n', "harnesses.toml"),
])
def test_a_malformed_selection_is_refused(tmp_path: Path, text: str, key: str) -> None:
    (tmp_path / selection.FILE).write_text(text.format(d=HEX * 4))
    with pytest.raises(ConfigError, match=key) as exc:
        selection.load(tmp_path)
    assert "0123456789ab" not in str(exc.value)


def test_previous_keeps_at_most_ten(tmp_path: Path) -> None:
    assert selection.MAX_PREVIOUS == 10
    history = tuple(pin(n) for n in range(12))
    selection.write(tmp_path, {"codex": Selection("codex", pin(20), history)})
    assert selection.load(tmp_path)["codex"].previous == history[:10]   # the ten most recent, in order
    # A file holding more than ten was not written by `write`: refused, not trimmed.
    rows = ", ".join(f'{{ use = "{p.install}", digest = "{p.digest}" }}' for p in history)
    (tmp_path / selection.FILE).write_text(f"[codex]\nprevious = [{rows}]\n")
    with pytest.raises(ConfigError, match="previous"):
        selection.load(tmp_path)


def test_an_unreadable_selection_is_not_an_absent_one(tmp_path: Path) -> None:
    (tmp_path / selection.FILE).symlink_to(tmp_path / "gone.toml")       # a dangling link
    with pytest.raises(ConfigError, match="exists but can't be read"):
        selection.load(tmp_path)
    (tmp_path / selection.FILE).unlink()
    (tmp_path / selection.FILE).mkdir()                                   # not a file
    with pytest.raises(ConfigError, match="exists but can't be read"):
        selection.load(tmp_path)
