"""Plan 4c Task 12: the store helper's units, `setup-store.sh`, `manifest.sh` and the host installer
(deploy/). Offline and unprivileged: `systemd-analyze verify` where it exists, a parse of the units' keys
against the defaults the harness reads, the units' command line serving one request, `bash -n`, the setup
script's dry run, which runs every check and changes nothing (`--prefix` stands in for `/`), and the whole
installer run against a fake root with every host command a shim that logs it. Nothing here runs as root or
changes the host."""

import configparser
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from deploy_host import (
    ALL_IN_PLACE,
    CHECKOUT,
    FEATURES_OFF,
    HOST_DIRS,
    INSTALL,
    MANIFEST,
    ME,
    MY_GROUP,
    REFUSED,
    REPO,
    SCRIPT,
    SERVICE_UNIT,
    SOCKET_UNIT,
    UNINSTALL,
    Host,
    defaults,
    hook,
    host_fixture,  # noqa: F401  (the `host` fixture)
    host_path,
    install_refusal,
    installer,
    pinned,
    script_constants,
    state,
    sum_of,
    summary,
)
from harness_env import git

from heterodyne.config import paths as config_paths
from heterodyne.harness import paths as harness_paths
from heterodyne.harness import settings
from heterodyne.harness.resources import store_core as sc


def unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True, empty_lines_in_values=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]   # systemd keys are case-sensitive
    parser.read_string(path.read_text())
    return parser


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


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    """A staged tree as the README's step 3 extracts it (only the helper is read from it)."""
    root = tmp_path / "staged"
    helper = root / script_constants()["PACKAGED"]
    helper.parent.mkdir(parents=True)
    shutil.copy(harness_paths.packaged_helper(), helper)
    return root


# `stat -c '%u %a' -- PATH` as it reads on a host: under $FAKE_PREFIX everything is root's unless $FAKE_UIDS
# (`PATH=UID;...`) names another owner, since no unprivileged test can make root's directories. Any other
# use of stat fails, so the script can't drift away from what this stands in for.
FAKE_STAT = r"""#!/bin/bash
[[ $# == 4 && $1 == -c && $2 == '%u %a' && $3 == -- ]] || { echo "fake stat: unexpected use" >&2; exit 99; }
out=$(/usr/bin/stat -c '%u %a' -- "$4") || exit
path=${4%/}
[[ $path == "$FAKE_PREFIX" || $path == "$FAKE_PREFIX"/* ]] || { echo "$out"; exit 0; }
uid=0
IFS=';' read -r -a pairs <<<"${FAKE_UIDS:-}"
for pair in "${pairs[@]}"; do [[ ${pair%=*} == "$path" ]] && uid=${pair##*=}; done
echo "$uid ${out#* }"
"""


