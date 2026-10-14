"""Plan 4c Task 10: `wsd`'s harness commands, the digest-pinned install approval (E13), and the taint holds.

The rig is a `LocalStore` under `tmp_path / "store"`, the fixture fetcher (an opener serving the release and
the npm registry), `fake_btq` (which records `bd create` and serves `show`), `fake_approve_bead` (the
read-only `--json` form only) and a fake admind `ask.sock`. The tests that need the launch guard build the
harness on `wsd_env`'s rig, so the guard, the journal and the runtime are the real wsd ones.

Two review points from g08sd are pinned here:
- (1) `advance()` runs `approve-bead <bead> --json` from every pickup, so its read has a short deadline:
  a fake that never answers ends as `Busy` (the row waits) within the deadline, and its process is reaped.
- (2) `recover()` runs before the first pickup, and `remove` runs under the guard's lock: a guard that
  has read a pin keeps a `remove` of that install waiting until its entry is journaled, and the remove then
  refuses `IN_USE`. A published row and its selection never race a launch."""

import hashlib
import json
import logging
import os
import re
import shlex
import socket
import sqlite3
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fakes.checkpoints import CrashAt, Recorder, SimulatedCrash
from fakes.fake_btq import ASK_FIELDS, World, factory
from fakes.fake_runtime import FakeRuntime
from harness_env import (
    CLAUDE_STRIP,
    CLAUDE_TARGET,
    CLAUDE_TREE,
    CODEX_PACKAGE,
    CODEX_STRIP,
    CODEX_TREE,
    git,
    make_repo,
    rewrite,
    sri_of,
    store_dir,
    tar_of,
    unseal,
)
from sandbox_env import short_dir
from test_harness_fetch import ASSET, REGISTRY, SUMS, Response, npm_files
from test_harness_publish import CRASH_POINTS
from test_harness_publish import crash as crash_publish
from test_wsd_launches import started
from wsd_env import PROFILES, WS, Clock, Rig, Worker, accounts_at, finish, git_repo, login_home, probe_lock

from heterodyne.agents import base as agents_base
from heterodyne.agents.registry import ADAPTERS, login_names
from heterodyne.config import secret_scan
from heterodyne.harness import digests, selection, verify
from heterodyne.harness.fetch import Fetcher
from heterodyne.harness.resources import store_core as sc
from heterodyne.harness.selection import InstallPin, Selection
from heterodyne.harness.settings import ContentSource, HarnessSettings, Recipe
from heterodyne.harness.store import LocalStore
from heterodyne.wsd import btq as wbtq
from heterodyne.wsd import harness as wh
from heterodyne.wsd.beads import HELD
from heterodyne.wsd.daemon import Wsd, assemble, selections_at
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import INSTALL_TAINTED, InstallTainted
from heterodyne.wsd.scheduler import Outcome, Scheduler
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.workstream import WorkstreamSettings

# --- Task 10's seams. Everything these tests assume beyond the plan's named interfaces is here, so the
# implementation's final names change only this section: how `HarnessOps` is built, the journal readers, the
# refusal type, the readout's deadline, the checkpoints, the events' actors and actions, and the guard's lock.
# The modules they name are `heterodyne.harness.digests`, `heterodyne.wsd.harness` and `heterodyne.wsd.btq`'s
# `approval_readout`, `Readout` and `Busy` (Task 10), and `heterodyne.harness.verify` and
# `heterodyne.wsd.runtime`'s `INSTALL_TAINTED` and `InstallTainted(session_key, generation, adapter, install)`
# (Task 8). ---

APPROVALS = "approvals"                 # `[harnesses] approval_workstream`
APPROVED_POINT = "harness.approved"     # the checkpoint just after a row's `approved` is journaled
USE_INTENT_POINT = "harness.use.intent"     # just after a `use` op is journaled open, before the selection
ACTOR_CTL = "wsctl"                     # the actor of an op a `wsctl harness` request runs
ACTOR_WSD = "wsd"                       # the actor of what `advance` and `recover` do on their own
CTL_ACTIONS = ("install", "verify", "use", "rollback", "remove", "prune")     # each op's event action
TAINTED = verify.INSTALL_TAINTED        # `use`'s refusal of a tainted install (Task 8's constant)
READOUT_DEADLINE = "READOUT_SECONDS"    # `wsd.btq`'s deadline for one `approve-bead --json` read


def make_ops(h: "H") -> Any:
    """`HarnessOps` as `daemon.assemble` builds it. `queues` is wsd's queue factory: the approval bead goes
    through `queues(approval_workstream, <wsd's session>)`. `readout` is `approval_readout` with the
    `approve-bead` path bound. `guard_locks` gives every workstream's operation lock, which `remove` and
    `prune` hold (E16)."""
    return wh.HarnessOps(
        settings=h.settings, store=h.store, fetcher=h.fetcher, queues=factory(h.world), journal=h.journal,
        readout=partial(wbtq.approval_readout, binary=h.approve_bead), ask_socket=h.asks.path,
        config_dir=h.config, taint_dir=h.taint, alerts_dir=h.alerts, runtime=h.runtime, clock=h.clock,
        commit=COMMIT, cp=h.cp, guard_locks=h.guard_locks)


def row(h: "H", staging_id: str) -> Any:
    """The `harness_installs` row: `.state`, `.failure`, `.install`, `.digest`, `.approval_bead`,
    `.context_digest`, `.ask_id`, `.adapter`, `.version`."""
    return h.journal.harness_install(staging_id)


def events(h: "H") -> list[Any]:
    """`harness_events`, oldest first: `.actor`, `.action`, `.adapter`, `.install`, `.detail`."""
    return list(h.journal.harness_events())


def refused(call: Callable[[], object]) -> str:
    with pytest.raises(wh.HarnessRefused) as e:
        call()
    return e.value.constant


def wire(rig: Rig, ops: Any) -> None:
    """The rig's deps with `Deps.harness`, and the Parker and Scheduler rebuilt on them."""
    rig.deps = replace(rig.deps, harness=ops)
    rig.parker = Parker(rig.ws, rig.deps)
    rig.sched = Scheduler(rig.ws, rig.deps, rig.parker)


def assembled(s: WsdSettings, journal: Journal, runtime: FakeRuntime, world: World, harness: Any) -> Wsd:
    return Wsd(s, assemble(s, journal, factory(world), runtime, harness=harness))


# --- the rig ---

COMMIT = hashlib.sha1(b"the running harness commit").hexdigest()  # noqa: S324 - a commit ID's form
TARGET = str(CODEX_PACKAGE["target"])
OPERATOR = "operator-a"
PREDICATE_LOG = "predicate {}"          # how the log names the failing predicate (E13)


