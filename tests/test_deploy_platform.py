"""The platform install (design btq-v7sgt) against the fake host: the preflight, the release, the services
and uninstall. Offline and unprivileged; every host command is a shim that logs it (tests/deploy_host.py)."""

import base64
import hashlib
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from deploy_host import (
    REPO,
    Host,
    git_show,
    hook,
    host_fixture,  # noqa: F401  (the `host` fixture)
    install_refusal,
    installer,
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
    assert list(cache.iterdir()) == []
    assert not release_dir(host).exists()


def test_the_real_cache_is_unchanged_by_the_scratch_build(host: Host) -> None:
    before = host.uvcache_snapshot()
    installer(host, "--dry-run")
    assert host.uvcache_snapshot() == before and len(before) == 3


def test_a_failed_scratch_build_is_removed_and_its_output_masked(host: Host) -> None:
    host.put("uv-miss", "")
    out = installer(host)
    assert install_refusal(host, out) == "the uv cache lacks a package this release needs: run with --fetch"
    assert list((host.home() / ".cache/heterodyne").iterdir()) == []
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
    assert list((host.home() / ".cache/heterodyne").iterdir()) == []


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


def files_of(d: Path) -> dict[Path, tuple[int, int, int]]:
    return {p.relative_to(d): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
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
    sync('"w1", "w2"', tmp_path / "copy")
    assert (proj / ".venv").is_dir()
    assert files_of(tmp_path / "orig") == snap
    assert len(files_of(tmp_path / "copy")) > len(snap)
