# admind live harness (tests/live/), 2026-10-06

An opt-in pytest suite that runs a fully isolated admind against the real White Noise relays. It has three
throwaway operators, a private beads database and its own btq config. `tester` and `tester2` are btq
approvers, for the two-approver race. `outsider` is an admind operator but not a btq approver. It proves the
relay end to end on the current code. Branch `admind-live-harness`, from main 0711e61. No change under `src/`.

## Running it

```sh
HZ_LIVE=1 uv run pytest tests/live -v
```

- Without `HZ_LIVE=1`, the live tests (`test_live_*.py`) are skipped by a collection hook in
  `tests/live/conftest.py`, so `uv run pytest -q` stays offline. `test_isolation_offline.py` needs no
  network or live binaries, so it always runs as part of the normal suite.
- `HZ_LIVE_KEEP=1` keeps the temp root for inspection. Processes are still killed.
- `HZ_LIVE_BTQ=<path>` points at another beads-task-queue checkout. The default is `~/repos/beads-task-queue`,
  which is only read from.
- Requirements: `wn-agent`, `bd`, `dolt`, `git` and `openssl` on PATH, and network access to
  `wss://relay.eu.whitenoise.chat` and `wss://relay.us.whitenoise.chat`. These two relays are hard-coded and
  nothing is read from `~/.config/heterodyne`.
- The terminal summary prints timings per setup step. It also prints how many processes the final sweep had
  to kill and how many stale runs were reaped.

## Files

| File | What it is |
|---|---|
| `tests/live/harness.py` | `Stack` (the isolated stack and its API), `Operator` (a throwaway wn-agent identity), `Procs` (process tracking and teardown), `guard`/`self_check`, `send_reaction`, `mask`, `reap_stale_runs`. |
| `tests/live/conftest.py` | The HZ_LIVE skip hook, the session `stack` fixture (try/finally, atexit backup, SIGTERM → KeyboardInterrupt), and the timings summary. |
| `tests/live/stub_admind.py` | The `admind run` entry point with the two stubs. |
| `tests/live/test_live_relay.py` | The seven live scenarios. |
| `tests/live/test_isolation_offline.py` | The offline hostile-override tests. They run in the normal suite. |

## What it isolates, and how

Each run makes one root, `/tmp/hzlive-XXXXXXXX`, with mode 0700. It sits under `/tmp`, not `$TMPDIR`, so
that `hs/admind/marmot/ctl/wn-agent.sock` stays well under 100 bytes. Everything lives in it:

| Path | Purpose |
|---|---|
| `home/` | `HOME` for every child, plus `XDG_*` under it. This holds dolt's global config, btq's state, approve-bead's lock dir and Python's `Path.home()`. |
| `hc/` | `HETERODYNE_CONFIG_DIR`: `config.toml` and `policy.toml`. |
| `hs/` | `HETERODYNE_STATE_DIR`: admind's state, `admind.db`, `audit.jsonl`, sockets and the wn-agent home. |
| `btq/` | `BTQ_CONFIG_DIR`: `policy.json` (approvers `["tester", "tester2"]`), `credentials.json`, the self-signed TLS cert and key. |
| `repo/` | `BTQ_REPO`: a git repo with one throwaway ADR and bd's `.beads/` descriptor, with no remote. |
| `dolt/` | The private `dolt sql-server` data, config and privileges. |
| `ops/<name>/` | Each operator's own wn-agent home, control socket and token. |
| `logs/` | Each long-running child's output. This is never printed, because it can hold invites. |

- **Environment.** `child_env(root, port)` builds every child's environment (admind, wn-agent, bd, dolt,
  approve-bead, git, openssl) from an allowlist, never from `os.environ`. Only PATH comes from the parent.
  - It sets HOME, TMPDIR, `XDG_*`, `TMUX_TMPDIR` and `HETERODYNE_CONFIG_DIR`/`HETERODYNE_STATE_DIR`, all
    under the root.
  - It sets `BTQ_CONFIG_DIR`, `BTQ_POLICY`, `BTQ_REPO` (the private repo) and `BTQ_DOLT_HOST`/`PORT`/
    `DATABASE` for the private server.
  - bd calls add exactly what btq's `Queue.__init__` builds, provisioned for the private server:
    `BEADS_DOLT_PASSWORD` (from the temp `credentials.json`; btq has no separate password file),
    `BEADS_DOLT_SERVER_USER=bel`, `BEADS_DOLT_SERVER_TLS=true`, `SSL_CERT_FILE` (the temp cert), `BEADS_DIR`
    and `BEADS_DOLT_SERVER_HOST`/`PORT`/`DATABASE`.
  - Binaries are resolved to absolute paths before any child HOME exists.
  - The production locations are taken from the password database's home directory, not `$HOME`, so a
    hostile HOME cannot move them.
