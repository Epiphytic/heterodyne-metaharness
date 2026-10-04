# heterodyne-metaharness Plan 3: wsd core A, queue and state — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the deterministic core of `wsd`, the workstream daemon:

- a SQLite journal and inbox (§3.3);
- a beads adapter that works through btq's `Queue` as agent `wsd`, with one workstream-session worker per workstream and one per-bead worker per claimed bead (§4.3);
- the shared pause gate and the per-workstream claim lock (§4.3);
- pickup, which never leaves a workstream idle while an unblocked bead exists (§5.2);
- the park and resume journals (§3.3, §4.3);
- the startup recovery order (§3.3);
- the seams that plans 4, 5 and 6 plug into: `AgentRuntime`, `ActionReconciler`, `Parker.park`, the inbox and the progress events.

**Architecture:** every multi-step change to the queue, a worktree or an agent session is an *operation* in the journal (`ops`): intent first, then one recorded step at a time, each step idempotent (check, write, read back). A crash anywhere is replayed from the step reached, and each step has a named checkpoint so tests can crash or pause there deterministically. Beads stay the source of truth: recovery rebuilds the journal's bead states from beads and the runtime, and anything it can't read holds the workstream. Pickup, park, resume and recovery are blocking code run per workstream under one lock; a small asyncio daemon runs them on startup, on timers and on control-socket requests.

**Tech Stack:** Python 3.12+, asyncio, sqlite3 (stdlib), msgspec (the only runtime dependency), btq's `Queue` loaded from `$BTQ_REPO/bin/btq`, git; pytest, hypothesis, ruff, pyright (strict on `src/`).

**Spec:** ADR 0001 revision 13 is design-repo commit `66b3aecb639e6ec56f108e2f55d483d4dedda485` in `$DESIGN_REPO`, approved in bead `btq-5ky39`. Section numbers (§) refer to it. Roadmap row 3 (`docs/superpowers/plans/2026-09-29-heterodyne-v1-roadmap.md`) scopes this plan. The progress-event requirement (waiting-on-input, held and stuck with concrete reasons, and per-message progress) follows the pending amendment `btq-xv48a`; this plan only records them, plan 6 renders them.

## Global Constraints

- **Variables:** `$HZ` is a fresh clone of `Epiphytic/heterodyne-metaharness` (never the live harness or a symlink to it), `$BTQ_REPO` the beads-task-queue checkout providing `bin/btq`, `$DESIGN_REPO` the design repo. "Install-agnostic: no install paths, npubs, tokens or relay URLs. Use the roadmap variables ($HZ, $BTQ_REPO, $DESIGN_REPO)." `scripts/check_install_agnostic.py` stays clean.
- "Python 3.12+, matching the repo's tooling (pytest, hypothesis, ruff, pyright)." Managed with `uv`. Runtime dependencies stay exactly `msgspec`. Async tests use `asyncio.run` inside sync test functions; no pytest-asyncio.
- "Tests never touch the network, a real wn-agent, the real systemctl, the real claude or codex binaries, ~/.claude, or the real beads database. Use fake executables and temp dirs." The queue is `tests/fakes/fake_btq.py`; the one contract test that loads the real `$BTQ_REPO/bin/btq` replaces its `bd` with a fake executable and points `HOME` at `tmp_path`, and is skipped when `BTQ_REPO` is unset.
- "Nothing may be specific to Claude or Codex." No adapter, model or CLI name appears in `src/heterodyne/wsd/`. Test profiles are made-up names (`p-one`, `p-two`).
- "Every recovery path fails closed. Never infer absent, complete or safe from missing or unreadable evidence." Concretely: an unreadable pause flag reads as paused; an unreadable claim holds the workstream; `Liveness.UNKNOWN` is never treated as dead; a missing `dependencies` key with a non-zero count is a malformed answer, not "no blockers"; a corrupt journal stops wsd and is left in place.
- "Use crash-window and interleaving tests (deterministic checkpoints) for every journaled transition." Every journaled step calls a named checkpoint (`POINTS`, `PARK_POINTS`, `RESUME_POINTS`, `RECOVERY_POINTS`, `gate.checked`); tests crash at each one (`CrashAt`) and replay, or pause at one (`PauseAt`) to force an interleaving.
- **The beads adapter goes through btq's interfaces, not raw bd.** wsd loads btq's `Queue` in-process. Labels, dependencies, comments and metadata have no `Queue` method, so they go through `Queue.bd()` of the per-bead worker, after `Queue.owned()` confirms the claim, under that worker's `Queue.exclusive()` lock. wsd never execs `bd` itself.
- **wsd never unclaims** (§4.3). A parked bead stays `in_progress` under its per-bead worker.
- **`src/` style:** ruff line length 110; pyright strict; `sys.platform` only in `src/heterodyne/platform.py`; error messages name keys, never secret values.
- **Review rule:** every task ends with a review by a **different LLM than the implementer** (cross-model), or by a fresh-context adversarial agent when only one LLM is available. The brief names the diff range and the ADR sections and asks for `[BLOCKING]`/`[NON-BLOCKING]` findings; fix or rebut every blocking one. Close evidence includes `Code-Review: reviewer=<model> author=<model> mode=<cross-model|adversarial> range=<BASE>..<HEAD>`.
- **Beads** (workstream `heterodyne`): Tasks 1–9 are `kind:task`, each with `metadata.design_approval=btq-5ky39`, `metadata.adr_revision=66b3aecb639e6ec56f108e2f55d483d4dedda485` and a blocking dependency on `btq-5ky39`. They run in order 1 → 9, each blocked by the one before.
- **Branches:** integration branch `plan-3-wsd` in `$HZ`, from `main`. One commit (or more) per task; never amend a pushed commit.

---

## Decisions made in this plan (within the ADR; reviewers should check them)

| # | Where the ADR is silent or loose | Choice | Why |
|---|---|---|---|
| D1 | §4.1/§4.3 use `uuid5(NS, …)` without fixing `NS`. | `NS = uuid5(NAMESPACE_URL, "urn:heterodyne:wsd")`, a constant in `ids.py` that must never change. | Deterministic and install-independent; a changed NS would orphan every claim and session. |
| D2 | btq's `Queue` has no call for labels, dependencies, comments or metadata. | `Queue.bd()` of the per-bead worker, after `Queue.owned()`, under `Queue.exclusive()`; every write is check → write → read back. | Keeps to btq's interface (the scope says "not raw bd"), and S6 shows `bd label add` exits 0 even on a missing bead, so only a read-back is evidence. |
| D3 | How `/stop` is represented on the bead. | A park with `hold=True`: `v2:held` plus `v2:parked`, no blocker needed. A `v2:held` bead is never resumable; plan 6's release removes `v2:held`. | Keeps "parked" one mechanism; the operator hold is visible on the bead, not only in the journal. |
| D4 | When a parked bead is "waiting on input" rather than "blocked on a bead". | When an open blocker carries `kind:approval`, `kind:question` or `kind:confirm` (`OPERATOR_INPUT_LABELS`). | Those are the operator-ask bead kinds of §5; plans 5–7 create them. |
| D5 | Which repository a bead's worktree comes from; which profile runs it. | `metadata.repo` names one of the workstream's `[repos]` (default `default`); a single `role:<role>=<profile>` label overrides the configured coder profile (§15 layer 4). | Both are per-bead data in beads, so recovery can recompute them. |
| D6 | Plan 5 owns action reconciliation (§5.4), but recovery step 3 runs now. | `HoldingReconciler`: any unclosed bead labelled `ws:<ws>` with `metadata.action_state` `executing` or `uncertain` holds the workstream (`actions_unreconciled`). Plan 5 replaces it. | Fails closed: plan 3 can't check targets, so it never assumes there is nothing to reconcile. |
| D7 | Recovery scope. | Recovery runs per workstream. A workstream whose recovery fails stays `held` and is recovered again before its next pickup; the others carry on. | One unreachable dependency must not stop unrelated workstreams. |
| D8 | Lost journal during a park. | Recovery finishes a park cut short (blocking edge present, `v2:parked` absent) when the session is dead. Because the WIP mark is per operation, this may add a second, possibly empty, WIP commit. | Harmless and visible in git; the alternative (guessing that an earlier WIP commit was this park's) infers from incomplete evidence. |
| D9 | Until plan 4 there is no agent runtime. | `NoRuntime` reports unavailable; every workstream is held `runtime_unavailable` and nothing is claimed. | Never claim work that can't run. |
| D10 | One coder session per workstream (§4.3) when a stop can't be confirmed. | A bead that is `parking` or `stuck`, or closed or lost with an unconfirmed stop, keeps the coder role while its session's liveness is not `dead`. The session key is recomputed from the bead's ID and labels, so a bead whose repository vanished still counts. | "Never infer safe": a session that may be live is treated as live. |
| D11 | What "never idle" means operationally (§5.2). | Pickup tries every candidate in turn (resumable first, then ready) until one starts; a refused, lost or failed candidate never ends pickup while another remains. A claim that did not land becomes a `beads_unreachable` hold, not idle. | Tested by a hypothesis property over random queues and faults. |
| D12 | Pause and resumable beads. | Pause blocks new claims only; a parked bead whose blockers closed still resumes while paused. | §4.3: "Pausing stops new claims only. Running and parked beads continue." |
| D13 | `[integrations.beads]` keys. | `btq` (the checkout) plus btq's optional locations (`config_dir`, `repo`, `dolt_host`, `dolt_port`, `dolt_database`, `tls_cert`) and `credentials = { file = … }`. Unset ones fall back to btq's `BTQ_*` environment. | Uses the table plan 1 reserved; credentials are only ever a file reference. |
| D14 | btq's state directory (`paused` flag, worker state) is under `Path.home()`. | wsd uses btq's location unchanged; tests that load the real btq set `HOME` to `tmp_path`. | btq owns that convention; the pause flag must be the one `btq pause` sets. |
| D15 | Callers of `Parker.park` (plans 4 and 6). | `park` raises `BeadsUnavailable` when beads can't be reached; the open park journal is replayed by the next pickup. Callers keep their request and report it as pending. | An outage never escalates a bead. |
| D16 | Approval beads for `HoldingReconciler` (plan 5). | Found by the `ws:<ws>` label. | Plan 5 must keep that label on approval beads (flagged in the seams doc). |

## ADR conflicts and gaps, flagged for the operator (not silently resolved)

1. **`/pause` in §6.3 vs §4.3.** §6.3's command table says `/pause` "stops or restarts pickup. The running task finishes its turn and is then parked." §4.3 says "Pausing stops new claims only. Running and parked beads continue, unless the operator uses `/stop`." This plan implements §4.3. Plan 6 owns `/pause` and should get the ADR text reconciled first.
2. **The park sequence.** §4.3 lists "record intent, commit WIP (recording the SHA), apply the label, add a bead comment". This plan adds two steps: **stop the session** before the WIP commit (otherwise the agent keeps writing into the worktree being committed), and **add the blocking edges** before the label (so a bead is never `v2:parked` without what keeps it from resuming). It is an elaboration, not a change of outcome, but a reviewer may want it written into the ADR.
3. **`/stop` representation** (D3) and **waiting-on-input detection** (D4) are not in the ADR. They are plan-level choices that plans 6 and 7 will depend on.
4. **btq state under `HOME`** (D14). btq's per-worker state (and so the shared pause flag) lives under the service user's home. That is fine for one host user, but plan 4's synthetic home for agents must not be the home wsd runs with.
5. **Runtime availability.** Until plan 4 lands, a running `wsd` claims nothing (D9). That is intended, but it means no end-to-end run is possible between plans 3 and 4.
6. **Sandbox backend.** The roadmap's plan 4 names bubblewrap; the operator has since chosen OpenShell (ADR revision 14, pending). Nothing in this plan depends on the backend: `AgentRuntime` is backend-neutral.

## File map

| File | Responsibility | Task |
|---|---|---|
| `src/heterodyne/fsutil.py` | `private_dir` (moved from `admind/store.py`, shared by admind and wsd) | 1 |
| `src/heterodyne/admind/store.py` | imports `private_dir` from `fsutil` | 1 |
| `src/heterodyne/wsd/__init__.py` | package | 1 |
| `src/heterodyne/wsd/ids.py` | deterministic worker and session IDs (§4.1, §4.3) | 1 |
| `src/heterodyne/wsd/checkpoints.py` | the `Checkpoint` hook type | 1 |
| `src/heterodyne/wsd/states.py` | bead and workstream states, reasons, legal transitions | 2 |
| `src/heterodyne/wsd/journal.py` | the SQLite journal: inbox, operations, bead states, holds, progress events | 3 |
| `src/heterodyne/wsd/btq.py` | loading btq's `Queue`; the `QueueLike` protocol | 4 |
| `src/heterodyne/wsd/gitwip.py` | WIP commits for parks | 4 |
| `src/heterodyne/wsd/beads.py` | `BeadsAdapter`: reads, claim, owned writes, worktrees, pause flag | 4 |
| `docs/spikes/S6-beads-json.md` | the bd 1.1 JSON shapes the adapter accepts | 4 |
| `src/heterodyne/wsd/gate.py` | `ClaimGate` (claim lock, pause), `instance_lock` | 5 |
| `src/heterodyne/wsd/runtime.py` | `AgentRuntime`, `NoRuntime`, `ActionReconciler`, `HoldingReconciler` | 5 |
| `src/heterodyne/wsd/workstream.py` | per-workstream settings, `place()`, `Deps` | 6 |
| `src/heterodyne/wsd/park.py` | `Parker`: park and resume journals | 6 |
| `src/heterodyne/wsd/scheduler.py` | `Scheduler`: pickup and the pickup journal | 7 |
| `src/heterodyne/wsd/recovery.py` | `recover()`: the startup recovery order | 8 |
| `src/heterodyne/wsd/settings.py` | `[wsd]`, `[integrations.beads]` and workstream settings | 9 |
| `src/heterodyne/wsd/ctl.py` | the control socket | 9 |
| `src/heterodyne/wsd/daemon.py` | `Wsd`: startup, timers, control requests | 9 |
| `src/heterodyne/wsd/cli.py` | `wsd` and `wsctl` | 9 |
| `src/heterodyne/defaults/defaults.toml`, `pyproject.toml` | `[wsd]` defaults; console scripts | 9 |
| `docs/wsd.md`, `docs/configuration.md`, `docs/install.md`, `examples/config.toml` | operator docs | 9 |
| `tests/fakes/checkpoints.py` | `Recorder`, `CrashAt`, `PauseAt`, `SimulatedCrash` | 1 |
| `tests/fakes/fake_btq.py` | in-memory queue with btq's `Queue` interface and bd 1.1 shapes | 4 |
| `tests/fakes/fake_runtime.py` | recording `AgentRuntime` | 5 |
| `tests/wsd_env.py` | shared helpers (Task 4), the test rig (Task 6), rig pickup (Task 7) | 4, 6, 7 |
| `tests/test_wsd_*.py` | one test file per module, plus `test_wsd_pause.py` | 1–9 |

---
### Task 1: Scaffolding, identities and checkpoints

**Files:**
- Create: `$HZ/src/heterodyne/fsutil.py`, `$HZ/src/heterodyne/wsd/__init__.py`, `$HZ/src/heterodyne/wsd/ids.py`, `$HZ/src/heterodyne/wsd/checkpoints.py`, `$HZ/tests/fakes/checkpoints.py`
- Modify: `$HZ/src/heterodyne/admind/store.py` (move `private_dir` out)
- Test: `$HZ/tests/test_wsd_ids.py`

**Interfaces:**
- Consumes: nothing from this plan. `admind/store.py`'s existing `private_dir`.
- Produces:
  - `heterodyne.fsutil.private_dir(path: Path) -> None`.
  - `heterodyne.wsd.ids`: `NS: uuid.UUID`, `SLUG: re.Pattern`, `PROFILE: re.Pattern`, `ROLE_LABEL = "role:"`, `class BadName(ValueError)`, `slug(value: str, what: str) -> str`, `ws_session(ws: str) -> str`, `bead_session(ws: str, bead: str) -> str`, `role_session(bead: str, role: str, profile: str) -> str`, `profile_for(labels: tuple[str, ...], role: str, default: str) -> str`.
  - `heterodyne.wsd.checkpoints`: `Checkpoint = Callable[[str], None]`, `nothing(_name: str) -> None`.
  - `tests/fakes/checkpoints.py`: `SimulatedCrash(BaseException)`, `Recorder` (`.seen: list[str]`), `CrashAt(point)`, `PauseAt(point)` (`.reached`, `.go`: `threading.Event`).

- [ ] **Step 1: Create `$HZ/tests/fakes/checkpoints.py`**

Checkpoints are how every crash-window and interleaving test reaches an exact point. `SimulatedCrash` is a `BaseException`, so no `except Exception` in wsd can swallow it.

```python
"""Deterministic checkpoints for crash-window and interleaving tests.

wsd calls `cp(name)` right after each journaled step. `CrashAt` raises `SimulatedCrash` (a BaseException,
so no `except Exception` in wsd can swallow it) the first time a named point is reached, which models the
process dying at exactly that point. `PauseAt` parks the calling thread at a point until the test lets it go.
"""

import threading


class SimulatedCrash(BaseException):
    pass


class Recorder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, name: str) -> None:
        self.seen.append(name)


class CrashAt(Recorder):
    def __init__(self, point: str) -> None:
        super().__init__()
        self.point = point
        self.fired = False

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point and not self.fired:
            self.fired = True
            raise SimulatedCrash(name)


class PauseAt(Recorder):
    def __init__(self, point: str) -> None:
        super().__init__()
        self.point = point
        self.reached = threading.Event()
        self.go = threading.Event()

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point and not self.reached.is_set():
            self.reached.set()
            if not self.go.wait(10):
                raise TimeoutError(name)
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_ids.py`**

```python
import uuid

import pytest

from heterodyne.wsd import ids


def test_session_ids_are_deterministic_uuid5() -> None:
    assert ids.ws_session("alpha") == str(uuid.uuid5(ids.NS, "alpha"))
    assert ids.bead_session("alpha", "btq-1") == str(uuid.uuid5(ids.NS, "alpha:btq-1"))
    assert ids.role_session("btq-1", "coder", "p-one") == str(uuid.uuid5(ids.NS, "btq-1:coder:p-one"))
    assert ids.bead_session("alpha", "btq-1") != ids.bead_session("beta", "btq-1")


def test_namespace_is_pinned() -> None:
    # Changing NS orphans every claim and session: this value must never change.
    assert str(ids.NS) == str(uuid.uuid5(uuid.NAMESPACE_URL, "urn:heterodyne:wsd"))


@pytest.mark.parametrize("bad", ["", "Alpha", "a b", "../x", "a/b", "-x", "x" * 129])
def test_bad_slugs_are_refused(bad: str) -> None:
    with pytest.raises(ids.BadName):
        ids.ws_session(bad)


def test_role_label_overrides_profile() -> None:
    assert ids.profile_for(("role:coder=p-two", "role:review=p-one"), "coder", "p-one") == "p-two"
    assert ids.profile_for((), "coder", "p-one") == "p-one"


def test_conflicting_role_labels_are_refused() -> None:
    with pytest.raises(ids.BadName):
        ids.profile_for(("role:coder=p-one", "role:coder=p-two"), "coder", "p-one")


def test_bad_profile_in_label_is_refused() -> None:
    with pytest.raises(ids.BadName):
        ids.profile_for(("role:coder=../x",), "coder", "p-one")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_ids.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd'`.

- [ ] **Step 4: Create `$HZ/src/heterodyne/fsutil.py`**

This is `admind/store.py`'s `private_dir`, moved unchanged so wsd can share it.

```python
"""Filesystem helpers shared by the daemons (admind, wsd)."""

import os
import stat
from pathlib import Path


def private_dir(path: Path) -> None:
    """Create `path` (and missing parents) and make it a 0700 directory owned by this user. A symlink in
    the final component, or a directory someone else owns, is refused: `path` is lstat'ed, opened with
    O_NOFOLLOW | O_DIRECTORY and the two are compared, so the chmod lands on the directory that was
    checked. Parents above `path` are not inspected."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = os.lstat(path)
    if not stat.S_ISDIR(before.st_mode):
        raise PermissionError(f"{path} is not a plain directory (a symlink is refused)")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise PermissionError(f"{path} changed while it was checked")
        if opened.st_uid != os.geteuid():
            raise PermissionError(f"{path} is not owned by the service user")
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
```

- [ ] **Step 5: Modify `$HZ/src/heterodyne/admind/store.py`**

Delete the whole `def private_dir(path: Path) -> None:` function (the code now in `fsutil.py`), and add this import after `from heterodyne.admind.redact import redact, redact_continuation`:

```python
from heterodyne.fsutil import private_dir
```

Keep `import os` and `import stat`: the rest of `store.py` still uses them. `Store` and everything else that calls `private_dir` keep working through the import.

- [ ] **Step 6: Create `$HZ/src/heterodyne/wsd/__init__.py`**

```python
"""wsd, the workstream daemon (ADR 0001 §3, §4.3, §5.2): queue, state, pickup, parking and recovery."""
```

- [ ] **Step 7: Create `$HZ/src/heterodyne/wsd/ids.py`**

```python
"""Deterministic identities (ADR 0001 §4.1, §4.3). Every key here is recomputed, never stored as truth,
so `wsd` finds its own claims and sessions again after a restart or a lost journal."""

import re
import uuid

# The namespace of every wsd uuid5. Changing it orphans every claim and session wsd holds: never change it.
NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:heterodyne:wsd")
SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")  # btq's routing and session slug rule
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ROLE_LABEL = "role:"


class BadName(ValueError):
    """A workstream, bead, role or profile name that is not a valid slug."""


def slug(value: str, what: str) -> str:
    if not SLUG.fullmatch(value):
        raise BadName(f"{what} is not a valid slug")
    return value


def ws_session(ws: str) -> str:
    """The workstream-session btq worker: it lists ready work and owns the shared pause flag."""
    return str(uuid.uuid5(NS, slug(ws, "workstream")))


def bead_session(ws: str, bead: str) -> str:
    """The per-bead btq worker that claims, owns and parks one bead."""
    return str(uuid.uuid5(NS, f"{slug(ws, 'workstream')}:{slug(bead, 'bead')}"))


def role_session(bead: str, role: str, profile: str) -> str:
    """The logical agent session key for (bead, role, profile) (§4.1)."""
    if not PROFILE.fullmatch(profile):
        raise BadName("profile is not a valid name")
    return str(uuid.uuid5(NS, f"{slug(bead, 'bead')}:{slug(role, 'role')}:{profile}"))


def profile_for(labels: tuple[str, ...], role: str, default: str) -> str:
    """The profile for `role` on a bead: a single `role:<role>=<profile>` label overrides the configured
    default (§4.1, §15 layer 4). Two different overrides for the same role are ambiguous: refused."""
    prefix = f"{ROLE_LABEL}{role}="
    chosen = {label[len(prefix):] for label in labels if label.startswith(prefix)}
    if len(chosen) > 1:
        raise BadName(f"bead has conflicting role:{role} labels")
    profile = chosen.pop() if chosen else default
    if not PROFILE.fullmatch(profile):
        raise BadName("profile is not a valid name")
    return profile
```

- [ ] **Step 8: Create `$HZ/src/heterodyne/wsd/checkpoints.py`**

```python
"""Named crash windows. Every journaled flow calls its checkpoint between steps; production passes
`nothing`, and tests pass a callable that raises or blocks at a chosen name (crash-window and
interleaving tests). Each flow lists its names in a `POINTS` tuple, and a test checks that a clean run
passes exactly those, so a new window can't be added without a test that crashes in it."""

from collections.abc import Callable

Checkpoint = Callable[[str], None]


def nothing(_name: str) -> None:
    return None
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_ids.py tests/test_admind_store.py -q`
Expected: `24 passed`.

- [ ] **Step 10: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 11: Commit**

```bash
cd $HZ
git add src/heterodyne/fsutil.py src/heterodyne/admind/store.py src/heterodyne/wsd tests/fakes/checkpoints.py tests/test_wsd_ids.py
git commit -m "feat(wsd): deterministic worker and session IDs, test checkpoints; share private_dir"
```

### Task 2: State model

**Files:**
- Create: `$HZ/src/heterodyne/wsd/states.py`
- Test: `$HZ/tests/test_wsd_states.py`

**Interfaces:**
- Consumes: nothing.
- Produces (`heterodyne.wsd.states`):
  - `class BeadState(StrEnum)`: `CLAIMING, STARTING, RUNNING, PARKING, PARKED, WAITING_INPUT, HELD, STUCK, RESUMING, CLOSED, DROPPED`.
  - `class Reason(StrEnum)`: `CLAIM_UNCERTAIN, CLAIM_LOST, CLAIM_ABANDONED, ROUTING_CHANGED, WORKTREE_FAILED, LAUNCH_FAILED, SESSION_DEAD, RUNTIME_UNAVAILABLE, PARK_FAILED, BLOCKED_ON_BEAD, WAITING_ON_OPERATOR, HELD_BY_OPERATOR, NEEDS_HUMAN, BEADS_UNREACHABLE, ACTIONS_UNRECONCILED, CONFIG_INVALID, UNEXPECTED_STATE`.
  - `class WsState(StrEnum)`: `RUNNING, IDLE, ALL_BLOCKED, PAUSED, HELD, STUCK`.
  - `TERMINAL`, `ACTIVE`, `WAITING`: `frozenset[BeadState]`; `ALLOWED: dict[BeadState | None, frozenset[BeadState]]`.
  - `class IllegalTransition(Exception)`, `allowed(src: BeadState | None, dst: BeadState) -> bool`, `check(src, dst) -> None` (raises `IllegalTransition`).
  - `ws_state(paused: bool, holds: Iterable[Reason], beads: Iterable[BeadState]) -> WsState`.

- [ ] **Step 1: Create `$HZ/tests/test_wsd_states.py`**

```python
import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.wsd.states import (
    ACTIVE,
    ALLOWED,
    TERMINAL,
    BeadState,
    IllegalTransition,
    Reason,
    WsState,
    allowed,
    check,
    ws_state,
)


def test_every_state_has_a_row() -> None:
    assert set(ALLOWED) == set(BeadState) | {None}


def test_every_live_state_can_close() -> None:
    for state in set(BeadState) - TERMINAL:
        assert allowed(state, BeadState.CLOSED)


def test_terminal_states_only_reclaim() -> None:
    for state in TERMINAL:
        assert ALLOWED[state] == {BeadState.CLAIMING}


def test_claiming_never_jumps_to_running() -> None:
    # A claim is never executed until it has been read back as ours and a worktree exists.
    with pytest.raises(IllegalTransition):
        check(BeadState.CLAIMING, BeadState.RUNNING)


def test_held_bead_never_resumes_directly() -> None:
    # /stop holds a bead until the operator releases it: HELD -> RESUMING is not a transition.
    assert not allowed(BeadState.HELD, BeadState.RESUMING)


@pytest.mark.parametrize(("paused", "holds", "beads", "expected"), [
    (False, [], [], WsState.IDLE),
    (False, [], [BeadState.CLOSED, BeadState.DROPPED], WsState.IDLE),
    (False, [], [BeadState.PARKED], WsState.ALL_BLOCKED),
    (False, [], [BeadState.PARKED, BeadState.STUCK], WsState.STUCK),
    (False, [], [BeadState.STUCK, BeadState.RUNNING], WsState.RUNNING),
    (True, [], [BeadState.RUNNING], WsState.PAUSED),
    (True, [Reason.BEADS_UNREACHABLE], [BeadState.RUNNING], WsState.HELD),
])
def test_ws_state_priority(paused: bool, holds: list[Reason], beads: list[BeadState],
                           expected: WsState) -> None:
    assert ws_state(paused, holds, beads) is expected


@given(st.booleans(), st.lists(st.sampled_from(list(BeadState))))
def test_idle_only_when_nothing_is_live(paused: bool, beads: list[BeadState]) -> None:
    state = ws_state(paused, [], beads)
    if state is WsState.IDLE:
        assert not set(beads) - TERMINAL
    if set(beads) & ACTIVE and not paused:
        assert state is WsState.RUNNING
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_states.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.states'`.

- [ ] **Step 3: Create `$HZ/src/heterodyne/wsd/states.py`**

```python
"""The bead and workstream state model (ADR 0001 §4.3, §5.2) as data plus pure functions.

The journal records one `BeadState` per bead with a reason from a fixed vocabulary, so the Marmot
surface (plan 6) always has a concrete answer to "what is this bead doing, and why". Beads stay the
source of truth (§3.3): these states are rebuilt from beads when the journal is lost.
"""

from collections.abc import Iterable
from enum import StrEnum


class BeadState(StrEnum):
    CLAIMING = "claiming"            # a claim is in flight; never executed until read back as ours
    STARTING = "starting"            # claimed; worktree and launch in progress
    RUNNING = "running"              # an agent session is working on it
    PARKING = "parking"              # the park journal is in progress
    PARKED = "parked"                # waits on blocking beads (claimed, in_progress, v2:parked)
    WAITING_INPUT = "waiting_input"  # parked on an operator question, picker or permission prompt
    HELD = "held"                    # parked by the operator (/stop); resumes only when they release it
    STUCK = "stuck"                  # needs a human; the reason says why
    RESUMING = "resuming"            # the resume journal is in progress
    CLOSED = "closed"
    DROPPED = "dropped"              # not ours: the claim was lost or abandoned


class Reason(StrEnum):
    """Why a bead or a workstream is in its state. Fixed wording lives with the renderer (plan 6)."""
    CLAIM_UNCERTAIN = "claim_uncertain"
    CLAIM_LOST = "claim_lost"
    CLAIM_ABANDONED = "claim_abandoned"
    ROUTING_CHANGED = "routing_changed"
    WORKTREE_FAILED = "worktree_failed"
    LAUNCH_FAILED = "launch_failed"
    SESSION_DEAD = "session_dead"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    PARK_FAILED = "park_failed"
    BLOCKED_ON_BEAD = "blocked_on_bead"
    WAITING_ON_OPERATOR = "waiting_on_operator"
    HELD_BY_OPERATOR = "held_by_operator"
    NEEDS_HUMAN = "needs_human"
    BEADS_UNREACHABLE = "beads_unreachable"
    ACTIONS_UNRECONCILED = "actions_unreconciled"
    CONFIG_INVALID = "config_invalid"
    UNEXPECTED_STATE = "unexpected_state"


class WsState(StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    ALL_BLOCKED = "all_blocked"
    PAUSED = "paused"
    HELD = "held"      # pickup is held for a workstream-level reason (see `holds`)
    STUCK = "stuck"    # nothing runs and at least one bead needs a human


TERMINAL = frozenset({BeadState.CLOSED, BeadState.DROPPED})
ACTIVE = frozenset({BeadState.CLAIMING, BeadState.STARTING, BeadState.RUNNING, BeadState.PARKING,
                    BeadState.RESUMING})
WAITING = frozenset({BeadState.PARKED, BeadState.WAITING_INPUT, BeadState.HELD})
_PARKED_LIKE = WAITING | {BeadState.STUCK}

# Every non-terminal state may also go to CLOSED: anyone with the right can close a bead at any time.
ALLOWED: dict[BeadState | None, frozenset[BeadState]] = {
    # A fresh journal learns beads from beads (§3.3), in whatever state they are.
    None: frozenset(BeadState) - TERMINAL,
    BeadState.CLAIMING: frozenset({BeadState.STARTING, BeadState.DROPPED, BeadState.STUCK, BeadState.CLOSED}),
    BeadState.STARTING: frozenset({BeadState.RUNNING, BeadState.PARKING, BeadState.STUCK, BeadState.CLOSED}),
    BeadState.RUNNING: frozenset({BeadState.PARKING, BeadState.RESUMING, BeadState.STUCK, BeadState.CLOSED}),
    BeadState.PARKING: frozenset({BeadState.PARKED, BeadState.WAITING_INPUT, BeadState.HELD,
                                  BeadState.STUCK, BeadState.CLOSED}),
    BeadState.PARKED: _PARKED_LIKE | {BeadState.RESUMING, BeadState.CLOSED},
    BeadState.WAITING_INPUT: _PARKED_LIKE | {BeadState.RESUMING, BeadState.CLOSED},
    BeadState.HELD: _PARKED_LIKE | {BeadState.CLOSED},
    BeadState.RESUMING: frozenset({BeadState.RUNNING, BeadState.PARKED, BeadState.WAITING_INPUT,
                                   BeadState.HELD, BeadState.STUCK, BeadState.CLOSED}),
    BeadState.STUCK: _PARKED_LIKE | {BeadState.RESUMING, BeadState.RUNNING, BeadState.CLOSED},
    BeadState.CLOSED: frozenset({BeadState.CLAIMING}),   # a reopened bead can be claimed again
    BeadState.DROPPED: frozenset({BeadState.CLAIMING}),
}


class IllegalTransition(Exception):
    pass


def allowed(src: BeadState | None, dst: BeadState) -> bool:
    """A same-state "transition" only updates the reason, and is always allowed."""
    return src == dst or dst in ALLOWED[src]


def check(src: BeadState | None, dst: BeadState) -> None:
    if not allowed(src, dst):
        raise IllegalTransition(f"{src} -> {dst}")


def ws_state(paused: bool, holds: Iterable[Reason], beads: Iterable[BeadState]) -> WsState:
    """The one-word workstream state for /workstreams (§6.3). Pickup has already run, so "idle" really
    means nothing is ready and nothing is in progress (§5.2)."""
    states = set(beads) - TERMINAL
    if set(holds):
        return WsState.HELD
    if paused:
        return WsState.PAUSED
    if states & ACTIVE:
        return WsState.RUNNING
    if BeadState.STUCK in states:
        return WsState.STUCK
    if states:
        return WsState.ALL_BLOCKED
    return WsState.IDLE
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_states.py -q`
Expected: `13 passed`.

- [ ] **Step 5: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 6: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/states.py tests/test_wsd_states.py
git commit -m "feat(wsd): bead and workstream states with concrete reasons and legal transitions"
```

### Task 3: The journal (SQLite inbox, operations, states, holds, progress events)

**Files:**
- Create: `$HZ/src/heterodyne/wsd/journal.py`
- Test: `$HZ/tests/test_wsd_journal.py`

**Interfaces:**
- Consumes: `heterodyne.fsutil.private_dir` (Task 1); `BeadState`, `Reason`, `WsState`, `check` (Task 2).
- Produces (`heterodyne.wsd.journal`):
  - `class JournalCorrupt(Exception)`, `class OpConflict(Exception)`.
  - `class InboxStatus(StrEnum)`: `PENDING, COMMITTED, SUPERSEDED, REJECTED, NEEDS_HUMAN`. `class OpKind(StrEnum)`: `PICKUP, PARK, RESUME`. `class OpStatus(StrEnum)`: `OPEN, DONE, ABANDONED, STUCK`.
  - Frozen dataclasses: `InboxRow(seq, surface, event_id, kind, ws, bead, payload, status, attempts)`, `Op(op_id, kind, ws, bead, step, data: dict[str, str], attempts, status)`, `BeadRow(ws, bead, state, reason, detail, since)`, `Event(seq, at, ws, bead, kind, detail, ref)`, `Snapshot(ws, state: WsState | None, holds: dict[Reason, str], beads: list[BeadRow], ops: list[Op], last_event: int)`.
  - `class Journal(path: Path)`, with `.fresh: bool` and: `close()`, `transaction()` (re-entrant context manager), `inbox_add(surface, event_id, kind, payload, ws=None, bead=None) -> int | None` (None for a duplicate), `inbox_pending()`, `inbox_get(seq)`, `inbox_failed(seq, limit) -> InboxStatus`, `inbox_finish(seq, status)`, `inbox_pending_count()`, `op_open(kind, ws, bead, data=None) -> Op` (one open op per bead, else `OpConflict`), `op_step(op_id, step, data=None) -> Op`, `op_failed(op_id) -> int`, `op_finish(op_id, status)`, `ops_open(ws=None) -> list[Op]`, `op_for(ws, bead) -> Op | None`, `set_state(ws, bead, state, reason=None, detail="", ref=None)` (legal transitions only; emits `state:<value>`), `adopt(ws, bead, state, reason=None, detail="")` (recovery only, no transition check), `state(ws, bead) -> BeadRow | None`, `states(ws)`, `hold(ws, reason, detail="")`, `unhold(ws, reason)`, `holds(ws) -> dict[Reason, str]`, `set_ws_state(ws, state)`, `emit(ws, bead, kind, detail="", ref=None) -> int`, `events_since(seq, limit=500) -> list[Event]`, `snapshot(ws) -> Snapshot`.

The journal is integrity-checked when opened (`PRAGMA integrity_check`, schema version, expected tables); any failure raises `JournalCorrupt` and the file is not modified. The hypothesis test drives random transition sequences and checks that the journal accepts exactly the legal ones.

- [ ] **Step 1: Create `$HZ/tests/test_wsd_journal.py`**

```python
import sqlite3
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import SimulatedCrash
from hypothesis import given, settings
from hypothesis import strategies as st

from heterodyne.wsd.journal import InboxStatus, Journal, JournalCorrupt, OpConflict, OpKind, OpStatus
from heterodyne.wsd.states import BeadState, IllegalTransition, Reason, WsState, allowed


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "state" / "wsd.db")


