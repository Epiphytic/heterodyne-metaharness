# admind live harness (tests/live/), 2026-10-06

An opt-in pytest suite that runs a fully isolated admind against the real White Noise relays. It has two
throwaway operators, `tester` and `outsider`, a private beads database and its own btq config. It proves the
relay end to end on the current code. Branch `admind-live-harness`, from main 0711e61. No change under `src/`.

## Running it

```sh
HZ_LIVE=1 uv run pytest tests/live -v
```

- Without `HZ_LIVE=1`, every item under `tests/live/` is skipped by a collection hook in
  `tests/live/conftest.py`, so `uv run pytest -q` stays offline. Its CI result is unchanged.
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
| `tests/live/test_live_relay.py` | The six scenarios. |

## What it isolates, and how

Each run makes one root, `/tmp/hzlive-XXXXXXXX`, with mode 0700. It sits under `/tmp`, not `$TMPDIR`, so
that `hs/admind/marmot/ctl/wn-agent.sock` stays well under 100 bytes. Everything lives in it:

| Path | Purpose |
|---|---|
| `home/` | `HOME` for every child, plus `XDG_*` under it. This holds dolt's global config, btq's state, approve-bead's lock dir and Python's `Path.home()`. |
| `hc/` | `HETERODYNE_CONFIG_DIR`: `config.toml` and `policy.toml`. |
| `hs/` | `HETERODYNE_STATE_DIR`: admind's state, `admind.db`, `audit.jsonl`, sockets and the wn-agent home. |
| `btq/` | `BTQ_CONFIG_DIR`: `policy.json` (approvers `["tester"]`), `credentials.json`, the self-signed TLS cert and key. |
| `repo/` | `BTQ_REPO`: a git repo with one throwaway ADR and bd's `.beads/` descriptor, with no remote. |
| `dolt/` | The private `dolt sql-server` data, config and privileges. |
| `ops/<name>/` | Each operator's own wn-agent home, control socket and token. |
| `logs/` | Each long-running child's output. This is never printed, because it can hold invites. |

- **Environment.** Every child gets an environment built from scratch: PATH, LANG, TERM, `HZ_LIVE_ROOT`,
  HOME, TMPDIR, `XDG_*`, `TMUX_TMPDIR`, `HETERODYNE_*` and `BTQ_*`. Nothing else is inherited from the calling
  shell, so no `BEADS_*`, `CLAUDE_*`, `TMUX` or production `XDG_*` value can leak in. Binaries are resolved to
  absolute paths before any child HOME exists.
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
- **Two policy lists.** `btq/policy.json` has approvers `["tester"]`. admind's `policy.toml` has
  `operators = approvers = ["tester", "outsider"]` and `[identities.<name>] marmot_npub`. admind's own rule
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
  - `admind init` creates the group. Both operators auto-accept the welcome, and `group_info` shows 3 members
    within about 2 s.
- **Masking.** Nothing the harness prints carries a token, nsec, npub or 64-hex value. Failure messages go
  through `mask()`, which is `secret_scan.show` plus masking of npub, nsec and any run of 32 or more hex
  digits. `Operator.__repr__` prints only the name. Child output goes to `logs/`, never to the terminal.

## What it starts, and how it is killed

- **Long-running processes.** Each is started with `start_new_session=True`, so each is its own process group:
  - the private `dolt sql-server`;
  - a temporary wn-agent, used only to pre-create admind's identity and stopped straight after;
  - one wn-agent per operator;
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

## Timings (final run)

Setup steps (s):

| guard | beads | admind identity | operators | admind init | operators joined | admind start | join signal | teardown |
|---|---|---|---|---|---|---|---|---|
| 0.1 | 3.0 | 3.4 | 6.7 | 5.3 | 2.0 | 2.3 | 3.9 | 0.1 |

Fixture setup took 26.8 s and the whole session took 47.4 s. Earlier runs took 44.6 to 45.7 s.

## Results

Three full live runs passed 6 of 6. The first ever run failed only on an expected-string bug in the test,
since fixed: the docs render the ask ID in backticks, but the message text has no backticks.

| Scenario (test) | What it proves | Result | Call (s) |
|---|---|---|---|
| `test_question_card_and_reply` | The question card reaches both operators with the same message IDs. Tester's reply to the card gets "Answer recorded for ask …", and `ask get` shows status `answered` with tester's text. | pass | 3.3 |
| `test_answer_command_multiline` | `!answer <id> line1\nline2` sent top-level by outsider is stored as `line1\nline2`. | pass | 3.8 |
| `test_asks_lists_and_cancel_notice` | `!asks` lists `<id> question · … · <title>`. `admind ask cancel` posts "Ask … was cancelled by its poster." as a reply to the card, and the status becomes `cancelled`. | pass | 5.3 |
| `test_approve_by_reply` | On an isolated `kind:approval` bead with no gaps, `!approve <bead> <digest12>` as outsider's reply is refused: "outsider is not a btq approver. Nothing recorded.", and the bead stays undecided. The same from tester gives "Approved … as tester (digest …, via Marmot).", and `approve-bead --json` reads back closed, `decision approve`, `approved_by tester`, `via marmot`. The ask is `approved`. | pass | 7.6 |
| `test_redacted_bead_refused_at_post` | A bead whose description holds a token-shaped string (built at run time, never committed) is refused at post with "… decide it at the terminal", and no card is sent. | pass | 0.4 |
| `test_audit_holds_no_identifiers` | After the scenarios, `audit.jsonl` (about 50 records) contains no `npub1` and no 64-hex run. | pass | <0.1 |

**Other checks:**
- `uv run pytest -q tests/live` without HZ_LIVE: 6 skipped.
- Full suite, run once at the end: `uv run pytest -q -x` gave 1797 passed and 8 skipped (the 6 live tests among them)
  in 14 min 53 s.
- `uv run ruff check`: clean. `uv run pyright` (0 errors) covers `tests/live`.
- A kept root contained no reference to the real home except the two read-only binaries (`wn-agent` and
  btq's `approve-bead`).
- bd's descriptor names only the private port.

## Notes for the scenarios that come next

- `op.react(message_id, emoji)` is implemented with the S4 wire shape (`send_reaction` → `app_event_sent`)
  but has not yet been exercised, because the current daemon ignores reactions. Each operator's subscription
  also records `reaction_added` events, as `Seen(kind="reaction")`.
- `op.wait_card` takes the card's message IDs from admind's outbox. It reads `admind.db` with SQLite
  `mode=ro`, then waits until that operator has received every chunk. `Stack.card_message_ids` is the place
  to extend for `!details` chunks (`askd:` keys).
- `Stack.post_approval_reply` and `post_question_reply` return the raw reply for refusal tests.
  `Stack.edit_bead` exists for stale-digest scenarios.
- One session stack is shared by all tests, so every test posts its own asks. Tests that decide an ask
  should use their own bead.
