"""The fake host for the deploy/ tests: a fake root, and shims for every host command, which log their argv.
Shared by test_deploy_store_helper.py and test_deploy_platform.py."""

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
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

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
ADMIND, WSD = "heterodyne-admind.service", "heterodyne-wsd.service"
# A unit made by hand before the installer (the dev checkout's), for --adopt
HAND_MADE = ("[Unit]\nDescription=admind, by hand\n[Service]\n"
             "ExecStart=/srv/dev/.venv/bin/python3 -m heterodyne.admind run\n"
             "[Install]\nWantedBy=default.target\n")
MANAGER = str(Path(__file__).resolve().with_name("fake_user_manager.py"))


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

# Every shim logs its argv to calls.log, one line, with an argument that spans lines (a `python3 -c`
# script) as <script>, and fails (exit 1) when a line of `fail` is a prefix of that line. Their state is files
# beside them, so a cleared environment (`env -i`) doesn't lose it.
SHIM_HEAD = r"""#!/bin/bash
S=@SHIMS@
name=${0##*/}
logged=()
for a in "$@"; do [[ $a == *$'\n'* ]] && a='<script>'; logged+=("$a"); done
printf '%s\n' "$name ${logged[*]}" >>"$S/calls.log"
while IFS= read -r f; do
    [[ -n $f && "$name ${logged[*]}" == "$f"* ]] && { echo "$name: injected failure" >&2; exit 1; }
done <"$S/fail"
"""
SHIMS = {
    # root, as far as the installer's `id -u` is concerned
    "id": r"""[[ $* == -u ]] && { echo 0; exit 0; }
exec /usr/bin/id "$@"
""",
    # `-u USER -- CMD...` runs CMD as this user, then `hook CMD...` if there is one; `-l USER -c CMD` runs
    # CMD in this environment. A run of deploy/platform_state.py (`python3 -I -c SCRIPT SUB ARGS...`) logs
    # "SUB ARGS" to platform.log, and fails (exit 1) when a line of `fail-platform` is a prefix of it.
    "runuser": r"""if [[ $1 == -u && $3 == -- ]]; then
    shift 3
    args=("$@")
    for ((i = 0; i + 1 < ${#args[@]}; i++)); do
        if [[ ${args[i]} == -c && ${args[i+1]} == *'prog="platform_state.py"'* ]]; then
            sub="${args[*]:i+2}"
            printf '%s\n' "$sub" >>"$S/platform.log"
            while IFS= read -r f; do
                [[ -n $f && $sub == "$f"* ]] && { echo "failed: injected" >&2; exit 1; }
            done <"$S/fail-platform"
        fi
    done
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
    "systemctl": r"""if [[ $1 == --user ]]; then shift; exec "$S/user-systemctl" "$@"; fi
case $1 in
    daemon-reload) [[ -f $S/reload-no-effect ]] || echo no >"$S/need-reload" ;;
    enable|disable|stop|list-units) ;;      # list-units: no helper instance left (the uninstall)
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
# `%F %u` (the user bus): the real type, and the real owner unless `uids` names another
if [[ $# == 4 && $1 == -c && $2 == '%F %u' && $3 == -- ]]; then
    out=$(/usr/bin/stat -c '%F|%u' -- "$4") || exit
    uid=${out#*|}
    while IFS='=' read -r p u; do [[ $root/$p == "${4%/}" ]] && uid=$u; done <"$S/uids"
    echo "${out%|*} $uid"
    exit 0
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
    # Linger is `linger`; a user with no session and no lingering (no `linger`) fails, as loginctl does
    "loginctl": r"""[[ $# == 5 && $1 == show-user && $3 == -p && $4 == Linger && $5 == --value ]] || exit 99
[[ -f $S/linger ]] || exit 1
cat "$S/linger"
""",
    # the service user's manager (tests/fake_user_manager.py), reached as `systemctl --user` with the bus
    # environment it logs
    "user-systemctl": r"""printf 'XDG_RUNTIME_DIR=%s\nDBUS_SESSION_BUS_ADDRESS=%s\n' "${XDG_RUNTIME_DIR-}" \
    "${DBUS_SESSION_BUS_ADDRESS-}" >"$S/user-bus.env"
exec @PYTHON@ @MANAGER@ @ROOT@ "$S" "$@"
""",
    "journalctl": "",
    # `cache dir` is `uvcache`; `sync` logs its UV_CACHE_DIR to uv-env.log and the rest of its uv settings to
    # uv-sync.env, fails as an offline cache miss with `uv-miss` (printing a sum, for the mask), and
    # otherwise writes the fake venv into the project's .venv/bin
    "uv": r"""case $1 in
    --version) echo "uv 0.11.16" ;;
    cache) [[ $2 == dir ]] || exit 99; echo "$S/uvcache" ;;
    sync)
        printf 'UV_CACHE_DIR=%s\n' "${UV_CACHE_DIR-}" >>"$S/uv-env.log"
        env | LC_ALL=C sort | command grep -E '^UV_' | command grep -v '^UV_CACHE_DIR=' >"$S/uv-sync.env" \
            || true
        if [[ -f $S/uv-miss && " $* " == *" --offline "* ]]; then
            digest=$(printf 'w%.0s' {1..8} | sha256sum | cut -c1-64)
            echo "error: w not found in the cache, sha256 $digest" >&2
            exit 2
        fi
        project=${*: -1}
        [[ ${*: -2:1} == --project && -d $project ]] || exit 99
        mkdir -p "$project/.venv/bin"
        cp -a "$S/venv/." "$project/.venv/bin/" ;;
    *) exit 99 ;;
esac
""",
    # cp as the system's, logged, so that `fail` can stand in for a hardlink copy across filesystems
    "cp": r"""exec /usr/bin/cp "$@"
""",
}


# The release's fake venv (the uv shim copies it into .venv/bin): each script is a shim, so it is logged and
# can be failed. `admind` and `wsd` are the real CLIs, except `admind initialised`, which exits with
# `admind-initialised-rc`; each script writes its environment to env-<name>.log.
VENV = {
    "python3": r"""exec @PYTHON@ "$@"
""",
    "admind": r"""env >"$S/env-admind.log"
if [[ $1 == initialised ]]; then exit "$(cat "$S/admind-initialised-rc")"; fi
exec @PYTHON@ -m heterodyne.admind "$@"
""",
    "wsd": r"""env >"$S/env-wsd.log"
exec @PYTHON@ -m heterodyne.wsd "$@"
""",
    "heterodyne": r"""env >"$S/env-heterodyne.log"
[[ $* == "config check" ]] || exit 99
cat "$S/config-check"
exit "$(cat "$S/config-check-rc")"
""",
    # status: wsctl-rc's exit if there is one; else up when the fake manager runs wsd, or `wsd-outside`
    "wsctl": r"""env >"$S/env-wsctl.log"
[[ $1 == status ]] || exit 99
if [[ -f $S/wsctl-rc ]]; then exit "$(cat "$S/wsctl-rc")"; fi
[[ $(cat "$S/u/heterodyne-wsd.service/active" 2>/dev/null) == active || -f $S/wsd-outside ]]
""",
}


def state() -> dict[str, str]:
    """A host where the store helper's socket is in place and verity is on."""
    return {
        "fail": "", "uids": "", "active": "active\n", "need-reload": "no\n", "linger": "yes\n",
        "props": f"Listen={LISTEN} (Stream)\nSocketGroup={MY_GROUP}\nSocketMode=0660\n",
        "sockstat": f"socket {defaults()['owner']} {MY_GROUP} 660\n",
        "mount": "ext4 /dev/fake0\n",
        "e2fsprogs": "tune2fs 1.47.0 (5-Feb-2023)\n",
        "superblock": FEATURES_OFF.replace("ext_attr", "ext_attr verity"),
        "config-check-rc": "0\n", "admind-initialised-rc": "0\n", "fail-platform": "",
        "user-system": "running\n", "manager-env": "PATH=/usr/bin:/bin\n",
        "config-check": "harnesses.store = /var/lib/heterodyne/harnesses    (defaults)\n",
    }


def make_checkout(root: Path) -> str:
    """A checkout at `root` with deploy/ and the helper at its one commit."""
    helper = root / script_constants()["PACKAGED"]
    helper.parent.mkdir(parents=True)
    shutil.copy(harness_paths.packaged_helper(), helper)
    shutil.copytree(REPO / "deploy", root / "deploy")
    (root / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0"\n')
    (root / "uv.lock").write_text("version = 1\n")
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

    @property
    def user(self) -> str:
        return ME

    @property
    def uid(self) -> int:
        return os.getuid()

    def bus_rel(self) -> str:
        return f"run/user/{self.uid}/bus"

    def bus(self) -> Path:
        return self.root / self.bus_rel()

    def user_bus_env(self) -> dict[str, str]:
        """The bus environment of the last `systemctl --user` call."""
        text = (self.shims / "user-bus.env").read_text()
        return dict(line.split("=", 1) for line in text.splitlines())

    def home(self) -> Path:
        """The service user's home under the fake root."""
        return self.root / self.real_home().removeprefix("/")

    def real_home(self) -> str:
        return pwd.getpwnam(ME).pw_dir

    def clear_calls(self) -> None:
        (self.shims / "calls.log").write_text("")

    def uvcache_snapshot(self) -> dict[str, tuple[int, int, int]]:
        cache = self.shims / "uvcache"
        return {str(p.relative_to(cache)): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
                for p in cache.rglob("*") if p.is_file()}

    def env_of(self, name: str) -> dict[str, str]:
        """The environment the fake venv's `name` last ran with."""
        text = (self.shims / f"env-{name}.log").read_text()
        return {k: v for k, _, v in (line.partition("=") for line in text.splitlines())}

    def uv_env_of_last_sync(self) -> dict[str, str]:
        text = (self.shims / "uv-sync.env").read_text()
        return dict(line.split("=", 1) for line in text.splitlines())

    def replace_ref(self, path: str, data: bytes) -> None:
        """A refs/replace ref that swaps the commit for one where `path` holds `data`."""
        blob = git_input(self.repo, data, "hash-object", "-w", "--stdin").strip()
        index = {"GIT_INDEX_FILE": str(self.shims / "replace.index")}
        git_env(self.repo, index, "read-tree", self.commit)
        git_env(self.repo, index, "update-index", "--cacheinfo", f"100644,{blob},{path}")
        tree = git_env(self.repo, index, "write-tree").strip()
        other = git_env(self.repo, {}, "commit-tree", tree, "-m", "replaced").strip()
        git_env(self.repo, {}, "replace", self.commit, other)

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

    # --- the platform's user state (design btq-v7sgt D4, D6) ---

    def unit_file(self, unit: str) -> Path:
        return self.home() / ".config/systemd/user" / unit

    def install_dir(self) -> Path:
        return self.home() / ".local/share/heterodyne/install"

    def record(self) -> dict[str, Any]:
        return json.loads((self.install_dir() / "record.json").read_text())

    def backups(self) -> list[Path]:
        d = self.install_dir() / "backup"
        return sorted(d.iterdir()) if d.is_dir() else []

    def platform_calls(self) -> list[str]:
        """Each platform_state.py run's "SUB ARGS", in order."""
        f = self.shims / "platform.log"
        return f.read_text().splitlines() if f.exists() else []

    def manager(self, *args: str) -> None:
        """The fake user manager, called as the service user would call it (logged, like any call)."""
        subprocess.run([str(self.shims / "user-systemctl"), *args], env={"HOME": self.real_home()},
                       check=True, capture_output=True, timeout=60)

    def unit_state(self, unit: str) -> Path:
        d = self.shims / "u" / unit
        d.mkdir(parents=True, exist_ok=True)
        return d

    def pid(self, unit: str) -> str:
        f = self.unit_state(unit) / "pid"
        return f.read_text().strip() if f.exists() else "0"

    def proc(self, unit: str) -> Path:
        return self.root / "proc" / self.pid(unit)

    def set_unit(self, unit: str, *, active: str) -> None:
        """The unit stopped, as `inactive` or `failed`."""
        if self.pid(unit) != "0":
            shutil.rmtree(self.proc(unit))
        d = self.unit_state(unit)
        (d / "active").write_text(f"{active}\n")
        (d / "sub").write_text("failed\n" if active == "failed" else "dead\n")
        (d / "pid").write_text("0\n")

    def process_env(self, unit: str) -> dict[str, str]:
        raw = (self.proc(unit) / "environ").read_bytes()
        return {k: v for k, _, v in (item.decode().partition("=") for item in raw.split(b"\0") if item)}

    def drift_process(self, unit: str, drift: str) -> None:
        """The running process, other than its unit: its argv, its unit revision, or a state dir the
        manager's environment gave it."""
        if drift == "argv":
            (self.proc(unit) / "cmdline").write_bytes(b"/usr/bin/python3\0-m\0heterodyne.admind\0run\0")
            return
        env = self.process_env(unit)
        if drift == "rev":
            env["HETERODYNE_UNIT_REV"] = "0" * 12
        else:
            env["HETERODYNE_STATE_DIR"] = "/elsewhere/state"
        environ = b"".join(f"{k}={v}".encode() + b"\0" for k, v in env.items())
        (self.proc(unit) / "environ").write_bytes(environ)

    def drop_in(self, unit: str, name: str, text: str) -> None:
        d = self.unit_file(unit).with_name(f"{unit}.d")
        d.mkdir(exist_ok=True)
        (d / name).write_text(text)

    def flap(self, unit: str) -> None:
        """The unit restarts on its own just after it starts."""
        (self.unit_state(unit) / "flap").write_text("")

    def hide_proc(self, unit: str) -> None:
        (self.proc(unit) / "environ").unlink()

    def hand_made_admind(self, kind: str = "file") -> bytes:
        """admind's unit made by hand: a file the manager runs (enabled and active), a symlink to one, or a
        directory in its place."""
        f = self.unit_file(ADMIND)
        f.parent.mkdir(parents=True, exist_ok=True)
        if kind == "symlink":
            (self.home() / "admind.service").write_text(HAND_MADE)
            f.symlink_to(self.home() / "admind.service")
        elif kind == "directory":
            f.mkdir()
        else:
            f.write_text(HAND_MADE)
            for args in (("daemon-reload",), ("enable", ADMIND), ("start", ADMIND)):
                self.manager(*args)
            self.clear_calls()
        return HAND_MADE.encode()

    def make_ambiguous(self, case: str) -> None:
        """After an install (adopting a hand-made admind for `bad-backup`), the state the uninstall can't
        decide on (design btq-v7sgt D6), at admind's unit."""
        if case == "bad-backup":
            self.hand_made_admind()
        out = installer(self, *(["--adopt"] if case == "bad-backup" else []))
        assert out.returncode == 0, out.stdout + out.stderr
        f = self.unit_file(ADMIND)
        record = self.install_dir() / "record.json"
        rec = self.record()
        if case == "marker-no-entry":
            del rec["units"][ADMIND]
        elif case == "entry-no-marker":
            f.write_text(HAND_MADE)
        elif case == "writing":
            rec["units"][ADMIND]["state"] = "writing"
        elif case == "malformed-record":
            record.write_text("{")
            return
        elif case == "symlink":
            (self.home() / "admind.service").write_bytes(f.read_bytes())
            f.unlink()
            f.symlink_to(self.home() / "admind.service")
        elif case == "bad-backup":
            [b] = self.backups()
            b.write_text("changed\n")
        elif case == "loaded-no-file":
            f.unlink()
        else:
            raise AssertionError(case)
        record.write_text(json.dumps(rec))

    def selftest_unit(self) -> None:
        f = self.unit_file("heterodyne-admind-selftest.service")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("[Service]\nExecStart=/bin/true\n")

    def new_commit(self) -> None:
        """A second commit in the checkout, which the pinned form then names."""
        (self.repo / "NEWS").write_text("a second commit\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "second")
        self.commit = git(self.repo, "rev-parse", "HEAD").decode().strip()
        self.sums = manifest(self.repo, self.commit)

    def home_snapshot(self) -> dict[str, tuple[int, int, bytes]]:
        """Every path under the fake home but the scratch parent (and .cache, which holds it), which must be
        empty."""
        cache = self.home() / ".cache/heterodyne"
        assert not cache.exists() or list(cache.iterdir()) == []
        out = {}
        for p in sorted(self.home().rglob("*")):
            if p in (cache, cache.parent):
                continue
            st = p.lstat()
            data = p.read_bytes() if p.is_file() and not p.is_symlink() else b""
            out[str(p.relative_to(self.home()))] = (st.st_mode, st.st_mtime_ns, data)
        return out


def git_env(repo: Path, extra: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], env=GIT_ENV | extra, capture_output=True,
                          check=True, timeout=60, text=True).stdout


def git_input(repo: Path, data: bytes, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], env=GIT_ENV, input=data, capture_output=True,
                          check=True, timeout=60).stdout.decode()


def git_show(host: Host, path: str) -> bytes:
    """`path` as the commit holds it, whatever refs/replace says."""
    return subprocess.run(["git", "-C", str(host.repo), "show", f"{host.commit}:{path}"],
                          env=GIT_ENV | {"GIT_NO_REPLACE_OBJECTS": "1"}, capture_output=True, check=True,
                          timeout=60).stdout


def unseal_all(root: Path) -> None:
    for p in [root, *root.rglob("*")]:
        if p.is_symlink():
            continue
        p.chmod(0o755 if p.is_dir() else 0o644)


def bind_bus(d: Path) -> socket.socket:
    """A real Unix socket at `d`/bus, the user manager's bus as the preflight sees it. It binds a relative
    name from inside `d`, since tmp_path can exceed the 108-byte limit on a socket path."""
    d.mkdir(parents=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    cwd = Path.cwd()
    os.chdir(d)
    try:
        sock.bind("bus")
    finally:
        os.chdir(cwd)
    return sock


@pytest.fixture(name="host")
def host_fixture(tmp_path: Path) -> Iterator[Host]:
    """The fake host; import `host_fixture` to use it as `host`."""
    root = tmp_path / "root"
    for d in (*HOST_DIRS, "run", "proc", "sys/fs/ext4/features", pwd.getpwnam(ME).pw_dir.removeprefix("/")):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "sys/fs/ext4/features/verity").write_text("")
    repo = root / CHECKOUT.removeprefix("/")
    commit = make_checkout(repo)
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in SHIMS.items():
        text = (SHIM_HEAD + body).replace("@SHIMS@", shlex.quote(str(shims)))
        text = text.replace("@PYTHON@", shlex.quote(sys.executable))
        text = text.replace("@MANAGER@", shlex.quote(MANAGER))
        (shims / name).write_text(text.replace("@ROOT@", shlex.quote(str(root))).replace("@LISTEN@", LISTEN))
        (shims / name).chmod(0o755)
    for name, text in state().items():
        (shims / name).write_text(text)
    (shims / "venv").mkdir()
    for name, body in VENV.items():
        text = body if name == "python3" else SHIM_HEAD + body
        text = text.replace("@SHIMS@", shlex.quote(str(shims)))
        text = text.replace("@PYTHON@", shlex.quote(sys.executable))
        (shims / "venv" / name).write_text("#!/bin/bash\n" + text if name == "python3" else text)
        (shims / "venv" / name).chmod(0o755)
    for i in range(3):
        cached = shims / "uvcache" / f"archive-v0/e{i}/f"
        cached.parent.mkdir(parents=True)
        cached.write_text(f"cached {i}\n")
    (shims / "calls.log").write_text("")
    bus = bind_bus(root / f"run/user/{os.getuid()}")
    for d in SEALED:
        (root / d).chmod(0o555)
    try:
        yield Host(root, shims, commit, manifest(repo, commit))
    finally:
        bus.close()
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
    steps = []
    for line in lines[at + 1:]:
        if not line.startswith("  "):
            break
        steps.append(line)
    return dict(line.strip().split(": ", 1) for line in steps)


def install_refusal(host: Host, out: subprocess.CompletedProcess[str]) -> str:
    """The one refusal, made before any change, with the stage (if any) gone."""
    assert out.returncode == 1, out.stdout + out.stderr
    [line] = out.stderr.splitlines()
    assert line.startswith(REFUSED)
    assert host.mutations() == []
    assert host.stages() == []
    return line.removeprefix(REFUSED)


ALL_IN_PLACE = {"store": "changed", "units": "changed", "release": "changed", "config": "ok", "verity": "ok",
                "services": "changed"}


def readme_block(name: str) -> str:
    m = re.search(rf"^```bash {name}\n(.*?)^```$", README.read_text(), re.MULTILINE | re.DOTALL)
    assert m is not None, name
    return m.group(1)


BOOT = "/run/hz-install-boot.XXXXXX"


def pinned(name: str, host: Host, tmp_path: Path, values: dict[str, str], env: dict[str, str],
           extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    """The README's pinned block `name`, as the operator pastes it with the approval's values: `sudo`
    dropped, the root-only copy under tmp, the checkout's real path where it is read, and `extra` appended
    after the script, as the new flags are (design btq-v7sgt D7)."""
    text = readme_block(name)
    assert text.startswith("sudo bash -euo pipefail -c '")
    text = text.removeprefix("sudo ")
    if extra:
        assert text.endswith("'\n") and not any(set(a) & set(" '\"\\$") for a in extra)
        text = text.removesuffix("'\n") + " " + " ".join(extra) + "'\n"
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