def test_new_journal_is_fresh_and_private(tmp_path: Path) -> None:
    j = Journal(tmp_path / "state" / "wsd.db")
    assert j.fresh
    assert (tmp_path / "state" / "wsd.db").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    j.close()
    again = Journal(tmp_path / "state" / "wsd.db")
    assert not again.fresh


def test_corrupt_journal_is_refused_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "wsd.db"
    path.write_bytes(b"not a database at all" * 100)
    before = path.read_bytes()
    with pytest.raises(JournalCorrupt):
        Journal(path)
    assert path.read_bytes() == before


def test_foreign_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "wsd.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE other (x)")
    db.close()
    with pytest.raises(JournalCorrupt):
        Journal(path)
    assert path.exists()


def test_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    Journal(tmp_path / "wsd.db").close()
    db = sqlite3.connect(tmp_path / "wsd.db")
    db.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    db.commit()
    db.close()
    with pytest.raises(JournalCorrupt):
        Journal(tmp_path / "wsd.db")


def test_symlinked_journal_is_refused(tmp_path: Path) -> None:
    Journal(tmp_path / "real.db").close()
    (tmp_path / "wsd.db").symlink_to(tmp_path / "real.db")
    with pytest.raises(JournalCorrupt):
        Journal(tmp_path / "wsd.db")


def test_inbox_dedups_on_surface_and_event(journal: Journal) -> None:
    seq = journal.inbox_add("marmot", "ev1", "approve", "{}", ws="alpha")
    assert seq is not None
    assert journal.inbox_add("marmot", "ev1", "approve", "{}") is None
    assert journal.inbox_add("github", "ev1", "approve", "{}") is not None
    assert [r.event_id for r in journal.inbox_pending()] == ["ev1", "ev1"]


def test_inbox_escalates_after_limit(journal: Journal) -> None:
    seq = journal.inbox_add("marmot", "ev1", "approve", "{}")
    assert seq is not None
    assert journal.inbox_failed(seq, 3) is InboxStatus.PENDING
    assert journal.inbox_failed(seq, 3) is InboxStatus.PENDING
    assert journal.inbox_failed(seq, 3) is InboxStatus.NEEDS_HUMAN
    assert journal.inbox_pending_count() == 0
    with pytest.raises(KeyError):
        journal.inbox_finish(seq, InboxStatus.COMMITTED)


def test_inbox_finish_is_once(journal: Journal) -> None:
    seq = journal.inbox_add("marmot", "ev1", "approve", "{}")
    assert seq is not None
    journal.inbox_finish(seq, InboxStatus.SUPERSEDED)
    row = journal.inbox_get(seq)
    assert row is not None and row.status is InboxStatus.SUPERSEDED
    with pytest.raises(KeyError):
        journal.inbox_finish(seq, InboxStatus.COMMITTED)


def test_one_open_op_per_bead(journal: Journal) -> None:
    op = journal.op_open(OpKind.PARK, "alpha", "btq-1", {"blockers": "btq-2"})
    with pytest.raises(OpConflict):
        journal.op_open(OpKind.RESUME, "alpha", "btq-1")
    journal.op_finish(op.op_id, OpStatus.DONE)
    journal.op_open(OpKind.RESUME, "alpha", "btq-1")


def test_op_step_merges_data_and_counts_failures(journal: Journal) -> None:
    op = journal.op_open(OpKind.PARK, "alpha", "btq-1", {"blockers": "btq-2"})
    op = journal.op_step(op.op_id, "committed", {"sha": "abc"})
    assert (op.step, op.data) == ("committed", {"blockers": "btq-2", "sha": "abc"})
    assert journal.op_failed(op.op_id) == 1
    assert journal.op_failed(op.op_id) == 2
    journal.op_finish(op.op_id, OpStatus.STUCK)
    with pytest.raises(OpConflict):
        journal.op_step(op.op_id, "labelled")
    assert journal.op_for("alpha", "btq-1") is None


def test_transaction_rolls_back_on_crash(journal: Journal) -> None:
    with pytest.raises(SimulatedCrash), journal.transaction():
        journal.op_open(OpKind.PICKUP, "alpha", "btq-1")
        journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
        raise SimulatedCrash("x")
    assert journal.ops_open() == []
    assert journal.state("alpha", "btq-1") is None
    assert journal.events_since(0, 10) == []


def test_set_state_records_reason_and_event(journal: Journal) -> None:
    journal.set_state("alpha", "btq-1", BeadState.PARKED, Reason.BLOCKED_ON_BEAD, "btq-2", ref="msg1")
    journal.set_state("alpha", "btq-1", BeadState.PARKED, Reason.BLOCKED_ON_BEAD, "btq-2", ref="msg1")
    events = journal.events_since(0, 10)
    assert [(e.kind, e.detail, e.ref) for e in events] == [("state:parked", "blocked_on_bead", "msg1")]


def test_adopt_skips_the_transition_check(journal: Journal) -> None:
    journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
    journal.adopt("alpha", "btq-1", BeadState.PARKED, Reason.BLOCKED_ON_BEAD)
    row = journal.state("alpha", "btq-1")
    assert row is not None and row.state is BeadState.PARKED


def test_holds_and_snapshot(journal: Journal) -> None:
    journal.hold("alpha", Reason.BEADS_UNREACHABLE, "RuntimeError")
    journal.hold("alpha", Reason.BEADS_UNREACHABLE, "RuntimeError")
    journal.set_ws_state("alpha", WsState.HELD)
    snap = journal.snapshot("alpha")
    assert snap.holds == {Reason.BEADS_UNREACHABLE: "RuntimeError"}
    assert snap.state is WsState.HELD
    journal.unhold("alpha", Reason.BEADS_UNREACHABLE)
    assert [e.kind for e in journal.events_since(0, 10)] == ["hold", "ws:held", "unhold"]


def test_events_cursor(journal: Journal) -> None:
    first = journal.emit("alpha", None, "a")
    journal.emit("alpha", "btq-1", "b")
    assert [e.kind for e in journal.events_since(first, 10)] == ["b"]


def test_concurrent_writers_serialize(journal: Journal) -> None:
    def work(n: int) -> None:
        for i in range(50):
            journal.emit("alpha", f"b{n}", f"e{i}")
    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(journal.events_since(0, 1000)) == 200


@settings(max_examples=60, deadline=None)
@given(st.lists(st.sampled_from(list(BeadState)), max_size=25))
def test_journal_only_records_legal_transitions(tmp_path_factory: pytest.TempPathFactory,
                                                steps: list[BeadState]) -> None:
    journal = Journal(tmp_path_factory.mktemp("j") / "wsd.db")
    current: BeadState | None = None
    for step in steps:
        if allowed(current, step):
            journal.set_state("alpha", "btq-1", step)
            current = step
        else:
            with pytest.raises(IllegalTransition):
                journal.set_state("alpha", "btq-1", step)
        row = journal.state("alpha", "btq-1")
        assert (None if row is None else row.state) == current
    kinds = [e.kind for e in journal.events_since(0, 1000)]
    assert len(kinds) == len([k for k in kinds if k.startswith("state:")])
    journal.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_journal.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.journal'`.

- [ ] **Step 3: Create `$HZ/src/heterodyne/wsd/journal.py`**

```python
"""wsd's SQLite journal (ADR 0001 §3.3, §5.4): one file in WAL mode under the wsd state directory.

What it holds, and what it never does:
- `inbox`: decision events keyed by (surface, event ID), `pending` until committed, superseded,
  rejected or escalated. The decision itself lives on the bead (§5.4); the inbox only orders and dedups.
- `ops`: the pickup, park and resume journals, one row per operation with its last completed step, so
  a crash at any point is replayed from the step it reached (§4.3).
- `beads`, `holds`, `workstreams`: the state every surface shows (plan 6), each with a concrete reason.
- `events`: an append-only progress log with a cursor, for per-message progress reactions (plan 6).

Beads stay the source of truth. Opening the journal checks it first: an unreadable, corrupt or
foreign file is refused (JournalCorrupt) and never deleted or recreated, because a lost journal must be
noticed, not papered over. A missing file is created atomically and reported as `fresh`, so recovery
knows every journal it relies on is gone and rebuilds from beads.
"""

import contextlib
import functools
import os
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

import msgspec

from heterodyne.fsutil import private_dir
from heterodyne.wsd.states import BeadState, Reason, WsState, check

SCHEMA_VERSION = "1"
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE inbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    surface TEXT NOT NULL,
    event_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    ws TEXT,
    bead TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'committed', 'superseded', 'rejected', 'needs_human')),
    attempts INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (surface, event_id));
CREATE TABLE ops (
    op_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('pickup', 'park', 'resume')),
    ws TEXT NOT NULL,
    bead TEXT NOT NULL,
    step TEXT NOT NULL,
    data TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('open', 'done', 'abandoned', 'stuck')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX ops_one_open ON ops (ws, bead) WHERE status = 'open';
CREATE TABLE beads (
    ws TEXT NOT NULL, bead TEXT NOT NULL, state TEXT NOT NULL, reason TEXT, detail TEXT NOT NULL,
    since TEXT NOT NULL, PRIMARY KEY (ws, bead));
CREATE TABLE holds (
    ws TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL, since TEXT NOT NULL,
    PRIMARY KEY (ws, reason));
CREATE TABLE workstreams (ws TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, ws TEXT NOT NULL, bead TEXT,
    kind TEXT NOT NULL, detail TEXT NOT NULL, ref TEXT);
"""
TABLES = frozenset({"meta", "inbox", "ops", "beads", "holds", "workstreams", "events"})


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JournalCorrupt(Exception):
    """The journal failed its integrity check. wsd refuses to start; the file is left for the operator."""


class OpConflict(Exception):
    """An operation is already open for this bead: finish or replay it first."""


class InboxStatus(StrEnum):
    PENDING = "pending"
    COMMITTED = "committed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    NEEDS_HUMAN = "needs_human"


class OpKind(StrEnum):
    PICKUP = "pickup"
    PARK = "park"
    RESUME = "resume"


class OpStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    ABANDONED = "abandoned"
    STUCK = "stuck"


@dataclass(frozen=True)
class InboxRow:
    seq: int
    surface: str
    event_id: str
    kind: str
    ws: str | None
    bead: str | None
    payload: str
    status: InboxStatus
    attempts: int


@dataclass(frozen=True)
class Op:
    op_id: str
    kind: OpKind
    ws: str
    bead: str
    step: str
    data: dict[str, str]
    attempts: int
    status: OpStatus


@dataclass(frozen=True)
class BeadRow:
    ws: str
    bead: str
    state: BeadState
    reason: Reason | None
    detail: str
    since: str


@dataclass(frozen=True)
class Event:
    seq: int
    at: str
    ws: str
    bead: str | None
    kind: str
    detail: str
    ref: str | None


@dataclass(frozen=True)
class Snapshot:
    """Everything a status surface needs about one workstream, read in one transaction."""
    ws: str
    state: WsState | None
    holds: dict[Reason, str]
    beads: list[BeadRow]
    ops: list[Op]
    last_event: int


_DATA = msgspec.json.Decoder(dict[str, str])
_INBOX = "seq, surface, event_id, kind, ws, bead, payload, status, attempts"
_OP = "op_id, kind, ws, bead, step, data, attempts, status"


def _opt(v: object) -> str | None:
    return None if v is None else str(v)


def _inbox(r: tuple[object, ...]) -> InboxRow:
    return InboxRow(int(cast(int, r[0])), str(r[1]), str(r[2]), str(r[3]), _opt(r[4]), _opt(r[5]), str(r[6]),
                    InboxStatus(str(r[7])), int(cast(int, r[8])))


def _op(r: tuple[object, ...]) -> Op:
    return Op(str(r[0]), OpKind(str(r[1])), str(r[2]), str(r[3]), str(r[4]), _DATA.decode(str(r[5])),
              int(cast(int, r[6])), OpStatus(str(r[7])))


def _bead(r: tuple[object, ...]) -> BeadRow:
    reason = _opt(r[3])
    return BeadRow(str(r[0]), str(r[1]), BeadState(str(r[2])), None if reason is None else Reason(reason),
                   str(r[4]), str(r[5]))


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Serialize a Journal method: wsd calls it from the event loop and from worker threads."""
    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0].lock:  # type: ignore[attr-defined]  # args[0] is the Journal
            return method(*args, **kwargs)
    return wrapper


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _create(path: Path) -> None:
    """Build a complete journal under a temporary name and rename it into place, so `path` either does
    not exist or holds a whole schema: a crash can never leave a half-made journal that looks fresh."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.new")
    db = sqlite3.connect(tmp, isolation_level=None)
    try:
        db.executescript("BEGIN;" + SCHEMA + "COMMIT;")
        db.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?), ('created_at', ?)",
                   (SCHEMA_VERSION, now()))
    finally:
        db.close()
    tmp.chmod(0o600)
    os.link(tmp, path)      # fails if `path` appeared meanwhile: never overwrite a journal
    tmp.unlink()


def _check(db: sqlite3.Connection) -> None:
    rows = db.execute("PRAGMA integrity_check").fetchall()
    if [tuple(r) for r in rows] != [("ok",)]:
        raise JournalCorrupt("integrity_check failed")
    tables = {str(r[0]) for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not TABLES <= tables:
        raise JournalCorrupt("tables missing")
    row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None or str(row[0]) != SCHEMA_VERSION:
        raise JournalCorrupt("unknown schema version")


class Journal:
    def __init__(self, path: Path) -> None:
        private_dir(path.parent)
        self.fresh = False
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            _create(path)
            self.fresh = True
        else:
            if not stat.S_ISREG(st.st_mode):
                raise JournalCorrupt("the journal path is not a regular file")
        self.lock = threading.RLock()
        self._depth = 0
        try:
            self.db = _connect(path)
            _check(self.db)
        except sqlite3.DatabaseError as exc:
            raise JournalCorrupt(type(exc).__name__) from None

    @_locked
    def close(self) -> None:
        self.db.close()

    @contextlib.contextmanager
    def transaction(self) -> Generator[None]:
        """All the block's journal writes commit, or (on any exception, a simulated crash included)
        none do. Re-entrant: a nested block joins the outer one."""
        with self.lock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.db.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            finally:
                self._depth = 0

    # --- inbox (§5.4) ---

    @_locked
    def inbox_add(self, surface: str, event_id: str, kind: str, payload: str,
                  ws: str | None = None, bead: str | None = None) -> int | None:
        """Insert a `pending` event; None if (surface, event_id) was already seen (a replay)."""
        at = now()
        cur = self.db.execute(
            "INSERT OR IGNORE INTO inbox (surface, event_id, kind, ws, bead, payload, status, received_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (surface, event_id, kind, ws, bead, payload, at, at))
        return None if cur.rowcount == 0 else cur.lastrowid

    @_locked
    def inbox_pending(self) -> list[InboxRow]:
        rows = self.db.execute(f"SELECT {_INBOX} FROM inbox WHERE status = 'pending' "  # noqa: S608
                               "ORDER BY seq").fetchall()
        return [_inbox(r) for r in rows]

    @_locked
    def inbox_get(self, seq: int) -> InboxRow | None:
        row = self.db.execute(f"SELECT {_INBOX} FROM inbox WHERE seq = ?", (seq,)).fetchone()  # noqa: S608
        return None if row is None else _inbox(row)

    @_locked
    def inbox_failed(self, seq: int, limit: int) -> InboxStatus:
        """Count one failed attempt; the `limit`th failure escalates the event to `needs_human`."""
        with self.transaction():
            self.db.execute("UPDATE inbox SET attempts = attempts + 1, updated_at = ? "
                            "WHERE seq = ? AND status = 'pending'", (now(), seq))
            self.db.execute("UPDATE inbox SET status = 'needs_human' "
                            "WHERE seq = ? AND status = 'pending' AND attempts >= ?", (seq, limit))
            row = self.db.execute("SELECT status FROM inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            raise KeyError(seq)
        return InboxStatus(str(row[0]))

    @_locked
    def inbox_finish(self, seq: int, status: InboxStatus) -> None:
        if status is InboxStatus.PENDING:
            raise ValueError("finish needs a final status")
        cur = self.db.execute("UPDATE inbox SET status = ?, updated_at = ? "
                              "WHERE seq = ? AND status = 'pending'", (status.value, now(), seq))
        if cur.rowcount != 1:
            raise KeyError(seq)

    # --- operation journals (§4.3) ---

    @_locked
    def op_open(self, kind: OpKind, ws: str, bead: str, data: dict[str, str] | None = None) -> Op:
        op_id = uuid.uuid4().hex
        at = now()
        try:
            self.db.execute(
                "INSERT INTO ops (op_id, kind, ws, bead, step, data, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'intent', ?, 'open', ?, ?)",
                (op_id, kind.value, ws, bead, msgspec.json.encode(data or {}).decode(), at, at))
        except sqlite3.IntegrityError:
            raise OpConflict(f"an operation is already open for {bead}") from None
        return self._op(op_id)

    def _op(self, op_id: str) -> Op:
        row = self.db.execute(f"SELECT {_OP} FROM ops WHERE op_id = ?", (op_id,)).fetchone()  # noqa: S608
        if row is None:
            raise KeyError(op_id)
        return _op(row)

    @_locked
    def op_step(self, op_id: str, step: str, data: dict[str, str] | None = None) -> Op:
        """Record that `step` completed, merging `data` (a SHA, a worktree path) into the op's data."""
        with self.transaction():
            op = self._op(op_id)
            if op.status is not OpStatus.OPEN:
                raise OpConflict(f"operation {op_id} is {op.status}")
            merged = {**op.data, **(data or {})}
            self.db.execute("UPDATE ops SET step = ?, data = ?, updated_at = ? WHERE op_id = ?",
                            (step, msgspec.json.encode(merged).decode(), now(), op_id))
            return self._op(op_id)

    @_locked
    def op_failed(self, op_id: str) -> int:
        """Count one failed attempt and return the total."""
        self.db.execute("UPDATE ops SET attempts = attempts + 1, updated_at = ? WHERE op_id = ?",
                        (now(), op_id))
        return self._op(op_id).attempts

    @_locked
    def op_finish(self, op_id: str, status: OpStatus) -> None:
        if status is OpStatus.OPEN:
            raise ValueError("finish needs a final status")
        self.db.execute("UPDATE ops SET status = ?, updated_at = ? WHERE op_id = ? AND status = 'open'",
                        (status.value, now(), op_id))

    @_locked
    def ops_open(self, ws: str | None = None) -> list[Op]:
        if ws is None:
            rows = self.db.execute(f"SELECT {_OP} FROM ops WHERE status = 'open' "  # noqa: S608
                                   "ORDER BY created_at, op_id")
        else:
            rows = self.db.execute(f"SELECT {_OP} FROM ops WHERE status = 'open' AND ws = ? "  # noqa: S608
                                   "ORDER BY created_at, op_id", (ws,))
        return [_op(r) for r in rows.fetchall()]

    @_locked
    def op_for(self, ws: str, bead: str) -> Op | None:
        row = self.db.execute(f"SELECT {_OP} FROM ops WHERE status = 'open' AND ws = ? AND bead = ?",  # noqa: S608
                              (ws, bead)).fetchone()
        return None if row is None else _op(row)

    # --- states, holds, events (plan 6 reads these) ---

    @_locked
    def set_state(self, ws: str, bead: str, state: BeadState, reason: Reason | None = None,
                  detail: str = "", ref: str | None = None) -> None:
        """Move a bead to `state` (a legal transition only) and append the matching progress event."""
        with self.transaction():
            current = self.state(ws, bead)
            check(None if current is None else current.state, state)
            if current is not None and (current.state, current.reason, current.detail) == (
                    state, reason, detail):
                return
            self.db.execute(
                "INSERT INTO beads (ws, bead, state, reason, detail, since) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (ws, bead) DO UPDATE SET state = excluded.state, reason = excluded.reason, "
                "detail = excluded.detail, since = excluded.since",
                (ws, bead, state.value, None if reason is None else reason.value, detail, now()))
            self.emit(ws, bead, f"state:{state.value}", reason.value if reason else "", ref)

    @_locked
    def adopt(self, ws: str, bead: str, state: BeadState, reason: Reason | None = None,
              detail: str = "") -> None:
        """Recovery's rebuild from beads (§3.3): beads are the truth, so the cached row is replaced
        without a transition check."""
        with self.transaction():
            current = self.state(ws, bead)
            if current is not None and (current.state, current.reason, current.detail) == (
                    state, reason, detail):
                return
            self.db.execute("DELETE FROM beads WHERE ws = ? AND bead = ?", (ws, bead))
            self.set_state(ws, bead, state, reason, detail)

    @_locked
    def state(self, ws: str, bead: str) -> BeadRow | None:
        row = self.db.execute("SELECT ws, bead, state, reason, detail, since FROM beads "
                              "WHERE ws = ? AND bead = ?", (ws, bead)).fetchone()
        return None if row is None else _bead(row)

    @_locked
    def states(self, ws: str) -> list[BeadRow]:
        rows = self.db.execute("SELECT ws, bead, state, reason, detail, since FROM beads WHERE ws = ? "
                               "ORDER BY bead", (ws,)).fetchall()
        return [_bead(r) for r in rows]

    @_locked
    def hold(self, ws: str, reason: Reason, detail: str = "") -> None:
        with self.transaction():
            if self.holds(ws).get(reason) == detail:
                return
            self.db.execute("INSERT INTO holds (ws, reason, detail, since) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT (ws, reason) DO UPDATE SET detail = excluded.detail",
                            (ws, reason.value, detail, now()))
            self.emit(ws, None, "hold", reason.value)

    @_locked
    def unhold(self, ws: str, reason: Reason) -> None:
        with self.transaction():
            cur = self.db.execute("DELETE FROM holds WHERE ws = ? AND reason = ?", (ws, reason.value))
            if cur.rowcount:
                self.emit(ws, None, "unhold", reason.value)

    @_locked
    def holds(self, ws: str) -> dict[Reason, str]:
        rows = self.db.execute("SELECT reason, detail FROM holds WHERE ws = ? ORDER BY reason",
                               (ws,)).fetchall()
        return {Reason(str(r[0])): str(r[1]) for r in rows}

    @_locked
    def set_ws_state(self, ws: str, state: WsState) -> None:
        with self.transaction():
            row = self.db.execute("SELECT state FROM workstreams WHERE ws = ?", (ws,)).fetchone()
            if row is not None and str(row[0]) == state.value:
                return
            self.db.execute("INSERT INTO workstreams (ws, state, updated_at) VALUES (?, ?, ?) "
                            "ON CONFLICT (ws) DO UPDATE SET state = excluded.state, "
                            "updated_at = excluded.updated_at",
                            (ws, state.value, now()))
            self.emit(ws, None, f"ws:{state.value}")

    @_locked
    def emit(self, ws: str, bead: str | None, kind: str, detail: str = "", ref: str | None = None) -> int:
        cur = self.db.execute("INSERT INTO events (at, ws, bead, kind, detail, ref) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (now(), ws, bead, kind, detail, ref))
        return int(cast(int, cur.lastrowid))

    @_locked
    def inbox_pending_count(self) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM inbox WHERE status = 'pending'").fetchone()
        return int(cast(int, row[0]))

    @_locked
    def events_since(self, seq: int, limit: int = 500) -> list[Event]:
        rows = self.db.execute("SELECT seq, at, ws, bead, kind, detail, ref FROM events WHERE seq > ? "
                               "ORDER BY seq LIMIT ?", (seq, limit)).fetchall()
        return [Event(int(cast(int, r[0])), str(r[1]), str(r[2]), _opt(r[3]), str(r[4]), str(r[5]),
                      _opt(r[6])) for r in rows]

    @_locked
    def snapshot(self, ws: str) -> Snapshot:
        with self.transaction():
            row = self.db.execute("SELECT state FROM workstreams WHERE ws = ?", (ws,)).fetchone()
            last = self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
            state = None if row is None else WsState(str(row[0]))
            return Snapshot(ws, state, self.holds(ws), self.states(ws), self.ops_open(ws),
                            int(cast(int, last[0])))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_journal.py -q`
