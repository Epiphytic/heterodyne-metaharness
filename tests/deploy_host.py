"""The fake host for the deploy/ tests: a fake root, and shims for every host command, which log their argv.
Shared by test_deploy_store_helper.py and test_deploy_platform.py."""

import grp
import os
import pwd
import re
import shlex
import shutil
import subprocess
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import pytest
from harness_env import GIT_ENV, git

from heterodyne.harness import paths as harness_paths

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


def script_constants() -> dict[str, str]:
    return dict(re.findall(r"^readonly (\w+)=(\S+)$", SCRIPT.read_text(), re.MULTILINE))


MANIFEST = DEPLOY / "manifest.sh"


HOST_DIRS = ("var/lib", "usr/local/libexec", "etc/systemd/system")


REFUSED = "refused: "


INSTALL = REPO / "deploy" / "install.sh"
UNINSTALL = REPO / "deploy" / "uninstall.sh"
README = DEPLOY / "README.md"
CHECKOUT = "/src/repo"          # the checkout's host path; the fake root stands in for /
LISTEN = str(defaults()["helper_socket"])
PAGE = os.sysconf("SC_PAGE_SIZE")
FEATURES_OFF = f"Block size:               {PAGE}\nFilesystem features:      has_journal ext_attr\n"
# The fake root's directories setup-store.sh checks from / down: none writable by this (the service) user.
SEALED = ("", "var", "var/lib", "usr", "usr/local", "usr/local/libexec", "etc", "etc/systemd",
          "etc/systemd/system")
MUTATING = ("install ", "useradd ", "systemctl daemon-reload", "systemctl enable", "systemctl start",
            "systemctl restart", "tune2fs -O")

# Every shim logs its argv to calls.log and fails (exit 1) when a line of `fail` is a prefix of
# "<name> <args>". Their state is files beside them, so a cleared environment (`env -i`) doesn't lose it.
SHIM_HEAD = r"""#!/bin/bash
S=@SHIMS@
name=${0##*/}
printf '%s\n' "$name $*" >>"$S/calls.log"
while IFS= read -r f; do
    [[ -n $f && "$name $*" == "$f"* ]] && { echo "$name: injected failure" >&2; exit 1; }
done <"$S/fail"
"""
SHIMS = {
    # root, as far as the installer's `id -u` is concerned
    "id": r"""[[ $* == -u ]] && { echo 0; exit 0; }
exec /usr/bin/id "$@"
""",
    # `-u USER -- CMD...` runs CMD as this user, then `hook CMD...` if there is one; `-l USER -c CMD` runs
    # CMD in this environment
    "runuser": r"""if [[ $1 == -u && $3 == -- ]]; then
    shift 3
    rc=0
    "$@" || rc=$?
    if [[ -x $S/hook ]]; then "$S/hook" "$@" >/dev/null 2>&1 </dev/null; fi
    exit "$rc"
fi
[[ $# == 4 && $1 == -l && $3 == -c ]] || exit 99
exec bash -c "$4"
""",
    # daemon-reload clears NeedDaemonReload (unless `reload-no-effect`); start and restart make the socket
    # active and, with `sockstat.after`, the socket file what it then is
    "systemctl": r"""case $1 in
    daemon-reload) [[ -f $S/reload-no-effect ]] || echo no >"$S/need-reload" ;;
    enable) ;;
    is-active) read -r state <"$S/active"; echo "$state"; [[ $state == active ]] ;;
    start|restart)
        echo active >"$S/active"
        if [[ -f $S/sockstat.after ]]; then cp "$S/sockstat.after" "$S/sockstat"; fi ;;
    show)
        if [[ $# == 5 && $2 == -p && $3 == NeedDaemonReload && $4 == --value ]]; then cat "$S/need-reload"
        elif [[ $# == 4 && $2 == -p && $3 == SocketGroup,SocketMode,Listen ]]; then cat "$S/props"
        else exit 99; fi ;;
    *) exit 99 ;;
esac
""",
    # under the fake root everything is root's unless `uids` (`<path under the root>=<uid>`) names another
    # owner; the socket file reads as `sockstat` says
    "stat": r"""root=@ROOT@
if [[ $# == 4 && $1 == -c && $2 == '%F %U %G %a' && $3 == -- ]]; then
    [[ $4 == "$root@LISTEN@" && -f $S/sockstat ]] || exit 1
    exec cat "$S/sockstat"
fi
[[ $# == 4 && $1 == -c && $2 == '%u %a' && $3 == -- ]] || { echo "fake stat: unexpected use" >&2; exit 99; }
out=$(/usr/bin/stat -c '%u %a' -- "$4") || exit
path=${4%/}
[[ $path == "$root" || $path == "$root"/* ]] || { echo "$out"; exit 0; }
uid=0
while IFS='=' read -r p u; do [[ $root/$p == "$path" ]] && uid=$u; done <"$S/uids"
echo "$uid ${out#* }"
""",
    "findmnt": r"""exec cat "$S/mount"
""",
    # no arguments: the usage, then the version line, as e2fsprogs prints them; `-O verity` adds the feature
    # (unless `tune2fs-no-effect`)
    "tune2fs": r"""if (($# == 0)); then
    echo "Usage: tune2fs [-l] device" >&2
    cat "$S/e2fsprogs" >&2
    exit 1
fi
case $1 in
    -l) [[ -f $S/superblock ]] || exit 1; cat "$S/superblock" ;;
    -O) [[ $2 == verity && $# == 3 ]] || exit 99
        [[ -f $S/tune2fs-no-effect ]] || sed -i '/^Filesystem features:/s/$/ verity/' "$S/superblock" ;;
    *) exit 99 ;;
esac
""",
    # installs into the fake root, with `-o` and `-g` dropped (the stat shim reads it as root's) and the
    # destination's directory writable for the copy only; fails before writing when `install-fail` names
    # the destination
    "install": r"""dest=${!#}
if [[ -f $S/install-fail && ${dest##*/} == "$(cat "$S/install-fail")" ]]; then
    echo "install: injected failure" >&2
    exit 1
fi
args=()
while (($# > 2)); do
    case $1 in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
done
dir=${dest%/*}
mode=$(/usr/bin/stat -c %a -- "$dir")
chmod u+w -- "$dir"
rc=0
/usr/bin/install "${args[@]}" -- "$1" "$dest" || rc=$?
chmod "$mode" -- "$dir"
exit "$rc"
""",
    "useradd": "",
    "uv": r"""case "$*" in
    *" heterodyne --help") ;;
    *" heterodyne config check") cat "$S/config-check"; exit "$(cat "$S/config-check-rc")" ;;
    *) exit 99 ;;
esac
""",
}


