"""The service user's systemd user manager, faked for the deploy/ tests: `systemctl --user` as install.sh and
uninstall.sh use it. The `user-systemctl` shim runs it as `fake_user_manager.py ROOT SHIMS VERB ARGS...`.

Its state is files under SHIMS/u/<unit>/, so that bash shims and the Host helpers can read and set it:
`active`, `sub`, `pid`, `nrestarts`, `enabled`, and `loaded.json`, what the last daemon-reload parsed from
the unit file under ROOT and its drop-ins. A start writes ROOT/proc/<pid>/cmdline and environ from the
loaded unit: SHIMS/manager-env, then Environment=, less UnsetEnvironment=. Knobs, in SHIMS:
`user-reload-no-effect` (daemon-reload loads nothing) and u/<unit>/flap (the second `show` of MainPID after
a start finds the unit restarted)."""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

FIRST_PID = 4000


class Manager:
    def __init__(self, root: Path, shims: Path) -> None:
        self.root = root
        self.shims = shims
        self.home = os.environ["HOME"]
        self.unit_dir = f"{self.home}/.config/systemd/user"

    # --- state ---

    def d(self, unit: str) -> Path:
        d = self.shims / "u" / unit
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get(self, unit: str, key: str, default: str) -> str:
        f = self.d(unit) / key
        return f.read_text().strip() if f.exists() else default

    def put(self, unit: str, key: str, value: str) -> None:
        (self.d(unit) / key).write_text(f"{value}\n")

    def loaded(self, unit: str) -> dict | None:
        f = self.d(unit) / "loaded.json"
        return json.loads(f.read_text()) if f.exists() else None

    # --- the unit files, as systemd reads them ---

    def units_path(self) -> Path:
        return self.root / self.unit_dir.removeprefix("/")

    def fragment(self, unit: str) -> Path:
        return self.units_path() / unit

    def dropins(self, unit: str) -> list[Path]:
        d = self.fragment(unit).with_name(f"{unit}.d")
        return sorted(d.glob("*.conf")) if d.is_dir() else []

    def files_hash(self, unit: str) -> str | None:
        f = self.fragment(unit)
        if not f.is_file():
            return None
        h = hashlib.sha256(f.read_bytes())
        for p in self.dropins(unit):
            h.update(p.name.encode() + b"\0" + p.read_bytes())
        return h.hexdigest()

    def parse(self, unit: str) -> dict:
        execs: list[list[str]] = []
        env: dict[str, str] = {}
        unset: list[str] = []
        for p in [self.fragment(unit), *self.dropins(unit)]:
            section = ""
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if line.startswith("["):
                    section = line
                    continue
                if section != "[Service]":
                    continue
                key, _, value = line.partition("=")
                if key == "ExecStart":
                    execs = [*execs, value.split()] if value else []
                elif key == "Environment":
                    if not value:
                        env = {}
                    for item in value.split():
                        k, _, v = item.partition("=")
                        env[k] = v.replace("%%", "%")
                elif key == "UnsetEnvironment":
                    unset = [*unset, *value.split()] if value else []
        return {"exec": [[a.replace("%%", "%") for a in e] for e in execs], "env": env, "unset": unset,
                "fragment": f"{self.unit_dir}/{unit}",
                "dropins": [f"{self.unit_dir}/{unit}.d/{p.name}" for p in self.dropins(unit)],
                "hash": self.files_hash(unit)}

    def daemon_reload(self) -> None:
        if (self.shims / "user-reload-no-effect").exists():
            return
        units = {p.name for p in self.units_path().glob("*.service")}
        known = self.shims / "u"
        units |= {p.name for p in known.iterdir() if p.is_dir()} if known.is_dir() else set()
        for unit in units:
            f = self.d(unit) / "loaded.json"
            if self.fragment(unit).is_file():
                f.write_text(json.dumps(self.parse(unit)))
            elif f.exists():
                f.unlink()

    # --- processes ---

    def next_pid(self) -> int:
        f = self.shims / "u" / "next-pid"
        pid = int(f.read_text()) if f.exists() else FIRST_PID
        f.write_text(f"{pid + 1}\n")
        return pid

    def proc(self, pid: int | str) -> Path:
        return self.root / "proc" / str(pid)

    def spawn(self, unit: str) -> None:
        loaded = self.loaded(unit)
        assert loaded is not None and loaded["exec"]
        old = self.get(unit, "pid", "0")
        if old != "0":
            shutil.rmtree(self.proc(old), ignore_errors=True)
        env: dict[str, str] = {}
        base = self.shims / "manager-env"
        if base.exists():
            for line in base.read_text().splitlines():
                k, sep, v = line.partition("=")
                if sep:
                    env[k] = v
        env |= loaded["env"]
        for k in loaded["unset"]:
            env.pop(k, None)
        pid = self.next_pid()
        d = self.proc(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in loaded["exec"][0]))
        (d / "environ").write_bytes(b"".join(f"{k}={v}".encode() + b"\0" for k, v in env.items()))
        self.put(unit, "pid", str(pid))
        self.put(unit, "active", "active")
        self.put(unit, "sub", "running")
        self.put(unit, "since-start", "0")

    def stop(self, unit: str) -> None:
        old = self.get(unit, "pid", "0")
        if old != "0":
            shutil.rmtree(self.proc(old), ignore_errors=True)
        self.put(unit, "pid", "0")
        self.put(unit, "active", "inactive")
        self.put(unit, "sub", "dead")

    # --- show ---

    def need_reload(self, unit: str) -> str:
        loaded = self.loaded(unit)
        now = self.files_hash(unit)
        if loaded is None:
            return "no" if now is None else "yes"
        return "no" if loaded["hash"] == now else "yes"

    def prop(self, unit: str, name: str) -> list[str]:
        loaded = self.loaded(unit)
        if name == "MainPID" and self.get(unit, "active", "inactive") == "active":
            since = int(self.get(unit, "since-start", "0")) + 1
            self.put(unit, "since-start", str(since))
            if since == 2 and (self.d(unit) / "flap").exists():
                self.spawn(unit)
                self.put(unit, "since-start", str(since))
                self.put(unit, "nrestarts", str(int(self.get(unit, "nrestarts", "0")) + 1))
        simple = {
            "Id": unit,
            "LoadState": "loaded" if loaded else "not-found",
            "ActiveState": self.get(unit, "active", "inactive"),
            "SubState": self.get(unit, "sub", "dead"),
            "MainPID": self.get(unit, "pid", "0"),
            "NRestarts": self.get(unit, "nrestarts", "0"),
            "NeedDaemonReload": self.need_reload(unit),
            "FragmentPath": loaded["fragment"] if loaded else "",
            "DropInPaths": " ".join(loaded["dropins"]) if loaded else "",
            "Environment": " ".join(f"{k}={v}" for k, v in loaded["env"].items()) if loaded else "",
            "UnsetEnvironment": " ".join(loaded["unset"]) if loaded else "",
        }
        if name == "ExecStart":
            execs = loaded["exec"] if loaded else []
            return [f"{{ path={e[0]} ; argv[]={' '.join(e)} ; ignore_errors=no ; start_time=[n/a] ; "
                    "stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }" for e in execs]
        if name not in simple:
            raise SystemExit(99)
        return [simple[name]]

    def show(self, args: list[str]) -> None:
        units: list[str] = []
        props: list[str] = []
        value = False
        it = iter(args)
        for a in it:
            if a == "-p":
                props += next(it).split(",")
            elif a == "--value":
                value = True
            elif a.startswith("-"):
                raise SystemExit(99)
            else:
                units.append(a)
        if not units or not props:
            raise SystemExit(99)
        blocks = []
        for unit in units:
            lines = [v if value else f"{p}={v}" for p in props for v in self.prop(unit, p)]
            blocks.append("".join(f"{line}\n" for line in lines))
        print("\n".join(blocks), end="")

    # --- the verbs ---

    def run(self, verb: str, args: list[str]) -> int:
        if verb == "is-system-running":
            state = (self.shims / "user-system").read_text().strip()
            print(state)
            return 0 if state == "running" else 1
        if verb == "daemon-reload" and not args:
            self.daemon_reload()
            return 0
        if verb == "show":
            self.show(args)
            return 0
        if len(args) != 1:
            return 99
        unit = args[0]
        if verb == "is-enabled":
            state = self.get(unit, "enabled", "disabled")
            print(state)
            return 0 if state == "enabled" else 1
        if verb == "is-active":
            state = self.get(unit, "active", "inactive")
            print(state)
            return 0 if state == "active" else 3
        if verb in ("enable", "disable"):
            if verb == "enable" and self.loaded(unit) is None:
                return 1
            self.put(unit, "enabled", "enabled" if verb == "enable" else "disabled")
            return 0
        if verb in ("start", "restart"):
            if self.loaded(unit) is None:
                print(f"Unit {unit} not found.", file=sys.stderr)
                return 5
            if verb == "restart" or self.get(unit, "active", "inactive") != "active":
                self.spawn(unit)
            return 0
        if verb == "stop":
            self.stop(unit)
            return 0
        if verb == "reset-failed":
            if self.get(unit, "active", "inactive") == "failed":
                self.put(unit, "active", "inactive")
                self.put(unit, "sub", "dead")
            return 0
        return 99


def main(argv: list[str]) -> int:
    root, shims, verb, *args = argv
    return Manager(Path(root), Path(shims)).run(verb, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