Expected: `17 passed`.

- [ ] **Step 5: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 6: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/journal.py tests/test_wsd_journal.py
git commit -m "feat(wsd): SQLite journal with inbox, operations, holds and progress events"
```

### Task 4: The beads adapter, through btq

**Files:**
- Create: `$HZ/docs/spikes/S6-beads-json.md`, `$HZ/src/heterodyne/wsd/btq.py`, `$HZ/src/heterodyne/wsd/gitwip.py`, `$HZ/src/heterodyne/wsd/beads.py`, `$HZ/tests/fakes/fake_btq.py`, `$HZ/tests/wsd_env.py`
- Test: `$HZ/tests/test_wsd_beads.py`, `$HZ/tests/test_wsd_gitwip.py`

**Interfaces:**
- Consumes: `ids.ws_session`, `ids.bead_session`, `ids.SLUG` (Task 1).
- Produces:
  - `heterodyne.wsd.btq`: `AGENT = "wsd"`, `class BtqUnavailable(Exception)`, `class QueueLike(Protocol)` (`worker: str`, `state: Path`, `bd(*args)`, `show(id)`, `ready()`, `claim(id)`, `owned(id, statuses=("in_progress",))`, `worktree(id, repository)`, `exclusive()`), `QueueFactory = Callable[[str, str], QueueLike]` (workstream, btq session), `load(checkout: Path) -> ModuleType`, `factory(module, locations: Mapping[str, str]) -> QueueFactory`.
  - `heterodyne.wsd.gitwip`: `class GitFailed(Exception)`, `git(path, *args) -> str`, `branch(path) -> str`, `toplevel(path) -> Path`, `find_wip(worktree, mark) -> str | None`, `wip_commit(worktree, mark, summary) -> str` (idempotent per mark; returns the SHA).
  - `heterodyne.wsd.beads`: labels `PARKED = "v2:parked"`, `HELD = "v2:held"`, `NEEDS_HUMAN = "needs-human"`, `OPERATOR_INPUT_LABELS`, `NON_BLOCKING_DEPS`; exceptions `BeadsUnavailable`, `UnexpectedShape(BeadsUnavailable)`, `ClaimRefused`, `ClaimUncertain`, `RoutingChanged`, `NotOurs`, `WorktreeConflict`; `class ClaimView(StrEnum)`: `OURS, FREE, OTHER`; frozen `Dep(id, status, kind, labels)` with `.blocking`; frozen `Bead(id, title, status, assignee, labels, metadata, deps)` with `open_blockers()` and `waits_on_operator()`; `parse(raw, detail) -> Bead`; `class BeadsAdapter(factory: QueueFactory)` with `ws_queue(ws)`, `bead_queue(ws, bead)`, `ready(ws) -> list[Bead]`, `show(ws, bead) -> Bead`, `exists(ws, bead) -> bool`, `read_claim(ws, bead) -> ClaimView`, `ours(ws) -> list[Bead]`, `with_metadata(ws, key, values) -> list[str]`, `comments(ws, bead) -> list[str]`, `paused(ws) -> bool`, `set_paused(ws, paused)`, `claim(ws, bead) -> Bead`, `ensure_label(ws, bead, label, present=True)`, `ensure_blocker(ws, bead, blocker)`, `ensure_comment(ws, bead, mark, text)`, `ensure_metadata(ws, bead, key, value)`, `worktree(ws, bead, repository: Path) -> Path`.
  - `tests/fakes/fake_btq.py`: `FakeBead`, `Fault(call, exc, after=False, times=1)`, `World(state_root)` (`beads`, `down`, `faults`, `calls`, `claims`, `stolen`, `worktrees`; `add(id, ws="alpha", **kw)`, `fault(call, exc, after=False, times=1)`, `close(id)`), `FakeQueue(world, ws, session)`, `factory(world) -> QueueFactory`.
  - `tests/wsd_env.py`: `WS = "alpha"`, `git_repo(path) -> Path`.

- [ ] **Step 1: Record the bd 1.1 JSON shapes (spike S6)**

The adapter must accept exactly the shapes bd produces and refuse anything else. Record them against a **throwaway** database, never the real queue:

```bash
export SPIKE=$(mktemp -d) && mkdir -p "$SPIKE/home" "$SPIKE/proj" && cd "$SPIKE/proj" && git init -q
export HOME="$SPIKE/home" BD_NON_INTERACTIVE=1
bd init --prefix s6 --non-interactive
a=$(bd create first --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
b=$(bd create second --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
bd show "$a" --json; bd list --json --limit 0
bd label add "$a" v2:parked --json; bd label add "$a" v2:parked --json
bd dep add "$a" "$b" --json; bd dep add "$a" "$b" --json
bd show "$a" --json; bd list --json --limit 0
bd comments "$b" --json; bd comments add "$a" hello --json; bd comments add "$a" hello --json; bd comments "$a" --json
bd update "$a" --set-metadata action_state=executing --json
bd show s6-missing --json; bd label add s6-missing x --json; echo "exit $?"
```

Write the findings to `$HZ/docs/spikes/S6-beads-json.md`. On bd 1.1.0 they are as below; if your bd differs, stop and update `beads.parse` and the fake to match before going on.

```markdown
# S6: bd 1.1 JSON shapes wsd relies on (plan 3, Task 4)

Run against bd 1.1.0 on 2026-10-04, in a throwaway embedded database (`bd init` in a temp directory with a temp `HOME`). No real queue was read or written. The fake queue (`tests/fakes/fake_btq.py`) answers in these shapes, and `heterodyne.wsd.beads.parse` refuses anything else (UnexpectedShape, which wsd treats as beads being unavailable).

## Beads

- `bd show <id> --json` and `bd list --json` both return a **list** of objects; `show` has exactly one.
- Always present: `id`, `title`, `status`, `priority`, `issue_type`, `created_at`, `created_by`, `updated_at`, `dependency_count`, `dependent_count`, `comment_count`.
- **Omitted when empty:** `labels` (no labels), `metadata` (no metadata), `assignee` (unassigned) and `dependencies` (when `dependency_count` is 0). An absent key means empty only for these; wsd reads a missing `dependencies` with a non-zero `dependency_count` as a malformed answer, never as "no blockers".
- `metadata` is an object of strings (`bd update <id> --set-metadata k=v` returns the updated bead list).

## Dependencies

- In `show`, `dependencies` lists the beads depended on, each with `id`, `title`, `status`, `priority`, `issue_type`, `created_at`, `created_by`, `updated_at`, `dependency_type`, and `labels` when that bead has any.
- In `list`, `dependencies` is a list of **edges** instead: `issue_id`, `depends_on_id`, `type`, `created_at`, `created_by`, `metadata` (a JSON string). wsd reads blockers only from `show`.
- `bd dep add A B` (A depends on B, type `blocks`) returns `{"issue_id", "depends_on_id", "type", "status": "added", "schema_version"}`. Adding the same edge again also reports `added` and creates no second edge: idempotent.

## Labels

- `bd label add <id> <label>` returns `[{"issue_id", "label", "status": "added"}]`, also when the label is already there (idempotent). `label remove` of an absent label reports `removed`.
- **`bd label add` on a missing bead exits 0** with `[]` and an error line on stderr. So a label write is never trusted from its exit status: wsd reads every write back (`BeadsAdapter.ensure_*`).

## Comments

- `bd comments <id> --json` returns a list of `{"id", "issue_id", "author", "text", "created_at"}`; `[]` when there are none.
- `bd comments add <id> <text>` is **not idempotent**: the same text twice makes two comments. wsd puts a unique mark (`wsd-park: <op id>`) in each comment and looks for it before adding.

## Not found

- `bd show <missing>` exits 1 with `no issue found matching "<id>"` (btq raises it as RuntimeError). `dep add` and `comments` on a missing bead exit 1 with the same phrase inside a JSON `error`. wsd treats only this phrase as "absent"; any other failure is BeadsUnavailable.
```

- [ ] **Step 2: Create `$HZ/tests/fakes/fake_btq.py`**

The fake follows btq's claim rules and error messages, and answers in the S6 shapes. `fault(..., after=True)` performs the write and then fails, which is how an uncertain write looks to wsd. `stolen` makes another worker win the claim race.

```python
"""An in-memory beads queue with btq's `Queue` interface (the `QueueLike` slice wsd uses).

It answers in the bd 1.1 JSON shapes recorded in spike S6 (`docs/spikes/S6-beads-json.md`): empty
`labels` and `metadata` are omitted, `dependencies` is omitted when `dependency_count` is 0, and `list`
gives dependency edges where `show` gives the beads depended on. Claims,
ownership and the pause flag follow btq's rules and error messages. Faults are injected per call name;
`after=True` performs the write and then fails, which is how an uncertain write looks to wsd.
"""

import contextlib
import hashlib
import subprocess
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOST = "testhost"


@dataclass
class FakeBead:
    id: str
    title: str = "a task"
    status: str = "open"
    assignee: str | None = None
    labels: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    deps: list[tuple[str, str]] = field(default_factory=list)      # (other bead, dependency type)
    comments: list[str] = field(default_factory=list)


@dataclass
class Fault:
    call: str
    exc: Exception
    after: bool = False
    times: int = 1


class World:
    def __init__(self, state_root: Path) -> None:
        self.beads: dict[str, FakeBead] = {}
        self.state_root = state_root
        self.down = False
        self.faults: list[Fault] = []
        self.calls: list[tuple[str, str]] = []        # (worker, call)
        self.claims: list[str] = []
        self.stolen: set[str] = set()      # beads another worker claims first (a lost race)
        self.worktrees: list[str] = []
        self.lock = threading.RLock()

    def add(self, bead_id: str, ws: str = "alpha", **kw: Any) -> FakeBead:
        labels = kw.pop("labels", [])
        bead = FakeBead(bead_id, labels=["agent:wsd", f"ws:{ws}", "kind:task", *labels], **kw)
        self.beads[bead_id] = bead
        return bead

    def fault(self, call: str, exc: Exception, after: bool = False, times: int = 1) -> None:
        self.faults.append(Fault(call, exc, after, times))

    def _take(self, call: str, after: bool) -> Exception | None:
        for f in self.faults:
            if f.call == call and f.after == after and f.times > 0:
                f.times -= 1
                return f.exc
        return None

    def check(self, call: str, worker: str) -> None:
        self.calls.append((worker, call))
        if self.down:
            raise RuntimeError("dolt: connection refused")
        exc = self._take(call, after=False)
        if exc is not None:
            raise exc

    def check_after(self, call: str) -> None:
        exc = self._take(call, after=True)
        if exc is not None:
            raise exc

    def blocked(self, bead: FakeBead) -> bool:
        return any(kind not in ("parent-child", "related", "discovered-from")
                   and self.beads[other].status != "closed" for other, kind in bead.deps)

    def ready_for(self, ws: str) -> list[FakeBead]:
        return [b for b in sorted(self.beads.values(), key=lambda b: b.id)
                if b.status == "open" and b.assignee is None and "agent:wsd" in b.labels
                and f"ws:{ws}" in b.labels and not self.blocked(b)]

    def close(self, bead_id: str) -> None:
        self.beads[bead_id].status = "closed"

    def json(self, bead: FakeBead, detail: bool) -> dict[str, Any]:
        out: dict[str, Any] = {"id": bead.id, "title": bead.title, "status": bead.status,
                               "priority": 2, "issue_type": "task",
                               "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}
        if bead.assignee:
            out["assignee"] = bead.assignee
        if bead.labels:
            out["labels"] = list(bead.labels)
        if bead.metadata:
            out["metadata"] = dict(bead.metadata)
        out["dependency_count"] = len(bead.deps)
        if not detail and bead.deps:        # `bd list` gives the edges, not the beads they point at
            out["dependencies"] = [{"issue_id": bead.id, "depends_on_id": other, "type": kind,
                                    "created_at": "2026-10-01T00:00:00Z", "metadata": "{}"}
                                   for other, kind in bead.deps]
        if detail and bead.deps:
            deps: list[dict[str, Any]] = []
            for other, kind in bead.deps:
                o = self.beads[other]
                dep: dict[str, Any] = {"id": o.id, "title": o.title, "status": o.status,
                                       "dependency_type": kind}
                if o.labels:
                    dep["labels"] = list(o.labels)
                deps.append(dep)
            out["dependencies"] = deps
        return out


class FakeQueue:
    def __init__(self, world: World, ws: str, session: str) -> None:
        self.world = world
        self.ws = ws
        self.worker = f"wsd:{HOST}:{session}"
        self.state = world.state_root / hashlib.sha256(self.worker.encode()).hexdigest()
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._exclusive = threading.Lock()

    def _get(self, bead_id: str) -> FakeBead:
        bead = self.world.beads.get(bead_id)
        if bead is None:
            raise RuntimeError(f"Error: no issue found matching {bead_id!r}")
        return bead

    def show(self, issue_id: str) -> Any:
        with self.world.lock:
            self.world.check("show", self.worker)
            return self.world.json(self._get(issue_id), detail=True)

    def ready(self) -> Any:
        with self.world.lock:
            self.world.check("ready", self.worker)
            if (self.state / "paused").exists():
                return []
            return [self.world.json(b, detail=False) for b in self.world.ready_for(self.ws)]

    def claim(self, issue_id: str) -> Any:
        with self.world.lock:
            self.world.check("claim", self.worker)
            bead = self._get(issue_id)
            if issue_id in self.world.stolen:
                bead.status, bead.assignee = "in_progress", "codex:otherhost:x"
            mine = [b for b in self.world.beads.values() if b.assignee == self.worker]
            if any(b.status == "in_progress" for b in mine):
                raise ValueError("Finish or release this worker's existing claim first")
            if bead not in self.world.ready_for(self.ws):
                raise ValueError("Task is not eligible for this worker")
            bead.status, bead.assignee = "in_progress", self.worker
            self.world.claims.append(issue_id)
            self.world.check_after("claim")
            return self.world.json(bead, detail=True)

    def owned(self, issue_id: str, statuses: tuple[str, ...] = ("in_progress",)) -> Any:
        with self.world.lock:
            self.world.check("owned", self.worker)
            bead = self._get(issue_id)
            if bead.status not in statuses or bead.assignee != self.worker:
                raise ValueError("Task is not in progress under this worker")
            return self.world.json(bead, detail=True)

    def worktree(self, issue_id: str, repository: str) -> Any:
        with self.world.lock:
            self.world.check("worktree", self.worker)
            self.owned(issue_id)
            repo = Path(repository)
            dest = repo.parent / f"{repo.name}-btq-{issue_id}"
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", f"btq/{issue_id}",
                            str(dest)], check=True, capture_output=True)
            self.world.worktrees.append(issue_id)
            self.world.check_after("worktree")
            return {"worktree": str(dest), "base": "main"}

    @contextlib.contextmanager
    def exclusive(self) -> Generator[None]:
        with self._exclusive:
            yield

    def bd(self, *args: str) -> Any:
        with self.world.lock:
            verb = args[0]
            name = "comments add" if verb == "comments" and len(args) > 2 else verb
            self.world.check(name, self.worker)
            result = self._bd(list(args))
            self.world.check_after(name)
            return result

    def _bd(self, args: list[str]) -> Any:
        verb = args.pop(0)
        if verb == "list":
            labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
            statuses = args[args.index("--status") + 1].split(",")
            beads = sorted(self.world.beads.values(), key=lambda b: b.id)
            return [self.world.json(b, detail=False) for b in beads
                    if b.status in statuses and all(lbl in b.labels for lbl in labels)]
        if verb == "label":
            action, bead_id, label = args
            bead = self._get(bead_id)
            if action == "add" and label not in bead.labels:
                bead.labels.append(label)
            if action == "remove" and label in bead.labels:
                bead.labels.remove(label)
            done = "added" if action == "add" else "removed"
            return [{"issue_id": bead_id, "label": label, "status": done}]
        if verb == "dep":
            _, bead_id, other = args
            bead = self._get(bead_id)
            self._get(other)
            if not any(d == other for d, _ in bead.deps):
                bead.deps.append((other, "blocks"))
            return {"status": "added"}
        if verb == "comments":
            if len(args) == 1:
                bead = self._get(args[0])
                return [{"id": i, "issue_id": bead.id, "author": self.worker, "text": t,
                         "created_at": "2026-10-01T00:00:00Z"} for i, t in enumerate(bead.comments)]
            _, bead_id, text = args
            self._get(bead_id).comments.append(text)
            return {"text": text}
        if verb == "update":
            bead_id, flag, pair = args
            assert flag == "--set-metadata"
            key, value = pair.split("=", 1)
            self._get(bead_id).metadata[key] = value
            return [self.world.json(self._get(bead_id), detail=False)]
        raise AssertionError(f"fake bd does not support {verb}")


def factory(world: World):  # noqa: ANN201 - returns a QueueFactory
    queues: dict[tuple[str, str], FakeQueue] = {}

    def make(ws: str, session: str) -> FakeQueue:
        if world.down:
            raise OSError("credentials unreadable")
        queues.setdefault((ws, session), FakeQueue(world, ws, session))
        return queues[(ws, session)]

    return make
```

- [ ] **Step 3: Create `$HZ/tests/wsd_env.py`**

The shared test helpers start small; Task 6 replaces this file with the test rig.

```python
"""Shared helpers for the wsd tests. Task 6 adds the test rig."""

import subprocess
from pathlib import Path

WS = "alpha"


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@example.org",
                                                   "commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path
```

- [ ] **Step 4: Create `$HZ/tests/test_wsd_beads.py`**

`test_btq_contract_*` loads the real `$BTQ_REPO/bin/btq` with a fake `bd` executable on `PATH` and `HOME` in `tmp_path`; it is skipped without `BTQ_REPO`.

```python
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import World, factory
from wsd_env import WS, git_repo

from heterodyne.wsd import btq, ids
from heterodyne.wsd.beads import (
    PARKED,
    BeadsAdapter,
    BeadsUnavailable,
    ClaimRefused,
    ClaimUncertain,
    ClaimView,
    NotOurs,
    UnexpectedShape,
    WorktreeConflict,
    parse,
)


def raw(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "btq-1", "title": "t", "status": "open", "dependency_count": 0}
    return {**base, **kw}


def test_parse_omitted_labels_metadata_and_deps() -> None:
    bead = parse(raw(), detail=True)
    assert (bead.labels, bead.metadata, bead.deps) == ((), {}, ())


def test_parse_missing_dependencies_with_a_count_fails_closed() -> None:
    with pytest.raises(UnexpectedShape):
        parse(raw(dependency_count=1), detail=True)


def test_parse_missing_count_and_list_fails_closed() -> None:
    bead = raw()
    del bead["dependency_count"]
    with pytest.raises(UnexpectedShape):
        parse(bead, detail=True)


@pytest.mark.parametrize("bad", [
    raw(labels="x"), raw(labels=[1]), raw(metadata=[]), raw(id=None), raw(dependencies={}),
    raw(dependency_count=1, dependencies=[{"id": "x", "status": "open"}]), [raw()], None,
])
def test_parse_rejects_unexpected_shapes(bad: Any) -> None:
    with pytest.raises(UnexpectedShape):
        parse(bad, detail=True)


def test_unknown_dependency_type_blocks() -> None:
    bead = parse(raw(dependency_count=2, dependencies=[
        {"id": "a", "status": "open", "dependency_type": "something-new"},
        {"id": "b", "status": "open", "dependency_type": "related"}]), detail=True)
    assert [d.id for d in bead.open_blockers()] == ["a"]


def test_operator_input_blocker_is_waiting_on_operator() -> None:
    bead = parse(raw(dependency_count=1, dependencies=[
        {"id": "a", "status": "open", "dependency_type": "blocks", "labels": ["kind:question"]}]),
        detail=True)
    assert bead.waits_on_operator()


def test_list_output_has_no_blocker_detail() -> None:
    with pytest.raises(ValueError, match="show"):
        parse(raw(), detail=False).open_blockers()


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path / "btq-state")


def test_claim_read_back_views(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    assert adapter.read_claim(WS, "btq-1") is ClaimView.FREE
    adapter.claim(WS, "btq-1")
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OURS
    world.beads["btq-1"].assignee = "someone:else"
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OTHER


def test_claim_errors_are_classified(world: World) -> None:
    world.add("btq-1", status="closed")
    adapter = BeadsAdapter(factory(world))
    with pytest.raises(ClaimRefused):
        adapter.claim(WS, "btq-1")
    world.add("btq-2")
    world.fault("claim", RuntimeError("timeout"), after=True)
    with pytest.raises(ClaimUncertain):
        adapter.claim(WS, "btq-2")
    assert adapter.read_claim(WS, "btq-2") is ClaimView.OURS


def test_ours_lists_only_per_bead_workers(world: World) -> None:
    world.add("btq-1")
    world.add("btq-2", status="in_progress", assignee="claude:host:x")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    assert [b.id for b in adapter.ours(WS)] == ["btq-1"]


def test_writes_are_idempotent_and_owned(world: World) -> None:
    world.add("btq-1")
    world.add("btq-2")
    adapter = BeadsAdapter(factory(world))
    with pytest.raises(NotOurs):
        adapter.ensure_label(WS, "btq-1", PARKED)
    adapter.claim(WS, "btq-1")
    for _ in range(2):
        adapter.ensure_label(WS, "btq-1", PARKED)
        adapter.ensure_blocker(WS, "btq-1", "btq-2")
        adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)")
    bead = world.beads["btq-1"]
    assert bead.labels.count(PARKED) == 1
    assert bead.deps == [("btq-2", "blocks")]
    assert bead.comments == ["parked (m-1)"]


def test_uncertain_comment_is_not_repeated(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    world.fault("comments add", RuntimeError("timeout"), after=True)
    with pytest.raises(BeadsUnavailable):
        adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)")
    adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)")
    assert world.beads["btq-1"].comments == ["parked (m-1)"]


