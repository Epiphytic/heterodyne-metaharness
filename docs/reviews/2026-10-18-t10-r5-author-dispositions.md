# T10 fifth MEDIUM review: author dispositions

Actual reviewer outcome: **REVISE**, ten findings. This author repair does not change that outcome or reset any of the five actual review ordinals. No additional review, human approval, deployment, or live operation is claimed.

Reviewed source: `9cb99dff27eb2292ee081512a959a751974856ab`; integration base: `117b28f2bb78114221279cb5dc5ff497588297aa`. Raw review SHA-256: `2f3e0546f5253ec51d7280448392e746fb308024933483afcc1e0c2e846e48d3`. The controller retains all four earlier reviews and the approved Plan4c input.

## Finding 1

```text
1. `src/heterodyne/wsd/daemon.py:306`: The new `self.parts.harness.recover()` call is only in the synchronous `Wsd.startup()`, which nothing in production calls. The real path, `serve()` -> `_startup()` (line ~705), runs `recover_one`/`pickup_one` on the lanes and never runs harness recovery.

   Failure scenario: wsd crashes after `harness_open` but before `harness_close` during `wsctl harness use`. On restart, `serve()` never calls `harness_close_all()`, so `(adapter, install)` stays in `harness_opened()` forever. `in_use()` then returns True permanently, and `remove`/`prune` refuse IN_USE until someone edits the journal. Rows whose ask worker died stay `staged` until expiry instead of failing at once. The stated invariant 'recover before any pickup' therefore does not hold in production. `test_recover_runs_before_the_first_pickup` passes only because it calls `wsd.startup()`, not `serve()`.
```

Fixed. Both synchronous startup and production serve/_startup run managed-install recovery before any workstream recovery/pickup. A recovery exception stops startup; individually ambiguous approved publications remain quarantined OPEN while unrelated lanes can continue. New real serve-path and async-startup tests exercise this ordering.

## Finding 2

```text
2. `src/heterodyne/wsd/daemon.py:509`: Harness requests run through `asyncio.to_thread(self.harness_one, ...)`, bypassing `_run`. So they skip the `stopping` refusal, the lane drain and Undrained accounting. `install` then starts a daemon=True `_ask` thread that the interpreter kills on exit.

   Failure scenario: An operator runs `wsctl harness install codex` (fetch can take up to FETCH_SECONDS=600) and wsd is then stopped. `_drain()` does not see the install thread, and `asyncio.run` blocks on the executor join (up to about 300 s). The thread inserts the `staged` row and starts `_ask`. That thread is killed partway through, after `bd create` and possibly after the post. The result is an orphan approval bead or live ask that a human can approve but that can never take effect. With finding 1, nothing at restart fails the row either.
```

Fixed. Every harness control request enters the daemon-owned HARNESS_LANE via _run, including stopping admission and normal bounded drain/Undrained accounting. Approval workers are non-daemon and the admitting lane waits for their completion. Tests hold an actual approval worker and request in flight, verify drain failure while running, and release it for clean completion. No unbounded shutdown-success claim: a genuinely stuck worker remains Undrained.

## Finding 3

```text
3. `src/heterodyne/wsd/scheduler.py:554`: Every workstream's pickup now calls `harness.advance()` while holding its parker lock. `advance` holds `HarnessOps.lock` for the whole loop and, for each posted row, runs approve-bead (up to 5 s), `bd show` (20 s timeout) and `ask_digest` ref resolution. Approval I/O is therefore repeated W times per tick and serializes every workstream behind it.

   Failure scenario: Take 4 workstreams and 3 posted installs. Each backstop tick runs 12 approve-bead and 12 `bd show` subprocesses. While one workstream's pickup is in `advance`, the other pickups block on `HarnessOps.lock`, and so do `wsctl harness list/use/verify`. A slow `bd` (20 s each) can delay every workstream's launches by about a minute. Simpler fix: drive `advance` once from its own timer or lane, not from inside each scheduler's operation lock.
```

Fixed. Scheduler.pickup no longer calls advance at all. A single dedicated harness timer/serialized lane advances approvals once per daemon tick, independently of workstream parker locks. Startup performs one advance on that lane. Existing pickup regression now asserts no approval network call. Slow approval I/O can still serialize harness commands on their own lane, not all workstream pickups.

