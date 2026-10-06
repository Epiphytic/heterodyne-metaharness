"""The wsd daemon (ADR 0001 §3.3, §5.2, §9): startup recovery, then pickup on triggers and timers.

Startup order is fixed: the instance lock; the journal, integrity-checked (corrupt: refuse to start);
recovery of every workstream (§3.3 steps 2 to 5); a startup pickup; and only then the control socket,
which is how events (timer ticks, and from plan 6 Marmot) reach wsd. A workstream whose recovery failed
stays held, and every later tick retries its recovery before any pickup: pickup never runs on state
recovery could not confirm.

Pickup and recovery are blocking (btq runs bd as a subprocess), so they run in worker threads, one lane
(a single worker thread) per workstream and one for status: a workstream's jobs wait only on that
workstream, and status waits on none of them. A lane holds at most one queued job of each kind (pickup,
reconcile, pause, resume, recover): a request for a kind already queued and not yet started joins it, and
gets its outcome, which loses nothing, because that job has not looked at anything yet. Its trigger is
the first request's. A pickup or reconcile that raises is recorded as `tick_failed`, whoever asked. Each
workstream's operation lock (`Parker.entry`, a reentrant lock also taken by park and release) is held
across the whole job: the "was it recovered?" decision, the recovery, the pickup and the `recovered`
update are one critical section, so no pickup can pass the check and then run after another job left the
workstream unrecovered. A job that raises (JournalBusy while another process holds the journal, for one)
leaves its workstream unrecovered, so the next trigger recovers it again before any pickup. A timer task
that dies anyway ends `serve` with its error rather than leave wsd running without its backstop.

Shutdown stops dispatch first (a request that arrives after it is refused), stops listening, cancels the
timers and every job that has not started, then waits up to DRAIN_SECONDS for jobs already running (a
thread can't be cancelled), then closes the socket's connections. A stop during startup does the same at
once: the socket and the timers never start after a stop. Jobs still running after the drain raise
Undrained, and `cli.run` then ends the process at once (a crash, which the journal is built to replay).

A workstream that is `stuck` (or a pickup whose outcome is `stuck`) is not idle: beads are ready or
claimed that only a human can move on. Status and tick replies say so.
"""

import asyncio
import concurrent.futures as cf
import contextlib
from collections.abc import Callable, Coroutine, Hashable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

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
from heterodyne.wsd.states import BeadState, WsState
from heterodyne.wsd.workstream import Deps

NEEDS_A_HUMAN = "not idle; a human must act"
FINISHED = frozenset({BeadState.CLOSED, BeadState.DROPPED})
DRAIN_SECONDS = 120.0       # how long shutdown waits for jobs already running in worker threads


class Stopping(Exception):
    """wsd is shutting down: no new job starts."""


class Undrained(Exception):
    """Jobs were still running in worker threads when shutdown gave up waiting for them."""


class JobFailed(Exception):
    """A pickup or reconcile raised. It was recorded (tick_failed, unless the journal was busy) and the
    workstream will be recovered again before its next pickup."""


STATUS_LANE = ""        # not a workstream slug (slugs are never empty)


