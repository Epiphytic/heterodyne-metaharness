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
from harness_env import GIT_ENV, git

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


# --- setup-store.sh, manifest.sh and the README's root commands ---

MANIFEST = DEPLOY / "manifest.sh"
README = DEPLOY / "README.md"
STAGE = "/run/heterodyne-store-setup"     # the README's root-only stage


@pytest.mark.parametrize("script", [SCRIPT, MANIFEST], ids=lambda p: p.name)
def test_the_scripts_parse(script: Path) -> None:
    out = subprocess.run(["bash", "-n", str(script)], capture_output=True, timeout=60, check=False)
    assert (out.returncode, out.stderr) == (0, b"")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A checkout with the helper and deploy/store-helper at its one commit."""
    root = tmp_path / "repo"
    c = script_constants()
    (root / c["PACKAGED"]).parent.mkdir(parents=True)
    shutil.copy(harness_paths.packaged_helper(), root / c["PACKAGED"])
    shutil.copytree(DEPLOY, root / "deploy" / "store-helper")
    subprocess.run(["git", "init", "-q", str(root)], env=GIT_ENV, check=True, capture_output=True, timeout=60)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root, git(root, "rev-parse", "HEAD").decode().strip()


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    """A staged tree as the README's step 3 extracts it (only the helper is read from it)."""
    root = tmp_path / "staged"
    helper = root / script_constants()["PACKAGED"]
    helper.parent.mkdir(parents=True)
    shutil.copy(harness_paths.packaged_helper(), helper)
    return root


HOST_DIRS = ("var/lib", "usr/local/libexec", "etc/systemd/system")

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


REFUSED = "refused: "


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


# --- the README's root commands, run as written (root's parts in place, a dry run at the end) ---

def block(name: str) -> str:
    """The README's ```bash <name> fence."""
    m = re.search(rf"^```bash {name}\n(.*?)^```$", README.read_text(), re.MULTILINE | re.DOTALL)
    assert m is not None, name
    return m.group(1)


def manifest(repo: tuple[Path, str]) -> list[str]:
    """manifest.sh's lines for the commit, as the lead computes them for the approval."""
    out = subprocess.run(["bash", str(MANIFEST), "--repo", str(repo[0]), "--commit", repo[1]],
                         capture_output=True, text=True, timeout=120, check=False)
    assert (out.returncode, out.stderr) == (0, "")
    return out.stdout.splitlines()


def step3(repo: tuple[Path, str], sums: list[str], stage: Path, prefix: Path, *,
          drop: str = "") -> subprocess.CompletedProcess[str]:
    """Step 3's root script with the approval's values in place, `runuser` (root only) dropped, the stage
    under tmp and the setup a dry run."""
    first, *body, last = block("step-3").splitlines(keepends=True)
    assert (first, last) == ("sudo bash -euo pipefail -s <<'STEP3'\n", "STEP3\n")
    script = "".join(body)
    values = {"runuser -u <service user> -- ": "", STAGE: str(stage), "<checkout>": str(repo[0]),
              "<pinned commit>": repo[1], "<the archive line from manifest.sh>": sums[0],
              "<the tree lines from manifest.sh>": "\n".join(sums[1:]),
              "--service-user <service user>": f"--service-user {ME} --dry-run --prefix {prefix}"}
    for placeholder, value in values.items():
        assert placeholder in script, placeholder
        script = script.replace(placeholder, value)
    assert re.search(r"<[a-z][^>\n]*>", script) is None      # every placeholder is one the approval fills
    if drop:
        assert drop in script
        script = script.replace(drop, "")
    return subprocess.run(["bash", "-euo", "pipefail", "-s"], input=script, capture_output=True, text=True,
                          timeout=120, check=False, env=host_env(prefix))