@pytest.fixture
def prefix(tmp_path: Path) -> Iterator[Path]:
    """`/` for the dry run: the parents of every path the script writes, none writable by this user."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "stat").write_text(FAKE_STAT)
    (fakebin / "stat").chmod(0o755)
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


def host_env(prefix: Path, uids: dict[str, int] | None = None) -> dict[str, str]:
    return {**os.environ, "PATH": f"{prefix.parent / 'fakebin'}:{os.environ['PATH']}",
            "FAKE_PREFIX": str(prefix),
            "FAKE_UIDS": ";".join(f"{prefix}/{p}={u}" for p, u in (uids or {}).items())}


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=120,
                          check=False, env=env)


def dry_run(staged: Path, prefix: Path, *, user: str = ME,
            uids: dict[str, int] | None = None) -> subprocess.CompletedProcess[str]:
    return run("--dry-run", "--prefix", str(prefix), "--staged", str(staged), "--service-user", user,
               env=host_env(prefix, uids))


def test_the_dry_run_lists_every_change_and_makes_none(staged: Path, prefix: Path) -> None:
    before = snapshot(prefix)
    out = dry_run(staged, prefix)
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
    installs = [line.split() for line in would[5:]]
    assert [w[:7] for w in installs] == [["install", "-o", "root", "-g", "root", "-m", "0644"]] * 3
    assert [w[8] for w in installs] == [f"{prefix}{c[k]}" for k in ("HELPER_COPY", "CONFIG", "DROPIN")]
    assert installs[0][7] == f"{staged}/{c['PACKAGED']}"       # the helper as staged, nothing in between
    assert not any(os.path.lexists(w[7]) for w in installs[1:])  # the generated files are removed on exit
    lines = out.stdout.splitlines()
    assert f'  ({c["CONFIG"]}: {{"service_uid": {os.getuid()}, "store": "{c["STORE"]}"}})' in lines
    assert f"  ({c['DROPIN']}: [Socket] SocketGroup={MY_GROUP})" in lines
    assert lines[-1] == "done"


def refusal(out: subprocess.CompletedProcess[str]) -> str:
    """The one refusal, made before any change was listed."""
    assert out.returncode == 1
    assert "would: " not in out.stdout and "change: " not in out.stdout
    [line] = out.stderr.splitlines()
    assert line.startswith(REFUSED)
    return line.removeprefix(REFUSED)


OTHER_UID = 12345      # neither root, nor the store owner, nor the service user


@pytest.mark.parametrize(("make", "owner", "check"), [
    ("var", None, "a path component is writable by the service user: /var"),
    ("usr/local/libexec", "me", "a path component is owned by the service user: /usr/local/libexec"),
    ("var/lib/heterodyne/harnesses", "me",
     "a path component is owned by the service user: /var/lib/heterodyne/harnesses"),
    ("var/lib/heterodyne", OTHER_UID, "/var/lib/heterodyne exists with another owner"),
    ("usr/local/libexec/heterodyne", OTHER_UID, "/usr/local/libexec/heterodyne exists with another owner"),
    ("etc/heterodyne:file", None, "a path component is not a directory: /etc/heterodyne"),
    ("var/lib/heterodyne:link", None, "a path component is a symlink: /var/lib/heterodyne"),
])
def test_the_dry_run_refuses_a_path_that_is_not_safe_and_changes_nothing(
        staged: Path, prefix: Path, tmp_path: Path, make: str, owner: int | str | None, check: str) -> None:
    unseal_tree(prefix)
    name, _, kind = make.partition(":")
    target = prefix / name
    if kind == "file":
        target.write_bytes(b"")
    elif kind == "link":
        (tmp_path / "elsewhere").mkdir()
        target.symlink_to(tmp_path / "elsewhere")
    else:
        target.mkdir(parents=True, exist_ok=True)
    seal(prefix)
    if name == "var":
        (prefix / "var").chmod(0o755)
    uids = {} if owner is None else {name: os.getuid() if owner == "me" else int(owner)}
    before = snapshot(prefix)
    assert refusal(dry_run(staged, prefix, uids=uids)) == check
    assert snapshot(prefix) == before


@pytest.mark.parametrize(("spoil", "check"), [
    ("absent", "the staged tree has no helper"),
    ("link", "the staged tree has no helper"),
    ("garbage", "the staged helper does not parse"),
])
def test_the_dry_run_refuses_a_staged_tree_without_a_good_helper(staged: Path, prefix: Path, tmp_path: Path,
                                                                 spoil: str, check: str) -> None:
    helper = staged / script_constants()["PACKAGED"]
    helper.unlink()
    if spoil == "link":
        (tmp_path / "real.py").write_bytes(harness_paths.packaged_helper().read_bytes())
        helper.symlink_to(tmp_path / "real.py")
    elif spoil == "garbage":
        helper.write_bytes(b"def (:\n")
    assert refusal(dry_run(staged, prefix)) == check


def test_the_dry_run_refuses_a_staged_tree_that_is_not_an_absolute_directory(staged: Path,
                                                                            prefix: Path) -> None:
    out = run("--dry-run", "--prefix", str(prefix), "--staged", "staged", "--service-user", ME,
              env=host_env(prefix))
    assert refusal(out) == "the staged tree is not an absolute directory"


@pytest.mark.parametrize(("user", "check"), [
    ("Not A User", "the service user is not a user name"),
    ("no-such-user-t12a", "the service user does not exist"),
    ("root", "the service user is root"),
    ("heterodyne-store", "the service user is the store owner"),
])
def test_the_dry_run_refuses_bad_arguments(staged: Path, prefix: Path, user: str, check: str) -> None:
    if user == "heterodyne-store":
        try:
            pwd.getpwnam(user)
        except KeyError:
            check = "the service user does not exist"
    assert refusal(dry_run(staged, prefix, user=user)) == check


def test_a_run_without_a_staged_tree_is_a_usage_error(prefix: Path) -> None:
    out = run("--dry-run", "--prefix", str(prefix), "--service-user", ME, env=host_env(prefix))
    assert out.returncode == 2
    assert out.stderr.startswith("usage: ")


def other_user() -> str | None:
    for name in ("nobody", "daemon"):
        if [p for p in pwd.getpwall() if p.pw_name == name and p.pw_uid not in (0, os.getuid())]:
            return name
    return None


@pytest.mark.skipif(other_user() is None, reason="no other non-root user on this host")
def test_a_dry_run_by_another_user_refuses_rather_than_speak_for_the_service_user(
        staged: Path, prefix: Path) -> None:
    user = other_user()
    assert user is not None
    assert refusal(dry_run(staged, prefix, user=user)) == "a dry run must be run by the service user"


def test_a_prefix_is_for_a_dry_run_only(staged: Path, prefix: Path) -> None:
    out = run("--prefix", str(prefix), "--staged", str(staged), "--service-user", ME, env=host_env(prefix))
    assert refusal(out) == "--prefix is for a dry run only"


@pytest.mark.skipif(os.geteuid() == 0, reason="as root this would change the host")
def test_a_run_that_changes_the_host_must_be_root(staged: Path) -> None:
    out = run("--staged", str(staged), "--service-user", ME)
    assert refusal(out) == "a run that changes the host must be root (or use --dry-run)"


# --- deploy/install.sh: the whole host setup, against a fake root with every host command a shim ---


@pytest.mark.parametrize("script", [SCRIPT, MANIFEST, INSTALL, UNINSTALL], ids=lambda p: p.name)
def test_the_scripts_parse(script: Path) -> None:
    out = subprocess.run(["bash", "-n", str(script)], capture_output=True, timeout=60, check=False)
    assert (out.returncode, out.stderr) == (0, b"")


def literal_paths(script: Path) -> list[str]:
    [m] = re.findall(r"^readonly PATHS=\((.*)\)$", script.read_text(), re.MULTILINE)
    return m.split()


def test_the_installer_and_the_manifest_archive_one_literal_path_set() -> None:
    assert literal_paths(INSTALL) == literal_paths(MANIFEST) == ["deploy", script_constants()["PACKAGED"]]
    for script in (INSTALL, MANIFEST):
        assert re.search(r"^\s*(source|\.)\s", script.read_text(), re.MULTILINE) is None


def test_a_pinned_run_installs_the_commits_bytes_and_reports_the_manifests_sums(host: Host) -> None:
    out = installer(host)
    assert (out.returncode, out.stderr) == (0, ""), out.stdout
    assert summary(out) == ALL_IN_PLACE
    lines = out.stdout.splitlines()
    assert f"commit: {host.commit}" in lines
    assert f"archive sha256: {sum_of(host.sums, 'archive.tar')}" in lines
    for u in (SOCKET_UNIT, SERVICE_UNIT):
        assert host.installed(u) == u.read_bytes()
    assert host.stages() == []


def test_the_manifest_names_the_installer_the_commit_ships(host: Host) -> None:
    assert host.sums[0].endswith("  archive.tar")
    assert sum_of(host.sums, "deploy/install.sh") == hashlib.sha256(INSTALL.read_bytes()).hexdigest()
    assert sum_of(host.sums, "tree/deploy/uninstall.sh") == hashlib.sha256(UNINSTALL.read_bytes()).hexdigest()


def test_the_service_user_defaults_to_the_sudo_user(host: Host) -> None:
    out = installer(host, user=None, env={"SUDO_USER": ME})
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out) == ALL_IN_PLACE


# --- the operator's pinned commands (README) ---


def pinned_install(host: Host, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return pinned("install-pinned", host, tmp_path, {
        "<checkout>": CHECKOUT, "<commit>": host.commit,
        "<install.sh sum>": sum_of(host.sums, "deploy/install.sh"),
        "<archive sum>": sum_of(host.sums, "archive.tar"),
        "--service-user <service user>": f"--service-user {ME} --prefix {host.root}"},
        {"PATH": host_path(host), "HZ_INSTALL_TEST": "1"})


def test_the_pinned_install_runs_the_approved_installer(host: Host, tmp_path: Path) -> None:
    out = pinned_install(host, tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out) == ALL_IN_PLACE
    assert list(tmp_path.glob("boot.*")) == []


def test_the_pinned_install_runs_nothing_from_a_changed_installer(host: Host, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    (host.repo / "deploy" / "install.sh").write_text(f"#!/bin/bash\ntouch {marker}\n")
    out = pinned_install(host, tmp_path)
    assert out.returncode != 0
    assert not marker.exists()
    assert host.calls() == []
    assert list(tmp_path.glob("boot.*")) == []


@pytest.mark.parametrize("changed", [False, True])
def test_the_pinned_uninstall_runs_only_the_approved_uninstaller(host: Host, tmp_path: Path,
                                                                 changed: bool) -> None:
    stubs, log = rollback_stubs(tmp_path, NO_PROCESS)
    marker = tmp_path / "ran"
    if changed:
        (host.repo / "deploy" / "uninstall.sh").write_text(f"#!/bin/bash\ntouch {marker}\n")
    out = pinned("uninstall-pinned", host, tmp_path, {
        "<uninstall.sh sum>": sum_of(host.sums, "tree/deploy/uninstall.sh")},
        {"PATH": f"{stubs}:{os.environ['PATH']}", "STUB_LOG": str(log), "STUB_FAIL_ON": NO_PROCESS})
    boot = f"rm -f {tmp_path}/boot."    # the block's own trap removing the copy
    calls = [c for c in log.read_text().splitlines() if not c.startswith(boot)]
    if changed:
        assert (out.returncode != 0, calls, marker.exists()) == (True, [], False)
    else:
        assert (out.returncode, out.stderr) == (0, "")
        assert calls[0] == "systemctl disable --now heterodyne-store-helper.socket"


# --- refusals: every check runs before the first change ---

def test_a_run_that_is_not_root_refuses(host: Host) -> None:
    (host.shims / "id").unlink()
    assert install_refusal(host, installer(host)) == "the installer must run as root"


@pytest.mark.parametrize(("user", "check"), [
    (None, "no service user: give --service-user or run it through sudo"),
    ("Not A User", "the service user is not a user name"),
    ("no-such-user-p4c", "the service user does not exist"),
    ("root", "the service user is root"),
])
def test_a_run_without_a_good_service_user_refuses(host: Host, user: str | None, check: str) -> None:
    assert install_refusal(host, installer(host, user=user)) == check


def test_an_unpinned_run_on_a_checkout_the_service_user_can_change_refuses(host: Host) -> None:
    assert install_refusal(host, installer(host, pinned=False)) == (
        "the service user can change the checkout: run the pinned form (--commit and --expect-sha256)")


def test_an_archive_the_approval_does_not_name_refuses_before_extracting(host: Host) -> None:
    out = installer(host, "--commit", host.commit, "--expect-sha256", "0" * 64, pinned=False)
    assert install_refusal(host, out) == "the archive does not match --expect-sha256"


def test_a_commit_that_resolves_to_another_refuses(host: Host) -> None:
    git(host.repo, "tag", "-a", "-m", "a tag", "t1")
    tag = git(host.repo, "rev-parse", "t1").decode().strip()
    assert tag != host.commit
    out = installer(host, "--commit", tag, "--expect-sha256", sum_of(host.sums, "archive.tar"), pinned=False)
    assert install_refusal(host, out) == "the commit resolves to another commit"


@pytest.mark.parametrize(("text", "check"), [
    ("[sandbox]\nfallback = true\n", None),
    ("[harnesses] # host\nowner = \"x\"\n  [harnesses.claude]\n  fallback = true\n", None),
    ("[harnesses]\n# fallback = true\n", None),
    ("[harnesses]\nfallback = true # on\n", "the host config sets harnesses.fallback"),
    ("  [harnesses] # host\nfallback = false\n", "the host config sets harnesses.fallback"),
    ("harnesses.fallback = true\n", "the host config sets harnesses.fallback"),
    ("harnesses = 1\n", "the host config's harnesses is not a table"),
    ("[harnesses\n", "the host config is not valid TOML"),
])
def test_the_host_config_must_not_set_the_fallback_and_is_never_edited(host: Host, text: str,
                                                                       check: str | None) -> None:
    host.config.parent.mkdir(parents=True)
    host.config.write_text(text)
    out = installer(host)
    if check is None:
        assert out.returncode == 0, out.stdout + out.stderr
    else:
        assert install_refusal(host, out) == check
    assert host.config.read_text() == text
    assert sorted(p.name for p in host.config.parent.iterdir()) == ["config.toml"]


def test_a_missing_host_config_reads_as_empty(host: Host) -> None:
    assert not host.config.exists()
    assert installer(host).returncode == 0


def test_the_installer_checks_the_config_heterodyne_reads(tmp_path: Path) -> None:
    c = dict(re.findall(r"^readonly (\w+)=(\S+)$", INSTALL.read_text(), re.MULTILINE))
    assert config_paths.config_dir({"HOME": str(tmp_path)}) / "config.toml" == tmp_path / c["HOST_CONFIG"]


def test_a_run_checkout_that_does_not_run_refuses(host: Host) -> None:
    host.put("fail", "uv run\n")
    assert install_refusal(host, installer(host)) == (
        "the run checkout does not run heterodyne (run uv sync there first)")


def test_the_prefix_is_for_the_tests_only(host: Host) -> None:
    out = installer(host, env={"HZ_INSTALL_TEST": ""})
    assert install_refusal(host, out) == "--prefix is for the tests only"


def test_a_staged_run_must_be_the_stages_own_installer(host: Host) -> None:
    stage = host.root / "run" / "stage"
    (stage / "tree").mkdir(parents=True)
    stage.chmod(0o700)
    out = installer(host, "--staged", str(stage), "--archive-sha256", "0" * 64)
    assert install_refusal(host, out) == "the staged installer is not the stage's"


# --- the installed bytes are the resolved commit's ---


def test_a_replace_ref_is_ignored(host: Host) -> None:
    unit_file = host.repo / "deploy" / "store-helper" / SOCKET_UNIT.name
    unit_file.write_bytes(unit_file.read_bytes() + b"# not the commit\n")
    git(host.repo, "commit", "-q", "-a", "-m", "another unit")
    git(host.repo, "replace", host.commit, git(host.repo, "rev-parse", "HEAD").decode().strip())
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert host.installed(SOCKET_UNIT) == SOCKET_UNIT.read_bytes()


def trust(host: Host) -> None:
    """A checkout the service user can't change: its path to install.sh and .git read-only."""
    (host.repo / "deploy" / "install.sh").chmod(0o444)
    for d in (host.repo / "deploy", host.repo / ".git", host.repo, host.root / "src"):
        d.chmod(0o555)


