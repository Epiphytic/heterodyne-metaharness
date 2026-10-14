"""Plan 4c Task 6 (E10, E11) and Amendment 1 E25: what content an adapter accepts, how it presents an
install's content to the CLI, and how `prepare_install_home` empties the home's exec directories."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import tomllib
from collections.abc import Iterator, Mapping
from pathlib import Path, PurePosixPath

import harness_env as he
import pytest
from sandbox_env import short_dir

from heterodyne.agents import exec_dirs as ed
from heterodyne.agents.base import CONTENT_MALFORMED, OWNS_KEY, UNDECLARED_VERSION, UNKNOWN_KIND, AdapterError
from heterodyne.agents.claude_code import ClaudeCode
from heterodyne.agents.codex import Codex
from heterodyne.agents.registry import ADAPTERS
from heterodyne.harness.resources import store_core as sc
from heterodyne.sandbox.spec import SessionLayout


@pytest.fixture
def layout() -> Iterator[SessionLayout]:
    with short_dir() as root:
        layout = SessionLayout(root / "hz0123456789ab")
        layout.home.mkdir(parents=True)
        yield layout


def item(root: Path, spec: Mapping[str, he.Spec]) -> Path:
    return he.make_tree(root, spec)


PLUGIN_JSON = json.dumps({"name": "p", "version": "1.0.0"}).encode()


# --- check_content ---

@pytest.mark.parametrize(("kind", "spec"), [
    ("plugins", {".claude-plugin/plugin.json": PLUGIN_JSON, "commands/hi.md": b"hi\n"}),
    ("plugins", {".claude-plugin/plugin.json": json.dumps({"name": "p", "mcpServers": {"x": {}}}).encode(),
                 ".mcp.json": json.dumps({"mcpServers": {"y": {"command": "y"}}}).encode()}),
    ("mcp", {"mcp.json": json.dumps({"mcpServers": {"echo": {"command": "echo"}}}).encode()}),
])
def test_claude_accepts_well_formed_content(tmp_path: Path, kind: str, spec: Mapping[str, he.Spec]) -> None:
    ClaudeCode().check_content(kind, item(tmp_path / "it", spec))


@pytest.mark.parametrize(("kind", "spec"), [
    ("skills", {"SKILL.md": b"---\nname: s\n---\n", "scripts/run.sh": he.EXECUTABLE + b"exit 0\n"}),
    ("mcp", {"mcp.toml": b'[mcp_servers.echo]\ncommand = "echo"\n'}),
])
def test_codex_accepts_well_formed_content(tmp_path: Path, kind: str, spec: Mapping[str, he.Spec]) -> None:
    Codex().check_content(kind, item(tmp_path / "it", spec))


@pytest.mark.parametrize(("adapter", "kind", "spec"), [
    ("claude-code", "plugins", {".claude-plugin/plugin.json": PLUGIN_JSON, "hooks/hooks.json": b"{}"}),
    ("claude-code", "plugins", {".claude-plugin/plugin.json": json.dumps(
        {"name": "p", "hooks": {}}).encode()}),
    ("claude-code", "plugins", {".claude-plugin/plugin.json": PLUGIN_JSON, "settings.json": b"{}"}),
    ("claude-code", "plugins", {".claude-plugin/plugin.json": json.dumps(
        {"name": "p", "mcpServers": {"heterodyne": {"command": "x"}}}).encode()}),
    ("claude-code", "plugins", {".claude-plugin/plugin.json": PLUGIN_JSON,
                                ".mcp.json": json.dumps({"mcpServers": {"heterodyne": {}}}).encode()}),
    ("claude-code", "mcp", {"mcp.json": json.dumps(
        {"mcpServers": {"heterodyne": {"command": "x"}}}).encode()}),
    ("claude-code", "mcp", {"mcp.json": json.dumps({"mcpServers": {}, "hooks": {}}).encode()}),
    ("codex", "mcp", {"mcp.toml": b'[mcp_servers.echo]\ncommand = "echo"\n[features]\nplugins = true\n'}),
    ("codex", "mcp", {"mcp.toml": b'check_for_update_on_startup = true\n'}),
    ("codex", "mcp", {"mcp.toml": b'[mcp_servers.heterodyne]\ncommand = "x"\n'}),
])
def test_content_that_sets_an_owned_key_is_refused(tmp_path: Path, adapter: str, kind: str,
                                                   spec: Mapping[str, he.Spec]) -> None:
    with pytest.raises(AdapterError) as refused:
        ADAPTERS[adapter].check_content(kind, item(tmp_path / "it", spec))
    assert str(refused.value) == OWNS_KEY


@pytest.mark.parametrize(("adapter", "kind"), [
    ("claude-code", "skills"), ("claude-code", "instructions"), ("codex", "plugins"),
    ("codex", "instructions"), ("codex", ""), ("codex", "../mcp"),
])
def test_an_undeclared_content_kind_is_refused(tmp_path: Path, adapter: str, kind: str) -> None:
    with pytest.raises(AdapterError) as refused:
        ADAPTERS[adapter].check_content(kind, item(tmp_path / "it", {"SKILL.md": b"x"}))
    assert str(refused.value) == UNKNOWN_KIND


@pytest.mark.parametrize(("adapter", "kind", "spec"), [
    ("claude-code", "plugins", {"commands/hi.md": b"hi\n"}),
    ("claude-code", "plugins", {".claude-plugin/plugin.json": b"[1]"}),
    ("claude-code", "plugins", {".claude-plugin/plugin.json": ("link", "../elsewhere.json")}),
    ("claude-code", "mcp", {"mcp.json": b"{"}),
    ("claude-code", "mcp", {"mcp.json": json.dumps({"mcpServers": {"x": "not a table"}}).encode()}),
    ("claude-code", "mcp", {}),
    ("codex", "skills", {"README.md": b"x"}),
    ("codex", "skills", {"SKILL.md": ("dir",)}),
    ("codex", "mcp", {"mcp.toml": b"x = [\n"}),
    ("codex", "mcp", {"mcp.toml": b'mcp_servers = "x"\n'}),
    ("codex", "mcp", {"mcp.toml": b'[mcp_servers]\necho = 1\n'}),
])
def test_malformed_content_is_refused(tmp_path: Path, adapter: str, kind: str,
                                      spec: Mapping[str, he.Spec]) -> None:
    with pytest.raises(AdapterError) as refused:
        ADAPTERS[adapter].check_content(kind, item(tmp_path / "it", spec))
    assert str(refused.value) == CONTENT_MALFORMED


def test_a_linked_content_item_is_refused(tmp_path: Path) -> None:
    real = item(tmp_path / "real", {"SKILL.md": b"x"})
    (tmp_path / "it").symlink_to(real)
    with pytest.raises(AdapterError) as refused:
        Codex().check_content("skills", tmp_path / "it")
    assert str(refused.value) == CONTENT_MALFORMED


# --- present ---

def install_tree(tmp_path: Path, adapter: str, spec: Mapping[str, he.Spec]) -> tuple[Path, sc.Manifest]:
    """An install root holding `spec`, and its manifest at the adapter's declared version."""
    root = he.make_tree(tmp_path / "install", {"cli/README": b"cli\n", **spec})
    fd = he.open_dir(root)
    try:
        entries = sc.entries_of(fd)
    finally:
        os.close(fd)
    version = ADAPTERS[adapter].version_pin
    return root, sc.Manifest(adapter, version, "t", "cli/bin/x", he.sources(), entries)


