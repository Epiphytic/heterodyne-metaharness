"""Plan 4c Task 12a: the store helper's units and `setup-store.sh` (deploy/store-helper/). Offline and
unprivileged: `systemd-analyze verify` where it exists, a parse of the units' keys against the defaults the
harness reads, the units' command line serving one request, `bash -n`, and the script's dry run, which runs
every check and changes nothing (`--prefix` stands in for `/`). Nothing here runs as root or changes the
host."""

import configparser
import grp
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest
from harness_env import git

from heterodyne.harness import paths as harness_paths
from heterodyne.harness import settings
from heterodyne.harness.resources import store_core as sc

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy" / "store-helper"
SOCKET_UNIT = DEPLOY / "heterodyne-store-helper.socket"
SERVICE_UNIT = DEPLOY / "heterodyne-store-helper@.service"
SCRIPT = DEPLOY / "setup-store.sh"
ME = pwd.getpwuid(os.getuid()).pw_name
MY_GROUP = grp.getgrgid(os.getgid()).gr_name


def defaults() -> dict[str, object]:
    text = (resources.files("heterodyne.defaults") / "defaults.toml").read_text()
    return tomllib.loads(text)["harnesses"]


def unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True, empty_lines_in_values=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]   # systemd keys are case-sensitive
    parser.read_string(path.read_text())
    return parser


def script_constants() -> dict[str, str]:
    return dict(re.findall(r"^readonly (\w+)=(\S+)$", SCRIPT.read_text(), re.MULTILINE))


# --- the units ---

@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed")
def test_the_units_pass_systemd_analyze_verify_with_nothing_to_say(tmp_path: Path) -> None:
    for f in (SOCKET_UNIT, SERVICE_UNIT):
        shutil.copy(f, tmp_path / f.name)
    out = subprocess.run(["systemd-analyze", "verify", "--man=no", str(tmp_path / SOCKET_UNIT.name),
                          str(tmp_path / SERVICE_UNIT.name)], capture_output=True, timeout=120, check=False)
    assert (out.returncode, out.stdout + out.stderr) == (0, b"")


def test_the_socket_unit_is_the_plans_and_listens_where_the_harness_connects() -> None:
    u = unit(SOCKET_UNIT)
    assert u.sections() == ["Unit", "Socket", "Install"]
    assert dict(u["Socket"]) == {
        "ListenStream": str(defaults()["helper_socket"]),
        "SocketUser": str(defaults()["owner"]),
        "SocketMode": "0660",
        "DirectoryMode": "0755",
        "Accept": "yes",
        "MaxConnections": "4",
    }
    assert dict(u["Install"]) == {"WantedBy": "sockets.target"}


def test_the_service_unit_is_the_plans_and_runs_the_installed_copy_as_the_store_owner() -> None:
    u = unit(SERVICE_UNIT)
    c = script_constants()
    owner = str(defaults()["owner"])
    assert u.sections() == ["Unit", "Service"]
    service = dict(u["Service"])
    exec_start = shlex.split(service.pop("ExecStart"))
    assert service == {
        "User": owner,
        "Group": owner,
        "StandardInput": "socket",
        "StandardOutput": "journal",
        "NoNewPrivileges": "yes",
        "PrivateNetwork": "yes",
        "PrivateTmp": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "ReadWritePaths": str(settings.STORE),
        "RestrictAddressFamilies": "AF_UNIX",
        "CapabilityBoundingSet": "",
        "SystemCallFilter": "@system-service",
        "UMask": "0022",
        "TimeoutSec": "600",
    }
    assert exec_start == [c["PYTHON"], "-I", str(defaults()["helper_copy"]), "--config", str(sc.CONFIG)]


def test_the_script_writes_where_the_units_and_the_harness_look() -> None:
    c = script_constants()
    assert c["STORE_USER"] == defaults()["owner"]
    assert c["STORE"] == str(settings.STORE)
    assert c["HELPER_COPY"] == defaults()["helper_copy"]
    assert c["CONFIG"] == str(sc.CONFIG)
    assert Path(c["DROPIN"]).parent == Path(c["DROPIN_DIR"])
    assert Path(c["DROPIN_DIR"]).name == f"{SOCKET_UNIT.name}.d"
    assert REPO / c["PACKAGED"] == harness_paths.packaged_helper()
    assert REPO / c["UNITS"] == DEPLOY


def exec_start_argv(config: Path) -> list[str]:
    """ExecStart's argv, with this interpreter and the packaged helper in place of the installed copy and
    `config` in place of the installed config."""
    exec_start = shlex.split(dict(unit(SERVICE_UNIT)["Service"])["ExecStart"])
    argv = [sys.executable, *exec_start[1:]]
    argv[argv.index(str(defaults()["helper_copy"]))] = str(harness_paths.packaged_helper())
    argv[argv.index(str(sc.CONFIG))] = str(config)
    return argv


