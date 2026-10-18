"""Observation-only durable episodes. No task, effect journal, relay receipt or authority mutation."""

import hashlib
import os
import stat
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import msgspec

from heterodyne.agents.base import write_private
from heterodyne.local_alerts import Intent, canonical, locked, publish, settle
from heterodyne.wsd.health import CODES, HealthSnapshot, Truth, predicates, read
from heterodyne.wsd.maintenance_window import Window, label


class Episode(msgspec.Struct, forbid_unknown_fields=True):
    sequence: int = 0
    opening_generation: str = ""
    phase: str = "closed"
    failure: Intent | None = None
    notice: Intent | None = None


class ObserverState(msgspec.Struct, forbid_unknown_fields=True):
    version: int
    installation: str
    epoch: str
    episodes: dict[str, Episode]
    reset: Intent | None
    prior_process: str | None = None
    prior_sequence: int | None = None
    sequence_seen_at: float | None = None
    observer_boot: str = ""
    maintenance_window: Window | None = None
    maintenance_label: str = "inactive"
    window_history: dict[str, Window] = msgspec.field(default_factory=dict[str, Window])


def timestamp(utc: float) -> str:
    return datetime.fromtimestamp(utc, UTC).isoformat().replace("+00:00", "Z")


def episode_id(installation: str, generation: str, code: str, sequence: int, phase: str) -> str:
    return hashlib.sha256(canonical([1, installation, generation, code, sequence, phase])).hexdigest()


class Observer:
    def __init__(
        self,
        state_dir: Path,
        alert_dir: Path,
        installation: str,
        generation: str,
        window: Window | None = None,
    ) -> None:
        self.directory = state_dir
        self.alert_dir = alert_dir
        self.installation = installation
        self.generation = generation
        self.window = window
        self.path = state_dir / "observer-state.json"

    def _save(self, state: ObserverState) -> None:
        write_private(self.path, msgspec.json.encode(state).decode())

    def _load(self, utc: float) -> ObserverState:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise ValueError("observer state is not private")
                payload = os.read(fd, 65537)
            finally:
                os.close(fd)
            state = msgspec.json.decode(payload, type=ObserverState)
            if (
                len(payload) > 65536
                or state.version != 1
                or state.installation != self.installation
                or len(state.epoch) != 32
                or len(state.window_history) > 64
                or set(state.episodes) != set(CODES)
                or any(
                    not 0 <= e.sequence <= (1 << 64) - 1 or e.phase not in ("open", "closed")
                    for e in state.episodes.values()
                )
            ):
                raise ValueError("observer identity")
            return state
        except (OSError, ValueError, msgspec.DecodeError):
            if self.path.exists() or self.path.is_symlink():
                self.path.replace(self.directory / f"observer-state.corrupt-{uuid.uuid4().hex}")
            epoch = uuid.uuid4().hex
            identity = hashlib.sha256(
                canonical([1, self.installation, epoch, "observer_state_reset", 1, "retirement"])
            ).hexdigest()
            reset = Intent(
                "p8",
                epoch,
                1,
                identity,
                "retirement",
                timestamp(utc),
                "Observation state reset. Earlier diagnostic conditions are unknown; effect state unchanged.",
            )
            state = ObserverState(1, self.installation, epoch, {code: Episode() for code in CODES}, reset)
            self._save(state)  # exact reset intent survives a crash before first publication
            return state

    def _flush(self, state: ObserverState, intent: Intent) -> bool:
        result = publish(self.alert_dir, intent)
        if result not in ("written", "existing"):
            return False
        if intent.kind in ("recovery", "retirement"):
            return settle(self.alert_dir, intent, hashlib.sha256(intent.payload()).hexdigest(), "published")
        return True

    def inspect(self, utc: float, monotonic: float, boot: str) -> dict[str, Truth]:
        # The observation-store lock is distinct from the shared alert-directory lock.
        with locked(self.directory):
            state = self._load(utc)
            prior = state.window_history.get(self.window.authority_id) if self.window else None
            state.maintenance_label = label(self.window, utc, prior)
            if self.window is not None and state.maintenance_label != "unknown":
                if self.window.authority_id not in state.window_history and len(state.window_history) >= 64:
                    state.maintenance_label = "unknown"
                else:
                    state.window_history[self.window.authority_id] = self.window
                    state.maintenance_window = self.window
            self._save(state)  # restart cannot shift times for the retained authority decision
            if state.reset is not None and self._flush(state, state.reset):
                state.reset = None
                self._save(state)
            snapshot: HealthSnapshot | None = None
            try:
                snapshot = read(self.directory / "health.json", self.installation, self.generation)
                if snapshot.boot_id != boot:
                    snapshot = None
            except (OSError, ValueError, msgspec.DecodeError):
                pass
            if state.observer_boot != boot:
                state.prior_process = None
                state.prior_sequence = None
                state.sequence_seen_at = None
                state.observer_boot = boot
            values = predicates(
                snapshot,
                utc=utc,
                monotonic=monotonic,
                prior_process=state.prior_process,
                prior_sequence=state.prior_sequence,
                sequence_seen_at=state.sequence_seen_at,
            )
            if snapshot is not None and (
                snapshot.process_id != state.prior_process or snapshot.sequence != state.prior_sequence
            ):
                state.prior_process = snapshot.process_id
                state.prior_sequence = snapshot.sequence
                state.sequence_seen_at = monotonic
            for code, value in values.items():
                episode = state.episodes[code]
                if episode.notice is not None and self._flush(state, episode.notice):
                    episode.notice = None
                    self._save(state)
                if value == "true" and episode.phase == "closed":
                    if episode.sequence >= (1 << 64) - 1:
                        continue
                    episode.sequence += 1
                    episode.opening_generation = self.generation
                    episode.phase = "open"
                    episode.failure = Intent(
                        "p8",
                        state.epoch,
                        1,
                        episode_id(self.installation, self.generation, code, episode.sequence, "failure"),
                        "failure",
                        timestamp(utc),
                        f"Workstream daemon diagnostic: {code}.",
                    )
                    episode.notice = episode.failure
                    self._save(state)
                    if self._flush(state, episode.notice):
                        episode.notice = None
                elif value == "false" and episode.phase == "open":
                    if episode.failure is not None:
                        settle(
                            self.alert_dir,
                            episode.failure,
                            hashlib.sha256(episode.failure.payload()).hexdigest(),
                            "cleared",
                        )
                    episode.phase = "closed"
                    episode.notice = Intent(
                        "p8",
                        state.epoch,
                        1,
                        episode_id(
                            self.installation, episode.opening_generation, code, episode.sequence, "recovery"
                        ),
                        "recovery",
                        timestamp(utc),
                        f"Workstream daemon diagnostic cleared: {code}.",
                    )
                    self._save(state)
                    if self._flush(state, episode.notice):
                        episode.notice = None
                # Unknown retains both open condition and durable episode identity.
            self._save(state)
            return values

    def run(self) -> dict[str, Truth]:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return self.inspect(time.time(), time.monotonic(), boot)
