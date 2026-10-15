"""Plan 4c Task 8: the launcher's check of a pinned install (E17, E22, E23), through `verify_pin` and through
`SandboxRuntime.launch` on the fake backend; the taint marker; and the Managed install probe's record."""

import ast
import contextlib
import errno
import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import harness_env as he
import pytest
from harness_env import Fixture
from sandbox_env import RuntimeRig, bead_worktree, launch_spec, runtime_rig, short_dir
from tmux_guard import new_test_tmux
from wsd_env import PROFILES, Clock, accounts_at, git_repo

from heterodyne.agents.base import UNDECLARED_VERSION, Adapter, UsageReport
from heterodyne.agents.codex import Codex
from heterodyne.agents.registry import ADAPTERS
from heterodyne.config.capabilities import Capabilities
from heterodyne.harness import verify
from heterodyne.harness.selection import InstallPin
from heterodyne.sandbox.build import build_usage_reader
from heterodyne.sandbox.hostread import HostUsageReader
from heterodyne.sandbox.runtime import (
    MAX_QUEUED,
    Phase,
    SandboxRuntime,
    SessionRecord,
    read_record,
    write_record,
)
from heterodyne.sandbox.spec import INSTALL_RECORD_INSIDE
from heterodyne.wsd.runtime import (
    INSTALL_UNPINNED,
    NOT_SELECTED,
    InstallTainted,
    LaunchFailed,
    LaunchSpec,
    LimitSeen,
    RuntimeUnavailable,
)
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.workstream import WorkstreamSettings

PROBES = Path(__file__).resolve().parents[1] / "src" / "heterodyne" / "sandbox" / "resources" / "probes.py"
UNSAFE = ["the install store is writable by another user"]


class Undeclared(Codex):
    """Codex declaring no version: every install of it is undeclared."""
    versions: Mapping[str, Any] = {}


def walk_problem() -> Callable[[dict[str, Any]], str | None]:
    """The probe's `walk_problem`, with the `listing` it walks by, defined on their own."""
    tree = ast.parse(PROBES.read_text())
    fns: list[ast.stmt] = [n for n in tree.body
                           if isinstance(n, ast.FunctionDef) and n.name in ("walk_problem", "listing")]
    scope: dict[str, Any] = {"os": os, "stat": stat, "errno": errno}
    exec(compile(ast.Module(fns, []), str(PROBES), "exec"), scope)  # noqa: S102 - the probe's own code
    return scope["walk_problem"]


def other_digest() -> str:
    return "sha256:" + hashlib.sha256(b"another manifest").hexdigest()


def other_name(f: Fixture) -> str:
    """A well-formed install name that is not published."""
    version, _, tail = f.pin.install.rpartition("-")
    return f"{version}-{'b' * 12 if tail != 'b' * 12 else 'c' * 12}"


def link_into(f: Fixture, folder: Path) -> Path:
    """A hard link to the install's entry point, made after publish, from `folder`: its nlink rises."""
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / "linked"
    os.link(f.binary, link)
    return link


def another_codex(f: Fixture) -> Fixture:
    """A second Codex install, published beside `f`'s and not selected."""
    tree = {**he.fake_tree("codex"), "codex-resources/voice/README": b"another voice\n"}
    return he.store_fixture(f.store.root.parent, "codex", tree=tree)


@dataclass
class Case:
    """What a refusal's breaker leaves: the adapter and pin to verify, and the store's problems."""
    adapter: Adapter
    pin: InstallPin
    problems: Callable[[], Sequence[str]] = list


def _tainted(f: Fixture, tmp: Path) -> Case:
    verify.taint(f.taint_dir, "codex", f.pin.install, verify.INSTALL_CHANGED)
    return Case(ADAPTERS["codex"], f.pin)


def _unsafe(f: Fixture, tmp: Path) -> Case:
    return Case(ADAPTERS["codex"], f.pin, lambda: UNSAFE)


def _no_such(f: Fixture, tmp: Path) -> Case:
    return Case(ADAPTERS["codex"], InstallPin(other_name(f), f.pin.digest))


def _meta_unreadable(f: Fixture, tmp: Path) -> Case:
    he.rewrite(f.store.root / "codex" / ".meta" / f"{f.pin.install}.manifest.json")    # no longer JSON
    return Case(ADAPTERS["codex"], f.pin)


