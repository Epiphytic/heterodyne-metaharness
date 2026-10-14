"""Links (cxmkf K20), `--ref` resolution and its hardening (K21, K21h) and bare-path hints (K22p).

`--ref` runs against a real temporary git repository and a fake `gh` on a private PATH that answers from
a fixture table and records its argv and environment. No network: the real gh is never on that PATH.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fakes.settings import make_settings
from test_admind_asks import TOKEN, via_main

from heterodyne.admind import asks, cli, links, refs
from heterodyne.admind.links import LinkSpec
from heterodyne.admind.notices import NoticePost

SHA = "".join("0123456789abcdef"[i % 16] for i in range(7, 47))       # 40 hex, built at runtime
SHA2 = SHA[::-1]
BASE = "https://github.com/owner/repo"
SENTINEL = "GH-STDERR-SENTINEL-" + "q" * 8


# --- K20: the pinned forms ------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    f"{BASE}/pull/17", f"{BASE}/pull/17/files", f"{BASE}/pull/17/commits/{SHA}",
    f"{BASE}/blob/{SHA}/docs/design.md", f"{BASE}/blob/{SHA}/docs/a%20b.md", f"{BASE}/tree/{SHA}/src/pkg",
    f"{BASE}/tree/{SHA}/x", f"{BASE}/commit/{SHA}", f"{BASE}/compare/{SHA}...{SHA2}",
    f"{BASE}/actions/runs/123456789", f"{BASE}/issues/5", "https://github.com/o-1/r.e_p-o/pull/1",
])
def test_pinned_forms_are_accepted(url: str) -> None:
    assert links.why(LinkSpec("PR #17", url)) is None


@pytest.mark.parametrize("url", [
    f"{BASE}/tree/main/docs", f"{BASE}/blob/main/docs/x.md", f"{BASE}/commit/{SHA[:7]}",
    f"{BASE}/blob/{SHA[:12]}/x.md", f"https://gitlab.com/owner/repo/commit/{SHA}",
    "http://github.com/owner/repo/pull/1",
    "https://user@github.com/owner/repo/pull/1",  # install-agnostic: allow=email
    "https://github.com:443/owner/repo/pull/1",
    f"{BASE}/pull/1?diff=split", f"{BASE}/pull/1#discussion", f"{BASE}/blob/{SHA}/docs/a b.md",
    f"{BASE}/blob/{SHA}/../x.md", f"{BASE}/blob/{SHA}/docs/./x.md", f"{BASE}/blob/{SHA}/", f"{BASE}/pull/0",
    "https://github.com/owner/../pull/1", f"{BASE}/pulls", f"{BASE}/pull/1/files/extra",
    f"{BASE}/commit/{SHA.upper()}",
    f" {BASE}/pull/1", f"{BASE}/pull/1\n",
])
def test_unpinned_or_odd_urls_are_refused(url: str) -> None:
    assert links.why(LinkSpec("L", url)) == "link-format"
    assert links.MESSAGES["link-format"] == links.FORMAT


def test_link_bounds_labels_and_redaction() -> None:
    long = f"{BASE}/blob/{SHA}/" + "z" * (links.MAX_URL - len(f"{BASE}/blob/{SHA}/"))
    assert len(long) == 400 and links.why(LinkSpec("L", long)) is None
    assert links.why(LinkSpec("L", long + "z")) == "link-too-long"
    for label in ("", "   ", "x" * 41, "a\nb", "tab\there", "line sep"):
        assert links.why(LinkSpec(label, f"{BASE}/pull/1")) == "link-label"
    assert links.why(LinkSpec("x" * 40, f"{BASE}/pull/1")) is None
    assert links.why(LinkSpec(TOKEN, f"{BASE}/pull/1")) == "link-redacted"
    eight = [LinkSpec(f"L{i}", f"{BASE}/pull/{i + 1}") for i in range(8)]
    assert links.check(eight) is None
    assert links.check([*eight, LinkSpec("L9", f"{BASE}/pull/9")]) == "too-many-links"
    mixed = [LinkSpec("ok", f"{BASE}/pull/1"), LinkSpec("bad", f"{BASE}/tree/main")]
    assert links.check(mixed) == "link-format"
    assert links.block(eight[:1]) == ["Links:", f"L0: {BASE}/pull/1"] and links.block([]) == []


def test_refusal_messages_never_echo_the_url() -> None:
    url = f"https://evil.example/{SENTINEL}"
    why = links.check([LinkSpec("L", url)])
    assert why == "link-format" and SENTINEL not in links.MESSAGES[why]
    refusal = asks.check(asks.AskPost("question", "T", "b" * 100, links=[LinkSpec("L", url)]))
    assert refusal is not None and SENTINEL not in refusal


# --- the repository and the fake gh ---------------------------------------------------------------
FAKE_GH = """#!{python}
import json, os, sys, time
args = sys.argv[1:]
env = dict(os.environ)
with open(env["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps({{"argv": args, "env": env}}) + "\\n")
table = json.load(open(env["FAKE_GH_TABLE"]))
sys.stderr.write(env.get("FAKE_GH_STDERR", ""))
if env.get("GH_HOST") == "ghe.example":
    status, code, body, delay = 200, 0, table.get("ghe-body", {{}}), 0
else:
    status, code, body, delay = table.get(args[-1], [404, 1, {{"message": "Not Found"}}, 0])
time.sleep(delay)
sys.stdout.write("HTTP/2.0 %d X\\r\\nContent-Type: application/json\\r\\n\\r\\n" % status + json.dumps(body))
sys.exit(code)
"""


class Repo:
    """A git repository with an `origin`, a private PATH holding git and the fake gh, and its table."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path / "repo"
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        git = shutil.which("git")
        assert git is not None
        (self.bin / "git").symlink_to(git)
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable))
        gh.chmod(0o755)
        self.log = tmp_path / "gh.log"
        self.table_path = tmp_path / "gh.json"
        self.table: dict[str, Any] = {}
        self.save()
        for name in list(os.environ):
            if name.startswith("GIT_") or name.startswith("GH_"):
                monkeypatch.delenv(name)
        monkeypatch.setenv("PATH", str(self.bin))
        monkeypatch.setenv("FAKE_GH_LOG", str(self.log))
        monkeypatch.setenv("FAKE_GH_TABLE", str(self.table_path))
        self.root.mkdir()
        self.run("init", "-q", "-b", "main")
        self.run("remote", "add", "origin", "https://github.com/owner/repo.git")
        self.write("docs/x.md", "x\n")
        self.write("docs/a b.md", "space\n")
        self.write("src/pkg/mod.py", "pass\n")
        self.write("README.md", "readme\n")
        self.first = self.commit("one")
        self.write("docs/x.md", "x2\n")
        self.sha = self.commit("two")

    def run(self, *args: str) -> str:
        env = {"PATH": str(self.bin), "HOME": str(self.root.parent), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": "/dev/null"}
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                              cwd=self.root, env=env, check=True, capture_output=True, text=True)
        return done.stdout.strip()

    def write(self, path: str, text: str) -> None:
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(text)

    def commit(self, message: str) -> str:
        self.run("add", "-A")
        self.run("commit", "-q", "-m", message)
        return self.run("rev-parse", "HEAD")

    def save(self) -> None:
        self.table_path.write_text(json.dumps(self.table))

    def answer(self, endpoint: str, status: int = 200, code: int | None = None, body: object = None,
               delay: float = 0) -> None:
        self.table[endpoint] = [status, (0 if status == 200 else 1) if code is None else code,
                                {} if body is None else body, delay]
        self.save()

    def on_github(self, sha: str, *paths: str, owner: str = "owner", repo: str = "repo") -> None:
        """The commit and `paths` (encoded as gh is asked for them) exist on GitHub."""
        self.answer(f"repos/{owner}/{repo}/commits/{sha}", body={"sha": sha})
        for path in paths:
            self.answer(f"repos/{owner}/{repo}/contents/{path}?ref={sha}", body={"path": path})

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def resolver(self) -> refs.Resolver:
        return refs.Resolver(str(self.root))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    return Repo(tmp_path, monkeypatch)