## Finding 4

```text
4. `src/heterodyne/wsd/harness.py:719`: `tainted()` writes a new alert and journals a `tainted` event without checking that the install's taint marker still exists. After `verify` or `remove` has moved the alert sequence on, a stale or redelivered InstallTainted report creates a fresh alert, and the scheduler also parks the bead.

   Failure scenario: A runtime report of a taint is queued. The operator restores the tree and runs `wsctl harness verify`, which clears the marker and bumps `harness_taints` to N+1. The queued report is then handled: `alerts.write` finds no `...-N+1.json` and writes 'install ... is tainted...'. admind relays a false alarm, and a RUNNING bead on the now-verified install is held under INSTALL_TAINTED. A guard on `verify.tainted(self.taint_dir, adapter, install)` would prevent this.
```

Fixed for cleared-before-processing reports. Scheduler checks the actual marker before parking; HarnessOps.tainted rechecks it under its lock before creating an alert. Marker-read errors hold and log only exception type. A regression verifies that a queued report after verify clears the marker cannot recreate the alert. The pre-existing marker-check/park concurrency boundary is not claimed to be an atomic cross-component transaction.

## Finding 5

```text
5. `src/heterodyne/sandbox/build.py:137`: `_checkout()` takes `Path(heterodyne.__file__).resolve().parents[2]`, and `_commit` runs `git -C <that> rev-parse HEAD`. In any non-editable install this is not the harness checkout, so the approval's commit ref can name an unrelated enclosing repository. Even in a checkout, HEAD ignores uncommitted changes.

   Failure scenario: wsd is installed into a venv's site-packages under a directory that sits inside some other git repo, such as a home directory under git. `parents[2]` is `.../lib/python3.12`, git finds the enclosing repo, and the approval card's `commit` ref names that repo's HEAD. The human approves an install bound to a commit that is not the running harness code. Likewise, a dirty editable checkout is reported as a clean HEAD.
```

Fixed for the reported provenance cases. _commit requires the exact imported editable package path inside the exact repository top level, a tracked package initializer, and clean package source including untracked files. It strips Git redirection environment and bounds Git calls. Non-editable/ancestor-repository and dirty source refuse managed-install approval provenance. Tests prove clean exact checkout acceptance, dirty rejection, unrelated ancestor rejection, and ambient GIT_DIR rejection. This source-check is not a cryptographic attestation of already-loaded Python bytecode against later concurrent checkout mutation; deployment must retain an immutable source checkout.

## Finding 6

```text
6. `src/heterodyne/wsd/harness.py:235`: The `version` from wsctl or the recipe is placed unvalidated into the GitHub release URL path (`layout.tag.format(version=version)`). Nothing checks that the staged `manifest.version` equals the requested version.

   Failure scenario: Something like `wsctl harness install codex '0.1/../../../../other/repo/releases/download/v1'` (or a recipe value containing `/`, `?` or `#`) builds a URL whose path the server resolves to a different repository's release, with the SHA256SUMS fetched from that same release. The staged package reports its own version. Only a careful human reading the card's tag line would notice; no code-level `[0-9A-Za-z.+-]` check refuses it.
```

Fixed. A closed bounded ASCII version component grammar rejects slash/query/fragment/control/empty/oversize values before fetch, for both overrides and recipes. The staged manifest version must equal the requested version before any approval row/worker. A package reporting another version is refused even when fetched through the requested fake release URL. Refused staged package cleanup still follows the existing store staging TTL.

## Finding 7

```text
7. `src/heterodyne/wsd/daemon.py:472`: `wsctl harness list` returns `listing.installs`, which includes each install's upstream `version` and an install name containing it. This covers store-staged installs whose approval was withheld as ASK_UNPOSTABLE. The prune replies are likewise keyed by version-bearing install names. The c3 fix treats this text as unsafe to log.

   Failure scenario: An upstream package version contains `vghp_` plus 36 alphanumeric characters. The approval text is withheld, but the staged tree stays in the store, and `wsctl harness list` prints the full version to stdout. On this host, agents run wsctl and capture its output into transcripts and logs, so the embedded secret leaks by a route the c3fix labels never cover.