def _digest_differs(f: Fixture, tmp: Path) -> Case:
    return Case(ADAPTERS["codex"], InstallPin(f.pin.install, other_digest()))


def _changed(f: Fixture, tmp: Path) -> Case:
    link_into(f, tmp / "elsewhere")
    return Case(ADAPTERS["codex"], f.pin)


def _undeclared(f: Fixture, tmp: Path) -> Case:
    return Case(Undeclared(), f.pin)


BREAKERS: dict[str, Callable[[Fixture, Path], Case]] = {
    verify.INSTALL_TAINTED: _tainted, verify.STORE_UNSAFE: _unsafe, verify.NO_SUCH_INSTALL: _no_such,
    verify.META_UNREADABLE: _meta_unreadable, verify.DIGEST_DIFFERS: _digest_differs,
    verify.INSTALL_CHANGED: _changed, UNDECLARED_VERSION: _undeclared}
# verify_pin is never handed a missing pin: NOT_SELECTED is `verified_cli`'s (and the guard's,
# test_wsd_dispatch), INSTALL_UNPINNED is `_cli`'s. Both are tested below through the runtime.
RUNTIME_ONLY = (NOT_SELECTED, INSTALL_UNPINNED)


def test_every_refusal_is_tested() -> None:
    assert set(BREAKERS) | set(RUNTIME_ONLY) == set(verify.REFUSALS)
    assert len(set(verify.REFUSALS)) == len(verify.REFUSALS)       # each text names one refusal
    assert set(verify.TAINTING) <= set(BREAKERS)


def markers(f: Fixture) -> list[Path]:
    return sorted(p for p in f.taint_dir.rglob("*") if p.is_file()) if f.taint_dir.exists() else []


def verify_case(f: Fixture, case: Case, mark: verify.Taint = verify.taint) -> verify.VerifiedInstall:
    return verify.verify_pin(f.store, case.adapter, case.pin, f.taint_dir, problems=case.problems, mark=mark)


# --- through verify_pin ---

@pytest.mark.parametrize("constant", BREAKERS)
def test_each_refusal_through_verify_pin(tmp_path: Path, constant: str) -> None:
    f = he.store_fixture(tmp_path / "i", "codex")
    verify_case(f, Case(ADAPTERS["codex"], f.pin))                  # it verifies before it is broken
    case = BREAKERS[constant](f, tmp_path)
    with pytest.raises(verify.VerifyRefused) as refused:
        verify_case(f, case)
    assert (refused.value.constant, str(refused.value)) == (constant, constant)


@pytest.mark.parametrize("name", ["../codex", "0.160.0", "0.160.0-ABCDEFABCDEF", ""])
def test_an_install_name_that_isnt_one_is_refused_before_any_path_is_built(tmp_path: Path, name: str) -> None:
    f = he.store_fixture(tmp_path / "i", "codex")
    with pytest.raises(verify.VerifyRefused) as refused:
        verify_case(f, Case(ADAPTERS["codex"], InstallPin(name, f.pin.digest)))
    assert refused.value.constant == verify.NO_SUCH_INSTALL and markers(f) == []


def test_store_problems_that_cant_be_listed_are_unsafe(tmp_path: Path) -> None:
    f = he.store_fixture(tmp_path / "i", "codex")

    def broken() -> list[str]:
        raise OSError("the store's parent can't be read")
    with pytest.raises(verify.VerifyRefused) as refused:
        verify_case(f, Case(ADAPTERS["codex"], f.pin, broken))
    assert refused.value.constant == verify.STORE_UNSAFE