def state() -> dict[str, str]:
    """A host where the store helper's socket is in place and verity is on."""
    return {
        "fail": "", "uids": "", "active": "active\n", "need-reload": "no\n",
        "props": f"Listen={LISTEN} (Stream)\nSocketGroup={MY_GROUP}\nSocketMode=0660\n",
        "sockstat": f"socket {defaults()['owner']} {MY_GROUP} 660\n",
        "mount": "ext4 /dev/fake0\n",
        "e2fsprogs": "tune2fs 1.47.0 (5-Feb-2023)\n",
        "superblock": FEATURES_OFF.replace("ext_attr", "ext_attr verity"),
        "config-check-rc": "0\n",
        "config-check": "harnesses.store = /var/lib/heterodyne/harnesses    (defaults)\n",
    }


def make_checkout(root: Path) -> str:
    """A checkout at `root` with deploy/ and the helper at its one commit."""
    helper = root / script_constants()["PACKAGED"]
    helper.parent.mkdir(parents=True)
    shutil.copy(harness_paths.packaged_helper(), helper)
    shutil.copytree(REPO / "deploy", root / "deploy")
    subprocess.run(["git", "init", "-q", str(root)], env=GIT_ENV, check=True, capture_output=True, timeout=60)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return git(root, "rev-parse", "HEAD").decode().strip()


def manifest(repo: Path, commit: str) -> list[str]:
    """manifest.sh's lines for the commit, as the lead computes them for the approval."""
    out = subprocess.run(["bash", str(MANIFEST), "--repo", str(repo), "--commit", commit],
                         capture_output=True, text=True, timeout=120, check=False)
    assert (out.returncode, out.stderr) == (0, "")
    return out.stdout.splitlines()


def sum_of(sums: list[str], name: str) -> str:
    [line] = [s for s in sums if s.endswith(f"  {name}")]
    return line.split()[0]


@dataclass
class Host:
    root: Path
    shims: Path
    commit: str
    sums: list[str]

    @property
    def repo(self) -> Path:
        return self.root / CHECKOUT.removeprefix("/")

    @property
    def config(self) -> Path:
        home = self.root / pwd.getpwnam(ME).pw_dir.removeprefix("/")
        return home / ".config" / "heterodyne" / "config.toml"

    def put(self, name: str, text: str) -> None:
        (self.shims / name).write_text(text)

    def calls(self) -> list[str]:
        return (self.shims / "calls.log").read_text().splitlines()

    def mutations(self) -> list[str]:
        return [c for c in self.calls() if c.startswith(MUTATING)]

    def stages(self) -> list[Path]:
        return sorted((self.root / "run").glob("heterodyne-install.*"))

    def installed(self, unit: Path) -> bytes:
        return (self.root / "etc/systemd/system" / unit.name).read_bytes()