def test_a_trusted_checkout_runs_unpinned_and_a_commit_switch_after_resolution_moves_nothing(
        host: Host) -> None:
    trust(host)
    commit_it = (f"git -C {shlex.quote(str(host.repo))} -c user.name=t -c user.email=t@example.org "
                 "commit -q -a -m switch")
    hook(host, "rev-parse", commit_it)
    out = installer(host, pinned=False)
    assert out.returncode == 0, out.stdout + out.stderr
    assert git(host.repo, "rev-parse", "HEAD").decode().strip() != host.commit     # the switch happened
    assert f"commit: {host.commit}" in out.stdout.splitlines()
    assert host.installed(SOCKET_UNIT) == SOCKET_UNIT.read_bytes()


def test_a_work_tree_edit_after_staging_moves_nothing(host: Host) -> None:
    hook(host, "archive", "printf 'echo WORK-TREE-COPY\\n' >>" +
         shlex.quote(str(host.repo / "deploy" / "store-helper" / "setup-store.sh")))
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "WORK-TREE-COPY" not in out.stdout
    assert host.installed(SOCKET_UNIT) == SOCKET_UNIT.read_bytes()


def test_the_stage_is_removed_after_a_failure(host: Host) -> None:
    host.put("fail", "systemctl daemon-reload\n")
    out = installer(host)
    assert out.returncode == 1
    assert summary(out)["units"] == "failed (daemon-reload failed)"
    assert summary(out)["verity"] == "not run"
    assert host.stages() == []


