import os
import subprocess
import sys
from pathlib import Path

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
