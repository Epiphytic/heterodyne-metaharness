import re
from dataclasses import replace
from pathlib import Path

import pytest

from heterodyne.agents.base import Cli
from heterodyne.agents.claude_code import ClaudeCode
from heterodyne.agents.codex import Codex
from heterodyne.sandbox.spec import (
    INSIDE_LOGIN_DIR,
    LAUNCHER_ENV,
    NAME,
    RUN_INSIDE,
    SOCKET_INSIDE,
    AgentFacts,
    Bind,
    Protected,
    SessionLayout,
    SpecInput,
    SpecRefused,
    build_spec,
    check_credentials,
    sandbox_name,
    short_id,
)

KEY = "alpha/bd-1/coder/p-two"


def facts(root: Path, cli_root: Path | None = None) -> AgentFacts:
    """A Codex-like adapter's facts, its CLI in an install store outside every login directory (or at
    `cli_root`)."""
    cli = (cli_root or root / "store" / "codex" / "0.160.0-0123456789ab") / "bin"
    cli.mkdir(parents=True, exist_ok=True)
    (cli / "codex").write_text("")
    layout = SessionLayout.at(root / "sessions", KEY)
    return AgentFacts(cli_root=cli.parent, cli_binary=cli / "codex", config_var="CODEX_HOME",
                      config_dir=layout.home / ".codex", login_names=("auth.json",),
                      model_hosts=("chatgpt.com",), extra_env={},
                      binds=(Bind(layout.bridge, Path("/run/hz-bridge"), False),),
                      read_write=("/run/hz-bridge",))


def spec_input(root: Path, role: str = "coder", **kw: object) -> SpecInput:
    login = root / "home" / ".codex" / "auth.json"
    login.parent.mkdir(parents=True, exist_ok=True)
    login.write_text("{}")
    worktree = root / "repo-btq-bd-1"
    worktree.mkdir(exist_ok=True)
    base = SpecInput(key=KEY, generation=1, role=role, layout=SessionLayout.at(root / "sessions", KEY),
                     worktree=worktree, agent=facts(root), login_files=(login,), extra_egress=(),
                     extra_ro_mounts=(), probe_allowed_host="api.openai.com", uid=1000, gid=1000)
    return replace(base, **kw)  # type: ignore[arg-type]


def protected(root: Path) -> Protected:
    other = root / "home" / ".codex-b"
    chosen = root / "home" / ".codex"
    return Protected(chosen_dir=chosen, chosen_files=(chosen / "auth.json",),
                     other_dirs=(other,), other_files=(other / "auth.json",))


def test_names_are_short_and_deterministic() -> None:
    assert short_id(KEY) == short_id(KEY) and re.fullmatch(r"hz[0-9a-f]{12}", short_id(KEY))
    assert NAME.fullmatch(sandbox_name(KEY, 1)) and len(sandbox_name(KEY, 9999)) <= 19
    assert sandbox_name(KEY, 2) != sandbox_name(KEY, 1)
    for bad in (0, 10000):
        with pytest.raises(SpecRefused):
            sandbox_name(KEY, bad)


def test_layout(tmp_path: Path) -> None:
    lay = SessionLayout.at(tmp_path, KEY)
    assert lay.root == tmp_path / short_id(KEY)
    assert lay.socket(3) == lay.root / "r3" / "s.sock" and lay.probe_socket(3) == lay.root / "r3" / "p.sock"
    assert lay.events(3) == lay.root / "r3-events.jsonl" and lay.oa_canary.parent == lay.root
    assert lay.daemon == lay.bridge / "daemon" and lay.git == lay.root / "git"


def test_git_binds_keep_their_modes(tmp_path: Path) -> None:
    private = Bind(tmp_path / "git", tmp_path / "repo" / ".git" / "worktrees" / "wt", False)
    objects = Bind(tmp_path / "repo" / ".git" / "objects", tmp_path / "repo" / ".git" / "objects", True)
    i = spec_input(tmp_path)
    pointer = Bind(i.worktree / ".git", i.worktree / ".git", True)       # a file over the writable worktree
    spec = build_spec(replace(i, git_binds=(private, objects, pointer)))
    assert {private, objects, pointer} <= set(spec.binds)
    assert spec.binds.index(pointer) > spec.binds.index(Bind(i.worktree, i.worktree, False))
    assert str(private.target) in spec.read_write
    assert {str(objects.target), str(pointer.target)} <= set(spec.read_only)
    assert str(objects.target) not in spec.read_write and str(pointer.target) not in spec.read_write


