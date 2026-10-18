"""Private acceptance-only immutable objects, event chain and fenced terminal commit.

No native/product locks or APIs are used. This is an offline evidence publisher;
source acquisition, audit completeness and authenticated scope need admitted ports.
"""

import contextlib
import fcntl
import os
import stat
import uuid
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Literal

import msgspec

from . import codec
from . import models as m
from .evaluator import DURATION_NS, Evaluator, Hold, Inventory, ProofRequest, Proofs, Unavailable, require

MAX_OBJECTS = 100_000
MAX_SPOOL = 1024
MAX_PREPARED = 1024


class Binding(m.Record, frozen=True):
    artifact: m.Artifact


class Fence(m.Record, frozen=True):
    schema: Literal["acceptance-fence.v1"]
    segment_id: m.ID
    attempt_id: m.ID
    before_head: m.Digest | None
    before_seq: m.Count
    heartbeat_sha256: m.Digest | None


class Manifest(m.Record, frozen=True):
    schema: Literal["acceptance-terminal.v1"]
    intent_sha256: m.Digest
    fence_sha256: m.Digest
    objects: tuple[m.Digest, ...]
    events: tuple[m.Digest, ...]
    spool_ids: tuple[m.Digest, ...]


class Release(m.Record, frozen=True):
    schema: Literal["acceptance-release.v1"]
    fence_sha256: m.Digest
    spool_ids: tuple[m.Digest, ...]
    events: tuple[m.Digest, ...]


class ReleaseProgram(m.Record, frozen=True):
    release: Release
    before_seq: m.Count


class Abort(m.Record, frozen=True):
    schema: Literal["acceptance-abort.v1"]
    fence_sha256: m.Digest
    intent_sha256: m.Digest | None
    reason: m.ID


