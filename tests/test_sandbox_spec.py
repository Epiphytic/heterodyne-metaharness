import re
from dataclasses import replace
from pathlib import Path

import pytest

from heterodyne.sandbox.spec import (
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


def facts(root: Path) -> AgentFacts:
    cli = root / "home" / ".codex" / "packages" / "rel" / "bin"
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


def test_coder_spec(tmp_path: Path) -> None:
    i = spec_input(tmp_path)
    spec = build_spec(i)
    by_target = {b.target: b for b in spec.binds}
    assert not by_target[i.worktree].read_only and not by_target[i.layout.home].read_only
    assert by_target[RUN_INSIDE].source == i.layout.run(1) and by_target[RUN_INSIDE].read_only
    assert by_target[i.agent.cli_root].read_only
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


def test_credentials_pass_with_cli_inside_the_chosen_login_dir(tmp_path: Path) -> None:
    spec = build_spec(spec_input(tmp_path))      # the CLI root is under ~/.codex/packages, as installed
    check_credentials(spec, protected(tmp_path), tmp_path / "home")


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
    no login file. Only the chosen account's own directory may hold a bound source (the Codex CLI)."""
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
