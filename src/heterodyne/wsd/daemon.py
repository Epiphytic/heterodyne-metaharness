"""The wsd daemon (ADR 0001 §3.3, §5.2, §9): startup recovery, then pickup on triggers and timers.

Startup order is fixed: the instance lock; the journal, integrity-checked (corrupt: refuse to start);
recovery of every workstream (§3.3 steps 2 to 5); a startup pickup; and only then the control socket,
which is how events (timer ticks, and from plan 6 Marmot) reach wsd. A workstream whose recovery failed
stays held, and every later tick retries its recovery before any pickup: pickup never runs on state
recovery could not confirm.

Pickup and recovery are blocking (btq runs bd as a subprocess), so they run in worker threads, one lane
(a single worker thread) per workstream and one for status: a workstream's jobs wait only on that
workstream, and status waits on none of them. Each workstream has its own backstop and reconcile timers,
so a workstream whose job is stuck delays only its own ticks. A lane holds at most one queued job per key:
a request whose key is queued and not yet started joins that job and gets its outcome, which loses
nothing, because that job has not looked at anything yet. A pickup's key is its kind and its trigger's
ref, so only triggers for the same event (or none) share a job, and a joined one shares the first one's
trigger. Pause and resume share one key, `pause-state`: the job applies whichever was asked last, when it
starts, and every caller is told the state it published, so a pause overtaken by a later resume is never
acknowledged as a pause. A pickup or reconcile that raises is recorded as `tick_failed`, whoever asked. Each
workstream's operation lock (`Parker.entry`, a reentrant lock also taken by park and release) is held
across the whole job: the "was it recovered?" decision, the recovery, the pickup and the `recovered`
update are one critical section, so no pickup can pass the check and then run after another job left the
workstream unrecovered. A job that raises (JournalBusy while another process holds the journal, for one)
leaves its workstream unrecovered, so the next trigger recovers it again before any pickup. A timer task
that dies anyway ends `serve` with its error rather than leave wsd running without its backstop.

Shutdown stops dispatch and cancels every job that has not started, on every lane in one step under
the daemon's shutdown lock, before anything else (a lane only starts a job under that lock, after
checking it is still open). Then it stops listening, cancels the timers, waits up to DRAIN_SECONDS for
jobs already running (a thread can't be cancelled), and closes the socket's connections. A stop during
startup does the same at once: the socket and the timers never start after a stop, nor the timers after
a stop that arrives while the socket starts. A socket that fails to start shuts down the same way, then
raises. Jobs still running after the drain raise Undrained, and `cli.run` then ends the process at once
(a crash, which the journal is built to replay).

Quota wakes (AU-5 §3.6): after each job on a workstream's lane, its pending wake is cancelled and, if the
scheduler's last pickup left a `wake_at`, a one-shot callback is armed for it on the event loop. When it
fires, it submits a `QUOTA_WAKE` pickup through the same lane, so it serialises with every other job.
Nothing is armed once wsd is stopping, and shutdown cancels the armed wakes and any wake pickup awaiting
its lane along with the timers. A wake that is lost (a restart) only delays: the backstop and startup
pickups compute it again.

Re-gates (AU-4 §5): the first successful recovery of each workstream in this process re-gates its
`account_changed` waits, under the same lock (`regated`, in memory only); nothing else re-gates except
`wsctl reload`. A reload loads the settings again and refuses any change outside each workstream's
`accounts`, `usage` and `models` ("restart required"); otherwise one job per lane swaps that workstream's
settings under its operation lock and re-gates it, and `s` becomes the loaded settings.

A workstream that is `stuck` (or a pickup whose outcome is `stuck`) is not idle: beads are ready or
claimed that only a human can move on. Status and tick replies say so.
"""

import asyncio
import concurrent.futures as cf
import contextlib
import dataclasses
import os
import threading
from collections import deque
from collections.abc import Callable, Coroutine, Hashable
from dataclasses import dataclass
from typing import Any

