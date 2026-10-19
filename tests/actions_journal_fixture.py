"""Explicit synthetic clock producer for retained reducer fixtures.

These tests already supply synthetic times. The adapter enrolls those times through
an injected journal sampler, not the production caller-observation fallback. Clock
provenance/adversarial tests import the production journal directly instead.
"""

from heterodyne.actions.dispatch import Clock
from heterodyne.actions.journal import FixtureJournal as Journal
from heterodyne.actions.journal import create_fixture
from heterodyne.actions.transitions import EMPTY_OBSERVATION

_DEFAULT = object()


class FixtureJournal(Journal):
    def __init__(self, path, *, clock=_DEFAULT):
        self.sample = Clock("allocation", 1, 1)
        super().__init__(path, clock=(lambda: self.sample) if clock is _DEFAULT else clock)

    @classmethod
    def create(cls, path, *, clock=_DEFAULT):
        create_fixture(path)
        return cls(path, clock=clock)

    def add_dispatch(self, value):
        self.sample = value.started
        return super().add_dispatch(value)

    def transition(self, kind, key, expected_version, reducer, *, observation=EMPTY_OBSERVATION):
        if observation.now is not None:
            self.sample = observation.now
        return super().transition(kind, key, expected_version, reducer, observation=observation)
