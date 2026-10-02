import os
import stat
from pathlib import Path

import pytest

from heterodyne.config import ConfigError
from heterodyne.services import Systemd, UnitStatus, for_backend

FAKE = """#!/bin/sh
echo "$@" >> "{log}"
case "$2" in
  restart) [ "$4" = "bad.service" ] && {{ echo "Unit bad.service not found." >&2; exit 5; }}; exit 0;;
  show) printf 'ActiveState=active\\nSubState=running\\nActiveEnterTimestamp=Wed 2026-09-30 10:00:00 UTC\\n';;
esac
"""


def fake_systemctl(tmp_path: Path) -> tuple[str, Path]:
    log = tmp_path / "calls"
    script = tmp_path / "systemctl"
    script.write_text(FAKE.format(log=log))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), log


def test_restart_and_status_use_user_scope_and_end_of_options(tmp_path: Path) -> None:
    binary, log = fake_systemctl(tmp_path)
    sd = Systemd(binary)
    assert sd.restart("wsd.service") == (True, "")
    ok, detail = sd.restart("bad.service")
    assert not ok and "not found" in detail
    st = sd.status("wsd.service")
    assert st == UnitStatus("wsd.service", "active", "running", "Wed 2026-09-30 10:00:00 UTC")
    assert st.line() == "wsd.service: active (running) since Wed 2026-09-30 10:00:00 UTC"
    calls = log.read_text().splitlines()
    assert calls[0] == "--user restart -- wsd.service"
    assert calls[2].startswith("--user show --property=ActiveState,SubState,ActiveEnterTimestamp -- ")


def test_invalid_unit_names_never_reach_systemctl(tmp_path: Path) -> None:
    binary, log = fake_systemctl(tmp_path)
    with pytest.raises(ValueError):
        Systemd(binary).restart("--now")
    assert not log.exists()


def test_backend_selection() -> None:
    assert isinstance(for_backend("systemd"), Systemd)
    with pytest.raises(ConfigError, match="launchd"):
        for_backend("launchd")
    assert os.name == "posix"


def test_backend_error_redacts_npub_and_names_key() -> None:
    from heterodyne.marmot.nip19 import hex_to_npub

    key = "ab" * 32
    npub = hex_to_npub(key)
    with pytest.raises(ConfigError) as exc:
        for_backend(npub)
    text = str(exc.value)
    assert npub not in text and key not in text
    assert "[platform] service_manager" in text


def test_missing_binary_is_a_failed_result(tmp_path: Path) -> None:
    sd = Systemd(str(tmp_path / "no-such-systemctl"))
    ok, detail = sd.restart("wsd.service")
    assert not ok and "wsd.service" in detail and str(tmp_path) not in detail
    assert sd.status("wsd.service").active == "unknown"


def test_hung_systemctl_is_a_failed_result(tmp_path: Path) -> None:
    script = tmp_path / "systemctl"
    script.write_text("#!/bin/sh\nexec sleep 5\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    sd = Systemd(str(script), timeout=0.2)
    ok, detail = sd.restart("wsd.service")
    assert not ok and "timed out" in detail and "wsd.service" in detail
    assert sd.status("wsd.service").active == "unknown"
