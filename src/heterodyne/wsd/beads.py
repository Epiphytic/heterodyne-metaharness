"""wsd's view of the beads queue, through btq's Queue library as agent `wsd` (ADR 0001 §4.3, §5.2).

- Ready work is listed with the workstream-session worker; each bead is claimed, owned and parked with
  its own per-bead worker (`ids.bead_session`).
- btq has no library call for labels, dependencies, comments or metadata, so those go through
  `Queue.bd()` of the per-bead worker, after `Queue.owned()` confirms the claim is still ours, under
  that worker's `exclusive()` lock.
- Every write is check, write, read back (`ensure_*`): a step whose effect is already on the bead is not
  repeated, and an uncertain write is only trusted once it reads back (§4.3).
- Ownership is found by assignee, not by routing labels: a bead held by one of this workstream's per-bead
  workers is ours even if its labels changed. Before every launch `validate` re-runs btq's own post-claim
  checks (`owned`, then `matches` and `design_allowed`, btq's shared routing and design-approval gate).
- The launched-session record (§3.3, §4.1) lives on the bead as metadata `wsd_session`: role, profile,
  session key, repository and worktree. It is written before every launch, so it survives a lost journal;
  plan 4 adds the adapter, model and session ID it learns at launch.
- Fail closed: anything that does not parse as the bd 1.1 JSON shapes recorded in spike S6 raises
  UnexpectedShape, and every infrastructure failure raises BeadsUnavailable. Neither is ever read as
  "absent", "not paused" or "unblocked".
"""

import contextlib
import os
import re
import subprocess
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import msgspec

from heterodyne.wsd import gitwip, ids
from heterodyne.wsd.btq import QueueFactory, QueueLike

PARKED = "v2:parked"
HELD = "v2:held"
NEEDS_HUMAN = "needs-human"
# A blocker carrying one of these labels is an operator ask (an approval, question, picker or permission
# prompt), so a bead parked on it is waiting on input rather than on other work (plans 5 to 7 create them).
OPERATOR_INPUT_LABELS = frozenset({"kind:approval", "kind:question", "kind:confirm"})
# Dependency types that never block. Any other type (`blocks` and anything bd adds later) blocks until
# the other bead is closed: an unknown type is never assumed harmless.
NON_BLOCKING_DEPS = frozenset({"parent-child", "related", "discovered-from"})
BTQ_REFUSALS = ("Finish or release this worker's existing claim first",
                "Task is not eligible for this worker")
BTQ_ROUTING_CHANGED = "Routing/design changed during claim"
BTQ_NOT_OWNED = "Task is not in progress under this worker"
NOT_FOUND = "no issue found matching"
RECORD_KEY = "wsd_session"
# The provenance line btq's `worktree` appends to the bead's notes. Paths may contain the separators, so a
# line is never split on them: it is matched against the exact worker, repository and worktree expected
# (`Bead.bases`), and what lies between is the base.
PROVENANCE = "worker={worker}; repository={repo}; base={base}; worktree={worktree}"
FULL_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_FAILURES = (RuntimeError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError,
             IndexError, AttributeError)


class BeadsUnavailable(Exception):
    """The queue could not be read or written (Dolt down, bd failed or timed out, credentials missing)."""


class UnexpectedShape(BeadsUnavailable):
    """bd answered, but not in a shape wsd understands; treated exactly like an unreadable queue."""


class ClaimRefused(Exception):
    """btq refused the claim before writing anything."""


class ClaimUncertain(Exception):
    """The claim's outcome is unknown: read it back before doing anything else."""


class RoutingChanged(Exception):
    """btq claimed the bead but its routing or design gate changed meanwhile: never execute it."""


class NotOurs(Exception):
    """The bead is not in progress under this workstream's per-bead worker."""


class WorktreeConflict(Exception):
    """The btq worktree path exists but is not this bead's worktree; it is inspected, never deleted."""


