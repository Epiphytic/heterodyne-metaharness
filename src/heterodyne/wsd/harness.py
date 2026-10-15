"""wsd's harness ops (plan 4c Task 10): `wsctl harness`'s commands, the digest-pinned install approval (E13,
E14) and the taint alerts (E17).

`install` fetches and stages the recipe, journals a `staged` row and replies with the staging ID; a worker
thread then composes the approval bead's text, refuses it if admind's scanner would flag any of it
(`ASK_UNPOSTABLE`), creates the bead and posts it on admind's `ask.sock`. From then on wsd only reads the
bead: `advance`, from every pickup, runs `approve-bead <bead> --json` and applies E13's predicates
(`approvals.verdict`). An approved row is published with the digest it was staged with; `recover`, at
start-up, settles every row a crash left `approved` from what the store reports, never from the name.

`use` and `rollback` write the selection after the launcher's checks (E11); the next launch reads it. `remove`
and `prune` hold every workstream's operation lock (the launch guard's), then refuse an install in use (E16):
a launch entry with no outcome, a launched generation the runtime still lists, the selection's current or
previous install, an open `use`, or an install still awaiting its approval.

Every lock is taken in one order: the workstreams' operation locks, then this object's. A refusal is a
`HarnessRefused` whose constant is its whole message; no path, digest or secret is ever in one."""

import contextlib
import json
import logging
import os
import re
import secrets
import socket
import tarfile
import tempfile
import threading
from collections.abc import Callable, Generator, Sequence
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

from heterodyne.agents.base import Adapter, AdapterError, content_items
from heterodyne.agents.registry import ADAPTERS, login_names
from heterodyne.config import ConfigError, secret_scan
from heterodyne.harness import digests, selection, verify
from heterodyne.harness.fetch import FETCH_FAILED, Fetcher
from heterodyne.harness.resources import store_core as sc
from heterodyne.harness.selection import InstallPin, Selection
from heterodyne.harness.settings import ContentSource, HarnessSettings, Recipe
from heterodyne.harness.store import Store, StoreRefused
from heterodyne.wsd import alerts, approvals
from heterodyne.wsd.btq import Busy, QueueFactory, Readout
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.journal import HarnessInstall, Journal
from heterodyne.wsd.launches import LAUNCHED
from heterodyne.wsd.runtime import AgentRuntime, RuntimeUnavailable

log = logging.getLogger(__name__)

NO_RECIPE = "the adapter has no recipe in [harnesses]"
NO_APPROVAL_WORKSTREAM = "[harnesses] approval_workstream is unset; an install needs it for its approval"
NO_COMMIT = "the running harness commit is unknown; an install's approval needs it"
NO_APPROVE_BEAD = "[admind] approve_bead is unset, so no install's approval could take effect"
ADMIND_INVALID = "[admind] is invalid, so no install's approval can be read; `admind ask list` says why"
UNSUPPORTED_SOURCE = "the recipe has defaults or npm content, which this version does not install"
NOT_APPROVED = "the install's approval did not verify"
DENIED = "the install's approval was denied"
ASK_UNPOSTABLE = "the install's approval could not be created or posted"
NOT_PUBLISHED = "the adapter has no published install of that name"
NOTHING_TO_ROLL_BACK = "the adapter's selection has no previous install"
IN_USE = "the install is in use"
CONFIRM_REQUIRED = "prune needs the code a prune without one gave, within 10 minutes, once"
SELECTION_UNREADABLE = "the selection file can't be read; `heterodyne config check` says why"
BAD_CONTENT = "a content item's archive can't be unpacked to be checked"

