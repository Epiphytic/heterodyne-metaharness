"""A recording AgentRuntime (plan 4 provides the real one). Sessions survive a simulated wsd restart,
like real agent processes do. A session stays listed until it is stopped or `end`s on its own."""

from heterodyne.wsd.runtime import (
    LaunchFailed,
    LaunchSpec,
    LaunchUncertain,
    Liveness,
    RuntimeUnavailable,
    Session,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.up = True
        self.listed: dict[str, Session] = {}
        self.launches: list[LaunchSpec] = []
        self.stops: list[str] = []
        self.launch_failures = 0      # the next N launches fail, confirmed: nothing starts
        self.launch_uncertain = 0     # the next N launches are uncertain: listed as UNKNOWN
        self.failing_beads: set[str] = set()     # every launch for these beads fails, confirmed
        self.uncertain_beads: set[str] = set()   # the first launch for each of these beads is uncertain
        self.stop_failures = 0
        self.list_failures = 0

    def available(self) -> bool:
        return self.up

    def sessions(self, ws: str) -> list[Session]:
        if not self.up or self.list_failures:
            self.list_failures = max(0, self.list_failures - 1)
            raise RuntimeUnavailable("can't list sessions")
        return [s for s in self.listed.values() if s.ws == ws]

    def launch(self, spec: LaunchSpec) -> None:
        if not self.up:
            raise RuntimeUnavailable("down")
        current = self.listed.get(spec.session_key)
        if current is not None and current.liveness is Liveness.LIVE:
            return
        if self.launch_failures or spec.bead in self.failing_beads:
            self.launch_failures = max(0, self.launch_failures - 1)
            raise LaunchFailed("launch failed")
        self.launches.append(spec)
        uncertain = bool(self.launch_uncertain) or spec.bead in self.uncertain_beads
        liveness = Liveness.UNKNOWN if uncertain else Liveness.LIVE
        self.listed[spec.session_key] = Session(spec.session_key, spec.ws, spec.bead, spec.role, liveness)
        if uncertain:
            if spec.bead in self.uncertain_beads:
                self.uncertain_beads.discard(spec.bead)
            else:
                self.launch_uncertain -= 1
            raise LaunchUncertain("no answer from the backend")

    def stop(self, session_key: str) -> None:
        if not self.up:
            raise RuntimeUnavailable("down")      # nothing confirmed: the session stays listed
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeUnavailable("stop not confirmed")
        self.stops.append(session_key)
        self.listed.pop(session_key, None)

    # --- test controls ---

    def end(self, session_key: str) -> None:
        """The session ended on its own (the agent exited or crashed)."""
        del self.listed[session_key]

    def set(self, session_key: str, liveness: Liveness) -> None:
        old = self.listed[session_key]
        self.listed[session_key] = Session(old.key, old.ws, old.bead, old.role, liveness)

    def adopt(self, spec: LaunchSpec, liveness: Liveness = Liveness.LIVE) -> None:
        """A session the runtime already runs (for example one launched before a lost journal)."""
        self.listed[spec.session_key] = Session(spec.session_key, spec.ws, spec.bead, spec.role, liveness)

    def live(self) -> list[str]:
        return [k for k, s in self.listed.items() if s.liveness is Liveness.LIVE]

    def coders(self, ws: str = "alpha") -> list[str]:
        """Beads with a listed coder session."""
        return sorted(s.bead for s in self.listed.values() if s.ws == ws and s.role == "coder")