class RecordUnreadable(Exception):
    """The bead's launched-session record is present but does not parse: never guessed at."""


class RecordConflict(Exception):
    """The bead already records a different launch identity than the one about to be written."""


class SessionRecord(msgspec.Struct, frozen=True, forbid_unknown_fields=False):
    """What was launched for a bead (§3.3 session registry). Session identity and worktree come from here,
    never from the current configuration. These five fields are the launch identity. Plan 4 adds its own
    fields (a thread ID, the reported model): they are ignored here, and wsd never rewrites a record that
    exists, so they are never lost."""
    role: str
    profile: str
    session_key: str
    repo: str
    worktree: str


def encode_record(record: SessionRecord) -> str:
    return msgspec.json.encode(record, order="sorted").decode()


class ClaimView(StrEnum):
    OURS = "ours"      # in_progress, assigned to our per-bead worker
    FREE = "free"      # open and unassigned: the claim did not happen
    OTHER = "other"    # anything else: someone else holds it, or it moved on
    GONE = "gone"      # bd says the bead does not exist


@dataclass(frozen=True)
class Dep:
    id: str
    status: str
    kind: str
    labels: tuple[str, ...]

    @property
    def blocking(self) -> bool:
        return self.kind not in NON_BLOCKING_DEPS and self.status != "closed"


@dataclass(frozen=True)
class Bead:
    id: str
    title: str
    status: str
    assignee: str | None
    labels: tuple[str, ...]
    metadata: dict[str, Any]
    deps: tuple[Dep, ...] | None     # None for list output, which carries no dependency status
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=lambda: cast(dict[str, Any], {}), compare=False, repr=False)

    def open_blockers(self) -> tuple[Dep, ...]:
        if self.deps is None:
            raise ValueError("dependency detail needs show(), not list()")
        return tuple(d for d in self.deps if d.blocking)

    def waits_on_operator(self) -> bool:
        return any(OPERATOR_INPUT_LABELS & set(d.labels) for d in self.open_blockers())

    def record(self) -> SessionRecord | None:
        """The launched-session record; None only if the key is absent. A present value that is not a JSON
        string holding a record (null, a number, an object bd did not store as a string) is RecordUnreadable,
        never "no record": only the operator's release may replace it."""
        if RECORD_KEY not in self.metadata:
            return None
        value = self.metadata[RECORD_KEY]
        if not isinstance(value, str):
            raise RecordUnreadable(self.id)
        try:
            return msgspec.json.decode(value, type=SessionRecord)
        except (msgspec.DecodeError, msgspec.ValidationError):
            raise RecordUnreadable(self.id) from None

    def bases(self, worker: str, repo: str, worktree: str) -> list[str]:
        """The base of every provenance note btq wrote when `worker` made `worktree` from `repo`, oldest
        first, exactly as written: a malformed base is returned for the caller to refuse, never skipped. A
        line counts only if it is exactly the expected prefix, a base, and the expected suffix, so separators
        inside a path never shift the fields; a crafted line that still fits yields a base that is not a
        commit hash, which fails closed."""
        head, tail = f"worker={worker}; repository={repo}; base=", f"; worktree={worktree}"     # PROVENANCE
        return [line[len(head):len(line) - len(tail)] for line in self.notes.split("\n")
                if len(line) >= len(head) + len(tail) and line.startswith(head) and line.endswith(tail)]


def _str(raw: dict[str, Any], key: str, required: bool = True) -> str | None:
    value = raw.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise UnexpectedShape(f"bead field {key} is not a string")
    return value


def _labels(raw: dict[str, Any]) -> tuple[str, ...]:
    labels = raw.get("labels", [])      # bd omits the key when there are none
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in cast(list[Any], labels)):
        raise UnexpectedShape("bead labels are not a list of strings")
    return tuple(cast(list[str], labels))