# --- the socket: reconciled from its live state ---

def socket_actions(host: Host) -> list[str]:
    return [c for c in host.calls() if c.startswith(("systemctl start", "systemctl restart"))]


def test_a_socket_in_place_is_left_alone(host: Host) -> None:
    out = installer(host)
    assert out.returncode == 0
    assert "systemctl daemon-reload" in host.calls()
    assert socket_actions(host) == []


def test_a_reload_that_clears_need_daemon_reload_needs_no_restart(host: Host) -> None:
    host.put("need-reload", "yes\n")
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert socket_actions(host) == []


def test_units_already_in_place_are_not_installed_again(host: Host) -> None:
    units = host.root / "etc/systemd/system"
    units.chmod(0o755)
    for u in (SOCKET_UNIT, SERVICE_UNIT):
        shutil.copy(u, units / u.name)
        (units / u.name).chmod(0o644)
    units.chmod(0o555)
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out)["units"] == "ok"
    assert [c for c in host.calls() if c.startswith("install ")] == []


@pytest.mark.parametrize("drift", [
    f"socket {defaults()['owner']} {MY_GROUP} 666\n",
    f"socket {defaults()['owner']} root 660\n",
    f"socket root {MY_GROUP} 660\n",
    f"regular file {defaults()['owner']} {MY_GROUP} 660\n",
])
def test_a_drifted_socket_is_restarted_and_then_in_place(host: Host, drift: str) -> None:
    host.put("sockstat", drift)
    host.put("sockstat.after", state()["sockstat"])
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert socket_actions(host) == ["systemctl restart heterodyne-store-helper.socket"]


