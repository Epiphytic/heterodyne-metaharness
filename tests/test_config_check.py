"""`heterodyne config check`: plan 4c T5's `[harnesses]` lines (the selection, the store's problems and the
fallback warning)."""

import os
from pathlib import Path

import pytest
from sandbox_env import config_env

from heterodyne import cli
from heterodyne.harness import paths, selection
from heterodyne.harness.selection import InstallPin, Selection
from heterodyne.harness.settings import FALLBACK_WARNING

HEX = "0123456789abcdef"   # digests built at runtime: no 64-hex literal (gitleaks)
PIN = InstallPin(f"0.160.0-{HEX[:12]}", "sha256:" + HEX[::-1] * 4)


def check(env: dict[str, str], monkeypatch: pytest.MonkeyPatch,
          capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    for var in [v for v in os.environ if v.startswith(("HETERODYNE_", "XDG_"))]:
        monkeypatch.delenv(var)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    rc = cli.main(["config", "check"])
    out, err = capsys.readouterr()
    return rc, out, err


def test_config_check_shows_the_fallback_warning_and_the_selection_source(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert FALLBACK_WARNING == ("managed installs use the fallback: a host process of the service user can "
                                "change an install after its checks (accepted risk, ADR §17 item 13)")
    env = config_env(tmp_path, "[harnesses]\nfallback = true\n")
    state = tmp_path / "state"
    env["HETERODYNE_STATE_DIR"] = str(state)
    env["XDG_DATA_HOME"] = str(state / "data")      # so the fallback's default store is in the state dir
    selection.write(Path(env["HETERODYNE_CONFIG_DIR"]), {"codex": Selection("codex", PIN, (PIN,))})
    rc, out, err = check(env, monkeypatch, capsys)
    assert rc == 0, err
    lines = out.splitlines()
    assert f"warning: {FALLBACK_WARNING}" in lines
    assert (f"harnesses.selection.codex = {PIN.install} (digest {PIN.digest[7:19]}, 1 previous)"
            "    (harnesses.toml)") in lines
    assert "harnesses.selection.claude-code = none    (harnesses.toml)" in lines
    assert f"harnesses problem: {paths.STORE_IN_STATE_DIR}" in lines
    assert f"harnesses problem: {paths.HELPER_DRIFT}" not in lines      # no helper under the fallback
    assert PIN.digest not in out                                       # a digest shows as its prefix only


def test_config_check_without_the_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    env = config_env(tmp_path, f'[harnesses]\nhelper_copy = "{tmp_path}/no-helper.py"\n')
    rc, out, err = check(env, monkeypatch, capsys)
    assert rc == 0, err
    lines = out.splitlines()
    assert not any(FALLBACK_WARNING in line for line in lines)
    assert f"harnesses problem: {paths.HELPER_DRIFT}" in lines         # the installed copy is missing
    assert "harnesses.selection.codex = none    (harnesses.toml)" in lines


def test_config_check_refuses_a_bad_harnesses_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    env = config_env(tmp_path, "[harnesses]\nstaging_hours = 0\n")
    rc, _, err = check(env, monkeypatch, capsys)
    assert rc == 1 and err.startswith("config error: [harnesses] staging_hours")
    env = config_env(tmp_path)
    (Path(env["HETERODYNE_CONFIG_DIR"]) / selection.FILE).write_text("[codex]\nuse = 1\n")
    rc, _, err = check(env, monkeypatch, capsys)
    assert rc == 1 and err.startswith("config error: harnesses.toml")


def test_config_check_redacts_a_full_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    commit = HEX * 4
    env = config_env(tmp_path, f"""
[harnesses.codex]
version = "0.160.0"
target = "x86_64-unknown-linux-musl"
content = [{{ kind = "skills", name = "s", git = "https://example.org/s.git", commit = "{commit}" }}]
""")
    rc, out, err = check(env, monkeypatch, capsys)
    assert rc == 0, err
    assert commit not in out + err
    assert "harnesses.codex.content = [{'kind': 'skills', 'name': 's', 'git': 'https://example.org/s.git', " \
           "'commit': <redacted hex key>}]    (host:config.toml)" in out.splitlines()


def test_config_check_redacts_the_derived_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    # r2: the fallback's store comes from XDG_DATA_HOME, so it is a user-supplied path like any other.
    ident = HEX * 4
    env = config_env(tmp_path, "[harnesses]\nfallback = true\n")
    env["XDG_DATA_HOME"] = str(tmp_path / ident / "data")
    rc, out, err = check(env, monkeypatch, capsys)
    assert rc == 0, err
    assert ident not in out + err
    assert "harnesses.store = <redacted hex key>    (defaults, fallback)" in out.splitlines()


def test_config_check_counts_an_unlistable_repo_parent_as_a_worktree(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # The btq worktrees (`<repo>-btq-*`) are found by listing the repo's parent; when that can't be listed,
    # the parent itself stands in for them rather than the store being judged clear of them.
    parent = tmp_path / "src"
    (parent / "repo").mkdir(parents=True)
    store = parent / "repo-btq-1" / "store"
    store.mkdir(parents=True)
    env = config_env(tmp_path, f'[harnesses]\nstore = "{store}"\n',
                     {"w": f'[repos]\nmain = "{parent / "repo"}"\n'})
    parent.chmod(0o300)                                                 # searchable, not listable
    try:
        rc, out, err = check(env, monkeypatch, capsys)
    finally:
        parent.chmod(0o700)
    assert rc == 0, err
    assert f"harnesses problem: {paths.STORE_IN_WORKTREE}" in out.splitlines()