def test_queue_down_is_unavailable_never_empty(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.ws_queue(WS)
    world.down = True
    with pytest.raises(BeadsUnavailable):
        adapter.ready(WS)
    with pytest.raises(BeadsUnavailable):
        adapter.ours(WS)
    with pytest.raises(BeadsUnavailable):
        adapter.exists(WS, "btq-1")


def test_missing_bead_is_the_only_absent(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    assert adapter.exists(WS, "btq-9") is False


def test_unreadable_pause_flag_counts_as_paused(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    state = adapter.ws_queue(WS).state
    assert adapter.paused(WS) is False
    state.rmdir()
    state.write_text("not a directory")       # lstat of state/paused now fails with ENOTDIR
    assert adapter.paused(WS) is True


def test_pause_flag_reads_back(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    adapter.set_paused(WS, True)
    assert (adapter.ws_queue(WS).state / "paused").exists()
    adapter.set_paused(WS, False)
    assert adapter.paused(WS) is False


def test_worktree_is_idempotent_and_conflicts_fail_closed(world: World, tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    first = adapter.worktree(WS, "btq-1", repo)
    assert adapter.worktree(WS, "btq-1", repo) == first
    assert world.worktrees == ["btq-1"]
    world.add("btq-2")
    adapter.claim(WS, "btq-2")
    (tmp_path / "proj-btq-btq-2").mkdir()
    (tmp_path / "proj-btq-btq-2" / "keep").write_text("x")
    with pytest.raises(WorktreeConflict):
        adapter.worktree(WS, "btq-2", repo)
    assert (tmp_path / "proj-btq-btq-2" / "keep").exists()


FAKE_BD = """#!{python}
import json, sys
log = {log!r}
with open(log, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if "show" in args:
    print(json.dumps([{bead}]))
else:
    print("[]")
"""


@pytest.mark.skipif(not os.environ.get("BTQ_REPO"), reason="needs $BTQ_REPO (a beads-task-queue checkout)")
def test_contract_with_real_btq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real btq Queue, loaded as wsd does, against a fake `bd` executable: no Dolt, no network."""
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bindir.mkdir()
    config = tmp_path / "btq-config"
    config.mkdir()
    (config / "credentials.json").write_text(json.dumps({"wsd": "test-only"}))
    session = ids.bead_session(WS, "btq-1")
    bead = {"id": "btq-1", "title": "t", "status": "in_progress", "assignee": f"wsd:fakehost:{session}",
            "labels": ["agent:wsd", f"ws:{WS}", "kind:task"], "dependency_count": 0}
    bd = bindir / "bd"
    bd.write_text(FAKE_BD.format(python=sys.executable, log=str(tmp_path / "bd.log"), bead=json.dumps(bead)))
    bd.chmod(bd.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr("socket.gethostname", lambda: "fakehost")
    module = btq.load(Path(os.environ["BTQ_REPO"]))
    adapter = BeadsAdapter(btq.factory(module, {"config_dir": str(config), "repo": str(tmp_path)}))
    assert adapter.show(WS, "btq-1").labels == ("agent:wsd", f"ws:{WS}", "kind:task")
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OURS
    assert adapter.paused(WS) is False
    assert str(adapter.ws_queue(WS).state).startswith(str(home))
    logged = [json.loads(line) for line in (tmp_path / "bd.log").read_text().splitlines()]
    actors = {argv[argv.index("--actor") + 1] for argv in logged}
    assert all(argv[-1] == "--json" for argv in logged)
    assert actors == {f"wsd:fakehost:{ids.ws_session(WS)}"}       # reads use the workstream worker


def test_btq_without_wsd_agent_is_refused(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "btq").write_text("AGENTS = ('claude',)\nclass Queue: pass\n")
    with pytest.raises(btq.BtqUnavailable):
        btq.load(tmp_path)


def test_missing_btq_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(btq.BtqUnavailable):
        btq.load(tmp_path)
```

- [ ] **Step 5: Create `$HZ/tests/test_wsd_gitwip.py`**

```python
from pathlib import Path

import pytest
from wsd_env import git_repo

from heterodyne.wsd import gitwip


def test_wip_commit_is_idempotent(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    (repo / "a.txt").write_text("x")
    first = gitwip.wip_commit(repo, "op1", "parked btq-1")
    (repo / "b.txt").write_text("later")
    assert gitwip.wip_commit(repo, "op1", "parked btq-1") == first
    assert gitwip.find_wip(repo, "op1") == first
    assert gitwip.find_wip(repo, "op2") is None
    assert "b.txt" in gitwip.git(repo, "status", "--porcelain")


def test_wip_commit_with_nothing_to_commit_still_records_the_mark(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    sha = gitwip.wip_commit(repo, "op1", "parked")
    assert gitwip.find_wip(repo, "op1") == sha


def test_wip_commit_skips_hooks(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    gitwip.wip_commit(repo, "op1", "parked")


def test_git_failure_is_raised(tmp_path: Path) -> None:
    with pytest.raises(gitwip.GitFailed):
        gitwip.wip_commit(tmp_path, "op1", "not a repo")
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_beads.py tests/test_wsd_gitwip.py -q`
Expected: FAIL: `ModuleNotFoundError` for `heterodyne.wsd.btq`, `heterodyne.wsd.beads` or `heterodyne.wsd.gitwip`.

- [ ] **Step 7: Create `$HZ/src/heterodyne/wsd/btq.py`**

```python
"""btq's Queue library, loaded in-process from the configured checkout (ADR 0001 §4.3, §16).

btq is a script without a `.py` suffix, so it is loaded by file. Only the `Queue` class is used, always
as agent `wsd`; `QueueLike` is the slice of it wsd relies on, which the test fake implements too.
"""

import importlib.machinery
import importlib.util
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

AGENT = "wsd"
MODULE_NAME = "_heterodyne_btq"


class BtqUnavailable(Exception):
    """The btq checkout is missing, unloadable, or predates the `wsd` agent (plan 1's btq change)."""


class QueueLike(Protocol):
    worker: str
    state: Path

    def bd(self, *args: str) -> Any: ...
    def show(self, issue_id: str) -> Any: ...
    def ready(self) -> Any: ...
    def claim(self, issue_id: str) -> Any: ...
    def owned(self, issue_id: str, statuses: tuple[str, ...] = ("in_progress",)) -> Any: ...
    def worktree(self, issue_id: str, repository: str) -> Any: ...
    def exclusive(self) -> AbstractContextManager[None]: ...


# (workstream, btq session) -> Queue("wsd", workstream, session, **locations)
QueueFactory = Callable[[str, str], QueueLike]


def load(checkout: Path) -> ModuleType:
    path = checkout / "bin" / "btq"
    try:
        loader = importlib.machinery.SourceFileLoader(MODULE_NAME, str(path))
        spec = importlib.util.spec_from_loader(MODULE_NAME, loader)
        if spec is None:
            raise BtqUnavailable("btq could not be loaded")
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    except (OSError, SyntaxError, ImportError) as exc:
        raise BtqUnavailable(f"btq could not be loaded ({type(exc).__name__})") from None
    if AGENT not in getattr(module, "AGENTS", ()) or not hasattr(module, "Queue"):
        raise BtqUnavailable("this btq has no wsd agent; install plan 1's btq change")
    return module


def factory(module: ModuleType, locations: Mapping[str, str]) -> QueueFactory:
    queue_class = cast(Callable[..., QueueLike], module.Queue)
    overrides = dict(locations)

    def make(ws: str, session: str) -> QueueLike:
        return queue_class(AGENT, ws, session, **overrides)

    return make
```

- [ ] **Step 8: Create `$HZ/src/heterodyne/wsd/gitwip.py`**

```python
"""The git operations of parking (ADR 0001 §4.3): find or make the WIP commit, and inspect a worktree.

The WIP commit message carries the park mark (`wsd-park: <op id>`), so a replayed park finds the commit
it already made instead of making a second one. The commit is local to the `btq/<id>` branch; nothing
is ever pushed (§5.3).
"""

import subprocess
from pathlib import Path

GIT_TIMEOUT = 60
PARK_MARK = "wsd-park: "


class GitFailed(Exception):
    pass


def git(path: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                                timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitFailed(type(exc).__name__) from None
    if result.returncode:
        raise GitFailed(f"git {args[0]} exited {result.returncode}")
    return result.stdout.strip()


def branch(path: Path) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD")


def toplevel(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel")).resolve()


def find_wip(worktree: Path, mark: str) -> str | None:
    """The SHA of the commit on HEAD's history whose message holds the mark, if any. Only the last 50
    commits are searched: a park's own commit is at or near the tip."""
    out = git(worktree, "log", "-n", "50", "--format=%H", "--fixed-strings", f"--grep={PARK_MARK}{mark}")
    shas = out.split()
    return shas[0] if shas else None


def wip_commit(worktree: Path, mark: str, summary: str) -> str:
    """Commit everything in the worktree as WIP and return HEAD. Idempotent: if a commit with the mark
    already exists, return it; if there is nothing to commit, an empty commit still records the mark."""
    found = find_wip(worktree, mark)
    if found is not None:
        return found
    git(worktree, "add", "--all")
    git(worktree, "-c", "user.name=wsd", "-c", "user.email=wsd@localhost", "commit", "--allow-empty",
        "--no-verify", "-m", f"WIP: {summary}\n\n{PARK_MARK}{mark}")
    return git(worktree, "rev-parse", "HEAD")
```

- [ ] **Step 9: Create `$HZ/src/heterodyne/wsd/beads.py`**

```python
"""wsd's view of the beads queue, through btq's Queue library as agent `wsd` (ADR 0001 §4.3, §5.2).

- Ready work is listed with the workstream-session worker; each bead is claimed, owned and parked with
  its own per-bead worker (`ids.bead_session`).
- btq has no library call for labels, dependencies, comments or metadata, so those go through
  `Queue.bd()` of the per-bead worker, after `Queue.owned()` confirms the claim is still ours, under
  that worker's `exclusive()` lock.
- Every write is check, write, read back (`ensure_*`): a step whose effect is already on the bead is not
  repeated, and an uncertain write is only trusted once it reads back (§4.3).
- Fail closed: anything that does not parse as the bd 1.1 JSON shapes recorded in spike S6 raises
  UnexpectedShape, and every infrastructure failure raises BeadsUnavailable. Neither is ever read as
  "absent", "not paused" or "unblocked".
"""

import contextlib
import os
import subprocess
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from heterodyne.wsd import gitwip, ids
from heterodyne.wsd.btq import QueueFactory, QueueLike

PARKED = "v2:parked"
HELD = "v2:held"
NEEDS_HUMAN = "needs-human"
# A blocker carrying one of these labels is an operator ask (an approval, question, picker or permission
# prompt), so a bead parked on it is waiting on input rather than on other work (plans 5 to 7 create them).
OPERATOR_INPUT_LABELS = frozenset({"kind:approval", "kind:question", "kind:confirm"})
# Dependency types that never block. Any other type (`blocks` and anything bd adds later) blocks until
# the other bead is closed: an unknown type is never assumed harmless.
NON_BLOCKING_DEPS = frozenset({"parent-child", "related", "discovered-from"})
BTQ_REFUSALS = ("Finish or release this worker's existing claim first",
                "Task is not eligible for this worker")
BTQ_ROUTING_CHANGED = "Routing/design changed during claim"
BTQ_NOT_OWNED = "Task is not in progress under this worker"
NOT_FOUND = "no issue found matching"
_FAILURES = (RuntimeError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError,
             IndexError, AttributeError)


class BeadsUnavailable(Exception):
    """The queue could not be read or written (Dolt down, bd failed or timed out, credentials missing)."""


class UnexpectedShape(BeadsUnavailable):
    """bd answered, but not in a shape wsd understands; treated exactly like an unreadable queue."""


class ClaimRefused(Exception):
    """btq refused the claim before writing anything."""


class ClaimUncertain(Exception):
    """The claim's outcome is unknown: read it back before doing anything else."""


class RoutingChanged(Exception):
    """btq claimed the bead but its routing or design gate changed meanwhile: never execute it."""


class NotOurs(Exception):
    """The bead is not in progress under this workstream's per-bead worker."""


class WorktreeConflict(Exception):
    """The btq worktree path exists but is not this bead's worktree; it is inspected, never deleted."""


class ClaimView(StrEnum):
    OURS = "ours"      # in_progress, assigned to our per-bead worker
    FREE = "free"      # open and unassigned: the claim did not happen
    OTHER = "other"    # anything else: someone else holds it, or it moved on


@dataclass(frozen=True)
class Dep:
    id: str
    status: str
    kind: str
    labels: tuple[str, ...]

    @property
    def blocking(self) -> bool:
        return self.kind not in NON_BLOCKING_DEPS and self.status != "closed"


@dataclass(frozen=True)
class Bead:
    id: str
    title: str
    status: str
    assignee: str | None
    labels: tuple[str, ...]
    metadata: dict[str, Any]
    deps: tuple[Dep, ...] | None     # None for list output, which carries no dependency status

    def open_blockers(self) -> tuple[Dep, ...]:
        if self.deps is None:
            raise ValueError("dependency detail needs show(), not list()")
        return tuple(d for d in self.deps if d.blocking)

    def waits_on_operator(self) -> bool:
        return any(OPERATOR_INPUT_LABELS & set(d.labels) for d in self.open_blockers())


def _str(raw: dict[str, Any], key: str, required: bool = True) -> str | None:
    value = raw.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise UnexpectedShape(f"bead field {key} is not a string")
    return value


def _labels(raw: dict[str, Any]) -> tuple[str, ...]:
    labels = raw.get("labels", [])      # bd omits the key when there are none
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in cast(list[Any], labels)):
        raise UnexpectedShape("bead labels are not a list of strings")
    return tuple(cast(list[str], labels))


def parse(raw: object, detail: bool) -> Bead:
    """One bead from `bd show` (detail=True) or `bd list` (detail=False) JSON."""
    if not isinstance(raw, dict):
        raise UnexpectedShape("bead is not an object")
    item = cast(dict[str, Any], raw)
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        raise UnexpectedShape("bead metadata is not an object")
    deps: tuple[Dep, ...] | None = None
    if detail:
        raw_deps = item.get("dependencies")
        if raw_deps is None:
            if item.get("dependency_count") != 0:    # bd omits the list only when there are none
                raise UnexpectedShape("bead dependencies are missing")
            raw_deps = []
        if not isinstance(raw_deps, list):
            raise UnexpectedShape("bead dependencies are not a list")
        parsed: list[Dep] = []
        for dep in cast(list[Any], raw_deps):
            if not isinstance(dep, dict):
                raise UnexpectedShape("a dependency is not an object")
            d = cast(dict[str, Any], dep)
            parsed.append(Dep(cast(str, _str(d, "id")), cast(str, _str(d, "status")),
                              cast(str, _str(d, "dependency_type")), _labels(d)))
        deps = tuple(parsed)
    return Bead(cast(str, _str(item, "id")), cast(str, _str(item, "title")), cast(str, _str(item, "status")),
                _str(item, "assignee", required=False) or None, _labels(item),
                cast(dict[str, Any], metadata), deps)


def _one(result: object) -> Bead:
    if not isinstance(result, dict):
        raise UnexpectedShape("show did not return one bead")
    return parse(cast(object, result), detail=True)


class BeadsAdapter:
    def __init__(self, factory: QueueFactory) -> None:
        self._factory = factory
        self._queues: dict[tuple[str, str], QueueLike] = {}
        self._lock = threading.Lock()

    def _queue(self, ws: str, session: str) -> QueueLike:
        with self._lock:
            queue = self._queues.get((ws, session))
            if queue is None:
                try:
                    queue = self._factory(ws, session)
                except _FAILURES as exc:     # credentials unreadable, no `wsd` entry, state dir refused
                    raise BeadsUnavailable(f"btq queue could not be opened ({type(exc).__name__})") from None
                self._queues[(ws, session)] = queue
            return queue

    def ws_queue(self, ws: str) -> QueueLike:
        return self._queue(ws, ids.ws_session(ws))

    def bead_queue(self, ws: str, bead: str) -> QueueLike:
        return self._queue(ws, ids.bead_session(ws, bead))

    @staticmethod
    def _call[T](fn: Callable[[], T]) -> T:
        try:
            return fn()
        except _FAILURES as exc:
            raise BeadsUnavailable(f"queue call failed ({type(exc).__name__})") from None

    # --- reads ---

    def ready(self, ws: str) -> list[Bead]:
        """New work in btq's order. btq returns [] while the workstream worker is paused."""
        queue = self.ws_queue(ws)
        raw = self._call(queue.ready)
        if not isinstance(raw, list):
            raise UnexpectedShape("ready did not return a list")
        return [parse(item, detail=False) for item in cast(list[Any], raw)]

    def show(self, ws: str, bead: str) -> Bead:
        queue = self.ws_queue(ws)
        return _one(self._call(lambda: queue.show(bead)))

    def exists(self, ws: str, bead: str) -> bool:
        """False only when bd says the bead does not exist; any other failure raises."""
        queue = self.ws_queue(ws)
        try:
            queue.show(bead)
        except RuntimeError as exc:
            if NOT_FOUND in str(exc):
                return False
            raise BeadsUnavailable("show failed") from None
        except _FAILURES as exc:
            raise BeadsUnavailable(f"show failed ({type(exc).__name__})") from None
        return True

    def read_claim(self, ws: str, bead: str) -> ClaimView:
        found = self.show(ws, bead)
        if found.status == "in_progress" and found.assignee == self.bead_queue(ws, bead).worker:
            return ClaimView.OURS
        if found.status == "open" and found.assignee is None:
            return ClaimView.FREE
        return ClaimView.OTHER

    def ours(self, ws: str) -> list[Bead]:
        """Every in_progress bead routed to this workstream and held by one of its per-bead workers, with
        dependency detail. A bead whose ID is not a slug can't have been claimed by wsd and is skipped."""
        queue = self.ws_queue(ws)
        raw = self._call(lambda: queue.bd("list", "--label", "agent:wsd", "--label", f"ws:{ws}",
                                          "--status", "in_progress", "--limit", "0"))
        if not isinstance(raw, list):
            raise UnexpectedShape("list did not return a list")
        found: list[Bead] = []
        for item in cast(list[Any], raw):
            listed = parse(item, detail=False)
            if not ids.SLUG.fullmatch(listed.id):
                continue
            if listed.assignee == self.bead_queue(ws, listed.id).worker:
                found.append(self.show(ws, listed.id))
        return found

    def with_metadata(self, ws: str, key: str, values: frozenset[str]) -> list[str]:
        """IDs of unclosed beads labelled `ws:<ws>` whose metadata `key` is one of `values`."""
        queue = self.ws_queue(ws)
        raw = self._call(lambda: queue.bd("list", "--label", f"ws:{ws}",
                                          "--status", "open,in_progress,blocked", "--limit", "0"))
        if not isinstance(raw, list):
            raise UnexpectedShape("list did not return a list")
        beads = [parse(item, detail=False) for item in cast(list[Any], raw)]
        return [b.id for b in beads if b.metadata.get(key) in values]

    def comments(self, ws: str, bead: str) -> list[str]:
        queue = self.ws_queue(ws)
        raw = self._call(lambda: queue.bd("comments", bead))
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise UnexpectedShape("comments did not return a list")
        texts: list[str] = []
        for item in cast(list[Any], raw):
            if not isinstance(item, dict) or not isinstance(cast(dict[str, Any], item).get("text"), str):
                raise UnexpectedShape("a comment has no text")
            texts.append(cast(str, cast(dict[str, Any], item)["text"]))
        return texts

    # --- the shared pause flag (§4.3) ---

    def paused(self, ws: str) -> bool:
        """True unless the flag is confirmed absent: an unreadable state directory counts as paused."""
        flag = self.ws_queue(ws).state / "paused"
        try:
            os.lstat(flag)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True

    def set_paused(self, ws: str, paused: bool) -> None:
        flag = self.ws_queue(ws).state / "paused"
        try:
            if paused:
                flag.touch()
            else:
                flag.unlink(missing_ok=True)
        except OSError as exc:
            raise BeadsUnavailable(f"pause flag not written ({type(exc).__name__})") from None
        if self.paused(ws) != paused:
            raise BeadsUnavailable("pause flag did not read back")

    # --- claim (§4.3) ---

    def claim(self, ws: str, bead: str) -> Bead:
        """Claim with the per-bead worker. ClaimRefused means nothing was written; ClaimUncertain means the
        caller must `read_claim` before anything else; RoutingChanged means the claim is ours but the
        bead must not be executed."""
        queue = self.bead_queue(ws, bead)
        try:
            return _one(queue.claim(bead))
        except ValueError as exc:
            if str(exc).startswith(BTQ_REFUSALS):
                raise ClaimRefused(str(exc)) from None
            raise ClaimUncertain(type(exc).__name__) from None
        except RuntimeError as exc:
            if str(exc).startswith(BTQ_ROUTING_CHANGED):
                raise RoutingChanged(bead) from None
            raise ClaimUncertain(type(exc).__name__) from None
        except _FAILURES as exc:
            raise ClaimUncertain(type(exc).__name__) from None

    # --- owned writes ---

    @contextlib.contextmanager
    def _owned(self, ws: str, bead: str) -> Generator[QueueLike]:
        queue = self.bead_queue(ws, bead)
        try:
            with queue.exclusive():
                try:
                    queue.owned(bead)
                except ValueError as exc:
                    if str(exc).startswith(BTQ_NOT_OWNED):
                        raise NotOurs(bead) from None
                    raise
                yield queue
        except (NotOurs, BeadsUnavailable):
            raise
        except _FAILURES as exc:
            raise BeadsUnavailable(f"queue write failed ({type(exc).__name__})") from None

    def _write(self, ws: str, bead: str, *args: str) -> None:
        with self._owned(ws, bead) as queue:
            queue.bd(*args)

    def ensure_label(self, ws: str, bead: str, label: str, present: bool = True) -> None:
        if (label in self.show(ws, bead).labels) == present:
            return
        self._write(ws, bead, "label", "add" if present else "remove", bead, label)
        if (label in self.show(ws, bead).labels) != present:
            raise BeadsUnavailable("label change did not read back")

    def ensure_blocker(self, ws: str, bead: str, blocker: str) -> None:
        def has() -> bool:
            deps = self.show(ws, bead).deps or ()
            return any(d.id == blocker and d.kind not in NON_BLOCKING_DEPS for d in deps)
        if has():
            return
        self._write(ws, bead, "dep", "add", bead, blocker)
        if not has():
            raise BeadsUnavailable("blocking edge did not read back")

    def ensure_comment(self, ws: str, bead: str, mark: str, text: str) -> None:
        """Comments are not idempotent in bd, so `mark` (unique per write, and part of `text`) is looked for
        first and the comment is added only if no comment holds it."""
        if mark not in text:
            raise ValueError("the comment text must contain its mark")
        if any(mark in c for c in self.comments(ws, bead)):
            return
        self._write(ws, bead, "comments", "add", bead, text)
        if not any(mark in c for c in self.comments(ws, bead)):
            raise BeadsUnavailable("comment did not read back")

    def ensure_metadata(self, ws: str, bead: str, key: str, value: str) -> None:
        if self.show(ws, bead).metadata.get(key) == value:
            return
        self._write(ws, bead, "update", bead, "--set-metadata", f"{key}={value}")
        if self.show(ws, bead).metadata.get(key) != value:
            raise BeadsUnavailable("metadata did not read back")

    # --- worktrees (§4.3: btq's convention) ---

    def worktree(self, ws: str, bead: str, repository: Path) -> Path:
        """The bead's btq worktree, `<repo>-btq-<id>` on branch `btq/<id>`. Idempotent: an existing path
        is reused only if it is that worktree on that branch; anything else is a WorktreeConflict."""
        ids.slug(bead, "bead")
        try:
            repo = repository.resolve(strict=True)
        except OSError:
            raise WorktreeConflict("repository missing") from None
        destination = repo.parent / f"{repo.name}-btq-{bead}"
        if os.path.lexists(destination):
            return self._existing_worktree(destination, bead)
        queue = self.bead_queue(ws, bead)
        try:
            result = queue.worktree(bead, str(repo))
        except ValueError as exc:
            if str(exc).startswith(BTQ_NOT_OWNED):
                raise NotOurs(bead) from None
            raise BeadsUnavailable(type(exc).__name__) from None
        except _FAILURES as exc:
            if os.path.lexists(destination):    # git made it, then the bead note failed: reuse it
                return self._existing_worktree(destination, bead)
            raise BeadsUnavailable(f"worktree failed ({type(exc).__name__})") from None
        if not isinstance(result, dict) or cast(dict[str, Any], result).get("worktree") != str(destination):
            raise UnexpectedShape("worktree result")
        return destination

    @staticmethod
    def _existing_worktree(destination: Path, bead: str) -> Path:
        try:
            if (destination.is_dir() and not destination.is_symlink()
                    and gitwip.toplevel(destination) == destination.resolve()
                    and gitwip.branch(destination) == f"btq/{bead}"):
                return destination
        except gitwip.GitFailed:
            pass
        raise WorktreeConflict("worktree path is not this bead's worktree")
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_beads.py tests/test_wsd_gitwip.py -q`
Expected: `30 passed, 1 skipped`. Then `BTQ_REPO=$BTQ_REPO uv run pytest tests/test_wsd_beads.py -q` runs the contract test too: `27 passed`.

- [ ] **Step 11: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 12: Commit**

```bash
cd $HZ
git add docs/spikes/S6-beads-json.md src/heterodyne/wsd/btq.py src/heterodyne/wsd/gitwip.py src/heterodyne/wsd/beads.py tests/fakes/fake_btq.py tests/wsd_env.py tests/test_wsd_beads.py tests/test_wsd_gitwip.py
git commit -m "feat(wsd): beads adapter through btq's Queue as agent wsd, with read-back writes"
```

### Task 5: Claim gate, pause, and the runtime and reconciler seams

**Files:**
- Create: `$HZ/src/heterodyne/wsd/gate.py`, `$HZ/src/heterodyne/wsd/runtime.py`, `$HZ/tests/fakes/fake_runtime.py`
- Test: `$HZ/tests/test_wsd_gate.py`, `$HZ/tests/test_wsd_runtime.py`

**Interfaces:**
- Consumes: `private_dir` (Task 1); `ids.slug` (Task 1); `Checkpoint`, `nothing` (Task 1); `BeadsAdapter`, `Bead`, `BeadsUnavailable` (Task 4).
- Produces:
  - `heterodyne.wsd.gate`: `class Paused(Exception)`, `class AlreadyRunning(Exception)`, `class ClaimGate(lock_dir: Path, beads: BeadsAdapter, cp: Checkpoint = nothing)` with `locked(ws)` (context manager; a per-workstream `flock`), `pause(ws)`, `resume(ws)`, `claim(ws, bead) -> Bead` (re-checks the flag inside the lock, then checkpoint `gate.checked`, then claims); `instance_lock(path: Path) -> int`.
  - `heterodyne.wsd.runtime` (**the plan 4 and plan 5 seams**): `ACTION_STATE = "action_state"`, `UNSETTLED_ACTIONS = {"executing", "uncertain"}`; `class Liveness(StrEnum)`: `LIVE, DEAD, UNKNOWN`; frozen `LaunchSpec(ws, bead, role, profile, session_key, label, worktree, resume, ref=None)`; `RuntimeUnavailable`, `LaunchFailed`; `class AgentRuntime(Protocol)`: `available() -> bool`, `launch(spec) -> None`, `liveness(session_key) -> Liveness`, `stop(session_key) -> None`; `NoRuntime`; `class ActionReconciler(Protocol)`: `unresolved(ws) -> list[str]`; `HoldingReconciler(beads)`.
  - `tests/fakes/fake_runtime.py`: `FakeRuntime` (`up`, `sessions`, `launches`, `stops`, `launch_failures`, `stop_failures`, `live()`).

- [ ] **Step 1: Create `$HZ/tests/test_wsd_gate.py`**

`test_pause_waits_for_the_claim_lock` holds the claim lock as an in-flight claim would and shows the pause is not acknowledged until the lock is released.

```python
import os
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import Recorder
from fakes.fake_btq import World, factory

from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.gate import AlreadyRunning, ClaimGate, Paused, instance_lock

WS = "alpha"


def gate(tmp_path: Path, cp: Recorder | None = None) -> tuple[World, BeadsAdapter, ClaimGate]:
    world = World(tmp_path / "btq-state")
    beads = BeadsAdapter(factory(world))
    return world, beads, ClaimGate(tmp_path / "claims", beads, cp or Recorder())


def test_claim_checks_the_flag_then_claims(tmp_path: Path) -> None:
    cp = Recorder()
    world, _, g = gate(tmp_path, cp)
    world.add("btq-1")
    assert g.claim(WS, "btq-1").status == "in_progress"
    assert cp.seen == ["gate.checked"]


def test_claim_rechecks_flag_inside_lock(tmp_path: Path) -> None:
    world, beads, g = gate(tmp_path)
    world.add("btq-1")
    beads.set_paused(WS, True)          # a direct `btq pause`: no lock taken
    with pytest.raises(Paused):
        g.claim(WS, "btq-1")
    assert world.claims == []


def test_pause_and_resume_set_the_shared_flag(tmp_path: Path) -> None:
    _, beads, g = gate(tmp_path)
    g.pause(WS)
    assert (beads.ws_queue(WS).state / "paused").exists()
    g.resume(WS)
    assert not beads.paused(WS)


def test_pause_waits_for_the_claim_lock(tmp_path: Path) -> None:
    _, beads, g = gate(tmp_path)
    acked = threading.Event()
    with g.locked(WS):                  # a claim in flight
        pauser = threading.Thread(target=lambda: (g.pause(WS), acked.set()))
        pauser.start()
        assert not acked.wait(0.3)
        assert not beads.paused(WS)
    pauser.join(5)
    assert acked.is_set() and beads.paused(WS)


def test_claim_lock_file_is_private(tmp_path: Path) -> None:
    _, _, g = gate(tmp_path)
    with g.locked(WS):
        pass
    assert (tmp_path / "claims").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "claims" / f"{WS}.claim").stat().st_mode & 0o777 == 0o600


def test_instance_lock_is_exclusive(tmp_path: Path) -> None:
    fd = instance_lock(tmp_path / "state" / "wsd.lock")
    with pytest.raises(AlreadyRunning):
        instance_lock(tmp_path / "state" / "wsd.lock")
    os.close(fd)
    os.close(instance_lock(tmp_path / "state" / "wsd.lock"))
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_runtime.py`**

```python
from pathlib import Path

import pytest
from fakes.fake_btq import World, factory

from heterodyne.wsd.beads import BeadsAdapter, BeadsUnavailable
from heterodyne.wsd.runtime import HoldingReconciler, Liveness, NoRuntime, RuntimeUnavailable


def test_no_runtime_is_unavailable_and_never_dead() -> None:
    runtime = NoRuntime()
    assert not runtime.available()
    assert runtime.liveness("any") is Liveness.UNKNOWN
    with pytest.raises(RuntimeUnavailable):
        runtime.stop("any")


def test_holding_reconciler_reports_every_unsettled_action(tmp_path: Path) -> None:
    world = World(tmp_path / "btq-state")
    world.add("btq-a", metadata={"action_state": "executing"})
    world.add("btq-b", metadata={"action_state": "uncertain"})
    world.add("btq-c", metadata={"action_state": "committed"})
    world.add("btq-d", ws="beta", metadata={"action_state": "uncertain"})
    world.add("btq-e", status="closed", metadata={"action_state": "uncertain"})
    assert HoldingReconciler(BeadsAdapter(factory(world))).unresolved("alpha") == ["btq-a", "btq-b"]


def test_holding_reconciler_fails_closed(tmp_path: Path) -> None:
    world = World(tmp_path / "btq-state")
    adapter = BeadsAdapter(factory(world))
    adapter.ws_queue("alpha")
    world.down = True
    with pytest.raises(BeadsUnavailable):
        HoldingReconciler(adapter).unresolved("alpha")
```

- [ ] **Step 3: Create `$HZ/tests/fakes/fake_runtime.py`**

```python
"""A recording AgentRuntime (plan 4 provides the real one). Sessions survive a simulated wsd restart,
like real agent processes do."""

from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec, Liveness, RuntimeUnavailable


class FakeRuntime:
    def __init__(self) -> None:
        self.up = True
        self.sessions: dict[str, Liveness] = {}
        self.launches: list[LaunchSpec] = []
        self.stops: list[str] = []
        self.launch_failures = 0      # the next N launches fail
        self.stop_failures = 0

    def available(self) -> bool:
        return self.up

    def launch(self, spec: LaunchSpec) -> None:
        if not self.up:
            raise RuntimeUnavailable("down")
        if self.launch_failures:
            self.launch_failures -= 1
            raise LaunchFailed("launch failed")
        if self.sessions.get(spec.session_key) is Liveness.LIVE:
            return
        self.launches.append(spec)
        self.sessions[spec.session_key] = Liveness.LIVE

    def liveness(self, session_key: str) -> Liveness:
        return self.sessions.get(session_key, Liveness.DEAD)

    def stop(self, session_key: str) -> None:
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeUnavailable("stop not confirmed")
        self.stops.append(session_key)
        if session_key in self.sessions:
            self.sessions[session_key] = Liveness.DEAD

    def live(self) -> list[str]:
        return [k for k, v in self.sessions.items() if v is Liveness.LIVE]
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_gate.py tests/test_wsd_runtime.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.gate'` (and `heterodyne.wsd.runtime`).

- [ ] **Step 5: Create `$HZ/src/heterodyne/wsd/gate.py`**

```python
"""The shared pause gate and the per-workstream claim lock (ADR 0001 §4.3).

Pause is the btq `paused` flag of the workstream-session worker. Every claim runs inside the
workstream's claim lock and re-checks that flag immediately before `claim()`; pausing takes the same
lock before it sets the flag and acknowledges. So once a pause is acknowledged, no claim can start. The
lock is an flock on a file in wsd's state directory, so `wsctl` (another process) and wsd share it.
A direct `btq pause` sets the same flag without the lock: honoured from the next claim check (best effort).
"""

import contextlib
import fcntl
import os
from collections.abc import Generator
from pathlib import Path

from heterodyne.fsutil import private_dir
from heterodyne.wsd import ids
from heterodyne.wsd.beads import Bead, BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint, nothing

POINTS = ("gate.checked",)


class Paused(Exception):
    """The workstream is paused: no claim was attempted."""


class AlreadyRunning(Exception):
    """Another wsd holds the instance lock for this state directory."""


class ClaimGate:
    def __init__(self, lock_dir: Path, beads: BeadsAdapter, cp: Checkpoint = nothing) -> None:
        self.lock_dir = lock_dir
        self.beads = beads
        self.cp = cp

    @contextlib.contextmanager
    def locked(self, ws: str) -> Generator[None]:
        """The workstream's claim lock. A fresh descriptor per use, so threads exclude each other too."""
        private_dir(self.lock_dir)
        fd = os.open(self.lock_dir / f"{ids.slug(ws, 'workstream')}.claim",
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)        # closing the descriptor releases the lock

    def pause(self, ws: str) -> None:
        """Returns (acknowledges) only once the flag reads back, with no claim in flight."""
        with self.locked(ws):
            self.beads.set_paused(ws, True)

    def resume(self, ws: str) -> None:
        with self.locked(ws):
            self.beads.set_paused(ws, False)

    def claim(self, ws: str, bead: str) -> Bead:
        with self.locked(ws):
            if self.beads.paused(ws):
                raise Paused(ws)
            self.cp("gate.checked")
            return self.beads.claim(ws, bead)


def instance_lock(path: Path) -> int:
    """Take the single-instance lock for a wsd state directory; the descriptor is held for the process's
    life. Two wsd processes on one journal would each believe they own every in-flight operation."""
    private_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise AlreadyRunning(str(path.name)) from None
    return fd
```

- [ ] **Step 6: Create `$HZ/src/heterodyne/wsd/runtime.py`**

```python
"""Seams to later plans: the agent runtime (plan 4) and action reconciliation (plan 5).

Plan 3 never launches an agent itself. It calls `AgentRuntime`, which plan 4 implements (adapters and
the sandbox backend chosen in ADR revision 14). Nothing here names an adapter or a sandbox. Until a real
runtime is wired in, `NoRuntime` reports itself unavailable, and wsd holds every workstream rather than
claim work it can't run.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from heterodyne.wsd.beads import BeadsAdapter

ACTION_STATE = "action_state"
UNSETTLED_ACTIONS = frozenset({"executing", "uncertain"})


class Liveness(StrEnum):
    LIVE = "live"
    DEAD = "dead"
    UNKNOWN = "unknown"     # can't tell: never treated as dead


@dataclass(frozen=True)
class LaunchSpec:
    ws: str
    bead: str
    role: str
    profile: str
    session_key: str        # ids.role_session(bead, role, profile)
    label: str              # "<bead> · <role> · <title>" (§4.1)
    worktree: Path
    resume: bool            # resume the same session rather than start one
    ref: str | None = None  # the message or event that caused the launch, for progress reactions


class RuntimeUnavailable(Exception):
    """No runtime can launch anything right now (none configured, or its backend is down)."""


class LaunchFailed(Exception):
    """This launch failed; the reason is the exception text (fixed wording, no secrets)."""


class AgentRuntime(Protocol):
    def available(self) -> bool:
        """Whether launches can be attempted at all. Checked before every claim."""
        ...

    def launch(self, spec: LaunchSpec) -> None:
        """Start (or, with `spec.resume`, resume) the session. Idempotent on `spec.session_key`: a live
        session is left alone. Returns once the session is live; raises LaunchFailed or
        RuntimeUnavailable otherwise."""
        ...

    def liveness(self, session_key: str) -> Liveness:
        """DEAD only when the runtime knows no session with this key is running (including one it never
        launched); UNKNOWN whenever it can't tell. wsd treats UNKNOWN as possibly live."""
        ...

    def stop(self, session_key: str) -> None:
        """Interrupt the session and wait until it has ended. Idempotent: an ended session is fine.
        Raises RuntimeUnavailable if it can't confirm the session has ended."""
        ...


class NoRuntime:
    """The runtime until plan 4: launches nothing, and never claims a session is dead."""

    def available(self) -> bool:
        return False

    def launch(self, spec: LaunchSpec) -> None:
        raise RuntimeUnavailable("no agent runtime is configured")

    def liveness(self, session_key: str) -> Liveness:
        return Liveness.UNKNOWN

    def stop(self, session_key: str) -> None:
        raise RuntimeUnavailable("no agent runtime is configured")


class ActionReconciler(Protocol):
    def unresolved(self, ws: str) -> list[str]:
        """IDs of approval beads whose action is `executing` or `uncertain` and not yet reconciled
        against its target (§5.4). Raises BeadsUnavailable if that can't be established."""
        ...


class HoldingReconciler:
    """Plan 3's reconciler: it can't check targets (plan 5 can), so it only finds unsettled actions and
    reports every one as unresolved. wsd holds the workstream until they are settled; it never assumes
    there are none."""

    def __init__(self, beads: BeadsAdapter) -> None:
        self.beads = beads

    def unresolved(self, ws: str) -> list[str]:
        return self.beads.with_metadata(ws, ACTION_STATE, UNSETTLED_ACTIONS)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_gate.py tests/test_wsd_runtime.py -q`
Expected: `9 passed`.

- [ ] **Step 8: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 9: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/gate.py src/heterodyne/wsd/runtime.py tests/fakes/fake_runtime.py tests/test_wsd_gate.py tests/test_wsd_runtime.py
git commit -m "feat(wsd): claim gate with the shared pause flag; runtime and reconciler seams"
```

### Task 6: Park and resume journals

**Files:**
- Create: `$HZ/src/heterodyne/wsd/workstream.py`, `$HZ/src/heterodyne/wsd/park.py`
- Modify: `$HZ/tests/wsd_env.py` (replace it with the test rig)
- Test: `$HZ/tests/test_wsd_park.py`

**Interfaces:**
- Consumes: `ids` (Task 1); `Journal`, `Op`, `OpKind`, `OpStatus` (Task 3); `BeadsAdapter`, `Bead`, labels and exceptions (Task 4); `gitwip.wip_commit`, `GitFailed` (Task 4); `ClaimGate` (Task 5); `AgentRuntime`, `ActionReconciler`, `LaunchSpec`, `LaunchFailed`, `RuntimeUnavailable` (Task 5).
- Produces:
  - `heterodyne.wsd.workstream`: `REPO_KEY = "repo"`, `DEFAULT_REPO = "default"`, `class ConfigInvalid(Exception)`, frozen `Limits(launch_failures_before_human=2, park_attempts_before_human=3)`, frozen `WorkstreamSettings(name, repos: dict[str, Path], coder_role, coder_profile, profiles: frozenset[str], limits=Limits())`, frozen `Placement(repo, worktree, profile, session_key, label)`, `place(ws, bead) -> Placement`, frozen `Deps(journal, beads, gate, runtime, reconciler, cp=nothing)`.
  - `heterodyne.wsd.park`: `PARK_POINTS = ("park.intent", "park.stopped", "park.committed", "park.blocked", "park.labelled", "park.commented")`, `RESUME_POINTS = ("resume.intent", "resume.unlabelled", "resume.launched")`, `PARK_MARK = "wsd-park: "`, `parked_state(bead) -> BeadState`, `resumable(bead) -> bool`, `class Parker(ws, deps)` with `park(bead, blockers: tuple[str, ...], why="", hold=False, ref=None) -> BeadState` (**the plan 5/6 seam**), `resume(bead: Bead, ref=None) -> BeadState`, `replay(op) -> BeadState`, `escalate(bead)`.
  - `tests/wsd_env.py`: `PROFILES`, `Rig` (`world`, `runtime`, `repo`, `ws`, `cp`, `journal`, `beads`, `gate`, `deps`, `parker`; `restart(cp=None)`, `start(bead)`, `replay_open()`, `worktree(bead)`, `state(bead)`), `make_rig(tmp_path, cp=None, limits=None) -> Rig`.

Park: intent → stop the session → WIP commit (SHA recorded) → blocking edges → labels (`v2:held` first when held, then `v2:parked`) → bead comment. Resume: intent → re-check resumable → remove `v2:parked` → launch the same session key in the same worktree. Each step is journaled when it completes; the crash tests restart at every point and replay.

- [ ] **Step 1: Create `$HZ/tests/wsd_env.py`**

Replace the Task 4 file. `restart()` models wsd dying and starting again: a new `Journal` on the same file and new wsd objects, while the queue, the repository and the agent sessions carry on. `start()` does what a pickup does for one bead without the pickup journal (Task 7 adds pickup).

```python
"""A wsd test rig: a fake queue, a fake runtime, a real temp git repository and a real journal.

`restart()` models wsd dying and starting again: a new Journal on the same file and new wsd objects,
while the queue, the repository and the agent sessions (other processes) carry on.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from fakes.checkpoints import Recorder
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime

from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import ActionReconciler, HoldingReconciler, LaunchSpec
from heterodyne.wsd.states import BeadState
from heterodyne.wsd.workstream import Deps, Limits, WorkstreamSettings, place

WS = "alpha"
PROFILES = frozenset({"p-one", "p-two"})


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@example.org",
                                                   "commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


@dataclass
class Rig:
    root: Path
    world: World
    runtime: FakeRuntime
    repo: Path
    ws: WorkstreamSettings
    cp: Checkpoint = field(default_factory=Recorder)
    reconciler: ActionReconciler | None = None
    journal: Journal = field(init=False)
    beads: BeadsAdapter = field(init=False)
    gate: ClaimGate = field(init=False)
    deps: Deps = field(init=False)
    parker: Parker = field(init=False)

    def __post_init__(self) -> None:
        self.restart(self.cp)

    def restart(self, cp: Checkpoint | None = None) -> None:
        if hasattr(self, "journal"):
            self.journal.close()
        self.cp = cp if cp is not None else Recorder()
        self.journal = Journal(self.root / "state" / "wsd.db")
        self.beads = BeadsAdapter(factory(self.world))
        self.gate = ClaimGate(self.root / "state" / "claims", self.beads, self.cp)
        self.deps = Deps(self.journal, self.beads, self.gate, self.runtime,
                         self.reconciler or HoldingReconciler(self.beads), self.cp)
        self.parker = Parker(self.ws, self.deps)

    def start(self, bead: str) -> None:
        """What a pickup does for one bead, without the pickup journal: claim, worktree, launch."""
        self.gate.claim(WS, bead)
        spot = place(self.ws, self.beads.show(WS, bead))
        self.beads.worktree(WS, bead, spot.repo)
        self.runtime.launch(LaunchSpec(WS, bead, self.ws.coder_role, spot.profile, spot.session_key,
                                       spot.label, spot.worktree, resume=False))
        self.journal.set_state(WS, bead, BeadState.RUNNING)

    def replay_open(self) -> None:
        for op in self.journal.ops_open(WS):
            self.parker.replay(op)

    def worktree(self, bead: str) -> Path:
        return self.repo.parent / f"{self.repo.name}-btq-{bead}"

    def state(self, bead: str) -> str | None:
        row = self.journal.state(WS, bead)
        return None if row is None else row.state.value


def make_rig(tmp_path: Path, cp: Checkpoint | None = None, limits: Limits | None = None) -> Rig:
    world = World(tmp_path / "btq-state")
    repo = git_repo(tmp_path / "repos" / "proj")
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES, limits or Limits())
    return Rig(tmp_path, world, FakeRuntime(), repo, ws, cp if cp is not None else Recorder())
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_park.py`**

```python
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, Recorder, SimulatedCrash
from wsd_env import WS, Rig, make_rig

from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, BeadsUnavailable
from heterodyne.wsd.park import PARK_MARK, PARK_POINTS, RESUME_POINTS, resumable
from heterodyne.wsd.states import Reason
from heterodyne.wsd.workstream import Limits


def running(tmp_path: Path, cp: Recorder | None = None, limits: Limits | None = None) -> Rig:
    rig = make_rig(tmp_path, cp=cp, limits=limits)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")       # the bead btq-1 will wait on
    rig.start("btq-1")
    (rig.worktree("btq-1") / "work.txt").write_text("half done")
    return rig


def wip_commits(rig: Rig) -> list[str]:
    out = gitwip.git(rig.worktree("btq-1"), "log", "--format=%H", f"--grep={PARK_MARK}")
    return out.split()


def test_park_sequence(tmp_path: Path) -> None:
    rig = running(tmp_path)
    key = rig.runtime.launches[0].session_key
    assert rig.parker.park("btq-1", ("btq-2",), why="needs the schema", ref="msg-7").value == "parked"
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p.startswith("park.")] == list(PARK_POINTS)
    bead = rig.world.beads["btq-1"]
    assert bead.status == "in_progress"                            # never unclaimed
    assert PARKED in bead.labels and ("btq-2", "blocks") in bead.deps
    assert len(wip_commits(rig)) == 1
    assert gitwip.git(rig.worktree("btq-1"), "status", "--porcelain") == ""
    [comment] = bead.comments
    assert "btq-2" in comment and "needs the schema" in comment
    assert rig.runtime.stops == [key]
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "parked" and row.reason is Reason.BLOCKED_ON_BEAD
    progress = [(e.kind, e.ref) for e in rig.journal.events_since(0) if e.bead == "btq-1"]
    assert ("state:parking", "msg-7") in progress and ("state:parked", "msg-7") in progress
    assert rig.journal.ops_open() == []


@pytest.mark.parametrize("point", PARK_POINTS)
def test_crash_at_every_park_point_completes_once(tmp_path: Path, point: str) -> None:
    rig = running(tmp_path, cp=CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",), why="w")
    rig.restart()
    rig.replay_open()
    bead = rig.world.beads["btq-1"]
    assert PARKED in bead.labels and bead.deps == [("btq-2", "blocks")]
    assert len(bead.comments) == 1
    assert len(wip_commits(rig)) == 1
    assert rig.state("btq-1") == "parked"
    assert rig.journal.ops_open() == []
    assert len(rig.runtime.launches) == 1                           # never relaunched while parked


def test_resume_sequence(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    bead = rig.beads.show(WS, "btq-1")
    assert resumable(bead)
    assert rig.parker.resume(bead, ref="msg-9").value == "running"
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p.startswith("resume.")] == list(RESUME_POINTS)
    spec = rig.runtime.launches[-1]
    assert (spec.bead, spec.resume, spec.ref) == ("btq-1", True, "msg-9")
    assert spec.worktree == rig.worktree("btq-1")
    assert spec.session_key == rig.runtime.launches[0].session_key
    assert PARKED not in rig.world.beads["btq-1"].labels


@pytest.mark.parametrize("point", RESUME_POINTS)
def test_crash_at_every_resume_point_launches_once(tmp_path: Path, point: str) -> None:
    rig = running(tmp_path)
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.resume(rig.beads.show(WS, "btq-1"))
    rig.restart()
    rig.replay_open()
    resumes = [s for s in rig.runtime.launches if s.resume]
    assert len(resumes) == 1
    assert PARKED not in rig.world.beads["btq-1"].labels
    assert rig.state("btq-1") == "running"
    assert rig.journal.ops_open() == []


def test_resume_abandoned_when_blocked_again(tmp_path: Path) -> None:
    """Between the decision to resume and the replay, the bead gained an open blocker: the resume is
    abandoned with the bead still parked, and nothing is launched."""
    rig = running(tmp_path)
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    bead = rig.beads.show(WS, "btq-1")
    rig.world.add("btq-3")
    rig.world.beads["btq-1"].deps.append(("btq-3", "blocks"))
    assert rig.parker.resume(bead).value == "parked"
    assert [s.resume for s in rig.runtime.launches] == [False]
    assert PARKED in rig.world.beads["btq-1"].labels


def test_operator_hold_is_not_resumable(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    bead = rig.beads.show(WS, "btq-1")
    assert HELD in bead.labels and not resumable(bead)
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "held" and row.reason is Reason.HELD_BY_OPERATOR


def test_question_blocker_is_waiting_input(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.world.add("btq-q", labels=["kind:question"])
    rig.world.beads["btq-q"].labels.remove("agent:wsd")
    rig.parker.park("btq-1", ("btq-q",), why="which schema?")
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "waiting_input" and row.reason is Reason.WAITING_ON_OPERATOR


def test_park_needs_a_blocker_or_a_hold(tmp_path: Path) -> None:
    rig = running(tmp_path)
    with pytest.raises(ValueError, match="blocker"):
        rig.parker.park("btq-1", ())
    assert rig.journal.ops_open() == []


def test_unconfirmed_stop_retries_then_escalates(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=2))
    rig.runtime.stop_failures = 5
    assert rig.parker.park("btq-1", ("btq-2",)).value == "parking"
    assert PARKED not in rig.world.beads["btq-1"].labels           # no label before the session stopped
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.PARK_FAILED
    rig.replay_open()                                               # second failure
    assert rig.state("btq-1") == "stuck"
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_beads_outage_during_park_never_escalates(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.world.fault("dep", RuntimeError("dolt down"), times=2)
    for _ in range(2):
        with pytest.raises(BeadsUnavailable):
            rig.replay_open() if rig.journal.ops_open() else rig.parker.park("btq-1", ("btq-2",))
        assert rig.state("btq-1") == "parking"
        assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    rig.replay_open()
    assert rig.state("btq-1") == "parked"


def test_park_of_a_bead_not_ours_is_abandoned(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.world.beads["btq-1"].assignee = "someone-else"
    assert rig.parker.park("btq-1", ("btq-2",)).value == "stuck"
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.CLAIM_LOST
    assert PARKED not in rig.world.beads["btq-1"].labels
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_park.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.park'` (or `heterodyne.wsd.workstream`).

- [ ] **Step 4: Create `$HZ/src/heterodyne/wsd/workstream.py`**

```python
"""One workstream's settings, and where a bead runs: repository, worktree, profile and session key."""

from dataclasses import dataclass, field
from pathlib import Path

from heterodyne.wsd import ids
from heterodyne.wsd.beads import Bead, BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.runtime import ActionReconciler, AgentRuntime

REPO_KEY = "repo"            # bead metadata naming one of the workstream's [repos]
DEFAULT_REPO = "default"
LABEL_TITLE_CHARS = 40


class ConfigInvalid(Exception):
    """The bead can't be placed: no such repository, or an unknown or ambiguous profile."""


@dataclass(frozen=True)
class Limits:
    launch_failures_before_human: int = 2      # §9 reconcile, §10 agent crash
    park_attempts_before_human: int = 3


@dataclass(frozen=True)
class WorkstreamSettings:
    name: str
    repos: dict[str, Path]
    coder_role: str
    coder_profile: str
    profiles: frozenset[str]
    limits: Limits = field(default_factory=Limits)


@dataclass(frozen=True)
class Placement:
    repo: Path
    worktree: Path
    profile: str
    session_key: str
    label: str


def place(ws: WorkstreamSettings, bead: Bead) -> Placement:
    name = bead.metadata.get(REPO_KEY, DEFAULT_REPO)
    if not isinstance(name, str) or name not in ws.repos:
        raise ConfigInvalid("the bead names no repository of this workstream")
    try:
        profile = ids.profile_for(bead.labels, ws.coder_role, ws.coder_profile)
        session_key = ids.role_session(bead.id, ws.coder_role, profile)
    except ids.BadName as exc:
        raise ConfigInvalid(str(exc)) from None
    if profile not in ws.profiles:
        raise ConfigInvalid("the bead names an unknown profile")
    repo = ws.repos[name].resolve()
    title = " ".join(bead.title.split())[:LABEL_TITLE_CHARS]
    return Placement(repo, repo.parent / f"{repo.name}-btq-{bead.id}", profile, session_key,
                     f"{bead.id} · {ws.coder_role} · {title}")


@dataclass(frozen=True)
class Deps:
    journal: Journal
    beads: BeadsAdapter
    gate: ClaimGate
    runtime: AgentRuntime
    reconciler: ActionReconciler
    cp: Checkpoint = nothing
```

- [ ] **Step 5: Create `$HZ/src/heterodyne/wsd/park.py`**

```python
"""The park and resume journals (ADR 0001 §3.3, §4.3).

Park: intent, stop the session, WIP commit (SHA recorded), blocking edges, labels, bead comment. Resume:
intent, remove `v2:parked`, launch the same session in the same worktree. Each step is journaled when
it completes and is idempotent, so a crash anywhere is replayed from the step reached. Blocking edges go
on before `v2:parked`, and `v2:held` before `v2:parked`, so the bead is never `v2:parked` without what
keeps it from being resumed.

wsd never unclaims: a parked bead stays `in_progress` under its per-bead worker. BeadsUnavailable is
never caught here: it propagates, and the caller holds the workstream until beads answer again (§10),
so an outage never escalates a bead.
"""

from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, Bead, BeadsUnavailable, NotOurs
from heterodyne.wsd.journal import Op, OpKind, OpStatus
from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec, RuntimeUnavailable
from heterodyne.wsd.states import BeadState, Reason
from heterodyne.wsd.workstream import ConfigInvalid, Deps, WorkstreamSettings, place

PARK_POINTS = ("park.intent", "park.stopped", "park.committed", "park.blocked", "park.labelled",
               "park.commented")
RESUME_POINTS = ("resume.intent", "resume.unlabelled", "resume.launched")
PARK_MARK = "wsd-park: "
REASONS = {BeadState.HELD: Reason.HELD_BY_OPERATOR, BeadState.WAITING_INPUT: Reason.WAITING_ON_OPERATOR,
           BeadState.PARKED: Reason.BLOCKED_ON_BEAD}


def park_comment(op: Op) -> str:
    blockers = op.data.get("blockers") or "none"
    why = op.data.get("why") or "no reason given"
    return (f"Parked by wsd ({PARK_MARK}{op.op_id}). WIP commit {op.data.get('sha', '?')}. "
            f"Waiting on: {blockers}. Why: {why}.")


def parked_state(bead: Bead) -> BeadState:
    """What a `v2:parked` bead is waiting for, from its labels and open blockers."""
    if HELD in bead.labels:
        return BeadState.HELD
    if bead.waits_on_operator():
        return BeadState.WAITING_INPUT
    return BeadState.PARKED


def resumable(bead: Bead) -> bool:
    """§4.3: claimed by wsd (the caller checked), `v2:parked`, every blocking edge closed. A bead held by
    the operator or escalated to a human is not resumable."""
    return (PARKED in bead.labels and HELD not in bead.labels and NEEDS_HUMAN not in bead.labels
            and not bead.open_blockers())


class Parker:
    def __init__(self, ws: WorkstreamSettings, deps: Deps) -> None:
        self.ws = ws
        self.d = deps

    def _finish(self, op: Op, status: OpStatus, state: BeadState, reason: Reason | None = None,
                detail: str = "") -> BeadState:
        with self.d.journal.transaction():
            self.d.journal.op_finish(op.op_id, status)
            self.d.journal.set_state(self.ws.name, op.bead, state, reason, detail, op.data.get("ref") or None)
        return state

    def escalate(self, bead: str) -> None:
        """Best effort `needs-human` on the bead; the journal already says STUCK, which plan 6 shows."""
        try:
            self.d.beads.ensure_label(self.ws.name, bead, NEEDS_HUMAN)
        except (BeadsUnavailable, NotOurs):
            self.d.journal.emit(self.ws.name, bead, "escalation_unrecorded")

    def _stuck(self, op: Op, reason: Reason, detail: str) -> BeadState:
        self._finish(op, OpStatus.STUCK, BeadState.STUCK, reason, detail)
        self.escalate(op.bead)
        return BeadState.STUCK

    # --- park ---

    def park(self, bead: str, blockers: tuple[str, ...], why: str = "", hold: bool = False,
             ref: str | None = None) -> BeadState:
        """Park a bead wsd holds. `blockers` are the beads it waits on; `hold` parks it for the operator
        (/stop) until they release it. Returns the bead's state afterwards (PARKING if a step must be
        retried)."""
        if not blockers and not hold:
            raise ValueError("a park needs a blocker or an operator hold")
        with self.d.journal.transaction():
            op = self.d.journal.op_open(OpKind.PARK, self.ws.name, bead,
                                        {"blockers": ",".join(blockers), "why": why,
                                         "hold": "1" if hold else "", "ref": ref or ""})
            self.d.journal.set_state(self.ws.name, bead, BeadState.PARKING, ref=ref)
        self.d.cp("park.intent")
        return self.replay_park(op)

    def replay_park(self, op: Op) -> BeadState:
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        try:
            spot = place(self.ws, self.d.beads.show(ws, bead))
            if op.step == "intent":
                self.d.runtime.stop(spot.session_key)
                op = j.op_step(op.op_id, "stopped")
                self.d.cp("park.stopped")
            if op.step == "stopped":
                sha = gitwip.wip_commit(spot.worktree, op.op_id, f"parked {bead}")
                op = j.op_step(op.op_id, "committed", {"sha": sha})
                self.d.cp("park.committed")
            if op.step == "committed":
                for blocker in filter(None, op.data.get("blockers", "").split(",")):
                    self.d.beads.ensure_blocker(ws, bead, blocker)
                op = j.op_step(op.op_id, "blocked")
                self.d.cp("park.blocked")
            if op.step == "blocked":
                if op.data.get("hold"):
                    self.d.beads.ensure_label(ws, bead, HELD)
                self.d.beads.ensure_label(ws, bead, PARKED)
                op = j.op_step(op.op_id, "labelled")
                self.d.cp("park.labelled")
            if op.step == "labelled":
                self.d.beads.ensure_comment(ws, bead, f"{PARK_MARK}{op.op_id}", park_comment(op))
                op = j.op_step(op.op_id, "commented")
                self.d.cp("park.commented")
            final = parked_state(self.d.beads.show(ws, bead))
        except NotOurs:
            return self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
        except ConfigInvalid as exc:
            return self._stuck(op, Reason.CONFIG_INVALID, str(exc))
        except (gitwip.GitFailed, RuntimeUnavailable) as exc:
            if j.op_failed(op.op_id) >= self.ws.limits.park_attempts_before_human:
                return self._stuck(op, Reason.PARK_FAILED, type(exc).__name__)
            j.set_state(ws, bead, BeadState.PARKING, Reason.PARK_FAILED, type(exc).__name__)
            return BeadState.PARKING
        return self._finish(op, OpStatus.DONE, final, REASONS.get(final))

    # --- resume ---

    def resume(self, bead: Bead, ref: str | None = None) -> BeadState:
        """Resume a resumable parked bead as the same session in the same worktree."""
        with self.d.journal.transaction():
            op = self.d.journal.op_open(OpKind.RESUME, self.ws.name, bead.id, {"ref": ref or ""})
            self.d.journal.set_state(self.ws.name, bead.id, BeadState.RESUMING, ref=ref)
        self.d.cp("resume.intent")
        return self.replay_resume(op)

    def replay_resume(self, op: Op) -> BeadState:
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        try:
            shown = self.d.beads.show(ws, bead)
            spot = place(self.ws, shown)
            if op.step == "intent":
                if not resumable(shown):        # it stopped being resumable meanwhile
                    final = parked_state(shown) if PARKED in shown.labels else BeadState.STUCK
                    return self._finish(op, OpStatus.ABANDONED, final,
                                        REASONS.get(final, Reason.UNEXPECTED_STATE))
                self.d.beads.ensure_label(ws, bead, PARKED, present=False)
                op = j.op_step(op.op_id, "unlabelled")
                self.d.cp("resume.unlabelled")
            if op.step == "unlabelled":
                self.d.runtime.launch(LaunchSpec(ws, bead, self.ws.coder_role, spot.profile, spot.session_key,
                                                 spot.label, spot.worktree, resume=True,
                                                 ref=op.data.get("ref") or None))
                op = j.op_step(op.op_id, "launched")
                self.d.cp("resume.launched")
        except NotOurs:
            return self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
        except ConfigInvalid as exc:
            return self._stuck(op, Reason.CONFIG_INVALID, str(exc))
        except (LaunchFailed, RuntimeUnavailable) as exc:
            if j.op_failed(op.op_id) >= self.ws.limits.launch_failures_before_human:
                return self._stuck(op, Reason.LAUNCH_FAILED, type(exc).__name__)
            j.set_state(ws, bead, BeadState.RESUMING, Reason.LAUNCH_FAILED, type(exc).__name__)
            return BeadState.RESUMING
        return self._finish(op, OpStatus.DONE, BeadState.RUNNING)

    def replay(self, op: Op) -> BeadState:
        if op.kind is OpKind.PARK:
            return self.replay_park(op)
        if op.kind is OpKind.RESUME:
            return self.replay_resume(op)
        raise ValueError("not a park or resume operation")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_park.py -q`
Expected: `18 passed`.

- [ ] **Step 7: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 8: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/workstream.py src/heterodyne/wsd/park.py tests/wsd_env.py tests/test_wsd_park.py
git commit -m "feat(wsd): journaled park and resume; wsd never unclaims"
```

### Task 7: Pickup ("never idle while an unblocked bead exists")

**Files:**
- Create: `$HZ/src/heterodyne/wsd/scheduler.py`
- Modify: `$HZ/tests/wsd_env.py` (add the scheduler to the rig)
- Test: `$HZ/tests/test_wsd_pickup.py`, `$HZ/tests/test_wsd_pause.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6; in particular `Parker`, `resumable`, `place`, `Deps`, `ClaimGate`/`Paused`, `BeadsAdapter.ready/ours/read_claim`, `Journal.op_*`, `ws_state`.
- Produces (`heterodyne.wsd.scheduler`):
  - `POINTS = ("pickup.intent", "pickup.claimed", "pickup.worktree", "pickup.launched")`, `CODER_BUSY`, `SESSION_MAY_LIVE`, `RETRIED_HOLDS`.
  - `class TriggerKind(StrEnum)`: `TURN_ENDED, BEAD_CLOSED, BEAD_PARKED, APPROVAL_RESOLVED, BACKSTOP, OPERATOR, STARTUP`; frozen `Trigger(kind, ref=None)` (**the plan 6 seam**: `ref` is the message behind the trigger, carried into progress events and `LaunchSpec.ref`).
  - `class Outcome(StrEnum)`: `STARTED, RESUMED, BUSY, NOTHING, HELD`.
  - `class Scheduler(ws, deps, parker=None)` with `.lock` (one pickup, park or recovery at a time per workstream), `.parker`, `pickup(trigger) -> Outcome`, `refresh_active() -> bool`, `relaunch_if_dead(bead) -> BeadState`, `start_new(bead, ref) -> BeadState`, `replay(op) -> BeadState`.
  - `tests/wsd_env.py`: `Rig.sched`, `Rig.pickup(kind=TriggerKind.BACKSTOP, ref=None) -> Outcome`.

Pickup, under the workstream lock: hold if no runtime; replay open journals; hold while a claim is unread or actions are unreconciled; refresh the beads that hold the coder role (a session that may be live keeps it); resume resumable parked beads first; then, unless paused, try ready beads in order until one starts. The hypothesis property `test_never_idle_while_unblocked_work_exists` checks the "never idle" rule over random queues, races and faults.

- [ ] **Step 1: Add the scheduler to the rig in `$HZ/tests/wsd_env.py`**

The whole file after the change:

```python
"""A wsd test rig: a fake queue, a fake runtime, a real temp git repository and a real journal.

`restart()` models wsd dying and starting again: a new Journal on the same file and new wsd objects,
while the queue, the repository and the agent sessions (other processes) carry on.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from fakes.checkpoints import Recorder
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime

from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import ActionReconciler, HoldingReconciler, LaunchSpec
from heterodyne.wsd.scheduler import Outcome, Scheduler, Trigger, TriggerKind
from heterodyne.wsd.states import BeadState
from heterodyne.wsd.workstream import Deps, Limits, WorkstreamSettings, place

WS = "alpha"
PROFILES = frozenset({"p-one", "p-two"})


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@example.org",
                                                   "commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


@dataclass
class Rig:
    root: Path
    world: World
    runtime: FakeRuntime
    repo: Path
    ws: WorkstreamSettings
    cp: Checkpoint = field(default_factory=Recorder)
    reconciler: ActionReconciler | None = None
    journal: Journal = field(init=False)
    beads: BeadsAdapter = field(init=False)
    gate: ClaimGate = field(init=False)
    deps: Deps = field(init=False)
    parker: Parker = field(init=False)
    sched: Scheduler = field(init=False)

    def __post_init__(self) -> None:
        self.restart(self.cp)

    def restart(self, cp: Checkpoint | None = None) -> None:
        if hasattr(self, "journal"):
            self.journal.close()
        self.cp = cp if cp is not None else Recorder()
        self.journal = Journal(self.root / "state" / "wsd.db")
        self.beads = BeadsAdapter(factory(self.world))
        self.gate = ClaimGate(self.root / "state" / "claims", self.beads, self.cp)
        self.deps = Deps(self.journal, self.beads, self.gate, self.runtime,
                         self.reconciler or HoldingReconciler(self.beads), self.cp)
        self.parker = Parker(self.ws, self.deps)
        self.sched = Scheduler(self.ws, self.deps, self.parker)

    def start(self, bead: str) -> None:
        """What a pickup does for one bead, without the pickup journal: claim, worktree, launch."""
        self.gate.claim(WS, bead)
        spot = place(self.ws, self.beads.show(WS, bead))
        self.beads.worktree(WS, bead, spot.repo)
        self.runtime.launch(LaunchSpec(WS, bead, self.ws.coder_role, spot.profile, spot.session_key,
                                       spot.label, spot.worktree, resume=False))
        self.journal.set_state(WS, bead, BeadState.RUNNING)

    def replay_open(self) -> None:
        for op in self.journal.ops_open(WS):
            self.parker.replay(op)

    def pickup(self, kind: TriggerKind = TriggerKind.BACKSTOP, ref: str | None = None) -> Outcome:
        return self.sched.pickup(Trigger(kind, ref))

    def worktree(self, bead: str) -> Path:
        return self.repo.parent / f"{self.repo.name}-btq-{bead}"

    def state(self, bead: str) -> str | None:
        row = self.journal.state(WS, bead)
        return None if row is None else row.state.value


def make_rig(tmp_path: Path, cp: Checkpoint | None = None, limits: Limits | None = None) -> Rig:
    world = World(tmp_path / "btq-state")
    repo = git_repo(tmp_path / "repos" / "proj")
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES, limits or Limits())
    return Rig(tmp_path, world, FakeRuntime(), repo, ws, cp if cp is not None else Recorder())
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_pickup.py`**

```python
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, Recorder, SimulatedCrash
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from wsd_env import WS, Rig, make_rig

from heterodyne.wsd import ids
from heterodyne.wsd.beads import NEEDS_HUMAN, PARKED, BeadsUnavailable
from heterodyne.wsd.runtime import Liveness
from heterodyne.wsd.scheduler import POINTS, Outcome
from heterodyne.wsd.states import Reason, WsState
from heterodyne.wsd.workstream import Limits


def test_clean_pickup_passes_every_point_once(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1", title="Do   the\nthing")
    assert rig.pickup(ref="msg-1") is Outcome.STARTED
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p.startswith("pickup.")] == list(POINTS)
    [spec] = rig.runtime.launches
    assert spec.session_key == ids.role_session("btq-1", "coder", "p-one")
    assert spec.label == "btq-1 · coder · Do the thing"
    assert spec.worktree == rig.worktree("btq-1") and spec.ref == "msg-1" and not spec.resume
    assert rig.state("btq-1") == "running"
    assert rig.journal.snapshot(WS).state is WsState.RUNNING


@pytest.mark.parametrize("point", POINTS)
def test_crash_at_every_pickup_point_replays_to_one_start(tmp_path: Path, point: str) -> None:
    rig = make_rig(tmp_path, cp=CrashAt(point))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    rig.pickup()
    assert rig.world.claims == ["btq-1"]
    assert rig.world.worktrees == ["btq-1"]
    assert len(rig.runtime.launches) == 1
    assert rig.state("btq-1") == "running"
    assert rig.journal.ops_open() == []


def test_uncertain_claim_that_landed_is_used(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("claim", RuntimeError("timeout"), after=True)
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"] and len(rig.runtime.launches) == 1


def test_claim_that_did_not_land_holds_and_retries(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("claim", RuntimeError("timeout"))
    assert rig.pickup() is Outcome.HELD                 # never "idle" with btq-1 still ready
    assert rig.journal.snapshot(WS).state is WsState.HELD
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"] and rig.journal.holds(WS) == {}


def test_unreadable_claim_holds_the_workstream(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.fault("claim", RuntimeError("timeout"), after=True)
    rig.world.fault("show", RuntimeError("dolt down"))
    assert rig.pickup() is Outcome.HELD
    assert Reason.CLAIM_UNCERTAIN in rig.journal.holds(WS)
    assert rig.world.claims == ["btq-1"]           # nothing else is claimed while it is unknown
    assert rig.pickup() is Outcome.BUSY             # read back as ours on the next trigger, and started
    assert rig.state("btq-1") == "running"
    assert Reason.CLAIM_UNCERTAIN not in rig.journal.holds(WS)
    assert rig.world.claims == ["btq-1"]


def test_lost_race_tries_the_next_bead(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.stolen.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    assert rig.state("btq-1") == "dropped" and rig.state("btq-2") == "running"


def test_routing_change_is_never_executed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.fault("claim", RuntimeError("Routing/design changed during claim; ask Bel"), after=True)
    assert rig.pickup() is Outcome.STARTED
    assert rig.state("btq-1") == "stuck"
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rig.world.beads["btq-1"].status == "in_progress"       # never unclaimed
    assert [s.bead for s in rig.runtime.launches] == ["btq-2"]


def test_unknown_repository_is_stuck_not_guessed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1", metadata={"repo": "elsewhere"})
    assert rig.pickup() is Outcome.NOTHING
    assert rig.journal.state(WS, "btq-1") is not None
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.CONFIG_INVALID
    assert rig.runtime.launches == []


def test_launch_failures_escalate_after_limit(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, limits=Limits(launch_failures_before_human=2))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.runtime.launch_failures = 2
    assert rig.pickup() is Outcome.STARTED          # STARTING, retried on the next trigger
    assert rig.state("btq-1") == "starting"
    assert rig.pickup() is Outcome.STARTED          # second failure: stuck; then btq-2 starts
    assert rig.state("btq-1") == "stuck" and rig.state("btq-2") == "running"
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_no_runtime_holds_without_claiming(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.runtime.up = False
    assert rig.pickup() is Outcome.HELD
    assert rig.world.claims == []
    assert rig.journal.snapshot(WS).holds == {Reason.RUNTIME_UNAVAILABLE: ""}


def test_beads_down_holds_and_never_reads_as_idle(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.down = True
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.snapshot(WS).state is WsState.HELD
    rig.world.down = False
    assert rig.pickup() is Outcome.STARTED
    assert rig.journal.holds(WS) == {}


def test_unsettled_action_holds_pickup(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "uncertain"})
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.ACTIONS_UNRECONCILED: "btq-ap"}
    assert rig.world.claims == []


def test_one_coder_session_at_a_time(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    for n in range(3):
        rig.world.add(f"btq-{n}")
    assert rig.pickup() is Outcome.STARTED
    assert rig.pickup() is Outcome.BUSY
    assert len(rig.runtime.live()) == 1
    rig.world.close("btq-0")
    assert rig.pickup() is Outcome.STARTED
    assert rig.state("btq-0") == "closed" and len(rig.runtime.live()) == 1


def test_lost_claim_stops_the_session(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.beads["btq-1"].assignee = "bel:host:recovery"
    rig.pickup()
    assert rig.state("btq-1") == "stuck"
    assert rig.runtime.live() == []


def test_dead_session_is_relaunched_as_the_same_session(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    key = rig.runtime.launches[0].session_key
    rig.runtime.sessions[key] = Liveness.DEAD
    assert rig.pickup() is Outcome.BUSY
    assert [(s.session_key, s.resume) for s in rig.runtime.launches] == [(key, False), (key, True)]


def test_unknown_liveness_never_starts_a_second_session(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.pickup()
    rig.runtime.sessions[rig.runtime.launches[0].session_key] = Liveness.UNKNOWN
    assert rig.pickup() is Outcome.BUSY
    assert rig.world.claims == ["btq-1"]


# --- never idle while an unblocked bead exists ---

KINDS = ("ok", "lost_race", "bad_repo", "launch_fails", "uncertain_landed", "uncertain_missed",
         "blocked", "closed_blocker")


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.sampled_from(KINDS), min_size=0, max_size=6))
def test_never_idle_while_an_unblocked_bead_exists(tmp_path_factory: pytest.TempPathFactory,
                                                    kinds: list[str]) -> None:
    rig = make_rig(tmp_path_factory.mktemp("never-idle"))
    rig.world.add("btq-zz-blocker")
    rig.world.beads["btq-zz-blocker"].labels.remove("agent:wsd")      # someone else's bead
    rig.world.add("btq-zz-done", status="closed")
    for n, kind in enumerate(kinds):
        bead = f"btq-{n}"
        rig.world.add(bead)
        if kind == "bad_repo":
            rig.world.beads[bead].metadata["repo"] = "nope"
        elif kind == "blocked":
            rig.world.beads[bead].deps.append(("btq-zz-blocker", "blocks"))
        elif kind == "closed_blocker":
            rig.world.beads[bead].deps.append(("btq-zz-done", "blocks"))
        elif kind == "lost_race":
            rig.world.stolen.add(bead)
        elif kind == "launch_fails":
            rig.runtime.launch_failures += 1
        elif kind == "uncertain_landed":
            rig.world.fault("claim", RuntimeError("timeout"), after=True)
        elif kind == "uncertain_missed":
            rig.world.fault("claim", RuntimeError("timeout"))
    # Every bead that finishes is closed by its agent; pickup runs on each trigger.
    for _ in range(4 * len(kinds) + 4):
        outcome = rig.pickup()
        assert len(rig.runtime.live()) <= 1
        if outcome is Outcome.NOTHING:
            assert rig.world.ready_for(WS) == []           # idle only when nothing is ready
            assert rig.runtime.live() == []
            break
        assert outcome in (Outcome.STARTED, Outcome.BUSY, Outcome.RESUMED, Outcome.HELD)
        for key in rig.runtime.live():
            bead = next(s.bead for s in rig.runtime.launches if s.session_key == key)
            rig.world.close(bead)
    else:
        pytest.fail("pickup never settled")
    assert "btq-zz-blocker" not in rig.world.claims


# --- parked beads in pickup (§5.2: resumable before ready; a parked bead never pre-empts) ---


def parked_rig(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    assert rig.pickup() is Outcome.STARTED
    rig.parker.park("btq-1", ("btq-2",))
    return rig


def test_parked_bead_never_preempts_the_running_one(tmp_path: Path) -> None:
    rig = parked_rig(tmp_path)
    rig.world.add("btq-3")
    assert rig.pickup() is Outcome.STARTED
    rig.world.close("btq-2")                                        # btq-1 is resumable now
    assert rig.pickup() is Outcome.BUSY
    assert PARKED in rig.world.beads["btq-1"].labels
    rig.world.close("btq-3")
    assert rig.pickup() is Outcome.RESUMED
    spec = rig.runtime.launches[-1]
    assert (spec.bead, spec.resume, spec.worktree) == ("btq-1", True, rig.worktree("btq-1"))


def test_resumable_beats_ready(tmp_path: Path) -> None:
    rig = parked_rig(tmp_path)
    rig.world.add("btq-0")                                          # sorts first in ready order
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.RESUMED
    assert "btq-0" not in rig.world.claims


def test_held_bead_is_skipped_by_pickup(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    assert rig.pickup() is Outcome.NOTHING
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_dead_session_of_parked_bead_is_left_alone(tmp_path: Path) -> None:
    rig = parked_rig(tmp_path)
    rig.runtime.sessions[rig.runtime.launches[0].session_key] = Liveness.DEAD
    rig.pickup()
    assert len(rig.runtime.launches) == 1


def test_pickup_replays_an_interrupted_park(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.world.fault("dep", RuntimeError("dolt down"))
    with pytest.raises(BeadsUnavailable):
        rig.parker.park("btq-1", ("btq-2",))
    assert rig.pickup() is Outcome.NOTHING          # the park finished first; btq-1 waits on btq-2
    assert rig.state("btq-1") == "parked"


def test_unconfirmed_park_stop_keeps_the_coder_role(tmp_path: Path) -> None:
    """A park that could not confirm its session stopped, and the stuck bead it escalates to, keep the
    coder role until the runtime reports that session dead: never two coder sessions at once."""
    rig = make_rig(tmp_path, limits=Limits(park_attempts_before_human=2))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    key = rig.runtime.launches[0].session_key
    rig.runtime.stop_failures = 5
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.add("btq-3")
    assert rig.pickup() is Outcome.BUSY             # PARKING, stop unconfirmed
    assert rig.state("btq-1") == "stuck"            # the replay inside that pickup escalated it
    assert rig.pickup() is Outcome.BUSY             # still stuck, session still live
    assert rig.world.claims == ["btq-1"]
    rig.runtime.sessions[key] = Liveness.DEAD
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1", "btq-3"]


def test_stuck_bead_whose_repo_vanished_keeps_its_live_session_counted(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.beads["btq-1"].metadata["repo"] = "gone"
    rig.world.add("btq-2")
    assert rig.pickup() is Outcome.BUSY
    assert rig.state("btq-1") == "stuck"
    assert rig.world.claims == ["btq-1"]


def test_closed_bead_with_unconfirmed_stop_keeps_the_role(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.close("btq-1")
    rig.world.add("btq-2")
    rig.runtime.stop_failures = 1
    assert rig.pickup() is Outcome.BUSY
    assert rig.state("btq-1") == "running"
    assert rig.pickup() is Outcome.STARTED          # the retried stop is confirmed
    assert rig.state("btq-1") == "closed"
    assert rig.world.claims == ["btq-1", "btq-2"]
```

- [ ] **Step 3: Create `$HZ/tests/test_wsd_pause.py`**

These force the §4.3 interleavings with `PauseAt`: a direct `btq pause` after listing, and a `wsctl`-style pause racing an in-flight claim.

```python
"""Pause at the pickup level (ADR 0001 §4.3): it stops new claims only, and an acknowledged pause is
never followed by a claim. Interleavings are forced with checkpoints."""

import threading
from pathlib import Path

from fakes.checkpoints import PauseAt
from wsd_env import WS, make_rig

from heterodyne.wsd.scheduler import Outcome


def test_pause_stops_claims(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.gate.pause(WS)
    assert rig.pickup() is Outcome.NOTHING
    assert rig.world.claims == []
    rig.gate.resume(WS)
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"]


def test_direct_btq_pause_after_listing_is_honoured(tmp_path: Path) -> None:
    cp = PauseAt("pickup.intent")           # ready() has listed btq-1; the claim has not started
    rig = make_rig(tmp_path, cp=cp)
    rig.world.add("btq-1")
    result: list[Outcome] = []
    worker = threading.Thread(target=lambda: result.append(rig.pickup()))
    worker.start()
    assert cp.reached.wait(5)
    rig.beads.set_paused(WS, True)
    cp.go.set()
    worker.join(5)
    assert result == [Outcome.NOTHING]
    assert rig.world.claims == []
    assert rig.state("btq-1") == "dropped"


def test_pause_waits_for_in_flight_claim(tmp_path: Path) -> None:
    """The claim has passed the flag check when the operator pauses. The pause is not acknowledged until
    that claim is done, and after the acknowledgement no claim starts."""
    cp = PauseAt("gate.checked")
    rig = make_rig(tmp_path, cp=cp)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    outcome: list[Outcome] = []
    picker = threading.Thread(target=lambda: outcome.append(rig.pickup()))
    picker.start()
    assert cp.reached.wait(5)
    acked = threading.Event()
    pauser = threading.Thread(target=lambda: (rig.gate.pause(WS), acked.set()))
    pauser.start()
    assert not acked.wait(0.3)              # blocked on the claim lock
    cp.go.set()
    picker.join(5)
    pauser.join(5)
    assert acked.is_set()
    assert outcome == [Outcome.STARTED]
    assert rig.world.claims == ["btq-1"]
    rig.world.close("btq-1")
    assert rig.pickup() is Outcome.NOTHING
    assert rig.world.claims == ["btq-1"]


def test_pause_does_not_stop_the_running_bead(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    rig.gate.pause(WS)
    assert rig.pickup() is Outcome.BUSY
    assert rig.runtime.stops == []


def test_pause_does_not_stop_a_resume(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.parker.park("btq-1", ("btq-2",))
    rig.gate.pause(WS)
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.RESUMED          # pausing stops new claims only
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_pickup.py tests/test_wsd_pause.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.scheduler'`.

- [ ] **Step 5: Create `$HZ/src/heterodyne/wsd/scheduler.py`**

```python
"""Deterministic pickup (ADR 0001 §5.2) for one workstream, and the pickup journal (§4.3).

On every trigger (a turn ends, a bead closes or parks, an approval resolves, the 60s backstop), with the
workstream lock held:
1. replay any open pickup, park or resume journal;
2. refresh the beads wsd thinks are active, against beads and the runtime;
3. if the coder role is free: resumable parked beads first, then new ready work, trying each candidate
   in turn until one starts or none is left. That loop is the "never idle while an unblocked bead
   exists" rule: a refused, lost or failed candidate never ends pickup while another one remains.

New work is journaled: intent, claim (inside the claim gate), worktree, launch. A claim with an uncertain
outcome is read back before anything else; while it can't be read back, the workstream is held.
"""

import threading
from dataclasses import dataclass
from enum import StrEnum

from heterodyne.wsd import ids
from heterodyne.wsd.beads import (
    NEEDS_HUMAN,
    PARKED,
    Bead,
    BeadsUnavailable,
    ClaimRefused,
    ClaimUncertain,
    ClaimView,
    NotOurs,
    RoutingChanged,
    WorktreeConflict,
)
from heterodyne.wsd.gate import Paused
from heterodyne.wsd.journal import Op, OpKind, OpStatus
from heterodyne.wsd.park import Parker, resumable
from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec, Liveness, RuntimeUnavailable
from heterodyne.wsd.states import BeadState, Reason, ws_state
from heterodyne.wsd.workstream import ConfigInvalid, Deps, WorkstreamSettings, place

POINTS = ("pickup.intent", "pickup.claimed", "pickup.worktree", "pickup.launched")
CODER_BUSY = frozenset({BeadState.CLAIMING, BeadState.STARTING, BeadState.RUNNING, BeadState.RESUMING})
# A park that has not confirmed its stop, or a stuck bead, may still have a live coder session.
SESSION_MAY_LIVE = frozenset({BeadState.PARKING, BeadState.STUCK})
# Holds that pickup re-checks itself each time; any other hold needs recovery or the operator.
RETRIED_HOLDS = frozenset({Reason.BEADS_UNREACHABLE, Reason.RUNTIME_UNAVAILABLE, Reason.CLAIM_UNCERTAIN,
                           Reason.ACTIONS_UNRECONCILED})


class TriggerKind(StrEnum):
    TURN_ENDED = "turn_ended"
    BEAD_CLOSED = "bead_closed"
    BEAD_PARKED = "bead_parked"
    APPROVAL_RESOLVED = "approval_resolved"
    BACKSTOP = "backstop"
    OPERATOR = "operator"
    STARTUP = "startup"


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    ref: str | None = None      # the message or event behind the trigger, for progress reactions


class Outcome(StrEnum):
    STARTED = "started"          # new work claimed and launched
    RESUMED = "resumed"          # a parked bead resumed
    BUSY = "busy"                # the coder role already has a bead
    NOTHING = "nothing"          # nothing is ready and nothing is resumable
    HELD = "held"                # pickup is held (see the workstream's holds)


class Scheduler:
    def __init__(self, ws: WorkstreamSettings, deps: Deps, parker: Parker | None = None) -> None:
        self.ws = ws
        self.d = deps
        self.parker = parker or Parker(ws, deps)
        self.lock = threading.Lock()     # one pickup, park or reconcile at a time per workstream

    # --- entry point ---

    def pickup(self, trigger: Trigger) -> Outcome:
        with self.lock:
            try:
                outcome = self._pickup(trigger)
                self.d.journal.unhold(self.ws.name, Reason.BEADS_UNREACHABLE)
            except BeadsUnavailable as exc:
                self.d.journal.hold(self.ws.name, Reason.BEADS_UNREACHABLE, type(exc).__name__)
                outcome = Outcome.HELD
            self._publish()
            return outcome

    def _publish(self) -> None:
        j, name = self.d.journal, self.ws.name
        try:
            paused = self.d.beads.paused(name)
        except BeadsUnavailable:
            paused = True        # can't read the flag: show the workstream as stopped, never as running
        j.set_ws_state(name, ws_state(paused, j.holds(name), (b.state for b in j.states(name))))

    def _pickup(self, trigger: Trigger) -> Outcome:
        j, name = self.d.journal, self.ws.name
        if not self.d.runtime.available():
            j.hold(name, Reason.RUNTIME_UNAVAILABLE)
            return Outcome.HELD
        j.unhold(name, Reason.RUNTIME_UNAVAILABLE)
        for op in j.ops_open(name):
            self.replay(op)
        if Reason.CLAIM_UNCERTAIN in j.holds(name):
            return Outcome.HELD
        unresolved = self.d.reconciler.unresolved(name)
        if unresolved:
            j.hold(name, Reason.ACTIONS_UNRECONCILED, ",".join(sorted(unresolved)))
            return Outcome.HELD
        j.unhold(name, Reason.ACTIONS_UNRECONCILED)
        if set(j.holds(name)) - RETRIED_HOLDS:
            return Outcome.HELD
        if self.refresh_active():
            return Outcome.BUSY
        idle = [b for b in self.d.beads.ours(name) if resumable(b) and j.op_for(name, b.id) is None]
        for bead in sorted(idle, key=lambda b: b.id):
            if self.parker.resume(bead, trigger.ref) in CODER_BUSY:
                return Outcome.RESUMED
        if self.d.beads.paused(name):
            return Outcome.NOTHING       # pausing stops new claims only (§4.3)
        for bead in self.d.beads.ready(name):
            if j.op_for(name, bead.id) is not None:
                continue
            try:
                state = self.start_new(bead, trigger.ref)
            except Paused:
                return Outcome.NOTHING
            if Reason.CLAIM_UNCERTAIN in j.holds(name):
                return Outcome.HELD          # an unread claim: nothing else is claimed until it is known
            if state in CODER_BUSY:
                return Outcome.STARTED
        return Outcome.NOTHING

    # --- the coder role ---

    def refresh_active(self) -> bool:
        """Re-read every bead the journal has in a coder-busy state; True if the coder role is taken.
        A closed bead is CLOSED; a bead no longer ours is STUCK (claim lost) and its session stopped; a
        dead session is relaunched as the same session, up to the launch-failure limit. A session whose
        liveness is unknown keeps the role busy: wsd never starts a second session beside it. That includes
        the session of a bead being parked or stuck, until the runtime confirms it dead."""
        j, name = self.d.journal, self.ws.name
        busy = False
        for row in j.states(name):
            if row.state in SESSION_MAY_LIVE:
                busy = self._may_be_live(row.bead) or busy
                continue
            if row.state not in CODER_BUSY or j.op_for(name, row.bead) is not None:
                busy = busy or row.state in CODER_BUSY
                continue
            bead = self.d.beads.show(name, row.bead)
            if bead.status == "closed":
                if self._stop(bead):
                    j.set_state(name, row.bead, BeadState.CLOSED)
                else:
                    busy = True         # stays as it is and is retried: the session may still be working
                continue
            if self.d.beads.read_claim(name, row.bead) is not ClaimView.OURS:
                stopped = self._stop(bead)
                j.set_state(name, row.bead, BeadState.STUCK, Reason.CLAIM_LOST)
                busy = busy or not stopped
                continue
            state = self.relaunch_if_dead(bead)
            if state in CODER_BUSY or (state in SESSION_MAY_LIVE and self._may_be_live(bead.id)):
                busy = True
        return busy

    def _session_key(self, bead: Bead) -> str | None:
        """The bead's coder session key. It needs only the bead's ID and labels, not a valid repository: a
        bead stuck on a configuration change may still have the session launched before the change. None
        if no session can have a key for this bead, so none was ever launched for it."""
        try:
            profile = ids.profile_for(bead.labels, self.ws.coder_role, self.ws.coder_profile)
            return ids.role_session(bead.id, self.ws.coder_role, profile)
        except ids.BadName:
            return None

    def _may_be_live(self, bead: str) -> bool:
        key = self._session_key(self.d.beads.show(self.ws.name, bead))
        return key is not None and self.d.runtime.liveness(key) is not Liveness.DEAD

    def _stop(self, bead: Bead) -> bool:
        """Stop the session of a bead that is closed or no longer ours: work on it has no claim behind it.
        False if the stop could not be confirmed."""
        key = self._session_key(bead)
        if key is None:
            return True
        try:
            self.d.runtime.stop(key)
        except RuntimeUnavailable:
            self.d.journal.emit(self.ws.name, bead.id, "stop_unconfirmed")
            return False
        return True

    def relaunch_if_dead(self, bead: Bead) -> BeadState:
        """An in-progress bead of ours: leave a live (or unknown) session alone, relaunch a dead one through
        the resume journal. A `v2:parked` or `needs-human` bead is never relaunched here."""
        j, name = self.d.journal, self.ws.name
        try:
            spot = place(self.ws, bead)
        except ConfigInvalid as exc:
            j.set_state(name, bead.id, BeadState.STUCK, Reason.CONFIG_INVALID, str(exc))
            return BeadState.STUCK
        if PARKED in bead.labels or NEEDS_HUMAN in bead.labels:
            j.set_state(name, bead.id, BeadState.STUCK, Reason.UNEXPECTED_STATE)
            return BeadState.STUCK
        if self.d.runtime.liveness(spot.session_key) is not Liveness.DEAD:
            current = j.state(name, bead.id)
            if current is None or current.state is not BeadState.RUNNING:
                j.set_state(name, bead.id, BeadState.RUNNING)
            return BeadState.RUNNING
        with j.transaction():
            op = j.op_open(OpKind.RESUME, name, bead.id)
            j.op_step(op.op_id, "unlabelled")       # nothing to unlabel: go straight to the launch
            j.set_state(name, bead.id, BeadState.RESUMING, Reason.SESSION_DEAD)
        reopened = j.op_for(name, bead.id)
        if reopened is None:
            raise RuntimeError("resume journal vanished")
        return self.parker.replay_resume(reopened)

    # --- new work ---

    def start_new(self, bead: Bead, ref: str | None) -> BeadState:
        j, name = self.d.journal, self.ws.name
        with j.transaction():
            op = j.op_open(OpKind.PICKUP, name, bead.id, {"ref": ref or ""})
            j.set_state(name, bead.id, BeadState.CLAIMING, ref=ref)
        self.d.cp("pickup.intent")
        try:
            self.d.gate.claim(name, bead.id)
        except Paused:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
            raise
        except ClaimRefused:
            return self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
        except RoutingChanged:
            return self._routing_changed(op)
        except ClaimUncertain:
            state = self.replay(op)
            row = j.state(name, bead.id)
            if row is not None and (row.state, row.reason) == (BeadState.DROPPED, Reason.CLAIM_ABANDONED):
                # The claim failed without landing: the queue is failing, not the bead. Hold pickup and let
                # the next trigger try again, rather than calling the workstream idle with work ready.
                raise BeadsUnavailable("claim did not land") from None
            return state
        op = j.op_step(op.op_id, "claimed")
        self.d.cp("pickup.claimed")
        return self._start(op)

    def _routing_changed(self, op: Op) -> BeadState:
        """btq claimed it but routing or the design gate changed: the claim stays (wsd never unclaims),
        the bead is never executed, and a human decides."""
        self._finish(op, OpStatus.STUCK, BeadState.STUCK, Reason.ROUTING_CHANGED)
        self.parker.escalate(op.bead)
        return BeadState.STUCK

    def _finish(self, op: Op, status: OpStatus, state: BeadState, reason: Reason | None = None,
                detail: str = "") -> BeadState:
        with self.d.journal.transaction():
            self.d.journal.op_finish(op.op_id, status)
            self.d.journal.set_state(self.ws.name, op.bead, state, reason, detail, op.data.get("ref") or None)
        return state

    def replay(self, op: Op) -> BeadState:
        """Continue an open journal from the step it reached."""
        if op.kind is not OpKind.PICKUP:
            return self.parker.replay(op)
        j, name = self.d.journal, self.ws.name
        if op.step == "intent":
            try:
                view = self.d.beads.read_claim(name, op.bead)
            except BeadsUnavailable as exc:
                j.hold(name, Reason.CLAIM_UNCERTAIN, op.bead)
                j.set_state(name, op.bead, BeadState.CLAIMING, Reason.CLAIM_UNCERTAIN, type(exc).__name__)
                return BeadState.CLAIMING
            j.unhold(name, Reason.CLAIM_UNCERTAIN)
            if view is ClaimView.FREE:
                return self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
            if view is ClaimView.OTHER:
                return self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_LOST)
            op = j.op_step(op.op_id, "claimed")
            self.d.cp("pickup.claimed")
        return self._start(op)

    def _start(self, op: Op) -> BeadState:
        j, name = self.d.journal, self.ws.name
        try:
            bead = self.d.beads.show(name, op.bead)
            spot = place(self.ws, bead)
            if op.step == "claimed":
                j.set_state(name, op.bead, BeadState.STARTING)
                path = self.d.beads.worktree(name, op.bead, spot.repo)
                op = j.op_step(op.op_id, "worktree", {"worktree": str(path)})
                self.d.cp("pickup.worktree")
            if op.step == "worktree":
                self.d.runtime.launch(LaunchSpec(name, op.bead, self.ws.coder_role, spot.profile,
                                                 spot.session_key, spot.label, spot.worktree, resume=False,
                                                 ref=op.data.get("ref") or None))
                op = j.op_step(op.op_id, "launched")
                self.d.cp("pickup.launched")
        except NotOurs:
            return self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
        except ConfigInvalid as exc:
            return self._stuck(op, Reason.CONFIG_INVALID, str(exc))
        except WorktreeConflict as exc:
            return self._stuck(op, Reason.WORKTREE_FAILED, str(exc))
        except (LaunchFailed, RuntimeUnavailable) as exc:
            if j.op_failed(op.op_id) >= self.ws.limits.launch_failures_before_human:
                return self._stuck(op, Reason.LAUNCH_FAILED, type(exc).__name__)
            j.set_state(name, op.bead, BeadState.STARTING, Reason.LAUNCH_FAILED, type(exc).__name__)
            return BeadState.STARTING
        return self._finish(op, OpStatus.DONE, BeadState.RUNNING)

    def _stuck(self, op: Op, reason: Reason, detail: str) -> BeadState:
        self._finish(op, OpStatus.STUCK, BeadState.STUCK, reason, detail)
        self.parker.escalate(op.bead)
        return BeadState.STUCK
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_pickup.py tests/test_wsd_pause.py tests/test_wsd_park.py -q`
Expected: `51 passed` (33 new, plus Task 6's 18 still passing with the extended rig).

- [ ] **Step 7: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 8: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/scheduler.py tests/wsd_env.py tests/test_wsd_pickup.py tests/test_wsd_pause.py
git commit -m "feat(wsd): journaled pickup that never idles while unblocked work exists"
```

### Task 8: Startup recovery

**Files:**
- Create: `$HZ/src/heterodyne/wsd/recovery.py`
- Test: `$HZ/tests/test_wsd_recovery.py`

**Interfaces:**
- Consumes: `Scheduler` (`.lock`, `.d`, `.ws`, `.parker`, `.replay`, `.relaunch_if_dead`), `CODER_BUSY` (Task 7); `parked_state` (Task 6); `BeadsAdapter.ours/read_claim`, labels (Task 4); `Journal.adopt/ops_open/hold/unhold` (Task 3).
- Produces (`heterodyne.wsd.recovery`): `RECOVERY_POINTS = ("recovery.read", "recovery.actions", "recovery.journals", "recovery.sessions")`; frozen `Recovered(ws, ok, replayed=0, completed_parks=0, relaunched=0)`; `recover(sched: Scheduler) -> Recovered`.

The order is the ADR's (§3.3): journal integrity (done by `Journal()`), read beads, reconcile actions, resume park journals, reconcile sessions; the caller accepts events only afterwards (Task 9). The tests crash at every recovery point and recover again, and cover a lost journal (deleted between runs).

- [ ] **Step 1: Create `$HZ/tests/test_wsd_recovery.py`**

```python
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, Recorder, SimulatedCrash
from wsd_env import WS, Rig, make_rig

from heterodyne.wsd.beads import NEEDS_HUMAN, PARKED
from heterodyne.wsd.recovery import RECOVERY_POINTS, recover
from heterodyne.wsd.runtime import Liveness
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.states import Reason


def lose_journal(rig: Rig) -> None:
    rig.journal.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{rig.root / 'state' / 'wsd.db'}{suffix}").unlink(missing_ok=True)
    rig.restart()
    assert rig.journal.fresh


def test_recovery_runs_in_adr_order(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    assert recover(rig.sched).ok
    assert isinstance(rig.cp, Recorder)
    assert rig.cp.seen == list(RECOVERY_POINTS)


@pytest.mark.parametrize("point", RECOVERY_POINTS)
def test_crash_during_recovery_is_safe_to_repeat(tmp_path: Path, point: str) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.sched.parker.park("btq-1", ("btq-2",))
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        recover(rig.sched)
    rig.restart()
    assert recover(rig.sched).ok
    assert rig.state("btq-1") == "parked"
    assert len(rig.runtime.launches) == 1


def test_lost_journal_rebuilds_running_from_beads(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.pickup()
    lose_journal(rig)
    assert recover(rig.sched).ok
    assert rig.state("btq-1") == "running"
    assert rig.pickup() is Outcome.BUSY                   # the live session keeps the role
    assert rig.world.claims == ["btq-1"]


def test_lost_journal_relaunches_a_dead_session_once(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    key = rig.runtime.launches[0].session_key
    rig.runtime.sessions[key] = Liveness.DEAD
    lose_journal(rig)
    result = recover(rig.sched)
    assert result.relaunched == 1
    assert [s.resume for s in rig.runtime.launches] == [False, True]
    assert rig.state("btq-1") == "running"


def test_lost_journal_finishes_a_cut_short_park(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.restart(CrashAt("park.blocked"))                   # edge on, v2:parked not yet
    with pytest.raises(SimulatedCrash):
        rig.sched.parker.park("btq-1", ("btq-2",))
    lose_journal(rig)
    result = recover(rig.sched)
    assert result.completed_parks == 1
    assert PARKED in rig.world.beads["btq-1"].labels
    assert rig.state("btq-1") == "parked"
    assert len(rig.runtime.launches) == 1


def test_lost_journal_parked_bead_resumes_when_unblocked(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.sched.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    lose_journal(rig)
    assert recover(rig.sched).ok
    assert rig.pickup() is Outcome.RESUMED


def test_needs_human_bead_is_stuck_not_relaunched(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.beads["btq-1"].labels.append(NEEDS_HUMAN)
    rig.runtime.sessions[rig.runtime.launches[0].session_key] = Liveness.DEAD
    lose_journal(rig)
    assert recover(rig.sched).ok
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "stuck" and row.reason is Reason.NEEDS_HUMAN
    assert len(rig.runtime.launches) == 1


def test_beads_down_at_startup_holds_and_claims_nothing(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.down = True
    result = recover(rig.sched)
    assert not result.ok
    assert Reason.BEADS_UNREACHABLE in rig.journal.holds(WS)
    assert rig.world.claims == []


def test_unsettled_actions_hold_after_recovery(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "executing"})
    assert recover(rig.sched).ok
    assert rig.journal.holds(WS) == {Reason.ACTIONS_UNRECONCILED: "btq-ap"}


def test_journal_bead_lost_to_another_worker(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.beads["btq-1"].assignee = "bel:host:recovery"
    rig.restart()
    assert recover(rig.sched).ok
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.CLAIM_LOST


def test_two_dead_sessions_never_run_together(tmp_path: Path) -> None:
    """Only possible after a manual intervention; recovery relaunches one and leaves the other stuck."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.pickup()
    adapter_worker = rig.beads.bead_queue(WS, "btq-2").worker
    rig.world.beads["btq-2"].status, rig.world.beads["btq-2"].assignee = "in_progress", adapter_worker
    rig.runtime.sessions[rig.runtime.launches[0].session_key] = Liveness.DEAD
    lose_journal(rig)
    assert recover(rig.sched).ok
    assert len(rig.runtime.live()) == 1
    assert sorted(str(rig.state(b)) for b in ("btq-1", "btq-2")) == ["running", "stuck"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_recovery.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.recovery'`.

- [ ] **Step 3: Create `$HZ/src/heterodyne/wsd/recovery.py`**

```python
"""Startup recovery for one workstream, in the ADR's order (ADR 0001 §3.3, §4.3, §10):

1. journal integrity check: done when the Journal is opened (a corrupt journal never gets this far);
2. read beads: every bead this workstream's per-bead workers hold;
3. reconcile actions in `executing`/`uncertain` (plan 5 checks targets; until then they hold pickup);
4. resume park journals: replay every open pickup, park and resume journal; with a lost journal, finish
   a park that was cut short (blocking edge on, `v2:parked` not yet) when its session is dead;
5. reconcile sessions: rebuild every bead's state from beads, relaunch a dead in-progress session;
6. only then does the caller accept events.

Recovery is the one place the journal's bead states are replaced from beads without a transition check
(`Journal.adopt`), because beads are the source of truth. Anything it can't read holds the workstream:
nothing is ever inferred from missing evidence.
"""

from dataclasses import dataclass

from heterodyne.wsd.beads import NEEDS_HUMAN, PARKED, Bead, BeadsUnavailable, ClaimView
from heterodyne.wsd.park import parked_state
from heterodyne.wsd.runtime import Liveness
from heterodyne.wsd.scheduler import CODER_BUSY, Scheduler
from heterodyne.wsd.states import TERMINAL, BeadState, Reason
from heterodyne.wsd.workstream import ConfigInvalid, place

RECOVERY_POINTS = ("recovery.read", "recovery.actions", "recovery.journals", "recovery.sessions")


@dataclass(frozen=True)
class Recovered:
    ws: str
    ok: bool                 # False: the workstream stays held until a later recovery succeeds
    replayed: int = 0        # open journals replayed
    completed_parks: int = 0  # parks finished from bead state alone (lost journal)
    relaunched: int = 0


def recover(sched: Scheduler) -> Recovered:
    j, name = sched.d.journal, sched.ws.name
    with sched.lock:
        try:
            result = _recover(sched)
        except BeadsUnavailable as exc:
            j.hold(name, Reason.BEADS_UNREACHABLE, type(exc).__name__)
            return Recovered(name, ok=False)
        j.unhold(name, Reason.BEADS_UNREACHABLE)
        return result


def _recover(sched: Scheduler) -> Recovered:
    j, name, d = sched.d.journal, sched.ws.name, sched.d
    # 2. read beads
    ours = {b.id: b for b in d.beads.ours(name)}
    for row in j.states(name):
        if row.state in TERMINAL or row.bead in ours or j.op_for(name, row.bead) is not None:
            continue
        bead = d.beads.show(name, row.bead)
        if bead.status == "closed":
            j.adopt(name, row.bead, BeadState.CLOSED)
        elif d.beads.read_claim(name, row.bead) is not ClaimView.OURS:
            j.adopt(name, row.bead, BeadState.STUCK, Reason.CLAIM_LOST)
    d.cp("recovery.read")
    # 3. actions
    unresolved = d.reconciler.unresolved(name)
    if unresolved:
        j.hold(name, Reason.ACTIONS_UNRECONCILED, ",".join(sorted(unresolved)))
    else:
        j.unhold(name, Reason.ACTIONS_UNRECONCILED)
    d.cp("recovery.actions")
    # 4. park journals (and pickup and resume journals)
    replayed = 0
    for op in j.ops_open(name):
        sched.replay(op)
        replayed += 1
    completed = 0
    for bead in d.beads.ours(name):
        if (j.op_for(name, bead.id) is None and PARKED not in bead.labels and NEEDS_HUMAN not in bead.labels
                and bead.open_blockers() and _liveness(sched, bead) is Liveness.DEAD):
            blockers = tuple(dep.id for dep in bead.open_blockers())
            sched.parker.park(bead.id, blockers, why="park completed by recovery")
            completed += 1
    d.cp("recovery.journals")
    # 5. sessions
    relaunched = 0
    for bead in sorted(d.beads.ours(name), key=lambda b: b.id):
        if j.op_for(name, bead.id) is not None:
            continue
        if NEEDS_HUMAN in bead.labels:
            j.adopt(name, bead.id, BeadState.STUCK, Reason.NEEDS_HUMAN)
        elif PARKED in bead.labels:
            state = parked_state(bead)
            j.adopt(name, bead.id, state, {BeadState.HELD: Reason.HELD_BY_OPERATOR,
                                           BeadState.WAITING_INPUT: Reason.WAITING_ON_OPERATOR}.get(
                                               state, Reason.BLOCKED_ON_BEAD))
        else:
            relaunched += _session(sched, bead)
    d.cp("recovery.sessions")
    return Recovered(name, ok=True, replayed=replayed, completed_parks=completed, relaunched=relaunched)


def _liveness(sched: Scheduler, bead: Bead) -> Liveness:
    try:
        return sched.d.runtime.liveness(place(sched.ws, bead).session_key)
    except ConfigInvalid:
        return Liveness.UNKNOWN


def _session(sched: Scheduler, bead: Bead) -> int:
    """An in-progress, unparked bead of ours: a live (or unknown) session keeps running; a dead one is
    relaunched, unless another bead already holds the coder role (one session per role, §4.3)."""
    j, name = sched.d.journal, sched.ws.name
    if _liveness(sched, bead) is not Liveness.DEAD:
        j.adopt(name, bead.id, BeadState.RUNNING)
        return 0
    others = [r for r in j.states(name) if r.bead != bead.id and r.state in CODER_BUSY]
    if others or not sched.d.runtime.available():
        reason = Reason.UNEXPECTED_STATE if others else Reason.RUNTIME_UNAVAILABLE
        j.adopt(name, bead.id, BeadState.STUCK, reason, "in progress without a session")
        return 0
    j.adopt(name, bead.id, BeadState.RUNNING, Reason.SESSION_DEAD)
    return 1 if sched.relaunch_if_dead(bead) in CODER_BUSY else 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_recovery.py -q`
Expected: `14 passed`.

- [ ] **Step 5: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 6: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/recovery.py tests/test_wsd_recovery.py
git commit -m "feat(wsd): startup recovery in the ADR's order, failing closed"
```

### Task 9: Settings, control socket, daemon, CLIs and docs

**Files:**
- Create: `$HZ/src/heterodyne/wsd/settings.py`, `$HZ/src/heterodyne/wsd/ctl.py`, `$HZ/src/heterodyne/wsd/daemon.py`, `$HZ/src/heterodyne/wsd/cli.py`, `$HZ/docs/wsd.md`
- Modify: `$HZ/src/heterodyne/defaults/defaults.toml`, `$HZ/pyproject.toml`, `$HZ/docs/configuration.md`, `$HZ/docs/install.md`, `$HZ/examples/config.toml`
- Test: `$HZ/tests/test_wsd_settings.py`, `$HZ/tests/test_wsd_daemon.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8; from the existing config package `heterodyne.config.load`, `paths`, `secret_scan.is_reference`, `secret_scan.show`, `ConfigError`, `config.layers.table_at`.
- Produces:
  - `heterodyne.wsd.settings`: frozen `WsdSettings(state_dir, backstop_seconds, reconcile_seconds, inbox_attempts_before_human, btq_checkout, btq_locations: dict[str, str], workstreams: tuple[WorkstreamSettings, ...])` with properties `journal`, `instance_lock`, `lock_dir`, `socket`; `workstream_names(config_dir) -> list[str]`; `resolve(env) -> WsdSettings`.
  - `heterodyne.wsd.ctl`: `CtlRequest(op: "tick"|"status"|"pause"|"resume", job: "pickup"|"reconcile"|None = None, ws: str|None = None)`, `CtlReply(result: "ok"|"refused"|"failed", message, data: dict[str, dict[str, str]] = {})`, `CtlUnavailable`, `CtlServer(path, handler)` (`start()`, `close()`), `request(path, req, timeout=600) -> CtlReply`.
  - `heterodyne.wsd.daemon`: frozen `Parts(journal, beads, gate, schedulers: dict[str, Scheduler])`; `assemble(s, journal, factory, runtime, reconciler=HoldingReconciler, cp=nothing) -> Parts` (**plans 4 and 5 pass their runtime and reconciler here**); `class Wsd(s, parts)` with `recovered: set[str]`, `recover_one`, `pickup_one`, `reconcile_one`, `startup`, `set_pause(name, paused) -> CtlReply`, `status(only)`, `handle(req)` (async), `serve(stop)` (async).
  - `heterodyne.wsd.cli`: `EX_CONFIG = 78`, `run(s, factory, runtime) -> int`, `wsd_main(argv=None) -> int`, `wsctl_main(argv=None) -> int`; console scripts `wsd` and `wsctl`.

`wsd run` order: instance lock → open (and so integrity-check) the journal → recover every workstream → a startup pickup each → only then the control socket and the timers. `wsctl pause` goes through wsd, which sets the flag under the claim lock (§4.3).

- [ ] **Step 1: Create `$HZ/tests/test_wsd_settings.py`**

```python
from pathlib import Path

import pytest

from heterodyne.config import ConfigError
from heterodyne.wsd.settings import resolve


def write(d: Path, name: str, text: str) -> None:
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    write(tmp_path, "config.toml", f"""
[profiles.a]
adapter = "codex"
model = "m1"
[profiles.b]
adapter = "claude-code"
model = "m2"
[integrations.beads]
btq = "{tmp_path}/btq"
dolt_port = 3307
credentials = {{ file = "{tmp_path}/btq-credentials.json" }}
""")
    write(tmp_path, "workstreams/alpha.toml", f"""
[roles]
coder = "b"
[repos]
default = "{tmp_path}/repos/proj"
docs = "~/docs"
""")
    return tmp_path


def env(d: Path) -> dict[str, str]:
    return {"HETERODYNE_CONFIG_DIR": str(d), "HOME": str(d)}


def test_resolve_defaults_and_workstreams(cfg: Path) -> None:
    s = resolve(env(cfg))
    assert (s.backstop_seconds, s.reconcile_seconds, s.inbox_attempts_before_human) == (60.0, 300.0, 3)
    assert s.state_dir == cfg / ".local" / "state" / "heterodyne" / "wsd"
    assert s.btq_locations == {"dolt_port": "3307", "credentials": str(cfg / "btq-credentials.json")}
    [ws] = s.workstreams
    assert (ws.name, ws.coder_role, ws.coder_profile) == ("alpha", "coder", "b")
    assert ws.repos == {"default": cfg / "repos" / "proj", "docs": cfg / "docs"}
    assert (ws.limits.launch_failures_before_human, ws.limits.park_attempts_before_human) == (2, 3)


def test_wsd_table_is_host_only(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[wsd]\nbackstop_seconds = 1\n')
    with pytest.raises(ConfigError, match="not allowed in a workstream"):
        resolve(env(cfg))


def test_inline_credentials_are_refused(cfg: Path) -> None:
    text = (cfg / "config.toml").read_text().replace(
        f'credentials = {{ file = "{cfg}/btq-credentials.json" }}', 'credentials = "inline"')
    write(cfg, "config.toml", text)
    with pytest.raises(ConfigError):
        resolve(env(cfg))


def test_workstream_needs_default_repo(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[roles]\ncoder = "b"\n[repos]\nother = "/x"\n')
    with pytest.raises(ConfigError, match="default"):
        resolve(env(cfg))


def test_relative_repo_is_refused(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[roles]\ncoder = "b"\n[repos]\ndefault = "proj"\n')
    with pytest.raises(ConfigError, match="absolute"):
        resolve(env(cfg))


def test_workstream_file_name_must_be_a_slug(cfg: Path) -> None:
    write(cfg, "workstreams/Bad Name.toml", "")
    with pytest.raises(ConfigError, match="slug"):
        resolve(env(cfg))


def test_coder_profile_must_exist(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[roles]\nreview = "a"\n[repos]\ndefault = "/x"\n')
    with pytest.raises(ConfigError, match="roles.coder"):
        resolve(env(cfg))


def test_unknown_wsd_key_is_refused(cfg: Path) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text() + "[wsd]\nbackstop = 5\n")
    with pytest.raises(ConfigError, match="unknown keys"):
        resolve(env(cfg))


def test_unknown_beads_integration_key_is_refused(cfg: Path) -> None:
    text = (cfg / "config.toml").read_text().replace("dolt_port = 3307", 'dolt_port = 3307\nrelay = "x"')
    write(cfg, "config.toml", text)
    with pytest.raises(ConfigError, match=r"\[integrations.beads\]: unknown keys"):
        resolve(env(cfg))


def test_btq_checkout_is_required(cfg: Path) -> None:
    text = (cfg / "config.toml").read_text().replace(f'btq = "{cfg}/btq"\n', "")
    write(cfg, "config.toml", text)
    with pytest.raises(ConfigError, match="btq must be a path"):
        resolve(env(cfg))


def test_integrations_marmot_is_left_alone(cfg: Path) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text()
          + '[integrations.marmot]\nsocket = "/run/x.sock"\n')
    assert resolve(env(cfg)).btq_checkout == cfg / "btq"
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_daemon.py`**

`test_startup_recovers_before_accepting_events` asserts the control socket does not exist while recovery runs.

```python
import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime
from wsd_env import PROFILES, WS, git_repo

from heterodyne.wsd import cli, ctl
from heterodyne.wsd.daemon import Wsd, assemble
from heterodyne.wsd.gate import instance_lock
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.workstream import WorkstreamSettings


def settings(tmp_path: Path) -> WsdSettings:
    repo = git_repo(tmp_path / "repos" / "proj")
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES)
    return WsdSettings(tmp_path / "state" / "wsd", 0.05, 3600, 3, tmp_path / "btq", {}, (ws,))


def test_startup_recovers_before_accepting_events(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world, runtime = World(tmp_path / "btq-state"), FakeRuntime()
    world.add("btq-1")
    journal = Journal(s.journal)
    daemon = Wsd(s, assemble(s, journal, factory(world), runtime))
    seen_socket_during_recovery: list[bool] = []
    original = daemon.recover_one

    def spy(name: str):  # noqa: ANN202
        seen_socket_during_recovery.append(s.socket.exists())
        return original(name)

    daemon.recover_one = spy  # type: ignore[method-assign]

    async def scenario() -> ctl.CtlReply:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        for _ in range(100):
            if s.socket.exists():
                break
            await asyncio.sleep(0.02)
        reply = await ctl.request(s.socket, ctl.CtlRequest("status"))
        stop.set()
        await task
        return reply

    reply = asyncio.run(scenario())
    assert seen_socket_during_recovery == [False]
    assert reply.data[WS]["state"] == "running" and reply.data[WS]["beads"] == "btq-1=running"
    assert not s.socket.exists()
    assert world.claims == ["btq-1"]


def test_tick_and_backstop(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world, runtime = World(tmp_path / "btq-state"), FakeRuntime()
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), runtime))

    async def scenario() -> tuple[ctl.CtlReply, ctl.CtlReply]:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        while not s.socket.exists():
            await asyncio.sleep(0.02)
        idle = await ctl.request(s.socket, ctl.CtlRequest("tick", job="pickup"))
        world.add("btq-1")
        for _ in range(100):                      # the 0.05 s backstop picks it up
            if world.claims:
                break
            await asyncio.sleep(0.02)
        bad = await ctl.request(s.socket, ctl.CtlRequest("tick", job="reconcile", ws="nope"))
        stop.set()
        await task
        return idle, bad

    idle, bad = asyncio.run(scenario())
    assert idle.data == {WS: {"outcome": "nothing"}}
    assert world.claims == ["btq-1"]
    assert bad.result == "refused"


def test_failed_recovery_is_retried_before_pickup(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    world.add("btq-1")
    world.down = True
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    assert daemon.startup() == {WS: daemon.pickup_one(WS, _trigger())}
    assert world.claims == [] and WS not in daemon.recovered
    world.down = False
    daemon.pickup_one(WS, _trigger())
    assert WS in daemon.recovered and world.claims == ["btq-1"]


def _trigger():  # noqa: ANN202
    from heterodyne.wsd.scheduler import Trigger, TriggerKind
    return Trigger(TriggerKind.BACKSTOP)


def test_corrupt_journal_refuses_to_start(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = settings(tmp_path)
    s.state_dir.mkdir(parents=True, mode=0o700)
    s.journal.write_bytes(b"garbage" * 200)
    world = World(tmp_path / "btq-state")
    assert cli.run(s, factory(world), FakeRuntime()) == cli.EX_CONFIG
    assert s.journal.read_bytes() == b"garbage" * 200
    assert "left in place" in capsys.readouterr().err


def test_second_instance_refuses(tmp_path: Path) -> None:
    s = settings(tmp_path)
    fd = instance_lock(s.instance_lock)
    try:
        assert cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime()) == 1
    finally:
        os.close(fd)


def test_wsctl_pause_goes_through_wsd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    s = replace(settings(tmp_path), backstop_seconds=3600)    # only explicit ticks pick up
    world = World(tmp_path / "btq-state")
    monkeypatch.setattr(cli, "_settings", lambda: s)
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))

    async def scenario() -> list[int]:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        while not s.socket.exists():
            await asyncio.sleep(0.02)
        codes = [await asyncio.to_thread(cli.wsctl_main, ["pause", WS])]
        world.add("btq-1")
        codes.append(await asyncio.to_thread(cli.wsd_main, ["tick", "pickup"]))
        codes.append(len(world.claims))
        codes.append(await asyncio.to_thread(cli.wsctl_main, ["resume", WS]))  # resume runs a pickup
        stop.set()
        await task
        return codes

    assert asyncio.run(scenario()) == [0, 0, 0, 0]
    assert world.claims == ["btq-1"]
    out = capsys.readouterr().out
    assert f"{WS}: paused." in out and f"{WS}: resumed." in out and "outcome=started" in out


def test_wsctl_pause_needs_a_running_wsd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    s = settings(tmp_path)
    monkeypatch.setattr(cli, "_settings", lambda: s)
    assert cli.wsctl_main(["pause", WS]) == 1
    assert "not running" in capsys.readouterr().err
    assert not (tmp_path / "btq-state").exists()        # nothing was set behind wsd's back


def test_pause_flag_failure_is_not_acknowledged(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    world.down = True
    reply = asyncio.run(daemon.handle(ctl.CtlRequest("pause", ws=WS)))
    assert reply.result == "failed" and "nothing is acknowledged" in reply.message


def test_pause_without_a_workstream_is_refused(tmp_path: Path) -> None:
    s = settings(tmp_path)
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "w")), FakeRuntime()))

    async def scenario() -> ctl.CtlReply:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        while not s.socket.exists():
            await asyncio.sleep(0.02)
        reply = await ctl.request(s.socket, ctl.CtlRequest("pause"))
        stop.set()
        await task
        return reply

    assert asyncio.run(scenario()).result == "refused"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_settings.py tests/test_wsd_daemon.py -q`
Expected: FAIL: `ModuleNotFoundError` for `heterodyne.wsd.settings` (and `heterodyne.wsd.daemon`).

- [ ] **Step 4: Add the `[wsd]` defaults to `$HZ/src/heterodyne/defaults/defaults.toml`**

Append at the end of the file:

```toml

# The workstream daemon (§4.3, §9). Host only; [integrations.beads] says where btq and the queue are.
[wsd]
backstop_seconds = 60
reconcile_seconds = 300
launch_failures_before_human = 2
park_attempts_before_human = 3
inbox_attempts_before_human = 3
coder_role = "coder"
```

The workstream layer already rejects any top-level table outside `roles`, `repos`, `sandbox`, `cron`, `render`, `timeouts` and `restrict`, so `[wsd]` and `[integrations]` are host-only without further code.

- [ ] **Step 5: Create `$HZ/src/heterodyne/wsd/settings.py`**

```python
"""wsd settings from the merged host config and each workstream's layer (ADR 0001 §4.3, §9, §15).

`[wsd]` and `[integrations.beads]` live in host `config.toml` only (the workstream layer rejects both).
`integrations.beads.btq` is the btq checkout; its other keys are btq's locations, passed to its `Queue`
unchanged, and anything not set falls back to btq's own `BTQ_*` environment and defaults. A workstream is
a file `workstreams/<name>.toml` whose stem is a slug; its `[repos]` names its repositories (absolute
paths, one of them `default`) and `roles.coder` its coder profile.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from heterodyne.config import Config, ConfigError, load, paths, secret_scan
from heterodyne.config.layers import table_at
from heterodyne.config.secret_scan import show
from heterodyne.wsd import ids
from heterodyne.wsd.workstream import DEFAULT_REPO, Limits, WorkstreamSettings

WSD_KEYS = frozenset({"backstop_seconds", "reconcile_seconds", "launch_failures_before_human",
                      "park_attempts_before_human", "inbox_attempts_before_human", "coder_role"})
BTQ_KEYS = frozenset({"btq", "config_dir", "repo", "dolt_host", "dolt_port", "dolt_database",
                      "tls_cert", "credentials"})
BTQ_PATHS = ("config_dir", "repo", "tls_cert")
BTQ_TEXT = ("dolt_host", "dolt_port", "dolt_database")
BEADS = "[integrations.beads]"
JOURNAL = "wsd.db"
INSTANCE_LOCK = "wsd.lock"
CTL_SOCKET = "ctl.sock"


@dataclass(frozen=True)
class WsdSettings:
    state_dir: Path
    backstop_seconds: float
    reconcile_seconds: float
    inbox_attempts_before_human: int
    btq_checkout: Path
    btq_locations: dict[str, str]
    workstreams: tuple[WorkstreamSettings, ...]

    @property
    def journal(self) -> Path:
        return self.state_dir / JOURNAL

    @property
    def instance_lock(self) -> Path:
        return self.state_dir / INSTANCE_LOCK

    @property
    def lock_dir(self) -> Path:
        return self.state_dir / "claims"

    @property
    def socket(self) -> Path:
        return self.state_dir / CTL_SOCKET


def workstream_names(config_dir: Path) -> list[str]:
    folder = config_dir / "workstreams"
    if not folder.is_dir():
        return []
    names = sorted(p.stem for p in folder.glob("*.toml"))
    for name in names:
        if not ids.SLUG.fullmatch(name):
            raise ConfigError(f"workstreams/{show(name, False)}.toml: the file name must be a slug")
    return names


def resolve(env: Mapping[str, str]) -> WsdSettings:
    host = load(env=env)
    wsd = table_at(host.values, "wsd", "config")
    _only(wsd, WSD_KEYS, "[wsd]")
    btq = table_at(table_at(host.values, "integrations", "config"), "beads", "integrations")
    _only(btq, BTQ_KEYS, BEADS)
    limits = Limits(launch_failures_before_human=_int(wsd, "launch_failures_before_human"),
                    park_attempts_before_human=_int(wsd, "park_attempts_before_human"))
    coder_role = wsd.get("coder_role")
    if not isinstance(coder_role, str) or not ids.SLUG.fullmatch(coder_role):
        raise ConfigError("[wsd] coder_role must be a role name")
    streams = tuple(_workstream(name, load(name, env), coder_role, limits, env)
                    for name in workstream_names(paths.config_dir(env)))
    return WsdSettings(
        state_dir=paths.state_dir(env) / "wsd",
        backstop_seconds=_seconds(wsd, "backstop_seconds"),
        reconcile_seconds=_seconds(wsd, "reconcile_seconds"),
        inbox_attempts_before_human=_int(wsd, "inbox_attempts_before_human"),
        btq_checkout=_path(btq.get("btq"), env, f"{BEADS} btq"),
        btq_locations=_locations(btq, env),
        workstreams=streams)


def _workstream(name: str, cfg: Config, coder_role: str, limits: Limits,
                env: Mapping[str, str]) -> WorkstreamSettings:
    where = f"workstreams/{name}.toml"
    repos: dict[str, Path] = {}
    for repo, value in table_at(cfg.values, "repos", where).items():
        if not ids.SLUG.fullmatch(repo):
            raise ConfigError(f"{where}: repos.{show(repo, False)} must be a slug")
        repos[repo] = _path(value, env, f"{where}: repos.{repo}")
    if DEFAULT_REPO not in repos:
        raise ConfigError(f"{where}: [repos] must name a `{DEFAULT_REPO}` repository")
    profiles = frozenset(table_at(cfg.values, "profiles", "config.toml"))
    coder_profile = table_at(cfg.values, "roles", where).get(coder_role)
    if not isinstance(coder_profile, str) or coder_profile not in profiles:
        raise ConfigError(f"{where}: roles.{coder_role} must name a profile from [profiles]")
    return WorkstreamSettings(name, repos, coder_role, coder_profile, profiles, limits)


def _locations(btq: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in BTQ_PATHS:
        if key in btq:
            found[key] = str(_path(btq[key], env, f"{BEADS} {key}"))
    for key in BTQ_TEXT:
        if key in btq:
            value = btq[key]
            if isinstance(value, int) and not isinstance(value, bool):
                value = str(value)
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{BEADS} {key} must be a non-empty string")
            found[key] = value
    if "credentials" in btq:
        ref: Any = btq["credentials"]
        if not secret_scan.is_reference(ref) or set(cast(Mapping[str, Any], ref)) != {"file"}:
            raise ConfigError(f'{BEADS} credentials must be {{ file = "<path>" }}')
        where = f"{BEADS} credentials.file"
        found["credentials"] = str(_path(cast(Mapping[str, Any], ref)["file"], env, where))
    return found


def _only(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown keys {show(unknown)} (allowed: {sorted(allowed)})")


def _path(value: Any, env: Mapping[str, str], where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a path")
    path = paths.expand(value, env)
    if not path.is_absolute():
        raise ConfigError(f"{where} must be an absolute path or start with ~/")
    return path


def _int(table: Mapping[str, Any], key: str) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise ConfigError(f"[wsd] {key} must be an integer from 1 to 100")
    return value


def _seconds(table: Mapping[str, Any], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 86400:
        raise ConfigError(f"[wsd] {key} must be a number of seconds, more than 0 and at most 86400")
    return float(value)
```

- [ ] **Step 6: Create `$HZ/src/heterodyne/wsd/ctl.py`**

```python
"""wsd's host control socket (ADR 0001 §4.3, §9): `wsd tick <job>` from the timers, and `wsctl pause`,
`resume` and `status`.

One JSON request per connection, one JSON reply. The socket is 0600 inside the 0700 wsd state directory;
anything able to use it can already act as wsd.
"""

import asyncio
import contextlib
import os
import socket
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.fsutil import private_dir

MAX_REQUEST = 4096
READ_SECONDS = 5.0


def _no_data() -> dict[str, dict[str, str]]:
    return {}


class CtlRequest(msgspec.Struct, frozen=True):
    op: Literal["tick", "status", "pause", "resume"]
    job: Literal["pickup", "reconcile"] | None = None      # tick only
    ws: str | None = None       # None: every workstream (tick and status); pause and resume need one


class CtlReply(msgspec.Struct, frozen=True):
    result: Literal["ok", "refused", "failed"]
    message: str
    data: dict[str, dict[str, str]] = msgspec.field(default_factory=_no_data)


class CtlUnavailable(Exception):
    """No wsd is listening (or it did not answer)."""


Handler = Callable[[CtlRequest], Awaitable[CtlReply]]


class CtlServer:
    def __init__(self, path: Path, handler: Handler) -> None:
        self.path = path
        self.handler = handler
        self.server: asyncio.Server | None = None
        self.created: tuple[int, int] | None = None

    async def start(self) -> None:
        """Bind the socket ourselves, so a symlink at the path is refused rather than followed. Only a
        stale real socket is replaced. The caller holds the instance lock, so no live wsd owns it."""
        private_dir(self.path.parent)
        with contextlib.suppress(FileNotFoundError):
            if not stat.S_ISSOCK(os.lstat(self.path).st_mode):
                raise FileExistsError("the control socket path exists and is not a socket")
            self.path.unlink()
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(self.path))
            self.path.chmod(0o600)
            made = os.lstat(self.path)
            self.created = (made.st_dev, made.st_ino)
            sock.listen()
            sock.setblocking(False)
            self.server = await asyncio.start_unix_server(self._handle, sock=sock, limit=MAX_REQUEST + 2)
        except BaseException:
            sock.close()
            raise

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 2)
        with contextlib.suppress(OSError):
            current = os.lstat(self.path)
            if (current.st_dev, current.st_ino) == self.created:
                self.path.unlink()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), READ_SECONDS)
                if len(line.rstrip(b"\n")) > MAX_REQUEST:
                    raise ValueError("request too long")
                req = msgspec.json.decode(line, type=CtlRequest)
            except (TimeoutError, ValueError, msgspec.DecodeError):
                reply = CtlReply("refused", "malformed request")
            else:
                if (req.op == "tick") != (req.job is not None):
                    reply = CtlReply("refused", "tick needs a job; nothing else takes one")
                elif req.op in ("pause", "resume") and req.ws is None:
                    reply = CtlReply("refused", f"{req.op} needs a workstream")
                else:
                    try:
                        reply = await self.handler(req)
                    except Exception as exc:  # noqa: BLE001 - fixed wording; the type is enough
                        reply = CtlReply("failed", f"wsd hit an internal error ({type(exc).__name__})")
            writer.write(msgspec.json.encode(reply) + b"\n")
            await writer.drain()
        except Exception:  # noqa: BLE001, S110 - one bad client must not end the server
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def request(path: Path, req: CtlRequest, timeout: float = 600.0) -> CtlReply:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 5)
    except (OSError, TimeoutError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    try:
        writer.write(msgspec.json.encode(req) + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        return msgspec.json.decode(line, type=CtlReply)
    except (OSError, TimeoutError, ValueError, msgspec.DecodeError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
```

- [ ] **Step 7: Create `$HZ/src/heterodyne/wsd/daemon.py`**

```python
"""The wsd daemon (ADR 0001 §3.3, §5.2, §9): startup recovery, then pickup on triggers and timers.

Startup order is fixed: the instance lock; the journal, integrity-checked (corrupt: refuse to start);
recovery of every workstream (§3.3 steps 2 to 5); a startup pickup; and only then the control socket,
which is how events (timer ticks, and from plan 6 Marmot) reach wsd. A workstream whose recovery failed
stays held, and every later tick retries its recovery before any pickup: pickup never runs on state
recovery could not confirm.

Pickup and recovery are blocking (btq runs bd as a subprocess), so they run in worker threads; each
workstream's `Scheduler.lock` keeps them one at a time per workstream.
"""

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from heterodyne.wsd.beads import BeadsAdapter, BeadsUnavailable
from heterodyne.wsd.btq import QueueFactory
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.ctl import CtlReply, CtlRequest, CtlServer
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.recovery import Recovered, recover
from heterodyne.wsd.runtime import ActionReconciler, AgentRuntime, HoldingReconciler
from heterodyne.wsd.scheduler import Outcome, Scheduler, Trigger, TriggerKind
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.workstream import Deps


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
        result = recover(self.parts.schedulers[name])
        if result.ok:
            self.recovered.add(name)
        else:
            self.recovered.discard(name)
        return result

    def pickup_one(self, name: str, trigger: Trigger) -> Outcome:
        """Pickup, after a recovery if this workstream's last one did not succeed."""
        if name not in self.recovered and not self.recover_one(name).ok:
            return Outcome.HELD
        return self.parts.schedulers[name].pickup(trigger)

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
            return CtlReply("ok", f"{name}: paused. No new claims start; a running bead finishes and parked "
                                  "beads may resume.")
        outcome = self.pickup_one(name, Trigger(TriggerKind.OPERATOR))
        return CtlReply("ok", f"{name}: resumed.", {name: {"outcome": outcome.value}})

    def status(self, only: str | None) -> dict[str, dict[str, str]]:
        found: dict[str, dict[str, str]] = {}
        for name in self.parts.schedulers:
            if only is not None and name != only:
                continue
            snap = self.parts.journal.snapshot(name)
            found[name] = {
                "state": snap.state.value if snap.state else "unknown",
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
        if req.ws is not None and req.ws not in self.parts.schedulers:
            return CtlReply("refused", "no such workstream")
        if req.op == "status":
            return CtlReply("ok", "status", await asyncio.to_thread(self.status, req.ws))
        if req.op in ("pause", "resume") and req.ws is not None:
            return await asyncio.to_thread(self.set_pause, req.ws, req.op == "pause")
        results: dict[str, dict[str, str]] = {}
        for name in self._names(req.ws):
            if req.job == "reconcile":
                outcome = await asyncio.to_thread(self.reconcile_one, name)
            else:
                outcome = await asyncio.to_thread(self.pickup_one, name, Trigger(TriggerKind.OPERATOR))
            results[name] = {"outcome": outcome.value}
        return CtlReply("ok", f"{req.job} done", results)

    def _guarded(self, job: Callable[[str], object], name: str) -> None:
        """A timer job that fails unexpectedly is recorded, and the workstream is recovered again before
        its next pickup: the timer keeps running, and nothing is assumed about what the failure left."""
        try:
            job(name)
        except Exception as exc:  # noqa: BLE001 - recorded by type; the next tick re-recovers
            self.recovered.discard(name)
            self.parts.journal.emit(name, None, "tick_failed", type(exc).__name__)

    async def _every(self, seconds: float, job: Callable[[str], object]) -> None:
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
        try:
            await stop.wait()
        finally:
            for task in timers:
                task.cancel()
            for task in timers:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await server.close()
```

- [ ] **Step 8: Create `$HZ/src/heterodyne/wsd/cli.py`**

```python
"""`wsd` (run, tick) and `wsctl` (pause, resume, status) command lines (ADR 0001 §4.3, §9, §16).

Every command except `wsd run` asks the running wsd over its control socket. `wsctl pause` goes through
wsd as §4.3 requires: wsd sets btq's shared pause flag under the workstream's claim lock, so a pause is
acknowledged only once no claim can start. With wsd stopped nothing claims, and nothing is acknowledged.
"""

import argparse
import asyncio
import os
import signal
import sys
from collections.abc import Sequence

from heterodyne.config import ConfigError
from heterodyne.wsd import btq, ctl
from heterodyne.wsd.daemon import Wsd, assemble
from heterodyne.wsd.gate import AlreadyRunning, instance_lock
from heterodyne.wsd.journal import Journal, JournalCorrupt
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings, resolve

EX_CONFIG = 78  # sysexits: configuration error; the unit does not restart on it


def _settings() -> WsdSettings:
    return resolve(os.environ)


def _factory(s: WsdSettings) -> btq.QueueFactory:
    return btq.factory(btq.load(s.btq_checkout), s.btq_locations)


def run(s: WsdSettings, factory: btq.QueueFactory, runtime: AgentRuntime) -> int:
    try:
        lock = instance_lock(s.instance_lock)
    except AlreadyRunning:
        print("wsd: another wsd is already running on this state directory", file=sys.stderr)
        return 1
    try:
        try:
            journal = Journal(s.journal)
        except JournalCorrupt as exc:
            print(f"wsd: the journal failed its check ({exc}); it was left in place for inspection. "
                  "Move it aside to start from beads alone.", file=sys.stderr)
            return EX_CONFIG
        if journal.fresh:
            journal.emit("-", None, "journal_created")
        daemon = Wsd(s, assemble(s, journal, factory, runtime))
        asyncio.run(_serve(daemon))
        return 0
    finally:
        os.close(lock)


async def _serve(daemon: Wsd) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await daemon.serve(stop)


def _ask(s: WsdSettings, req: ctl.CtlRequest) -> ctl.CtlReply | None:
    try:
        return asyncio.run(ctl.request(s.socket, req))
    except ctl.CtlUnavailable:
        print("wsd is not running (no answer on its control socket)", file=sys.stderr)
        return None


def _print(reply: ctl.CtlReply) -> int:
    print(reply.message)
    for name, fields in sorted(reply.data.items()):
        print(name + ": " + " ".join(f"{k}={v}" for k, v in fields.items() if v))
    return 0 if reply.result == "ok" else 1


def wsd_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wsd")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the workstream daemon")
    tick = sub.add_parser("tick", help="ask the running wsd to run a job now (timers call this)")
    tick.add_argument("job", choices=["pickup", "reconcile"])
    tick.add_argument("--ws", default=None)
    args = parser.parse_args(argv)
    try:
        s = _settings()
        if args.cmd == "run":
            return run(s, _factory(s), NoRuntime())
    except ConfigError as exc:
        print(f"wsd: {exc}", file=sys.stderr)
        return EX_CONFIG
    except btq.BtqUnavailable as exc:
        print(f"wsd: {exc}", file=sys.stderr)
        return EX_CONFIG
    reply = _ask(s, ctl.CtlRequest("tick", job=args.job, ws=args.ws))
    return 1 if reply is None else _print(reply)


def wsctl_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wsctl")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("pause", "resume"):
        sub.add_parser(name, help=f"{name} new claims for a workstream").add_argument("ws")
    sub.add_parser("status", help="show what wsd is doing").add_argument("ws", nargs="?")
    args = parser.parse_args(argv)
    try:
        s = _settings()
        if args.cmd == "status":
            reply = _ask(s, ctl.CtlRequest("status", ws=args.ws))
            return 1 if reply is None else _print(reply)
        reply = _ask(s, ctl.CtlRequest(args.cmd, ws=args.ws))
        return 1 if reply is None else _print(reply)
    except ConfigError as exc:
        print(f"wsctl: {exc}", file=sys.stderr)
        return EX_CONFIG
```

- [ ] **Step 9: Add the console scripts to `$HZ/pyproject.toml`**

In `[project.scripts]`, after `admind = "heterodyne.admind.cli:main"`:

```toml
wsd = "heterodyne.wsd.cli:wsd_main"
wsctl = "heterodyne.wsd.cli:wsctl_main"
```

Then `cd $HZ && uv sync`.

- [ ] **Step 10: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_settings.py tests/test_wsd_daemon.py -q`
Expected: `20 passed`.

- [ ] **Step 11: Create `$HZ/docs/wsd.md`**

The operator page, including the seams for plans 4–6 (section 5).

```markdown
# wsd: the workstream daemon (queue and state)

`wsd` picks up beads for each configured workstream, one coder bead at a time, and keeps a journal so that a crash never loses or repeats a claim, a launch, a park or a resume (ADR 0001 §3.3, §4.3, §5.2, §9). This page covers plan 3: the queue, the journal, pickup, parking and recovery. Agent launching (plan 4), approvals (plan 5) and Marmot (plan 6) plug into the seams listed at the end.

**Until plan 4 wires in a runtime, `wsd run` holds every workstream (`runtime_unavailable`) and claims nothing.**

## 1. Configuration

All of it is host config (`config.toml`); a workstream file can't set `[wsd]` or `[integrations]`.

| Key | Meaning |
|---|---|
| `wsd.backstop_seconds` | How often pickup runs without a trigger. Default 60. |
| `wsd.reconcile_seconds` | How often state is rebuilt from beads and the runtime, then pickup runs. Default 300. |
| `wsd.launch_failures_before_human` | Failed launches of one bead before it is `stuck` with `needs-human`. 1 to 100, default 2. |
| `wsd.park_attempts_before_human` | Failed park attempts (session not confirmed stopped, git failure) before `stuck`. Default 3. Beads being unreachable never counts. |
| `wsd.inbox_attempts_before_human` | Failed attempts to apply one inbound event before it needs a human. Default 3. |
| `wsd.coder_role` | The role (from a workstream's `[roles]`) that runs beads. Default `coder`. |
| `integrations.beads.btq` | Required. The btq checkout; wsd loads its `bin/btq` and uses its `Queue` as agent `wsd`. |
| `integrations.beads.config_dir`, `repo`, `dolt_host`, `dolt_port`, `dolt_database`, `tls_cert` | Optional btq locations, passed to `Queue` unchanged. Unset ones fall back to btq's own `BTQ_*` environment and defaults. |
| `integrations.beads.credentials` | Optional. Must be `{ file = "<path>" }`: btq's credentials file. Inline secrets are refused. |

A workstream is `workstreams/<name>.toml`, where `<name>` is a slug (lowercase letters, digits, `.`, `_`, `-`). wsd needs:

- `[repos]`: repository name to absolute path (or `~/...`). One must be called `default`. A bead's `metadata.repo` picks another one; an unknown name makes that bead `stuck` with `config_invalid`.
- `[roles]`: `coder` (or your `wsd.coder_role`) must name a profile from `[profiles]`.

The journal, locks and control socket live in `<state>/wsd/` (0700): `wsd.db`, `wsd.lock`, `claims/<ws>.claim` and `ctl.sock`.

## 2. Commands

| Command | Does |
|---|---|
| `wsd run` | Runs the daemon. Exit 78 means a configuration problem or a journal that failed its check; the unit should not restart on it. Exit 1 means another wsd holds the state directory. |
| `wsd tick pickup [--ws WS]` | Asks the running wsd to run pickup now. Timers may call it. |
| `wsd tick reconcile [--ws WS]` | Asks for a reconcile (recovery, then pickup). |
| `wsctl pause WS` | Asks wsd to stop new claims for `WS`. Returns only once no claim can start. Needs a running wsd. |
| `wsctl resume WS` | Asks wsd to allow claims again; wsd then runs a pickup. |
| `wsctl status [WS]` | Each workstream's state, holds, beads with their state and reason, open journal steps and the last event number. |

Pause is btq's own pause flag for the workstream's session worker, which wsd sets under the workstream's claim lock. A direct `btq pause` on that worker sets the same flag without the lock: wsd honours it from its next claim check, so at most one claim already under way can still complete. With wsd stopped nothing claims, so `wsctl` refuses rather than acknowledge anything. Pause stops **new claims only**. A running bead carries on, and a parked bead whose blockers closed still resumes. wsd never unclaims a bead.

## 3. States

Each bead wsd holds has one state and, when it is waiting or stuck, a reason:

- `claiming`, `starting`, `running`, `resuming`: being started, working, or coming back from a park.
- `parking`, then `parked` (`blocked_on_bead`), `waiting_input` (`waiting_on_operator`: it waits on an approval, question or confirm bead) or `held` (`held_by_operator`: `/stop`).
- `stuck`: needs a human. The reason says why (`launch_failed`, `park_failed`, `config_invalid`, `claim_lost`, `routing_changed`, `worktree_failed`, `unexpected_state`) and the bead gets the `needs-human` label.
- `closed`, `dropped` (no longer ours).

A workstream is `running`, `idle`, `all_blocked`, `paused`, `held` or `stuck`. `held` lists its holds: `beads_unreachable`, `runtime_unavailable`, `claim_uncertain`, `actions_unreconciled` (a plan 5 action in `executing` or `uncertain`). A hold is retried on every pickup and cleared once its cause is gone. Holds never escalate a bead: an outage is not the bead's fault.

Every state change is also a progress event in the journal, carrying the message or event that caused it when there is one.

## 4. Recovery

On start, before the control socket opens, wsd runs for each workstream:

1. the journal integrity check (a failed check stops wsd with exit 78 and leaves the file in place; move it aside to start from beads alone);
2. read every bead the workstream's per-bead workers hold;
3. hold the workstream while any action is `executing` or `uncertain`;
4. replay every open pickup, park and resume journal from its last step;
5. rebuild every bead's state from beads, and relaunch a dead running session;
6. then a startup pickup, and only then events.

A workstream whose recovery failed stays `held` and is recovered again before its next pickup. Nothing is inferred from missing evidence: an unreadable claim, liveness or pause flag holds rather than proceeds.

## 5. Seams for later plans

- **Plan 4, `AgentRuntime`** (`heterodyne.wsd.runtime`): `available()`, `launch(LaunchSpec)`, `liveness(session_key) -> live|dead|unknown` and `stop(session_key)`. `stop` returns only once the session is confirmed stopped; `unknown` is never treated as dead. Pass it to `heterodyne.wsd.cli.run`.
- **Plan 5, `ActionReconciler`**: `unresolved(ws) -> [approval bead IDs]`. The default `HoldingReconciler` reports every `executing` or `uncertain` action, so the workstream stays held until plan 5 settles them. Approval beads must carry the `ws:<ws>` label and `metadata.action_state`.
- **Plans 5 and 6, parking**: `Parker.park(bead, blockers, why, hold, ref)` parks a running bead on blocking beads, or for the operator with `hold=True`. It raises `BeadsUnavailable` when beads can't be reached; the caller keeps the request and retries.
- **Plan 6, events**: `Journal.inbox_add` (deduplicated by surface and event ID), `inbox_pending`, `inbox_failed`, `inbox_finish`; `Journal.events_since(seq)` for progress; `Journal.snapshot(ws)` for status. Triggers enter as `Scheduler.pickup(Trigger(kind, ref))`.
```

- [ ] **Step 12: Update the configuration and install docs and the example config**

In `$HZ/docs/configuration.md`, replace the `[integrations]` bullet under "Host config" with:

```markdown
- **`[integrations]`:** external tools (btq, the `wn-agent` socket and its token) are configured by location here. `wsd` reads `[integrations.beads]` (see [wsd.md](wsd.md#1-configuration)); nothing reads `[integrations.marmot]` yet.
- **`[wsd]`:** the workstream daemon's timers and limits; see [wsd.md](wsd.md#1-configuration).
```

and, under "Workstream config", after the `[roles]` bullet, add:

```markdown
- **`[repos]`** names the workstream's repositories for `wsd`: absolute or `~/` paths, one of them `default` (see [wsd.md](wsd.md#1-configuration)).
```

In `$HZ/docs/install.md`, replace the btq row of the requirements table with:

```markdown
| btq, the Beads task-queue client | beads integration | by `wsd`, from `config.toml` `[integrations.beads]` (`btq` is the checkout) |
```

In `$HZ/examples/config.toml`, replace the `[integrations.beads]` table with:

```toml
[integrations.beads]
btq = "<path-to-btq-checkout>"           # wsd loads <checkout>/bin/btq
# credentials = { file = "<path-to-btq-credentials.json>" }   # optional; inline secrets are refused
```

- [ ] **Step 13: Run the whole gate**

Run: `cd $HZ && uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`
Expected: ruff and pyright clean; every test passes (the BTQ contract test is skipped without `BTQ_REPO`; the full suite takes about ten minutes); the install-agnostic check prints nothing and exits 0.

- [ ] **Step 14: Run the btq contract test against the real btq**

Run: `cd $HZ && BTQ_REPO=$BTQ_REPO uv run pytest tests/test_wsd_beads.py -q`
Expected: `27 passed`. This loads `$BTQ_REPO/bin/btq` but runs a fake `bd`: no queue is touched.

- [ ] **Step 15: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/settings.py src/heterodyne/wsd/ctl.py src/heterodyne/wsd/daemon.py src/heterodyne/wsd/cli.py src/heterodyne/defaults/defaults.toml pyproject.toml uv.lock docs/wsd.md docs/configuration.md docs/install.md examples/config.toml tests/test_wsd_settings.py tests/test_wsd_daemon.py
git commit -m "feat(wsd): daemon, control socket, wsd/wsctl CLIs and operator docs"
```

## Self-review notes (plan author)

**Spec coverage** (roadmap row 3 and the task brief):

| Requirement | Where |
|---|---|
| SQLite journal and inbox (§3.3) | Task 3 (`Journal`; inbox dedup by surface and event ID; `inbox_failed` escalates to `needs_human` after `inbox_attempts_before_human`) |
| Beads adapter with the `wsd` identity and per-bead workers | Task 1 (`ws_session`, `bead_session`), Task 4 (`BeadsAdapter`, through btq's `Queue` as agent `wsd`) |
| Shared pause gate and claim lock | Task 5 (`ClaimGate`), Task 7 (`test_wsd_pause.py` interleavings), Task 9 (`wsctl pause` through wsd) |
| Pickup, never idle while an unblocked bead exists | Task 7 (`Scheduler.pickup`, hypothesis property) |
| Park/resume journal | Task 6 (`Parker`), Task 7 (resume ordering) |
| Startup recovery order | Task 8 (`recover`), Task 9 (`Wsd.serve`: socket only after recovery) |
| Waiting-on-input, held, stuck with concrete reasons; per-message progress events | Task 2 (`Reason`), Task 3 (`set_state` emits `state:<value>` with `ref`), Task 6 (`waiting_input`, `held`), Task 7 (`Trigger.ref`) |
| Seams for plans 4, 5, 6 (interfaces and fakes only) | Task 5 (`AgentRuntime`, `ActionReconciler`, fakes), Task 6 (`Parker.park`), Task 3 (inbox, events), Task 9 (`assemble`, docs §5) |
| Crash-window and interleaving tests for every journaled transition | `PARK_POINTS`, `RESUME_POINTS` (Task 6), `POINTS` (Task 7), `RECOVERY_POINTS` (Task 8), `gate.checked` (Tasks 5, 7) |
| Fail closed | Global Constraints list; tests: unreadable flag, uncertain claim, unknown liveness, malformed JSON, corrupt journal, unconfirmed stop |

**Placeholder scan:** no step says "TBD", "similar to" or "add error handling"; every code step carries the full file. Edits to existing files show the exact text.

**Type consistency:** checked by building the tree task by task from these code blocks and running each task's tests, ruff and pyright at each step (Task 1: 24 passed; 2: 13; 3: 17; 4: 30 + 1 skipped; 5: 9; 6: 18; 7: 33; 8: 14; 9: 20), then the full gate on the result. Every Python block compiles (`py_compile`), and test function names are unique across `tests/`.

**Known limits, deliberately left to later plans:** no real `AgentRuntime` (plan 4), no action reconciliation against targets (plan 5), no inbox consumer and no Marmot rendering of progress events (plan 6), and no systemd units or timers for `wsd tick` (plan 8).