from heterodyne.config import ConfigError
from heterodyne.wsd.beads import BeadsAdapter, BeadsUnavailable
from heterodyne.wsd.btq import QueueFactory
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.ctl import CtlReply, CtlRequest, CtlServer, refusal
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal, JournalBusy
from heterodyne.wsd.recovery import Recovered, recover
from heterodyne.wsd.runtime import ActionReconciler, AgentRuntime, HoldingReconciler
from heterodyne.wsd.scheduler import Outcome, Scheduler, Trigger, TriggerKind
from heterodyne.wsd.settings import WsdSettings, resolve, restart_fields, restart_view
from heterodyne.wsd.states import BeadState, WsState
from heterodyne.wsd.workstream import Deps, WorkstreamSettings

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
PAUSE_STATE = "pause-state"     # the one key pause and resume share: the last one asked is applied
RELOAD = "reload"


def _resolve() -> WsdSettings:
    return resolve(os.environ)


class Lane:
    """One worker thread running its jobs in order, with at most one queued (not started) job per key.
    Admission and closing happen under `lock`; a job starts only under `shutdown` (shared by every lane
    of a daemon, which closes them all under it at once) and then `lock`, so once shutdown has begun no
    job that had not started ever does, on any lane. Lock order: `shutdown`, then `lock`; neither is
    held while a job runs."""

    def __init__(self, name: str, shutdown: threading.Lock) -> None:
        self.name = name or "status"
        self.shutdown = shutdown
        self.lock = threading.Lock()
        self.ready = threading.Condition(self.lock)
        self.pending: deque[tuple[cf.Future[object], Hashable, Callable[[], object]]] = deque()
        self.queued: dict[Hashable, cf.Future[object]] = {}
        self.jobs: set[cf.Future[object]] = set()       # queued or running
        self.closed = False
        self.worker: threading.Thread | None = None

    def submit(self, key: Hashable, fn: Callable[[], object]) -> cf.Future[object]:
        """Queue `fn`, or return the job queued under `key` that has not started. Raises Stopping once
        closed."""
        with self.lock:
            if self.closed:
                raise Stopping
            joined = self.queued.get(key)
            if joined is not None:
                return joined
            fut: cf.Future[object] = cf.Future()
            self.pending.append((fut, key, fn))
            self.queued[key] = fut
            self.jobs.add(fut)
            fut.add_done_callback(self._forget)
            if self.worker is None:
                self.worker = threading.Thread(target=self._work, name=f"wsd-lane-{self.name}")
                self.worker.start()
            self.ready.notify()
            return fut

    def _forget(self, fut: cf.Future[object]) -> None:
        with self.lock:
            self.jobs.discard(fut)

    def _work(self) -> None:
        while True:
            with self.lock:
                while not self.pending and not self.closed:
                    self.ready.wait()
            with self.shutdown, self.lock:  # every lane closes under `shutdown`: all of them, or none
                if self.closed:
                    return
                if not self.pending:
                    continue
                fut, key, fn = self.pending.popleft()
                del self.queued[key]        # started: a later request queues a new job
                if not fut.set_running_or_notify_cancel():
                    continue
            try:
                result = fn()
            except BaseException as exc:  # noqa: BLE001 - handed to whoever awaits the job
                fut.set_exception(exc)
            else:
                fut.set_result(result)

    def close(self) -> set[cf.Future[object]]:
        """Admit nothing more, cancel every job not started, and return the one still running (if any)."""
        with self.lock:
            self.closed = True
            cancelled = [fut for fut, _, _ in self.pending]
            self.pending.clear()
            self.queued.clear()
            running = {f for f in self.jobs if f not in cancelled}
            self.ready.notify_all()
        for fut in cancelled:
            fut.cancel()                    # its callbacks run outside the lock
        return {f for f in running if not f.done()}


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
    def __init__(self, s: WsdSettings, parts: Parts, load: Callable[[], WsdSettings] = _resolve) -> None:
        self.s = s
        self.parts = parts
        self.load = load            # `wsctl reload`'s settings: plan 1's loader
        self.recovered: set[str] = set()
        self.regated: set[str] = set()      # re-gated since this process started (AU-4 §5.1); memory only
        self.reloads = 0                    # reload requests so far: each one's jobs get their own key
        self.reloading: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
        self.stopping = False
        self.shutdown = threading.Lock()        # held while every lane closes; see Lane
        self.lanes = {name: Lane(name, self.shutdown) for name in [STATUS_LANE, *parts.schedulers]}
        self.wanted_pause: dict[str, bool] = {}     # the last pause or resume asked, per workstream
        self.wakes: dict[str, asyncio.TimerHandle] = {}     # the armed quota wake, per workstream
        self.waking: set[asyncio.Task[None]] = set()        # wake pickups not yet finished

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
        """A recovery, then, after the process's first successful one, the startup re-gate (AU-4 §5.1). A
        re-gate that raises leaves the workstream unrecovered and not re-gated: the next trigger does both."""
        self.recovered.discard(name)
        result = recover(self.parts.schedulers[name])
        if result.ok:
            if name not in self.regated:
                self.parts.schedulers[name].regate_all()
                self.regated.add(name)
            self.recovered.add(name)
        return result

    def recover_one(self, name: str) -> Recovered:
        """A recovery that fails, or raises, leaves the workstream to be recovered again first."""
        return self._locked(name, lambda: self._recover(name))

    def pickup_one(self, name: str, trigger: Trigger) -> Outcome:
        """Pickup, after a recovery if this workstream's last one did not succeed. Blocking: events reach
        it through `pickup`, on the workstream's lane, never through Scheduler.pickup directly."""
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

    def reload_one(self, name: str, ws: WorkstreamSettings) -> dict[str, str]:
        """`wsctl reload` on one workstream's lane (AU-4 §5.2): swap its settings, then re-gate its
        `account_changed` waits, after a recovery if its last one did not succeed."""
        def work() -> dict[str, str]:
            sched = self.parts.schedulers[name]
            sched.reconfigure(ws)
            if name not in self.recovered and not self._recover(name).ok:
                return {"outcome": Outcome.HELD.value}
            counts = sched.regate_all()
            return {"over": str(counts.over), "requota": str(counts.requota),
                    "unchanged": str(counts.unchanged)}
        return self._locked(name, work)

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

    def apply_pause_state(self, name: str) -> tuple[bool, CtlReply]:
        """Apply the pause or resume asked last (read as the job starts, so after every request that
        joined it), and say which one it was."""
        paused = self.wanted_pause[name]
        return paused, self.set_pause(name, paused)

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

    async def _run[T](self, lane: str, key: Hashable, fn: Callable[[], T]) -> T:
        """Run blocking work on a lane, or join the job queued there under `key` and not yet started, and
        wait for it. Cancelling the caller leaves the job to its lane (a thread can't be stopped; shutdown
        drains it). Refused, and a job that shutdown cancelled before it started reads, as Stopping;
        unless the caller was cancelled as well, which it stays (a timer would carry on from Stopping)."""
        if self.stopping:
            raise Stopping
        fut = self.lanes[lane].submit(key, fn)
        if lane != STATUS_LANE:
            self._arm_on(lane, fut)
        try:
            return await asyncio.shield(asyncio.wrap_future(fut))  # type: ignore[return-value]
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if fut.cancelled() and not (task and task.cancelling()):
                raise Stopping from None
            raise

    # --- quota wakes (AU-5 §3.6) ---

    def _arm_on(self, name: str, fut: cf.Future[Any]) -> None:
        """Arm the workstream's wake when its job finishes, whether or not anyone still awaits it. Added
        before the caller's own callback, so the wake is armed before the caller resumes."""
        loop = asyncio.get_running_loop()

        def done(f: cf.Future[Any]) -> None:
            if not f.cancelled():
                with contextlib.suppress(RuntimeError):     # the loop has closed: nothing to wake
                    loop.call_soon_threadsafe(self._arm, name)

        fut.add_done_callback(done)

    def _arm(self, name: str) -> None:
        """Replace the workstream's pending wake with its scheduler's `wake_at`, if any."""
        pending = self.wakes.pop(name, None)
        if pending is not None:
            pending.cancel()
        sched = self.parts.schedulers[name]
        if self.stopping or sched.wake_at is None:
            return
        loop = asyncio.get_running_loop()
        self.wakes[name] = loop.call_later(max(0, sched.wake_at - sched.d.clock()), self._wake, name)

    def _wake(self, name: str) -> None:
        self.wakes.pop(name, None)
        if self.stopping:
            return
        task = asyncio.get_running_loop().create_task(self._wake_pickup(name))
        self.waking.add(task)
        task.add_done_callback(self.waking.discard)

    async def _wake_pickup(self, name: str) -> None:
        with contextlib.suppress(JobFailed, Stopping):      # recorded, or shutting down
            await self.pickup(name, Trigger(TriggerKind.QUOTA_WAKE))

    async def _cancel_wakes(self) -> None:
        for handle in self.wakes.values():
            handle.cancel()
        self.wakes.clear()
        tasks = list(self.waking)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

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
            return await self._pause_state(req.ws, req.op == "pause")
        if req.op == RELOAD:
            return await self._reload()
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

    def _reload_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self.reloading is None or self.reloading[0] is not loop:
            self.reloading = (loop, asyncio.Lock())
        return self.reloading[1]

    async def _reload(self) -> CtlReply:
        """One reload at a time, start to finish: a second one loads and compares only after the first
        has swapped every lane and set `self.s`, and its jobs never join the first one's (each reload's
        jobs have their own key), so every acknowledged snapshot is the one applied."""
        async with self._reload_lock():
            return await self._reload_once()

    async def _reload_once(self) -> CtlReply:
        """Load the settings again, refuse anything but `accounts`, `usage` and `models` changing (O3), then
        swap and re-gate on every lane, each under its operation lock. `self.s` becomes the new settings
        once every lane has swapped, so the next reload compares against them. A lane whose recovery
        failed swapped but did not re-gate: the reply is `failed`, and the operator reloads again once it
        recovers (nothing else re-gates it; AU-4 §5.1)."""
        try:
            loaded = self.load()
        except ConfigError as exc:
            return CtlReply("refused", f"reload refused: {exc}; nothing was changed")
        running = dataclasses.replace(self.s, workstreams=tuple(
            sched.ws for sched in self.parts.schedulers.values()))
        changed = restart_fields(restart_view(running), restart_view(loaded))
        if changed:
            return CtlReply("refused", f"restart required: {', '.join(changed)}; nothing was changed")
        streams = {w.name: w for w in loaded.workstreams}
        names = list(self.parts.schedulers)
        self.reloads += 1
        key = (RELOAD, self.reloads)
        done = await asyncio.gather(*(self._run(name, key, lambda n=name: self.reload_one(n, streams[n]))
                                      for name in names), return_exceptions=True)
        self.s = loaded
        results: dict[str, dict[str, str]] = {}
        failed: list[str] = []
        for name, counts in zip(names, done, strict=True):
            if isinstance(counts, Stopping) or (isinstance(counts, BaseException)
                                                and not isinstance(counts, Exception)):
                raise counts
            if isinstance(counts, Exception):
                failed.append(f"{name}: {type(counts).__name__}")
                results[name] = {"outcome": "failed", "error": type(counts).__name__}
            else:
                if counts.get("outcome") == Outcome.HELD.value:
                    failed.append(f"{name}: recovery failed")
                results[name] = counts
        if failed:
            return CtlReply("failed", f"reload applied, but re-gating did not finish ({'; '.join(failed)}); "
                                      "run wsctl reload again once recovered", results)
        return CtlReply("ok", "reload done", results)

    async def _pause_state(self, name: str, paused: bool) -> CtlReply:
        """Ask for a pause or a resume. Asked before the lane's pause-state job starts, it joins that job,
        which applies the last one asked. The reply is that job's, which says what was published; a caller
        whose request a later one overtook is told so, and is never told its own request took effect."""
        if self.stopping:
            raise Stopping
        self.wanted_pause[name] = paused        # before joining: the job reads it only once started
        applied, reply = await self._run(name, PAUSE_STATE, lambda: self.apply_pause_state(name))
        if applied == paused or reply.result != "ok":
            return reply
        asked, now = ("pause", "resumed") if paused else ("resume", "paused")
        return CtlReply("refused", f"{name}: your {asked} was overtaken by a later request before it ran; "
                                   f"{name} is {now}. {reply.message}", reply.data)

    async def _job(self, name: str, kind: str, job: Callable[[str], Outcome],
                   ref: str | None = None) -> Outcome:
        """A pickup or reconcile on the workstream's lane. Requests share a job only if they share both
        the kind and the trigger's ref: one event's ref is never recorded as another's."""
        return await self._run(name, (kind, ref), lambda: self._guarded(job, name))

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

    async def _every(self, name: str, seconds: float, kind: str, job: Callable[[str], Outcome]) -> None:
        """One workstream's timer: its ticks wait on its own jobs only."""
        while True:
            await asyncio.sleep(seconds)
            with contextlib.suppress(JobFailed, Stopping):      # recorded, or shutting down: carry on
                await self._job(name, kind, job)

    async def pickup(self, name: str, trigger: Trigger) -> Outcome:
        """The event entry point from the loop (plan 6): `pickup_one` on the workstream's lane, sharing a
        queued job only with requests of the same ref. Raises JobFailed (recorded) or Stopping."""
        return await self._job(name, "pickup", lambda n: self.pickup_one(n, trigger), trigger.ref)

    def _backstop(self, name: str) -> Outcome:
        return self.pickup_one(name, Trigger(TriggerKind.BACKSTOP))

    async def _startup(self) -> None:
        """`startup`, on the lanes: every workstream's recovery, and only then the startup pickups."""
        recoveries: list[Coroutine[Any, Any, object]] = [
            self._run(name, "recover", lambda n=name: self.recover_one(n)) for name in self.parts.schedulers]
        await asyncio.gather(*recoveries)
        pickups: list[Coroutine[Any, Any, object]] = [
            self._run(name, ("pickup", None), lambda n=name: self.pickup_one(n, Trigger(TriggerKind.STARTUP)))
            for name in self.parts.schedulers]
        await asyncio.gather(*pickups)

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
            await self._cancel_wakes()
            await self._drain()
            if failed is not None:
                raise failed
            return
        server = CtlServer(self.s.socket, self.handle)
        timers: list[asyncio.Task[None]] = []
        stopping = asyncio.create_task(stop.wait())
        try:
            await server.start()        # inside: a socket that fails to start still closes every lane
            if stop.is_set():
                return                  # stopped while the socket started: no timer starts
            timers = [asyncio.create_task(self._every(name, seconds, kind, job))
                      for name in self.parts.schedulers
                      for seconds, kind, job in ((self.s.backstop_seconds, "pickup", self._backstop),
                                                 (self.s.reconcile_seconds, "reconcile", self.reconcile_one))]
            await asyncio.wait([stopping, *timers], return_when=asyncio.FIRST_COMPLETED)
            for task in timers:
                if task.done():
                    task.result()       # a timer only ends by raising: wsd must not run on without it
        finally:
            self._cancel_queued()       # before any await: from here no request, timer or queued job starts
            server.stop_accepting()
            for task in (stopping, *timers):
                task.cancel()
            await asyncio.gather(stopping, *timers, return_exceptions=True)
            await self._cancel_wakes()
            try:
                await self._drain()
            finally:
                await server.close()    # the handlers waiting on drained jobs have their replies now

    def _cancel_queued(self) -> set[cf.Future[object]]:
        """Stop admitting, cancel every job not yet started, and return those still running: on every lane
        at once, under `shutdown`, which each lane takes before it starts a job."""
        with self.shutdown:
            self.stopping = True
            return {f for ln in self.lanes.values() for f in ln.close()}

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