class Opener:
    """The fixture opener: an exact URL, else the longest suffix that ends the URL (the release's base is
    the adapter's to build). Every URL asked is kept."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.asked: list[str] = []

    def __call__(self, url: str, timeout: float) -> Response:
        self.asked.append(url)
        assert url.startswith("https://") and timeout > 0
        if url in self.files:
            return Response(self.files[url])
        for suffix in sorted(self.files, key=len, reverse=True):
            if url.endswith("/" + suffix):
                return Response(self.files[suffix])
        raise OSError(404, "not found")


def codex_files(artifact: bytes) -> dict[str, bytes]:
    other = hashlib.sha256(b"another package").hexdigest()
    sums = (f"{other}  codex-package-aarch64-unknown-linux-musl.tar.gz\n"
            f"{hashlib.sha256(artifact).hexdigest()}  {ASSET}\n").encode()
    return {f"rust-v0.160.0/{ASSET}": artifact, f"rust-v0.160.0/{SUMS}": sums}


class AskSocket:
    """admind's `ask.sock`, faked: one JSON line in, one JSON line out per connection. Every post is kept."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.posts: list[dict[str, Any]] = []
        self.reply: dict[str, Any] = {"result": "posted", "message": "posted"}
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(8)
        self.sock.settimeout(0.1)
        self.stopping = False
        self.thread = threading.Thread(target=self._serve, name="ask-sock", daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.stopping:
            try:
                conn, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(10)
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                self.posts.append(json.loads(data))
                conn.sendall(json.dumps(self.reply).encode() + b"\n")

    def close(self) -> None:
        self.stopping = True
        self.thread.join(5)
        self.sock.close()


def approve_bead_wrapper(root: Path, db: Path, log: Path) -> Path:
    fake = Path(__file__).parent / "fakes" / "fake_approve_bead.py"
    wrapper = root / "approve-bead"
    wrapper.write_text(f"#!/bin/sh\nFAKE_BTQ_DB={shlex.quote(str(db))} FAKE_BTQ_LOG={shlex.quote(str(log))} "
                       f"exec {shlex.quote(sys.executable)} {shlex.quote(str(fake))} \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


def skill_repo(root: Path, *, object_format: str = "sha1", text: bytes = b"# review\n") -> tuple[Path, str]:
    """A git repository whose tree is a Codex skill (it has `SKILL.md`), and its commit."""
    repo, _ = make_repo(root, object_format=object_format)
    (repo / "SKILL.md").write_bytes(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "skill")
    return repo, git(repo, "rev-parse", "HEAD").decode().strip()


def skill(name: str, repo: Path, commit: str) -> ContentSource:
    return ContentSource("skills", name, str(repo), commit, None, None)


@dataclass
class H:
    tmp: Path
    world: World
    journal: Journal
    runtime: FakeRuntime
    clock: Clock
    asks: AskSocket
    rig: Rig | None = None
    cp: Any = field(default_factory=Recorder)
    recipes: dict[str, Recipe] = field(default_factory=dict)
    approval_workstream: str = APPROVALS
    opener: Opener = field(init=False)
    store: LocalStore = field(init=False)
    ops: Any = field(init=False)

    def __post_init__(self) -> None:
        for d in (self.config, self.taint.parent, self.alerts):
            d.mkdir(parents=True, exist_ok=True)
        self.db.write_text("{}")
        self.approve_bead = approve_bead_wrapper(self.tmp, self.db, self.log)
        codex = tar_of(CODEX_TREE, prefix=CODEX_STRIP)
        claude = tar_of(CLAUDE_TREE, prefix=CLAUDE_STRIP, dirs=False)
        self.codex_artifact, self.claude_artifact = codex, claude
        self.opener = Opener({**codex_files(codex), **npm_files(claude, registry=REGISTRY)})
        store_dir(self.tmp)
        self.store = LocalStore(self.tmp / "store", clock=self.clock)
        self.ops = make_ops(self)

    # where things are
    @property
    def config(self) -> Path:
        return self.tmp / "config"

    @property
    def taint(self) -> Path:
        return self.tmp / "state" / "harness-taint"

    @property
    def alerts(self) -> Path:
        return self.tmp / "alerts"

    @property
    def db(self) -> Path:
        return self.tmp / "btq.json"

    @property
    def log(self) -> Path:
        return self.tmp / "btq.log"

    @property
    def settings(self) -> HarnessSettings:
        return HarnessSettings(self.tmp / "store", True, "", self.tmp / "helper.sock",
                               self.tmp / "store_core.py",
                               24, self.approval_workstream, self.recipes)

    @property
    def fetcher(self) -> Fetcher:
        return Fetcher(self.opener, self.tmp / "cache", self.clock, 60.0, npm_registry=REGISTRY)

    def guard_locks(self) -> Sequence[AbstractContextManager[Any]]:
        return [self.rig.parker.lock] if self.rig is not None else []

    def rebuild(self, cp: Any = None) -> None:
        """New ops on the same journal (after the rig restarted, or with another checkpoint)."""
        if self.rig is not None:
            self.journal = self.rig.journal
        self.cp = cp if cp is not None else Recorder()
        self.ops = make_ops(self)

    def restart(self, cp: Any = None) -> None:
        """wsd dies and starts again: a new journal on the same file and new ops. The store, the queue and
        admind carry on."""
        if self.rig is not None:
            self.rig.restart(self.rig.cp)
        else:
            self.journal.close()
            self.journal = Journal(self.tmp / "state" / "wsd.db")
        self.rebuild(cp)

    def queue(self) -> Any:
        return factory(self.world)(APPROVALS, "test-reader")

    def approve_log(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def set_readout(self, bead: str, entry: dict[str, Any]) -> None:
        beads = json.loads(self.db.read_text())
        beads[bead] = entry
        self.db.write_text(json.dumps(beads))


def default_recipes(tmp: Path) -> dict[str, Recipe]:
    repo, commit = skill_repo(tmp / "skills" / "review")
    return {"codex": Recipe("0.160.0", TARGET, (skill("review", repo, commit),), None),
            "claude-code": Recipe("2.1.286", CLAUDE_TARGET, (), None)}


@pytest.fixture
def ask_dir() -> Iterator[Path]:
    with short_dir() as d:
        yield d


def build(tmp: Path, ask_root: Path, rig: Rig | None = None) -> H:
    asks = AskSocket(ask_root / "ask.sock")
    if rig is None:
        world, journal = World(tmp / "btq-state"), Journal(tmp / "state" / "wsd.db")
        h = H(tmp, world, journal, FakeRuntime(), Clock(), asks, recipes=default_recipes(tmp))
    else:
        h = H(tmp, rig.world, rig.journal, rig.runtime, rig.clock, asks, rig=rig,
              recipes=default_recipes(tmp))
    return h


@pytest.fixture
def h(tmp_path: Path, ask_dir: Path) -> Iterator[H]:
    rig = build(tmp_path, ask_dir)
    try:
        yield rig
    finally:
        rig.asks.close()
        unseal(tmp_path / "store")


def on_rig(tmp_path: Path, ask_root: Path, profile: str = "p-two") -> tuple[Rig, H]:
    """The harness on `wsd_env`'s rig: btq-1 ready on `profile` (Codex), whose guard reads the selection from
    the harness's config dir (T7), with `Deps.harness` wired."""
    rig = started(tmp_path, profile=profile)
    rig.selections = selections_at(tmp_path / "config")
    rig.restart()
    h = build(tmp_path, ask_root, rig)
    wire(rig, h.ops)
    return rig, h


@pytest.fixture
def rigged(tmp_path: Path, ask_dir: Path) -> Iterator[tuple[Rig, H]]:
    rig, h = on_rig(tmp_path, ask_dir)
    try:
        yield rig, h
    finally:
        h.asks.close()
        unseal(tmp_path / "store")


def rewire(rig: Rig, h: H, cp: Any = None) -> None:
    """After `rig.restart()`: the ops on the new journal, wired again."""
    h.rebuild(cp)
    wire(rig, h.ops)


# --- steps ---

def settle(h: H, staging_id: str, seconds: float = 60.0) -> Any:
    """The row once `install`'s worker is done with it (`posted` or `failed`)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r = row(h, staging_id)
        if r is not None and r.state in ("posted", "failed"):
            return r
        time.sleep(0.02)
    raise AssertionError(f"install {staging_id} did not settle: {row(h, staging_id)}")


def posted(h: H, adapter: str = "codex", version: str | None = None) -> tuple[str, Any]:
    staging_id = h.ops.install(adapter, version)
    r = settle(h, staging_id)
    assert r.state == "posted", r.failure
    return staging_id, r


def staged_manifest(h: H, staging_id: str) -> sc.Manifest:
    return sc.parse((h.tmp / "store" / ".staged" / f"{staging_id}.json").read_bytes())


def show(h: H, bead: str) -> dict[str, Any]:
    return h.queue().show(bead)


def ask_of(bead: dict[str, Any]) -> dict[str, Any]:
    ask = bead["metadata"]["ask"]
    return json.loads(ask) if isinstance(ask, str) else ask


def approved_entry(h: H, bead: str) -> dict[str, Any]:
    """The readout of an approval closed by an operator, as `approve-bead --json` gives it."""
    digest, gaps = h.queue().ask_digest(show(h, bead))
    assert digest is not None and gaps == []
    return {"status": "closed", "labels": ["kind:approval"], "kind_approval": True, "digest": digest,
            "posted_digest": digest, "approved_digest": digest, "approvers": [OPERATOR],
            "decision": "approve",
            "approved_by": OPERATOR, "denied_by": None, "denied_digest": None, "via": "marmot",
            "gate_valid": True}


def approve(h: H, bead: str, **changes: Any) -> None:
    """The operator approves `bead`: the readout says so (with `changes` applied) and the bead is closed."""
    h.set_readout(bead, {**approved_entry(h, bead), **changes})
    h.world.close(bead)


def published(h: H, adapter: str = "codex") -> tuple[str, str]:
    """A whole install: staged, posted, approved and published. Returns (staging ID, install name)."""
    staging_id, r = posted(h, adapter)
    approve(h, r.approval_bead)
    h.ops.advance(h.clock())
    done = row(h, staging_id)
    assert done.state == "published", done.failure
    return staging_id, done.install


def pin_of(h: H, adapter: str, install: str) -> InstallPin:
    [info] = [i for i in h.store.list() if i.adapter == adapter and i.name == install]
    return InstallPin(install, info.digest)


def fake_pin(n: int) -> InstallPin:
    hexed = hashlib.sha256(f"fake install {n}".encode()).hexdigest()
    return InstallPin(f"0.160.0-{hexed[:12]}", "sha256:" + hexed)


def select(h: H, adapter: str, current: InstallPin | None, previous: tuple[InstallPin, ...] = ()) -> None:
    selection.write(h.config, {adapter: Selection(adapter, current, previous)})


def selected(h: H, adapter: str) -> Selection | None:
    return selection.load(h.config).get(adapter)


def installs(h: H) -> set[str]:
    return {i.name for i in h.store.list()}


def with_recipe(h: H, adapter: str, recipe: Recipe) -> None:
    h.recipes = {**h.recipes, adapter: recipe}
    h.ops = make_ops(h)
    if h.rig is not None:
        wire(h.rig, h.ops)


def second_codex(h: H, name: str = "second") -> None:
    """The Codex recipe with another skill commit: the next install has another digest."""
    repo, commit = skill_repo(h.tmp / "skills" / name, text=f"# {name}\n".encode())
    with_recipe(h, "codex", Recipe("0.160.0", TARGET, (skill("review", repo, commit),), None))


# --- creating the approval (E13, E14) ---

def test_install_stages_creates_the_approval_bead_and_posts_the_ask(h: H) -> None:
    staging_id, r = posted(h)
    assert h.world.created == [r.approval_bead]
    bead = show(h, r.approval_bead)
    assert "kind:approval" in bead["labels"]
    ask = ask_of(bead)
    assert tuple(sorted(ask)) == tuple(sorted(ASK_FIELDS))
    m = staged_manifest(h, staging_id)
    shown = digests.display(digests.of_manifest(m))
    install = sc.install_name(m.version, sc.digest(m))
    assert ask["effect"] == f"publish codex install {install} manifest {shown}"
    assert digests.parse_display(ask["effect"].rsplit(" manifest ", 1)[1]) == digests.of_manifest(m)
    assert ask["refs"] == [{"kind": "commit", "repo": "Epiphytic/heterodyne-metaharness", "id": COMMIT}]
    digest, gaps = h.queue().ask_digest(bead)
    assert gaps == [] and bead["metadata"]["context_digest"] == digest == r.context_digest
    assert bead["title"] == f"Add the codex 0.160.0 managed install ({install})"
    description = bead["description"]
    assert f"Manifest digest: {shown}" in description
    assert secret_scan.identifier_kind(description) is None
    assert secret_scan.sensitive_kind(description) is None
    assert (r.adapter, r.version, r.digest) == ("codex", "0.160.0", sc.digest(m))
    [post] = h.asks.posts
    assert post == {"type": "post", "kind": "approval", "bead": r.approval_bead}


def test_install_passes_every_adapters_login_names_to_stage(h: H, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[frozenset[str]] = []
    real = LocalStore.stage

    def stage(self: LocalStore, *args: Any, **kwargs: Any) -> sc.Staged:
        seen.append(frozenset(kwargs["login_names"] if "login_names" in kwargs else args[4]))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(LocalStore, "stage", stage)
    posted(h, "codex")
    posted(h, "claude-code")
    assert seen == [login_names(), login_names()]
    assert {"auth.json", ".credentials.json"} <= login_names()


@pytest.mark.parametrize("adapter", ["codex", "claude-code"])
def test_upstream_checksums_are_compared_but_never_shown(h: H, adapter: str) -> None:
    _, r = posted(h, adapter)
    bead = show(h, r.approval_bead)
    text = bead["title"] + "\n" + bead["description"] + "\n" + json.dumps(ask_of(bead))
    if adapter == "codex":
        upstream = hashlib.sha256(h.codex_artifact).hexdigest()
        assert "checksum matches" in text and SUMS in text
        assert upstream not in text and upstream not in text.replace("sha256:", "")
        # the cli line's file digest is wsd's own, grouped (E14), never the SHA256SUMS line's run
        fd = os.open(h.tmp / "codex.tar.gz", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            os.write(fd, h.codex_artifact)
            os.lseek(fd, 0, os.SEEK_SET)
            assert digests.display(digests.of_fd(fd)) in text
        finally:
            os.close(fd)
    else:
        integrity = sri_of(h.claude_artifact)
        assert "checksum matches" in text and REGISTRY in text
        assert integrity not in text and integrity.removeprefix("sha512-") not in text


def secret_cases(tmp: Path) -> dict[str, ContentSource]:
    """Upstream strings admind's scanner flags, as a recipe supplies them: a content name holding an `sk-`
    token or an npub, a repository path holding a 64-hex run, and a sha256 repository's commit ID."""
    repo, commit = skill_repo(tmp / "skills" / "plain")
    hexed = hashlib.sha256(b"a path component").hexdigest()
    hex_repo, hex_commit = skill_repo(tmp / "skills" / hexed / "review")
    sha256_repo, sha256_commit = skill_repo(tmp / "skills" / "sha256", object_format="sha256")
    return {"sk-name": skill("sk-" + "a1b2c3d4e5f6g7h8i9j0", repo, commit),
            "npub-name": skill("npub1" + "qpzry9x8gf2tvdw0s3jn54khce6mua7l", repo, commit),
            "hex-repo": skill("review", hex_repo, hex_commit),
            "sha256-commit": skill("review", sha256_repo, sha256_commit)}


@pytest.mark.parametrize("case", ["sk-name", "npub-name", "hex-repo", "sha256-commit"])
def test_a_secret_looking_upstream_string_is_withheld_not_edited(h: H, case: str) -> None:
    source = secret_cases(h.tmp)[case]
    with_recipe(h, "codex", Recipe("0.160.0", TARGET, (source,), None))
    staging_id = h.ops.install("codex", None)
    r = settle(h, staging_id)
    assert (r.state, r.failure) == ("failed", wh.ASK_UNPOSTABLE)
    assert h.world.created == [] and h.asks.posts == []
    assert [c for _, c in h.world.calls if c == "create"] == []


def test_the_sources_lines_say_signature_available_not_verified(h: H) -> None:
    _, r = posted(h, "claude-code")
    assert "signature available, not verified" in show(h, r.approval_bead)["description"]
    h.opener.files.update(npm_files(h.claude_artifact, registry=REGISTRY, signed=False))
    _, unsigned = posted(h, "claude-code")
    text = show(h, unsigned.approval_bead)["description"]
    assert "no signature published" in text and "signature available" not in text
    _, codex = posted(h, "codex")
    assert "trust on first install (unsigned)" in show(h, codex.approval_bead)["description"]


# --- what wsd never writes (E13) ---

def test_wsd_never_writes_to_an_approval_bead_after_creating_it(h: H) -> None:
    published(h)                                             # approved and published
    denied_id, denied = posted(h)
    entry = approved_entry(h, denied.approval_bead)
    h.set_readout(denied.approval_bead, {**entry, "decision": "deny", "approved_by": None,
                                         "approved_digest": None, "denied_by": OPERATOR,
                                         "denied_digest": entry["digest"]})
    h.world.close(denied.approval_bead)
    failing_id, failing = posted(h)
    approve(h, failing.approval_bead, approvers=[])          # predicate 9
    h.ops.advance(h.clock())
    assert (row(h, denied_id).state, row(h, failing_id).state) == ("denied", "failed")
    calls = [c for _, c in h.world.calls]
    assert calls.count("create") == 3
    assert set(calls) <= {"create", "show"}, sorted(set(calls))
    assert all(argv[1:] == ["--json"] and len(argv) == 2 for argv in h.approve_log())
    assert {argv[0] for argv in h.approve_log()} <= set(h.world.created)


# --- reading the decision (E13's predicates 2-10) ---

def other_digest() -> str:
    return hashlib.sha256(b"another ask").hexdigest()


# (predicate, case, the readout's change, the expected end): "failed" ends `failed` with NOT_APPROVED and the
# predicate's number in the log; "posted" waits; "denied" is a denial. A change is {"set": {...}} (fields
# set last), {"drop": [...]} (fields left out) or {"bead": {...}} (the bead's ask fields changed, the
# digests recomputed and the journal's context digest moved with them: only predicate 10 sees it).
PREDICATE_CASES: list[tuple[int, str, dict[str, Any], str]] = [
    (2, "missing", {"drop": ["id"]}, "failed"),
    (2, "null", {"set": {"id": None}}, "failed"),
    (2, "type", {"set": {"id": 7}}, "failed"),
    (2, "value", {"set": {"id": "btq-other"}}, "failed"),
    (3, "missing", {"drop": ["kind_approval"]}, "failed"),
    (3, "null", {"set": {"kind_approval": None}}, "failed"),
    (3, "type", {"set": {"kind_approval": "true"}}, "failed"),
    (3, "value", {"set": {"kind_approval": False}}, "failed"),
    (4, "missing", {"drop": ["status"]}, "failed"),
    (4, "null", {"set": {"status": None}}, "failed"),
    (4, "type", {"set": {"status": 1}}, "failed"),
    (4, "value", {"set": {"status": "open", "decided": False}}, "posted"),
    (4, "decided-missing", {"drop": ["decided"]}, "failed"),
    (4, "decided-value", {"set": {"decided": False}}, "failed"),
    (5, "missing", {"drop": ["decision"]}, "failed"),
    (5, "null", {"set": {"decision": None}}, "failed"),
    (5, "type", {"set": {"decision": 1}}, "failed"),
    (5, "value", {"set": {"decision": "deny"}}, "denied"),
    (5, "denied-missing", {"drop": ["denied_by", "denied_digest"]}, "failed"),
    (6, "missing", {"drop": ["digest"]}, "failed"),
    (6, "null", {"set": {"digest": None}}, "failed"),
    (6, "type", {"set": {"digest": 5}}, "failed"),
    (6, "value", {"set": {"digest": "<other>"}}, "failed"),
    (6, "empty", {"set": {"digest": ""}}, "failed"),
    (6, "gaps", {"set": {"gaps": ["metadata.ask.risks is empty"]}}, "failed"),
    (6, "gaps-missing", {"drop": ["gaps"]}, "failed"),
    (6, "gaps-null", {"set": {"gaps": None}}, "failed"),
    (7, "missing", {"drop": ["posted_digest"]}, "failed"),
    (7, "null", {"set": {"posted_digest": None}}, "failed"),
    (7, "type", {"set": {"posted_digest": 5}}, "failed"),
    (7, "value", {"set": {"posted_digest": "<other>"}}, "failed"),
    (8, "missing", {"drop": ["approved_digest"]}, "failed"),
    (8, "null", {"set": {"approved_digest": None}}, "failed"),
    (8, "type", {"set": {"approved_digest": 5}}, "failed"),
    (8, "value", {"set": {"approved_digest": "<other>"}}, "failed"),
    (9, "missing", {"drop": ["approvers"]}, "failed"),
    (9, "null", {"set": {"approvers": None}}, "failed"),
    (9, "type", {"set": {"approvers": OPERATOR}}, "failed"),
    (9, "value", {"set": {"approvers": []}}, "failed"),
    (9, "empty-name", {"set": {"approvers": [""], "approved_by": ""}}, "failed"),
    (9, "by-missing", {"drop": ["approved_by"]}, "failed"),
    (9, "by-null", {"set": {"approved_by": None}}, "failed"),
    (9, "by-type", {"set": {"approved_by": 3}}, "failed"),
    (9, "by-value", {"set": {"approved_by": "operator-b"}}, "failed"),
    # A missing effect (or ask) is a gap, so predicate 6's cases cover it; these keep the ask's fields exact.
    (10, "null", {"bead": {"effect": None}}, "failed"),
    (10, "type", {"bead": {"effect": 5}}, "failed"),
    (10, "other-adapter", {"bead": {"effect": "<adapter:claude-code>"}}, "failed"),
    (10, "other-install", {"bead": {"effect": "<install:0.160.0-000000000000>"}}, "failed"),
    (10, "other-digest", {"bead": {"effect": "<display:other>"}}, "failed"),
    (10, "ungrouped", {"bead": {"effect": "<display:ungrouped>"}}, "failed"),
    (10, "trailing", {"bead": {"effect": "<suffix: >"}}, "failed"),
    (10, "verb", {"bead": {"effect": "<verb:use>"}}, "failed"),
]


def rewritten_effect(effect: str, spec: str) -> str:
    verb, adapter, _, install, _, shown = effect.split(" ", 5)
    kind, _, arg = spec.strip("<>").partition(":")
    if kind == "adapter":
        adapter = arg
    elif kind == "install":
        install = arg
    elif kind == "display" and arg == "other":
        hexed = other_digest()
        shown = "sha256:" + " ".join(hexed[i:i + 4] for i in range(0, 64, 4))
    elif kind == "display":
        shown = shown.replace(" ", "")
    elif kind == "suffix":
        return effect + arg
    elif kind == "verb":
        verb = arg
    return " ".join([verb, adapter, "install", install, "manifest", shown])


def edit_ask(h: H, staging_id: str, bead: str, changes: dict[str, Any]) -> None:
    """The bead's ask edited; its digests (and the journaled context digest) moved with it, so only
    predicate 10 can see the edit."""
    b = h.world.beads[bead]
    ask = dict(ask_of({"metadata": b.metadata}))
    for key, value in changes.items():
        edited = isinstance(value, str) and value.startswith("<")
        ask[key] = rewritten_effect(ask[key], value) if edited else value
    b.metadata = {**b.metadata, "ask": ask}
    digest = h.queue().ask_digest(show(h, bead))[0]
    db = sqlite3.connect(h.tmp / "state" / "wsd.db", isolation_level=None)
    try:
        db.execute("UPDATE harness_installs SET context_digest = ? WHERE staging_id = ?",
                   (digest, staging_id))
    finally:
        db.close()


@pytest.mark.parametrize("predicate, case, change, end", PREDICATE_CASES,
                         ids=[f"{p}-{c}" for p, c, _, _ in PREDICATE_CASES])
def test_nothing_is_published_unless_every_predicate_holds(h: H, caplog: pytest.LogCaptureFixture,
                                                           predicate: int, case: str, change: dict[str, Any],
                                                           end: str) -> None:
    caplog.set_level(logging.DEBUG)
    staging_id, r = posted(h)
    bead = r.approval_bead
    if "bead" in change:
        edit_ask(h, staging_id, bead, change["bead"])
    entry = approved_entry(h, bead)
    sets = {k: (other_digest() if v == "<other>" else v) for k, v in change.get("set", {}).items()}
    if sets.get("decision") == "deny":
        sets.update(approved_by=None, approved_digest=None, denied_by=OPERATOR, denied_digest=entry["digest"])
    if sets.get("status") == "open":
        entry = {k: v for k, v in entry.items() if k not in ("decision", "approved_by", "approved_digest",
                                                             "denied_by", "denied_digest", "via")}
        h.set_readout(bead, {**entry, "override": sets})
    else:
        h.set_readout(bead, {**entry, "override": sets, "drop": change.get("drop", [])})
        h.world.close(bead)
    h.ops.advance(h.clock())
    after = row(h, staging_id)
    assert after.state == end
    if end == "failed":
        assert after.failure == wh.NOT_APPROVED
        assert PREDICATE_LOG.format(predicate) in caplog.text
    assert installs(h) == set() and not after.install


def test_a_busy_readout_waits(h: H) -> None:
    staging_id, r = posted(h)
    h.set_readout(r.approval_bead, {**approved_entry(h, r.approval_bead), "busy": True})
    h.world.close(r.approval_bead)
    h.ops.advance(h.clock())
    assert row(h, staging_id).state == "posted" and installs(h) == set()
    h.set_readout(r.approval_bead, approved_entry(h, r.approval_bead))
    h.ops.advance(h.clock())
    assert row(h, staging_id).state == "published"


def reaped(pids_file: Path) -> bool:
    for line in pids_file.read_text().split():
        try:
            os.kill(int(line), 0)
        except ProcessLookupError:
            continue
        return False
    return True


def test_a_slow_readout_is_busy_within_its_short_deadline_and_reaped(h: H, monkeypatch: pytest.MonkeyPatch,
                                                                    ) -> None:
    """g08sd (1): `advance` runs from every pickup, so the read's deadline is short. A fake that never
    answers is `Busy` once it passes; the row waits, and the fake's process is gone."""
    assert 0 < getattr(wbtq, READOUT_DEADLINE) <= 10
    staging_id, r = posted(h)
    approve(h, r.approval_bead, read="hang")
    monkeypatch.setattr(wbtq, READOUT_DEADLINE, 1.0)
    started_at = time.monotonic()
    assert isinstance(wbtq.approval_readout(r.approval_bead, binary=h.approve_bead), wbtq.Busy)
    h.ops.advance(h.clock())
    assert time.monotonic() - started_at < 2 * (1.0 + 5.0)          # two reads, each within deadline + reap
    assert row(h, staging_id).state == "posted" and installs(h) == set()
    assert reaped(h.tmp / "pids")
    assert h.approve_log() == [[r.approval_bead, "--json"]] * 2


def test_approval_readout_parses_a_busy_reply_and_an_approval(h: H) -> None:
    _, r = posted(h)
    h.set_readout(r.approval_bead, {"busy": True})
    assert isinstance(wbtq.approval_readout(r.approval_bead, binary=h.approve_bead), wbtq.Busy)
    approve(h, r.approval_bead)
    got = wbtq.approval_readout(r.approval_bead, binary=h.approve_bead)
    assert isinstance(got, wbtq.Readout) and got.id == r.approval_bead and got.decision == "approve"
    h.set_readout(r.approval_bead, {**approved_entry(h, r.approval_bead), "read": "garbage"})
    with pytest.raises(Exception):  # noqa: B017 - any refusal: a reply that isn't JSON is never a readout
        wbtq.approval_readout(r.approval_bead, binary=h.approve_bead)


def test_every_pickup_advances_the_installs_once(tmp_path: Path) -> None:
    class Recording:
        def __init__(self) -> None:
            self.advanced: list[int] = []

        def advance(self, now: int) -> None:
            self.advanced.append(now)

    rig = started(tmp_path, profile="p-two")
    fake = Recording()
    wire(rig, fake)
    assert rig.pickup() is Outcome.STARTED
    rig.clock.advance(30)
    rig.pickup()
    assert fake.advanced == [rig.clock() - 30, rig.clock()]


def test_an_approval_with_null_denied_fields_is_approved(h: H) -> None:
    staging_id, r = posted(h)
    approve(h, r.approval_bead)
    readout = wbtq.approval_readout(r.approval_bead, binary=h.approve_bead)
    assert isinstance(readout, wbtq.Readout) and (readout.denied_by, readout.denied_digest) == (None, None)
    h.ops.advance(h.clock())
    assert row(h, staging_id).state == "published"


@pytest.mark.parametrize("field_name", ["denied_by", "denied_digest"])
def test_a_non_null_denied_field_is_a_denial(h: H, field_name: str) -> None:
    staging_id, r = posted(h)
    entry = approved_entry(h, r.approval_bead)
    approve(h, r.approval_bead, **{field_name: OPERATOR if field_name == "denied_by" else entry["digest"]})
    h.ops.advance(h.clock())
    assert row(h, staging_id).state == "denied" and installs(h) == set()


def test_an_ask_edited_after_posting_is_not_approved(h: H, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    staging_id, r = posted(h)
    entry = approved_entry(h, r.approval_bead)             # the digest the operator approved
    b = h.world.beads[r.approval_bead]
    b.metadata = {**b.metadata, "ask": {**ask_of({"metadata": b.metadata}), "why": "something else"}}
    h.set_readout(r.approval_bead, entry)
    h.world.close(r.approval_bead)
    h.ops.advance(h.clock())
    after = row(h, staging_id)
    assert (after.state, after.failure) == ("failed", wh.NOT_APPROVED) and installs(h) == set()
    assert PREDICATE_LOG.format(6) in caplog.text or PREDICATE_LOG.format(7) in caplog.text


# --- publishing (E21) ---

def test_an_approved_install_is_published_with_the_staged_digest(h: H) -> None:
    staging_id, r = posted(h)
    m = staged_manifest(h, staging_id)
    approve(h, r.approval_bead)
    h.ops.advance(h.clock())
    done = row(h, staging_id)
    assert done.state == "published" and done.install == sc.install_name(m.version, sc.digest(m))
    [info] = h.store.list()
    assert (info.name, info.digest, info.state) == (done.install, sc.digest(m), "published")
    assert h.store.outcome(staging_id) == f"published:{done.install}"
    assert selected(h, "codex") is None                    # publishing selects nothing (E13's excludes)


def test_a_restart_in_approved_publishes_once(h: H) -> None:
    staging_id, r = posted(h)
    approve(h, r.approval_bead)
    h.rebuild(CrashAt(APPROVED_POINT))
    with pytest.raises(SimulatedCrash):
        h.ops.advance(h.clock())
    assert row(h, staging_id).state == "approved" and installs(h) == set()
    h.restart()
    h.ops.recover()
    done = row(h, staging_id)
    assert done.state == "published" and installs(h) == {done.install}
    h.ops.recover()
    h.ops.advance(h.clock())
    assert [e.action for e in events(h) if e.action == "published"] == ["published"]
    assert installs(h) == {done.install}


@pytest.mark.parametrize("point", list(CRASH_POINTS))
def test_a_crash_inside_publish_is_resolved_from_the_store_not_from_the_name(h: H, point: str) -> None:
    staging_id, r = posted(h)
    m = staged_manifest(h, staging_id)
    approve(h, r.approval_bead)
    h.rebuild(CrashAt(APPROVED_POINT))
    with pytest.raises(SimulatedCrash):
        h.ops.advance(h.clock())
    crash_publish(h.tmp, "publish", [staging_id, sc.digest(m)], point)     # the publish wsd was running
    h.restart()
    h.ops.recover()
    done = row(h, staging_id)
    name = sc.install_name(m.version, sc.digest(m))
    assert (done.state, done.install) == ("published", name)
    assert h.store.state("codex", name) == "published"
    assert h.store.outcome(staging_id) == f"published:{name}"


def test_a_tree_that_recovery_refuses_is_failed_never_published(h: H) -> None:
    """The name is in place, but the store's recovery refuses the tree: the row records the store's
    failure, never `published` from the install's name."""
    staging_id, r = posted(h)
    m = staged_manifest(h, staging_id)
    name = sc.install_name(m.version, sc.digest(m))
    approve(h, r.approval_bead)
    h.rebuild(CrashAt(APPROVED_POINT))
    with pytest.raises(SimulatedCrash):
        h.ops.advance(h.clock())
    crash_publish(h.tmp, "publish", [staging_id, sc.digest(m)], "P3")
    root = h.tmp / "store" / "codex" / name
    rewrite(next(p for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()))
    h.restart()
    h.ops.recover()
    done = row(h, staging_id)
    assert done.state == "failed" and not done.install
    assert h.store.outcome(staging_id) == f"failed:{done.failure}"
    assert h.store.state("codex", name) != "published" if root.exists() else True


def test_a_name_conflict_fails_the_install(h: H) -> None:
    staging_id, r = posted(h)
    m = staged_manifest(h, staging_id)
    taken = h.tmp / "store" / "codex" / sc.install_name(m.version, sc.digest(m))
    taken.mkdir(parents=True)
    approve(h, r.approval_bead)
    h.ops.advance(h.clock())
    after = row(h, staging_id)
    assert (after.state, after.failure) == ("failed", sc.NAME_CONFLICT) and not after.install


# --- use, rollback, remove and prune (E11, E16) ---

def test_use_and_rollback_write_the_selection_and_take_effect_at_the_next_launch(
        rigged: tuple[Rig, H]) -> None:
    rig, h = rigged
    _, a = published(h)
    second_codex(h)
    _, b = published(h)
    h.ops.use("codex", a)
    sel = selected(h, "codex")
    assert sel is not None and sel.current == pin_of(h, "codex", a) and sel.previous == ()
    h.ops.use("codex", b)
    sel = selected(h, "codex")
    assert sel is not None and sel.current == pin_of(h, "codex", b)
    assert sel.previous[0] == pin_of(h, "codex", a)
    assert rig.pickup() is Outcome.STARTED
    [spec] = rig.runtime.launches
    assert (spec.install, spec.install_digest) == (b, pin_of(h, "codex", b).digest)
    h.ops.rollback("codex")
    sel = selected(h, "codex")
    assert sel is not None and sel.current == pin_of(h, "codex", a)
    assert sel.previous[0] == pin_of(h, "codex", b)
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    rig.pickup()
    assert [(s.install, s.generation) for s in rig.runtime.launches] == [(b, 1), (a, 2)]


def test_use_refuses_an_undeclared_version_a_tainted_install_and_content_owning_a_key(
        h: H, monkeypatch: pytest.MonkeyPatch) -> None:
    _, a = published(h)
    assert refused(lambda: h.ops.use("codex", fake_pin(1).install)) == wh.NOT_PUBLISHED
    codex = type(ADAPTERS["codex"])
    with monkeypatch.context() as m:
        m.setattr(codex, "versions", {})
        assert refused(lambda: h.ops.use("codex", a)) == agents_base.UNDECLARED_VERSION
    with monkeypatch.context() as m:
        def owns(self: object, kind: str, tree: Path) -> None:
            raise agents_base.AdapterError(agents_base.OWNS_KEY)
        m.setattr(codex, "check_content", owns)
        assert refused(lambda: h.ops.use("codex", a)) == agents_base.OWNS_KEY
    verify.taint(h.taint, "codex", a, verify.INSTALL_CHANGED)
    assert refused(lambda: h.ops.use("codex", a)) == TAINTED
    assert selected(h, "codex") is None
    assert refused(lambda: h.ops.rollback("codex")) != ""          # nothing to roll back to


def entry_without_outcome(rig: Rig, h: H, a: str) -> None:
    select(h, "codex", pin_of(h, "codex", a))
    rig.restart(CrashAt("pickup.entry"))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    rewire(rig, h)
    select(h, "codex", fake_pin(3), (fake_pin(2), pin_of(h, "codex", a)))


def launched_and_listed(rig: Rig, h: H, a: str) -> None:
    select(h, "codex", pin_of(h, "codex", a))
    assert rig.pickup() is Outcome.STARTED
    select(h, "codex", fake_pin(3), (fake_pin(2), pin_of(h, "codex", a)))


def the_selection(rig: Rig, h: H, a: str) -> None:
    select(h, "codex", pin_of(h, "codex", a))


def the_previous_selection(rig: Rig, h: H, a: str) -> None:
    select(h, "codex", fake_pin(2), (pin_of(h, "codex", a), fake_pin(3)))


def an_open_use(rig: Rig, h: H, a: str) -> None:
    rewire(rig, h, CrashAt(USE_INTENT_POINT))
    with pytest.raises(SimulatedCrash):
        h.ops.use("codex", a)
    rig.restart()
    rewire(rig, h)


IN_USE_CASES: dict[str, Callable[[Rig, H, str], None]] = {
    "an-entry-without-an-outcome": entry_without_outcome, "launched-and-still-listed": launched_and_listed,
    "the-selection": the_selection, "the-previous-selection": the_previous_selection,
    "an-open-use": an_open_use}


@pytest.mark.parametrize("case", list(IN_USE_CASES))
def test_remove_and_prune_refuse_an_install_in_use(rigged: tuple[Rig, H], case: str) -> None:
    rig, h = rigged
    _, a = published(h)
    IN_USE_CASES[case](rig, h, a)
    assert refused(lambda: h.ops.remove("codex", a)) == wh.IN_USE
    assert a not in h.ops.prune(None).installs
    assert a in installs(h)


def test_an_ended_generations_install_is_not_in_use(rigged: tuple[Rig, H]) -> None:
    rig, h = rigged
    _, a = published(h)
    launched_and_listed(rig, h, a)
    rig.runtime.end(rig.key("btq-1"))
    h.ops.remove("codex", a)
    assert a not in installs(h)


def test_prune_needs_the_confirmation_code_and_it_expires(h: H) -> None:
    _, a = published(h)
    second_codex(h)
    _, b = published(h)
    h.ops.use("codex", b)                                  # b is in use; a is not
    first = h.ops.prune(None)
    assert first.installs == [a] and len(first.code) == 8 and int(first.code, 16) >= 0
    assert refused(lambda: h.ops.prune("0" * 8 if first.code != "0" * 8 else "1" * 8)) == wh.CONFIRM_REQUIRED
    h.clock.advance(601)
    assert refused(lambda: h.ops.prune(first.code)) == wh.CONFIRM_REQUIRED     # past 10 minutes
    assert installs(h) == {a, b}
    second = h.ops.prune(None)
    h.clock.advance(599)
    assert h.ops.prune(second.code).installs == [a]
    assert installs(h) == {b}
    assert refused(lambda: h.ops.prune(second.code)) == wh.CONFIRM_REQUIRED   # a code is used once


# --- the race with the launch guard (g08sd (2)) ---

def test_recover_runs_before_the_first_pickup(tmp_path: Path) -> None:
    """Start-up runs `harness.recover()` to its end before any workstream's pickup."""
    order: list[str] = []
    release = threading.Event()
    entered = threading.Event()

    class Held:
        def recover(self) -> None:
            entered.set()
            order.append("recover")
            assert release.wait(10)

        def advance(self, now: int) -> None:
            order.append("advance")

    s = settings_for(tmp_path)
    runtime = FakeRuntime()
    wsd = assembled(s, Journal(s.journal), runtime, World(tmp_path / "btq-state"), Held())
    up = Worker(wsd.startup, "startup")
    up.start()
    assert entered.wait(10)
    time.sleep(0.2)
    assert order == ["recover"] and runtime.expires == []          # no pickup has started
    release.set()
    finish(up)
    assert order[0] == "recover" and order.count("recover") == 1 and "advance" in order
    assert runtime.expires != []


def settings_for(tmp_path: Path) -> WsdSettings:
    repo = git_repo(tmp_path / "repos" / "proj")
    accounts = accounts_at(login_home(tmp_path / "home"))
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES, accounts=accounts)
    return WsdSettings(tmp_path / "state" / "wsd", 0.05, 3600, 3, tmp_path / "btq", {}, (ws,), accounts)


def test_a_guard_holding_a_pin_keeps_remove_waiting_until_its_entry_is_journaled(
        rigged: tuple[Rig, H]) -> None:
    """The guard has read pin A and not yet journaled its entry. The selection moves off A, and a remove
    of A asks for the guard's lock: it waits, and once the entry names A it refuses IN_USE. The row stays
    `published` and the install stays whole."""
    rig, h = rigged
    staging_id, a = published(h)
    select(h, "codex", pin_of(h, "codex", a))
    real = selections_at(h.config)
    reading, go = threading.Event(), threading.Event()

    def slow(adapter: str) -> InstallPin | None:
        pin = real(adapter)
        if adapter == "codex":
            reading.set()
            assert go.wait(10)
        return pin

    rig.selections = slow
    rig.restart()
    rewire(rig, h)
    lock = probe_lock(rig, "remover")
    result: list[str] = []
    guard = Worker(rig.pickup, "guard")
    guard.start()
    assert reading.wait(10)
    select(h, "codex", fake_pin(3), (fake_pin(2), fake_pin(1)))     # A is neither selected nor previous
    remover = Worker(lambda: result.append(refused(lambda: h.ops.remove("codex", a))), "remover")
    remover.start()
    assert lock.asked.wait(10)
    assert lock.probes == ["blocked"] and remover.is_alive() and result == []
    go.set()
    finish(guard, remover)
    assert result == [wh.IN_USE]
    [spec] = rig.runtime.launches
    assert spec.install == a
    assert row(h, staging_id).state == "published" and h.store.state("codex", a) == "published"


# --- taint (E17) ---

def test_a_taint_signal_holds_every_bead_on_the_install_and_writes_one_alert(
        rigged: tuple[Rig, H], monkeypatch: pytest.MonkeyPatch) -> None:
    rig, h = rigged
    _, a = published(h)
    select(h, "codex", pin_of(h, "codex", a))
    assert rig.pickup() is Outcome.STARTED
    parks: list[tuple[str, tuple[str, ...], str, bool]] = []
    real = Parker.park

    def park(self: Parker, bead: str, blockers: tuple[str, ...], why: str = "", hold: bool = False,
             ref: str | None = None) -> Any:
        parks.append((bead, blockers, why, hold))
        return real(self, bead, blockers, why, hold, ref)

    monkeypatch.setattr(Parker, "park", park)
    key = rig.key("btq-1")
    rig.runtime.report(WS, InstallTainted(key, 1, "codex", a))
    rig.pickup()
    assert parks == [("btq-1", (), INSTALL_TAINTED, True)]
    assert HELD in rig.beads.show(WS, "btq-1").labels
    alerts = [p for p in h.alerts.iterdir() if not p.name.startswith(".")]
    assert len(alerts) == 1 and re.fullmatch(r"harness-taint-codex-[0-9a-f]{12}", alerts[0].name)
    assert a in alerts[0].read_text()
    rig.runtime.report(WS, InstallTainted(key, 1, "codex", a))     # again: still one alert for the install
    rig.pickup()
    assert [p.name for p in h.alerts.iterdir() if not p.name.startswith(".")] == [alerts[0].name]


def install_files(h: H, adapter: str, install: str) -> list[Path]:
    root = h.tmp / "store" / adapter / install
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def test_verify_clears_the_taint_only_after_a_matching_rehash(h: H) -> None:
    _, a = published(h)
    verify.taint(h.taint, "codex", a, verify.INSTALL_CHANGED)
    h.ops.verify("codex", a)
    assert not verify.tainted(h.taint, "codex", a)
    first = install_files(h, "codex", a)[0]
    original = first.read_bytes()
    rewrite(first)                                          # changed, then restored exactly: ctime moved
    rewrite(first, original)
    verify.taint(h.taint, "codex", a, verify.INSTALL_CHANGED)
    h.ops.verify("codex", a)                                 # re-recorded (E18), then the marker goes
    assert not verify.tainted(h.taint, "codex", a)
    assert h.store.state("codex", a) == "published"
    rewrite(first)                                          # changed and left so
    assert refused(lambda: h.ops.verify("codex", a)) != ""
    assert verify.tainted(h.taint, "codex", a)              # a failed verify taints, and keeps it (E22)
    h.ops.advance(h.clock())
    assert verify.tainted(h.taint, "codex", a)


# --- the journal, and refusals before any work ---

def test_every_harness_op_is_journaled_with_its_actor(h: H) -> None:
    staging_id, a = published(h)
    second_codex(h)
    _, b = published(h)
    h.ops.use("codex", a)
    h.ops.use("codex", b)
    h.ops.rollback("codex")
    h.ops.verify("codex", a)
    second_codex(h, "third")
    _, c = published(h)
    second_codex(h, "fourth")
    _, d = published(h)
    h.ops.remove("codex", c)
    h.ops.prune(h.ops.prune(None).code)
    assert d not in installs(h)
    seen = events(h)
    for action in CTL_ACTIONS:
        assert any(e.action == action and e.actor == ACTOR_CTL for e in seen), action
    assert any(e.action == "approved" and e.actor == ACTOR_WSD for e in seen)
    assert any(e.action == "published" and e.actor == ACTOR_WSD and e.install == a for e in seen)
    assert all(e.adapter in ("codex", "") for e in seen)
    assert all(secret_scan.identifier_kind(e.detail or "") is None for e in seen)
    assert row(h, staging_id).state == "published"


def test_install_refuses_without_a_recipe_or_an_approval_workstream(h: H) -> None:
    assert refused(lambda: h.ops.install("aider", None)) == wh.NO_RECIPE
    h.recipes = {"claude-code": h.recipes["claude-code"]}
    h.ops = make_ops(h)
    assert refused(lambda: h.ops.install("codex", None)) == wh.NO_RECIPE
    h.approval_workstream = ""
    h.ops = make_ops(h)
    assert refused(lambda: h.ops.install("claude-code", None)) == wh.NO_APPROVAL_WORKSTREAM
    assert h.world.created == [] and h.asks.posts == [] and h.opener.asked == []


def test_an_open_approval_expires_after_staging_hours(h: H) -> None:
    staging_id, _ = posted(h)
    h.ops.advance(h.clock() + 23 * 3600)
    assert row(h, staging_id).state == "posted"
    h.clock.advance(24 * 3600 + 1)
    h.ops.advance(h.clock())
    assert row(h, staging_id).state == "expired" and installs(h) == set()


def test_an_ask_admind_refuses_to_post_fails_the_install(h: H) -> None:
    h.asks.reply = {"result": "refused", "message": "the ask is not postable"}
    staging_id = h.ops.install("codex", None)
    r = settle(h, staging_id)
    assert r.state == "failed" and r.failure == wh.ASK_UNPOSTABLE