@pytest.mark.parametrize("constant", verify.TAINTING)
def test_an_integrity_refusal_writes_the_taint_marker_before_it_raises(tmp_path: Path, constant: str) -> None:
    f = he.store_fixture(tmp_path / "i", "codex")
    case = BREAKERS[constant](f, tmp_path)
    order: list[str] = []

    def mark(taint_dir: Path, adapter: str, install: str, text: str) -> None:
        order.append("marked")
        verify.taint(taint_dir, adapter, install, text)
    with pytest.raises(verify.VerifyRefused) as refused:
        verify_case(f, case, mark)
    order.append("raised")
    assert order == ["marked", "raised"] and refused.value.constant == constant
    marker = f.taint_dir / "codex" / f.pin.install
    assert marker.read_text() == constant + "\n" and stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert verify.tainted(f.taint_dir, "codex", f.pin.install)
    assert [p.name for p in (f.taint_dir / "codex").iterdir()] == [f.pin.install]     # no temp file left
    with pytest.raises(verify.VerifyRefused) as again:          # now it refuses before it reads the install
        verify_case(f, Case(ADAPTERS["codex"], f.pin))
    assert again.value.constant == verify.INSTALL_TAINTED


@pytest.mark.parametrize("constant", [c for c in BREAKERS if c not in verify.TAINTING])
def test_a_refusal_outside_tainting_writes_no_marker(tmp_path: Path, constant: str) -> None:
    f = he.store_fixture(tmp_path / "i", "codex")
    case = BREAKERS[constant](f, tmp_path)
    before = markers(f)
    with pytest.raises(verify.VerifyRefused):
        verify_case(f, case, lambda *a: pytest.fail("marked"))
    assert markers(f) == before


def test_a_taint_marker_that_cant_be_read_refuses(tmp_path: Path) -> None:
    f = he.store_fixture(tmp_path / "i", "codex")
    (f.taint_dir / "codex" / f.pin.install).mkdir(parents=True)        # a directory where the file goes
    with pytest.raises(OSError):
        verify.tainted(f.taint_dir, "codex", f.pin.install)
    with pytest.raises(verify.VerifyRefused) as refused:
        verify_case(f, Case(ADAPTERS["codex"], f.pin))
    assert refused.value.constant == verify.INSTALL_TAINTED


def test_a_cleared_taint_verifies_again(tmp_path: Path) -> None:
    f = he.store_fixture(tmp_path / "i", "codex")
    verify.taint(f.taint_dir, "codex", f.pin.install, verify.DIGEST_DIFFERS)
    verify.clear_taint(f.taint_dir, "codex", f.pin.install)
    verify.clear_taint(f.taint_dir, "codex", f.pin.install)              # none left: nothing to do
    assert verify_case(f, Case(ADAPTERS["codex"], f.pin)).name == f.pin.install


def test_a_hard_link_into_an_install_from_a_login_dir_is_caught(tmp_path: Path) -> None:
    f = he.store_fixture(tmp_path / "i", "codex")
    link_into(f, tmp_path / "home" / ".codex")
    with pytest.raises(verify.VerifyRefused) as refused:
        verify_case(f, Case(ADAPTERS["codex"], f.pin))
    assert refused.value.constant == verify.INSTALL_CHANGED
    assert verify.tainted(f.taint_dir, "codex", f.pin.install)


def identities(root: Path) -> set[tuple[int, int]]:
    return {(st.st_dev, st.st_ino) for st in (p.lstat() for p in [root, *root.rglob("*")])}


def test_a_hard_link_to_a_login_file_is_impossible_in_an_install_before_and_after_a_refresh_by_rename(
        tmp_path: Path) -> None:
    """Stage copies the artifact's bytes, so every published file is a new inode with one link; the
    install's directories are read-only, so no link can be added into them; and a login refreshed by
    rename is a new inode the install never shares."""
    login_dir = tmp_path / "home" / ".codex"
    login_dir.mkdir(parents=True)
    login = login_dir / "auth.json"
    login.write_text('{"fake": "not-a-token"}')
    f = he.store_fixture(tmp_path / "i", "codex")
    installed = verify_case(f, Case(ADAPTERS["codex"], f.pin))
    assert all(e.nlink == 1 for e in installed.manifest.entries if e.kind == "file")
    for when in ("before", "after"):
        assert (login.stat().st_dev, login.stat().st_ino) not in identities(f.root), when
        if os.geteuid() != 0:
            with pytest.raises(PermissionError):
                os.link(login, f.root / "auth.json")
        assert verify_case(f, Case(ADAPTERS["codex"], f.pin)).record == installed.record, when
        fresh = login_dir / ".auth.json.new"
        fresh.write_text('{"fake": "refreshed"}')
        fresh.replace(login)                        # the CLI's own refresh: a new file renamed over it