def refused(r: refs.Resolver, spec: str) -> str:
    with pytest.raises(refs.RefError) as exc:
        r.resolve(spec)
    return str(exc.value)


# --- K21: resolution ------------------------------------------------------------------------------
def test_a_file_is_a_blob_link_and_a_directory_a_tree_link(repo: Repo) -> None:
    repo.on_github(repo.sha, "docs/x.md", "src/pkg", "docs/a%20b.md")
    r = repo.resolver()
    blob = r.resolve("docs/x.md")
    assert blob == LinkSpec("docs/x.md", f"{BASE}/blob/{repo.sha}/docs/x.md") and links.why(blob) is None
    tree = r.resolve("src/pkg")
    assert tree == LinkSpec("src/pkg", f"{BASE}/tree/{repo.sha}/src/pkg") and links.why(tree) is None
    spaced = r.resolve("docs/a b.md")
    assert spaced.url == f"{BASE}/blob/{repo.sha}/docs/a%20b.md" and links.why(spaced) is None
    endpoints = [c["argv"][-1] for c in repo.calls()]
    assert f"repos/owner/repo/contents/docs/a%20b.md?ref={repo.sha}" in endpoints


def test_rev_is_resolved_locally_to_a_full_sha(repo: Repo) -> None:
    repo.on_github(repo.first, "docs/x.md")
    assert repo.resolver().resolve("docs/x.md@HEAD~1").url == f"{BASE}/blob/{repo.first}/docs/x.md"
    assert repo.resolver().resolve(f"docs/x.md@{repo.first[:10]}").url.endswith(f"/{repo.first}/docs/x.md")