def adapter_input(tmp_path: Path, adapter: Codex | ClaudeCode) -> SpecInput:
    """spec_input with the adapter's own facts and login file."""
    login = tmp_path / "home" / adapter.config_subdir / adapter.login_names[0]
    login.parent.mkdir(parents=True, exist_ok=True)
    login.write_text("{}")
    root = tmp_path / "store" / adapter.name / "install"
    root.mkdir(parents=True)
    layout = SessionLayout.at(tmp_path / "sessions", KEY)
    agent = adapter.facts(layout, Cli(root / "cli", root, "0"), 1000)
    return spec_input(tmp_path, agent=agent, login_files=(login,))


def test_the_codex_home_binds_tmp_and_packages_to_one_empty_read_only_dir(tmp_path: Path) -> None:
    """E25: Codex's exec directories in the home are each covered by the generation's one empty directory,
    read-only, bound after the home so it lies over it."""
    i = adapter_input(tmp_path, Codex())
    spec = build_spec(i)
    targets = [b.target for b in spec.binds]
    empties = [b for b in spec.binds if b.source == i.layout.empty(1)]
    assert [(b.target, b.read_only) for b in empties] == [(i.layout.home / ".codex" / "tmp", True),
                                                          (i.layout.home / ".codex" / "packages", True)]
    assert all(targets.index(b.target) > targets.index(i.layout.home) for b in empties)
    assert i.layout.empty(1) == i.layout.run(1) / "empty" and not any(b.install for b in empties)


def test_claude_code_gets_no_empty_binds(tmp_path: Path) -> None:
    i = adapter_input(tmp_path, ClaudeCode())
    assert not [b for b in build_spec(i).binds if b.source == i.layout.empty(1)]


def test_only_the_install_bind_is_marked(tmp_path: Path) -> None:
    """E24: `driver_config` writes S9-8's fields on the bind marked install, and build_spec marks exactly
    one, the CLI's install root, read-only at its host path."""
    i = adapter_input(tmp_path, Codex())
    [install] = [b for b in build_spec(i).binds if b.install]
    assert (install.source, install.target, install.read_only) == (i.agent.cli_root, i.agent.cli_root, True)


def test_coder_spec(tmp_path: Path) -> None:
    i = spec_input(tmp_path)
    spec = build_spec(i)
    by_target = {b.target: b for b in spec.binds}
    assert not by_target[i.worktree].read_only and not by_target[i.layout.home].read_only
    assert by_target[RUN_INSIDE].source == i.layout.run(1) and by_target[RUN_INSIDE].read_only
    assert by_target[i.agent.cli_root].read_only and spec.install == i.agent.cli_root
    [login] = spec.logins
    assert login.target == i.layout.home / ".codex" / "auth.json" and login.read_only
    assert login.source == (tmp_path / "home" / ".codex" / "auth.json").resolve()
    assert str(i.worktree) in spec.read_write and str(i.layout.home) in spec.read_write
    assert {e.name: e.hosts for e in spec.egress} == {"model": ("chatgpt.com",),
                                                     "probe_control": ("api.openai.com",)}
    assert {e.name: e.binaries for e in spec.egress}["probe_control"] == ("/usr/bin/curl",)
    assert set(spec.env) <= LAUNCHER_ENV and spec.env["HZ_SESSION_SOCKET"] == str(SOCKET_INSIDE)
    assert spec.env["CODEX_HOME"] == str(i.layout.home / ".codex")
    assert spec.workdir == i.worktree


def test_reviewer_worktree_is_read_only(tmp_path: Path) -> None:
    i = spec_input(tmp_path, role="reviewer")
    spec = build_spec(i)
    assert {b.target: b for b in spec.binds}[i.worktree].read_only
    assert str(i.worktree) in spec.read_only and str(i.worktree) not in spec.read_write