def test_the_record_of_a_verified_install_matches_the_probes_walk(tmp_path: Path) -> None:
    for adapter in ("claude-code", "codex"):
        installed = he.store_fixture(tmp_path / adapter, adapter).verified()
        empty = ["/sandbox/home/.codex/tmp"]
        record = json.loads(verify.install_record(installed, empty))
        assert (record["path"], record["empty_read_only"]) == (str(installed.root), empty)
        assert walk_problem()(record) is None, adapter


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_a_host_package_publishes_with_its_own_modes(tmp_path: Path, adapter: str) -> None:
    """The live tests' store (`host_part`): an unpacked package's executables keep their mode, whatever
    their first bytes, beside a `defaults` part."""
    tree = {k: (b"\x7fELF fake" if v.startswith(he.EXECUTABLE) else v) if isinstance(v, bytes) else v
            for k, v in he.fake_tree(adapter).items()}
    source = he.make_tree(tmp_path / "unpacked", tree)
    entry = he.CODEX_ENTRY if adapter == "codex" else he.CLAUDE_ENTRY
    (source / entry.removeprefix("cli/")).chmod(0o755)
    parts = [he.host_part(adapter, source), he.defaults_part("config.toml", b"# fake\n")]
    installed = he.store_fixture(tmp_path / "i", adapter, parts=parts).verified()
    assert os.access(installed.cli.binary, os.X_OK) and installed.cli.binary.read_bytes() == b"\x7fELF fake"
    assert (installed.root / "defaults" / "config.toml").read_bytes() == b"# fake\n"


# --- through SandboxRuntime ---

@pytest.fixture
def rig() -> Iterator[RuntimeRig]:
    with short_dir() as root:
        tmux = new_test_tmux()
        rig = runtime_rig(root, tmux, Clock())
        try:
            yield rig
        finally:
            for key in list(rig.runtime.servers):
                with contextlib.suppress(RuntimeUnavailable):
                    rig.runtime.stop(key)
            tmux.kill_server()
            assert tmux.socket_path is not None
            tmux.socket_path.unlink(missing_ok=True)


def codex_spec(rig: RuntimeRig, pin: InstallPin, bead: str = "btq-1") -> LaunchSpec:
    spec = launch_spec(rig, "p-two", bead)
    if bead != "btq-1":
        spec = replace(spec, worktree=bead_worktree(rig, bead))
    return replace(spec, install=pin.install, install_digest=pin.digest)


def tainted_signals(rig: RuntimeRig) -> list[InstallTainted]:
    return [s for s in rig.runtime.signals("alpha") if isinstance(s, InstallTainted)]


@pytest.mark.parametrize("constant", [*BREAKERS, INSTALL_UNPINNED])
def test_each_refusal_through_a_launch(rig: RuntimeRig, constant: str) -> None:
    """The launch is refused with exactly the refusal's text, before any sandbox is made."""
    f = rig.installs["codex"]
    if constant == INSTALL_UNPINNED:
        pin = InstallPin("", "")
    else:
        case = BREAKERS[constant](f, rig.root / "x")
        pin = case.pin
        rig.runtime.c = replace(rig.runtime.c, store_problems=case.problems,
                                adapters={**rig.runtime.c.adapters, "codex": case.adapter})
    with pytest.raises(LaunchFailed) as refused:
        rig.runtime.launch(codex_spec(rig, pin))
    assert str(refused.value) == constant
    assert rig.backend.created == []


@pytest.mark.parametrize("half", ["install", "install_digest"])
def test_half_a_pin_is_unpinned(rig: RuntimeRig, half: str) -> None:
    spec = replace(codex_spec(rig, rig.installs["codex"].pin), **{half: ""})
    with pytest.raises(LaunchFailed) as refused:
        rig.runtime.launch(spec)
    assert str(refused.value) == INSTALL_UNPINNED and rig.backend.created == []