def parse(raw: object, detail: bool) -> Bead:
    """One bead from `bd show` (detail=True) or `bd list` (detail=False) JSON."""
    if not isinstance(raw, dict):
        raise UnexpectedShape("bead is not an object")
    item = cast(dict[str, Any], raw)
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        raise UnexpectedShape("bead metadata is not an object")
    deps: tuple[Dep, ...] | None = None
    if detail:
        count = item.get("dependency_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise UnexpectedShape("bead dependency_count is missing")
        raw_deps = item.get("dependencies", [])     # bd omits the list only when there are none
        if not isinstance(raw_deps, list):
            raise UnexpectedShape("bead dependencies are not a list")
        if len(cast(list[Any], raw_deps)) != count:   # an empty or cut-short list is not "no blockers"
            raise UnexpectedShape("bead dependencies do not match dependency_count")
        parsed: list[Dep] = []
        for dep in cast(list[Any], raw_deps):
            if not isinstance(dep, dict):
                raise UnexpectedShape("a dependency is not an object")
            d = cast(dict[str, Any], dep)
            parsed.append(Dep(cast(str, _str(d, "id")), cast(str, _str(d, "status")),
                              cast(str, _str(d, "dependency_type")), _labels(d)))
        deps = tuple(parsed)
    return Bead(cast(str, _str(item, "id")), cast(str, _str(item, "title")), cast(str, _str(item, "status")),
                _str(item, "assignee", required=False) or None, _labels(item),
                cast(dict[str, Any], metadata), deps, _str(item, "notes", required=False) or "", item)


def _settled(value: object, settled: frozenset[str]) -> bool:
    return isinstance(value, str) and value in settled


def _one(result: object) -> Bead:
    if not isinstance(result, dict):
        raise UnexpectedShape("show did not return one bead")
    return parse(cast(object, result), detail=True)


class BeadsAdapter:
    def __init__(self, factory: QueueFactory) -> None:
        self._factory = factory
        self._queues: dict[tuple[str, str], QueueLike] = {}
        self._lock = threading.Lock()

    def _queue(self, ws: str, session: str) -> QueueLike:
        with self._lock:
            queue = self._queues.get((ws, session))
            if queue is None:
                try:
                    queue = self._factory(ws, session)
                except _FAILURES as exc:     # credentials unreadable, no `wsd` entry, state dir refused
                    raise BeadsUnavailable(f"btq queue could not be opened ({type(exc).__name__})") from None
                self._queues[(ws, session)] = queue
            return queue

    def ws_queue(self, ws: str) -> QueueLike:
        return self._queue(ws, ids.ws_session(ws))

    def bead_queue(self, ws: str, bead: str) -> QueueLike:
        return self._queue(ws, ids.bead_session(ws, bead))

    @staticmethod
    def _call[T](fn: Callable[[], T]) -> T:
        try:
            return fn()
        except _FAILURES as exc:
            raise BeadsUnavailable(f"queue call failed ({type(exc).__name__})") from None

    # --- reads ---

    def ready(self, ws: str) -> list[Bead]:
        """New work in btq's order. btq returns [] while the workstream worker is paused."""
        queue = self.ws_queue(ws)
        raw = self._call(queue.ready)
        if not isinstance(raw, list):
            raise UnexpectedShape("ready did not return a list")
        return [parse(item, detail=False) for item in cast(list[Any], raw)]

    def show(self, ws: str, bead: str) -> Bead:
        queue = self.ws_queue(ws)
        return _one(self._call(lambda: queue.show(bead)))

    def exists(self, ws: str, bead: str) -> bool:
        """False only when bd says the bead does not exist; any other failure raises."""
        queue = self.ws_queue(ws)
        try:
            queue.show(bead)
        except RuntimeError as exc:
            if NOT_FOUND in str(exc):
                return False
            raise BeadsUnavailable("show failed") from None
        except _FAILURES as exc:
            raise BeadsUnavailable(f"show failed ({type(exc).__name__})") from None
        return True

    def claimable(self, ws: str, bead: Bead) -> bool:
        """Whether btq's claim can take a bead the workstream worker's `ready()` listed. btq's claim lists
        ready work again with the claiming per-bead worker and refuses anything outside it, and the two
        workers' `matches` differ only in the session: a bead pinned with `session:<workstream session>`
        is listed but can never be claimed. A bead whose ID is not a slug has no per-bead worker."""
        if not ids.SLUG.fullmatch(bead.id):
            return False
        queue = self.bead_queue(ws, bead.id)
        return self._call(lambda: queue.matches(bead.raw))

    def read_claim(self, ws: str, bead: str) -> ClaimView:
        """Who holds the claim. GONE only when bd confirms the bead does not exist; any other failure to
        read it raises BeadsUnavailable."""
        try:
            found = self.show(ws, bead)
        except BeadsUnavailable:
            if self.exists(ws, bead):
                raise
            return ClaimView.GONE
        if found.status == "in_progress" and found.assignee == self.bead_queue(ws, bead).worker:
            return ClaimView.OURS
        if found.status == "open" and found.assignee is None:
            return ClaimView.FREE
        return ClaimView.OTHER

    def ours(self, ws: str) -> list[Bead]:
        """Every bead held by one of this workstream's per-bead workers, with dependency detail. Found by
        assignee across every unclosed claimed status, never by routing labels: a bead whose labels changed
        under its claim is still ours, and recovery must see it. A bead whose ID is not a slug can't have
        been claimed by wsd and is skipped."""
        queue = self.ws_queue(ws)
        raw = self._call(lambda: queue.bd("list", "--status", "in_progress,blocked,open", "--limit", "0"))
        if not isinstance(raw, list):
            raise UnexpectedShape("list did not return a list")
        found: list[Bead] = []
        for item in cast(list[Any], raw):
            listed = parse(item, detail=False)
            if not ids.SLUG.fullmatch(listed.id):
                continue
            if listed.assignee == self.bead_queue(ws, listed.id).worker:
                found.append(self.show(ws, listed.id))
        return found

    def with_metadata(self, ws: str, key: str, settled: frozenset[str]) -> list[str]:
        """IDs of beads labelled `ws:<ws>`, closed ones included, whose metadata `key` is set to anything
        but one of the `settled` values. Closing a bead says nothing about its action, so status is never
        a reason to skip one, and a value wsd doesn't know (a number, an object, null) is never read as
        settled."""
        queue = self.ws_queue(ws)
        raw = self._call(lambda: queue.bd("list", "--all", "--label", f"ws:{ws}", "--limit", "0"))
        if not isinstance(raw, list):
            raise UnexpectedShape("list did not return a list")
        beads = [parse(item, detail=False) for item in cast(list[Any], raw)]
        return [b.id for b in beads if key in b.metadata and not _settled(b.metadata[key], settled)]

    def validate(self, ws: str, bead: str) -> Bead:
        """btq's post-claim checks, run again right before a launch: the bead is in progress under our
        per-bead worker (NotOurs otherwise), and btq's own `matches` and `design_allowed` still pass
        (RoutingChanged otherwise). Returns the bead as read under the worker's exclusive lock."""
        with self._owned(ws, bead) as owned:
            try:
                issue = owned.owned(bead)
                ok = owned.matches(issue) and owned.design_allowed(issue)
            except ValueError as exc:
                if str(exc).startswith(BTQ_NOT_OWNED):
                    raise NotOurs(bead) from None
                raise
        if not ok:
            raise RoutingChanged(bead)
        return _one(issue)

    def comments(self, ws: str, bead: str) -> list[str]:
        queue = self.ws_queue(ws)
        raw = self._call(lambda: queue.bd("comments", bead))
        if not isinstance(raw, list):      # bd answers [] when there are none; null is not evidence of none
            raise UnexpectedShape("comments did not return a list")
        texts: list[str] = []
        for item in cast(list[Any], raw):
            if not isinstance(item, dict) or not isinstance(cast(dict[str, Any], item).get("text"), str):
                raise UnexpectedShape("a comment has no text")
            texts.append(cast(str, cast(dict[str, Any], item)["text"]))
        return texts

    # --- the shared pause flag (§4.3) ---

    def paused(self, ws: str) -> bool:
        """True unless the flag is confirmed absent: an unreadable state directory counts as paused."""
        flag = self.ws_queue(ws).state / "paused"
        try:
            os.lstat(flag)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True

    def set_paused(self, ws: str, paused: bool) -> None:
        flag = self.ws_queue(ws).state / "paused"
        try:
            if paused:
                flag.touch()
            else:
                flag.unlink(missing_ok=True)
        except OSError as exc:
            raise BeadsUnavailable(f"pause flag not written ({type(exc).__name__})") from None
        if self.paused(ws) != paused:
            raise BeadsUnavailable("pause flag did not read back")

    # --- claim (§4.3) ---

    def claim(self, ws: str, bead: str) -> Bead:
        """Claim with the per-bead worker. ClaimRefused means nothing was written; ClaimUncertain means the
        caller must `read_claim` before anything else; RoutingChanged means the claim is ours but the
        bead must not be executed."""
        queue = self.bead_queue(ws, bead)
        try:
            return _one(queue.claim(bead))
        except ValueError as exc:
            if str(exc).startswith(BTQ_REFUSALS):
                raise ClaimRefused(str(exc)) from None
            raise ClaimUncertain(type(exc).__name__) from None
        except RuntimeError as exc:
            if str(exc).startswith(BTQ_ROUTING_CHANGED):
                raise RoutingChanged(bead) from None
            raise ClaimUncertain(type(exc).__name__) from None
        except _FAILURES as exc:
            raise ClaimUncertain(type(exc).__name__) from None

    # --- owned writes ---

    @contextlib.contextmanager
    def _owned(self, ws: str, bead: str) -> Generator[QueueLike]:
        queue = self.bead_queue(ws, bead)
        try:
            with queue.exclusive():
                try:
                    queue.owned(bead)
                except ValueError as exc:
                    if str(exc).startswith(BTQ_NOT_OWNED):
                        raise NotOurs(bead) from None
                    raise
                yield queue
        except (NotOurs, BeadsUnavailable):
            raise
        except _FAILURES as exc:
            raise BeadsUnavailable(f"queue write failed ({type(exc).__name__})") from None

    def _write(self, ws: str, bead: str, *args: str) -> None:
        with self._owned(ws, bead) as queue:
            queue.bd(*args)

    def ensure_label(self, ws: str, bead: str, label: str, present: bool = True) -> None:
        if (label in self.show(ws, bead).labels) == present:
            return
        self._write(ws, bead, "label", "add" if present else "remove", bead, label)
        if (label in self.show(ws, bead).labels) != present:
            raise BeadsUnavailable("label change did not read back")

    def ensure_blocker(self, ws: str, bead: str, blocker: str) -> None:
        def has() -> bool:
            deps = self.show(ws, bead).deps or ()
            return any(d.id == blocker and d.kind not in NON_BLOCKING_DEPS for d in deps)
        if has():
            return
        self._write(ws, bead, "dep", "add", bead, blocker)
        if not has():
            raise BeadsUnavailable("blocking edge did not read back")

    def ensure_comment(self, ws: str, bead: str, mark: str, text: str) -> None:
        """Comments are not idempotent in bd, so `mark` (unique per write, and part of `text`) is looked for
        first and the comment is added only if no comment holds it."""
        if mark not in text:
            raise ValueError("the comment text must contain its mark")
        if any(mark in c for c in self.comments(ws, bead)):
            return
        self._write(ws, bead, "comments", "add", bead, text)
        if not any(mark in c for c in self.comments(ws, bead)):
            raise BeadsUnavailable("comment did not read back")

    def ensure_record(self, ws: str, bead: str, record: SessionRecord,
                      replace_unreadable: bool = False) -> None:
        """Write the launched-session record before the first launch it describes, and read it back. A
        record already on the bead with the same launch identity is kept exactly as it is, with every field
        plan 4 added; one with another identity raises RecordConflict, and an unreadable one
        RecordUnreadable, unless `replace_unreadable` (only the operator's release sets it). The read, the
        write and the read-back all run under the per-bead worker's lock, so nothing writes the record in
        between."""
        with self._owned(ws, bead) as queue:
            try:
                current = self.show(ws, bead).record()
            except RecordUnreadable:
                if not replace_unreadable:
                    raise
                current = None
            if current is None:
                self._call(lambda: queue.bd("update", bead, "--set-metadata",
                                            f"{RECORD_KEY}={encode_record(record)}"))
                current = self.show(ws, bead).record()
                if current is None:
                    raise BeadsUnavailable("session record did not read back")
            if current != record:
                raise RecordConflict(bead)

    # --- worktrees (§4.3: btq's convention) ---

    def worktree(self, ws: str, bead: str, repository: Path) -> Path:
        """The bead's btq worktree, `<repo>-btq-<id>` on branch `btq/<id>`. Idempotent: an existing path
        is reused only if it is that worktree on that branch; anything else is a WorktreeConflict."""
        ids.slug(bead, "bead")
        try:
            repo = repository.resolve(strict=True)
        except OSError:
            raise WorktreeConflict("repository missing") from None
        destination = repo.parent / f"{repo.name}-btq-{bead}"
        if os.path.lexists(destination):
            return self.verify_worktree(ws, bead, repo, destination)
        queue = self.bead_queue(ws, bead)
        try:
            result = queue.worktree(bead, str(repo))
        except ValueError as exc:
            if str(exc).startswith(BTQ_NOT_OWNED):
                raise NotOurs(bead) from None
            raise BeadsUnavailable(type(exc).__name__) from None
        except _FAILURES as exc:
            raise BeadsUnavailable(f"worktree failed ({type(exc).__name__})") from None
        if not isinstance(result, dict) or cast(dict[str, Any], result).get("worktree") != str(destination):
            raise UnexpectedShape("worktree result")
        return self.verify_worktree(ws, bead, repo, destination)

    def verify_worktree(self, ws: str, bead: str, repository: Path, worktree: Path) -> Path:
        """`worktree` is this bead's worktree of `repository`: a real directory at its own top level, on
        branch `btq/<id>`, sharing the repository's git common directory, recorded on the bead by btq for
        our per-bead worker, and holding the base btq recorded (a commit HEAD descends from; should several
        notes name the path, every one must hold). A path that fails any check (including a worktree whose
        provenance note was never written, or a branch of the same name with unrelated history) is a
        WorktreeConflict: inspected by a human, never deleted or reused."""
        worker = self.bead_queue(ws, bead).worker
        try:
            repo = repository.resolve(strict=True)
        except OSError:
            raise WorktreeConflict("repository missing") from None
        bases = self.show(ws, bead).bases(worker, str(repo), str(worktree))
        try:
            ok = (bool(bases) and worktree.is_dir() and not worktree.is_symlink()
                  and gitwip.toplevel(worktree) == worktree.resolve()
                  and gitwip.branch(worktree) == f"btq/{bead}"
                  and gitwip.common_dir(worktree) == gitwip.common_dir(repo)
                  and all(FULL_SHA.fullmatch(base) and gitwip.descends_from(worktree, base)
                          for base in bases))
        except (OSError, gitwip.GitFailed):
            ok = False
        if not ok:
            raise WorktreeConflict("the path is not this bead's recorded worktree")
        return worktree