class Lane:
    """One worker thread, and the jobs queued on it by kind: at most one queued (not started) per kind."""

    def __init__(self, name: str) -> None:
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"wsd-lane-{name or 'status'}")
        self.queued: dict[Hashable, cf.Future[object]] = {}
        self.jobs: set[cf.Future[object]] = set()


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
        self.stopping = False
        self.lanes = {name: Lane(name) for name in [STATUS_LANE, *parts.schedulers]}

    # --- blocking work (worker threads) ---

    def _locked[T](self, name: str, work: Callable[[], T]) -> T:
        """Run `work` holding the workstream's operation lock. If it raises, nothing is assumed about what
        it left: the workstream is recovered again before its next pickup. The marker changes under the
        same lock as the decision that reads it."""
        with self.parts.schedulers[name].parker.entry():
            try:
                return work()
            except BaseException:
                self.recovered.discard(name)
                raise

    def _recover(self, name: str) -> Recovered:
        self.recovered.discard(name)
        result = recover(self.parts.schedulers[name])
        if result.ok:
            self.recovered.add(name)
        return result

    def recover_one(self, name: str) -> Recovered:
        """A recovery that fails, or raises, leaves the workstream to be recovered again first."""
        return self._locked(name, lambda: self._recover(name))

    def pickup_one(self, name: str, trigger: Trigger) -> Outcome:
        """Pickup, after a recovery if this workstream's last one did not succeed: the event entry point
        (plan 6 triggers come in here, never through Scheduler.pickup directly)."""
        def work() -> Outcome:
            if name not in self.recovered and not self._recover(name).ok:
                return Outcome.HELD
            return self.parts.schedulers[name].pickup(trigger)
        return self._locked(name, work)

    def _operator_pickup(self, name: str) -> Outcome:
        return self.pickup_one(name, Trigger(TriggerKind.OPERATOR))

    def reconcile_one(self, name: str) -> Outcome:
        """The 5-minute reconcile (§9): rebuild state from beads and the runtime, then pick up."""
        def work() -> Outcome:
            if not self._recover(name).ok:
                return Outcome.HELD
            return self.parts.schedulers[name].pickup(Trigger(TriggerKind.BACKSTOP))
        return self._locked(name, work)

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
        outcome = self._operator_pickup(name)
        return CtlReply("ok", f"{name}: resumed.", {name: outcome_fields(outcome)})

    def status(self, only: str | None, everything: bool = False) -> dict[str, dict[str, str]]:
        """Each workstream's state. Closed and dropped beads (history) are counted, not listed, unless
        `everything`: the reply must stay within ctl.MAX_REPLY however long wsd has run."""
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
                                  for b in snap.beads if everything or b.state not in FINISHED),
                "finished": str(sum(b.state in FINISHED for b in snap.beads)),
                "open_ops": ",".join(f"{o.bead}:{o.kind.value}@{o.step}" for o in snap.ops),
                "last_event": str(snap.last_event),
            }
        return found

    # --- the event loop ---

    def _names(self, only: str | None) -> list[str]:
        return [n for n in self.parts.schedulers if only is None or n == only]

    def _submit(self, lane: str, kind: Hashable, fn: Callable[[], object]) -> cf.Future[object]:
        """Queue `fn` on `lane`, or join the job of the same kind queued there and not yet started.
        Called from the event loop thread only. Refused once shutdown has begun."""
        if self.stopping:
            raise Stopping
        ln = self.lanes[lane]
        ln.jobs = {f for f in ln.jobs if not f.done()}
        queued = ln.queued.get(kind)
        if queued is not None and not queued.running() and not queued.done():
            return queued
        fut = ln.pool.submit(fn)
        ln.queued[kind] = fut
        ln.jobs.add(fut)
        return fut

    async def _run[T](self, lane: str, kind: Hashable, fn: Callable[[], T]) -> T:
        """Run blocking work on a lane and wait for it. Cancelling the caller leaves the job to its lane
        (a thread can't be stopped; shutdown drains it). A job that shutdown cancelled before it started
        reads as Stopping."""
        fut = self._submit(lane, kind, fn)
        try:
            return await asyncio.shield(asyncio.wrap_future(fut))  # type: ignore[return-value]
        except asyncio.CancelledError:
            if fut.cancelled():
                raise Stopping from None
            raise

    async def handle(self, req: CtlRequest) -> CtlReply:
        why = refusal(req)
        if why:
            return CtlReply("refused", why)
        if req.ws is not None and req.ws not in self.parts.schedulers:
            return CtlReply("refused", "no such workstream")
        try:
            return await self._dispatch(req)
        except Stopping:
            return CtlReply("refused", "wsd is stopping; nothing more was started")

    async def _dispatch(self, req: CtlRequest) -> CtlReply:
        if req.op == "status":
            ws, everything = req.ws, req.all
            return CtlReply("ok", "status", await self._run(STATUS_LANE, ("status", ws, everything),
                                                            lambda: self.status(ws, everything)))
        if req.op in ("pause", "resume") and req.ws is not None:
            name, paused = req.ws, req.op == "pause"
            return await self._run(name, req.op, lambda: self.set_pause(name, paused))
        job = self.reconcile_one if req.job == "reconcile" else self._operator_pickup
        kind = req.job or "pickup"
        names = self._names(req.ws)
        done = await asyncio.gather(*(self._job(name, kind, job) for name in names), return_exceptions=True)
        results: dict[str, dict[str, str]] = {}
        failed: list[str] = []
        for name, outcome in zip(names, done, strict=True):
            if isinstance(outcome, JobFailed):
                failed.append(f"{name}: {outcome}")
                results[name] = {"outcome": "failed", "error": str(outcome)}
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                results[name] = outcome_fields(outcome)
        if failed:
            return CtlReply("failed", f"{req.job} failed ({'; '.join(failed)}); recovered again before the "
                                      "next pickup", results)
        return CtlReply("ok", f"{req.job} done", results)

    async def _job(self, name: str, kind: str, job: Callable[[str], Outcome]) -> Outcome:
        return await self._run(name, kind, lambda: self._guarded(job, name))

    def _guarded(self, job: Callable[[str], Outcome], name: str) -> Outcome:
        """A pickup or reconcile that fails unexpectedly is recorded and raised as JobFailed; the
        workstream is recovered again before its next pickup (the job's own lock section saw to that),
        and nothing is assumed about what the failure left. A journal another process holds can refuse
        the record too; that is not a further failure. Any other failure to record it propagates as
        itself: a timer that meets it dies, and so does wsd."""
        try:
            return job(name)
        except Exception as exc:  # noqa: BLE001 - recorded by type; the next pickup re-recovers
            failure, cause = type(exc).__name__, exc
        try:
            self.parts.journal.emit(name, None, "tick_failed", failure)
        except JournalBusy:
            pass        # nothing to record it in; `recovered` already says what matters
        raise JobFailed(failure) from cause

    async def _every(self, seconds: float, job: Callable[[str], Outcome], kind: str = "pickup") -> None:
        async def one(name: str) -> None:
            with contextlib.suppress(JobFailed, Stopping):      # recorded, or shutting down: carry on
                await self._job(name, kind, job)

        while True:
            await asyncio.sleep(seconds)
            await asyncio.gather(*(one(name) for name in self.parts.schedulers))

    async def _startup(self) -> None:
        """`startup`, on the lanes: each workstream's recovery, then its startup pickup, in its own lane."""
        jobs: list[Coroutine[Any, Any, object]] = []
        for name in self.parts.schedulers:
            jobs.append(self._run(name, "recover", lambda n=name: self.recover_one(n)))
            jobs.append(self._run(name, "pickup",
                                  lambda n=name: self.pickup_one(n, Trigger(TriggerKind.STARTUP))))
        await asyncio.gather(*jobs)

    async def serve(self, stop: asyncio.Event) -> None:
        """Run until `stop` is set or a timer dies. Returns (or raises) only once no job is running, or
        raises Undrained if some still are after DRAIN_SECONDS. A stop during startup goes straight to
        the drain; the socket and the timers never start after it."""
        starting = asyncio.create_task(self._startup())
        stopped = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait([starting, stopped], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (starting, stopped):
                task.cancel()
            await asyncio.gather(starting, stopped, return_exceptions=True)
        failed = None if starting.cancelled() else starting.exception()
        if starting.cancelled() or failed is not None or stop.is_set():
            await self._drain()
            if failed is not None:
                raise failed
            return
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
            self.stopping = True        # from here no request or timer starts a job
            server.stop_accepting()
            for task in (stopping, *timers):
                task.cancel()
            await asyncio.gather(stopping, *timers, return_exceptions=True)
            try:
                await self._drain()
            finally:
                await server.close()    # the handlers waiting on drained jobs have their replies now

    def _cancel_queued(self) -> set[cf.Future[object]]:
        """Stop admitting, cancel every job not yet started, and return those still running."""
        self.stopping = True
        for ln in self.lanes.values():
            ln.pool.shutdown(wait=False, cancel_futures=True)
        return {f for ln in self.lanes.values() for f in ln.jobs if not f.done()}

    async def _drain(self) -> None:
        running = self._cancel_queued()
        if running:
            await asyncio.wait([asyncio.wrap_future(f) for f in running], timeout=DRAIN_SECONDS)
        left = [f for f in running if not f.done()]
        if left:
            raise Undrained(f"{len(left)} job(s) still running after {DRAIN_SECONDS:g} s")

    def drain_now(self, timeout: float) -> bool:
        """The same drain without an event loop (for an owner whose loop has gone): True once nothing is
        running, False if something still is after `timeout`."""
        _, left = cf.wait(self._cancel_queued(), timeout)
        return not left


def outcome_fields(outcome: Outcome) -> dict[str, str]:
    """A pickup's outcome for a reply; a stuck one is never read as idle."""
    fields = {"outcome": outcome.value}
    if outcome is Outcome.STUCK:
        fields["attention"] = NEEDS_A_HUMAN
    return fields