def test_verified_cli_without_a_pin_verifies_the_selection_and_with_one_verifies_the_pin(
        rig: RuntimeRig) -> None:
    f = rig.installs["codex"]
    codex = ADAPTERS["codex"]
    cli = rig.runtime.verified_cli(codex)                       # plan 4b's one-argument form
    assert (cli.install, cli.digest, cli.root) == (f.pin.install, f.pin.digest, f.root)
    assert cli.binary == f.binary and cli.version == "0.160.0"
    other = another_codex(f)
    assert other.pin != f.pin
    pinned = rig.runtime.verified_cli(codex, other.pin)         # not the selection: the pin's install
    assert (pinned.install, pinned.digest, pinned.root) == (other.pin.install, other.pin.digest, other.root)
    he.select(f.config_dir or rig.root, "codex", other.pin)
    assert rig.runtime.verified_cli(codex).install == other.pin.install     # the selection, read again


def test_verified_cli_with_nothing_selected_is_not_selected(rig: RuntimeRig) -> None:
    config_dir = rig.installs["codex"].config_dir
    assert config_dir is not None
    (config_dir / "harnesses.toml").write_text("")
    with pytest.raises(LaunchFailed) as refused:
        rig.runtime.verified_cli(ADAPTERS["codex"])
    assert str(refused.value) == NOT_SELECTED
    (config_dir / "harnesses.toml").write_text("not = [toml")       # unreadable: not a selection either
    with pytest.raises(LaunchFailed) as refused:
        rig.runtime.verified_cli(ADAPTERS["codex"])
    assert str(refused.value) == NOT_SELECTED


def test_a_taint_found_at_launch_signals_every_live_session_on_the_install(rig: RuntimeRig) -> None:
    f = rig.installs["codex"]
    other = another_codex(f)
    on_it = [codex_spec(rig, f.pin), codex_spec(rig, f.pin, "btq-2")]
    elsewhere = codex_spec(rig, other.pin, "btq-3")
    for spec in (*on_it, elsewhere):
        rig.runtime.launch(spec)
    assert tainted_signals(rig) == []
    link_into(f, rig.root / "elsewhere")
    with pytest.raises(LaunchFailed) as refused:
        rig.runtime.launch(codex_spec(rig, f.pin, "btq-4"))
    assert str(refused.value) == verify.INSTALL_CHANGED
    signals = tainted_signals(rig)
    assert sorted((s.session_key, s.generation, s.adapter, s.install) for s in signals) == sorted(
        (spec.session_key, 1, "codex", f.pin.install) for spec in on_it)
    assert tainted_signals(rig) == []                       # drained once
    with pytest.raises(LaunchFailed) as again:              # the marker refuses; no second round of signals
        rig.runtime.verified_cli(ADAPTERS["codex"])
    assert str(again.value) == verify.INSTALL_TAINTED and tainted_signals(rig) == []


def live_on(rig: RuntimeRig, pin: InstallPin, count: int) -> list[str]:
    """`count` running sessions' records on the install, written as a launch writes them (no sandbox)."""
    keys = [f"live-{i:04d}" for i in range(count)]
    for key in keys:
        write_record(rig.runtime.layout(key), SessionRecord(
            key=key, ws="alpha", bead=f"btq-{key}", role="coder", profile="p-two", adapter="codex",
            generation=1, phase=Phase.RUNNING, sandbox=f"sb-{key}", tmux_session=f"wsd-{key}",
            install_name=pin.install, install_digest=pin.digest))
    return keys


def test_the_record_pins_the_generations_install(rig: RuntimeRig) -> None:
    f = rig.installs["codex"]
    spec = codex_spec(rig, f.pin)
    rig.runtime.launch(spec)
    rec = read_record(rig.runtime.layout(spec.session_key))
    assert rec is not None and (rec.install_name, rec.install_digest) == (f.pin.install, f.pin.digest)


def test_every_session_on_a_tainted_install_gets_its_notice_however_many(rig: RuntimeRig) -> None:
    """Codex T8 r1 B1: taint notices are kept apart from the capped usage queue."""
    f = rig.installs["codex"]
    keys = live_on(rig, f.pin, MAX_QUEUED + 1)
    link_into(f, rig.root / "elsewhere")
    with pytest.raises(LaunchFailed):
        rig.runtime.verified_cli(ADAPTERS["codex"])
    assert sorted(s.session_key for s in tainted_signals(rig)) == keys


