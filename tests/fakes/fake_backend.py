"""A sandbox backend for offline tests: each 'sandbox' is a record, and its commands run on the host.

Paths a spec binds somewhere else inside (/run/hz, /run/hz-bridge, the Codex daemon directory) are mapped
back to their host sources in argv and in the environment, and the map is exported as HZ_FAKE_PATHS
for the fake CLI's hook commands. Login binds are not mapped: nothing in the fake reads them. The test's
Python directory leads PATH, so the hook command's `python3` is the test interpreter."""

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from heterodyne.sandbox.backend import BackendError, BackendUnavailable, ExecResult
from heterodyne.sandbox.spec import SandboxSpec


@dataclass
class _Box:
    spec: SandboxSpec
    paths: dict[str, str]


@dataclass
class FakeBackend:
    up: bool = True
    boxes: dict[str, _Box] = field(default_factory=dict[str, _Box])
    extra: set[str] = field(default_factory=set[str])         # listed names the runtime didn't create
    create_failures: int = 0
    delete_unconfirmed: int = 0
    list_failures: int = 0
    created: list[str] = field(default_factory=list[str])
    deleted: list[str] = field(default_factory=list[str])
    execs: list[list[str]] = field(default_factory=list[list[str]])
    ttys: list[list[str]] = field(default_factory=list[list[str]])          # each pane's argv, as asked
    reapers: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    env_extra: dict[str, str] = field(default_factory=dict[str, str])     # fault knobs for the fake CLI

    def available(self) -> bool:
        return self.up

    def names(self) -> set[str]:
        if not self.up or self.list_failures:
            self.list_failures = max(0, self.list_failures - 1)
            raise BackendUnavailable("the fake backend is down")
        return set(self.boxes) | self.extra

    def create(self, spec: SandboxSpec, scratch: Path) -> None:
        if not self.up:
            raise BackendUnavailable("the fake backend is down")
        paths = {str(b.target): str(b.source) for b in spec.binds if b.target != b.source}
        self.boxes[spec.name] = _Box(spec, paths)
        self.created.append(spec.name)
        if self.create_failures:
            self.create_failures -= 1
            raise BackendError("sandbox create failed")

    def _host(self, box: _Box, text: str) -> str:
        if not box.paths:
            return text
        pattern = "|".join(re.escape(p) for p in sorted(box.paths, key=len, reverse=True))
        return re.sub(pattern, lambda m: box.paths[m.group(0)], text)

    def _env(self, box: _Box) -> dict[str, str]:
        env = {k: self._host(box, v) for k, v in box.spec.env.items()}
        env["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), env.get("PATH", "")))
        env["HZ_FAKE_PATHS"] = json.dumps(box.paths)
        env |= self.env_extra
        return env

    def _box(self, name: str) -> _Box:
        box = self.boxes.get(name)
        if box is None or not self.up:
            raise BackendError("no such sandbox")
        return box

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        box = self._box(name)
        host = [self._host(box, a) for a in argv]
        self.execs.append(host)
        try:
            proc = subprocess.run(host, cwd=workdir, env=self._env(box), input=input or b"",
                                  capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise BackendError("sandbox exec timed out") from None
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def tty_argv(self, name: str, workdir: Path, argv: Sequence[str]) -> list[str]:
        box = self._box(name)
        self.ttys.append(list(argv))
        env = [f"{k}={v}" for k, v in self._env(box).items()]
        return ["env", "-i", *env, "sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", str(workdir),
                *(self._host(box, a) for a in argv)]

    def delete(self, name: str) -> bool:
        if not self.up:
            return False
        if self.delete_unconfirmed:
            self.delete_unconfirmed -= 1
            return False
        box = self.boxes.pop(name, None)
        self.extra.discard(name)
        self.deleted.append(name)
        if box is not None:             # ends the fake app-server, which runs until its socket goes
            for folder in (Path(source) for source in box.paths.values()):
                if folder.name in ("bridge", "daemon") and folder.is_dir():
                    for entry in folder.iterdir():
                        if entry.is_socket():
                            entry.unlink()
        return True

    def logs(self, name: str, since: float) -> list[str]:
        return []

    def network_mode(self, name: str) -> str:
        return "none"

    def workload_pid(self, name: str) -> int:
        return os.getpid()

    def reaper_argv(self, name: str, deadline: int) -> list[str]:
        """The fake's sandboxes live in this process, so its backstop only waits; Task 11's tests drive
        the deletion it stands for, and `reaper.reap` is tested on its own."""
        self.reapers.append((name, deadline))
        return [sys.executable, "-I", "-c", "import time; time.sleep(86400)"]

    def pane_env(self) -> Mapping[str, str]:
        return {}