ACTOR_CTL = "wsctl"             # an op a `wsctl harness` request ran
ACTOR_WSD = "wsd"               # what `advance`, `recover` and a taint did on their own
APPROVED_POINT = "harness.approved"         # just after a row's `approved` is journaled
USE_INTENT_POINT = "harness.use.intent"     # just after a `use` op is journaled open
OPEN = ("staged", "posted", "approved")     # rows whose install is still to come
SESSION = "wsd-harness"         # wsd's btq session on the approval workstream
CODE_SECONDS = 600              # a prune code's life
ASK_SECONDS = 10.0              # one `ask.sock` exchange
MAX_ASK_REPLY = 1 << 20
EFFECT = re.compile(r"publish (\S+) install (\S+) manifest (.+)")
ALERT_ADAPTER = re.compile(r"[a-z0-9-]{1,32}")
FALLBACK_RISK = "a host process of the service user can change it"

Readouts = Callable[[str], Readout | Busy]


class HarnessRefused(Exception):
    """A harness op refused: `.constant` is its whole message."""

    def __init__(self, constant: str) -> None:
        super().__init__(constant)
        self.constant = constant


@dataclass(frozen=True)
class Pruning:
    """`prune` without a code: what it would remove, and the code that confirms it."""
    installs: list[str]
    code: str
    adapters: dict[str, str]        # install -> adapter


@dataclass(frozen=True)
class Pruned:
    installs: list[str]
    adapters: dict[str, str]


@dataclass(frozen=True)
class Listing:
    """`list`: each install's adapter, version and states (comma separated), and whether the store is the
    fallback."""
    installs: dict[str, dict[str, str]]
    fallback: bool


@dataclass
class _Fetched:
    """What `install` hands `stage`, and the approval text's source lines."""
    parts: list[sc.Part] = field(default_factory=lambda: list[sc.Part]())
    fds: list[int] = field(default_factory=lambda: list[int]())
    lines: list[str] = field(default_factory=lambda: list[str]())
    commits: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())


def iso(at: int) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp())


def _no_problems() -> Sequence[str]:
    return ()


def _no_locks() -> Sequence[AbstractContextManager[Any]]:
    return ()


