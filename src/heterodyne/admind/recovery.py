"""Source-enrolled maintenance NOTICE consent. Never executes a queue/native operation.

Local callers share the trusted daemon UID. Registration is not authority; only an
actual authenticated, current-operator message on the delivered notice can consent.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast

import msgspec

from heterodyne.admind.redact import redact

if TYPE_CHECKING:
    from heterodyne.admind.store import Store

SOCKET = "maintenance-recovery.sock"
MAX_BYTES = 262_144
HEX = re.compile(r"[0-9a-f]{64}\Z")
COMMAND = re.compile(r"!maintenance (settle|revoke) ([0-9a-f]{24})\Z")


class Invalid(ValueError):
    """Fixed refusal code; never includes input material."""


class Selector(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    type: Literal["task", "ask", "notice", "local"]
    id: str


class Projection(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    adapter_sha256: str
    selector: Selector
    before_sha256: str
    action: Literal[
        "clear-proven-notice",
        "clear-proven-cancel",
        "abort-proven-handoff",
        "abandon-notice",
        "finish-health",
    ]


class Descriptor(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["maintenance-recovery.v1"]
    proposal_id: str
    operation: str
    mode: Literal["settle", "abandon-notice", "finish-health"]
    enrollment_sha256: str
    original_record_sha256: str
    source_closure: str
    effects_root: str | None
    effect_count: int
    local_records: list[Projection]
    backup_before_sha256: str | None
    failure_before_sha256: str | None
    proof_set_sha256: str
    result: Literal["complete", "abandoned-diagnostic", "health-restored"]
    expires_at: str


class Register(msgspec.Struct, frozen=True, tag="maintenance.recovery.register", forbid_unknown_fields=True):
    descriptor: Descriptor
    proof_set_sha256: str


class Status(msgspec.Struct, frozen=True, tag="maintenance.recovery.status", forbid_unknown_fields=True):
    proposal_sha256: str


class Permit(msgspec.Struct, frozen=True, tag="maintenance.recovery.permit", forbid_unknown_fields=True):
    proposal_sha256: str
    certificate_sha256: str | None = None


Request = Register | Status | Permit


class Reply(msgspec.Struct, frozen=True):
    result: str
    message: str
    view: dict[str, object] | None = None


def canonical(value: object) -> bytes:
    return json.dumps(
        msgspec.to_builtins(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def sha(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise Invalid("duplicate-key")
        result[key] = value
    return result


def decode[T](data: bytes, kind: type[T]) -> T:
    if len(data) > MAX_BYTES:
        raise Invalid("oversized")
    try:
        raw: object = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(Invalid("nonfinite")),
        )
        stack: list[tuple[object, int]] = [(raw, 0)]
        while stack:
            value, depth = stack.pop()
            if depth > 12:
                raise Invalid("depth")
            if isinstance(value, dict):
                stack.extend((item, depth + 1) for item in cast(dict[str, object], value).values())
            elif isinstance(value, list):
                stack.extend((item, depth + 1) for item in cast(list[object], value))
        return msgspec.convert(raw, type=kind, strict=True)
    except (ValueError, TypeError, RecursionError, msgspec.ValidationError):
        raise Invalid("malformed") from None


def stamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise Invalid("time") from None


def descriptor(data: bytes) -> Descriptor:
    obj = decode(data, Descriptor)
    for value in (obj.proposal_id, obj.operation):
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except ValueError:
            raise Invalid("uuid") from None
    hashes = [obj.enrollment_sha256, obj.original_record_sha256, obj.source_closure, obj.proof_set_sha256]
    hashes.extend(
        v for v in (obj.effects_root, obj.backup_before_sha256, obj.failure_before_sha256) if v is not None
    )
    for item in obj.local_records:
        hashes.extend((item.adapter_sha256, item.before_sha256))
        if not re.fullmatch(r"[\x21-\x7e]{1,128}", item.selector.id):
            raise Invalid("selector")
    if not all(HEX.fullmatch(v) for v in hashes):
        raise Invalid("hash")
    if not 0 <= obj.effect_count <= 4096 or len(obj.local_records) > 8:
        raise Invalid("bounds")
    if len({(r.selector.type, r.selector.id) for r in obj.local_records}) != len(obj.local_records):
        raise Invalid("duplicate-selector")
    expected = {
        "settle": "complete",
        "abandon-notice": "abandoned-diagnostic",
        "finish-health": "health-restored",
    }
    if obj.result != expected[obj.mode]:
        raise Invalid("result")
    stamp(obj.expires_at)
    return obj


def check(req: Request) -> str | None:
    try:
        decode(msgspec.json.encode(req), cast(type[Request], Request))
        if isinstance(req, Register):
            obj = descriptor(canonical(req.descriptor))
            if req.proof_set_sha256 != obj.proof_set_sha256:
                raise Invalid("proof-set")
        elif not HEX.fullmatch(req.proposal_sha256):
            raise Invalid("hash")
        elif isinstance(req, Permit) and req.certificate_sha256 is not None:
            if not HEX.fullmatch(req.certificate_sha256):
                raise Invalid("hash")
    except (Invalid, TypeError, ValueError):
        return "invalid-request"
    return None


def private_read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise Invalid("unsafe-file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = stream.read(MAX_BYTES + 1)
        if len(value) > MAX_BYTES:
            raise Invalid("oversized")
        return value
    finally:
        os.close(fd)


def publish(path: Path, data: bytes) -> None:
    """Atomic no-replace immutable publication; a crash never exposes a partial file."""
    private_parents(path.parent)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise Invalid("unsafe-directory")
    temporary = path.parent / (".recovery-" + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            repair_alias(path, data)
            if private_read(path) != data:
                raise Invalid("publication-conflict") from None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            temporary.unlink()
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def private_parents(path: Path) -> None:
    """Create every missing ancestor privately without changing an existing ancestor."""
    missing: list[Path] = []
    current = path
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
    # The publication parent must itself be private, even when it already existed.
    for directory in [path, *missing]:
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise Invalid("unsafe-directory")


def repair_alias(path: Path, data: bytes) -> None:
    """Repair only the exact link-before-unlink crash of our immutable publisher.

    An arbitrary extra link is never accepted. A bounded private-directory scan
    must find exactly one own temporary alias to this exact inode and byte value.
    """
    info = path.lstat()
    if info.st_nlink == 1:
        return
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 2
    ):
        raise Invalid("unsafe-file")
    alias = None
    with os.scandir(path.parent) as entries:
        for count, entry in enumerate(entries):
            if count >= 256:
                raise Invalid("publication-directory-bound")
            if not re.fullmatch(r"\.recovery-[0-9a-f]{32}", entry.name):
                continue
            other = entry.stat(follow_symlinks=False)
            if (other.st_dev, other.st_ino) == (info.st_dev, info.st_ino):
                if alias is not None:
                    raise Invalid("unsafe-file")
                alias = Path(entry.path)
    if alias is None:
        raise Invalid("unsafe-file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        now = os.fstat(fd)
        if (now.st_dev, now.st_ino, now.st_nlink) != (info.st_dev, info.st_ino, 2):
            raise Invalid("unsafe-file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            if stream.read(MAX_BYTES + 1) != data:
                raise Invalid("publication-conflict")
        alias.unlink()
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)


class Enrollment(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Installed data-only locator; publishing it is a separate deployment action."""

    schema: Literal["admind-maintenance-recovery-enrollment.v1"]
    enrollment_sha256: str
    source_closure: str
    coordinator_root: str
    descriptor_links: dict[str, str] = {}


SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance_recovery_proposals (
 hash TEXT PRIMARY KEY, operation TEXT NOT NULL UNIQUE, descriptor BLOB NOT NULL,
 notice_key TEXT NOT NULL UNIQUE, notice_text TEXT NOT NULL, created_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting-consent','consented','revoked','terminal')),
 decision TEXT, operator TEXT, policy TEXT, generation INTEGER NOT NULL DEFAULT 0,
 permit BLOB, terminal BLOB);
CREATE TABLE IF NOT EXISTS maintenance_recovery_messages (
 account TEXT NOT NULL, group_id TEXT NOT NULL, message_id TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL, proposal TEXT NOT NULL, provenance BLOB NOT NULL,
 outcome TEXT NOT NULL, PRIMARY KEY(account,group_id,message_id));
CREATE TABLE IF NOT EXISTS maintenance_recovery_revocations (
 account TEXT NOT NULL, group_id TEXT NOT NULL, message_id TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL, proposal TEXT NOT NULL UNIQUE, provenance BLOB NOT NULL,
 outcome TEXT NOT NULL, PRIMARY KEY(account,group_id,message_id));
CREATE TABLE IF NOT EXISTS maintenance_recovery_refusals (
 account TEXT NOT NULL, group_id TEXT NOT NULL, message_id TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL, provenance BLOB NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(account,group_id,message_id));
