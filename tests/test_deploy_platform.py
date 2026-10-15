"""The platform install (design btq-v7sgt) against the fake host: the preflight, the release, the services
and uninstall. Offline and unprivileged; every host command is a shim that logs it (tests/deploy_host.py)."""

import base64
import contextlib
import fcntl
import hashlib
import os
import shlex
import shutil
import stat
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from deploy_host import (
    ADMIND,
    CHECKOUT,
    FEATURES_OFF,
    HAND_MADE,
    ME,
    README,
    REFUSED,
    REPO,
    SHIM_HEAD,
    WSD,
    Host,
    git_show,
    hook,
    host_fixture,  # noqa: F401  (the `host` fixture)
    host_path,
    install_refusal,
    installer,
    pinned,
    readme_block,
    sum_of,
    summary,
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


# --- the release (D2) ---

def release_dir(host: Host) -> Path:
    return host.home() / ".local/share/heterodyne/releases" / host.commit


def uv_calls(host: Host) -> list[str]:
    return [c for c in host.calls() if c.startswith("uv sync")]


def uv_envs(host: Host) -> list[str]:
    log = host.shims / "uv-env.log"
    return log.read_text().splitlines() if log.exists() else []


def test_a_fresh_build_is_locked_offline_and_complete_last(host: Host) -> None:
    out = installer(host)
    assert out.returncode in (0, 3), out.stdout + out.stderr
    assert summary(out)["release"] == "changed"
    [scratch, real] = uv_calls(host)
    for call in (scratch, real):
        assert call.startswith("uv sync --locked --offline --no-dev --python /usr/bin/python3 --project ")
    assert real.endswith(f"--project {release_dir(host)}")
    assert host.uv_env_of_last_sync() == {"UV_PYTHON_DOWNLOADS": "never", "UV_LINK_MODE": "copy"}
    assert (release_dir(host) / ".complete").read_text().splitlines()[0] == host.commit
    assert (release_dir(host) / "uv.lock").exists()      # the full tree, not PATHS
    assert f"release: {host.commit}" in out.stdout.splitlines()


def test_a_rerun_is_ok_without_uv(host: Host) -> None:
    installer(host)
    host.clear_calls()
    out = installer(host)
    assert summary(out)["release"] == "ok" and uv_calls(host) == []


def test_a_release_without_complete_is_rebuilt(host: Host) -> None:
    installer(host)
    (release_dir(host) / ".complete").unlink()
    (release_dir(host) / "leftover").write_text("x")
    host.clear_calls()
    out = installer(host)
    assert summary(out)["release"] == "changed" and not (release_dir(host) / "leftover").exists()
    assert (release_dir(host) / ".complete").exists()


def test_a_dry_run_builds_only_in_scratch_on_a_disposable_cache_and_removes_it(host: Host) -> None:
    out = installer(host, "--dry-run")
    assert summary(out)["release"] == "would change"
    [env] = uv_envs(host)
    cache = host.home() / ".cache/heterodyne"
    assert env.startswith(f"UV_CACHE_DIR={cache}/preflight.") and env.endswith("/cache")
    assert not cache.exists()
    assert not release_dir(host).exists()


def test_the_real_cache_is_unchanged_by_the_scratch_build(host: Host) -> None:
    before = host.uvcache_snapshot()
    installer(host, "--dry-run")
    assert host.uvcache_snapshot() == before and len(before) == 3
    assert not (host.shims / "uvcache/new-entry").exists()


def test_a_failed_scratch_build_is_removed_and_its_output_masked(host: Host) -> None:
    host.put("uv-miss", "")
    out = installer(host)
    assert install_refusal(host, out) == "the uv cache lacks a package this release needs: run with --fetch"
    assert not (host.home() / ".cache/heterodyne").exists()
    assert "w not found in the cache, sha256 <hex>" in out.stdout
    assert not release_dir(host).exists()


def test_fetch_skips_the_scratch_build_and_drops_offline(host: Host) -> None:
    host.put("uv-miss", "")
    out = installer(host, "--fetch")
    [real] = uv_calls(host)
    assert "--offline" not in real.split() and summary(out)["release"] == "changed"


def test_a_completed_release_skips_the_scratch_build(host: Host) -> None:
    installer(host)
    host.clear_calls()
    (host.shims / "uv-env.log").unlink()
    host.put("uv-miss", "")
    out = installer(host)
    assert out.returncode in (0, 3), out.stdout + out.stderr
    assert uv_envs(host) == []


def test_a_cache_that_cannot_be_hardlinked_refuses_and_never_uses_the_real_cache(host: Host) -> None:
    host.put("fail", "cp -al\n")
    why = install_refusal(host, installer(host))
    assert why == "the uv cache could not be copied for the offline build check"
    assert uv_envs(host) == []
    assert not (host.home() / ".cache/heterodyne").exists()


def test_a_work_tree_edit_after_resolution_leaves_the_release_the_commits(host: Host) -> None:
    hook(host, "uv sync", f"echo edited >>{host.repo}/deploy/install.sh")
    installer(host)
    for path in ("deploy/install.sh", "deploy/store-helper/heterodyne-store-helper.socket"):
        assert (release_dir(host) / path).read_bytes() == git_show(host, path)


def test_a_replace_ref_does_not_change_the_release(host: Host) -> None:
    host.replace_ref("deploy/install.sh", b"# replaced\n")
    installer(host)
    assert (release_dir(host) / "deploy/install.sh").read_bytes() == git_show(host, "deploy/install.sh")


# --- the config check (D3, D5) ---

def test_config_check_runs_from_the_release_with_the_unit_locations(host: Host) -> None:
    installer(host)
    env = host.env_of("heterodyne")
    assert env["HETERODYNE_CONFIG_DIR"] == f"{host.real_home()}/.config/heterodyne"
    assert env["HETERODYNE_STATE_DIR"] == f"{host.real_home()}/.local/state/heterodyne"
    assert env["PATH"].startswith(f"{release_dir(host)}/.venv/bin:")


def test_a_dry_run_before_the_release_is_built_is_advisory(host: Host) -> None:
    out = installer(host, "--dry-run")
    assert summary(out)["config"] == "advisory (the release is not built yet)"
    assert not (host.shims / "env-heterodyne.log").exists()


# --- the real uv, offline (D2): no network, and skipped without uv ---

def real_uv() -> str | None:
    found = shutil.which("uv")
    return found


REAL_UV = real_uv()
OFFLINE = {"PATH": "/usr/bin:/bin", "UV_PYTHON_DOWNLOADS": "never", "UV_NO_CONFIG": "1",
           "UV_LINK_MODE": "copy"}


@pytest.mark.skipif(REAL_UV is None, reason="uv is not installed")
def test_real_uv_offline_against_an_empty_cache_fails(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        shutil.copy(REPO / name, tree / name)
    shutil.copytree(REPO / "src", tree / "src")
    (tmp_path / "cache").mkdir()
    env = OFFLINE | {"HOME": str(tmp_path), "UV_CACHE_DIR": str(tmp_path / "cache")}
    out = subprocess.run([str(REAL_UV), "sync", "--locked", "--offline", "--no-dev", "--python",
                          "/usr/bin/python3", "--project", str(tree)], env=env, capture_output=True,
                         text=True, timeout=300, check=False)
    assert out.returncode != 0
    assert not (tree / ".venv" / "bin" / "heterodyne").exists()


def wheel(d: Path, name: str) -> None:
    """A minimal pure-Python wheel, made by hand, so no build backend is needed offline."""
    files = {f"{name}/__init__.py": b"X = 1\n",
             f"{name}-0.dist-info/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: 0\n".encode(),
             f"{name}-0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: hand\nRoot-Is-Purelib: true\n"
                                          b"Tag: py3-none-any\n"}
    def digest(b: bytes) -> str:
        return base64.urlsafe_b64encode(hashlib.sha256(b).digest()).rstrip(b"=").decode()
    rows = [f"{n},sha256={digest(b)},{len(b)}" for n, b in files.items()]
    files[f"{name}-0.dist-info/RECORD"] = ("\n".join([*rows, f"{name}-0.dist-info/RECORD,,"]) + "\n").encode()
    with zipfile.ZipFile(d / f"{name}-0-py3-none-any.whl", "w") as z:
        for n, b in files.items():
            z.writestr(n, b)


def files_of(d: Path) -> dict[Path, tuple[int, int, int, bytes]]:
    return {p.relative_to(d): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns,
                               hashlib.sha256(p.read_bytes()).digest())
            for p in d.rglob("*") if p.is_file() and not p.is_symlink()}


@pytest.mark.skipif(REAL_UV is None, reason="uv is not installed")
def test_a_build_against_a_hardlink_copy_of_the_cache_leaves_the_original_unchanged(tmp_path: Path) -> None:
    """D2: uv replaces cache entries by rename, so a hardlink copy shields the real cache. The fixture cache
    holds a real archive entry (a hand-made wheel, from --find-links); the build against the copy reads it
    and adds a second package, so the copy is written to, and the original must not change."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel(wheels, "w1")
    wheel(wheels, "w2")
    proj = tmp_path / "proj"
    proj.mkdir()

    def sync(deps: str, cache: Path) -> None:
        (proj / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n'
                                             f'requires-python = ">=3.12"\ndependencies = [{deps}]\n')
        shutil.rmtree(proj / ".venv", ignore_errors=True)
        env = OFFLINE | {"HOME": str(tmp_path), "UV_CACHE_DIR": str(cache)}
        for cmd in (["lock"], ["sync", "--locked", "--python", "/usr/bin/python3"]):
            subprocess.run([str(REAL_UV), *cmd, "--offline", "--find-links", str(wheels), "--project",
                            str(proj)], env=env, check=True, capture_output=True, timeout=300)

    sync('"w1"', tmp_path / "orig")
    snap = files_of(tmp_path / "orig")
    assert any(p.parts[0] == "archive-v0" for p in snap)
    subprocess.run(["cp", "-al", "--", str(tmp_path / "orig"), str(tmp_path / "copy")], check=True,
                   timeout=120)
    assert files_of(tmp_path / "copy") == snap  # every file the same inode: hardlinks, not copies
    sync('"w1", "w2"', tmp_path / "copy")
    site = next((proj / ".venv").glob("lib/python3*/site-packages"))
    assert (site / "w1" / "__init__.py").is_file() and (site / "w2" / "__init__.py").is_file()
    after = files_of(tmp_path / "copy")
    w1 = [p for p in snap if p.parts[0] == "archive-v0" and any("w1" in q for q in p.parts)]
    assert w1 and all(after[p][0] == snap[p][0] for p in w1)  # read in place, not re-extracted
    assert files_of(tmp_path / "orig") == snap
    assert len(after) > len(snap)


# --- the services (D4, D6) ---

def user_calls(host: Host, *verbs: str) -> list[str]:
    return [c for c in host.calls() if c.startswith("user-systemctl ") and c.split()[1] in verbs]


def started(host: Host) -> list[list[str]]:
    return [c.split()[1:] for c in user_calls(host, "start", "restart")]


MARKER = "# Rendered by heterodyne's deploy/install.sh for release "


def test_a_fresh_install_writes_reloads_enables_and_starts_admind_then_wsd(host: Host) -> None:
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out)["services"] == "changed"
    assert started(host) == [["start", ADMIND], ["start", WSD]]
    calls = host.calls()
    assert calls.index("user-systemctl daemon-reload") < calls.index(f"user-systemctl start {ADMIND}")
    assert [c.split()[2] for c in user_calls(host, "enable")] == [ADMIND, WSD]
    for u in (ADMIND, WSD):
        assert host.unit_file(u).read_text().startswith(f"{MARKER}{host.commit};")
        assert host.record()["units"][u] == {"origin": "created", "state": "written"}
    assert host.record()["release"] == host.commit
    never_touched_the_manager(host)


def test_verity_runs_before_any_service_starts(host: Host) -> None:
    host.put("superblock", FEATURES_OFF)
    assert installer(host).returncode == 0
    calls = host.calls()
    assert calls.index("tune2fs -O verity /dev/fake0") < calls.index(f"user-systemctl start {ADMIND}")


def test_a_rerun_in_place_starts_nothing(host: Host) -> None:
    installer(host)
    host.clear_calls()
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out)["services"] == "ok"
    assert user_calls(host, "start", "restart", "daemon-reload", "enable", "reset-failed") == []


@pytest.mark.parametrize(("state", "verbs"), [("inactive", ["start"]), ("failed", ["reset-failed", "start"])])
def test_an_inactive_or_failed_service_is_started(host: Host, state: str, verbs: list[str]) -> None:
    installer(host)
    host.set_unit(WSD, active=state)
    host.clear_calls()
    assert installer(host).returncode == 0
    assert [c.split()[1] for c in user_calls(host, "start", "restart", "reset-failed")] == verbs


@pytest.mark.parametrize("drift", ["argv", "rev", "manager-state-dir"])
def test_a_running_process_that_differs_is_restarted(host: Host, drift: str) -> None:
    installer(host)
    host.drift_process(ADMIND, drift)
    host.clear_calls()
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert started(host) == [["restart", ADMIND]]


@pytest.mark.parametrize(("dropin", "prop"), [
    ("[Service]\nExecStart=\nExecStart=/usr/bin/python3 -m heterodyne.other run\n", "ExecStart"),
    ("[Service]\nEnvironment=PATH=/opt/bin\n", "Environment"),
])
def test_a_drop_in_that_changes_the_identity_fails_and_restarts_nothing(host: Host, dropin: str,
                                                                        prop: str) -> None:
    installer(host)
    host.drop_in(ADMIND, "10-x.conf", dropin)
    host.clear_calls()
    out = installer(host)
    assert out.returncode == 1 and summary(out)["services"].startswith("failed")
    assert prop in out.stderr and "drop-in 10-x.conf" in out.stderr
    assert started(host) == []


def test_a_drop_in_that_changes_nothing_compared_is_a_note(host: Host) -> None:
    installer(host)
    host.drop_in(ADMIND, "10-nice.conf", "[Service]\nNice=5\n")
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"note: {ADMIND} has drop-ins, which change nothing the installer compares: 10-nice.conf" \
        in out.stdout.splitlines()


def test_need_daemon_reload_left_after_the_reload_fails(host: Host) -> None:
    host.put("user-reload-no-effect", "")
    out = installer(host)
    assert out.returncode == 1
    assert "systemd still has an old copy of a platform unit after daemon-reload" in out.stderr
    assert "NeedDaemonReload" not in out.stderr
    assert started(host) == []


def test_a_wsd_that_is_not_ready_fails_with_the_journal(host: Host) -> None:
    host.put("wsctl-rc", "1\n")
    out = installer(host)
    assert out.returncode == 1 and f"{WSD} is not ready" in out.stderr
    assert f"journalctl --user -u {WSD} -n 20 --no-pager" in host.calls()


def test_a_readiness_failure_keeps_the_recorded_release_until_the_services_pass(host: Host) -> None:
    assert installer(host).returncode == 0
    old = host.commit
    host.new_commit()
    host.put("wsctl-rc", "1\n")
    out = installer(host)
    assert out.returncode == 1 and f"{WSD} is not ready" in out.stderr
    assert host.record()["release"] == old
    (host.shims / "wsctl-rc").unlink()
    assert installer(host).returncode == 0
    assert host.record()["release"] == host.commit


def test_an_admind_that_restarts_during_readiness_fails_the_recheck(host: Host) -> None:
    host.flap(ADMIND)
    out = installer(host)
    assert out.returncode == 1 and f"{ADMIND} restarted while it was checked" in out.stderr
    assert f"journalctl --user -u {ADMIND} -n 20 --no-pager" in host.calls()


def test_an_unreadable_proc_is_not_a_match_and_the_restart_is_rechecked(host: Host) -> None:
    installer(host)
    host.hide_proc(ADMIND)
    host.clear_calls()
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert started(host) == [["restart", ADMIND]]
    assert f"ok: {ADMIND} is up and runs the render" in out.stdout.splitlines()


@contextlib.contextmanager
def wsd_lock_held(host: Host) -> Iterator[None]:
    """A wsd's single-instance lock on the units' state directory, held as a wsd holds it."""
    lock = host.home() / ".local/state/heterodyne/wsd/wsd.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@pytest.mark.parametrize("flags", [(), ("--dry-run",), ("--fetch",)])
def test_a_wsd_outside_its_unit_refuses_a_fresh_install_before_any_change(host: Host,
                                                                         flags: tuple[str, ...]) -> None:
    with wsd_lock_held(host):
        out = installer(host, *flags)
    assert install_refusal(host, out) == "a wsd is running outside its unit: stop it first"
    assert not release_dir(host).exists() and started(host) == []


def test_a_wsd_outside_its_unit_refuses_a_rerun_before_any_change(host: Host) -> None:
    assert installer(host).returncode == 0
    host.set_unit(WSD, active="inactive")
    host.clear_calls()
    with wsd_lock_held(host):
        assert install_refusal(host, installer(host)) == "a wsd is running outside its unit: stop it first"
    assert started(host) == []


def test_the_units_own_wsd_holding_the_lock_is_not_outside(host: Host) -> None:
    assert installer(host).returncode == 0
    with wsd_lock_held(host):            # the fake manager says the unit is active
        out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_free_lock_goes_ahead_and_an_unreadable_one_refuses(host: Host) -> None:
    lock = host.home() / ".local/state/heterodyne/wsd/wsd.lock"
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"")
    assert installer(host, "--dry-run").returncode == 0
    lock.unlink()
    lock.symlink_to(host.home() / "elsewhere")
    assert install_refusal(host, installer(host, "--dry-run")) == \
        "whether a wsd runs outside its unit can't be told"


def test_no_wsd_skips_the_probe(host: Host) -> None:
    with wsd_lock_held(host):
        out = installer(host, "--no-wsd")
    assert out.returncode in (0, 3), out.stdout + out.stderr


def test_a_contaminated_manager_environment_changes_nothing_the_installer_checked(host: Host) -> None:
    dirty = {"BTQ_REPO": "/elsewhere/repo", "HETERODYNE_CONFIG_DIR": "/elsewhere/c",
             "HETERODYNE_STATE_DIR": "/elsewhere/s", "XDG_CONFIG_HOME": "/elsewhere/xc",
             "XDG_STATE_HOME": "/elsewhere/xs", "XDG_DATA_HOME": "/elsewhere/xd", "PATH": "/elsewhere/bin"}
    host.put("manager-env", "".join(f"{k}={v}\n" for k, v in dirty.items()))
    before = (host.shims / "manager-env").read_bytes()
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    for u in (ADMIND, WSD):
        env = host.process_env(u)
        assert env["HETERODYNE_CONFIG_DIR"] == f"{host.real_home()}/.config/heterodyne"
        assert env["HETERODYNE_STATE_DIR"] == f"{host.real_home()}/.local/state/heterodyne"
        assert env["PATH"].startswith(f"{release_dir(host)}/.venv/bin:")
        assert not {"BTQ_REPO", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"} & set(env)
    for name in ("heterodyne", "admind", "wsctl"):
        assert host.env_of(name)["HETERODYNE_STATE_DIR"] == f"{host.real_home()}/.local/state/heterodyne"
    assert (host.shims / "manager-env").read_bytes() == before
    never_touched_the_manager(host)


# --- adoption, and a failure then a rerun ---

def test_a_hand_made_unit_without_adopt_refuses_before_any_change(host: Host) -> None:
    host.hand_made_admind()
    assert install_refusal(host, installer(host)) == (
        f"{ADMIND} was not written by the installer: run with --adopt to replace it (the old file is kept)")
    assert user_calls(host, "daemon-reload", "enable", "start", "restart") == []


def test_adopt_backs_up_records_replaces_and_restarts_once(host: Host) -> None:
    old = host.hand_made_admind()
    out = installer(host, "--adopt")
    assert out.returncode == 0, out.stdout + out.stderr
    order = [c.split() for c in host.platform_calls()
             if c.split()[0] in ("backup", "record-set", "write") and ADMIND in c]
    assert [c[0] + (f" {c[4]}" if c[0] == "record-set" else "") for c in order] == [
        "backup", "record-set writing", "write", "record-set written"]
    [b] = host.backups()
    assert b.read_bytes() == old
    assert host.record()["units"][ADMIND]["origin"] == "adopted"
    assert started(host) == [["restart", ADMIND], ["start", WSD]]


@pytest.mark.parametrize(("fail_at", "rerun_args", "backups"), [
    (f"record-set --unit {ADMIND} --state writing", ("--adopt",), 2),
    (f"write --unit {ADMIND}", (), 1),
    (f"record-set --unit {ADMIND} --state written", (), 1),
], ids=["after-backup", "after-writing", "after-write"])
def test_a_failure_at_each_boundary_is_completed_by_a_rerun(
        host: Host, fail_at: str, rerun_args: tuple[str, ...], backups: int) -> None:
    host.hand_made_admind()
    host.put("fail-platform", fail_at + "\n")
    assert installer(host, "--adopt").returncode == 1
    host.put("fail-platform", "")
    out = installer(host, *rerun_args)
    assert out.returncode == 0, out.stdout + out.stderr
    assert len(host.backups()) == backups
    assert host.record()["units"][ADMIND]["state"] == "written"
    assert host.record()["units"][ADMIND]["origin"] == "adopted"


def test_a_hand_made_file_changed_while_writing_refuses(host: Host) -> None:
    host.hand_made_admind()
    host.put("fail-platform", f"write --unit {ADMIND}\n")
    installer(host, "--adopt")
    host.put("fail-platform", "")
    host.unit_file(ADMIND).write_text("[Unit]\nDescription=changed by hand\n")
    host.clear_calls()
    assert install_refusal(host, installer(host)) == f"{ADMIND} changed since its backup"


def test_an_upgrade_keeps_the_adoption(host: Host) -> None:
    host.hand_made_admind()
    assert installer(host, "--adopt").returncode == 0
    before = host.record()["units"][ADMIND]
    host.new_commit()
    host.clear_calls()
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert host.record()["units"][ADMIND] == before and host.record()["release"] == host.commit
    assert host.unit_file(ADMIND).read_text().startswith(f"{MARKER}{host.commit};")
    assert started(host) == [["restart", ADMIND], ["restart", WSD]]


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_a_symlink_or_a_directory_refuses_even_with_adopt(host: Host, kind: str) -> None:
    host.hand_made_admind(kind=kind)
    why = install_refusal(host, installer(host, "--adopt"))
    assert why == f"{ADMIND}: the unit file is a symlink or not a regular file"


def test_the_selftest_unit_is_never_touched(host: Host) -> None:
    host.selftest_unit()
    assert installer(host, "--adopt").returncode == 0
    assert not any("heterodyne-admind-selftest" in c for c in host.calls() + host.platform_calls())


# --- what is skipped ---

def test_admind_not_initialised_writes_its_unit_but_does_not_enable_or_start_it(host: Host) -> None:
    host.put("admind-initialised-rc", "1\n")
    out = installer(host)
    assert out.returncode == 3, out.stdout + out.stderr
    assert summary(out)["services"] == "skipped (admind init has not run)"
    assert host.unit_file(ADMIND).exists()
    assert not any(ADMIND in c for c in user_calls(host, "enable", "start", "restart"))
    assert started(host) == [["start", WSD]]


@pytest.mark.parametrize("rc", ["2\n", "78\n"])
def test_admind_initialised_that_cannot_tell_fails(host: Host, rc: str) -> None:
    host.put("admind-initialised-rc", rc)
    out = installer(host)
    assert out.returncode == 1
    assert summary(out)["services"] == f"failed (admind initialised can't tell (exit {rc.strip()}))"
    assert started(host) == []


def test_no_wsd_writes_no_wsd_unit(host: Host) -> None:
    out = installer(host, "--no-wsd")
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out)["services"] == "skipped (--no-wsd)"
    assert not host.unit_file(WSD).exists() and started(host) == [["start", ADMIND]]


def test_no_wsd_stops_and_disables_a_managed_wsd_and_keeps_its_file(host: Host) -> None:
    installer(host)
    host.clear_calls()
    out = installer(host, "--no-wsd")
    assert out.returncode == 0, out.stdout + out.stderr
    assert [c.split()[1:] for c in user_calls(host, "stop", "disable")] == [["stop", WSD], ["disable", WSD]]
    assert host.unit_file(WSD).exists()


def test_a_dry_run_changes_no_user_state(host: Host) -> None:
    host.hand_made_admind()
    snap = host.home_snapshot()
    out = installer(host, "--dry-run", "--adopt")
    assert out.returncode == 0, out.stdout + out.stderr
    assert host.home_snapshot() == snap
    changing = ("daemon-reload", "enable", "start", "restart", "reset-failed", "stop", "disable")
    assert user_calls(host, *changing) == []
    assert host.mutations() == [] and host.backups() == []
    assert f"would: back up {ADMIND}" in out.stdout.splitlines()


def test_a_dry_run_after_an_install_reports_a_drift_and_changes_nothing(host: Host) -> None:
    installer(host)
    host.drift_process(ADMIND, "argv")
    snap = host.home_snapshot()
    host.clear_calls()
    out = installer(host, "--dry-run")
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out)["services"] == "would change"
    assert f"would: restart {ADMIND}" in out.stdout.splitlines()
    assert host.home_snapshot() == snap and started(host) == []


# --- uninstall (D6) ---

# The root steps' commands, logged and doing nothing (pgrep: nothing runs as the store owner).
ROOT_TOOLS = {"rm": '[[ ${2:-} == */.config/systemd/user/* ]] && exec /usr/bin/rm "$@"\nexit 0\n',
              "rmdir": "", "userdel": "", "pgrep": "exit 1\n"}


def root_tools(host: Host) -> Path:
    tools = host.shims / "root-tools"
    if not tools.exists():
        tools.mkdir()
        for name, body in ROOT_TOOLS.items():
            (tools / name).write_text(SHIM_HEAD.replace("@SHIMS@", shlex.quote(str(host.shims))) + body)
            (tools / name).chmod(0o755)
    return tools


def uninstaller(host: Host, *args: str) -> subprocess.CompletedProcess[str]:
    tools = root_tools(host)
    argv = ["bash", str(host.repo / "deploy" / "uninstall.sh"), "--prefix", str(host.root),
            "--service-user", ME, *args]
    return subprocess.run(argv, capture_output=True, text=True, timeout=300, check=False,
                          env={**os.environ, "PATH": f"{tools}:{host_path(host)}", "HZ_INSTALL_TEST": "1"})


def root_steps(host: Host) -> list[str]:
    home = str(host.home())
    return [c for c in host.calls() if c.startswith(("systemctl disable --now heterodyne-store-helper",
                                                     "rm ", "rmdir ", "userdel ")) and home not in c]


def test_managed_units_go_wsd_first_then_one_reload_then_the_root_steps(host: Host) -> None:
    assert installer(host).returncode == 0
    host.clear_calls()
    out = uninstaller(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert [c.split()[1:] for c in user_calls(host, "stop")] == [["stop", WSD], ["stop", ADMIND]]
    assert [c.split()[1:] for c in user_calls(host, "disable")] == [["disable", WSD], ["disable", ADMIND]]
    calls = host.calls()
    assert user_calls(host, "daemon-reload") == ["user-systemctl daemon-reload"]
    assert calls.index("user-systemctl daemon-reload") < calls.index(root_steps(host)[0])
    assert root_steps(host)[-1] == "userdel heterodyne-store"
    assert not host.unit_file(ADMIND).exists() and not host.unit_file(WSD).exists()
    assert host.record()["units"] == {}
    assert release_dir(host).exists() and f"release: {host.commit} (kept)" in out.stdout.splitlines()
    never_touched_the_manager(host)


def test_a_second_uninstall_finds_nothing_to_remove(host: Host) -> None:
    assert installer(host).returncode == 0
    assert uninstaller(host).returncode == 0
    host.clear_calls()
    out = uninstaller(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert user_calls(host, "stop", "disable", "daemon-reload") == []


@pytest.mark.parametrize("case", ["marker-no-entry", "entry-no-marker", "writing", "malformed-record",
                                  "symlink", "bad-backup", "loaded-no-file"])
def test_ambiguous_state_refuses_with_nothing_touched(host: Host, case: str) -> None:
    host.make_ambiguous(case)
    before = host.home_snapshot()
    host.clear_calls()
    out = uninstaller(host, *(["--restore-adopted"] if case == "bad-backup" else []))
    assert out.returncode == 1, out.stdout + out.stderr
    assert out.stderr.splitlines()[-1].startswith("refused: ")
    if case != "malformed-record":      # that one names no release, so nothing is classified
        assert out.stderr.startswith(f"{ADMIND}: ambiguous: ")
    assert user_calls(host, "stop", "disable", "daemon-reload") == [] and root_steps(host) == []
    assert host.home_snapshot() == before


def test_a_not_managed_hand_made_unit_is_left_alone(host: Host) -> None:
    old = host.hand_made_admind()
    out = uninstaller(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert host.unit_file(ADMIND).read_bytes() == old
    assert f"{ADMIND}: not managed by the installer; left in place" in out.stdout.splitlines()
    assert user_calls(host, "stop", "disable") == []


def test_restore_adopted_puts_the_backup_back_and_does_not_start_it(host: Host) -> None:
    old = host.hand_made_admind()
    assert installer(host, "--adopt").returncode == 0
    host.clear_calls()
    out = uninstaller(host, "--restore-adopted")
    assert out.returncode == 0, out.stdout + out.stderr
    assert host.unit_file(ADMIND).read_bytes() == old and not host.unit_file(WSD).exists()
    assert not any(ADMIND in c for c in user_calls(host, "enable", "start", "restart"))


def test_without_restore_adopted_an_adopted_unit_is_removed(host: Host) -> None:
    host.hand_made_admind()
    assert installer(host, "--adopt").returncode == 0
    out = uninstaller(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert not host.unit_file(ADMIND).exists() and len(host.backups()) == 1


def test_a_failing_stop_stops_the_uninstall_before_the_root_steps(host: Host) -> None:
    assert installer(host).returncode == 0
    host.put("fail", f"user-systemctl stop {WSD}\n")
    host.clear_calls()
    out = uninstaller(host)
    assert out.returncode == 1 and root_steps(host) == []
    assert host.unit_file(ADMIND).exists() and host.unit_file(WSD).exists()


SHOW = f"user-systemctl show {WSD} {ADMIND} -p Id -p LoadState -p ActiveState"


def test_nothing_installed_asks_systemd_then_goes_on_to_the_root_steps(host: Host) -> None:
    out = uninstaller(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "platform: nothing installed" in out.stdout.splitlines()
    assert [c for c in host.calls() if c.startswith("user-systemctl ")] == [SHOW]
    assert root_steps(host)[0] == "systemctl disable --now heterodyne-store-helper.socket"


def test_no_user_manager_at_all_is_nothing_loaded(host: Host) -> None:
    shutil.rmtree(host.root / f"run/user/{os.getuid()}")
    out = uninstaller(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert [c for c in host.calls() if c.startswith("user-systemctl ")] == []
    assert root_steps(host)[-1] == "userdel heterodyne-store"


def no_record_case(host: Host, case: str) -> None:
    f = host.unit_file(ADMIND)
    if case in ("marker-no-record", "loaded-no-file-no-record", "active-no-file-no-record"):
        assert installer(host).returncode == 0
        (host.install_dir() / "record.json").unlink()
        if case != "marker-no-record":
            f.unlink()                   # the fake manager still has it loaded, and running
        if case == "loaded-no-file-no-record":
            host.set_unit(ADMIND, active="inactive")
    elif case in ("dangling-symlink", "dangling-symlink-no-manager"):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.symlink_to(host.home() / "gone.service")
        if case == "dangling-symlink-no-manager":
            shutil.rmtree(host.root / f"run/user/{os.getuid()}")
    elif case == "symlink":
        (host.home() / "a.service").write_text(HAND_MADE)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.symlink_to(host.home() / "a.service")
    elif case == "manager-unreadable":
        host.put("fail", SHOW + "\n")
    elif case == "runtime-not-a-directory":          # ENOTDIR is not ENOENT
        shutil.rmtree(host.root / "run/user")
        (host.root / "run/user").write_text("")
    elif case == "record-unreadable":               # the record's lookup fails with ENOTDIR
        shutil.rmtree(host.install_dir(), ignore_errors=True)
        host.install_dir().parent.mkdir(parents=True, exist_ok=True)
        host.install_dir().write_text("")
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case", ["marker-no-record", "loaded-no-file-no-record", "active-no-file-no-record",
                                  "dangling-symlink", "dangling-symlink-no-manager", "symlink",
                                  "manager-unreadable", "runtime-not-a-directory", "record-unreadable"])
def test_without_a_record_unclear_evidence_refuses_before_any_root_step(host: Host, case: str) -> None:
    no_record_case(host, case)
    before = host.home_snapshot()
    host.clear_calls()
    out = uninstaller(host)
    assert out.returncode == 1, out.stdout + out.stderr
    assert out.stderr.splitlines()[-1].startswith("refused: ")
    assert out.stderr.splitlines()[-1].endswith("; nothing was changed")
    if case == "runtime-not-a-directory":
        assert out.stderr.splitlines()[-1] == ("refused: the service user's systemd state can't be read; "
                                               "nothing was changed")
        assert not [c for c in host.calls() if c.startswith("user-systemctl ")]
    elif case == "record-unreadable":
        assert out.stderr.splitlines()[-1] == "refused: the install record can't be read; nothing was changed"
    elif case != "manager-unreadable":
        assert f"{ADMIND}: ambiguous: " in out.stderr
    assert user_calls(host, "stop", "disable", "daemon-reload") == [] and root_steps(host) == []
    assert host.home_snapshot() == before


def test_a_hand_made_unit_with_no_record_is_left_alone_even_while_it_runs(host: Host) -> None:
    old = host.hand_made_admind()
    host.set_unit(ADMIND, active="active")
    out = uninstaller(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert host.unit_file(ADMIND).read_bytes() == old
    assert f"{ADMIND}: not managed by the installer; left in place" in out.stdout.splitlines()
    assert user_calls(host, "stop", "disable") == [] and root_steps(host)[-1] == "userdel heterodyne-store"


def test_the_user_stages_removal_goes_through_the_shims(host: Host) -> None:
    assert installer(host).returncode == 0
    host.put("fail", f"rm -- {host.unit_file(WSD)}\n")
    host.clear_calls()
    out = uninstaller(host)
    assert out.returncode == 1 and out.stderr.splitlines()[-1] == f"failed: {WSD} can't be removed"
    assert root_steps(host) == []


def test_the_uninstall_prefix_is_for_the_tests_only(host: Host) -> None:
    out = subprocess.run(["bash", str(host.repo / "deploy" / "uninstall.sh"), "--prefix", str(host.root)],
                         capture_output=True, text=True, timeout=60, check=False,
                         env={k: v for k, v in os.environ.items() if k != "HZ_INSTALL_TEST"})
    assert (out.returncode, out.stderr) == (1, "refused: --prefix is for the tests only\n")


# --- the docs, and the pinned blocks with the new flags (D7) ---

def test_the_pinned_install_runs_with_the_new_flags_appended(host: Host, tmp_path: Path) -> None:
    old = host.hand_made_admind()
    out = pinned("install-pinned", host, tmp_path, {
        "<checkout>": CHECKOUT, "<commit>": host.commit,
        "<install.sh sum>": sum_of(host.sums, "deploy/install.sh"),
        "<archive sum>": sum_of(host.sums, "archive.tar"),
        "--service-user <service user>": f"--service-user {ME} --prefix {host.root}"},
        {"PATH": host_path(host), "HZ_INSTALL_TEST": "1"}, extra=("--adopt", "--no-wsd"))
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out)["services"] == "skipped (--no-wsd)"
    assert [b.read_bytes() for b in host.backups()] == [old] and not host.unit_file(WSD).exists()


def test_the_pinned_uninstall_runs_with_restore_adopted_appended(host: Host, tmp_path: Path) -> None:
    old = host.hand_made_admind()
    assert installer(host, "--adopt").returncode == 0
    tools = root_tools(host)
    out = pinned("uninstall-pinned", host, tmp_path, {
        "<uninstall.sh sum>": sum_of(host.sums, "tree/deploy/uninstall.sh")},
        {"PATH": f"{tools}:{host_path(host)}", "HZ_INSTALL_TEST": "1"},
        extra=("--prefix", str(host.root), "--service-user", ME, "--restore-adopted"))
    assert out.returncode == 0, out.stdout + out.stderr
    assert host.unit_file(ADMIND).read_bytes() == old


def test_the_pinned_blocks_are_unchanged() -> None:
    assert readme_block("install-pinned").splitlines()[-1] == (
        'bash "$b" --checkout <checkout> --commit <commit> --expect-sha256 <archive sum> '
        "--service-user <service user>'")
    assert readme_block("uninstall-pinned").splitlines()[-1] == "bash \"$b\"'"


def test_the_readme_names_every_flag_the_prerequisites_and_the_limits() -> None:
    text = README.read_text()
    for flag in ("--adopt", "--no-wsd", "--fetch", "--restore-adopted", "--dry-run", "--no-verity"):
        assert f"`{flag}`" in text, flag
    for words in ("loginctl enable-linger", "hatchling", "`release`", "`services`", "admind init",
                  "skipped (admind init has not run)", "~/.local/share/heterodyne/releases/<commit>",
                  "record.json", "wsd.lock", "/run/user/<uid>", "dry run leaves the home as it found it",
                  "can't tell"):
        assert words in text, words
    assert "uv run --offline --project" not in text      # the run checkout is gone (D2)


def test_the_install_header_names_the_steps_and_the_flags() -> None:
    header = "".join(line for line in (REPO / "deploy" / "install.sh").read_text().splitlines(keepends=True)
                     if line.startswith("#"))
    for words in ("--adopt", "--no-wsd", "--fetch", "release", "services", "admind init"):
        assert words in header, words


def test_the_docs_say_the_installer_writes_the_units() -> None:
    assert "deploy/install.sh" in (REPO / "docs" / "admind.md").read_text()
    wsd = (REPO / "docs" / "wsd.md").read_text()
    assert "heterodyne-wsd.service" in wsd and "TimeoutStopSec=150" in wsd


def tree_of(d: Path) -> dict[str, tuple[int, bytes]]:
    return {str(p.relative_to(d)): (p.lstat().st_mode,
                                    p.read_bytes() if p.is_file() and not p.is_symlink() else b"")
            for p in sorted(d.rglob("*"))}


def test_a_dry_run_leaves_a_fresh_home_as_it_found_it(host: Host) -> None:
    before = tree_of(host.home())
    out = installer(host, "--dry-run")
    assert out.returncode in (0, 3), out.stdout + out.stderr
    assert tree_of(host.home()) == before


def test_a_fresh_install_makes_the_unit_directory_the_service_users_and_private(host: Host) -> None:
    assert installer(host).returncode == 0
    d = host.unit_file(ADMIND).parent
    st = d.lstat()
    assert stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) & 0o022 == 0


@pytest.mark.parametrize(("where", "why"), [
    ("units-writable", "the user unit directory can be written by others"),
    ("units-symlink", "the user unit directory is not the service user's directory"),
    ("install-symlink", "the install directory is not the service user's directory"),
    ("scratch-symlink", "the scratch directory is not the service user's directory"),
    ("scratch-ancestor-symlink", "the scratch directory is not the service user's directory"),
    ("scratch-readable", "the scratch directory can be read by others"),
    ("units-ancestor-symlink", "the user unit directory is not the service user's directory"),
    ("units-ancestor-writable", "the user unit directory is under a directory others can write"),
    ("install-ancestor-symlink", "the install directory is not the service user's directory"),
    ("install-ancestor-writable", "the install directory is under a directory others can write"),
])
def test_a_directory_that_is_not_the_service_users_refuses_before_any_change(host: Host, where: str,
                                                                             why: str) -> None:
    units = host.home() / ".config/systemd/user"
    install = host.home() / ".local/share/heterodyne/install"
    scratch = host.home() / ".cache/heterodyne"
    elsewhere = host.home() / "elsewhere"
    elsewhere.mkdir()
    target = {"units-writable": units, "units-symlink": units, "install-symlink": install,
              "scratch-symlink": scratch, "scratch-ancestor-symlink": scratch.parent,
              "scratch-readable": scratch, "units-ancestor-symlink": host.home() / ".config",
              "units-ancestor-writable": host.home() / ".config",
              "install-ancestor-symlink": host.home() / ".local",
              "install-ancestor-writable": host.home() / ".local"}[where]
    target.parent.mkdir(parents=True, exist_ok=True)
    if where == "scratch-readable" or where.endswith("-writable"):
        target.mkdir(exist_ok=True)
        target.chmod(0o755 if where == "scratch-readable" else 0o777)
    else:
        target.symlink_to(elsewhere)
    out = installer(host)
    assert install_refusal(host, out) == why
    assert not release_dir(host).exists() and started(host) == [] and list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("branch", ["fetch", "completed"])
@pytest.mark.parametrize(("where", "why"), [
    ("symlink", "the scratch directory is not the service user's directory"),
    ("readable", "the scratch directory can be read by others"),
    ("ancestor-writable", "the scratch directory is under a directory others can write"),
])
def test_a_scratch_refusal_comes_before_any_root_change_with_no_scratch_build(host: Host, branch: str,
                                                                             where: str, why: str) -> None:
    """--fetch and a completed release skip the scratch build, but the services step still renders into
    the scratch parent: its check is the preflight's, in every branch."""
    if branch == "completed":
        assert installer(host).returncode == 0
        shutil.rmtree(host.home() / ".cache/heterodyne")
        host.clear_calls()
    scratch = host.home() / ".cache/heterodyne"
    scratch.parent.mkdir(exist_ok=True)
    elsewhere = host.home() / "elsewhere"
    elsewhere.mkdir()
    if where == "symlink":
        scratch.symlink_to(elsewhere)
    elif where == "readable":
        scratch.mkdir(mode=0o755)
        scratch.chmod(0o755)
    else:
        scratch.parent.chmod(0o777)
    out = installer(host, *(["--fetch"] if branch == "fetch" else []))
    assert install_refusal(host, out) == why
    assert started(host) == [] and list(elsewhere.iterdir()) == []


def failing_mktemp(host: Host, where: str) -> None:
    """A logged mktemp shim, which fails when its directory is under `where`."""
    tool = host.shims / "mktemp"
    head = SHIM_HEAD.replace("@SHIMS@", shlex.quote(str(host.shims)))
    tool.write_text(head + 'exec /usr/bin/mktemp "$@"\n')
    tool.chmod(0o755)
    host.put("fail", f"mktemp -d {host.home()}/.cache/heterodyne/{where}.\n")


def test_a_scratch_directory_that_cannot_be_made_leaves_the_home_as_it_found_it(host: Host) -> None:
    before = tree_of(host.home())
    failing_mktemp(host, "preflight")
    out = installer(host, "--dry-run")
    assert out.returncode == 1, out.stdout + out.stderr
    assert out.stderr.splitlines()[-1] == f"{REFUSED}the offline build check has no scratch directory"
    assert host.mutations() == [] and host.stages() == []
    assert tree_of(host.home()) == before


def test_a_render_directory_that_cannot_be_made_in_a_dry_run_leaves_the_home_as_found(host: Host) -> None:
    assert installer(host).returncode == 0
    shutil.rmtree(host.home() / ".cache/heterodyne")
    before = tree_of(host.home())
    failing_mktemp(host, "render")
    out = installer(host, "--dry-run")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "no render directory" in out.stderr
    assert tree_of(host.home()) == before


def test_a_dry_run_with_a_completed_release_leaves_the_home_as_it_found_it(host: Host) -> None:
    """The services step renders into the scratch parent to compare, and must remove what it made."""
    assert installer(host).returncode == 0
    shutil.rmtree(host.home() / ".cache/heterodyne")
    before = tree_of(host.home())
    out = installer(host, "--dry-run")
    assert out.returncode in (0, 3), out.stdout + out.stderr
    assert tree_of(host.home()) == before
