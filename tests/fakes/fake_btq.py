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
import json
import subprocess
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOST = "testhost"
ASK_FIELDS = ("effect", "excludes", "why", "risks", "refs")      # btq's


@dataclass
class FakeBead:
    id: str
    title: str = "a task"
    description: str = ""
    issue_type: str = "task"
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
        self.created: list[str] = []        # beads `bd create` made, in order
        self.closed_reasons: dict[str, str] = {}      # bead -> the `--reason` of its `bd close`
        self.prefix = "btq"                 # the database's `issue_prefix`, which an explicit `--id` must use
        self.orphan_next_create = False     # the next `bd create`'s caller dies before bd commits it
        self.orphans: list[tuple[FakeQueue, list[str]]] = []      # creates still running, uncommitted
        self.hooks: list[tuple[str, int, Callable[[], None]]] = []      # (call, nth, fn): see `before`
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

    def before(self, call: str, fn: Callable[[], None], nth: int = 1) -> None:
        """Run `fn` once, just before the `nth` call named `call` from now on is made."""
        self.hooks.append((call, nth, fn))

    def live_creators(self, worker: str) -> int:
        """The fence's probe: `bd create` processes of `worker` still running (the orphans)."""
        return sum(1 for queue, _ in self.orphans if queue.worker == worker)

    def commit_orphans(self) -> None:
        """Every orphaned `bd create` finishes and commits, as its caller is long gone."""
        orphans, self.orphans = self.orphans, []
        for queue, args in orphans:
            queue._create(args)      # pyright: ignore[reportPrivateUsage]

    def check(self, call: str, worker: str, bead: str | None = None) -> None:
        due = []
        for i, (name, nth, fn) in enumerate(self.hooks):
            if name == call:
                self.hooks[i] = (name, nth - 1, fn)
                if nth == 1:
                    due.append(fn)
        self.hooks = [h for h in self.hooks if h[1] > 0]
        for fn in due:
            fn()
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
                               "priority": 2, "issue_type": bead.issue_type,
                               "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}
        if bead.description:
            out["description"] = bead.description
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
        self.policy: dict[str, Any] = {}

    def ask_digest(self, issue: Any) -> tuple[str | None, list[str]]:
        """btq's `ask_digest`, simplified: SHA-256 over the title, description and `metadata.ask`, with a
        gap for an ask that is not exactly btq's fields."""
        if not isinstance(issue, dict):
            return None, ["issue is not an object"]
        fields: dict[str, Any] = issue
        metadata = fields.get("metadata")
        ask = metadata.get("ask") if isinstance(metadata, dict) else None
        if isinstance(ask, str):
            try:
                ask = json.loads(ask)
            except ValueError:
                ask = None
        if not isinstance(ask, dict) or set(ask) != set(ASK_FIELDS):
            return None, ["metadata.ask is not exactly btq's ask fields"]
        body = json.dumps([fields.get("title"), fields.get("description", ""), ask], sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest(), []

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
        if verb == "create":
            if self.world.orphan_next_create:
                self.world.orphan_next_create = False
                self.world.orphans.append((self, list(args)))
                raise RuntimeError("the caller died while bd create ran")
            return self._create(args)
        if verb == "config":
            action, key = args
            assert action == "get", "fake bd config: get KEY"
            value = self.world.prefix if key == "issue_prefix" else ""      # bd 1.1: "" for an unknown key
            return {"key": key, "schema_version": 1, "value": value}
        if verb == "close":      # `bd close ID --reason R`, or `--reason=R`
            bead_id, flag, *rest = args
            name, eq, reason = flag.partition("=")
            assert name == "--reason" and len(rest) == (0 if eq else 1), "fake bd close: ID --reason R"
            reason = reason if eq else rest[0]
            bead = self._get(bead_id)
            bead.status = "closed"
            self.world.closed_reasons[bead_id] = reason
            return [self.world.json(bead, detail=False)]
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

    def _create(self, args: list[str]) -> Any:
        """`bd create`: the title positional or as `--title`; `--description`, `--type` (the bead's
        `issue_type`), `--labels` (comma separated) or repeated `--label`/`-l`, and `--metadata` (a JSON
        object); `--flag=value` or `--flag value`. Answers with the created bead, as bd 1.1's `create --json`
        does. An explicit `--id` must carry the database's prefix (`--force` is never wsd's), and an `--id`
        that exists is upserted exactly as bd 1.1 does it (probed 2026-10-15): title, description, type and
        metadata are replaced, labels are added to, comments kept, and a closed bead is reopened with its
        close reason cleared."""
        values: dict[str, list[str]] = {}
        title: str | None = None
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--json":
                i += 1
                continue
            if arg.startswith("-"):
                name, eq, value = arg.partition("=")
                if not eq:
                    i += 1
                    value = args[i]
                values.setdefault(name.lstrip("-"), []).append(value)
            else:
                assert title is None, f"fake bd create: a second positional {arg!r}"
                title = arg
            i += 1
        title = title if title is not None else values.pop("title", [""])[-1]
        labels = [lbl for v in values.get("labels", []) + values.get("label", []) + values.get("l", [])
                  for lbl in v.split(",") if lbl]
        metadata: dict[str, Any] = {}
        for raw in values.get("metadata", []):
            parsed = json.loads(raw)
            assert isinstance(parsed, dict), "fake bd create: --metadata is not a JSON object"
            metadata.update(parsed)
        assert "force" not in values, "fake bd create: wsd never passes --force"
        description = values.get("description", [""])[-1]
        issue_type = values.get("type", ["task"])[-1]
        explicit = values.get("id", [None])[-1]
        if explicit is not None and not explicit.startswith(f"{self.world.prefix}-"):
            raise RuntimeError(f"Error: prefix mismatch: database uses '{self.world.prefix}-' but ID "
                               f"'{explicit}' doesn't match (use --force to override)")
        existing = self.world.beads.get(explicit) if explicit is not None else None
        if existing is not None:         # bd 1.1's upsert
            existing.title, existing.description, existing.issue_type = title, description, issue_type
            existing.metadata = metadata
            existing.labels.extend(lbl for lbl in labels if lbl not in existing.labels)
            existing.status = "open"
            self.world.closed_reasons.pop(existing.id, None)
            return self.world.json(existing, detail=True)
        bead_id = explicit if explicit is not None else f"btq-h{len(self.world.created) + 1}"
        bead = FakeBead(bead_id, title=title, description=description, issue_type=issue_type, labels=labels,
                        metadata=metadata)
        self.world.beads[bead_id] = bead
        self.world.created.append(bead_id)
        return self.world.json(bead, detail=True)


def factory(world: World):  # noqa: ANN201 - returns a QueueFactory
    queues: dict[tuple[str, str], FakeQueue] = {}

    def make(ws: str, session: str) -> FakeQueue:
        if world.down:
            raise OSError("credentials unreadable")
        queues.setdefault((ws, session), FakeQueue(world, ws, session))
        return queues[(ws, session)]

    return make