def test_a_pending_taint_outlasts_any_number_of_usage_reports(rig: RuntimeRig) -> None:
    """Codex T8 r1 B1: a flood of limit reports evicts only older limit reports, never a taint notice."""
    f = rig.installs["codex"]
    [key] = live_on(rig, f.pin, 1)
    link_into(f, rig.root / "elsewhere")
    with pytest.raises(LaunchFailed):
        rig.runtime.verified_cli(ADAPTERS["codex"])
    limited = UsageReport(limited=True, reset_hint=1)
    for _ in range(MAX_QUEUED + 44):
        rig.runtime._report("alpha", key, 1, Capabilities(limit_signal=True), limited)  # noqa: SLF001
    drained = rig.runtime.signals("alpha")
    taints = [s for s in drained if isinstance(s, InstallTainted)]
    assert taints == [InstallTainted(key, 1, "codex", f.pin.install)]
    assert sum(isinstance(s, LimitSeen) for s in drained) == MAX_QUEUED


def test_a_session_tainted_twice_before_a_drain_is_signalled_once(rig: RuntimeRig) -> None:
    f = rig.installs["codex"]
    [key] = live_on(rig, f.pin, 1)
    link_into(f, rig.root / "elsewhere")
    for _ in range(2):
        with pytest.raises(LaunchFailed):
            rig.runtime.verified_cli(ADAPTERS["codex"])
        verify.clear_taint(f.taint_dir, "codex", f.pin.install)     # the next verify walks again
    assert [s.session_key for s in tainted_signals(rig)] == [key]


def test_a_taint_survives_a_restart(rig: RuntimeRig) -> None:
    f = rig.installs["codex"]
    link_into(f, rig.root / "elsewhere")
    with pytest.raises(LaunchFailed):
        rig.runtime.verified_cli(ADAPTERS["codex"])
    restarted = SandboxRuntime(rig.runtime.c, rig.backend, rig.selftest)
    with pytest.raises(LaunchFailed) as refused:
        restarted.launch(codex_spec(rig, f.pin))
    assert str(refused.value) == verify.INSTALL_TAINTED and rig.backend.created == []


def test_the_root_replaced_between_the_check_and_the_bind_fails_the_probe(rig: RuntimeRig) -> None:
    """The launch verifies the install, then binds it; a copy renamed over the root in between keeps every
    byte, but the probe's walk (run against the fake's view: the host path) sees another root."""
    f = rig.installs["codex"]
    spec = codex_spec(rig, f.pin)
    seen: list[str | None] = []

    def swap() -> None:
        parent = f.root.parent
        mode = stat.S_IMODE(parent.stat().st_mode)
        parent.chmod(0o700)
        try:
            shutil.copytree(f.root, parent / ".copy", symlinks=True)
            f.root.rename(parent / ".old")
            (parent / ".copy").rename(f.root)
        finally:
            parent.chmod(mode)
        run = rig.runtime.layout(spec.session_key).run(1)
        seen.append(walk_problem()(json.loads((run / INSTALL_RECORD_INSIDE.name).read_text())))
    rig.selftest.during_exec = swap
    rig.runtime.launch(spec)
    assert seen == ["MI_ROOT_DIFFERS"]


def test_the_trusted_read_verifies_the_current_selection(rig: RuntimeRig) -> None:
    f = rig.installs["codex"]
    repo = git_repo(rig.root / "wsd" / "proj")
    accounts = accounts_at(rig.home)
    ws = WorkstreamSettings("alpha", {"default": repo}, "coder", "p-one", PROFILES, accounts=accounts)
    s = WsdSettings(rig.root / "wsd" / "state", 0.05, 3600, 3, rig.root / "wsd" / "btq", {}, (ws,), accounts,
                    config_dir=f.config_dir)
    reader = build_usage_reader(s, {"HOME": str(rig.home), "PATH": os.defpath}, rig.runtime)
    assert isinstance(reader, HostUsageReader)
    assert reader.cli().install == f.pin.install
    verify.taint(f.taint_dir, "codex", f.pin.install, verify.INSTALL_CHANGED)
    with pytest.raises(LaunchFailed) as refused:
        reader.cli()
    assert str(refused.value) == verify.INSTALL_TAINTED
