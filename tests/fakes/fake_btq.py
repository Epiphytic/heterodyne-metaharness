"""An in-memory beads queue with btq's `Queue` interface (the `QueueLike` slice wsd uses).

It answers in the bd 1.1 JSON shapes recorded in spike S6 (`docs/spikes/S6-beads-json.md`): empty
`labels` and `metadata` are omitted, `dependencies` is omitted when `dependency_count` is 0, and `list`
gives dependency edges where `show` gives the beads depended on. Claims, ownership, routing (`matches`),
the design gate (`design_allowed`, simplified: a `kind:task` needs a `design_approval` the world
accepts), worktree provenance notes and the pause flag follow btq's rules and error messages. As in btq,
`ready()` keeps only what the listing worker's `matches` and design gate pass, and `claim()` refuses a
bead its own worker's `ready()` does not list (so a `session:` pin is honoured by both). Faults are
injected per call name, optionally for one bead only; `after=True` performs the write and then fails,
which is how an uncertain write looks to wsd.
"""

import contextlib
import hashlib
import subprocess
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOST = "testhost"


@dataclass
class FakeBead:
    id: str
    title: str = "a task"
    status: str = "open"
    assignee: str | None = None
    labels: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    deps: list[tuple[str, str]] = field(default_factory=list)      # (other bead, dependency type)
    comments: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Fault:
    call: str
    exc: Exception
    after: bool = False
    times: int = 1
    bead: str | None = None      # only calls about this bead