def test_extra_egress_and_mounts(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    spec = build_spec(spec_input(tmp_path, extra_egress=("github.com",), extra_ro_mounts=(shared,)))
    assert {e.name: e.hosts for e in spec.egress}["extra"] == ("github.com",)
    assert Bind(shared, shared, True) in spec.binds


@pytest.mark.parametrize("change, needle", [
    ({"extra_egress": ("mcp-proxy.anthropic.com",)}, "denied host"),
    ({"generation": 0}, "generation"),
])
def test_refusals(tmp_path: Path, change: dict[str, object], needle: str) -> None:
    with pytest.raises(SpecRefused, match=needle):
        build_spec(spec_input(tmp_path, "coder", **change))


def test_the_updater_variables_pass_as_extra_env(tmp_path: Path) -> None:
    i = spec_input(tmp_path)
    agent = replace(i.agent, extra_env={"DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1"})
    assert build_spec(replace(i, agent=agent)).env["DISABLE_UPDATES"] == "1"


@pytest.mark.parametrize("name", ["DISABLE_TELEMETRY", "LD_PRELOAD", "CODEX_CI", "DISABLE_UPDATES_X"])
def test_any_other_new_extra_env_key_is_refused(tmp_path: Path, name: str) -> None:
    i = spec_input(tmp_path)
    agent = replace(i.agent, extra_env={"DISABLE_UPDATES": "1", name: "1"})
    with pytest.raises(SpecRefused, match="outside the launcher's allowlist"):
        build_spec(replace(i, agent=agent))


def test_long_socket_path_is_refused(tmp_path: Path) -> None:
    deep = tmp_path / ("d" * 90)
    i = spec_input(tmp_path)
    with pytest.raises(SpecRefused, match="socket path"):
        build_spec(replace(i, layout=SessionLayout.at(deep, KEY)))


def test_missing_login_is_refused(tmp_path: Path) -> None:
    i = spec_input(tmp_path)
    (tmp_path / "home" / ".codex" / "auth.json").unlink()
    with pytest.raises(SpecRefused, match="login file"):
        build_spec(i)


def test_credentials_pass_with_the_cli_in_the_install_store(tmp_path: Path) -> None:
    check_credentials(build_spec(spec_input(tmp_path)), protected(tmp_path), tmp_path / "home")


@pytest.mark.parametrize("route", ["direct", "linked"])
def test_a_cli_inside_the_chosen_login_dir_is_refused(tmp_path: Path, route: str) -> None:
    """Plan 4c: the CLI comes from the install store, so nothing is bound from the chosen account's login
    directory, the old ~/.codex/packages install included, whether named directly or through a link."""
    inside = tmp_path / "home" / ".codex" / "packages" / "rel"
    root = inside
    if route == "linked":
        facts(tmp_path, inside)
        root = tmp_path / "innocent"
        root.symlink_to(inside)
    spec = build_spec(spec_input(tmp_path, agent=facts(tmp_path, root)))
    with pytest.raises(SpecRefused) as refused:
        check_credentials(spec, protected(tmp_path), tmp_path / "home")
    assert str(refused.value) == INSIDE_LOGIN_DIR


@pytest.mark.parametrize("source", ["home/.codex-b", "home", "home/.codex", "."])
def test_a_bind_exposing_a_login_or_the_home_is_refused(tmp_path: Path, source: str) -> None:
    spec = build_spec(spec_input(tmp_path))
    (tmp_path / "home" / ".codex-b").mkdir(parents=True, exist_ok=True)
    src = (tmp_path / source).resolve()
    bad = replace(spec, binds=(*spec.binds, Bind(src, Path("/mnt/x"), True)))
    with pytest.raises(SpecRefused):
        check_credentials(bad, protected(tmp_path), tmp_path / "home")


def test_a_source_inside_another_accounts_login_dir_is_refused(tmp_path: Path) -> None:
    """r15 §7: a source in any other account's login directory refuses the launch, even one that holds
    no login file. The chosen account's own directory holds none either (`INSIDE_LOGIN_DIR`)."""
    inner = tmp_path / "home" / ".codex-b" / "packages"
    inner.mkdir(parents=True)
    spec = build_spec(spec_input(tmp_path))
    bad = replace(spec, binds=(*spec.binds, Bind(inner, Path("/mnt/x"), True)))
    with pytest.raises(SpecRefused, match="another account's login directory"):
        check_credentials(bad, protected(tmp_path), tmp_path / "home")


def test_a_symlinked_route_to_another_login_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "home" / ".codex-b"
    other.mkdir(parents=True)
    (other / "auth.json").write_text("{}")
    link = tmp_path / "innocent"
    link.symlink_to(other)
    spec = build_spec(spec_input(tmp_path))
    bad = replace(spec, binds=(*spec.binds, Bind(link, Path("/mnt/x"), True)))
    with pytest.raises(SpecRefused):
        check_credentials(bad, protected(tmp_path), tmp_path / "home")


def test_login_binds_must_be_exactly_the_chosen_files_read_only(tmp_path: Path) -> None:
    spec = build_spec(spec_input(tmp_path))
    [login] = spec.logins
    for logins in ((), (replace(login, read_only=False),), (login, login)):
        with pytest.raises(SpecRefused, match="login binds"):
            check_credentials(replace(spec, logins=logins), protected(tmp_path), tmp_path / "home")


def _other_with_outward_link(tmp_path: Path) -> Path:
    """Another account's login directory holding `packages`, a symlink out of it to an innocent directory."""
    other = tmp_path / "home" / ".codex-b"
    other.mkdir(parents=True)
    (other / "auth.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (other / "packages").symlink_to(outside)
    return other


def test_a_route_through_another_login_dir_is_refused_even_if_it_leads_out(tmp_path: Path) -> None:
    """The source's own path and its canonical one both lie outside `.codex-b`, but the kernel walks
    through it: `alias` resolves into it, and only then does `packages` lead out."""
    other = _other_with_outward_link(tmp_path)
    (tmp_path / "alias").symlink_to(other)
    spec = build_spec(spec_input(tmp_path))
    bad = replace(spec, binds=(*spec.binds, Bind(tmp_path / "alias" / "packages", Path("/mnt/x"), True)))
    with pytest.raises(SpecRefused, match="another account's login directory"):
        check_credentials(bad, protected(tmp_path), tmp_path / "home")


def test_dot_dot_is_resolved_against_the_resolved_prefix(tmp_path: Path) -> None:
    """`up/..` is `.codex-b` to the kernel, since `up` resolves into it; lexically it is `tmp_path`."""
    other = _other_with_outward_link(tmp_path)
    (other / "inner").mkdir()
    (tmp_path / "up").symlink_to(other / "inner")
    spec = build_spec(spec_input(tmp_path))
    source = tmp_path / "up" / ".." / "packages"
    bad = replace(spec, binds=(*spec.binds, Bind(source, Path("/mnt/x"), True)))
    with pytest.raises(SpecRefused, match="another account's login directory"):
        check_credentials(bad, protected(tmp_path), tmp_path / "home")


def test_dot_dot_out_of_a_plain_directory_passes(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    spec = build_spec(spec_input(tmp_path))
    ok = replace(spec, binds=(*spec.binds, Bind(tmp_path / "a" / ".." / "b", Path("/mnt/x"), True)))
    check_credentials(ok, protected(tmp_path), tmp_path / "home")


def test_a_symlink_loop_in_a_source_is_refused(tmp_path: Path) -> None:
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    spec = build_spec(spec_input(tmp_path))
    bad = replace(spec, binds=(*spec.binds, Bind(tmp_path / "loop", Path("/mnt/x"), True)))
    with pytest.raises(SpecRefused, match="symbolic links"):
        check_credentials(bad, protected(tmp_path), tmp_path / "home")


@pytest.mark.parametrize("path", [
    "/", "/proc", "/proc/self", "/proc/1/task", "/proc/./sys/../self",
    "//proc",                                   # POSIX keeps a leading "//"; refused, not folded
    "/var/run/../proc",                         # /var/run -> /run in the image: the kernel reaches /proc
    "/dev/fd/..", "/dev/fd", "/dev/fd/3", "/dev/stdin", "/dev/stderr", "/etc/mtab",   # links into /proc
    "/w/./x", "/w//x", "/w/x/", "w/x", "",                                            # non-canonical
])
def test_nothing_writable_reaches_proc(tmp_path: Path, path: str) -> None:
    """Landlock grants the union of its rules: a writable / or /proc subtree would undo /proc's read-only
    rule, which probe protection rests on (D23, Task 9B)."""
    agent = replace(facts(tmp_path), read_write=(path,))
    with pytest.raises(SpecRefused, match="^a writable path would make /proc writable$"):
        build_spec(spec_input(tmp_path, agent=agent))


def test_proc_is_read_only(tmp_path: Path) -> None:
    s = build_spec(spec_input(tmp_path))
    assert "/proc" in s.read_only
    assert not any(p == "/proc" or p.startswith("/proc/") for p in s.read_write)


def test_canonical_writable_paths_beside_proc_are_kept(tmp_path: Path) -> None:
    agent = replace(facts(tmp_path), read_write=("/run/hz-bridge", "/procs", "/dev/fdx", "/dev/pts"))
    kept = build_spec(spec_input(tmp_path, agent=agent)).read_write
    assert {"/run/hz-bridge", "/procs", "/dev/fdx"} <= set(kept)


@pytest.mark.parametrize("adapter", [ClaudeCode(), Codex()])
@pytest.mark.parametrize("role", ["coder", "reviewer"])
def test_host_create_ledger_is_outside_every_build_spec_bind(
        tmp_path: Path, adapter: Codex | ClaudeCode, role: str) -> None:
    i = replace(adapter_input(tmp_path, adapter), role=role,
                layout=SessionLayout.at(tmp_path / "state" / "wsd" / "sessions", KEY))
    ledger = tmp_path / "state" / "creates"
    ledger.mkdir(parents=True)
    for bind in build_spec(i).binds:
        assert not ledger.is_relative_to(bind.source)