def test_an_inactive_socket_is_started(host: Host) -> None:
    host.put("active", "inactive\n")
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert socket_actions(host) == ["systemctl start heterodyne-store-helper.socket"]


@pytest.mark.parametrize("fail_on", ["systemctl daemon-reload", "systemctl restart"])
def test_a_failed_reload_or_restart_is_repaired_by_the_next_run(host: Host, fail_on: str) -> None:
    host.put("sockstat", f"socket {defaults()['owner']} root 660\n")
    host.put("sockstat.after", state()["sockstat"])
    host.put("fail", f"{fail_on}\n")
    assert installer(host).returncode == 1
    host.put("fail", "")
    host.put("calls.log", "")
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "systemctl daemon-reload" in host.calls()
    assert socket_actions(host) == ["systemctl restart heterodyne-store-helper.socket"]


def test_a_run_that_installed_one_unit_and_failed_is_completed_by_the_next(host: Host) -> None:
    host.put("install-fail", f"{SERVICE_UNIT.name}\n")
    out = installer(host)
    assert out.returncode == 1
    assert summary(out)["units"] == "failed (a unit can't be installed)"
    assert host.installed(SOCKET_UNIT) == SOCKET_UNIT.read_bytes()
    assert not (host.root / "etc/systemd/system" / SERVICE_UNIT.name).exists()
    (host.shims / "install-fail").unlink()
    host.put("calls.log", "")
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert [c.rsplit("/", 1)[-1] for c in host.calls() if c.startswith("install ")] == [SERVICE_UNIT.name]
    assert f"ok: /etc/systemd/system/{SOCKET_UNIT.name}" in out.stdout.splitlines()
    assert [host.installed(u) for u in (SOCKET_UNIT, SERVICE_UNIT)] == [SOCKET_UNIT.read_bytes(),
                                                                       SERVICE_UNIT.read_bytes()]