class World:
    def __init__(self, state_root: Path) -> None:
        self.beads: dict[str, FakeBead] = {}
        self.state_root = state_root
        self.down = False
        self.faults: list[Fault] = []
        self.calls: list[tuple[str, str]] = []        # (worker, call)
        self.claims: list[str] = []
        self.stolen: set[str] = set()      # beads another worker claims first (a lost race)
        self.worktrees: list[str] = []
        self.approvals = {"approval-1"}     # design approvals btq's gate accepts
        self.lock = threading.RLock()

    def add(self, bead_id: str, ws: str = "alpha", **kw: Any) -> FakeBead:
        labels = kw.pop("labels", [])
        kind = kw.pop("kind", "task")
        metadata = {"design_approval": "approval-1", **kw.pop("metadata", {})}
        bead = FakeBead(bead_id, labels=["agent:wsd", f"ws:{ws}", f"kind:{kind}", *labels],
                        metadata=metadata, **kw)
        self.beads[bead_id] = bead
        return bead

    def fault(self, call: str, exc: Exception, after: bool = False, times: int = 1,
              bead: str | None = None) -> None:
        self.faults.append(Fault(call, exc, after, times, bead))

    def _take(self, call: str, after: bool, bead: str | None) -> Exception | None:
        for f in self.faults:
            if (f.call == call and f.after == after and f.times > 0
                    and (f.bead is None or f.bead == bead)):
                f.times -= 1
                return f.exc
        return None

    def check(self, call: str, worker: str, bead: str | None = None) -> None:
        self.calls.append((worker, call))
        if self.down:
            raise RuntimeError("dolt: connection refused")
        exc = self._take(call, after=False, bead=bead)
        if exc is not None:
            raise exc

    def check_after(self, call: str, bead: str | None = None) -> None:
        exc = self._take(call, after=True, bead=bead)
        if exc is not None:
            raise exc

    def blocked(self, bead: FakeBead) -> bool:
        return any(kind not in ("parent-child", "related", "discovered-from")
                   and self.beads[other].status != "closed" for other, kind in bead.deps)

    def ready_for(self, ws: str) -> list[FakeBead]:
        return [b for b in sorted(self.beads.values(), key=lambda b: b.id)
                if b.status == "open" and b.assignee is None and "agent:wsd" in b.labels
                and f"ws:{ws}" in b.labels and not self.blocked(b)]

    def close(self, bead_id: str) -> None:
        self.beads[bead_id].status = "closed"

    def json(self, bead: FakeBead, detail: bool) -> dict[str, Any]:
        out: dict[str, Any] = {"id": bead.id, "title": bead.title, "status": bead.status,
                               "priority": 2, "issue_type": "task",
                               "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}
        if bead.assignee:
            out["assignee"] = bead.assignee
        if bead.labels:
            out["labels"] = list(bead.labels)
        if bead.metadata:
            out["metadata"] = dict(bead.metadata)
        if bead.notes:
            out["notes"] = bead.notes
        out["dependency_count"] = len(bead.deps)
        if not detail and bead.deps:        # `bd list` gives the edges, not the beads they point at
            out["dependencies"] = [{"issue_id": bead.id, "depends_on_id": other, "type": kind,
                                    "created_at": "2026-10-01T00:00:00Z", "metadata": "{}"}
                                   for other, kind in bead.deps]
        if detail and bead.deps:
            deps: list[dict[str, Any]] = []
            for other, kind in bead.deps:
                o = self.beads[other]
                dep: dict[str, Any] = {"id": o.id, "title": o.title, "status": o.status,
                                       "dependency_type": kind}
                if o.labels:
                    dep["labels"] = list(o.labels)
                deps.append(dep)
            out["dependencies"] = deps
        return out


class FakeQueue:
    def __init__(self, world: World, ws: str, session: str) -> None:
        self.world = world
        self.ws = ws
        self.worker = f"wsd:{HOST}:{session}"
        self.state = world.state_root / hashlib.sha256(self.worker.encode()).hexdigest()
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._exclusive = threading.Lock()

    def _get(self, bead_id: str) -> FakeBead:
        bead = self.world.beads.get(bead_id)
        if bead is None:
            raise RuntimeError(f"Error: no issue found matching {bead_id!r}")
        return bead

    def show(self, issue_id: str) -> Any:
        with self.world.lock:
            self.world.check("show", self.worker, issue_id)
            return self.world.json(self._get(issue_id), detail=True)

    def ready(self) -> Any:
        with self.world.lock:
            self.world.check("ready", self.worker)
            return self._ready()

    def _ready(self) -> list[dict[str, Any]]:
        """btq's `ready()`: [] while this worker is paused, else `bd ready` for its agent and workstream,
        kept only where this worker's `matches` and the design gate pass (a `session:` pin included)."""
        if (self.state / "paused").exists():
            return []
        listed = [self.world.json(b, detail=False) for b in self.world.ready_for(self.ws)]
        return [item for item in listed if self.matches(item) and self.design_allowed(item)]

    def claim(self, issue_id: str) -> Any:
        with self.world.lock:
            self.world.check("claim", self.worker, issue_id)
            bead = self._get(issue_id)
            if issue_id in self.world.stolen:
                bead.status, bead.assignee = "in_progress", "codex:otherhost:x"
            mine = [b for b in self.world.beads.values() if b.assignee == self.worker]
            if any(b.status in ("in_progress", "blocked") for b in mine):
                raise ValueError("Finish or release this worker's existing claim first")
            if issue_id not in {item["id"] for item in self._ready()}:    # this worker's own ready()
                raise ValueError("Task is not eligible for this worker")
            bead.status, bead.assignee = "in_progress", self.worker
            self.world.claims.append(issue_id)
            self.world.check_after("claim", issue_id)
            return self.world.json(bead, detail=True)

    def owned(self, issue_id: str, statuses: tuple[str, ...] = ("in_progress",)) -> Any:
        with self.world.lock:
            self.world.check("owned", self.worker, issue_id)
            bead = self._get(issue_id)
            if bead.status not in statuses or bead.assignee != self.worker:
                raise ValueError("Task is not in progress under this worker")
            return self.world.json(bead, detail=True)

    def worktree(self, issue_id: str, repository: str) -> Any:
        with self.world.lock:
            self.world.check("worktree", self.worker, issue_id)
            self.owned(issue_id)
            repo = Path(repository).resolve(strict=True)
            dest = repo.parent / f"{repo.name}-btq-{issue_id}"
            base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                  capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", f"btq/{issue_id}",
                            str(dest), base], check=True, capture_output=True)
            self.world.worktrees.append(issue_id)
            self.world.check_after("worktree", issue_id)     # git made it, the provenance note failed
            bead = self._get(issue_id)
            line = f"worker={self.worker}; repository={repo}; base={base}; worktree={dest}"
            bead.notes = f"{bead.notes}\n{line}" if bead.notes else line
            return {"worktree": str(dest), "base": base}

    def matches(self, issue: Any) -> bool:
        """btq's routing rule, for this worker's agent (`wsd`), workstream and session."""
        labels: list[str] = issue.get("labels", [])
        routes = {p: [v[len(p):] for v in labels if v.startswith(p)]
                  for p in ("agent:", "ws:", "session:", "kind:")}
        session = self.worker.split(":", 2)[2]
        return (routes["agent:"] == ["wsd"] and routes["ws:"] == [self.ws]
                and routes["session:"] in ([], [session]) and len(routes["kind:"]) == 1
                and routes["kind:"][0] in ("brainstorm", "task", "review", "research")
                and "needs-human" not in labels)

    def design_allowed(self, issue: Any) -> bool:
        """btq's gate, simplified: a `kind:task` needs a design approval the world accepts."""
        if "kind:task" not in issue.get("labels", []):
            return True
        return issue.get("metadata", {}).get("design_approval") in self.world.approvals

    @contextlib.contextmanager
    def exclusive(self) -> Generator[None]:
        with self._exclusive:
            yield

    def bd(self, *args: str) -> Any:
        with self.world.lock:
            verb = args[0]
            name = "comments add" if verb == "comments" and len(args) > 2 else verb
            about = next((a for a in args[1:] if a in self.world.beads), None)
            self.world.check(name, self.worker, about)
            result = self._bd(list(args))
            self.world.check_after(name, about)
            return result

    def _bd(self, args: list[str]) -> Any:
        verb = args.pop(0)
        if verb == "list":
            labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
            every = "--all" in args
            statuses = args[args.index("--status") + 1].split(",") if "--status" in args else []
            beads = sorted(self.world.beads.values(), key=lambda b: b.id)
            return [self.world.json(b, detail=False) for b in beads
                    if (every or b.status in statuses) and all(lbl in b.labels for lbl in labels)]
        if verb == "label":
            action, bead_id, label = args
            bead = self._get(bead_id)
            if action == "add" and label not in bead.labels:
                bead.labels.append(label)
            if action == "remove" and label in bead.labels:
                bead.labels.remove(label)
            done = "added" if action == "add" else "removed"
            return [{"issue_id": bead_id, "label": label, "status": done}]
        if verb == "dep":
            _, bead_id, other = args
            bead = self._get(bead_id)
            self._get(other)
            if not any(d == other for d, _ in bead.deps):
                bead.deps.append((other, "blocks"))
            return {"status": "added"}
        if verb == "comments":
            if len(args) == 1:
                bead = self._get(args[0])
                return [{"id": i, "issue_id": bead.id, "author": self.worker, "text": t,
                         "created_at": "2026-10-01T00:00:00Z"} for i, t in enumerate(bead.comments)]
            _, bead_id, text = args
            self._get(bead_id).comments.append(text)
            return {"text": text}
        if verb == "update":
            bead_id, flag, pair = args
            assert flag == "--set-metadata"
            key, value = pair.split("=", 1)
            try:      # bd 1.1 stores a number as a number, anything else (JSON objects too) as a string
                self._get(bead_id).metadata[key] = int(value)
            except ValueError:
                self._get(bead_id).metadata[key] = value
            return [self.world.json(self._get(bead_id), detail=False)]
        raise AssertionError(f"fake bd does not support {verb}")


def factory(world: World):  # noqa: ANN201 - returns a QueueFactory
    queues: dict[tuple[str, str], FakeQueue] = {}

    def make(ws: str, session: str) -> FakeQueue:
        if world.down:
            raise OSError("credentials unreadable")
        queues.setdefault((ws, session), FakeQueue(world, ws, session))
        return queues[(ws, session)]

    return make
