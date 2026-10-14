"""The platform install (design btq-v7sgt) against the fake host: the preflight, the release, the services
and uninstall. Offline and unprivileged; every host command is a shim that logs it (tests/deploy_host.py)."""

import pytest
from deploy_host import (
    Host,
    host_fixture,  # noqa: F401  (the `host` fixture)
    install_refusal,
    installer,
)

NEVER = ("daemon-reexec", "user@", " -M ", "machinectl", "enable-linger", "disable-linger", "set-environment",
         "unset-environment", "import-environment")


def never_touched_the_manager(host: Host) -> None:
    for call in host.calls():
        assert not any(bad in f" {call} " for bad in NEVER), call


# --- the preflight: lingering, the bus and the user manager (D1) ---

@pytest.mark.parametrize("linger", ["no\n", None], ids=["no", "unreadable"])
@pytest.mark.parametrize("dry", [False, True], ids=["run", "dry-run"])
def test_lingering_off_refuses_with_the_command(host: Host, linger: str | None, dry: bool) -> None:
    if linger is None:
        (host.shims / "linger").unlink()
    else:
        host.put("linger", linger)
    why = install_refusal(host, installer(host, *(["--dry-run"] if dry else [])))
    assert why == (f"lingering is off for {host.user} (or could not be read): run "
                   f"`sudo loginctl enable-linger {host.user}`, then run the installer again")
    never_touched_the_manager(host)


@pytest.mark.parametrize("bus", ["missing", "foreign", "not-a-socket"])
def test_a_bus_that_is_not_the_users_refuses(host: Host, bus: str) -> None:
    if bus == "missing":
        host.bus().unlink()
    elif bus == "foreign":
        host.put("uids", f"{host.bus_rel()}=0\n")
    else:
        host.bus().unlink()
        host.bus().write_text("")
    assert install_refusal(host, installer(host)) == "the service user's systemd bus is not running"


@pytest.mark.parametrize("state", ["starting", "stopping", "offline", None])
def test_a_user_manager_that_is_not_up_refuses(host: Host, state: str | None) -> None:
    if state is None:
        host.put("fail", "user-systemctl is-system-running\n")
    else:
        host.put("user-system", f"{state}\n")
    assert install_refusal(host, installer(host)) == "the service user's systemd manager is not running"


@pytest.mark.parametrize("state", ["running", "degraded"])
def test_running_or_degraded_is_up_and_the_manager_is_reached_on_its_own_bus(host: Host, state: str) -> None:
    host.put("user-system", f"{state}\n")
    out = installer(host, "--dry-run")
    assert out.returncode in (0, 3), out.stdout + out.stderr
    assert "user-systemctl is-system-running" in host.calls()
    uid = host.uid
    assert host.user_bus_env() == {"XDG_RUNTIME_DIR": f"/run/user/{uid}",
                                   "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}
    never_touched_the_manager(host)