def unseal_all(root: Path) -> None:
    for p in [root, *root.rglob("*")]:
        if p.is_symlink():
            continue
        p.chmod(0o755 if p.is_dir() else 0o644)


@pytest.fixture(name="host")
def host_fixture(tmp_path: Path) -> Iterator[Host]:
    """The fake host; import `host_fixture` to use it as `host`."""
    root = tmp_path / "root"
    for d in (*HOST_DIRS, "run", "sys/fs/ext4/features", pwd.getpwnam(ME).pw_dir.removeprefix("/")):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "sys/fs/ext4/features/verity").write_text("")
    repo = root / CHECKOUT.removeprefix("/")
    commit = make_checkout(repo)
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in SHIMS.items():
        text = (SHIM_HEAD + body).replace("@SHIMS@", shlex.quote(str(shims)))
        (shims / name).write_text(text.replace("@ROOT@", shlex.quote(str(root))).replace("@LISTEN@", LISTEN))
        (shims / name).chmod(0o755)
    for name, text in state().items():
        (shims / name).write_text(text)
    (shims / "calls.log").write_text("")
    for d in SEALED:
        (root / d).chmod(0o555)
    yield Host(root, shims, commit, manifest(repo, commit))
    unseal_all(root)


def host_path(host: Host) -> str:
    return f"{host.shims}:{os.environ['PATH']}"


def installer(host: Host, *args: str, pinned: bool = True, user: str | None = ME, script: Path | None = None,
              env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """install.sh against the fake root: the pinned form by default (this user can write the checkout)."""
    argv = ["bash", str(script or host.repo / "deploy" / "install.sh"), "--prefix", str(host.root),
            "--checkout", CHECKOUT]
    if user is not None:
        argv += ["--service-user", user]
    if pinned:
        argv += ["--commit", host.commit, "--expect-sha256", sum_of(host.sums, "archive.tar")]
    base = {k: v for k, v in os.environ.items() if k != "SUDO_USER"}
    return subprocess.run([*argv, *args], capture_output=True, text=True, timeout=300, check=False,
                          env={**base, "PATH": host_path(host), "HZ_INSTALL_TEST": "1", **(env or {})})


def summary(out: subprocess.CompletedProcess[str]) -> dict[str, str]:
    lines = out.stdout.splitlines()
    at = lines.index("summary:")
    return dict(line.strip().split(": ", 1) for line in lines[at + 1:at + 5])


def install_refusal(host: Host, out: subprocess.CompletedProcess[str]) -> str:
    """The one refusal, made before any change, with the stage (if any) gone."""
    assert out.returncode == 1, out.stdout + out.stderr
    [line] = out.stderr.splitlines()
    assert line.startswith(REFUSED)
    assert host.mutations() == []
    assert host.stages() == []
    return line.removeprefix(REFUSED)


ALL_IN_PLACE = {"store": "changed", "units": "changed", "config": "ok", "verity": "ok"}


def readme_block(name: str) -> str:
    m = re.search(rf"^```bash {name}\n(.*?)^```$", README.read_text(), re.MULTILINE | re.DOTALL)
    assert m is not None, name
    return m.group(1)


BOOT = "/run/hz-install-boot.XXXXXX"


def pinned(name: str, host: Host, tmp_path: Path, values: dict[str, str],
           env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """The README's pinned block `name`, as the operator pastes it with the approval's values: `sudo`
    dropped, the root-only copy under tmp, and the checkout's real path where it is read."""
    text = readme_block(name)
    assert text.startswith("sudo bash -euo pipefail -c '")
    text = text.removeprefix("sudo ")
    for placeholder, value in {BOOT: str(tmp_path / "boot.XXXXXX"), "cat <checkout>/": f"cat {host.repo}/",
                               **values}.items():
        assert placeholder in text, placeholder
        text = text.replace(placeholder, value)
    assert re.search(r"<[a-z][^>\n]*>", text) is None
    return subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=300, check=False,
                          env={**os.environ, **env})


HOOK_EDIT_UNIT = r"""#!/bin/bash
[[ " $* " == *" @WHEN@ "* ]] || exit 0
chmod -R u+w @REPO@
printf '# not the commit\n' >>@REPO@/deploy/store-helper/heterodyne-store-helper.socket
@THEN@
"""


def hook(host: Host, when: str, then: str = "") -> None:
    text = (HOOK_EDIT_UNIT.replace("@WHEN@", when).replace("@REPO@", shlex.quote(str(host.repo)))
            .replace("@THEN@", then))
    (host.shims / "hook").write_text(text)
    (host.shims / "hook").chmod(0o755)