def test_a_long_path_label_keeps_its_tail(repo: Repo) -> None:
    path = "docs/" + "/".join(["deeply-nested-directory"] * 3) + "/file.md"
    repo.write(path, "deep\n")
    sha = repo.commit("deep")
    repo.on_github(sha, path)
    link = repo.resolver().resolve(path)
    assert len(link.label) == links.MAX_LABEL and link.label == "…" + path[-39:]
    assert links.why(link) is None


def test_unknown_rev_and_missing_path_are_refused_locally(repo: Repo) -> None:
    r = repo.resolver()
    assert "is not a commit" in refused(r, "docs/x.md@nosuchbranch")
    assert "is not a file or directory" in refused(r, "docs/missing.md")
    assert repo.calls() == []                   # nothing asked of GitHub


def test_github_refusals_are_distinct(repo: Repo) -> None:
    r = repo.resolver()
    assert refused(r, "docs/x.md") == f"{repo.sha} is not on GitHub at owner/repo; push it first"
    repo.on_github(repo.sha)                    # the commit, but not the path
    assert refused(r, "docs/x.md") == f"docs/x.md does not exist at {repo.sha} on GitHub"


@pytest.mark.parametrize("case", ["exit-nonzero", "status-500", "wrong-sha", "no-json", "path-500"])
def test_unexpected_gh_answers_are_unchecked(repo: Repo, case: str) -> None:
    commit = f"repos/owner/repo/commits/{repo.sha}"
    if case == "exit-nonzero":
        repo.answer(commit, 200, code=1, body={"sha": repo.sha})
    elif case == "status-500":
        repo.answer(commit, 500)
    elif case == "wrong-sha":
        repo.answer(commit, body={"sha": repo.first})
    elif case == "no-json":
        repo.answer(commit, body="not an object")
    else:
        repo.on_github(repo.sha)
        repo.answer(f"repos/owner/repo/contents/docs/x.md?ref={repo.sha}", 500)
    assert refused(repo.resolver(), "docs/x.md") == refs.UNCHECKED


