import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_install_agnostic.py"

# Install-specific-looking samples are assembled at runtime so this test file carries no literal
# the checker (or gitleaks) would flag when it scans the repo itself. All values are obviously fake.
HOME_PATH = "/" + "home/alice/repos/x"
IP_PORT = "10.1.2.3" + ":3307"
EMAIL = "alice" + "@corp.io"
NPUB = "npub1" + "q" * 58


def hermetic_env(tmp: Path) -> dict[str, str]:
    # Point the default --extra lookup at an empty dir so the operator's real leakcheck.txt is never read.
    return {**os.environ, "HETERODYNE_CONFIG_DIR": str(tmp / "no-config")}


def run(tmp: Path, files: dict[str, str], extra: str | None = None) -> subprocess.CompletedProcess[str]:
    for name, text in files.items():
        (tmp / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp / name).write_text(text)
    args = [sys.executable, str(SCRIPT), "--root", str(tmp)]
    if extra is not None:
        (tmp / "leak.txt").write_text(extra)
        args += ["--extra", str(tmp / "leak.txt")]
    return subprocess.run([*args, *files], capture_output=True, text=True, env=hermetic_env(tmp))


def test_clean_file_passes(tmp_path: Path) -> None:
    text = "Config lives in ${XDG_CONFIG_HOME:-~/.config}/heterodyne\n"
    assert run(tmp_path, {"docs/a.md": text}).returncode == 0


def test_flags_install_specific_values(tmp_path: Path) -> None:
    text = f"see {HOME_PATH}\nserver {IP_PORT}\nmail {EMAIL}\n{NPUB}\n"
    r = run(tmp_path, {"docs/a.md": text})
    assert r.returncode == 1
    for rule in ("home-path", "ip-port", "email", "npub"):
        assert rule in r.stdout


def test_output_format(tmp_path: Path) -> None:
    r = run(tmp_path, {"docs/a.md": f"ok\nsee {HOME_PATH}\n"})
    assert r.stdout.splitlines() == [f"docs/a.md:2: home-path: see {HOME_PATH}"]


def test_example_domain_email_passes(tmp_path: Path) -> None:
    assert run(tmp_path, {"docs/a.md": "mail " + "alice" + "@example.com\n"}).returncode == 0


def test_model_names_only_flagged_in_src(tmp_path: Path) -> None:
    assert run(tmp_path, {"docs/a.md": "we use gpt-6-sol\n"}).returncode == 0
    r = run(tmp_path, {"src/heterodyne/x.py": 'MODEL = "gpt-6-sol"\n'})
    assert r.returncode == 1 and "model-name" in r.stdout


def test_examples_and_fixtures_exempt(tmp_path: Path) -> None:
    files = {"examples/c.toml": "/" + "home/alice\n", "tests/fixtures/f.txt": "10.0.0.1" + ":22\n"}
    assert run(tmp_path, files).returncode == 0


def test_exemptions_are_prefix_exact(tmp_path: Path) -> None:
    # A lookalike directory or file name does not inherit an exemption.
    files = {"docs/examples/c.toml": HOME_PATH + "\n", "LICENSE.extra": HOME_PATH + "\n"}
    r = run(tmp_path, files)
    assert r.returncode == 1
    assert "docs/examples/c.toml:1" in r.stdout and "LICENSE.extra:1" in r.stdout


def test_extra_local_denylist(tmp_path: Path) -> None:
    r = run(tmp_path, {"docs/a.md": "runs on myhostname\n"}, extra="myhostname\n")
    assert r.returncode == 1 and "local-denylist" in r.stdout


def test_extra_ignores_blank_and_comment_lines(tmp_path: Path) -> None:
    r = run(tmp_path, {"docs/a.md": "# heading\nplain text\n"}, extra="\n# a comment\n\n")
    assert r.returncode == 0, r.stdout


def test_allow_marker_suppresses_only_named_rule_on_that_line(tmp_path: Path) -> None:
    marker = "  # install-agnostic: allow=ip-port (sandbox-internal proxy)"
    ok = run(tmp_path, {"spikes/p.py": f'PROXY = "{IP_PORT}"{marker}\n'})
    assert ok.returncode == 0, ok.stdout
    # The marker does not cover other rules on the same line...
    r = run(tmp_path, {"spikes/q.py": f'X = "{IP_PORT} {HOME_PATH}"{marker}\n'})
    assert r.returncode == 1 and "q.py:1: home-path:" in r.stdout and "q.py:1: ip-port:" not in r.stdout
    # ...nor the next line.
    r = run(tmp_path, {"spikes/r.py": f"# install-agnostic: allow=ip-port\nPROXY = '{IP_PORT}'\n"})
    assert r.returncode == 1 and "spikes/r.py:2: ip-port" in r.stdout


def test_allow_marker_cannot_silence_local_denylist(tmp_path: Path) -> None:
    line = "runs on myhostname  # install-agnostic: allow=local-denylist\n"
    r = run(tmp_path, {"docs/a.md": line}, extra="myhostname\n")
    assert r.returncode == 1 and "local-denylist" in r.stdout


def test_missing_explicit_extra_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("fine\n")
    r = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--extra",
                        str(tmp_path / "nope.txt"), "a.md"], capture_output=True, text=True,
                       env=hermetic_env(tmp_path))
    assert r.returncode == 2