CLAUDE_CONTENT: dict[str, he.Spec] = {
    "content/plugins/beta/.claude-plugin/plugin.json": PLUGIN_JSON,
    "content/plugins/alpha/.claude-plugin/plugin.json": PLUGIN_JSON,
    "content/mcp/echo/mcp.json": json.dumps({"mcpServers": {"echo": {"command": "echo"}}}).encode(),
}
CODEX_CONTENT: dict[str, he.Spec] = {
    "content/skills/beta/SKILL.md": b"beta\n",
    "content/skills/alpha/SKILL.md": b"alpha\n",
    "content/mcp/echo/mcp.toml": b'[mcp_servers.echo]\ncommand = "echo"\n',
}


def under(path: str | Path, root: Path) -> bool:
    return Path(os.path.normpath(path)).is_relative_to(root)


def test_presentation_uses_only_paths_inside_the_install(layout: SessionLayout, tmp_path: Path) -> None:
    install, manifest = install_tree(tmp_path / "c", "claude-code", CLAUDE_CONTENT)
    shown = ClaudeCode().present(layout, install, manifest)
    assert shown.argv == ("--plugin-dir", str(install / "content/plugins/alpha"),
                          "--plugin-dir", str(install / "content/plugins/beta"),
                          "--mcp-config", str(install / "content/mcp/echo/mcp.json"), "--strict-mcp-config")
    for flag, value in zip(shown.argv, shown.argv[1:], strict=False):
        if flag in ("--plugin-dir", "--mcp-config"):
            assert under(value, install)
    assert shown.binds == () and dict(shown.env) == {}

    install, manifest = install_tree(tmp_path / "x", "codex", CODEX_CONTENT)
    shown = Codex().present(layout, install, manifest)
    assert shown.argv == () and dict(shown.env) == {}
    assert [(b.source, b.target, b.read_only) for b in shown.binds] == [
        (install / "content/skills/alpha", layout.home / ".codex/skills/alpha", True),
        (install / "content/skills/beta", layout.home / ".codex/skills/beta", True)]
    assert all(under(b.source, install) and b.read_only for b in shown.binds)


