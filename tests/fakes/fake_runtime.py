"""A recording AgentRuntime (plan 4 provides the real one). Sessions survive a simulated wsd restart,
like real agent processes do. A session stays listed until it is stopped or `end`s on its own, and then
`ended` reports how (g08sd): `stop` is STOPPED, `lifetime` LIFETIME, `end` DIED unless told. A launch
returns a `Started` with synthetic tmux IDs; its native ID is the spec's, or a synthetic thread ID when the
spec has none (Codex before its thread is known)."""

from heterodyne.sandbox.spec import short_id
from heterodyne.wsd.runtime import (
    Action,
    EndCause,
    Ended,
    LaunchFailed,
    LaunchSpec,
    LaunchUncertain,
    Liveness,
    Resolution,
    ResolveFailed,
    RuntimeUnavailable,
    Session,
    Signal,
    Started,
    UnlandedView,
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
        self.expires: list[tuple[str, int]] = []
        self.expire_failures = 0
        self.list_failures = 0
        self.calls = 0                # every launch() call, whatever it did
        self.raise_after_start: BaseException | None = None    # raised once, after the session started
        self.views: dict[str, list[UnlandedView]] = {}        # ws -> its unlanded sessions
        self.resolves: list[tuple[str, str, int, Action, str | None]] = []     # every resolve asked
        self.resolve_error: Exception | None = None            # raised by the next resolve
        self.intents: dict[tuple[str, int], Resolution] = {}   # resolved, not yet finished: the hold
        self.finished: list[tuple[str, str, int]] = []
        self.ends: dict[str, Ended] = {}      # key -> its latest generation's end
        self.generations: dict[str, int] = {}
        self.ended_failures = 0
        self.queued: dict[str, list[Signal]] = {}   # reports by workstream, drained by signals()
        self.drains: list[str] = []

    def available(self) -> bool:
        return self.up

    def sessions(self, ws: str) -> list[Session]:
        if any(r.ws == ws for r in self.intents.values()):
            raise RuntimeUnavailable("an unlanded resolve is unfinished")
        if not self.up or self.list_failures:
            self.list_failures = max(0, self.list_failures - 1)
            raise RuntimeUnavailable("can't list sessions")
        return [s for s in self.listed.values() if s.ws == ws]

    def launch(self, spec: LaunchSpec) -> Started:
        self.calls += 1
        if not self.up:
            raise RuntimeUnavailable("down")
        current = self.listed.get(spec.session_key)
        if current is not None and current.liveness is Liveness.LIVE:
            return self._started(spec)
        if self.launch_failures or spec.bead in self.failing_beads:
            self.launch_failures = max(0, self.launch_failures - 1)
            raise LaunchFailed("launch failed")
        self.launches.append(spec)
        self.generations[spec.session_key] = spec.generation
        self.ends.pop(spec.session_key, None)
        uncertain = bool(self.launch_uncertain) or spec.bead in self.uncertain_beads
        liveness = Liveness.UNKNOWN if uncertain else Liveness.LIVE
        self.listed[spec.session_key] = Session(spec.session_key, spec.ws, spec.bead, spec.role, liveness)
        if uncertain:
            if spec.bead in self.uncertain_beads:
                self.uncertain_beads.discard(spec.bead)
            else:
                self.launch_uncertain -= 1
            raise LaunchUncertain("no answer from the backend")
        if self.raise_after_start is not None:
            exc, self.raise_after_start = self.raise_after_start, None
            raise exc
        return self._started(spec)

    def _started(self, spec: LaunchSpec) -> Started:
        native = spec.native_id if spec.native_id is not None else f"thread-{spec.generation}"
        return Started(f"wsd-{spec.session_key[:8]}", f"%{self.calls}", 4000 + self.calls, native)

    def stop(self, session_key: str) -> None:
        if not self.up:
            raise RuntimeUnavailable("down")      # nothing confirmed: the session stays listed
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeUnavailable("stop not confirmed")
        self.stops.append(session_key)
        if self.listed.pop(session_key, None) is not None:
            self._ended(session_key, EndCause.STOPPED)

    def expire(self, ws: str, now: int) -> None:
        self.expires.append((ws, now))
        if self.expire_failures:
            self.expire_failures -= 1
            raise RuntimeUnavailable("lifetime stop not confirmed")

    def signals(self, ws: str) -> list[Signal]:
        self.drains.append(ws)
        return self.queued.pop(ws, [])

    def unlanded(self, ws: str) -> list[UnlandedView]:
        if not self.up:
            raise RuntimeUnavailable("down")
        return list(self.views.get(ws, []))

    def resolve_unlanded(self, ws: str, sid: str, generation: int, action: Action,
                         tip: str | None) -> Resolution:
        """Like SandboxRuntime's: the Resolution comes from the intent, which a repeat finds again."""
        self.resolves.append((ws, sid, generation, action, tip))
        if self.resolve_error is not None:
            exc, self.resolve_error = self.resolve_error, None
            raise exc
        done = self.intents.setdefault(
            (sid, generation), Resolution(ws, "btq-1", sid, generation, action, tip,
                                          f"refs/heads/unlanded/btq-1/{sid}-g{generation}" if action == "keep"
                                          else ""))
        self.views[ws] = [v for v in self.views.get(ws, []) if v.short_id != sid]
        for key in [k for k in self.ends if short_id(k) == sid]:
            del self.ends[key]                  # its record goes with its directory, as SandboxRuntime's does
        return done

    def finish_resolution(self, ws: str, sid: str, generation: int) -> None:
        intent = self.intents.get((sid, generation))
        if intent is not None and intent.ws != ws:
            raise ResolveFailed("not this workstream's")
        self.intents.pop((sid, generation), None)
        self.finished.append((ws, sid, generation))

    def ended(self, session_key: str) -> Ended | None:
        if not self.up or self.ended_failures:
            self.ended_failures = max(0, self.ended_failures - 1)
            raise RuntimeUnavailable("can't read the session's end")
        return self.ends.get(session_key)

    def _ended(self, session_key: str, cause: EndCause) -> None:
        generation = self.generations.get(session_key)
        if generation is not None:
            self.ends[session_key] = Ended(generation, cause, True, 60)

    # --- test controls ---

    def report(self, ws: str, *signals: Signal) -> None:
        """The workstream's sessions sent these reports (already attributed, as the runtime does)."""
        self.queued.setdefault(ws, []).extend(signals)

    def end(self, session_key: str, cause: EndCause = EndCause.DIED) -> None:
        """The session ended on its own (the agent exited or crashed)."""
        del self.listed[session_key]
        self._ended(session_key, cause)

    def lifetime(self, session_key: str) -> None:
        """The runtime's lifetime stop of a live session (D13): healthy evidence, which resets the count."""
        self.end(session_key, EndCause.LIFETIME)

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