"""


class MessageFields(TypedDict):
    account: str
    group: str
    mid: str
    sender: str
    target: str | None
    text: str
    authority: Callable[[], str | None]


class Service:
    """All mutating calls join Store transactions and daemon work_lock serialization.

    The pure core's `authority` callback is a fresh host observation, never request JSON.
    It is called twice without awaits around each transaction's decision boundary.
    """

    def __init__(
        self,
        store: Store,
        root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        boottime: Callable[[], int] | None = None,
        boot: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.root = root
        self.clock = clock or (lambda: datetime.now(UTC))
        self.boottime = boottime or boot_ns
        self.boot = boot or boot_id
        with store.transaction():
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    store.db.execute(statement)

    def enrollment(self) -> Enrollment:
        obj = decode(private_read(self.root / "enrollment.json"), Enrollment)
        if (
            not HEX.fullmatch(obj.enrollment_sha256)
            or not HEX.fullmatch(obj.source_closure)
            or not Path(obj.coordinator_root).is_absolute()
            or len(obj.descriptor_links) > 128
            or any(not HEX.fullmatch(key) or len(link) > 2048 for key, link in obj.descriptor_links.items())
        ):
            raise Invalid("enrollment")
        graph = decode(private_read(Path(obj.coordinator_root) / "enrollment.json"), dict[str, object])
        sources = graph.get("sources")
        roots = graph.get("roots")
        if (
            sha(graph) != obj.enrollment_sha256
            or graph.get("root") != obj.coordinator_root
            or graph.get("schema") != "queue-maintenance-fence.v1"
            or type(graph.get("uid")) is not int
            or graph.get("uid") != os.getuid()
            or not isinstance(sources, dict)
            or not sources
            or len(cast(dict[str, object], sources)) > 256
            or not isinstance(roots, dict)
            or cast(dict[str, object], roots).get("admind_maintenance_recovery") != str(self.root)
            or sha(cast(dict[str, object], sources)) != obj.source_closure
        ):
            raise Invalid("enrollment-drift")
        for filename, expected in cast(dict[str, object], sources).items():
            if (
                not Path(filename).is_absolute()
                or not isinstance(expected, str)
                or not HEX.fullmatch(expected)
                or source_hash(Path(filename)) != expected
            ):
                raise Invalid("source-drift")
        coordinator = graph.get("coordinator_path")
        if (
            not isinstance(coordinator, str)
            or not Path(coordinator).is_absolute()
            or source_hash(Path(coordinator)) != graph.get("coordinator_sha256")
        ):
            raise Invalid("coordinator-drift")
        coverage = decode(private_read(Path(obj.coordinator_root) / "coverage.json"), dict[str, object])
        if sha(coverage) != graph.get("coverage_sha256"):
            raise Invalid("coverage-drift")
        return obj

    def get(self, key: str) -> dict[str, object]:
        if not HEX.fullmatch(key):
            raise Invalid("hash")
        with self.store.transaction(read_only=True):
            row = self.store.db.execute(
                "SELECT state,generation,permit,terminal FROM maintenance_recovery_proposals WHERE hash=?",
                (key,),
            ).fetchone()
            if row is None:
                raise Invalid("missing")
            return {
                "proposal_sha256": key,
                "state": row[0],
                "generation": row[1],
                "permit": None if row[2] is None else json.loads(row[2]),
                "terminal": None if row[3] is None else json.loads(row[3]),
            }

    def live(self, obj: Descriptor, created: str | None = None) -> datetime:
        at = self.clock()
        if at.tzinfo is None or not math.isfinite(at.timestamp()):
            raise Invalid("clock")
        expiry = stamp(obj.expires_at)
        if expiry <= at or expiry > at + timedelta(hours=24):
            raise Invalid("expired")
        if created is not None and at < stamp(created):
            raise Invalid("clock-rollback")
        return at

    def register(
        self,
        req: Register,
        *,
        uid: int | None,
        pid: int | None,
        chunk_chars: int,
        post: Callable[[str, str], None],
    ) -> Reply:
        if uid != os.getuid():
            raise Invalid("peer")
        if check(req):
            raise Invalid("request")
        obj = descriptor(canonical(req.descriptor))
        enrolled = self.enrollment()
        if (
            obj.enrollment_sha256 != enrolled.enrollment_sha256
            or obj.source_closure != enrolled.source_closure
        ):
            raise Invalid("enrollment-mismatch")
        at = self.live(obj)
        key = sha(obj)
        # No native command: the enrolled immutable original operation supplies actor provenance.
        original = private_read(Path(enrolled.coordinator_root) / "operations" / f"{obj.operation}.json")
        if sha(decode(original, dict[str, object])) != obj.original_record_sha256:
            raise Invalid("original-mismatch")
        origin = decode(original, dict[str, object])
        actor = origin.get("actor")
        if not isinstance(actor, str) or not re.fullmatch(r"[\x21-\x7e]{1,256}", actor):
            raise Invalid("actor")
        text = render(obj, actor, enrolled.descriptor_links.get(key), min(chunk_chars, 3200))
        from heterodyne.admind import notices

        notice_key = ""
        with self.store.transaction():
            previous = self.store.db.execute(
                "SELECT hash,descriptor FROM maintenance_recovery_proposals WHERE operation=?",
                (obj.operation,),
            ).fetchone()
            if previous is not None:
                if previous[0] != key or bytes(previous[1]) != canonical(obj):
                    raise Invalid("immutable-operation")
                return Reply("existing", "proposal already registered", self.get(key))
            if (
                self.store.db.execute("SELECT count(*) FROM maintenance_recovery_proposals").fetchone()[0]
                >= 128
            ):
                raise Invalid("capacity")
            if (
                self.store.notices_since((at - timedelta(hours=1)).isoformat()) >= notices.NOTICE_PER_HOUR
                or self.store.undelivered_notices() >= notices.NOTICE_UNDELIVERED
            ):
                raise Invalid("notice-capacity")
            notice_id = notices.new_id(self.store.notice_exists)
            notice_key = "notice:" + notice_id
            self.store.db.execute(
                "INSERT INTO maintenance_recovery_proposals "
                "(hash,operation,descriptor,notice_key,notice_text,created_at,state) VALUES(?,?,?,?,?,?,?)",
                (key, obj.operation, canonical(obj), notice_key, text, at.isoformat(), "awaiting-consent"),
            )
            self.store.insert_notice(notice_id, "maintenance", pid, 0, len(text), 0, 1, at.isoformat())
            post(notice_key + ":0", text)
        return Reply("registered", "awaiting delivered-notice consent", self.get(key))

    def message(
        self,
        *,
        account: str,
        group: str,
        mid: str,
        sender: str,
        target: str | None,
        text: str,
        authority: Callable[[], str | None],
        post: Callable[[str], None] | None = None,
    ) -> Reply:
        if len(text.encode("utf-8")) > 1024:
            raise Invalid("command-bound")
        command = COMMAND.fullmatch(text)
        if command is None or not all(HEX.fullmatch(v) for v in (account, mid, sender)):
            raise Invalid("command-auth")
        if not re.fullmatch(r"(?:[0-9a-f]{2}){1,128}", group):
            raise Invalid("group")
        provenance = dict(
            account=account,
            group=group,
            message_id=mid,
            sender=sender,
            reply_to=target,
            text=text,
        )
        payload = sha({k: v for k, v in provenance.items() if k != "policy_sha256"})
        with self.store.transaction():
            duplicate = self.store.db.execute(
                "SELECT payload_sha256,outcome,proposal FROM maintenance_recovery_messages "
                "WHERE account=? AND group_id=? AND message_id=? UNION ALL "
                "SELECT payload_sha256,outcome,proposal FROM maintenance_recovery_revocations "
                "WHERE account=? AND group_id=? AND message_id=?",
                (account, group, mid, account, group, mid),
            ).fetchone()
            if duplicate is not None:
                if duplicate[0] != payload:
                    raise Invalid("replay-mismatch")
                if post is not None:
                    post("Maintenance recovery: " + duplicate[1] + ".")
                return Reply("duplicate", duplicate[1], self.get(duplicate[2]))
            refused = self.store.db.execute(
                "SELECT payload_sha256,reason FROM maintenance_recovery_refusals "
                "WHERE account=? AND group_id=? AND message_id=?",
                (account, group, mid),
            ).fetchone()
            if refused is not None:
                if refused[0] != payload:
                    raise Invalid("replay-mismatch")
                if post is not None:
                    post("Maintenance recovery refused: " + refused[1] + ".")
                return Reply("duplicate", refused[1])
            policy = authority()
            if policy is None:
                raise Invalid("authority")
            provenance["policy_sha256"] = policy
            rows = self.store.db.execute(
                "SELECT hash,descriptor,notice_key,state,created_at FROM "
                "maintenance_recovery_proposals WHERE substr(hash,1,24)=?",
                (command[2],),
            ).fetchall()
            if len(rows) != 1:
                raise Invalid("token")
            key, raw, notice_key, state, created = rows[0]
            deliveries = self.store.db.execute(
                "SELECT message_id,text,status FROM outbox WHERE key IN (?,?) AND status='sent'",
                (notice_key + ":0", notice_key + ":r0"),
            ).fetchall()
            delivery = deliveries[0] if len(deliveries) == 1 else None
            if (
                delivery is None
                or delivery[2] != "sent"
                or target is None
                or delivery[0] != target
                or delivery[1]
                != self.store.db.execute(
                    "SELECT notice_text FROM maintenance_recovery_proposals WHERE hash=?", (key,)
                ).fetchone()[0]
            ):
                raise Invalid("thread-unmapped")
            try:
                self.live(descriptor(bytes(raw)), created)
                live = True
            except Invalid:
                live = False
            revoking = live and command[1] == "revoke" and state in ("awaiting-consent", "consented")
            if (
                not revoking
                and self.store.db.execute(
                    "SELECT count(*) FROM maintenance_recovery_messages WHERE proposal=?", (key,)
                ).fetchone()[0]
                >= 128
            ):
                raise Invalid("message-capacity")
            if authority() != policy:
                raise Invalid("authority-drift")
            outcome = "history-only"
            if live and command[1] == "revoke" and state in ("awaiting-consent", "consented"):
                self.store.db.execute(
                    "UPDATE maintenance_recovery_proposals SET state='revoked' WHERE hash=?", (key,)
                )
                outcome = "revoked"
            elif live and command[1] == "settle" and state == "awaiting-consent":
                self.store.db.execute(
                    "UPDATE maintenance_recovery_proposals SET state='consented',"
                    "decision=?,operator=?,policy=? WHERE hash=?",
                    (mid, sender, policy, key),
                )
                outcome = "consented"
            elif live and command[1] == "settle" and state == "consented":
                outcome = "duplicate-decision"
            table = "maintenance_recovery_revocations" if revoking else "maintenance_recovery_messages"
            self.store.db.execute(
                f"INSERT INTO {table} VALUES(?,?,?,?,?,?,?)",
                (account, group, mid, payload, key, canonical(provenance), outcome),
            )
            if post is not None:
                post("Maintenance recovery: " + outcome + ".")
        return Reply("recorded", outcome, self.get(key))

    def refusal(
        self,
        *,
        account: str,
        group: str,
        mid: str,
        sender: str,
        target: str | None,
        text: str,
        reason: str,
        authority: Callable[[], str | None],
        post: Callable[[str], None],
    ) -> None:
        """Bounded authenticated refusal receipt; never a recovery decision."""
        provenance = dict(
            account=account, group=group, message_id=mid, sender=sender, reply_to=target, text=text
        )
        payload = sha(provenance)
        with self.store.transaction():
            old = self.store.db.execute(
                "SELECT payload_sha256,reason FROM maintenance_recovery_refusals "
                "WHERE account=? AND group_id=? AND message_id=?",
                (account, group, mid),
            ).fetchone()
            if old is not None:
                if old[0] != payload:
                    raise Invalid("replay-mismatch")
                post("Maintenance recovery refused: " + old[1] + ".")
                return
            policy = authority()
            if policy is None:
                return
            if (
                self.store.db.execute("SELECT count(*) FROM maintenance_recovery_refusals").fetchone()[0]
                >= 128
            ):
                raise Invalid("refusal-capacity")
            provenance["policy_sha256"] = policy
            self.store.db.execute(
                "INSERT INTO maintenance_recovery_refusals VALUES(?,?,?,?,?,?)",
                (account, group, mid, payload, canonical(provenance), reason),
            )
            post("Maintenance recovery refused: " + reason + ".")

    def permit(self, req: Permit, *, uid: int | None, authority: Callable[[str], str | None]) -> Reply:
        if uid != os.getuid():
            raise Invalid("peer")
        enrolled = self.enrollment()
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT descriptor,state,decision,operator,policy,generation,permit,"
                "created_at FROM maintenance_recovery_proposals WHERE hash=?",
                (req.proposal_sha256,),
            ).fetchone()
            if row is None or row[1] != "consented":
                raise Invalid("not-consented")
            obj = descriptor(bytes(row[0]))
            self.live(obj, row[7])
            current_policy = authority(row[3])
            if current_policy is None:
                raise Invalid("authority")
            if current_policy != row[4]:
                raise Invalid("policy-drift")
            generation = row[5]
            previous = None if row[6] is None else bytes(row[6])
            if previous is not None and req.certificate_sha256 is None:
                self.publish_permit(req.proposal_sha256, previous)
                return Reply(
                    "issued", "existing generation; never blindly reissue", self.get(req.proposal_sha256)
                )
            if previous is None and req.certificate_sha256 is not None:
                raise Invalid("unexpected-certificate")
            if previous is not None:
                if generation >= 3:
                    raise Invalid("generation-cap")
                old = decode(previous, dict[str, object])
                if old["boot_id"] != self.boot():
                    raise Invalid("boot-changed")
                self.certificate(obj, req, old, enrolled)
            if authority(row[3]) != current_policy:
                raise Invalid("authority-drift")
            boot = self.boot()
            try:
                if str(uuid.UUID(boot)) != boot:
                    raise Invalid("boot")
            except ValueError:
                raise Invalid("boot") from None
            issued = self.boottime()
            if type(issued) is not int or issued < 0:
                raise Invalid("clock")
            value = dict(
                schema="maintenance-recovery-permit.v1",
                permit_id=str(uuid.uuid4()),
                proposal_sha256=req.proposal_sha256,
                decision_message_id=row[2],
                operator=row[3],
                policy_sha256=current_policy,
                boot_id=boot,
                issued_boottime_ns=issued,
                initiate_before_boottime_ns=issued + 30_000_000_000,
                state="issued",
                generation=generation + 1,
                previous_generation_sha256=None if previous is None else hashlib.sha256(previous).hexdigest(),
            )
            encoded = canonical(value)
            self.store.db.execute(
                "UPDATE maintenance_recovery_proposals SET generation=?,permit=? WHERE hash=?",
                (generation + 1, encoded, req.proposal_sha256),
            )
        # DB commit precedes publication. A crash leaves exactly this candidate for idempotent repair.
        self.publish_permit(req.proposal_sha256, encoded)
        return Reply("issued", "single-use enrolled publication", self.get(req.proposal_sha256))

    def certificate(self, obj: Descriptor, req: Permit, old: dict[str, object], enrolled: Enrollment) -> None:
        generation = old["generation"]
        folder = Path(enrolled.coordinator_root) / "recovery-v1"
        raw = private_read(folder / f"{req.proposal_sha256}.nonconsumption-{generation}.json")
        if (
            hashlib.sha256(raw).hexdigest() != req.certificate_sha256
            or private_read(folder / f"{req.proposal_sha256}.superseded-{generation}.json") != raw
        ):
            raise Invalid("certificate-publication")
        value = decode(raw, dict[str, object])
        expected = dict(
            schema="maintenance-recovery-nonconsumption.v1",
            proposal_sha256=req.proposal_sha256,
            permit_sha256=hashlib.sha256(canonical(old)).hexdigest(),
            generation=generation,
            operation=obj.operation,
            original_record_sha256=obj.original_record_sha256,
            effects_root=obj.effects_root,
            enrollment_sha256=obj.enrollment_sha256,
            boot_id=old["boot_id"],
            observed_boottime_ns=value.get("observed_boottime_ns"),
        )
        observed = value.get("observed_boottime_ns")
        old_issued = old.get("issued_boottime_ns")
        if (
            value != expected
            or not isinstance(observed, int)
            or isinstance(observed, bool)
            or not isinstance(old_issued, int)
            or observed < old_issued
            or observed > self.boottime()
        ):
            raise Invalid("certificate")
        for suffix in ("consumed", "issuing", "receipt"):
            if os.path.lexists(folder / f"{req.proposal_sha256}.{suffix}.json"):
                raise Invalid("consumption-unknown")

    def publish_permit(self, key: str, data: bytes) -> None:
        value = decode(data, dict[str, object])
        publish(self.root / "generations" / key / f"{value['generation']}.json", data)
        replace_current(self.root / "permits" / f"{key}.json", data)


def boot_id() -> str:
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if str(uuid.UUID(value)) != value:
        raise Invalid("boot")
    return value


def boot_ns() -> int:
    import time

    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


def render(obj: Descriptor, actor: str, link: str | None, budget: int) -> str:
    """Never shorten material scope; literal hashes are private unless a safe public link exists."""
    token = sha(obj)[:24]
    records = ", ".join(f"{p.action} {p.selector.type}:{p.selector.id}" for p in obj.local_records) or "none"
    consequences = (
        "Diagnostic may already be delivered; never resend it. Other native effects stay held."
        if obj.mode == "abandon-notice"
        else "No native replay or task/approval changes."
    )
    lines = [
        "Maintenance recovery consent",
        f"Operation: {obj.operation}",
        f"Original actor: {actor}",
        f"Result: {obj.result}",
        f"Local records: {records}",
        f"Proof-covered effects: {obj.effect_count}",
        "Remaining native unknowns: never cleared by this consent.",
        consequences,
        f"Expires: {obj.expires_at}",
        f"Reply: !maintenance settle {token}",
        f"Revoke: !maintenance revoke {token}",
        "An already issued permit may start recovery for 30 seconds after revoke.",
    ]
    if link is not None:
        if not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/[0-9a-f]{40}/[A-Za-z0-9_./-]+", link
        ):
            raise Invalid("public-link")
        lines.insert(5, "Exact proposal/source/proofs: " + link)
    else:
        lines.insert(5, "Source closure: " + obj.source_closure)
        lines.insert(6, "Exact proposal: " + sha(obj))
    text = "\n".join(lines)
    if len(text) > budget or len(lines) > 36 or redact(text) != text:
        raise Invalid("notice-material-unrepresentable")
    return text


def replace_current(path: Path, data: bytes) -> None:
    """Atomic current pointer; immutable generation was fsynced before this call."""
    private_parents(path.parent)
    directory = path.parent.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.getuid()
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise Invalid("unsafe-directory")
    if path.exists() or path.is_symlink():
        private_read(path)
    temporary = path.parent / (".permit-" + uuid.uuid4().hex)
    publish(temporary, data)
    try:
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def source_hash(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16_777_216:
            raise Invalid("source-bound")
        value = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            remaining = 16_777_217
            while remaining:
                block = stream.read(min(65_536, remaining))
                if not block:
                    break
                remaining -= len(block)
                value.update(block)
            if remaining == 0:
                raise Invalid("source-bound")
        return value.hexdigest()
    finally:
        os.close(fd)