def test_claude_always_passes_strict_mcp_config(layout: SessionLayout, tmp_path: Path) -> None:
    install, manifest = install_tree(tmp_path, "claude-code", {})
    assert ClaudeCode().present(layout, install, manifest).argv == ("--strict-mcp-config",)


@pytest.mark.parametrize(("adapter", "spec"), [
    ("claude-code", {"content/skills/s/SKILL.md": b"x"}),
    ("codex", {"content/plugins/p/.claude-plugin/plugin.json": PLUGIN_JSON}),
])
def test_presenting_an_undeclared_kind_is_refused(layout: SessionLayout, tmp_path: Path, adapter: str,
                                                  spec: Mapping[str, he.Spec]) -> None:
    install, manifest = install_tree(tmp_path, adapter, spec)
    with pytest.raises(AdapterError) as refused:
        ADAPTERS[adapter].present(layout, install, manifest)
    assert str(refused.value) == UNKNOWN_KIND


def test_presenting_content_that_owns_a_key_is_refused(layout: SessionLayout, tmp_path: Path) -> None:
    install, manifest = install_tree(tmp_path, "claude-code", {
        "content/plugins/p/.claude-plugin/plugin.json": PLUGIN_JSON,
        "content/plugins/p/settings.json": b"{}"})
    with pytest.raises(AdapterError) as refused:
        ClaudeCode().present(layout, install, manifest)
    assert str(refused.value) == OWNS_KEY


def test_presenting_an_undeclared_version_is_refused(layout: SessionLayout, tmp_path: Path) -> None:
    install, manifest = install_tree(tmp_path, "codex", {})
    other = sc.Manifest("codex", "0.161.0", manifest.target, manifest.entry_point, manifest.sources,
                        manifest.entries)
    with pytest.raises(AdapterError) as refused:
        Codex().present(layout, install, other)
    assert str(refused.value) == UNDECLARED_VERSION