class Ledger:
    """All namespace operations serialize on one retained publisher lock inode."""

    def __init__(self, root: Path, proofs: Proofs | None = None):
        self.root = root
        self.proofs: Proofs = proofs if proofs is not None else Unavailable()
        self._directory(root)
        self._directory(root / "objects")
        self._directory(root / "events")
        self._directory(root / "spool")
        self._directory(root / "inventory")
        self._directory(root / "spool-retired")
        self.entry_raw = self._read(root / "entry.json")
        self.entry = codec.decode(self.entry_raw, m.Entry)
        self.entry_digest = codec.digest(self.entry_raw)

    @classmethod
    def create(cls, root: Path, entry_raw: bytes, proofs: Proofs | None = None) -> "Ledger":
        codec.decode(entry_raw, m.Entry)
        require(not root.exists() and root.parent.is_dir(), "ledger_exists_or_parent_missing")
        root.mkdir(mode=0o700)
        for name in ("objects", "events", "spool", "inventory", "spool-retired"):
            (root / name).mkdir(mode=0o700)
        cls._atomic(root / "entry.json", entry_raw)
        cls._atomic(root / "publisher.lock", b"")
        cls._atomic(root / "spool.lock", b"")
        return cls(root, proofs)

    @staticmethod
    def _directory(path: Path) -> None:
        try:
            st = path.lstat()
        except OSError as exc:
            raise Hold("ledger_directory_unavailable") from exc
        require(
            stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700,
            "unsafe_ledger_directory",
        )

    @staticmethod
    def _read(path: Path) -> bytes:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise Hold("ledger_file_unavailable") from exc
        try:
            st = os.fstat(fd)
            require(
                stat.S_ISREG(st.st_mode)
                and st.st_uid == os.getuid()
                and st.st_nlink == 1
                and stat.S_IMODE(st.st_mode) == 0o600
                and st.st_size <= codec.MAX_BYTES,
                "unsafe_ledger_file",
            )
            raw = b""
            while len(raw) <= codec.MAX_BYTES:
                block = os.read(fd, min(65536, codec.MAX_BYTES + 1 - len(raw)))
                if not block:
                    return raw
                raw += block
            raise Hold("ledger_file_bound")
        except OSError as exc:
            raise Hold("ledger_file_unavailable") from exc
        finally:
            os.close(fd)

    @staticmethod
    def _sync(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _atomic(path: Path, raw: bytes) -> None:
        require(len(raw) <= codec.MAX_BYTES, "ledger_file_bound")
        if path.exists() or path.is_symlink():
            require(Ledger._read(path) == raw, "immutable_file_conflict")
            return
        namespace = path.parent
        if (
            namespace.name in ("objects", "events", "spool", "inventory")
            and (namespace.parent / "entry.json").is_file()
        ):
            prepared = namespace.parent / (".prepared-" + namespace.name)
        else:
            prepared = namespace / ".prepared"
        prepared.mkdir(mode=0o700, exist_ok=True)
        Ledger._directory(prepared)
        require(sum(1 for _ in prepared.iterdir()) < MAX_PREPARED, "prepared_capacity")
        temporary = prepared / uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        # Failed prepared bytes remain retained for diagnosis.
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        Ledger._sync(prepared)
        # The namespace is private and publisher-locked. Never overwrite an existing record.
        require(not path.exists() and not path.is_symlink(), "immutable_file_conflict")
        temporary.rename(path)
        Ledger._sync(path.parent)
        Ledger._sync(prepared)

    @contextlib.contextmanager
    def _lock(self, *, spooling: bool = False) -> Generator[None]:
        self._directory(self.root)
        path = self.root / ("spool.lock" if spooling else "publisher.lock")
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            require(
                stat.S_ISREG(st.st_mode)
                and st.st_uid == os.getuid()
                and st.st_nlink == 1
                and stat.S_IMODE(st.st_mode) == 0o600,
                "unsafe_publisher_lock",
            )
            fcntl.flock(fd, fcntl.LOCK_EX)
            require(path.stat(follow_symlinks=False).st_ino == st.st_ino, "publisher_lock_changed")
            yield
        finally:
            os.close(fd)

    def _object(self, raw: bytes) -> str:
        digest = codec.digest(raw)
        directory = self.root / "objects"
        require(
            (directory / digest).exists() or len(self._record_names(directory)) < MAX_OBJECTS,
            "object_capacity",
        )
        self._atomic(directory / digest, raw)
        return digest

    def _get(self, digest: str) -> bytes:
        codec.decode(codec.canonical(digest), m.Digest)
        raw = self._read(self.root / "objects" / digest)
        require(codec.digest(raw) == digest, "object_hash_mismatch")
        return raw

    def _events(self) -> tuple[bytes, ...]:
        names = self._record_names(self.root / "events")
        require(len(names) <= 2147483647, "event_capacity")
        result: list[bytes] = []
        head = None
        for seq, name in enumerate(names):
            require(name == f"{seq:010d}.json", "unexpected_event_tail")
            raw = self._read(self.root / "events" / name)
            event = codec.decode(raw, m.Event)
            require(
                event.seq == seq and event.previous_sha256 == head and event.run_id == self.entry.run_id,
                "event_chain",
            )
            result.append(raw)
            head = codec.digest(raw)
        return tuple(result)

    def events(self) -> tuple[bytes, ...]:
        with self._lock():
            return self._events()

    def _append(self, raw: bytes) -> None:
        event = codec.decode(raw, m.Event)
        events = self._events()
        if event.seq < len(events):
            require(events[event.seq] == raw, "event_recovery_conflict")
            return
        require(
            event.seq == len(events)
            and event.run_id == self.entry.run_id
            and event.previous_sha256 == (codec.digest(events[-1]) if events else None),
            "event_chain",
        )
        require(event.seq < 2147483647, "event_capacity")
        self._atomic(self.root / "events" / f"{event.seq:010d}.json", raw)

    def append(self, raw: bytes) -> None:
        with self._lock():
            require(
                not ((self.root / "fence.json").exists() or (self.root / "fence.json").is_symlink()),
                "terminal_fenced",
            )
            self._append(raw)

    def spool(self, raw: bytes) -> str:
        """Retain exact source envelopes. No automatic conversion or loss of cursor/time."""
        codec.load(raw)
        with self._lock(spooling=True):
            directory = self.root / "spool"
            digest = codec.digest(raw)
            path = directory / digest
            require(path.exists() or len(self._spool_ids()) < MAX_SPOOL, "spool_overflow")
            require(
                path.exists() or len(self._record_names(directory)) < MAX_OBJECTS, "spool_retention_capacity"
            )
            self._atomic(path, raw)
            return digest

    def begin(self, segment_id: str) -> str:
        """Durably fence FIRST; only then may a collector sample the final endpoint.

        This method samples no clock and acquires no source data. Production
        terminal proof must establish the subsequent capture/cursor ordering.
        """
        with self._lock():
            require(
                not ((self.root / "fence.json").exists() or (self.root / "fence.json").is_symlink()),
                "terminal_fenced",
            )
            codec.decode(codec.canonical(segment_id), m.ID)
            require(
                not (self.root / ("terminal-" + codec.digest(segment_id.encode()) + ".json")).exists(),
                "segment_already_terminal",
            )
            events = self._events()
            fence = Fence(
                schema="acceptance-fence.v1",
                segment_id=segment_id,
                attempt_id=uuid.uuid4().hex,
                before_head=codec.digest(events[-1]) if events else None,
                before_seq=len(events),
                heartbeat_sha256=None,
            )
            raw = codec.canonical(codec.validate(fence))
            self._atomic(self.root / "fence.json", raw)
            retry = self.root / "release-retry.json"
            if retry.exists():
                retry.unlink()
                self._sync(self.root)
            return codec.digest(raw)

    def heartbeat(self, heartbeat_raw: bytes) -> None:
        """Attach the exact post-fence capture without replacing the fence bytes."""
        heartbeat = codec.decode(heartbeat_raw, m.Event)
        with self._lock():
            raw = self._active_fence()
            fence = codec.decode(raw, Fence)
            require(
                heartbeat.event == "heartbeat"
                and heartbeat.checkpoint is not None
                and heartbeat.segment_id == fence.segment_id,
                "terminal_heartbeat",
            )
            require(
                heartbeat.seq == fence.before_seq and heartbeat.previous_sha256 == fence.before_head,
                "terminal_heartbeat_chain",
            )
            self._object(heartbeat_raw)
            self._atomic(self.root / ("heartbeat-" + codec.digest(raw) + ".json"), heartbeat_raw)
            self._append(heartbeat_raw)

    def _fence(self) -> tuple[bytes, Fence]:
        raw = self._active_fence()
        fence = codec.decode(raw, Fence)
        heartbeat_path = self.root / ("heartbeat-" + codec.digest(raw) + ".json")
        if heartbeat_path.exists():
            heartbeat = self._read(heartbeat_path)
            self._append(heartbeat)
            fence = msgspec.structs.replace(fence, heartbeat_sha256=codec.digest(heartbeat))
        return raw, fence

    def _active_fence(self) -> bytes:
        require((self.root / "fence.json").exists(), "no_active_fence")
        return self._read(self.root / "fence.json")

    @staticmethod
    def _record_names(directory: Path) -> tuple[str, ...]:
        # Legacy failed prepares and the separately bounded quarantine are not records.
        try:
            return tuple(
                sorted(
                    p.name
                    for p in directory.iterdir()
                    if p.name != ".prepared" and not p.name.startswith(".prepare-")
                )
            )
        except OSError as exc:
            raise Hold("ledger_directory_unavailable") from exc

    def _spool_ids(self) -> tuple[str, ...]:
        originals = self._record_names(self.root / "spool")
        original_ids = set(originals)
        retired: set[str] = set()
        receipts: dict[str, Release] = {}
        for name in self._record_names(self.root / "spool-retired"):
            codec.decode(codec.canonical(name), m.Digest)
            digest = codec.decode(self._read(self.root / "spool-retired" / name), m.Digest)
            if digest not in receipts:
                receipt = codec.decode(self._read(self.root / ("released-" + digest + ".json")), Release)
                require(receipt.fence_sha256 == digest, "spool_retirement_receipt")
                receipts[digest] = receipt
            require(name in original_ids and name in receipts[digest].spool_ids, "spool_retirement_receipt")
            retired.add(name)
        ids = tuple(x for x in originals if x not in retired)
        require(len(ids) <= MAX_SPOOL, "spool_overflow")
        for digest in ids:
            codec.decode(codec.canonical(digest), m.Digest)
            require(codec.digest(self._read(self.root / "spool" / digest)) == digest, "spool_hash_mismatch")
        return ids

    def _heartbeat(self, fence: Fence) -> bytes:
        if fence.heartbeat_sha256 is None:
            raise Hold("terminal_heartbeat_missing")
        return self._get(fence.heartbeat_sha256)

    def retain(self, artifact: m.Artifact, raw: bytes) -> None:
        """Copy exact originals and immutable ID mapping; no implicit source discovery."""
        codec.validate(artifact)
        require(codec.digest(raw) == artifact.sha256, "artifact_missing_or_changed")
        with self._lock():
            self._object(raw)
            self._atomic(
                self.root / "inventory" / codec.digest(artifact.id.encode()),
                codec.canonical(Binding(artifact=artifact)),
            )

    def _inventory(self) -> Inventory:
        values: dict[str, bytes] = {}
        for name in self._record_names(self.root / "inventory"):
            path = self.root / "inventory" / name
            binding = codec.decode(self._read(path), Binding)
            require(path.name == codec.digest(binding.artifact.id.encode()), "inventory_binding")
            values[binding.artifact.id] = self._get(binding.artifact.sha256)
        return Inventory(values)

    def _proof(self, role: Literal["terminal_fence"], value: object) -> None:
        inventory = self._inventory()
        inventory.references(value)
        self.proofs.verify(ProofRequest(role, self.entry_digest, codec.canonical(value)), inventory)

    def _validate_events(self, raws: Sequence[bytes]) -> None:
        events = Evaluator(self._inventory(), self.proofs).validate_events(
            self.entry, self.entry_digest, raws
        )
        for previous, current in zip(events, events[1:], strict=False):
            require(
                current.boot_id != previous.boot_id
                or int(current.monotonic_ns) >= int(previous.monotonic_ns),
                "event_monotonic",
            )

    def commit(
        self,
        intent: m.SealIntent,
        segment: m.Segment,
        assessment: m.Assessment,
        terminal_events: Sequence[bytes],
    ) -> str:
        """Commit exact prepared bundle only after the source-fence adapter proves it.

        The immutable terminal manifest is the commit point. Recovery completes
        those bytes only; it cannot select a new endpoint or infer a live success.
        """
        for value in (intent, segment, assessment):
            codec.validate(value)
        with self._lock():
            fence_raw, fence = self._fence()
            require(
                not (self.root / ("abort-" + codec.digest(fence_raw) + ".json")).exists(), "aborted_fence"
            )
            heartbeat = codec.decode(self._heartbeat(fence), m.Event)
            require(
                intent.segment_id == segment.segment_id == fence.segment_id
                and intent.entry_sha256 == segment.entry_sha256 == self.entry_digest,
                "terminal_binding",
            )
            require(
                intent.checkpoint == heartbeat.checkpoint
                and intent.checkpoint_event_sha256 == fence.heartbeat_sha256,
                "terminal_checkpoint",
            )
            require(intent.result == segment.status and intent.reason == segment.reason, "terminal_result")
            require(
                segment.run_id == assessment.run_id == self.entry.run_id
                and segment.start_event_sha256 == intent.start_event_sha256,
                "terminal_run",
            )
            inventory = self._inventory()
            starts = [
                codec.decode(raw, m.Event)
                for raw in self._events()
                if codec.digest(raw) == intent.start_event_sha256
            ]
            require(
                len(starts) == 1
                and starts[0].event == "segment_start"
                and starts[0].segment_id == intent.segment_id
                and starts[0].checkpoint is not None,
                "terminal_start",
            )
            start_ref = starts[0].checkpoint
            if start_ref is None:
                raise Hold("terminal_start")
            first = codec.decode(inventory.read(start_ref), m.Checkpoint)
            last = codec.decode(inventory.read(intent.checkpoint), m.Checkpoint)
            elapsed = int(last.monotonic_ns) - int(first.monotonic_ns)
            require(
                first.boot_id == last.boot_id
                and elapsed >= 0
                and first.entry_sha256 == last.entry_sha256 == self.entry_digest
                and segment.start == first.at
                and segment.end == last.at
                and segment.duration_ns == str(elapsed),
                "terminal_endpoint",
            )
            if intent.result == "sealed":
                require(elapsed >= DURATION_NS, "duration_short")
            expected_count = 2 if intent.result == "sealed" else 1
            require(len(terminal_events) == expected_count, "terminal_events")
            stop = codec.decode(terminal_events[0], m.Event)
            expected = m.Stop(
                reason=intent.reason,
                checkpoint=intent.checkpoint,
                checkpoint_event_sha256=intent.checkpoint_event_sha256,
                transaction_id=intent.transaction_id,
            )
            require(
                stop.event == "segment_stop"
                and stop.stop == expected
                and stop.checkpoint is None
                and stop.segment_id == intent.segment_id
                and stop.seq == fence.before_seq + 1
                and stop.previous_sha256 == fence.heartbeat_sha256,
                "terminal_stop",
            )
            require(
                segment.end_event_sha256 == codec.digest(terminal_events[0])
                and assessment.segment_sha256 == codec.digest(codec.canonical(segment))
                and assessment.evaluation == intent.evaluation
                and assessment.archive_receipt is None
                and assessment.current_release is None,
                "terminal_artifact_graph",
            )
            if intent.result == "sealed":
                require(
                    intent.reason == "none"
                    and assessment.result == "qualified_pending_archive"
                    and not assessment.unresolved,
                    "terminal_qualification",
                )
                evaluated = Evaluator(inventory, self.proofs).interval(
                    self.entry_raw,
                    self._events(),
                    segment.segment_id,
                    assessment.coverage,
                    assessment.supplemental_corpus,
                    assessment.unresolved,
                )
                require(evaluated.result == "qualified_pending_archive", evaluated.reason)
                require(evaluated.duration_ns == segment.duration_ns, "terminal_evaluated_duration")
                qualification = codec.decode(terminal_events[1], m.Event)
                require(
                    qualification.event == "qualification"
                    and qualification.stop is None
                    and qualification.checkpoint is None
                    and qualification.segment_id == intent.segment_id
                    and qualification.seq == stop.seq + 1
                    and qualification.previous_sha256 == codec.digest(terminal_events[0]),
                    "terminal_qualification_event",
                )
            else:
                require(
                    intent.reason != "none" and assessment.result == "not_qualified", "terminal_interrupt"
                )
            require(
                all(
                    codec.decode(raw, m.Event).boot_id == last.boot_id
                    and codec.decode(raw, m.Event).monotonic_ns == last.monotonic_ns
                    for raw in terminal_events
                ),
                "terminal_clock",
            )
            self._validate_events((*self._events(), *terminal_events))
            self._atomic(self.root / ("intent-" + codec.digest(fence_raw) + ".json"), codec.canonical(intent))
            with self._lock(spooling=True):
                spool_ids = self._spool_ids()
                self._proof(
                    "terminal_fence",
                    (
                        fence,
                        intent,
                        segment,
                        assessment,
                        tuple(terminal_events),
                        spool_ids,
                    ),
                )
                manifest = Manifest(
                    schema="acceptance-terminal.v1",
                    intent_sha256=self._object(codec.canonical(intent)),
                    fence_sha256=codec.digest(fence_raw),
                    objects=tuple(self._object(codec.canonical(v)) for v in (segment, assessment)),
                    events=tuple(self._object(raw) for raw in terminal_events),
                    spool_ids=spool_ids,
                )
                self._object(codec.canonical(manifest))
                target = self.root / ("terminal-" + codec.digest(intent.segment_id.encode()) + ".json")
                self._atomic(target, codec.canonical(manifest))
                self._complete(manifest, fence)
                return codec.digest(codec.canonical(manifest))

    def _complete(self, manifest: Manifest, fence: Fence) -> None:
        require(manifest.fence_sha256 == codec.digest(self._read(self.root / "fence.json")), "fence_changed")
        require(len(manifest.objects) == 2 and len(manifest.events) in (1, 2), "manifest_shape")
        self._get(codec.digest(codec.canonical(manifest)))
        for digest in (manifest.intent_sha256, *manifest.objects):
            self._get(digest)
        inventory = self._inventory()
        inventory.references(codec.decode(self._get(manifest.intent_sha256), m.SealIntent))
        inventory.references(codec.decode(self._get(manifest.objects[0]), m.Segment))
        inventory.references(codec.decode(self._get(manifest.objects[1]), m.Assessment))
        require(len(self._events()) <= fence.before_seq + 1 + len(manifest.events), "unexpected_event_tail")
        for digest in manifest.events:
            self._append(self._get(digest))
        self._atomic(self.root / ("complete-" + manifest.fence_sha256 + ".json"), codec.canonical(manifest))
        # The fence remains until source spool reconciliation is explicitly proven.

    def recover(self) -> str:
        with self._lock():
            if not ((self.root / "fence.json").exists() or (self.root / "fence.json").is_symlink()):
                self._events()
                return "unfenced"
            raw, fence = self._fence()
            path = self.root / ("terminal-" + codec.digest(fence.segment_id.encode()) + ".json")
            if not path.exists() and not path.is_symlink():
                require(
                    not (self.root / ("complete-" + codec.digest(raw) + ".json")).exists(),
                    "terminal_manifest_missing",
                )
                return "prepared_fence"
            manifest = codec.decode(self._read(path), Manifest)
            require(manifest.fence_sha256 == codec.digest(raw), "manifest_fence")
            self._complete(manifest, fence)
            return "committed_complete_fenced"

    def abort(self, reason: str, intent: m.SealIntent | None = None) -> str:
        codec.decode(codec.canonical(reason), m.ID)
        with self._lock():
            raw, fence = self._fence()
            require(
                not (self.root / ("terminal-" + codec.digest(fence.segment_id.encode()) + ".json")).exists(),
                "committed_cannot_abort",
            )
            intent_path = self.root / ("intent-" + codec.digest(raw) + ".json")
            retained = self._read(intent_path) if intent_path.exists() else None
            if retained is not None:
                previous = codec.decode(retained, m.SealIntent)
                require(
                    intent is None or codec.canonical(codec.validate(intent)) == retained,
                    "prepared_intent_conflict",
                )
                intent = previous
            if intent is not None:
                codec.validate(intent)
                heartbeat = codec.decode(self._heartbeat(fence), m.Event)
                require(
                    intent.segment_id == fence.segment_id and intent.entry_sha256 == self.entry_digest,
                    "terminal_binding",
                )
                require(
                    intent.checkpoint == heartbeat.checkpoint
                    and intent.checkpoint_event_sha256 == fence.heartbeat_sha256,
                    "terminal_checkpoint",
                )
                events = self._events()
                self._validate_events(events)
                starts = [
                    codec.decode(item, m.Event)
                    for item in events
                    if codec.digest(item) == intent.start_event_sha256
                ]
                require(
                    len(starts) == 1
                    and starts[0].event == "segment_start"
                    and starts[0].segment_id == fence.segment_id,
                    "terminal_start",
                )
                retained = codec.canonical(intent)
            abort = codec.validate(
                Abort(
                    schema="acceptance-abort.v1",
                    fence_sha256=codec.digest(raw),
                    intent_sha256=codec.digest(retained) if retained is not None else None,
                    reason=reason,
                )
            )
            self._proof("terminal_fence", (abort, intent))
            if retained is not None:
                self._object(retained)
                self._atomic(intent_path, retained)
            self._atomic(self.root / ("abort-" + abort.fence_sha256 + ".json"), codec.canonical(abort))
            return codec.digest(codec.canonical(abort))

    def release(self, reconciled_events: Sequence[bytes]) -> None:
        """Retire only the proven snapshot; immutable originals and late arrivals remain."""
        requested = tuple(reconciled_events)
        with self._lock():
            active = (self.root / "fence.json").exists() or (self.root / "fence.json").is_symlink()
            raw = b""
            if active:
                raw, _ = self._fence()
                digest = codec.digest(raw)
                require(
                    (self.root / ("complete-" + digest + ".json")).exists()
                    or (self.root / ("abort-" + digest + ".json")).exists(),
                    "fence_not_terminal",
                )
                program_path = self.root / ("release-program-" + digest + ".json")
            else:
                require((self.root / "release-retry.json").exists(), "no_active_fence")
                digest = codec.decode(self._read(self.root / "release-retry.json"), m.Digest)
                program_path = self.root / ("release-program-" + digest + ".json")
            with self._lock(spooling=True):
                proven = False
                if program_path.exists():
                    program = codec.decode(self._read(program_path), ReleaseProgram)
                    require(
                        program.release.events == tuple(codec.digest(x) for x in requested),
                        "release_program_conflict",
                    )
                else:
                    require(active, "no_active_fence")
                    for item in requested:
                        require(codec.decode(item, m.Event).segment_id is None, "spooled_segment")
                    before = self._events()
                    self._validate_events((*before, *requested))
                    spool_ids = self._spool_ids()
                    self._proof("terminal_fence", ("release", digest, requested, spool_ids))
                    proven = True
                    program = ReleaseProgram(
                        release=Release(
                            schema="acceptance-release.v1",
                            fence_sha256=digest,
                            spool_ids=spool_ids,
                            events=tuple(self._object(x) for x in requested),
                        ),
                        before_seq=len(before),
                    )
                    self._atomic(program_path, codec.canonical(program))
                release = program.release
                digest = release.fence_sha256
                require(not active or codec.digest(raw) == digest, "release_fence_changed")
                candidate = tuple(self._get(x) for x in release.events)
                require(candidate == requested, "release_program_conflict")
                current = self._events()
                end = program.before_seq + len(candidate)
                require(program.before_seq <= len(current) <= end, "release_event_tail")
                self._validate_events((*current[: program.before_seq], *candidate))
                receipt = self.root / ("released-" + digest + ".json")
                if not receipt.exists():
                    if not proven:
                        self._proof("terminal_fence", ("release", digest, candidate, release.spool_ids))
                    for item in candidate:
                        self._append(item)
                    self._atomic(receipt, codec.canonical(release))
                else:
                    require(self._read(receipt) == codec.canonical(release), "release_receipt_conflict")
                    require(current == (*current[: program.before_seq], *candidate), "release_event_tail")
                self._atomic(self.root / "release-retry.json", codec.canonical(digest))
                for spool_id in release.spool_ids:
                    require(
                        codec.digest(self._read(self.root / "spool" / spool_id)) == spool_id,
                        "spool_hash_mismatch",
                    )
                    self._atomic(self.root / "spool-retired" / spool_id, codec.canonical(digest))
                if active:
                    (self.root / "fence.json").unlink()
                    self._sync(self.root)