def test_a_socket_still_drifted_after_the_restart_fails(host: Host) -> None:
    host.put("sockstat", f"socket {defaults()['owner']} root 660\n")
    out = installer(host)
    assert out.returncode == 1
    assert summary(out)["units"].startswith("failed (the socket does not match its units")


def test_a_reload_that_leaves_need_daemon_reload_fails(host: Host) -> None:
    host.put("need-reload", "yes\n")
    host.put("reload-no-effect", "")
    out = installer(host)
    assert out.returncode == 1
    assert summary(out)["units"] == "failed (systemd still has an old copy of a unit after daemon-reload)"


# --- verity ---

@pytest.mark.parametrize(("name", "text", "why"), [
    ("mount", "xfs /dev/fake0\n", "not ext4"),
    ("", "", "the kernel's ext4 has no verity"),
    ("e2fsprogs", "tune2fs 1.44.5 (15-Dec-2018)\n", "e2fsprogs older than 1.45"),
    ("superblock", "Block size:               1024\nFilesystem features:      has_journal\n",
     "the block size is not the page size"),
])
def test_a_host_without_verity_installs_the_rest_and_exits_3(host: Host, name: str, text: str,
                                                             why: str) -> None:
    if name:
        host.put(name, text)
    else:
        (host.root / "sys/fs/ext4/features/verity").unlink()
    out = installer(host)
    assert out.returncode == 3, out.stdout + out.stderr
    assert f"skipped: verity ({why})" in out.stdout.splitlines()
    assert summary(out) == {**ALL_IN_PLACE, "verity": f"skipped ({why})"}
    assert not [c for c in host.calls() if c.startswith("tune2fs -O")]


