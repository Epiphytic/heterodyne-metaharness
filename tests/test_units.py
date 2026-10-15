"""The platform units' shared renderer (design btq-v7sgt D3): the environment block that pins the checked
locations, the unset list, the marker and the unit revision, for admind and wsd."""

import argparse
import configparser
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from heterodyne import units
from heterodyne.admind import unit as admind_unit
from heterodyne.marmot.nip19 import hex_to_npub
from heterodyne.wsd import cli as wsd_cli
from heterodyne.wsd import unit as wsd_unit

T = "[Service]\nExecStart={python} -m x run\n{environment}[Install]\nWantedBy=default.target\n"
C = "a" * 40
LOCATIONS = {"path": "/usr/bin", "config_dir": "/c", "state_dir": "/s"}


def block(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(("Environment=", "UnsetEnvironment="))]


def test_the_block_pins_the_locations_and_unsets_the_overrides() -> None:
    text = units.render(T, "/r/.venv/bin/python3", path="/r/.venv/bin:/usr/bin",
                        config_dir="/h/.config/heterodyne", state_dir="/h/.local/state/heterodyne", release=C)
    lines = block(text)
    assert lines[:4] == ["Environment=PATH=/r/.venv/bin:/usr/bin",
                         "Environment=HETERODYNE_CONFIG_DIR=/h/.config/heterodyne",
                         "Environment=HETERODYNE_STATE_DIR=/h/.local/state/heterodyne",
                         f"Environment=HETERODYNE_RELEASE={C}"]
    assert re.fullmatch(r"Environment=HETERODYNE_UNIT_REV=[0-9a-f]{12}", lines[4])
    assert lines[5] == "UnsetEnvironment=BTQ_REPO XDG_CONFIG_HOME XDG_STATE_HOME XDG_DATA_HOME"
    assert len(lines) == 6
    assert text.splitlines()[0] == f"{units.MARKER_PREFIX}{C}; the next install replaces it."


def test_no_name_is_both_set_and_unset() -> None:
    lines = block(units.render(T, "/p", release=C, **LOCATIONS))
    set_names = {line.removeprefix("Environment=").split("=", 1)[0] for line in lines[:-1]}
    unset = set(lines[-1].removeprefix("UnsetEnvironment=").split())
    assert set_names.isdisjoint(unset) and unset == set(units.UNSET)


def test_the_revision_covers_every_other_line() -> None:
    a = units.render(T, "/p", release=C, **LOCATIONS)
    b = units.render(T.replace("x run", "y run"), "/p", release=C, **LOCATIONS)
    c = units.render(T, "/p", release=C, **(LOCATIONS | {"path": "/usr/bin:/bin"}))
    assert len({units.rev(t) for t in (a, b, c)}) == 3
    assert f"{units.REV_KEY}={units.rev(a)}\n" in a


def test_without_a_release_there_is_no_marker_and_no_release_variable() -> None:
    text = units.render(T, "/p", **LOCATIONS)
    assert not text.startswith("#") and "HETERODYNE_RELEASE" not in text and units.REV_KEY in text


@pytest.mark.parametrize("release", ["HEAD", "a" * 39, "A" * 40, "a" * 41])
def test_a_release_that_is_not_a_full_commit_refuses(release: str) -> None:
    with pytest.raises(ValueError, match="HETERODYNE_RELEASE"):
        units.render(T, "/p", release=release, **LOCATIONS)


@pytest.mark.parametrize(("name", "key"), [("python", "python"), ("PATH", "path"),
                                           ("HETERODYNE_CONFIG_DIR", "config_dir"),
                                           ("HETERODYNE_STATE_DIR", "state_dir")])
@pytest.mark.parametrize("bad", [" ", '"', "\\", "\x00", "\x85"])
def test_unsafe_values_refuse_naming_the_key(name: str, key: str, bad: str) -> None:
    values = {"python": "/p", **LOCATIONS, key: f"/x{bad}y"}
    with pytest.raises(ValueError, match=name):
        units.render(T, values.pop("python"), **values)


def test_an_empty_path_refuses() -> None:
    with pytest.raises(ValueError, match="PATH is empty"):
        units.render(T, "/p", **(LOCATIONS | {"path": ""}))


def test_sensitive_values_refuse_without_printing_them() -> None:
    npub = hex_to_npub("ab" * 32)
    with pytest.raises(ValueError) as info:
        units.render(T, "/p", **(LOCATIONS | {"config_dir": f"/srv/{npub}"}))
    assert "HETERODYNE_CONFIG_DIR" in str(info.value) and npub not in str(info.value)


def test_percent_is_escaped_for_systemd() -> None:
    text = units.render(T, "/p%", **(LOCATIONS | {"path": "/opt/50%/bin"}))
    assert "ExecStart=/p%% -m x run" in text and "Environment=PATH=/opt/50%%/bin" in text