def test_step_3_stages_the_approved_bytes_and_runs_the_staged_setup(repo: tuple[Path, str], prefix: Path,
                                                                    tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    out = step3(repo, manifest(repo), stage, prefix)
    assert (out.returncode, out.stderr) == (0, "")
    assert out.stdout.splitlines()[-1] == "done"
    staged_helper = stage / "tree" / script_constants()["PACKAGED"]
    assert staged_helper.read_bytes() == harness_paths.packaged_helper().read_bytes()
    assert f"would: install -o root -g root -m 0644 {staged_helper} " in out.stdout
    assert (stage / "tree" / "deploy" / "store-helper" / SCRIPT.name).read_bytes() == SCRIPT.read_bytes()


@pytest.fixture
def replaced(repo: tuple[Path, str]) -> tuple[tuple[Path, str], list[str]]:
    """The approved commit, its manifest taken before the service user plants `refs/replace/<commit>`
    pointing at a commit with another helper."""
    root, commit = repo
    sums = manifest(repo)
    helper = root / script_constants()["PACKAGED"]
    helper.write_bytes(helper.read_bytes() + b"# not the approved helper\n")
    git(root, "commit", "-q", "-a", "-m", "another helper")
    git(root, "replace", commit, git(root, "rev-parse", "HEAD").decode().strip())
    genuine = harness_paths.packaged_helper().read_bytes()
    assert git(root, "cat-file", "blob", f"{commit}:{script_constants()['PACKAGED']}") != genuine
    return repo, sums


def test_step_3_ignores_a_replace_ref_and_stages_the_approved_helper(
        replaced: tuple[tuple[Path, str], list[str]], prefix: Path, tmp_path: Path) -> None:
    repo, sums = replaced
    stage = tmp_path / "stage"
    out = step3(repo, sums, stage, prefix)
    assert (out.returncode, out.stderr) == (0, "")
    staged_helper = stage / "tree" / script_constants()["PACKAGED"]
    assert staged_helper.read_bytes() == harness_paths.packaged_helper().read_bytes()


def test_step_3_refuses_bytes_the_approval_does_not_name_before_extracting_them(
        replaced: tuple[tuple[Path, str], list[str]], prefix: Path, tmp_path: Path) -> None:
    """Without GIT_NO_REPLACE_OBJECTS the planted helper is archived: the approved sum fails, and nothing is
    extracted or run."""
    repo, sums = replaced
    stage = tmp_path / "stage"
    out = step3(repo, sums, stage, prefix, drop="GIT_NO_REPLACE_OBJECTS=1 ")
    assert out.returncode != 0
    assert "FAILED" in out.stdout + out.stderr
    assert list((stage / "tree").iterdir()) == []
    assert "would: " not in out.stdout


def test_step_4_installs_the_units_from_the_stage_only() -> None:
    text = block("step-4")
    sources = re.findall(r"\S*deploy/store-helper/\S+", text)
    assert sorted(sources) == sorted(f"{STAGE}/tree/deploy/store-helper/{u.name}"
                                     for u in (SOCKET_UNIT, SERVICE_UNIT))
    assert text.splitlines()[-1] == f"sudo rm -r {STAGE}"


def test_step_6_removes_only_the_harnesses_fallback_and_keeps_a_backup(tmp_path: Path) -> None:
    lines = block("step-6").splitlines()
    assert lines[-1].endswith("heterodyne config check")
    config = tmp_path / "config.toml"
    original = ("[sandbox]\nfallback = true\n\n[harnesses]\nowner = \"x\"\nfallback = true\n"
                "\n[harnesses.claude]\nfallback = true\n")
    config.write_text(original)
    out = subprocess.run(["bash", "-euo", "pipefail", "-c", "\n".join(lines[:-1])], capture_output=True,
                         text=True, timeout=60, check=False,
                         env={**os.environ, "HETERODYNE_CONFIG_DIR": str(tmp_path)})
    assert (out.returncode, out.stderr) == (0, "")
    assert config.read_text() == original.replace('owner = "x"\nfallback = true\n', 'owner = "x"\n')
    assert (tmp_path / "config.toml.bak-t12b").read_text() == original


def test_the_rollback_stops_every_helper_before_it_removes_the_store_or_the_owner() -> None:
    text = block("rollback")
    order = ["systemctl disable --now heterodyne-store-helper.socket",
             "systemctl stop 'heterodyne-store-helper@*.service'",
             "systemctl list-units --plain --no-legend --state=active,activating,deactivating "
             "'heterodyne-store-helper@*'",
             "pgrep -u heterodyne-store",
             "rm -r /var/lib/heterodyne",
             "userdel heterodyne-store"]
    at = [text.find(step) for step in order]
    assert -1 not in at and at == sorted(at), at
    first, *body, last = text.split("\n\n")[0].splitlines(keepends=True)
    last += "\n"                       # the blank line after the heredoc took its newline
    assert (first, last) == ("sudo bash -euo pipefail -s <<'STOP'\n", "STOP\n")
    out = subprocess.run(["bash", "-n"], input="".join(body), capture_output=True, text=True, timeout=60,
                         check=False)
    assert (out.returncode, out.stderr) == (0, "")