class HarnessOps:
    def __init__(self, *, settings: HarnessSettings, store: Store, fetcher: Fetcher, queues: QueueFactory,
                 journal: Journal, readout: Readouts, ask_socket: Path, config_dir: Path, taint_dir: Path,
                 alerts_dir: Path, runtime: AgentRuntime, clock: Callable[[], int], commit: str, repo: str,
                 cp: Checkpoint = nothing,
                 guard_locks: Callable[[], Sequence[AbstractContextManager[Any]]] = _no_locks,
                 store_problems: Callable[[], Sequence[str]] = _no_problems,
                 readout_refusal: str = "") -> None:
        self.settings = settings
        self.store = store
        self.fetcher = fetcher
        self.queues = queues
        self.journal = journal
        self.readout = readout
        self.ask_socket = ask_socket
        self.config_dir = config_dir
        self.taint_dir = taint_dir
        self.alerts_dir = alerts_dir
        self.runtime = runtime
        self.clock = clock
        self.commit = commit
        self.cp = cp
        self.guard_locks = guard_locks
        self.store_problems = store_problems
        self.repo = repo
        self.failing: set[str] = set()          # staging IDs whose last advance raised (logged once)
        self.readout_refusal = readout_refusal  # why `readout` can't work (NO_APPROVE_BEAD...), or ""
        self.lock = threading.RLock()
        self.alerts_lock = threading.Lock()
        self.code: tuple[str, int, dict[str, str]] | None = None   # the prune code, when, and what it lists

    # --- install: fetch, stage, then the approval in a worker ---

    def install(self, adapter: str, version: str | None) -> str:
        """Stage the recipe's install and return its staging ID; the approval is created and posted by a
        worker thread. A refusal leaves no row."""
        recipe = self.settings.recipes.get(adapter)
        impl = ADAPTERS.get(adapter)
        if recipe is None or impl is None:
            raise HarnessRefused(NO_RECIPE)
        if not self.settings.approval_workstream:
            raise HarnessRefused(NO_APPROVAL_WORKSTREAM)
        if not self.commit:
            raise HarnessRefused(NO_COMMIT)
        if self.readout_refusal:
            raise HarnessRefused(self.readout_refusal)
        if recipe.defaults is not None or any(c.git is None or c.commit is None for c in recipe.content):
            raise HarnessRefused(UNSUPPORTED_SOURCE)
        version = recipe.version if version is None else version
        with ExitStack() as stack:
            try:
                got = self._fetch(stack, impl, recipe, version)
                staged = self.store.stage(adapter, impl.install_layout.layout, got.parts, got.fds,
                                          login_names(), verity=not self.settings.fallback,
                                          target=recipe.target)
                manifest = self.store.staged_manifest(staged.id)
            except (sc.Refused, StoreRefused) as exc:
                raise HarnessRefused(exc.constant) from None
            except AdapterError as exc:
                raise HarnessRefused(str(exc.args[0])) from None
            except OSError:
                raise HarnessRefused(FETCH_FAILED) from None
        now = iso(self.clock())
        install = sc.install_name(manifest.version, staged.digest)
        with self.lock:
            self.journal.harness_insert(HarnessInstall(staged.id, adapter, staged.digest, manifest.version,
                                                       "", "", "", "staged", "", "", now, now))
            self.journal.harness_event(now, ACTOR_CTL, "install", adapter, install, "staged")
        worker = threading.Thread(target=self._ask, name=f"harness-ask-{adapter}", daemon=True,
                                  args=(staged.id, adapter, install, manifest, staged, got))
        worker.start()
        return staged.id

    def _fetch(self, stack: ExitStack, impl: Adapter, recipe: Recipe, version: str) -> _Fetched:
        """Every artifact fetched and checked, open read-only (closed and removed when `stack` ends)."""
        got = _Fetched()
        layout = impl.install_layout
        if layout.artifact == "github-release":
            tag = layout.tag.format(version=version)
            asset = layout.asset.format(target=recipe.target)
            art = self.fetcher.release(f"https://github.com/{layout.locator}/releases/download/{tag}", asset,
                                       layout.sums)
            source = sc.Source("cli", "release", asset, tag, art.checksum, art.origin, False, False)
            fmt: sc.PartFormat = "tar.gz"
            named, origin = f"{asset}, release {tag}", layout.sums
        else:
            name = layout.locator.replace("<target>", recipe.target)
            art = self.fetcher.npm(name, version)
            source = sc.Source("cli", "npm", name, version, art.checksum, art.origin, art.signature_available,
                               False)
            fmt, named, origin = "tgz", f"{name} {version}", art.origin
        stack.callback(art.path.unlink, missing_ok=True)
        fd = self._open(stack, art.path)
        shown = digests.display(digests.of_fd(fd))
        signed = "signature available, not verified" if art.signature_available else "no signature published"
        got.parts.append(sc.Part("cli", fmt, layout.strip, art.checksum, art.origin, art.signature_available,
                                 source, None))
        got.fds.append(fd)
        got.lines.append(f"- cli: {named}, file {shown}; checksum matches {origin}; {signed}")
        for item in recipe.content:
            self._content(stack, impl, item, got)
        return got

    def _content(self, stack: ExitStack, impl: Adapter, item: ContentSource, got: _Fetched) -> None:
        repo, commit = cast(str, item.git), cast(str, item.commit)
        g = self.fetcher.git(repo, commit)
        stack.callback(g.archive.unlink, missing_ok=True)
        stack.callback(g.commit_object.unlink, missing_ok=True)
        part = f"content/{item.kind}/{item.name}"
        _check_archive(impl, item.kind, g.archive)
        checksum = f"git:{g.commit}"
        got.parts.append(sc.Part(part, "tar", "", checksum, repo, False,
                                 sc.Source(part, "git", item.name, g.commit, checksum, repo, False, False),
                                 g.commit))
        got.fds += [self._open(stack, g.archive), self._open(stack, g.commit_object)]
        got.lines.append(f"- {part}: git {repo} at {g.commit}; trust on first install (unsigned)")

    @staticmethod
    def _open(stack: ExitStack, path: Path) -> int:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        return fd

    def _compose(self, adapter: str, install: str, manifest: sc.Manifest, staged: sc.Staged,
                 got: _Fetched) -> approvals.Composed:
        shown = digests.display(digests.of_manifest(manifest))
        fallback = self.store.fallback
        store = f"fallback (accepted risk: {FALLBACK_RISK})" if fallback else "helper (store owner)"
        context = "\n".join([
            f"What: add a managed install of {adapter} {manifest.version} to the store. It becomes "
            "selectable; nothing uses it until `wsctl harness use`.",
            f"Manifest digest: {shown}",
            f"Entries: {staged.entries}, size: {staged.size}",
            "Sources:", *got.lines,
            f"Store: {store}"])
        risks = "the sources' trust as listed" + ("; under the fallback, the accepted risk above." if fallback
                                                  else ".")
        fields = approvals.AskFields(
            effect=f"publish {adapter} install {install} manifest {shown}",
            excludes="selecting it (`use`), and any other version or content.",
            why=f"the recipe in [harnesses.{adapter}] asks for it.", risks=risks)
        title = f"Add the {adapter} {manifest.version} managed install ({install})"
        return approvals.compose(title, fields, context, [approvals.Ref("commit", self.repo, self.commit)],
                                 "execution")

    def _ask(self, staging_id: str, adapter: str, install: str, manifest: sc.Manifest, staged: sc.Staged,
             got: _Fetched) -> None:
        """The worker: compose, scan, create, read back, post. Anything amiss fails the row."""
        try:
            composed = self._compose(adapter, install, manifest, staged, got)
            texts = (composed.title, composed.description, json.dumps(composed.ask))
            kinds = [k for k in map(secret_scan.sensitive_kind, texts) if k is not None]
            if kinds:
                log.warning("install %s: the approval text holds a %s; withheld", install, kinds[0])
                self._fail_open(staging_id, ASK_UNPOSTABLE)
                return
            ws = self.settings.approval_workstream
            queue = self.queues(ws, SESSION)
            issue = {"title": composed.title, "description": composed.description,
                     "metadata": {"ask": composed.ask}}
            digest, gaps = queue.ask_digest(issue)
            if digest is None or gaps:
                log.warning("install %s: btq finds the approval incomplete: %s", install, "; ".join(gaps))
                self._fail_open(staging_id, ASK_UNPOSTABLE)
                return
            created = queue.bd("create", composed.title, f"--description={composed.description}",
                               "--type=task", f"--labels=kind:approval,ws:{ws}", "--metadata",
                               json.dumps({"ask": composed.ask, "context_digest": digest}))
            bead = _created_id(created)
            if queue.ask_digest(queue.show(bead)) != (digest, []):
                log.warning("install %s: the approval bead does not read back as created", install)
                self._fail_open(staging_id, ASK_UNPOSTABLE)
                return
            with self.lock:
                self.journal.harness_set(staging_id, iso(self.clock()), approval_bead=bead,
                                         context_digest=digest)
            reply = self._post(bead)
            if reply.get("result") != "posted":
                log.warning("install %s: admind did not post the approval", install)
                self._fail_open(staging_id, ASK_UNPOSTABLE)
                return
            with self.lock:
                row = self.journal.harness_install(staging_id)
                if row is not None and row.state == "staged":
                    self.journal.harness_set(staging_id, iso(self.clock()), state="posted",
                                             ask_id=_ask_id(reply))
        except Exception:
            log.exception("install %s: the approval could not be created or posted", install)
            self._fail_open(staging_id, ASK_UNPOSTABLE)

    def _fail_open(self, staging_id: str, constant: str) -> None:
        """Fail the row if it is still being asked for (`staged`)."""
        with self.lock:
            row = self.journal.harness_install(staging_id)
            if row is not None and row.state == "staged":
                self._end(row, "failed", constant)

    def _post(self, bead: str) -> dict[str, Any]:
        """admind's `AskPost` for an approval, as one JSON line on `ask.sock`; its reply."""
        line = json.dumps({"type": "post", "kind": "approval", "bead": bead}).encode() + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(ASK_SECONDS)
            s.connect(str(self.ask_socket))
            s.sendall(line)
            data = b""
            while not data.endswith(b"\n") and len(data) <= MAX_ASK_REPLY:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        reply: object = json.loads(data)
        return cast(dict[str, Any], reply) if isinstance(reply, dict) else {}

    # --- advance and recover ---

    def advance(self, now: int) -> None:
        """Expire, decide and publish each open row (E13, E21). A row that raises is logged, once until it
        advances again, and the loop goes on to the next."""
        with self.lock:
            for row in self.journal.harness_installs(*OPEN):
                try:
                    self._advance(row, now)
                except Exception:  # noqa: BLE001 - one row's trouble never stops the others; it retries
                    if row.staging_id in self.failing:
                        log.debug("install %s: advancing failed again", self._name(row), exc_info=True)
                    else:
                        self.failing.add(row.staging_id)
                        log.exception("install %s: advancing failed; retried each pickup", self._name(row))
                else:
                    self.failing.discard(row.staging_id)

    def _advance(self, row: HarnessInstall, now: int) -> None:
        expired = epoch(row.created_at) + self.settings.staging_hours * 3600 < now
        if row.state in ("staged", "posted") and expired:
            self._end(row, "expired", "")
        elif row.state == "posted":
            self._decide(row)
        elif row.state == "approved":
            self._recover_store()
            self._settle(row)

    def _decide(self, row: HarnessInstall) -> None:
        try:
            readout = self.readout(row.approval_bead)
        except Exception:  # noqa: BLE001 - an unreadable decision waits for the next pickup
            log.warning("install %s: approve-bead could not be read", self._name(row), exc_info=True)
            return
        if isinstance(readout, Busy):
            return
        try:
            queue = self.queues(self.settings.approval_workstream, SESSION)
            shown = queue.show(row.approval_bead)
            recomputed = queue.ask_digest(shown)
        except Exception:  # noqa: BLE001 - as above
            log.warning("install %s: the approval bead could not be read", self._name(row), exc_info=True)
            return
        expect = approvals.Expected(row.approval_bead, row.context_digest, partial(self._effect_ok, row))
        verdict = approvals.verdict(readout, shown, recomputed, expect, None)
        if isinstance(verdict, approvals.Waiting):
            return
        if isinstance(verdict, approvals.Denied):
            self._end(row, "denied", DENIED)
            return
        if isinstance(verdict, approvals.Unverified):
            log.warning("install %s: the approval failed predicate %d", self._name(row), verdict.n)
            self._end(row, "failed", NOT_APPROVED)
            return
        at = iso(self.clock())
        approved = self.journal.harness_set(row.staging_id, at, state="approved")
        self.journal.harness_event(at, ACTOR_WSD, "approved", row.adapter, self._name(row))
        self.cp(APPROVED_POINT)
        self._settle(approved)

    def _effect_ok(self, row: HarnessInstall, effect: Any) -> bool:
        """Predicate 10: the ask's effect publishes exactly this row's install, with the digest wsd computes
        from the staged manifest."""
        if not isinstance(effect, str):
            return False
        match = EFFECT.fullmatch(effect)
        if match is None or match.group(1) != row.adapter or match.group(2) != self._name(row):
            return False
        try:
            shown = digests.parse_display(match.group(3))
            manifest = self.store.staged_manifest(row.staging_id)
        except (ValueError, sc.Refused, StoreRefused, OSError):
            return False
        return shown == digests.of_manifest(manifest) and sc.digest(manifest) == row.digest

    def _recover_store(self) -> None:
        try:
            self.store.recover()
        except Exception:  # noqa: BLE001 - `outcome` then says what the store has
            log.warning("the store's recovery failed", exc_info=True)

    def _settle(self, row: HarnessInstall) -> None:
        """An approved row, from what the store reports (E21): staged is published; a published outcome
        whose install is `published` is recorded; a failure is recorded; anything else is RECOVERY_FAILED."""
        try:
            outcome = self.store.outcome(row.staging_id)
            if outcome == "staged":
                install = self.store.publish(row.staging_id, row.digest)
                self._published(row, install)
                return
            if outcome is not None and outcome.startswith("published:"):
                install = outcome.removeprefix("published:")
                if self.store.state(row.adapter, install) == "published":
                    self._published(row, install)
                    return
            if outcome is not None and outcome.startswith("failed:"):
                self._end(row, "failed", outcome.removeprefix("failed:"))
                return
            self._end(row, "failed", sc.RECOVERY_FAILED)
        except StoreRefused as exc:
            self._end(row, "failed", exc.constant)

    def _published(self, row: HarnessInstall, install: str) -> None:
        at = iso(self.clock())
        self.journal.harness_set(row.staging_id, at, state="published", install=install)
        self.journal.harness_event(at, ACTOR_WSD, "published", row.adapter, install)

    def _end(self, row: HarnessInstall, state: str, failure: str) -> None:
        at = iso(self.clock())
        self.journal.harness_set(row.staging_id, at, state=state, failure=failure)
        self.journal.harness_event(at, ACTOR_WSD, state, row.adapter, self._name(row), failure)

    @staticmethod
    def _name(row: HarnessInstall) -> str:
        return sc.install_name(row.version, row.digest)

    def recover(self) -> None:
        """Start-up's, before any pickup: close every `use` a crash left open, fail every row whose worker
        died, and settle every approved row from the store."""
        with self.lock:
            self.journal.harness_close_all()
            for row in self.journal.harness_installs("staged"):
                self._end(row, "failed", ASK_UNPOSTABLE)
            approved = self.journal.harness_installs("approved")
            if approved:
                self._recover_store()
            for row in approved:
                self._settle(row)

    # --- use, rollback, verify ---

    def _pin(self, adapter: str, install: str) -> InstallPin:
        if adapter in ADAPTERS:
            for info in self.store.list():
                if (info.adapter, info.name, info.state) == (adapter, install, "published"):
                    return InstallPin(install, info.digest)
        raise HarnessRefused(NOT_PUBLISHED)

    def _check(self, adapter: str, pin: InstallPin) -> None:
        """E11's checks: the launcher's, then each content item's."""
        impl = ADAPTERS[adapter]
        try:
            v = verify.verify_pin(self.store, impl, pin, self.taint_dir, problems=self.store_problems)
        except verify.VerifyRefused as exc:
            raise HarnessRefused(exc.constant) from None
        try:
            for kind, name in content_items(v.manifest):
                impl.check_content(kind, v.root / "content" / kind / name)
        except AdapterError as exc:
            raise HarnessRefused(str(exc.args[0])) from None

    def _selections(self) -> dict[str, Selection]:
        try:
            return dict(selection.load(self.config_dir))
        except ConfigError:
            raise HarnessRefused(SELECTION_UNREADABLE) from None

    def _select(self, action: str, adapter: str, pin: InstallPin, previous: tuple[InstallPin, ...]) -> None:
        """Write the selection with the op journaled open around it; a crash leaves it open until start-up."""
        chosen, install = Selection(adapter, pin, previous), pin.install
        at = iso(self.clock())
        self.journal.harness_open(adapter, install, at)
        try:
            self.cp(USE_INTENT_POINT)
            selection.write(self.config_dir, {**self._selections(), adapter: chosen})
            self.journal.harness_event(at, ACTOR_CTL, action, adapter, install)
        except Exception:
            self.journal.harness_close(adapter, install)
            raise
        self.journal.harness_close(adapter, install)

    def use(self, adapter: str, install: str) -> None:
        with self.lock:
            pin = self._pin(adapter, install)
            self._check(adapter, pin)
            old = self._selections().get(adapter)
            previous = () if old is None else tuple(
                p for p in (old.current, *old.previous) if p is not None and p != pin)
            self._select("use", adapter, pin, previous)

    def rollback(self, adapter: str) -> None:
        with self.lock:
            old = self._selections().get(adapter)
            if old is None or not old.previous:
                raise HarnessRefused(NOTHING_TO_ROLL_BACK)
            target = old.previous[0]
            if self._pin(adapter, target.install) != target:
                raise HarnessRefused(NOT_PUBLISHED)
            self._check(adapter, target)
            previous = tuple(p for p in (old.current, *old.previous[1:]) if p is not None and p != target)
            self._select("rollback", adapter, target, previous)

    def verify(self, adapter: str, install: str) -> None:
        """Rehash the install (the store re-records a tree whose bytes match, E18); clear its taint only
        then. A tree that doesn't match is tainted."""
        if adapter not in ADAPTERS or not sc.INSTALL.fullmatch(install):
            raise HarnessRefused(NOT_PUBLISHED)
        with self.lock:
            at = iso(self.clock())
            try:
                self.store.verify(adapter, install)
            except StoreRefused as exc:
                if exc.constant != sc.NO_SUCH_INSTALL:
                    verify.taint(self.taint_dir, adapter, install, verify.INSTALL_CHANGED)
                self.journal.harness_event(at, ACTOR_CTL, "verify", adapter, install, exc.constant)
                raise HarnessRefused(exc.constant) from None
            verify.clear_taint(self.taint_dir, adapter, install)
            self.journal.harness_event(at, ACTOR_CTL, "verify", adapter, install, "verified")

    # --- remove and prune (E16) ---

    @contextlib.contextmanager
    def _guarded(self) -> Generator[None]:
        """Every workstream's operation lock, then ours: no launch guard is between reading a pin and
        journaling its entry while this is held."""
        with ExitStack() as stack:
            for lock in self.guard_locks():
                stack.enter_context(lock)
            stack.enter_context(self.lock)
            yield

    def in_use(self, adapter: str, install: str) -> bool:
        j = self.journal
        for entry in j.launches_naming(install):
            if entry.outcome is None:
                return True
            if entry.outcome == LAUNCHED:
                try:
                    listed = {s.key for s in self.runtime.sessions(entry.ws)}
                except RuntimeUnavailable:
                    return True
                newest = max(e.generation for e in j.launches(entry.session_key))
                if entry.session_key in listed and entry.generation == newest:
                    return True
        try:
            chosen = self._selections().get(adapter)
        except HarnessRefused:
            return True
        if chosen is not None and install in {p.install for p in (chosen.current, *chosen.previous[:1])
                                              if p is not None}:
            return True
        if (adapter, install) in j.harness_opened():
            return True
        return any(r.adapter == adapter and self._name(r) == install for r in j.harness_installs(*OPEN))

    def _remove(self, adapter: str, install: str, action: str) -> None:
        try:
            self.store.remove(adapter, install)
        except StoreRefused as exc:
            raise HarnessRefused(exc.constant) from None
        verify.clear_taint(self.taint_dir, adapter, install)
        self.journal.harness_event(iso(self.clock()), ACTOR_CTL, action, adapter, install)

    def remove(self, adapter: str, install: str) -> None:
        with self._guarded():
            if self.in_use(adapter, install):
                raise HarnessRefused(IN_USE)
            self._remove(adapter, install, "remove")

    def _prunable(self) -> dict[str, str]:
        return {i.name: i.adapter for i in sorted(self.store.list(), key=lambda i: (i.adapter, i.name))
                if i.state == "published" and not self.in_use(i.adapter, i.name)}

    def prune(self, confirm: str | None) -> Pruning | Pruned:
        """Without `confirm`, what would go and a one-time code; with the code (within CODE_SECONDS), remove
        what it listed that is still unused."""
        with self._guarded():
            now = self.clock()
            if confirm is None:
                listed = self._prunable()
                code = secrets.token_hex(4)
                self.code = (code, now, listed)
                return Pruning(sorted(listed), code, listed)
            issued, self.code = self.code, None
            if (issued is None or not secrets.compare_digest(issued[0], confirm)
                    or now - issued[1] > CODE_SECONDS):
                raise HarnessRefused(CONFIRM_REQUIRED)
            still = self._prunable()
            gone: dict[str, str] = {}
            for install, adapter in sorted(issued[2].items()):
                if still.get(install) != adapter:
                    continue
                try:
                    self._remove(adapter, install, "prune")
                except HarnessRefused as exc:
                    log.warning("prune: %s was not removed: %s", install, exc.constant)
                    continue
                gone[install] = adapter
            if not gone:
                self.journal.harness_event(iso(now), ACTOR_CTL, "prune", "", "", "nothing removed")
            return Pruned(sorted(gone), gone)

    # --- list and taint ---

    def list(self, adapter: str | None) -> Listing:
        with self.lock:
            try:
                chosen = dict(selection.load(self.config_dir))
            except ConfigError:
                chosen = {}
            out: dict[str, dict[str, str]] = {}
            for info in self.store.list():
                if adapter is not None and info.adapter != adapter:
                    continue
                sel = chosen.get(info.adapter)
                states: list[str] = []
                if sel is not None and sel.current is not None and sel.current.install == info.name:
                    states.append("selected")
                if sel is not None and info.name in {p.install for p in sel.previous}:
                    states.append("previous")
                if info.state != "published":
                    states.append(info.state)
                elif self.in_use(info.adapter, info.name) and not states:
                    states.append("in use")
                if verify.tainted(self.taint_dir, info.adapter, info.name):
                    states.append("tainted")
                out[info.name] = {"adapter": info.adapter, "version": info.version,
                                  "state": ", ".join(states or ["published"])}
            for row in self.journal.harness_installs(*OPEN):
                if adapter is not None and row.adapter != adapter:
                    continue
                state = {"staged": "staged", "posted": "awaiting approval", "approved": "approved"}[row.state]
                out.setdefault(self._name(row), {"adapter": row.adapter, "version": row.version,
                                                 "state": state})
            return Listing(out, self.store.fallback)

    def tainted(self, adapter: str, install: str) -> None:
        """An install was found tainted (E17): its one alert, and the event."""
        if not ALERT_ADAPTER.fullmatch(adapter) or not sc.INSTALL.fullmatch(install):
            log.warning("a taint report names no install wsd alerts on")
            return
        text = (f"The {adapter} install {install} is tainted: its tree is not the one its manifest "
                "describes. Every launch on it refuses, and the beads running on it are held. Once it is "
                f"restored, `wsctl harness verify {adapter} {install}` clears it; or remove it.")
        with self.alerts_lock:
            at = self.clock()
            if alerts.write(self.alerts_dir, f"harness-taint-{adapter}-{install[-12:]}", text, at=at):
                self.journal.harness_event(iso(at), ACTOR_WSD, "tainted", adapter, install)