- **Per-spawn check.** `Procs.start` and `Procs.run` call `check_child_env` before every spawn, so it runs
  before every database operation. It refuses:
  - any key outside the allowlist;
  - a missing or wrong `HZ_LIVE_ROOT`;
  - any location key (HOME, `XDG_*`, `HETERODYNE_*`, `BTQ_CONFIG_DIR`/`POLICY`/`REPO`, `SSL_CERT_FILE`,
    `BEADS_DIR`) outside the root or overlapping a production location;
  - any `BTQ_DOLT_*` or `BEADS_DOLT_SERVER_*` endpoint that is incomplete, not loopback, port 3307 or
    database `tasks`.
- **Guard.** `Stack.check_isolation` runs before anything starts, and again with admind's settings once the
  config is complete.
  - It resolves each location the way the children do: `heterodyne.config.paths`, btq's own `locations()`
    (bin/btq is imported in a child under the test environment, never run), `admind.settings.resolve()` for
    `state_dir`, `marmot_home`, `workdir` and `alerts_dir`, and the operator homes.
  - It asserts that every location is under the root, and that none is inside or contains a production
    location: `~/.local/state/heterodyne`, `~/.config/heterodyne`, `~/.config/beads-task-queue`, `~/.hermes`,
    the btq state and share dirs, the White Noise share dir and `~/.config/systemd`.
  - It asserts that the beads endpoint is neither port 3307 nor database `tasks`, and that btq resolves the
    private port and database.
  - `approve_bead` must be the read-only btq checkout's binary, and the relays must be the two test relays.
  - `self_check` then shows that the guard refuses each production location, port 3307, database `tasks` and
    the root's parent. A guard that accepted those would prove nothing.
- **Beads.**
  - A private `dolt sql-server` runs on a free loopback port and has TLS available. It creates database
    `hzlive` and a `bel` user with a random password.
  - `bd init --server --external` sets up prefix `hzl` in `repo/`.
  - Beads are created and edited as btq's `Queue` would do it: `bel` over TLS, with
    `SSL_CERT_FILE=btq/server.crt`.
  - Plaintext is allowed on that private server only so that `bd init` can run as root before metadata
    exists.
  - The shared server (loopback, port 3307) is never contacted.
- **Two policy lists.** `btq/policy.json` has approvers `["tester", "tester2"]`. admind's `policy.toml` has
  `operators = approvers = ["tester", "tester2", "outsider"]` and `[identities.<name>] marmot_npub`. admind's own rule
  requires operators to be a subset of its approvers. That is a different list from btq's.
- **No admin agent.** `stub_admind.py` runs the real `cli.main(["run"])` with two module globals of
  `heterodyne.admind.cli` replaced, so no `src/` seam was needed:
  - `Tmux` becomes an in-memory pane. It is alive from launch, so no tmux server runs and no `claude`
    starts. Pastes are counted to a file and dropped; none happened in these scenarios.
  - `for_backend` becomes a service manager that never runs `systemctl`.
  - `start_timeout_seconds = 3600`, so the missing SessionStart never triggers a relaunch.
  - The adapter binary is `/bin/false` and is never executed.
  - Everything else is real: admind's own wn-agent child, the relays, `ask.sock`, the store, the audit log
    and `approve-bead`.