def test_gh_missing_or_slow_is_unchecked(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.answer(f"repos/owner/repo/commits/{repo.sha}", body={"sha": repo.sha}, delay=3)
    monkeypatch.setattr(refs, "GH_SECONDS", 0.3)
    assert refused(repo.resolver(), "docs/x.md") == refs.UNCHECKED
    (repo.bin / "gh").unlink()
    assert refused(repo.resolver(), "docs/x.md") == refs.UNCHECKED


def test_gh_stderr_never_surfaces(repo: Repo, monkeypatch: pytest.MonkeyPatch,
                                  capfd: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FAKE_GH_STDERR", SENTINEL)
    message = refused(repo.resolver(), "docs/x.md")         # 404: push it first
    repo.on_github(repo.sha, "docs/x.md")
    repo.resolver().resolve("docs/x.md")
    out, err = capfd.readouterr()
    assert SENTINEL not in message + out + err
    leaked = [p for p in repo.root.parent.rglob("*") if p.is_file() and p != repo.log
              and SENTINEL.encode() in p.read_bytes()]
    assert leaked == []


def test_a_hostile_gh_host_is_never_used(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """With GH_HOST pointing at an enterprise host (where the fake answers 200 for everything), the check
    still asks github.com, which says 404, so the post is refused."""
    for name, value in (("GH_HOST", "ghe.example"), ("GH_ENTERPRISE_TOKEN", "e-token"),
                        ("GITHUB_ENTERPRISE_TOKEN", "e-token"), ("GH_DEBUG", "api")):
        monkeypatch.setenv(name, value)
    repo.table["ghe-body"] = {"sha": repo.sha}
    repo.save()
    assert "push it first" in refused(repo.resolver(), "docs/x.md")
    for call in repo.calls():
        assert call["argv"][:7] == ["api", "--hostname", "github.com", "--method", "GET", "--include",
                                    f"repos/owner/repo/commits/{repo.sha}"]
        env = call["env"]
        assert not {"GH_HOST", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_DEBUG"} & set(env)
        assert env["GH_PROMPT_DISABLED"] == "1" and env["GH_NO_UPDATE_NOTIFIER"] == "1"


def test_stale_or_fabricated_remote_refs_prove_nothing(repo: Repo) -> None:
    repo.write("docs/new.md", "unpushed\n")
    sha = repo.commit("unpushed")
    repo.run("update-ref", "refs/remotes/origin/main", sha)
    repo.run("update-ref", "refs/remotes/other/main", sha)
    assert refused(repo.resolver(), "docs/new.md") == f"{sha} is not on GitHub at owner/repo; push it first"


@pytest.mark.parametrize("url", ["https://gitlab.com/owner/repo.git", "https://github.com/owner/repo/extra.git",
                                 "https://github.com/owner", "https://github.example/owner/repo.git",
                                 "git@gitlab.com:owner/repo.git", "file:///srv/repo.git", "/srv/repo.git"])  # noqa: E501  install-agnostic: allow=email
def test_a_non_github_origin_is_refused(repo: Repo, url: str) -> None:
    repo.run("remote", "set-url", "origin", url)
    assert refused(repo.resolver(), "docs/x.md") == refs.NOT_GITHUB
    assert repo.calls() == []


def test_no_origin_is_refused(repo: Repo) -> None:
    repo.run("remote", "remove", "origin")
    assert refused(repo.resolver(), "docs/x.md") == refs.NOT_GITHUB


def test_each_post_reads_the_origin_it_finds(repo: Repo) -> None:
    repo.on_github(repo.sha, "docs/x.md")
    repo.on_github(repo.sha, "docs/x.md", owner="fork-owner", repo="fork.repo")
    assert repo.resolver().resolve("docs/x.md").url.startswith(f"{BASE}/blob/")
    for url in ("git@github.com:fork-owner/fork.repo.git", "ssh://git@github.com/fork-owner/fork.repo",  # noqa: E501  install-agnostic: allow=email
                "https://github.com/fork-owner/fork.repo/"):
        repo.run("remote", "set-url", "origin", url)
        link = repo.resolver().resolve("docs/x.md")
        assert link.url == f"https://github.com/fork-owner/fork.repo/blob/{repo.sha}/docs/x.md"
    owners = {tuple(c["argv"][-1].split("/")[1:3]) for c in repo.calls()}
    assert owners == {("owner", "repo"), ("fork-owner", "fork.repo")}


@pytest.mark.parametrize("spec", ["docs/x.md@--output=x", "docs/x.md@-p", "docs/x.md@HEAD;id", "../x",
                                  "/etc/passwd", "-x", "a:b", "docs/a\nb.md", "docs//x.md", "docs/./x.md",
                                  "", "x" * 1001, "docs/x.md@" + "a" * 201, "docs/x.md@HEAD:docs"])
def test_hostile_arguments_are_refused_before_any_call(spec: str) -> None:
    calls: list[Sequence[str]] = []

    def recording(argv: Sequence[str], env: dict[str, str], seconds: float, limit: int) -> None:
        calls.append(argv)
    with pytest.raises(refs.RefError):
        refs.Resolver("/nonexistent", recording).resolve(spec)
    assert calls == []


def test_git_runs_hardened_with_an_environment_from_scratch(repo: Repo,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[list[str], dict[str, str]]] = []

    def recording(argv: Sequence[str], env: dict[str, str], seconds: float,
                  limit: int) -> tuple[int, bytes] | None:
        seen.append((list(argv), env))
        return refs.run(argv, env, seconds, limit)
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setenv("GIT_EXEC_PATH", "/elsewhere")
    repo.on_github(repo.sha, "docs/x.md")
    refs.Resolver(str(repo.root), recording).resolve("docs/x.md")
    gits = [(argv, env) for argv, env in seen if argv[0] == "git"]
    assert gits and all(argv[:len(refs.GIT)] == list(refs.GIT) for argv, _ in gits)
    for _, env in gits:
        assert set(env) == {"PATH", "HOME", "LANG", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
                            "GIT_TERMINAL_PROMPT", "GIT_OPTIONAL_LOCKS"}
        assert env["GIT_CONFIG_GLOBAL"] == "/dev/null" and env["LANG"] == "C"
    rev_parse = next(argv for argv, _ in gits if "rev-parse" in argv)
    assert rev_parse[-2:] == ["--end-of-options", "HEAD^{commit}"]
    get_url = next(argv for argv, _ in gits if "get-url" in argv)
    assert get_url[-2:] == ["--end-of-options", "origin"]
    assert all(argv[-1].startswith(repo.sha + ":") for argv, _ in gits if "cat-file" in argv)


def test_hostile_repository_configuration_runs_nothing(repo: Repo) -> None:
    marker = repo.root.parent / "marker"
    script = repo.root.parent / "hostile.sh"
    script.write_text(f"#!/bin/sh\n: > {marker}\n")     # a builtin: PATH holds only git and gh
    script.chmod(0o755)
    hooks = repo.root.parent / "hooks"
    hooks.mkdir()
    for hook in ("post-checkout", "pre-commit", "reference-transaction", "post-index-change"):
        shutil.copy(script, hooks / hook)
    for key in ("core.fsmonitor", "core.pager", "core.sshCommand"):
        repo.run("config", key, str(script))
    repo.run("config", "core.hooksPath", str(hooks))
    repo.on_github(repo.sha, "docs/x.md")
    r = repo.resolver()
    r.resolve("docs/x.md")
    r.hints("see docs/x.md")
    assert not marker.exists()
    # The fixture is live: an unhardened git does run the hostile fsmonitor or hooks.
    repo.write("docs/y.md", "y\n")
    repo.run("add", "-A")
    repo.run("status")
    assert marker.exists()


def test_an_inherited_git_dir_is_ignored(repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True, env={"PATH": str(repo.bin)})
    subprocess.run(["git", "-C", str(other), "remote", "add", "origin", "https://github.com/evil/other.git"],
                   check=True, env={"PATH": str(repo.bin)})
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    repo.on_github(repo.sha, "docs/x.md")
    assert repo.resolver().resolve("docs/x.md").url == f"{BASE}/blob/{repo.sha}/docs/x.md"


# --- K22p: bare-path hints ------------------------------------------------------------------------
def hint(path: str) -> str:
    return f"hint: {path} is a bare path; --ref {path} renders it as a link"


@pytest.mark.parametrize("text", ["see docs/x.md here", "in `docs/x.md`", 'the "docs/x.md" file',
                                  "docs/x.md.", "(docs/x.md)", "[docs/x.md],", "'docs/x.md';"])
def test_a_tracked_path_gets_a_hint(repo: Repo, text: str) -> None:
    assert repo.resolver().hints(text) == [hint("docs/x.md")]


@pytest.mark.parametrize("text", [f"{BASE}/blob/{SHA}/docs/x.md", "untracked/y.md", "README.md", "docs/x",
                                  "docs/x.mdx", "docs/"])
def test_no_hint_for_links_untracked_paths_or_no_slash(repo: Repo, text: str) -> None:
    assert repo.resolver().hints(text) == []


def test_at_most_five_hints_and_each_once(repo: Repo) -> None:
    names = [f"docs/h{i}.md" for i in range(7)]
    for name in names:
        repo.write(name, "h\n")
    repo.commit("hints")
    got = repo.resolver().hints(" ".join(names), "docs/h0.md again")
    assert got == [hint(n) for n in names[:5]]


def test_hints_are_quiet_outside_a_repository(tmp_path: Path) -> None:
    assert refs.Resolver(str(tmp_path)).hints("docs/x.md") == []


# --- the CLI builds the requests --------------------------------------------------------------------
@pytest.fixture
def captured(repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[asks.AskRequest]:
    via_main(monkeypatch, make_settings(tmp_path / "admind"))
    monkeypatch.chdir(repo.root)
    sent: list[asks.AskRequest] = []

    def request(s: object, req: asks.AskRequest, timeout: float = 0) -> asks.AskReply:
        sent.append(req)
        if isinstance(req, NoticePost):
            return asks.AskReply("posted", "posted abc234")
        return asks.AskReply("posted", "ask k7m2 posted")
    monkeypatch.setattr(cli, "_ask_request", request)
    return sent


def test_cli_ask_post_resolves_refs_and_hints(repo: Repo, captured: list[asks.AskRequest], tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    repo.on_github(repo.sha, "docs/x.md")
    body = tmp_path / "body.txt"
    body.write_text("The failure is in src/pkg/mod.py; the plan is in docs/x.md. " + "context " * 10)
    code = cli.main(["ask", "post", "--kind", "question", "--title", "Which?", "--body-file", str(body),
                     "--choice", "rerun", "--choice", "skip", "--recommended", "A", "--ref", "docs/x.md",
                     "--link", "PR #17=https://github.com/owner/repo/pull/17"])
    out = capsys.readouterr()
    assert code == 0 and out.out == "ask k7m2 posted\n"
    (req,) = captured
    assert isinstance(req, asks.AskPost) and req.form == "choices" and req.choices == ["rerun", "skip"]
    assert req.links == [LinkSpec("PR #17", "https://github.com/owner/repo/pull/17"),
                         LinkSpec("docs/x.md", f"{BASE}/blob/{repo.sha}/docs/x.md")]
    assert hint("src/pkg/mod.py") in out.err and hint("docs/x.md") in out.err


def test_cli_refuses_a_bad_ref_or_too_many_links_without_a_request(
        repo: Repo, captured: list[asks.AskRequest], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["notice", "post", "--title", "T", "--ref", "docs/x.md"]) == 1
    assert "push it first" in capsys.readouterr().err
    assert [c["argv"][-1] for c in repo.calls()] == [f"repos/owner/repo/commits/{repo.sha}"]
    nine = [a for i in range(9) for a in ("--link", f"L{i}=https://github.com/owner/repo/pull/{i + 1}")]
    assert cli.main(["notice", "post", "--title", "T", *nine]) == 1
    assert links.TOO_MANY in capsys.readouterr().err
    assert cli.main(["notice", "post", "--title", "T", "--link", "no-equals-sign"]) == 1
    assert cli.main(["ask", "post", "--kind", "approval", "--bead", "btq-ab12c", "--ref", "docs/x.md"]) == 1
    assert captured == [] and len(repo.calls()) == 1          # the later refusals asked nothing of GitHub


def test_cli_notice_post_builds_a_notice(captured: list[asks.AskRequest], tmp_path: Path,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    body = tmp_path / "n.txt"
    body.write_text("Plan 4b Task 11 Part A is in main.\n")
    code = cli.main(["notice", "post", "--title", "PR #69 merged", "--body-file", str(body),
                     "--from", "team-lead", "--link", "PR #69=https://github.com/owner/repo/pull/69"])
    assert code == 0 and capsys.readouterr().out == "posted abc234\n"
    link = LinkSpec("PR #69", "https://github.com/owner/repo/pull/69")
    body_text = "Plan 4b Task 11 Part A is in main.\n"
    assert captured == [NoticePost("PR #69 merged", body_text, [link], "team-lead")]