def test_admind_by_hand_writes_the_locations_it_resolves_and_never_passes_overrides_through() -> None:
    env = {"PATH": "/usr/bin", "HOME": "/h", "HETERODYNE_CONFIG_DIR": "/etc/hz", "XDG_STATE_HOME": "/st",
           "BTQ_REPO": "/b"}
    text = admind_unit.render("/p", env)
    assert "Environment=HETERODYNE_CONFIG_DIR=/etc/hz\n" in text
    assert "Environment=HETERODYNE_STATE_DIR=/st/heterodyne\n" in text
    assert not [line for line in text.splitlines() if line.startswith(("Environment=BTQ_REPO=",
                                                                        "Environment=XDG_"))]


def test_the_installer_form_takes_every_value_from_its_flags(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    units.add_arguments(parser)
    args = parser.parse_args(["--python", "/r/py", "--config-dir", "/c", "--state-dir", "/s", "--release", C])
    assert units.command(admind_unit.render, args, {"PATH": "/usr/bin", "HOME": "/elsewhere"}) == 0
    out = capsys.readouterr().out
    assert "ExecStart=/r/py -m heterodyne.admind run\n" in out
    assert "Environment=HETERODYNE_CONFIG_DIR=/c\n" in out and "/elsewhere" not in out


def test_a_value_that_cannot_go_in_a_unit_is_one_line_and_78(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    units.add_arguments(parser)
    args = parser.parse_args(["--config-dir", "/c d", "--state-dir", "/s"])
    assert units.command(admind_unit.render, args, {"PATH": "/usr/bin"}) == units.EX_CONFIG
    seen = capsys.readouterr()
    assert seen.out == "" and seen.err.count("\n") == 1 and "HETERODYNE_CONFIG_DIR" in seen.err


def parsed(text: str) -> configparser.ConfigParser:
    p = configparser.ConfigParser(interpolation=None, strict=False, empty_lines_in_values=False)
    p.optionxform = str  # type: ignore[assignment,method-assign]   # systemd keys are case-sensitive
    p.read_string("\n".join(line for line in text.splitlines() if not line.startswith("#")))
    return p


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed")
def test_the_admind_render_passes_systemd_analyze_verify(tmp_path: Path) -> None:
    f = tmp_path / "heterodyne-admind.service"
    f.write_text(admind_unit.render(sys.executable, {"PATH": "/usr/bin:/bin"}, config_dir=str(tmp_path),
                                    state_dir=str(tmp_path), release=C))
    out = subprocess.run(["systemd-analyze", "--user", "verify", "--man=no", str(f)], capture_output=True,
                         text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    assert parsed(f.read_text())["Service"]["ExecStart"] == f"{sys.executable} -m heterodyne.admind run"


def test_the_wsd_unit_is_the_designs() -> None:
    text = wsd_unit.render("/r/py", {"PATH": "/usr/bin"}, config_dir="/c", state_dir="/s", release=C)
    s = parsed(text)["Service"]
    assert s["ExecStart"] == "/r/py -m heterodyne.wsd run"
    assert int(s["TimeoutStopSec"]) > 120      # wsd's own drain on SIGTERM (docs/wsd.md)
    assert (s["Type"], s["Restart"], s["KillMode"], s["RestartSec"], s["RestartPreventExitStatus"],
            s["UMask"]) == ("simple", "always", "control-group", "5", "78", "0077")
    assert parsed(text)["Install"]["WantedBy"] == "default.target"


def test_wsd_unit_never_sets_btq_repo_and_needs_no_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("BTQ_REPO", "/some/repo")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HETERODYNE_CONFIG_DIR", str(tmp_path / "absent"))
    assert wsd_cli.wsd_main(["unit", "--python", "/r/py", "--config-dir", "/c", "--state-dir", "/s"]) == 0
    out = capsys.readouterr().out
    assert not [line for line in out.splitlines() if line.startswith("Environment=BTQ_REPO=")]
    assert "UnsetEnvironment=BTQ_REPO " in out


def test_wsd_unit_refuses_a_bad_value_with_78(capsys: pytest.CaptureFixture[str]) -> None:
    assert wsd_cli.wsd_main(["unit", "--python", "/r/my py", "--config-dir", "/c", "--state-dir", "/s"]) == 78
    assert "python" in capsys.readouterr().err


def test_python_dash_m_heterodyne_wsd_runs_the_cli() -> None:
    out = subprocess.run([sys.executable, "-m", "heterodyne.wsd", "--help"], capture_output=True, text=True,
                         timeout=60, check=False)
    assert out.returncode == 0 and "unit" in out.stdout and "run" in out.stdout


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed")
def test_the_wsd_render_passes_systemd_analyze_verify(tmp_path: Path) -> None:
    f = tmp_path / "heterodyne-wsd.service"
    f.write_text(wsd_unit.render(sys.executable, {"PATH": "/usr/bin:/bin"}, config_dir=str(tmp_path),
                                 state_dir=str(tmp_path), release=C))
    out = subprocess.run(["systemd-analyze", "--user", "verify", "--man=no", str(f)], capture_output=True,
                         text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