- **Identities.**
  - admind's account is pre-created with `wn-agent bootstrap --label heterodyne-admind
    --invite-policy deny`, using the same home and token file that `admind init` uses. `admind init` then
    reuses it, which lets the operators name it before the group exists.
  - Each operator is bootstrapped with `--invite-policy allowlist --allow-welcomer <that admind npub>`, so
    only the isolated admind can invite them.
  - `admind init` creates the group. All three operators auto-accept the welcome, and `group_info` shows 4 members
    within about 2 s.
- **Masking.** Nothing the harness prints carries a token, nsec, npub or 64-hex value. Failure messages go
  through `mask()`, which is `secret_scan.show` plus masking of npub, nsec and any run of 32 or more hex
  digits. `Operator.__repr__` prints only the name. Child output goes to `logs/`, never to the terminal.

## What it starts, and how it is killed

- **Long-running processes.** Each is started with `start_new_session=True`, so each is its own process group:
  - the private `dolt sql-server`;
  - a temporary wn-agent, used only to pre-create admind's identity and stopped straight after;
  - one wn-agent per operator (three);
  - the stub admind, whose own wn-agent child is in admind's process group.
- **Short-lived processes.** These also run in their own sessions: `openssl`, `git`, `dolt sql`, `bd`,
  `wn-agent bootstrap`, `admind init` and `admind ask ...`, and `approve-bead`. If one times out, its group is
  killed.
- **Teardown.** `Stack.close` is idempotent. It runs from the fixture's `finally` and from `atexit`.
  1. It sends SIGTERM to each recorded process group by PID, newest first. admind stops cleanly and writes
     `admind stop` to the audit log.
  2. It sends SIGKILL to the group after 15 s, or immediately if the leader has already exited, which catches
     orphaned group members.
  3. It sweeps `/proc/*/environ` for this run's exact `HZ_LIVE_ROOT=<root>` entry and kills any match by PID.
  4. It removes the root.

  No `pkill`, `killall`, tmux server or `systemctl` is used.
- **SIGTERM to pytest.** The fixture installs a SIGTERM handler that raises KeyboardInterrupt, so pytest
  unwinds through the same teardown. Tested: a run sent SIGTERM mid-test left no process and no root.
- **SIGKILL to pytest.** This cannot be caught. Each root records `owner.pid`. At start, `reap_stale_runs`
  looks for earlier `/tmp/hzlive-*` roots that we own and whose owner is dead. It kills every process
  carrying that root's marker by PID and removes the root. Tested:
  - pytest was sent SIGKILL during setup;
  - five processes survived: dolt, two operator wn-agents, the stub admind and admind's wn-agent;
  - the next run reported "stale runs reaped at start: 1", and afterwards no `hzlive` process or root
    remained.
  - A live concurrent run is never touched, because its owner is alive.

## Timings (three operators)

Setup steps (s):

| guard | beads | admind identity | operators | admind init | operators joined | admind start | join signal | teardown |
|---|---|---|---|---|---|---|---|---|
| 0.0 | 3.0 | 3.3 | 9.9 | 5.4 | 0.0 | 2.3 | 3.7 | 0.2 |

- Fixture setup took 27.7 s. The session (the 22 offline tests and the 6 live tests of that run) took
  48.7 s. The run with the reaction scenario added took 49.2 s.
- Each extra operator costs about 3 s to bootstrap.
- Joining can take 0 to 2 s: by the time the last operator's bootstrap returns, the welcomes have often
  already been accepted.
- With two operators, setup took 26.8 s and full runs took 44.6 to 47.4 s.

## Results

- Three full live runs with two operators passed 6 of 6.
- After the design-review changes, a run with three operators passed 28 of 28 (22 offline and 6 live).
  The final run, with the reaction scenario added, passed 7 of 7 live.
- The first ever run failed only on an expected-string bug in the test, since fixed: the docs render the ask
  ID in backticks, but the message text has no backticks.

| Scenario (test) | What it proves | Result | Call (s) |
|---|---|---|---|
| `test_question_card_and_reply` | The question card reaches both operators with the same message IDs. Tester's reply to the card gets "Answer recorded for ask …", and `ask get` shows status `answered` with tester's text. | pass | 3.3 |
| `test_answer_command_multiline` | `!answer <id> line1\nline2` sent top-level by outsider is stored as `line1\nline2`. | pass | 3.8 |
| `test_asks_lists_and_cancel_notice` | `!asks` lists `<id> question · … · <title>`. `admind ask cancel` posts "Ask … was cancelled by its poster." as a reply to the card, and the status becomes `cancelled`. | pass | 5.3 |
| `test_approve_by_reply` | On an isolated `kind:approval` bead with no gaps, `!approve <bead> <digest12>` as outsider's reply is refused: "outsider is not a btq approver. Nothing recorded.", and the bead stays undecided. The same from tester gives "Approved … as tester (digest …, via Marmot).", and `approve-bead --json` reads back closed, `decision approve`, `approved_by tester`, `via marmot`. The ask is `approved`. | pass | 7.6 |
| `test_redacted_bead_refused_at_post` | A bead whose description holds a token-shaped string (built at run time, never committed) is refused at post with "… decide it at the terminal", and no card is sent. | pass | 0.4 |
| `test_reaction_frame_keeps_event_id` | Tester's `send_reaction` on admind's ready notice reaches tester2 and outsider with the raw frame kept. `event_id_hex`, which the src decoder drops, equals the ID `send_reaction` returned, and the target and actor are right. | pass | ~1 |
| `test_audit_holds_no_identifiers` | After the scenarios, `audit.jsonl` (about 50 records) contains no `npub1` and no 64-hex run. | pass | <0.1 |

**Offline hostile-override tests** (`test_isolation_offline.py`, 22 tests, about 0.6 s, normal suite):
- The parent environment is filled with production-looking values: `BTQ_POLICY` at the real
  `~/.config/beads-task-queue/policy.json`, the real `BTQ_CONFIG_DIR`/`REPO`/`CREDENTIALS`/`TLS_CERT`,
  `BTQ_DOLT_PORT=3307`, `BTQ_DOLT_DATABASE=tasks`, the real `HETERODYNE_CONFIG_DIR`/`STATE_DIR`, HOME and
  `XDG_*`, `BEADS_DOLT_SERVER_PORT=3307`/`DATABASE=tasks`, `BEADS_DIR`, a password, a credential command,
  and `TMUX`.
- The tests show that:
  - `child_env` carries none of those values, only allowlisted keys, every location under the root, and
    the private endpoint;
  - `heterodyne.config.paths` resolves under the root;
  - `check_child_env` refuses an environment carrying any one of those values (19 parametrized cases; the
    password is not a location, so it is covered only by the first test);
  - `Procs.run` and `Procs.start` with port 3307 spawn nothing;
  - the forbidden locations do not follow `$HOME`, and the guard self-check passes;
  - btq's own `locations()` and policy path, evaluated in a child under the built environment, resolve
    under the root to the private port and database. This test is skipped when no btq checkout is present,
    for example in CI.

**Other checks:**
- `uv run pytest -q tests/live` without HZ_LIVE: 22 passed, 6 skipped. 7 skipped since the reaction
  scenario was added.
- Full suite, run once before the design-review changes: `uv run pytest -q -x` gave 1797 passed and
  8 skipped (the 6 live tests among them) in 14 min 53 s. The later changes touch only `tests/live/`. They
  were checked with `tests/live` and `tests/test_admind_daemon.py` in one session (63 passed, 6 skipped)
  to confirm the module names do not collide.
- `uv run ruff check`: clean. `uv run pyright` (0 errors) covers `tests/live`.
- A kept root contained no reference to the real home except the two read-only binaries (`wn-agent` and
  btq's `approve-bead`).
- bd's descriptor names only the private port.

## Notes for the scenarios that come next

- `op.react(message_id, emoji)` sends with the S4 wire shape (`send_reaction` → `app_event_sent`) and
  returns the reaction's event ID.
- Each operator's subscription goes through `RawControlClient.subscribe_raw`. It is the src client's own
  framing and decoding, but it also yields each frame as parsed JSON.
- `op.frames` holds every frame raw. A reaction's `Seen.message_id` is its `event_id_hex`, and `Seen.raw`
  is its frame.
- `op.wait_reaction(target, emoji=..., sender=...)` waits for one.
- `op.wait_card` takes the card's message IDs from admind's outbox. It reads `admind.db` with SQLite
  `mode=ro`, then waits until that operator has received every chunk. `Stack.card_message_ids` is the place
  to extend for `!details` chunks (`askd:` keys).
- `Stack.post_approval_reply` and `post_question_reply` return the raw reply for refusal tests.
  `Stack.edit_bead` exists for stale-digest scenarios.
- One session stack is shared by all tests, so every test posts its own asks. Tests that decide an ask
  should use their own bead.