def one_connection(argv: list[str], request: bytes) -> tuple[int, bytes, bytes]:
    """`argv` serving one connection on stdin, as `Accept=yes` hands it over: (exit status, reply, stderr)."""
    client, server = socket.socketpair()
    try:
        proc = subprocess.Popen(argv, stdin=server.fileno(), stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        server.close()
        client.sendall(request)
        data = b""
        while chunk := client.recv(1 << 16):
            data += chunk
        _, err = proc.communicate(timeout=120)
    finally:
        client.close()
        server.close()
    return proc.returncode, data, err


def test_the_units_command_line_serves_one_request(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    config = tmp_path / "store-helper.json"
    config.write_text(f'{{"service_uid": {os.getuid()}, "store": "{store}"}}\n')
    status, reply, err = one_connection(exec_start_argv(config), sc.encode_request("list", store, {}))
    assert (status, err) == (0, b"")
    assert json.loads(reply)["ok"] is True


def test_the_units_command_line_without_its_config_refuses_and_says_which(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    status, reply, err = one_connection(exec_start_argv(tmp_path / "absent.json"),
                                        sc.encode_request("list", store, {}))
    assert (status, reply, err) == (sc.CONFIG_EXIT, b"", sc.BAD_CONFIG.encode() + b"\n")


# --- setup-store.sh ---

def test_the_script_parses() -> None:
    out = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, timeout=60, check=False)
    assert (out.returncode, out.stderr) == (0, b"")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A checkout with the helper and the units at its one commit, as the script reads them."""
    root = tmp_path / "repo"
    c = script_constants()
    (root / c["PACKAGED"]).parent.mkdir(parents=True)
    shutil.copy(harness_paths.packaged_helper(), root / c["PACKAGED"])
    shutil.copytree(DEPLOY, root / c["UNITS"])
    subprocess.run(["git", "init", "-q", str(root)], env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                          "GIT_CONFIG_GLOBAL": os.devnull,
                                                          "GIT_CONFIG_NOSYSTEM": "1"},
                   check=True, capture_output=True, timeout=60)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root, git(root, "rev-parse", "HEAD").decode().strip()


HOST_DIRS = ("var/lib", "usr/local/libexec", "etc/systemd/system")


@pytest.fixture
def prefix(tmp_path: Path) -> Iterator[Path]:
    """`/` for the dry run: the parents of every path the script writes, none writable by this user."""
    root = tmp_path / "root"
    for d in HOST_DIRS:
        (root / d).mkdir(parents=True)
    seal(root)
    yield root
    unseal_tree(root)


def seal(root: Path) -> None:
    for d in sorted([root, *(p for p in root.rglob("*") if p.is_dir() and not p.is_symlink())], reverse=True):
        d.chmod(0o555)


def unseal_tree(root: Path) -> None:
    for d in [root, *(p for p in root.rglob("*") if p.is_dir() and not p.is_symlink())]:
        d.chmod(0o755)


def snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    return {str(p.relative_to(root)): (p.lstat().st_mode, p.read_bytes() if p.is_file() else None)
            for p in [root, *root.rglob("*")]}


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=120,
                          check=False)


def dry_run(repo: tuple[Path, str], prefix: Path, *extra: str, user: str = ME,
            commit: str | None = None) -> subprocess.CompletedProcess[str]:
    return run("--dry-run", "--prefix", str(prefix), "--repo", str(repo[0]), "--commit",
               commit or repo[1], "--service-user", user, *extra)


def test_the_dry_run_lists_every_change_and_makes_none(repo: tuple[Path, str], prefix: Path) -> None:
    before = snapshot(prefix)
    out = dry_run(repo, prefix)
    assert (out.returncode, out.stderr) == (0, "")
    assert snapshot(prefix) == before
    c = script_constants()
    would = [line.removeprefix("would: ") for line in out.stdout.splitlines() if line.startswith("would: ")]
    try:
        pwd.getpwnam(c["STORE_USER"])
    except KeyError:
        assert would.pop(0) == (f"useradd --system --no-create-home --shell /usr/sbin/nologin --user-group "
                                f"{c['STORE_USER']}")
    else:
        assert f"ok: the user {c['STORE_USER']}" in out.stdout.splitlines()
    dirs = [(c["STATE"], "root"), (c["STORE"], c["STORE_USER"]), (c["LIBEXEC"], "root"),
            (c["CONFIG_DIR"], "root"), (c["DROPIN_DIR"], "root")]
    assert would[:5] == [f"install -d -o {o} -g {o} -m 0755 {prefix}{d}" for d, o in dirs]
    installs = would[5:]
    assert len(installs) == 3
    for line, path in zip(installs, (c["HELPER_COPY"], c["CONFIG"], c["DROPIN"]), strict=True):
        words = line.split()
        assert words[:6] == ["install", "-o", "root", "-g", "root", "-m"] and words[6] == "0644"
        assert words[8] == f"{prefix}{path}"
        assert not os.path.lexists(words[7])        # the staged sources are removed on exit
    lines = out.stdout.splitlines()
    assert f'  ({c["CONFIG"]}: {{"service_uid": {os.getuid()}, "store": "{c["STORE"]}"}})' in lines
    assert f"  ({c['DROPIN']}: [Socket] SocketGroup={MY_GROUP})" in lines
    assert lines[-1] == "done"


REFUSED = "refused: "


def refusal(out: subprocess.CompletedProcess[str]) -> str:
    """The one refusal, made before any change was listed."""
    assert out.returncode == 1
    assert "would: " not in out.stdout and "change: " not in out.stdout
    [line] = out.stderr.splitlines()
    assert line.startswith(REFUSED)
    return line.removeprefix(REFUSED)


@pytest.mark.parametrize(("make", "check"), [
    ("var", "a path component is writable by the service user: /var"),
    ("var/lib/heterodyne", "/var/lib/heterodyne exists with another owner"),
    ("usr/local/libexec/heterodyne", "/usr/local/libexec/heterodyne exists with another owner"),
    ("etc/heterodyne:file", "a path component is not a directory: /etc/heterodyne"),
    ("var/lib/heterodyne:link", "a path component is a symlink: /var/lib/heterodyne"),
])
def test_the_dry_run_refuses_a_path_that_is_not_safe_and_changes_nothing(
        repo: tuple[Path, str], prefix: Path, tmp_path: Path, make: str, check: str) -> None:
    unseal_tree(prefix)
    name, _, kind = make.partition(":")
    target = prefix / name
    if kind == "file":
        target.write_bytes(b"")
    elif kind == "link":
        (tmp_path / "elsewhere").mkdir()
        target.symlink_to(tmp_path / "elsewhere")
    elif not target.exists():
        target.mkdir()
    seal(prefix)
    if name == "var":
        (prefix / "var").chmod(0o755)
    before = snapshot(prefix)
    assert refusal(dry_run(repo, prefix)) == check
    assert snapshot(prefix) == before


def test_the_dry_run_refuses_a_checkout_that_is_not_the_commit(repo: tuple[Path, str], prefix: Path) -> None:
    root, first = repo
    assert refusal(dry_run(repo, prefix, commit="0" * 40)) == "the commit is not in the checkout"
    (root / "README").write_bytes(b"x\n")
    git(root, "add", "README")
    git(root, "commit", "-q", "-m", "second")
    assert refusal(dry_run(repo, prefix, commit=first)) == (
        "the checkout is not at the commit (the units are installed from it)")
    second = git(root, "rev-parse", "HEAD").decode().strip()
    unit_file = root / script_constants()["UNITS"] / SOCKET_UNIT.name
    unit_file.write_text(unit_file.read_text() + "# changed\n")
    assert refusal(dry_run(repo, prefix, commit=second)) == (
        f"the checkout's {script_constants()['UNITS']} differs from the commit")


def test_the_dry_run_refuses_a_commit_without_the_helper(repo: tuple[Path, str], prefix: Path) -> None:
    root, _ = repo
    git(root, "rm", "-q", script_constants()["PACKAGED"])
    git(root, "commit", "-q", "-m", "no helper")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    assert refusal(dry_run(repo, prefix, commit=head)) == "the commit has no helper"


def other_user() -> str | None:
    for name in ("nobody", "daemon"):
        with_name = [p for p in pwd.getpwall() if p.pw_name == name and p.pw_uid not in (0, os.getuid())]
        if with_name:
            return name
    return None


@pytest.mark.parametrize(("args", "check"), [
    (["--commit", "abc"], "the commit is not a full commit ID"),
    (["--service-user", "Not A User"], "the service user is not a user name"),
    (["--service-user", "no-such-user-t12a"], "the service user does not exist"),
    (["--service-user", "root"], "the service user is root"),
    (["--service-user", "heterodyne-store"], "the service user is the store owner"),
])
def test_the_dry_run_refuses_bad_arguments(repo: tuple[Path, str], prefix: Path, args: list[str],
                                           check: str) -> None:
    given = {"--commit": repo[1], "--service-user": ME} | dict(zip(args[::2], args[1::2], strict=True))
    if given["--service-user"] == "heterodyne-store":
        try:
            pwd.getpwnam("heterodyne-store")
        except KeyError:
            check = "the service user does not exist"
    out = run("--dry-run", "--prefix", str(prefix), "--repo", str(repo[0]), "--commit", given["--commit"],
              "--service-user", given["--service-user"])
    assert refusal(out) == check


@pytest.mark.skipif(other_user() is None, reason="no other non-root user on this host")
def test_a_dry_run_by_another_user_refuses_rather_than_speak_for_the_service_user(
        repo: tuple[Path, str], prefix: Path) -> None:
    user = other_user()
    assert user is not None
    assert refusal(dry_run(repo, prefix, user=user)) == "a dry run must be run by the service user"


def test_a_prefix_is_for_a_dry_run_only(repo: tuple[Path, str], prefix: Path) -> None:
    out = run("--prefix", str(prefix), "--repo", str(repo[0]), "--commit", repo[1], "--service-user", ME)
    assert refusal(out) == "--prefix is for a dry run only"


@pytest.mark.skipif(os.geteuid() == 0, reason="as root this would change the host")
def test_a_run_that_changes_the_host_must_be_root(repo: tuple[Path, str]) -> None:
    out = run("--repo", str(repo[0]), "--commit", repo[1], "--service-user", ME)
    assert refusal(out) == "a run that changes the host must be root (or use --dry-run)"
