"""The wsd daemon (ADR 0001 §3.3, §5.2, §9): startup recovery, then pickup on triggers and timers.

Startup order is fixed: the instance lock; the journal, integrity-checked (corrupt: refuse to start);
recovery of every workstream (§3.3 steps 2 to 5); a startup pickup; and only then the control socket,
which is how events (timer ticks, and from plan 6 Marmot) reach wsd. A workstream whose recovery failed
stays held, and every later tick retries its recovery before any pickup: pickup never runs on state
recovery could not confirm.

Pickup and recovery are blocking (btq runs bd as a subprocess), so they run in worker threads; each
workstream's operation lock (`Parker.entry`, also taken by park and release) keeps them one at a time.
A job that raises (JournalBusy while another process holds the journal, for one) leaves its workstream
unrecovered, so the next trigger recovers it again before any pickup. A timer task that dies anyway ends
`serve` with its error rather than leave wsd running without its backstop.

A workstream that is `stuck` (or a pickup whose outcome is `stuck`) is not idle: beads are ready or
claimed that only a human can move on. Status and tick replies say so.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from heterodyne.wsd.beads import BeadsAdapter, BeadsUnavailable
from heterodyne.wsd.btq import QueueFactory
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.ctl import CtlReply, CtlRequest, CtlServer, refusal
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal, JournalBusy
from heterodyne.wsd.recovery import Recovered, recover
from heterodyne.wsd.runtime import ActionReconciler, AgentRuntime, HoldingReconciler
from heterodyne.wsd.scheduler import Outcome, Scheduler, Trigger, TriggerKind
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.states import WsState
from heterodyne.wsd.workstream import Deps

NEEDS_A_HUMAN = "not idle; a human must act"


@dataclass(frozen=True)
class Parts:
    journal: Journal
    beads: BeadsAdapter
    gate: ClaimGate
    schedulers: dict[str, Scheduler]


def assemble(s: WsdSettings, journal: Journal, factory: QueueFactory, runtime: AgentRuntime,
             reconciler: Callable[[BeadsAdapter], ActionReconciler] = HoldingReconciler,
             cp: Checkpoint = nothing) -> Parts:
    beads = BeadsAdapter(factory)
    gate = ClaimGate(s.lock_dir, beads, cp)
    deps = Deps(journal, beads, gate, runtime, reconciler(beads), cp)
    return Parts(journal, beads, gate, {w.name: Scheduler(w, deps) for w in s.workstreams})


class Wsd:
    def __init__(self, s: WsdSettings, parts: Parts) -> None:
        self.s = s
        self.parts = parts
        self.recovered: set[str] = set()

    # --- blocking work (worker threads) ---

    def recover_one(self, name: str) -> Recovered:
        """A recovery that fails, or raises, leaves the workstream to be recovered again first."""
        self.recovered.discard(name)
        result = recover(self.parts.schedulers[name])
        if result.ok:
            self.recovered.add(name)
        return result

    def _job(self, job: Callable[[str], Outcome], name: str) -> Outcome:
        """Run a pickup or reconcile; if it raises, nothing is assumed about what it left."""
        try:
            return job(name)
        except BaseException:
            self.recovered.discard(name)
            raise

    def pickup_one(self, name: str, trigger: Trigger) -> Outcome:
        """Pickup, after a recovery if this workstream's last one did not succeed."""
        if name not in self.recovered and not self.recover_one(name).ok:
            return Outcome.HELD
        return self.parts.schedulers[name].pickup(trigger)

    def _operator_pickup(self, name: str) -> Outcome:
        return self.pickup_one(name, Trigger(TriggerKind.OPERATOR))

    def reconcile_one(self, name: str) -> Outcome:
        """The 5-minute reconcile (§9): rebuild state from beads and the runtime, then pick up."""
        if not self.recover_one(name).ok:
            return Outcome.HELD
        return self.parts.schedulers[name].pickup(Trigger(TriggerKind.BACKSTOP))

    def startup(self) -> dict[str, Outcome]:
        """Recover every workstream, then run one startup pickup each (§3.3 step 6 comes after this)."""
        for name in self.parts.schedulers:
            self.recover_one(name)
        return {name: self.pickup_one(name, Trigger(TriggerKind.STARTUP)) for name in self.parts.schedulers}

    def set_pause(self, name: str, paused: bool) -> CtlReply:
        """§4.3: the flag is set under the claim lock, so once this returns no claim can start. A flag that
        can't be confirmed is reported as a failure: nothing is acknowledged."""
        try:
            if paused:
                self.parts.gate.pause(name)
            else:
                self.parts.gate.resume(name)
        except BeadsUnavailable as exc:
            return CtlReply("failed", f"the pause flag could not be confirmed ({exc}); "
                                      "nothing is acknowledged")
        self.parts.journal.emit(name, None, "paused" if paused else "resumed")
        if paused:
            sched = self.parts.schedulers[name]
            with sched.parker.entry():
                sched.publish()        # the acknowledged pause shows at once, not at the next pickup
            return CtlReply("ok", f"{name}: paused. No new claims start; a running bead finishes and parked "
                                  "beads may resume.")
        outcome = self._job(self._operator_pickup, name)
        return CtlReply("ok", f"{name}: resumed.", {name: outcome_fields(outcome)})

    def status(self, only: str | None) -> dict[str, dict[str, str]]:
        found: dict[str, dict[str, str]] = {}
        for name in self.parts.schedulers:
            if only is not None and name != only:
                continue
            snap = self.parts.journal.snapshot(name)
            found[name] = {
                "state": snap.state.value if snap.state else "unknown",
                "attention": NEEDS_A_HUMAN if snap.state is WsState.STUCK else "",
                "recovered": "yes" if name in self.recovered else "no",
                "holds": ",".join(r.value for r in snap.holds),
                "beads": ",".join(f"{b.bead}={b.state.value}" + (f"({b.reason.value})" if b.reason else "")
                                  for b in snap.beads),
                "open_ops": ",".join(f"{o.bead}:{o.kind.value}@{o.step}" for o in snap.ops),
                "last_event": str(snap.last_event),
            }
        return found

    # --- the event loop ---

    def _names(self, only: str | None) -> list[str]:
        return [n for n in self.parts.schedulers if only is None or n == only]

    async def handle(self, req: CtlRequest) -> CtlReply:
        why = refusal(req)
        if why:
            return CtlReply("refused", why)
        if req.ws is not None and req.ws not in self.parts.schedulers:
            return CtlReply("refused", "no such workstream")
        if req.op == "status":
            return CtlReply("ok", "status", await asyncio.to_thread(self.status, req.ws))
        if req.op in ("pause", "resume") and req.ws is not None:
            return await asyncio.to_thread(self.set_pause, req.ws, req.op == "pause")
        job = self.reconcile_one if req.job == "reconcile" else self._operator_pickup
        results: dict[str, dict[str, str]] = {}
        for name in self._names(req.ws):
            outcome = await asyncio.to_thread(self._job, job, name)
            results[name] = outcome_fields(outcome)
        return CtlReply("ok", f"{req.job} done", results)

    def _guarded(self, job: Callable[[str], Outcome], name: str) -> None:
        """A timer job that fails unexpectedly is recorded, and the workstream is recovered again before
        its next pickup: the timer keeps running, and nothing is assumed about what the failure left. A
        journal another process holds can refuse the record too; the timer outlives that as well."""
        try:
            self._job(job, name)
        except Exception as exc:  # noqa: BLE001 - recorded by type; the next tick re-recovers
            failure = type(exc).__name__
        else:
            return
        try:
            self.parts.journal.emit(name, None, "tick_failed", failure)
        except JournalBusy:
            pass        # nothing to record it in; `recovered` already says what matters

    async def _every(self, seconds: float, job: Callable[[str], Outcome]) -> None:
        while True:
            await asyncio.sleep(seconds)
            for name in self.parts.schedulers:
                await asyncio.to_thread(self._guarded, job, name)

    async def serve(self, stop: asyncio.Event) -> None:
        await asyncio.to_thread(self.startup)
        server = CtlServer(self.s.socket, self.handle)
        await server.start()
        timers = [asyncio.create_task(self._every(
                      self.s.backstop_seconds, lambda n: self.pickup_one(n, Trigger(TriggerKind.BACKSTOP)))),
                  asyncio.create_task(self._every(self.s.reconcile_seconds, self.reconcile_one))]
        stopping = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait([stopping, *timers], return_when=asyncio.FIRST_COMPLETED)
            for task in timers:
                if task.done():
                    task.result()       # a timer only ends by raising: wsd must not run on without it
        finally:
            for task in (stopping, *timers):
                task.cancel()
            await asyncio.gather(stopping, *timers, return_exceptions=True)
            await server.close()


def outcome_fields(outcome: Outcome) -> dict[str, str]:
    """A pickup's outcome for a reply; a stuck one is never read as idle."""
    fields = {"outcome": outcome.value}
    if outcome is Outcome.STUCK:
        fields["attention"] = NEEDS_A_HUMAN
    return fields