def test_default_scan_uses_git_ls_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracked.md").write_text(HOME_PATH + "\n")
    (tmp_path / "untracked.md").write_text(HOME_PATH + "\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.md"], check=True)
    r = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path)],
                       capture_output=True, text=True, env=hermetic_env(tmp_path))
    assert r.returncode == 1
    assert "tracked.md:1" in r.stdout and "untracked.md" not in r.stdout


# --- Fail-closed behaviour: nothing may be silently skipped. ---


def scan(root: Path, *paths: str, extra: str | None = None) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(SCRIPT), "--root", str(root)]
    if extra is not None:
        (root / "leak.txt").write_text(extra)
        args += ["--extra", str(root / "leak.txt")]
    env = {**hermetic_env(root), "GIT_CEILING_DIRECTORIES": str(root.parent)}
    return subprocess.run([*args, *paths], capture_output=True, text=True, env=env)


def git_repo(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def test_invalid_utf8_does_not_hide_the_rest_of_the_file(tmp_path: Path) -> None:
    data = b"caf\xe9 " + HOME_PATH.encode() + b"\nok\nsee " + IP_PORT.encode() + b"\n"
    (tmp_path / "a.md").write_bytes(data)
    r = scan(tmp_path, "a.md")
    assert r.returncode == 1
    assert "a.md:1: home-path:" in r.stdout and "a.md:3: ip-port:" in r.stdout


def test_binary_file_is_scanned_bytewise(tmp_path: Path) -> None:
    blob = b"\x00\x01\x02 " + HOME_PATH.encode() + b" \xff\x00 myhostname \x00"
    (tmp_path / "img.bin").write_bytes(blob)
    r = scan(tmp_path, "img.bin", extra="myhostname\n")
    assert r.returncode == 1
    assert "img.bin:1: home-path (binary):" in r.stdout and "img.bin:1: local-denylist (binary):" in r.stdout


def test_allow_marker_does_not_apply_in_binary_files(tmp_path: Path) -> None:
    (tmp_path / "b.bin").write_bytes(b"\x00" + IP_PORT.encode() + b" install-agnostic: allow=ip-port\n")
    r = scan(tmp_path, "b.bin")
    assert r.returncode == 1 and "ip-port (binary)" in r.stdout


def test_clean_binary_file_passes(tmp_path: Path) -> None:
    (tmp_path / "c.bin").write_bytes(bytes(range(256)) * 4)
    assert scan(tmp_path, "c.bin").returncode == 0


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can read mode-000 files")
def test_unreadable_file_fails_closed(tmp_path: Path) -> None:
    f = tmp_path / "secret.md"
    f.write_text("fine\n")
    f.chmod(0)
    try:
        r = scan(tmp_path, "secret.md")
    finally:
        f.chmod(0o600)
    assert r.returncode == 1 and "secret.md:0: unreadable:" in r.stdout


def test_missing_path_fails_closed(tmp_path: Path) -> None:
    r = scan(tmp_path, "gone.md")
    assert r.returncode == 1 and "gone.md:0: unreadable:" in r.stdout


def test_directory_path_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    r = scan(tmp_path, "d")
    assert r.returncode == 1 and "d:0: unreadable:" in r.stdout


def test_symlink_is_scanned_as_its_target(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    (repo / "bad").symlink_to(HOME_PATH)          # dangling is fine: git stores the target string
    (repo / "good").symlink_to("docs/readme.md")
    subprocess.run(["git", "-C", str(repo), "add", "bad", "good"], check=True)
    r = scan(repo)
    assert r.returncode == 1
    assert "bad:1: home-path:" in r.stdout and "good:" not in r.stdout


def test_submodule_fails_closed(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    sha = "0123456789abcdef0123456789abcdef01234567"
    subprocess.run(["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/x"],
                   check=True)
    r = scan(repo)
    assert r.returncode == 1 and "vendor/x:0: submodule:" in r.stdout


def test_git_ls_files_failure_exits_2(tmp_path: Path) -> None:
    (tmp_path / "notgit").mkdir()
    r = scan(tmp_path / "notgit")
    assert r.returncode == 2 and "git ls-files failed" in r.stderr


@pytest.mark.parametrize("form", ["tilde", "xdg", "xdg-relative"])
def test_default_deny_list_location_matches_config_dir(tmp_path: Path, form: str) -> None:
    """The default leakcheck.txt is found wherever `heterodyne` itself looks for host config."""
    home = tmp_path / "home"
    if form == "xdg":
        config = tmp_path / "xdg" / "heterodyne"
        env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    else:
        config = home / ".config" / "heterodyne"
        # "~/..." must expand against HOME; a relative XDG_CONFIG_HOME is ignored (XDG spec).
        env = ({"HETERODYNE_CONFIG_DIR": "~/.config/heterodyne"} if form == "tilde"
               else {"XDG_CONFIG_HOME": "relative/xdg"})
    config.mkdir(parents=True)
    (config / "leakcheck.txt").write_text("sekrit-host\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("built on sekrit-host\n")
    base = {k: v for k, v in os.environ.items()
            if k not in ("HETERODYNE_CONFIG_DIR", "XDG_CONFIG_HOME")}
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "docs/a.md"],
                            capture_output=True, text=True, env={**base, "HOME": str(home), **env},
                            cwd=tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "local-denylist" in result.stdout