def test_codex_merges_content_mcp_servers_before_the_launchers_tables(layout: SessionLayout,
                                                                      tmp_path: Path) -> None:
    install, _ = install_tree(tmp_path, "codex", {
        **CODEX_CONTENT,
        "content/mcp/more/mcp.toml": b'[mcp_servers.more]\ncommand = "more"\nargs = ["-x"]\n'})
    Codex().prepare_install_home(layout, tmp_path / "wt", install)
    text = (layout.home / ".codex" / "config.toml").read_text()
    config = tomllib.loads(text)
    assert config["mcp_servers"] == {"echo": {"command": "echo"}, "more": {"command": "more", "args": ["-x"]}}
    assert text.index("[mcp_servers") < text.index("[projects") < text.index("[features]")


def test_codex_refuses_content_servers_that_collide(layout: SessionLayout, tmp_path: Path) -> None:
    install, _ = install_tree(tmp_path, "codex", {
        **CODEX_CONTENT, "content/mcp/again/mcp.toml": b'[mcp_servers.echo]\ncommand = "other"\n'})
    with pytest.raises(AdapterError) as refused:
        Codex().prepare_install_home(layout, tmp_path / "wt", install)
    assert str(refused.value) == CONTENT_MALFORMED


# --- the exec directories (E25) ---

TMP, PACKAGES = PurePosixPath(".codex/tmp"), PurePosixPath(".codex/packages")


def test_the_adapters_declare_their_exec_dirs() -> None:
    assert Codex().empty_read_only == (TMP, PACKAGES)
    assert ClaudeCode().empty_read_only == ()


def test_install_home_creates_missing_exec_dirs_empty(layout: SessionLayout, tmp_path: Path) -> None:
    assert Codex().prepare_install_home(layout, tmp_path / "wt", tmp_path) == ()
    for rel in (TMP, PACKAGES):
        path = layout.home / rel
        assert path.is_dir() and not path.is_symlink() and not any(path.iterdir())
        assert path.stat().st_mode & 0o777 == 0o700
    assert ClaudeCode().prepare_install_home(layout, tmp_path / "wt", tmp_path) == ()