```

Fixed at CLI output boundaries. List/prune dictionary keys and verify/use/remove messages use adapter-plus-manifest labels; list withholds upstream versions. A published vghp_ fixture proves list and prune JSON do not echo that version. Internal store identities are preserved unchanged. Human-readable labels intentionally do not replace canonical install names accepted by existing mutation commands; no new selector grammar or secret-bearing identifier egress is introduced.

## Finding 8

```text
8. `src/heterodyne/wsd/harness.py:222`: `store.stage()` commits a staged tree before `harness_insert`/`harness_event` run under `self.lock`. The two journal writes are not in one transaction, and a failure after staging (JournalBusy, sqlite error) leaves an orphan staging with no row.

   Failure scenario: During `install`, the journal is busy past BUSY_TIMEOUT when `harness_insert` runs. The reply is `failed`, but the staged tree is already in the store and has no journal row. `list` shows it as a bare store entry. No approval is ever asked for it, and nothing in wsd cleans it up except the store's own expiry, if any. If `harness_insert` succeeds but `harness_event` fails, the row exists with no event.
```

Partially fixed with explicit bounded residual. A journal transaction preflight refuses an already-busy/unusable journal before staging. Journal install row plus event, approved row plus event, and terminal published/failed row plus event now commit atomically in SQLite. Injected real SQLite failure proves rollback of row and event and no approval creation. Store staging is a separate durability domain: a later SQLite failure or manifest-version refusal can leave an unapproved staged tree, retained for the existing store expiry sweep. No replay, implicit publication, invented cleanup authority, or cross-store atomicity is claimed. This residual is disclosed for the next PoC review.

## Finding 9

```text
9. `src/heterodyne/wsd/harness.py:468`: In `_settle`, if `store.publish()` succeeds but returns a name other than `_name(row)`, the code falls through to `_end(row, 'failed', RECOVERY_FAILED)`. The install is now published in the store, but its row says failed.

   Failure scenario: The helper publishes the staging under a differently formatted name. The row is marked failed, so it is no longer OPEN, and the published install is not protected by `in_use`. The next `prune` lists it as removable, and `use` can select it, even though wsd recorded the approval flow as failed.
```

Fixed. An unexpected publish name/outcome keeps the original row approved/OPEN rather than terminal failed. All installs of that adapter remain conservatively in use while publication is unresolved; use/rollback refuse, and prune/remove protection remains. Exact subsequent store outcome readback settles the original row without a new approval. A real-store fixture tests wrong-name readback, selection refusal, prune protection, and subsequent exact recovery.

## Finding 10

```text
10. `src/heterodyne/wsd/daemon.py:496`: `harness_one` catches only HarnessRefused, StoreRefused, OSError and JournalBusy. Other exceptions from these paths fall to CtlServer's generic handler, which neither logs them nor matches the HARNESS_FAILED wording ('wsd's log says which').

    Failure scenario: During `use`, `selection.write` raises ConfigError, the journal raises sqlite3.OperationalError, or `store.list()` raises a non-StoreRefused `sc.Refused`. The operator gets 'wsd hit an internal error (X)', and nothing reaches wsd's log, so there is no record of which harness action failed, on which install label, or at what stage.
```

Fixed. harness_one catches the complete Exception boundary after explicit typed refusals, logs only the action and exception type, and emits a constant failed response. A sqlite3.OperationalError containing private text proves type visibility with no exception-text leak. No BaseException masking or native retry behavior was added.

## Verification scope and measured debt

Named offline fixtures only: fake queue/runtime/socket, private SQLite/store state, temporary Git repositories. No installation, activation, native queue operation, production DB access, message send, or model invocation. Earlier failing runs are retained separately; two initial failures were test fixture/dispatch naming errors, not production regressions. Final evidence records corrected tests and exact commands, without reusing the historical 579-test count.

Ripwire quality-delta is not clean. The retained report includes increased state-machine complexity/size, repeated boundary logging and independent-worker test shapes, and historical churn. Pytest-discovered tests also appear as static dead-code findings. These are disclosed, not suppressed or waived. The added approval quarantine condition is integrated into the existing in-use predicate instead of adding a second independent scan. No unrelated refactor was attempted.