def _check_archive(impl: Adapter, kind: str, archive: Path) -> None:
    """The adapter's check of a content item, on its git archive unpacked into a scratch directory."""
    with tempfile.TemporaryDirectory(prefix="harness-content-") as scratch:
        try:
            with tarfile.open(archive, mode="r:") as t:
                t.extractall(scratch, filter="data")
        except (tarfile.TarError, OSError):
            raise HarnessRefused(BAD_CONTENT) from None
        impl.check_content(kind, Path(scratch))


def _created_id(created: Any) -> str:
    """The bead ID in `bd create --json`'s answer (an object, or a list of one)."""
    if isinstance(created, list) and len(cast(list[Any], created)) == 1:
        created = cast(list[Any], created)[0]
    bead = cast(dict[str, Any], created).get("id") if isinstance(created, dict) else None
    if not isinstance(bead, str) or not bead:
        raise ValueError("bd create gave no bead ID")
    return bead


def _ask_id(reply: dict[str, Any]) -> str:
    ask = reply.get("ask")
    summary = cast(dict[str, Any], ask).get("summary") if isinstance(ask, dict) else None
    ask_id = cast(dict[str, Any], summary).get("ask_id") if isinstance(summary, dict) else None
    return ask_id if isinstance(ask_id, str) else ""
