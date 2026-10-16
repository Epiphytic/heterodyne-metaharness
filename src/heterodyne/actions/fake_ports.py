"""Explicit OFFLINE ports for reducer tests. Not registered with WSD, CLI or daemon.

All real registry entries remain unavailable. Fake names/IDs prove no native provenance.
"""

from dataclasses import dataclass, field

from heterodyne.actions.decisions import DecisionObservation
from heterodyne.actions.requests import CreationKey, Refused


class AdapterUnavailable(Refused):
    pass


class UnavailablePorts:
    """Fail closed at every proposed integration boundary; no effect or queue client is loaded."""

    available = False

    @staticmethod
    def invoke(_operation: str) -> None:
        raise AdapterUnavailable("adapter-unavailable")


@dataclass
class FakeQueue:
    """Lost create acknowledgement leaves a discoverable keyed shell and never requires recreate."""

    shells: dict[CreationKey, tuple[str, ...]] = field(default_factory=lambda: {})
    create_calls: int = 0
    decisions: dict[str, DecisionObservation] = field(default_factory=lambda: {})
    closed: set[str] = field(default_factory=lambda: set())
    pins: set[str] = field(default_factory=lambda: set())
    decision_writes: int = 0

    def create(self, key: CreationKey, approval_id: str, *, lose_ack: bool = False) -> str:
        if key in self.shells:
            raise Refused("fake-create-replay")
        self.create_calls += 1
        self.shells[key] = (approval_id,)
        if lose_ack:
            raise TimeoutError("fake-lost-create-ack")
        return approval_id

    def lookup(self, key: CreationKey) -> tuple[str, ...]:
        return self.shells.get(key, ())

    def decide(self, event: DecisionObservation, *, lose_ack: bool = False) -> None:
        old = self.decisions.get(event.approval_id)
        if old is not None:
            if old != event:
                raise Refused("fake-native-first-decision")
            return
        self.decision_writes += 1
        self.decisions[event.approval_id] = event
        if lose_ack:
            raise TimeoutError("fake-lost-decision-ack")

    def read_decision(self, approval_id: str) -> DecisionObservation | None:
        return self.decisions.get(approval_id)

    def close_decision(self, event: DecisionObservation) -> None:
        if self.decisions.get(event.approval_id) != event:
            raise Refused("fake-close-binding")
        self.closed.add(event.approval_id)

    def finish_pins(self, event: DecisionObservation) -> None:
        if event.approval_id not in self.closed or self.decisions.get(event.approval_id) != event:
            raise Refused("fake-pins-before-close")
        self.pins.add(event.approval_id)


@dataclass
class FakeSession:
    session_key: str
    generation: int
    parked: bool = False
    landed: str | None = None

    def park(self, session_key: str, generation: int) -> None:
        if (session_key, generation) != (self.session_key, self.generation):
            raise Refused("fake-session-binding")
        self.parked = True

    def land(self, source: str, *, session_key: str, generation: int) -> None:
        if not self.parked or (session_key, generation) != (self.session_key, self.generation):
            raise Refused("fake-landing-binding")
        self.landed = source


@dataclass
class FakeEffect:
    """A deterministic in-memory target for claim/readback fixtures, not an enabled adapter."""

    issued: set[str] = field(default_factory=lambda: set())
    observations: dict[str, str] = field(default_factory=lambda: {})

    def perform(
        self, attempt_id: str, *, claim_consumed_readback: bool, dispatch_kind: str = "initial"
    ) -> None:
        if dispatch_kind != "initial" or not claim_consumed_readback or attempt_id in self.issued:
            raise Refused("fake-effect-claim-or-replay")
        self.issued.add(attempt_id)
        self.observations[attempt_id] = "succeeded"

    def reconcile(self, attempt_id: str) -> str:
        return self.observations.get(attempt_id, "unknown")