def test_verity_is_turned_on_when_off(host: Host) -> None:
    host.put("superblock", FEATURES_OFF)
    out = installer(host)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "tune2fs -O verity /dev/fake0" in host.calls()
    assert summary(out)["verity"] == "changed"
    assert any(line.startswith("warning: ") for line in out.stdout.splitlines())


@pytest.mark.parametrize(("setup", "why"), [
    ({"fail": "tune2fs -O\n"}, "tune2fs -O verity failed"),
    ({"tune2fs-no-effect": ""}, "verity is not on after tune2fs"),
])
def test_verity_that_breaks_fails_the_install(host: Host, setup: dict[str, str], why: str) -> None:
    host.put("superblock", FEATURES_OFF)
    for name, text in setup.items():
        host.put(name, text)
    out = installer(host)
    assert out.returncode == 1, out.stdout + out.stderr
    assert summary(out) == {**ALL_IN_PLACE, "verity": f"failed ({why})"}


@pytest.mark.parametrize(("setup", "why"), [
    ({"e2fsprogs": "no version here\n"}, "the e2fsprogs version can't be read"),
    ({"superblock": None}, "the superblock can't be read"),
    ({"superblock": "Block size:               4096\n"}, "the superblock can't be read"),
    ({"mount": "ext4 rootfs\n"}, "the store's device can't be read"),
    ({"fail": "findmnt\n"}, "the store's filesystem can't be found"),
])
def test_verity_the_preflight_cannot_read_refuses_before_any_change(host: Host, setup: dict[str, str | None],
                                                                     why: str) -> None:
    for name, text in setup.items():
        if text is None:
            (host.shims / name).unlink()
        else:
            host.put(name, text)
    out = installer(host)
    assert install_refusal(host, out) == why
    changes = ("install ", "systemctl daemon-reload", "systemctl enable")
    assert not [c for c in host.calls() if c.startswith(changes)]


def test_a_filesystem_that_changes_after_the_preflight_refuses_before_any_change(host: Host) -> None:
    host.put("superblock", FEATURES_OFF)
    superblock = shlex.quote(str(host.shims / "superblock"))
    hook(host, "archive", f"sed -i 's/^Block size:.*/Block size: 1024/' {superblock}")
    out = installer(host)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "refused: the store's filesystem changed since the preflight" in out.stderr.splitlines()
    assert host.mutations() == []
    assert set(summary(out).values()) == {"not run"}
    assert host.stages() == []


def test_no_verity_skips_the_step_on_purpose(host: Host) -> None:
    host.put("superblock", FEATURES_OFF)
    out = installer(host, "--no-verity")
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out)["verity"] == "skipped (--no-verity)"
    assert not [c for c in host.calls() if c.startswith(("tune2fs", "findmnt"))]


# --- step 6 and the dry run ---

@pytest.mark.parametrize("problem", ["HELPER_DRIFT", "STORE_PATH_WRITABLE"])
def test_a_store_problem_in_config_check_fails_the_install(host: Host, problem: str) -> None:
    host.put("config-check", state()["config-check"] + f"harnesses problem: {problem}\n")
    out = installer(host)
    assert out.returncode == 1
    assert summary(out)["config"] == "failed (config check reports a store problem)"


FAILED_CHECKS = [
    ({"config-check-rc": "1\n"}, "config check failed"),
    ({"config-check": state()["config-check"] + "harnesses problem: HELPER_DRIFT\n"},
     "config check reports a store problem"),
]


@pytest.mark.parametrize(("setup", "why"), FAILED_CHECKS)
def test_a_failed_config_check_stops_before_verity_is_turned_on(host: Host, setup: dict[str, str],
                                                                why: str) -> None:
    host.put("superblock", FEATURES_OFF)
    for name, text in setup.items():
        host.put(name, text)
    out = installer(host)
    assert out.returncode == 1, out.stdout + out.stderr
    assert summary(out) == {**ALL_IN_PLACE, "config": f"failed ({why})", "verity": "not run"}
    assert not [c for c in host.calls() if c.startswith("tune2fs -O")]