def test_install_home_clears_leftover_exec_dir_content(layout: SessionLayout, tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    (target / "keep").write_text("keep")
    he.make_tree(layout.home / PACKAGES, {"app-server-daemon/releases/0.160.0/bin/codex": b"#!/bin/sh\n",
                                          "stray": b"12345"})
    he.make_tree(layout.home / TMP, {"arg0/codex-arg0abc/apply_patch": ("link", str(target))})
    cleared = Codex().prepare_install_home(layout, tmp_path / "wt", tmp_path)
    assert [c.path for c in cleared] == [TMP, PACKAGES]
    assert (cleared[0].files, cleared[0].links, cleared[0].dirs) == (0, 1, 2)
    assert (cleared[1].files, cleared[1].links, cleared[1].dirs, cleared[1].bytes) == (2, 0, 4, 15)
    for rel in (TMP, PACKAGES):
        assert not any((layout.home / rel).iterdir())
    assert (target / "keep").read_text() == "keep"


@pytest.mark.parametrize("rel", [TMP, PACKAGES])
@pytest.mark.parametrize("what", ["link", "file", "dangling", "fifo"])
def test_install_home_refuses_an_exec_dir_that_is_not_a_directory(layout: SessionLayout, tmp_path: Path,
                                                                  rel: PurePosixPath, what: str) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    path = layout.home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if what == "link":
        path.symlink_to(target)
    elif what == "dangling":
        path.symlink_to(tmp_path / "nowhere")
    elif what == "fifo":
        os.mkfifo(path)
    else:
        path.write_text("x")
    with pytest.raises(AdapterError) as refused:
        Codex().prepare_install_home(layout, tmp_path / "wt", tmp_path)
    assert str(refused.value) == f"the home's {rel} isn't a directory"
    assert str(refused.value) == ed.EXEC_DIR_NOT_A_DIRECTORY.format(path=rel)
    assert not any(target.iterdir())


def test_a_linked_codex_dir_refuses_before_the_exec_dirs(layout: SessionLayout, tmp_path: Path) -> None:
    target = tmp_path / "outside"
    (target / "tmp").mkdir(parents=True)
    (target / "tmp" / "keep").write_text("x")
    (layout.home / ".codex").symlink_to(target)
    with pytest.raises(AdapterError):
        Codex().prepare_install_home(layout, tmp_path / "wt", tmp_path)
    assert (target / "tmp" / "keep").exists()


def clear(home: Path, rel: PurePosixPath = TMP, **kw: object) -> tuple[ed.Cleared, ...]:
    return ed.prepare(home, (rel,), **kw)  # type: ignore[arg-type]


def test_clear_ends_with_an_empty_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    he.make_tree(home / TMP, {"a/b/c/d": b"x", "e": b"", "f": ("link", "a"), "g": ("dir",)})
    (cleared,) = clear(home)
    assert (home / TMP).is_dir() and not any((home / TMP).iterdir())
    assert (cleared.files, cleared.links, cleared.dirs, cleared.bytes) == (2, 1, 4, 1)
    assert clear(home) == ()                                # an empty directory needs no clearing


def test_clear_refuses_a_tree_past_its_bounds(tmp_path: Path) -> None:
    home = tmp_path / "home"
    deep = "/".join(["d"] * (ed.MAX_DEPTH + 1))
    he.make_tree(home / TMP, {deep: ("dir",)})
    with pytest.raises(AdapterError) as refused:
        clear(home)
    assert str(refused.value) == ed.EXEC_DIR_TOO_LARGE.format(path=TMP)
    assert (home / TMP / deep).is_dir()                     # the scan deletes nothing

    shutil.rmtree(home / TMP)
    (home / TMP).mkdir()
    for i in range(ed.MAX_ENTRIES + 1):
        (home / TMP / f"f{i}").touch()
    with pytest.raises(AdapterError) as refused:
        clear(home)
    assert str(refused.value) == ed.EXEC_DIR_TOO_LARGE.format(path=TMP)
    assert len(list((home / TMP).iterdir())) == ed.MAX_ENTRIES + 1


def test_clear_at_its_bounds_is_accepted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    deep = "/".join(["d"] * ed.MAX_DEPTH)
    he.make_tree(home / TMP, {deep: ("dir",)})
    (cleared,) = clear(home)
    assert cleared.dirs == ed.MAX_DEPTH


def test_clear_returns_a_bounded_summary_without_contents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    secret = b"sk-not-a-real-secret"
    spec: dict[str, he.Spec] = {f"n{i:02}": secret for i in range(20)}
    spec["\x1bctl\nname" + "x" * 300] = b""
    he.make_tree(home / TMP, spec)
    (cleared,) = clear(home)
    assert len(cleared.names) == ed.MAX_NAMES == 16
    assert all(len(n) <= ed.MAX_NAME_CHARS == 128 for n in cleared.names)
    assert all(n.isprintable() for n in cleared.names)
    assert cleared.names[0].startswith("\\x1bctl\\x0aname")      # sorted: the escape sorts first
    assert "secret" not in repr(cleared)
    assert (cleared.files, cleared.bytes) == (21, 20 * len(secret))


def test_a_directory_swapped_for_a_link_during_the_clear_stops_it(tmp_path: Path) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary").write_text("canary")
    he.make_tree(home / TMP, {"sub/file": b"x"})

    def swap() -> None:
        shutil.rmtree(home / TMP / "sub")
        (home / TMP / "sub").symlink_to(outside)
    with pytest.raises(AdapterError) as refused:
        clear(home, between=swap)
    assert str(refused.value) == ed.EXEC_DIR_NOT_CLEARED.format(path=TMP)
    assert (outside / "canary").read_text() == "canary"


@pytest.mark.parametrize("change", ["appear", "vanish"])
def test_an_entry_that_appears_or_vanishes_stops_the_clear(tmp_path: Path, change: str) -> None:
    home = tmp_path / "home"
    he.make_tree(home / TMP, {"a": b"x", "b/c": b"y"})

    def act() -> None:
        if change == "appear":
            (home / TMP / "b" / "new").write_text("n")
        else:
            (home / TMP / "b" / "c").unlink()
    with pytest.raises(AdapterError) as refused:
        clear(home, between=act)
    assert str(refused.value) == ed.EXEC_DIR_NOT_CLEARED.format(path=TMP)


# --- mounts, in a private user and mount namespace ---

def _userns() -> bool:
    if shutil.which("unshare") is None:
        return False
    try:
        subprocess.run(["unshare", "-rm", "true"], check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


needs_userns = pytest.mark.skipif(not _userns(), reason="unprivileged user namespaces aren't available")

CHILD = textwrap.dedent("""
    import json, subprocess, sys
    from pathlib import Path, PurePosixPath
    from heterodyne.agents import exec_dirs as ed
    from heterodyne.agents.base import AdapterError
    home, rel, mode, kind, at, outside = sys.argv[1:7]
    def mount(where):
        cmd = (["mount", "-t", "tmpfs", "none", where] if kind == "tmpfs"
               else ["mount", "--bind", outside, where])
        subprocess.run(cmd, check=True)
    if mode == "before":
        mount(at)
    hook = (lambda: mount(at)) if mode == "between" else None
    try:
        ed.prepare(Path(home), (PurePosixPath(rel),), between=hook)
        print(json.dumps("cleared"))
    except AdapterError as exc:
        print(json.dumps(str(exc)))
""")


def run_child(home: Path, rel: PurePosixPath, mode: str, kind: str, at: Path, outside: Path) -> str:
    done = subprocess.run(["unshare", "-rm", sys.executable, "-c", CHILD, str(home), str(rel), mode, kind,
                           str(at), str(outside)], capture_output=True, text=True, timeout=120, check=True)
    return str(json.loads(done.stdout.strip().splitlines()[-1]))


def mount_fixture(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    he.make_tree(home / TMP, {"keep": b"k", "sub/deeper/file": b"f", "sub/other": b"o"})
    outside = tmp_path / "outside"
    he.make_tree(outside, {"canary": b"canary", "deeper/file": b"f"})
    return home, outside


def intact(home: Path, outside: Path) -> bool:
    return ((outside / "canary").read_bytes() == b"canary" and (home / TMP / "keep").exists()
            and (home / TMP / "sub" / "deeper" / "file").exists() and (home / TMP / "sub" / "other").exists())


@needs_userns
@pytest.mark.parametrize("kind", ["tmpfs", "bind"])
def test_clear_refuses_a_mount_at_the_exec_dir(tmp_path: Path, kind: str) -> None:
    home, outside = mount_fixture(tmp_path)
    assert run_child(home, TMP, "before", kind, home / TMP, outside) == ed.EXEC_DIR_CROSSES_MOUNT.format(
        path=TMP)
    assert intact(home, outside)


@needs_userns
@pytest.mark.parametrize("kind", ["tmpfs", "bind"])
def test_clear_refuses_a_mount_below_the_exec_dir(tmp_path: Path, kind: str) -> None:
    home, outside = mount_fixture(tmp_path)
    assert run_child(home, TMP, "before", kind, home / TMP / "sub", outside) == \
        ed.EXEC_DIR_CROSSES_MOUNT.format(path=TMP)
    assert intact(home, outside)


@needs_userns
@pytest.mark.parametrize("kind", ["tmpfs", "bind"])
def test_a_mount_added_between_the_scan_and_the_delete_stops_the_clear(tmp_path: Path, kind: str) -> None:
    home, outside = mount_fixture(tmp_path)
    assert run_child(home, TMP, "between", kind, home / TMP / "sub", outside) == \
        ed.EXEC_DIR_NOT_CLEARED.format(path=TMP)
    assert (outside / "canary").read_bytes() == b"canary" and (outside / "deeper" / "file").exists()
