import hashlib
import os
import shutil
import subprocess
import zlib
from pathlib import Path
from typing import Any

import pytest
from wsd_env import git_repo

from heterodyne.sandbox import sessiongit
from heterodyne.wsd import gitwip

BRANCH = "btq/btq-1"
IDENT = ("-c", "user.name=agent", "-c", "user.email=agent@example.org")


@pytest.fixture(autouse=True)
def host_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No system or global config of the machine running the tests: a test that wants one plants it."""
    home = tmp_path / "host-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("XDG_CONFIG_HOME", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL"):
        monkeypatch.delenv(name, raising=False)


def linked(tmp_path: Path) -> tuple[Path, Path]:
    repo = git_repo(tmp_path / "repo")
    worktree = tmp_path / "wt"
    gitwip.git(repo, "worktree", "add", "-q", "-b", BRANCH, str(worktree))
    return repo, worktree


def inside(private: Path, worktree: Path, *args: str) -> str:
    """git as the agent runs it: the worktree's `.git` file finds the private directory at its bind."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {"GIT_DIR": str(private), "GIT_WORK_TREE": str(worktree)}
    return subprocess.run(["git", *args], cwd=worktree, env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


def tip(repo: Path, ref: str) -> str:
    return gitwip.git(repo, "rev-parse", ref)


def test_pin_takes_the_git_dir_from_the_repository_not_the_dot_git_file(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    marker = tmp_path / "ran"
    evil = git_repo(tmp_path / "evil")
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o700)
    gitwip.git(evil, "config", "core.fsmonitor", str(hook))
    (worktree / ".git").write_text(f"gitdir: {evil / '.git'}\n")        # the agent redirects it
    p = gitwip.pin(repo, worktree, BRANCH)
    assert p.git_dir == (repo / ".git" / "worktrees" / "wt").resolve()
    assert p.common == (repo / ".git").resolve()
    sha = gitwip.wip_commit(p, "m1", "test")
    assert tip(repo, BRANCH) == sha and not marker.exists()


def test_pin_refuses_a_head_moved_off_the_bead_branch(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    (repo / ".git" / "worktrees" / "wt" / "HEAD").write_text("ref: refs/heads/main\n")
    with pytest.raises(gitwip.GitFailed, match="bead branch"):
        gitwip.pin(repo, worktree, BRANCH)


@pytest.mark.parametrize("name", ["HEAD", "commondir", "gitdir"])
def test_pin_refuses_linked_metadata(tmp_path: Path, name: str) -> None:
    repo, worktree = linked(tmp_path)
    meta = repo / ".git" / "worktrees" / "wt" / name
    copy = tmp_path / f"copy-{name}"
    copy.write_text(meta.read_text())
    meta.unlink()
    meta.symlink_to(copy)
    with pytest.raises(gitwip.GitFailed):
        gitwip.pin(repo, worktree, BRANCH)


def test_pin_refuses_a_linked_worktree_directory(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    real = repo / ".git" / "worktrees" / "wt"
    real.rename(tmp_path / "moved")
    real.symlink_to(tmp_path / "moved")
    with pytest.raises(gitwip.GitFailed):
        gitwip.pin(repo, worktree, BRANCH)


def test_pin_refuses_per_worktree_config(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    gitwip.git(repo, "config", "extensions.worktreeConfig", "true")
    with pytest.raises(gitwip.GitFailed, match="per-worktree"):
        gitwip.pin(repo, worktree, BRANCH)


def test_the_main_worktree_is_never_seeded(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    gitwip.git(repo, "checkout", "-q", "-b", BRANCH)
    p = gitwip.pin(repo, repo, BRANCH)                  # plan 3's callers may still commit in it
    with pytest.raises(gitwip.GitFailed, match="main worktree"):
        sessiongit.seed(p, tmp_path / "private", read_only=False)


def test_a_session_commit_lands_on_the_bead_branch_alone(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    rw, objects, pointer = sessiongit.seed(p, private, read_only=False)
    assert (rw.source, rw.target, rw.read_only) == (private, p.git_dir, False)
    assert (pointer.source, pointer.target, pointer.read_only) == (p.work_tree / ".git",) * 2 + (True,)
    assert (objects.source, objects.target, objects.read_only) == (p.common / "objects",) * 2 + (True,)
    main = tip(repo, "main")
    assert inside(private, worktree, "rev-parse", "main") == main            # the snapshot is readable
    (worktree / "a.txt").write_text("work")
    inside(private, worktree, "add", "a.txt")
    inside(private, worktree, *IDENT, "commit", "-q", "-m", "agent work")
    made = inside(private, worktree, "rev-parse", "HEAD")
    inside(private, worktree, "update-ref", "refs/heads/main", made)       # moves only its own copy
    (worktree / ".git").write_text("gitdir: /nowhere\n")
    assert sessiongit.land(p, private) == made
    assert tip(repo, BRANCH) == made and tip(repo, "main") == main
    assert (worktree / ".git").read_text() == f"gitdir: {p.git_dir}\n"
    assert sessiongit.land(p, private) is None                             # idempotent
    assert gitwip.git(repo, "fsck", "--connectivity-only", "--no-dangling") == ""


def test_a_packed_bead_branch_still_lands(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "agent work")
    inside(private, worktree, "pack-refs", "--all")
    assert sessiongit.land(p, private) == tip(repo, BRANCH)


def test_landing_refuses_a_link_in_the_session_objects(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    before = tip(repo, BRANCH)
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "agent work")
    (private / "objects" / "zz").symlink_to(tmp_path)
    with pytest.raises(gitwip.GitFailed, match="link"):
        sessiongit.land(p, private)
    assert tip(repo, BRANCH) == before


def test_btq_git_never_reads_the_session_git_dir(tmp_path: Path) -> None:
    """btq's git is not pinned: it follows the worktree's `.git`. The pointer is trusted when it is bound
    read-only, so no config, hook or fsmonitor the agent writes in its private directory runs on the host."""
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    (worktree / ".git").write_text(f"gitdir: {private}\n")              # planted in an earlier generation
    sessiongit.seed(p, private, read_only=False)
    assert (worktree / ".git").read_text() == f"gitdir: {p.git_dir}\n"
    marker = tmp_path / "ran"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o700)
    with (private / "config").open("a") as fh:                         # what the agent can write
        fh.write(f"[core]\n\tfsmonitor = {hook}\n\thooksPath = {tmp_path}\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"], env=env, check=True,
                   capture_output=True)                                 # as btq runs it
    assert not marker.exists()


@pytest.mark.parametrize("swap", ["objects", "objects/info"])
def test_landing_refuses_a_linked_object_store_and_writes_nothing_outside(tmp_path: Path, swap: str) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    before = tip(repo, BRANCH)
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "agent work")
    outside = tmp_path / "outside"
    shutil.move(private / swap, outside)                  # the agent moves the store out, links it back
    next(outside.rglob("alternates")).unlink()
    (private / swap).symlink_to(outside)
    files = {q: q.read_bytes() for q in outside.rglob("*") if q.is_file()}
    with pytest.raises(gitwip.GitFailed):
        sessiongit.land(p, private)
    assert {q: q.read_bytes() for q in outside.rglob("*") if q.is_file()} == files
    assert not any(outside.rglob("alternates")) and tip(repo, BRANCH) == before


def test_landing_ignores_an_alternates_file_the_agent_rewrote(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    other = git_repo(tmp_path / "other")
    gitwip.git(other, *IDENT, "commit", "-q", "--allow-empty", "-m", "foreign")   # one this repo never had
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    before = tip(repo, BRANCH)
    (private / "objects" / "info" / "alternates").write_text(f"{other / '.git' / 'objects'}\n")
    (private / "refs" / "heads" / "btq" / "btq-1").write_text(tip(other, "main") + "\n")
    with pytest.raises(gitwip.GitFailed):
        sessiongit.land(p, private)
    assert tip(repo, BRANCH) == before


def test_landing_into_a_vanished_worktree_is_a_git_failure(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    shutil.rmtree(worktree)
    with pytest.raises(gitwip.GitFailed):
        sessiongit.land(p, private)


# r1 review: drivers the agent's .gitattributes can select, landing that isn't a fast-forward, and a
# packed-refs file larger than one read.


def planted_filter(tmp_path: Path, worktree: Path) -> Path:
    """The agent's side of a filter attack: an attribute selecting the driver, and the script it runs."""
    marker = tmp_path / "ran"
    (worktree / "clean.sh").write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    (worktree / "clean.sh").chmod(0o700)
    (worktree / ".gitattributes").write_text("* filter=conv\n")
    (worktree / "work.txt").write_text("work")
    return marker


def test_pin_refuses_a_configured_filter_driver(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    gitwip.git(repo, "config", "filter.conv.clean", "./clean.sh")      # trusted config, agent's script
    marker = planted_filter(tmp_path, worktree)
    with pytest.raises(gitwip.GitFailed, match="driver"):
        gitwip.pin(repo, worktree, BRANCH)
    assert not marker.exists()


@pytest.mark.parametrize("key", ["diff.conv.textconv", "merge.conv.driver", "filter.conv.process"])
def test_pin_refuses_every_attribute_driver(tmp_path: Path, key: str) -> None:
    repo, worktree = linked(tmp_path)
    gitwip.git(repo, "config", key, "./clean.sh")
    with pytest.raises(gitwip.GitFailed, match="driver"):
        gitwip.pin(repo, worktree, BRANCH)


LFS = ('[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge -- %f\n'
       '\tprocess = git-lfs filter-process\n\trequired = true\n')


def test_a_driver_in_the_global_config_is_the_operators(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """A driver the operator configured, as installing git-lfs does, is the host's: pin trusts it. Pinned
    git never reads it anyway."""
    repo, worktree = linked(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(LFS)
    monkeypatch.setenv("HOME", str(home))
    assert gitwip.pin(repo, worktree, BRANCH).branch == BRANCH


def test_a_driver_in_the_system_config_is_the_operators(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    repo, worktree = linked(tmp_path)
    (tmp_path / "system.cfg").write_text(LFS + "[safe]\n\tdirectory = *\n")
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "system.cfg"))
    assert gitwip.pin(repo, worktree, BRANCH).branch == BRANCH


def test_a_system_include_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, worktree = linked(tmp_path)
    (tmp_path / "system.cfg").write_text(f"[include]\n\tpath = {worktree / 'included.cfg'}\n")
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "system.cfg"))
    with pytest.raises(gitwip.GitFailed, match="includes"):
        gitwip.pin(repo, worktree, BRANCH)


def test_pin_refuses_an_include_in_the_repository_config(tmp_path: Path) -> None:
    """An included file could gain a driver after the check, so the repository's config includes none."""
    repo, worktree = linked(tmp_path)
    (tmp_path / "extra.cfg").write_text("[core]\n\tquotePath = false\n")
    gitwip.git(repo, "config", "include.path", str(tmp_path / "extra.cfg"))
    with pytest.raises(gitwip.GitFailed, match="includes"):
        gitwip.pin(repo, worktree, BRANCH)


def test_pinned_git_never_reads_system_or_global_config(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence in depth under the pin's check: a driver only the global config defines never runs in a
    host WIP commit, even with the check bypassed."""
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(f"[filter \"conv\"]\n\tclean = {worktree / 'clean.sh'}\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    marker = planted_filter(tmp_path, worktree)
    gitwip.wip_commit(p, "m1", "test")
    assert not marker.exists()


def test_landing_refuses_an_unrelated_history(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    before = tip(repo, BRANCH)
    inside(private, worktree, "checkout", "-q", "--orphan", "other")
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "unrelated")
    unrelated = inside(private, worktree, "rev-parse", "HEAD")
    inside(private, worktree, "update-ref", f"refs/heads/{BRANCH}", unrelated)
    with pytest.raises(gitwip.GitFailed, match="descend"):
        sessiongit.land(p, private)
    assert tip(repo, BRANCH) == before


def test_landing_refuses_a_rewind(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    landed = gitwip.wip_commit(p, "m1", "an earlier generation's work")
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    inside(private, worktree, "update-ref", f"refs/heads/{BRANCH}", tip(repo, "main"))   # below the base
    with pytest.raises(gitwip.GitFailed, match="descend"):
        sessiongit.land(p, private)
    assert tip(repo, BRANCH) == landed


def test_a_bead_branch_packed_past_one_read_still_lands(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    for i in range(120):                                # sorted before btq/: the bead entry is far in
        gitwip.git(repo, "branch", f"a-{i:03}-{'x' * 20}", "main")
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "agent work")
    inside(private, worktree, "pack-refs", "--all")
    packed = (private / "packed-refs").read_text()
    assert packed.index(f"refs/heads/{BRANCH}") > 4096 and not (private / "refs/heads/btq/btq-1").exists()
    assert sessiongit.land(p, private) == tip(repo, BRANCH)


def test_oversized_metadata_fails_explicitly(tmp_path: Path) -> None:
    big = tmp_path / "big"
    big.write_text("x" * 5000)
    with pytest.raises(gitwip.GitFailed, match="too large"):
        gitwip.read_meta(big)
    assert gitwip.read_meta(big, limit=5000) == "x" * 5000


# r2 review: signing and other programs the repository's config names, and includes at every level.


def planted_signer(tmp_path: Path, worktree: Path) -> Path:
    marker = tmp_path / "signed"
    (worktree / "sign.sh").write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    (worktree / "sign.sh").chmod(0o700)
    return marker


def test_pin_refuses_a_repository_signing_program(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    gitwip.git(repo, "config", "commit.gpgsign", "true")
    gitwip.git(repo, "config", "gpg.program", "./sign.sh")
    marker = planted_signer(tmp_path, worktree)
    with pytest.raises(gitwip.GitFailed, match="program"):
        gitwip.pin(repo, worktree, BRANCH)
    assert not marker.exists()


def test_a_host_wip_commit_never_signs(tmp_path: Path) -> None:
    """Defence in depth under the pin's check: with signing on and a signer in the worktree, a host WIP
    commit made with the check bypassed still runs no signer."""
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    gitwip.git(repo, "config", "commit.gpgsign", "true")
    gitwip.git(repo, "config", "gpg.program", "./sign.sh")
    marker = planted_signer(tmp_path, worktree)
    sha = gitwip.wip_commit(p, "m1", "test")
    assert not marker.exists() and tip(repo, BRANCH) == sha


@pytest.mark.parametrize("key", ["gpg.ssh.program", "core.sshCommand", "core.pager", "pager.log",
                                 "core.editor", "sequence.editor", "core.askPass", "credential.helper",
                                 "credential.https://example.org.helper", "diff.external", "core.hooksPath",
                                 "core.fsmonitor", "core.gitProxy", "remote.origin.uploadpack",
                                 "remote.origin.receivepack", "gpg.ssh.defaultKeyCommand"])
def test_pin_refuses_a_program_in_the_repository_config(tmp_path: Path, key: str) -> None:
    repo, worktree = linked(tmp_path)
    gitwip.git(repo, "config", key, "./run.sh")
    with pytest.raises(gitwip.GitFailed, match="program"):
        gitwip.pin(repo, worktree, BRANCH)


def test_a_builtin_fsmonitor_setting_is_not_a_program(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    gitwip.git(repo, "config", "core.fsmonitor", "false")
    assert gitwip.pin(repo, worktree, BRANCH).branch == BRANCH


def global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".gitconfig").write_text(text)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def test_the_host_programs_of_the_global_config_are_trusted(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator's own settings (a credential helper, signing) are the host's, not the agent's."""
    repo, worktree = linked(tmp_path)
    global_config(tmp_path, monkeypatch, "[commit]\n\tgpgsign = true\n[credential]\n\thelper = store\n")
    assert gitwip.pin(repo, worktree, BRANCH).branch == BRANCH


@pytest.mark.parametrize("include", ["[include]\n\tpath = {target}\n",
                                     "[includeIf \"gitdir:/\"]\n\tpath = {target}\n"])
def test_pin_refuses_an_include_in_the_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     include: str) -> None:
    """btq's unpinned `git worktree add` reads the global config: an include there could reach a file the
    agent writes, which could later set a hooks path. Benign contents now don't make it safe."""
    repo, worktree = linked(tmp_path)
    target = worktree / "included.cfg"
    target.write_text("[core]\n\tquotePath = false\n")
    global_config(tmp_path, monkeypatch, include.format(target=target))
    with pytest.raises(gitwip.GitFailed, match="includes"):
        gitwip.pin(repo, worktree, BRANCH)


def test_a_relative_global_hooks_path_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A relative hooks path is resolved in the worktree, so btq's git would run the agent's hooks."""
    repo, worktree = linked(tmp_path)
    global_config(tmp_path, monkeypatch, "[core]\n\thooksPath = .githooks\n")
    with pytest.raises(gitwip.GitFailed, match="program"):
        gitwip.pin(repo, worktree, BRANCH)


# --- the operator's resolve (btq-xb56t): snapshot and import_snapshot ---

ZERO = "0" * 40


def session_commits(tmp_path: Path, count: int = 1) -> tuple[Path, Path, gitwip.Pinned, Path, str]:
    """A seeded private directory with `count` agent commits on its bead branch, as `land` would refuse
    them. Returns the repository, the worktree, the pin, the private directory and the session's tip."""
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    for n in range(count):
        (worktree / f"f{n}.txt").write_text(f"work {n}\n")
        inside(private, worktree, "add", f"f{n}.txt")
        inside(private, worktree, *IDENT, "commit", "-q", "-m", f"agent work {n}")
    return repo, worktree, p, private, inside(private, worktree, "rev-parse", "HEAD")


def take(p: gitwip.Pinned, private: Path, tmp_path: Path, name: str = "one") -> sessiongit.Snapshot:
    (tmp_path / "inspect").mkdir(mode=0o700, exist_ok=True)
    return sessiongit.snapshot(p, private, tmp_path / "inspect" / name)


def loose_path(private: Path, sha: str) -> Path:
    return private / "objects" / sha[:2] / sha[2:]


def test_a_snapshot_holds_the_tip_and_compares_it(tmp_path: Path) -> None:
    repo, _, p, private, made = session_commits(tmp_path, 2)
    snap = take(p, private, tmp_path)
    assert (snap.tip, snap.error) == (made, "")
    landed = sessiongit.landed(p)
    cmp = sessiongit.compare(p, snap, landed)
    assert (cmp.ahead, cmp.behind, cmp.subjects) == (2, 0, (b"agent work 1", b"agent work 0"))
    assert sessiongit.fast_forward(p, snap, landed)
    assert not (snap.dest / "objects" / "info" / "alternates").exists()
    assert tip(repo, BRANCH) == landed                                   # nothing was imported


def test_the_stores_only_alternate_is_the_bead_repositorys_and_fsck_runs_without_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """fsck walks every alternate, so it runs with none and checks the store's own objects; every other
    store call gets the bead repository's objects alone, never anything under the private directory."""
    repo, _, p, private, made = session_commits(tmp_path, 2)
    calls: list[tuple[str, str | None]] = []
    real = subprocess.run

    def recorded(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        env = kwargs.get("env")
        if env is not None and env.get("GIT_DIR", "").startswith(str(tmp_path / "inspect")):
            verb = next(a for a in argv[1:] if not a.startswith("-") and "=" not in a)
            calls.append((verb, env.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(sessiongit.subprocess, "run", recorded)
    snap = take(p, private, tmp_path)
    sessiongit.compare(p, snap, sessiongit.landed(p))
    assert snap.tip == made
    assert ("fsck", None) in calls and [v for v, alt in calls if alt is None] == ["config", "fsck"]
    alternates = {alt for _, alt in calls if alt is not None}
    assert alternates == {str(p.common / "objects")} and p.common.resolve() == (repo / ".git").resolve()
    assert not any(alt.startswith(str(private)) for alt in alternates)


def test_snapshot_ignores_private_config_hooks_and_alternates(tmp_path: Path) -> None:
    repo, _, p, private, made = session_commits(tmp_path)
    marker = tmp_path / "ran"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    script = f"#!/bin/sh\ntouch {marker}\n"
    for name in ("fsmonitor.sh", *(f"hooks/{h}" for h in ("post-checkout", "reference-transaction",
                                                          "post-index-change"))):
        (tmp_path / name).write_text(script)
        (tmp_path / name).chmod(0o700)
    with (private / "config").open("a") as fh:
        fh.write(f"[core]\n\tfsmonitor = {tmp_path / 'fsmonitor.sh'}\n\thooksPath = {hooks}\n")
    (private / "hooks").mkdir(exist_ok=True)
    shutil.copy(hooks / "reference-transaction", private / "hooks" / "reference-transaction")
    elsewhere = tmp_path / "elsewhere.git"
    gitwip.git(tmp_path, "init", "-q", "--bare", str(elsewhere))
    for found in (private / "objects").glob("??/*"):                    # the tip's objects live elsewhere
        target = elsewhere / "objects" / found.parent.name / found.name
        target.parent.mkdir(exist_ok=True)
        found.rename(target)
    with (private / "objects" / "info" / "alternates").open("a") as fh:
        fh.write(f"{elsewhere / 'objects'}\n")
    snap = take(p, private, tmp_path)
    assert (snap.tip, snap.error) == (None, sessiongit.INCOMPLETE)
    assert not marker.exists()
    assert tip(repo, BRANCH) != made


def make_pack(private: Path, worktree: Path) -> Path:
    """Pack the session's own objects (its alternates' stay where they are)."""
    inside(private, worktree, "repack", "-q", "-a", "-d", "-l")
    [pack] = (private / "objects" / "pack").glob("pack-*.pack")
    return pack


def absent_fanout(private: Path) -> str:
    """The first fan-out directory, `00` to `ff`, with nothing under the private objects: the commit's
    own is random (its hash holds its timestamps), so a fixed one collides 1 run in 256."""
    return next(f"{n:02x}" for n in range(256) if not os.path.lexists(private / "objects" / f"{n:02x}"))


@pytest.mark.parametrize(("plant", "taken"), [
    ("object-dir-link", False), ("pack-link", False), ("fifo", False), ("hard-linked-pack", False),
    ("oversized", False), ("object-dir-link", True), ("fifo", True)])
def test_snapshot_refuses_links_special_files_and_hard_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            plant: str, taken: bool) -> None:
    """`taken`: the commit's random fan-out directory is one a fixed plant would have used (CI, PR #67)."""
    _, worktree, p, private, made = session_commits(tmp_path)
    if taken:
        for fan in ("ab", "cd"):
            (private / "objects" / fan).mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = b"outside the private directory\n"
    (outside / ("a" * 38)).write_bytes(secret)
    expected = sessiongit.LINKED
    fan = absent_fanout(private)
    assert not os.path.lexists(private / "objects" / fan)
    if plant == "object-dir-link":
        (private / "objects" / fan).symlink_to(outside)
    elif plant == "pack-link":
        (outside / "pack.pack").write_bytes(secret)
        (private / "objects" / "pack").mkdir(exist_ok=True)
        (private / "objects" / "pack" / f"pack-{'b' * 40}.pack").symlink_to(outside / "pack.pack")
    elif plant == "fifo":
        (private / "objects" / fan).mkdir()
        os.mkfifo(private / "objects" / fan / ("e" * 38))
    elif plant == "hard-linked-pack":
        os.link(make_pack(private, worktree), outside / "linked.pack")
        expected = sessiongit.HARD_LINKED
    else:
        monkeypatch.setattr(sessiongit, "OBJECT_LIMIT", 16)
        expected = sessiongit.TOO_LARGE
    snap = take(p, private, tmp_path)
    assert (snap.tip, snap.error) == (None, expected)
    copied = [f.read_bytes() for f in snap.dest.rglob("*") if f.is_file() and not f.is_symlink()]
    assert secret not in copied
    assert inside(private, worktree, "rev-parse", "HEAD") == made


def write_loose(path: Path, raw: bytes) -> None:
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(zlib.compress(raw))


@pytest.mark.parametrize("damage", ["corrupt-loose", "corrupt-pack", "missing-parent", "wrong-name",
                                    "malformed-blob", "tree-tip", "blob-tip"])
def test_snapshot_refuses_malformed_objects(tmp_path: Path, damage: str) -> None:
    _, worktree, p, private, made = session_commits(tmp_path, 2)
    expected = sessiongit.CORRUPT
    if damage == "corrupt-loose":
        write_loose(loose_path(private, made), b"commit 9999\0not a commit")
    elif damage == "corrupt-pack":
        pack = make_pack(private, worktree)
        data = bytearray(pack.read_bytes())
        data[len(data) // 2] ^= 0xFF
        pack.chmod(0o600)
        pack.write_bytes(bytes(data))
    elif damage == "missing-parent":
        loose_path(private, inside(private, worktree, "rev-parse", "HEAD~1")).unlink()
        expected = sessiongit.INCOMPLETE
    elif damage == "wrong-name":
        blob = inside(private, worktree, "hash-object", "-w", "--stdin")      # the empty blob
        other = hashlib.sha1(b"some other name").hexdigest()                  # noqa: S324 - a name, not security
        loose_path(private, other).parent.mkdir(exist_ok=True)
        shutil.copy(loose_path(private, blob), loose_path(private, other))
    elif damage == "malformed-blob":
        raw = b"blob 99\0short"
        write_loose(loose_path(private, hashlib.sha1(raw).hexdigest()), raw)  # noqa: S324
    else:
        name = "HEAD^{tree}" if damage == "tree-tip" else "HEAD:f1.txt"
        what = inside(private, worktree, "rev-parse", name)
        (private / "refs" / "heads" / "btq" / "btq-1").write_text(f"{what}\n")
        expected = sessiongit.NOT_A_COMMIT
    snap = take(p, private, tmp_path)
    assert (snap.tip, snap.error) == (None, expected)
    with pytest.raises(gitwip.GitFailed):
        sessiongit.compare(p, snap, sessiongit.landed(p))                   # no counts, no subjects


def tree_digest(root: Path) -> list[tuple[str, int, int, int, bytes]]:
    found: list[tuple[str, int, int, int, bytes]] = []
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        body = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
        found.append((str(path.relative_to(root)), st.st_mode, st.st_nlink, st.st_mtime_ns, body))
    return found


def test_snapshot_leaves_the_private_dir_unchanged(tmp_path: Path) -> None:
    _, worktree, p, private, _ = session_commits(tmp_path, 2)
    make_pack(private, worktree)
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "loose after the pack")
    before = tree_digest(private)
    snap = take(p, private, tmp_path)
    assert snap.tip is not None
    assert tree_digest(private) == before


def test_import_snapshot_uses_the_given_tip(tmp_path: Path) -> None:
    repo, worktree, p, private, made = session_commits(tmp_path)
    snap = take(p, private, tmp_path)
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "after the snapshot")
    assert inside(private, worktree, "rev-parse", "HEAD") != made
    old = sessiongit.landed(p)
    sessiongit.import_snapshot(p, snap, made, f"refs/heads/{BRANCH}", old)
    assert tip(repo, BRANCH) == made
    keep = "refs/heads/unlanded/btq-1/hz0123456789ab-g1"
    sessiongit.import_snapshot(p, snap, made, keep, ZERO)
    assert tip(repo, keep) == made
    with pytest.raises(gitwip.GitFailed):
        sessiongit.import_snapshot(p, snap, made, keep, ZERO)              # create only
    with pytest.raises(gitwip.GitFailed, match="does not hold"):
        sessiongit.import_snapshot(p, snap, old, f"refs/heads/{BRANCH}", made)
    assert gitwip.git(repo, "fsck", "--connectivity-only", "--no-dangling") == ""


def test_a_keep_ref_under_a_taken_name_cant_be_created(tmp_path: Path) -> None:
    repo, _, p, private, made = session_commits(tmp_path)
    gitwip.git(repo, "branch", "unlanded/btq-1", "main")
    snap = take(p, private, tmp_path)
    with pytest.raises(gitwip.GitFailed):
        sessiongit.import_snapshot(p, snap, made, "refs/heads/unlanded/btq-1/hz0123456789ab-g1", ZERO)