@pytest.mark.parametrize(("setup", "why"), FAILED_CHECKS)
def test_a_failed_config_check_is_advice_in_a_dry_run(host: Host, setup: dict[str, str], why: str) -> None:
    for name, text in setup.items():
        host.put(name, text)
    out = installer(host, "--dry-run")
    assert out.returncode == 0, out.stdout + out.stderr
    assert summary(out)["config"] == f"advisory ({why}; the changes above may fix it)"
    assert host.mutations() == []


def test_a_dry_run_makes_no_change(host: Host) -> None:
    host.put("superblock", FEATURES_OFF)
    host.put("active", "inactive\n")
    out = installer(host, "--dry-run")
    assert out.returncode == 0, out.stdout + out.stderr
    assert host.mutations() == []
    assert summary(out) == {"store": "would change", "units": "would change", "config": "ok",
                            "verity": "would change"}
    assert "would: systemctl start heterodyne-store-helper.socket" in out.stdout.splitlines()
    assert "would: tune2fs -O verity /dev/fake0" in out.stdout.splitlines()
    assert host.stages() == []


# --- deploy/uninstall.sh ---

STUB = r"""#!/bin/bash
name=$(basename "$0")
echo "$name $*" >>"$STUB_LOG"
IFS=';' read -r -a fail <<<"${STUB_FAIL_ON:-}"
for f in "${fail[@]}"; do [[ -n $f && "$name $*" == "$f"* ]] && exit 1; done
exit 0
"""
ROLLBACK_TOOLS = ("systemctl", "pgrep", "rm", "rmdir", "userdel")
NO_PROCESS = "pgrep "      # pgrep exits 1: nothing runs as the store owner


def rollback_stubs(tmp_path: Path, fail_on: str) -> tuple[Path, Path]:
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    for tool in ROLLBACK_TOOLS:
        (stubs / tool).write_text(STUB)
        (stubs / tool).chmod(0o755)
    log = tmp_path / "calls.log"
    log.write_text("")
    return stubs, log


def rollback(tmp_path: Path, fail_on: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """uninstall.sh with every host command a stand-in that logs its call and fails as told."""
    stubs, log = rollback_stubs(tmp_path, fail_on)
    out = subprocess.run(["bash", str(UNINSTALL)], capture_output=True, text=True, timeout=60, check=False,
                         env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "STUB_LOG": str(log),
                              "STUB_FAIL_ON": fail_on})
    return out, log.read_text().splitlines()


def test_the_rollback_stops_every_helper_then_removes_and_ends_with_the_owner(tmp_path: Path) -> None:
    out, calls = rollback(tmp_path, NO_PROCESS)
    assert (out.returncode, out.stderr) == (0, "")
    assert calls[:4] == ["systemctl disable --now heterodyne-store-helper.socket",
                         "systemctl stop heterodyne-store-helper@*.service",
                         "systemctl list-units --plain --no-legend --state=active,activating,deactivating "
                         "heterodyne-store-helper@*",
                         "pgrep -u heterodyne-store"]
    assert calls[-2:] == ["rmdir /var/lib/heterodyne/harnesses /var/lib/heterodyne",
                          "userdel heterodyne-store"]
    assert not [c for c in calls if c.startswith("rm -r") or c.startswith("rm -f")]


@pytest.mark.parametrize(("fail_on", "refused"), [
    ("", "refused: a process still runs as heterodyne-store"),
    (f"{NO_PROCESS};systemctl stop", ""),
    (f"{NO_PROCESS};systemctl disable", ""),
])
def test_the_rollback_removes_nothing_while_a_helper_may_run(tmp_path: Path, fail_on: str,
                                                             refused: str) -> None:
    out, calls = rollback(tmp_path, fail_on)
    assert out.returncode != 0
    assert refused in out.stderr
    assert not [c for c in calls if c.split()[0] in ("rm", "rmdir", "userdel")]


def test_the_rollback_keeps_a_store_that_is_not_empty_and_its_owner(tmp_path: Path) -> None:
    out, calls = rollback(tmp_path, f"{NO_PROCESS};rmdir /var/lib/heterodyne/harnesses")
    assert out.returncode != 0
    assert calls[-1] == "rmdir /var/lib/heterodyne/harnesses /var/lib/heterodyne"
    assert "userdel heterodyne-store" not in calls
