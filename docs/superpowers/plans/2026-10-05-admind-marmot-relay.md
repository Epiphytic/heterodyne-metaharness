# admind Marmot relay for asks and approvals (interim) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a local process can post a question, a merge request or a btq approval request to admind's operator group. The operator answers from the phone, by replying or with `!answer`. They decide approvals with `!approve <bead> <digest12>` or `!deny <bead> <reason>`, sent as a reply to the card. admind records the decision through `approve-bead`, with `via=marmot`.

**Architecture:**

- New modules:
  - `asks.py` (pure): validation, IDs, card and details rendering, command-free logic;
  - `approvals.py`: the `approve-bead` subprocess runner, the readout schema and the read-back outcome mapping.
- New SQLite tables, created if absent.
- A second instance of `ctl.CtlServer`, with its limit and request type as parameters, serves `ask.sock`.
- The daemon gains four commands, a reply-to-card branch in `handle`, an ask-socket handler and a startup reconcile.
- One change in `beads-task-queue`: `approve-bead` gets `--json`, `--expect-digest`, `--via` and `--via-ref`.

**Tech Stack:** unchanged. Python 3.12+, asyncio, sqlite3, msgspec, pytest, ruff, pyright (strict on `src/`). beads-task-queue is stdlib Python with `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-05-admind-marmot-relay-design.md` (this commit range). Rule numbers R1–R20 and sections (§) refer to it; "ADR §n" refers to `docs/adr/0001-workstreams-v2.md` at revision 13.

## Global Constraints

- **Variables:**
  - `$HZ` is a clone of the product repo (`heterodyne-metaharness`).
  - `$BTQ` is a clone of `beads-task-queue`, not its live checkout.
  - No committed file may hold an install path, npub, hex account or group ID, token, relay URL, unit file path or operator name of the reference install.
- **Live systems are off-limits** until Task 4's deployment steps, and those need the controller's explicit go per step:
  - never stop, restart or reconfigure `heterodyne-admind.service` or any other unit;
  - never open a Marmot home;
  - never touch `~/.config/beads-task-queue` or a live btq queue.
- **Tests:**
  - No network, real `wn-agent`, real `claude`, `~/.claude`, real `bd`, real Dolt server or real `~/.config/beads-task-queue`.
  - admind tests use the existing `Harness` and `FakeWnAgent`, plus a fake `approve-bead` script under `tmp_path` (Task 2, `tests/fakes/fake_approve_bead.py`).
  - beads-task-queue tests use a fake `bd` on `PATH`, with `HOME`, `BTQ_CONFIG_DIR` and `BTQ_POLICY` in a temp directory.
  - Async tests use `asyncio.run`; no pytest-asyncio.
- **No new runtime dependencies** in either repo. Runtime deps stay exactly `msgspec` in `$HZ` and stdlib in `$BTQ`.
- **Never print tokens or npubs.** Operator names reach Marmot only through `redact`. Errors name config keys, not values.
- **Don't patch out the latch.** No test, flag or config may disable it or the digest check.
- **Install-agnostic:** `scripts/check_install_agnostic.py` stays clean. No model names in `src/`.
- **Review rule** (ADR §11.1): every task ends with a review by a different LLM than its implementer.
  - If the implementer is Claude: `~/.codex/packages/app-server-daemon/releases/0.159.3-x86_64-unknown-linux-musl/bin/codex exec -m gpt-6.1-sol -c model_reasoning_effort=medium -s read-only -o /tmp/review-<task>.md "$(cat /tmp/brief-<task>.md)" < /dev/null`, under `timeout 900`.
    - `< /dev/null` is mandatory.
    - Kill by PID, never `pkill -f`.
  - If the implementer is Codex: `claude -p --model claude-opus-5-5 --permission-mode plan "<brief>" > /tmp/review-<task>.md`.
  - Fix or rebut every blocking finding. Close evidence: `Code-Review: reviewer=<model> author=<model> mode=cross-model range=<BASE>..<HEAD>`.
- **Beads** (workstream `heterodyne`):
  - The design is approved by a `kind:approval` bead created for this spec, `<APPROVAL>`, with `metadata.adr_revision` set to ADR revision 13's commit.
  - Tasks 1–4 are `kind:task`, each with `metadata.design_approval=<APPROVAL>`, and each depends on `<APPROVAL>`.
  - Order: 1 → 2 → 3 → 4. Task 2 can start in parallel with Task 1. Task 3 needs Task 1 merged, because the real flag names are its contract.
- **Branches and PRs:**
  - Task 1: branch `approve-bead-relay` in `$BTQ` from `master`; its own PR.
  - Tasks 2–4: one integration branch `admind-relay` in `$HZ` from `main`, with one PR at the end of Task 4 (as plan 3).
  - Nothing is merged by an agent. Every merge ask carries the full PR URL.
- **Gate before each `$HZ` commit:** `uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`, all clean.
- **Gate before each `$BTQ` commit:** `python3 -m unittest discover -s tests -v`. `test_queue.py`'s live-server cases skip without a server; the new tests must pass offline.

## Decisions made in this plan (within the spec)

| # | Decision |
|---|---|
| P1 | `--json` readout lines are captured from `approve-bead`'s own `show_field`/`show_ask` with `contextlib.redirect_stdout`. That way they are byte-identical to the terminal readout, and no second renderer can drift. |
| P2 | `--json` needs no `--as`, because it only reads. With `--json`, the validity checks (closed, labels, gaps, design_review, posted digest) are reported as fields, and the exit status is 0. A `bd` failure still exits non-zero. |
| P3 | `approvals.ApproveBead` is the only place admind builds `approve-bead` argv. It always passes `--flag=value`, and has one method per call (`read`, `decide`). Tests swap the binary, never the class. |
| P4 | `ctl.CtlServer` gains keyword-only `max_request`, `request_type` and `check`; the defaults are the current values, so `ctl.sock` code and tests are unchanged. `ask.sock` uses `asks.ASK_SOCKET`, 262,144 bytes, and the tagged union `AskRequest`. |
| P5 | The card budget is counted on the rendered text before redaction. Redaction never removes lines, and expansion is bounded by the chunker, as in B13. |
| P6 | `Store.ask_for_message` joins `outbox` on `message_id` with `status='sent'` and `key LIKE 'ask:%' OR key LIKE 'askd:%'`. The ask ID is parsed from the key, so no new index table is needed. An index on `outbox(message_id)` is added (`CREATE INDEX IF NOT EXISTS`). |

## File map

| File | Task | Change |
|---|---|---|
| `$BTQ/bin/approve-bead` | 1 | `--json`, `--expect-digest`, `--via`, `--via-ref` |
| `$BTQ/tests/test_approve_bead_relay.py` | 1 | new, offline |
| `$BTQ/docs/PICKUP.md`, `$BTQ/README.md` | 1 | document the flags (one paragraph each) |
| `src/heterodyne/admind/asks.py` | 2 | new: request structs, validation, IDs, rendering |
| `src/heterodyne/admind/store.py` | 2 | `asks`, `ask_answers`, `ask_details` tables and methods |
| `src/heterodyne/admind/ctl.py` | 2 | parameterise `CtlServer` and `request` |
| `src/heterodyne/admind/commands.py` | 2, 3 | `asks`, `answer` (2); `approve`, `deny` (3); HELP |
| `src/heterodyne/admind/daemon.py` | 2, 3 | ask socket, card replies, the commands, reconcile |
| `src/heterodyne/admind/cli.py` | 2 | `admind ask post\|get\|wait\|list\|cancel` |
| `src/heterodyne/admind/approvals.py` | 3 | new: `ApproveBead`, `Readout`, `settle` |
| `src/heterodyne/admind/settings.py` | 3 | optional `approve_bead` |
| `tests/fakes/fake_approve_bead.py` | 2 | new fake |
| `tests/test_admind_asks.py` | 2 | unit and integration tests for questions and merges |
| `tests/test_admind_approvals.py` | 3 | unit and integration tests for approvals |
| `docs/admind.md`, `docs/configuration.md`, `examples/config.toml` | 4 | runbook, key, acceptance |
| `docs/adr/0001-workstreams-v2.md` | — | **not edited**; the §8 delta goes to the r14 editor (spec §10) |

---

### Task 1: `approve-bead` relay flags (beads-task-queue)

**Files:** `$BTQ/bin/approve-bead`, `$BTQ/tests/test_approve_bead_relay.py`, `$BTQ/docs/PICKUP.md`, `$BTQ/README.md`.

**Interface (the contract Task 3 calls):**

```
approve-bead ID --json
  stdout: one JSON object, exit 0 unless bd fails:
  {"format": 1, "id": str, "status": str, "labels": [str], "kind_approval": bool,
   "digest": hex64, "posted_digest": str|null, "gaps": [str], "design_review_valid": bool|null,
   "adr_revision": str|null, "approvers": [str],
   "decision": str|null, "approved_by": str|null, "denied_by": str|null,
   "approved_digest": str|null, "denied_digest": str|null, "via": str|null, "via_ref": str|null,
   "readout": {"title": [str], "description": [str], "ask": [str]}}
approve-bead ID --as=NAME --yes [--deny --note=TEXT] --expect-digest=HEX64 --via=marmot --via-ref=REF
  exit 0: decided; non-zero with one line on stderr otherwise.
```

- `design_review_valid` is null when the bead has no `design_review`.
- `--expect-digest` must be 64 lowercase hex characters. It is compared with the digest at the first computation (exit 3, "The ask is not the one you were shown (expected …12, now …12); nothing written.") and again at the existing pre-write recheck (same message).
- `--via` is `cli` (default) or `marmot`. `--via-ref` needs `--via=marmot` and must match `[A-Za-z0-9:._-]{1,80}`; anything else is a usage error (exit 2 from argparse), written before any `bd` call.
- Fields written: `via=<via>` and, if given, `via_ref=<ref>`. Comment: `Approved by NAME at T via approve-bead (Marmot, admind message REF); context_digest=…`.

Steps:

- [ ] **1.1 Failing tests.** Create `tests/test_approve_bead_relay.py`:

```python
"""approve-bead's relay flags, offline: a fake bd serves beads from a JSON file in a temp dir."""
import json, os, subprocess, sys, tempfile, textwrap, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE_BD = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    db = os.environ["FAKE_BD_DB"]; log = os.environ["FAKE_BD_LOG"]
    beads = json.load(open(db))
    args = [a for a in sys.argv[1:] if a != "--json"]
    while args and args[0] in ("-C", "--actor"):
        args = args[2:]
    open(log, "a").write(json.dumps(args) + "\\n")
    cmd = args[0]
    if cmd == "show":
        print(json.dumps([beads[args[1]]]))
    elif cmd == "comments" and len(args) == 2:
        print("[]")
    elif cmd == "update":
        bead = beads[args[1]]; rest = args[2:]
        while rest:
            flag, value, rest = rest[0], rest[1], rest[2:]
            if flag == "--set-metadata":
                k, v = value.split("=", 1); bead.setdefault("metadata", {})[k] = v
        json.dump(beads, open(db, "w")); print("{}")
    elif cmd == "comments":
        print("{}")
    elif cmd == "close":
        beads[args[1]]["status"] = "closed"; json.dump(beads, open(db, "w")); print("{}")
    elif cmd == "dolt":            # any connectivity probe Queue() makes
        print("{}")
    else:
        print(json.dumps({"error": cmd}), file=sys.stderr); sys.exit(1)
''')
```

  The fixture:

  - writes a temp `HOME`, `BTQ_CONFIG_DIR` with `credentials.json` and `policy.json` (approvers `["alice", "bob"]`), and a temp git repo holding `docs/adr/0001-x.md` for a pinned file ref;
  - sets `BTQ_POLICY` and `BTQ_REPO` (or the env names `btq.locations()` reads) to the temp paths;
  - puts the fake `bd` first on `PATH`;
  - builds `approval-1`: open, labels `["kind:approval"]`, a complete ask with `effect`, `excludes`, `why`, `risks` and `refs` (the pinned ADR file), and `adr_revision`.

  Before writing the fixture, read `btq.Queue.__init__` and `btq.locations()` for exactly which env variables and probes are needed. If `Queue()` opens a Dolt connection eagerly, patch through a small `--json`-only path that builds the queue lazily (keep the patch in this task, covered by a test).

  Tests (each runs `bin/approve-bead` as a subprocess, with the fixture's env):

  - `test_json_is_read_only_and_complete`: the keys equal the interface set; `digest` equals `btq.ask_digest` computed in-process; `readout["ask"]` equals the lines `show_ask` prints; the fake `bd` log has no `update`, `comments add` or `close`.
  - `test_json_reports_checks_instead_of_exiting`: a closed bead, a bead without `kind:approval`, a bead with a gap (empty `why`) each exit 0 with `status`, `kind_approval` and `gaps` set accordingly.
  - `test_expect_digest_mismatch_writes_nothing`: `--expect-digest=` + `"0"*64` exits 3; no writes.
  - `test_expect_digest_recheck`: wrap `bd` so the second `show` returns an edited description; exits non-zero with "nothing written"; no writes.
  - `test_marmot_decision_records_via`: `--as=alice --yes --expect-digest=<d> --via=marmot --via-ref=marmot:id:0123456789ab` closes the bead; the metadata has `via=marmot`, `via_ref=marmot:id:0123456789ab` and `approved_digest=<d>`; the comment names the ref; `btq.Queue(...).approval_valid(closed, rev)` is True with a fixture `design_review`.
  - `test_deny_via_marmot`: `--deny --note=too broad` writes `decision=deny`, `via=marmot`.
  - `test_default_via_is_cli`: no `--via` gives `via=cli` and no `via_ref`.
  - `test_via_ref_requires_marmot_and_pattern`: `--via-ref=x` without `--via=marmot`, and `--via=marmot --via-ref='a b'`, exit 2 before any `bd` call (empty log).
  - `test_via_ref_is_not_a_digest_key`: `'via_ref'.endswith('_digest')` is False, and `approval_valid` does not reject the Marmot record (covered above; asserted explicitly so a rename can't slip).

- [ ] **1.2 Run them to see them fail:** `python3 -m unittest tests.test_approve_bead_relay -v` gives errors on unknown arguments.
- [ ] **1.3 Implement** in `bin/approve-bead`:
  - add the four arguments. Validate `--expect-digest` (`re.fullmatch('[0-9a-f]{64}')`) and `--via-ref` (`[A-Za-z0-9:._-]{1,80}`, requires `--via=marmot`) right after `parse_args`, with `parser.error`;
  - with `--json`, skip the `--as`/approver check (P2), compute `issue`, `resolution`, `digest` and `gaps` exactly as now, capture `show_field('title', …)`, `show_field('description', …)` and `show_ask(…)` with `redirect_stdout(io.StringIO())`, print the object, and return before the readout and checks;
  - after the first `digest` computation: if `args.expect_digest` differs, `sys.exit(3)` with the message;
  - at the pre-write recheck: compare against `args.expect_digest or digest`;
  - `fields`: replace `'via=cli'` with `f'via={args.via}'`, and append `f'via_ref={args.via_ref}'` if given; extend the comment.
- [ ] **1.4 Run the suite:** `python3 -m unittest discover -s tests -v`. All new tests pass, existing ones pass or skip as before.
- [ ] **1.5 Docs:** one paragraph in `docs/PICKUP.md` (under approvals) and `README.md`: the flags exist for admind's Marmot relay. `via` is provenance only and is not checked by `approval_valid`.
- [ ] **1.6 Commit:** `git add bin/approve-bead tests/test_approve_bead_relay.py docs/PICKUP.md README.md && git commit -m "approve-bead: --json readout, --expect-digest, --via/--via-ref for the admind Marmot relay"`.
- [ ] **1.7 Cross-model review**, fix, then open the PR (`gh pr create`). Report the full PR URL to the controller for the operator's merge.

---

### Task 2: Asks: store, socket, CLI, cards, answers

**Files:** `asks.py` (new), `store.py`, `ctl.py`, `commands.py`, `daemon.py`, `cli.py`, `tests/fakes/fake_approve_bead.py` (new), `tests/test_admind_asks.py` (new).

**Interfaces:**

```python
# asks.py
ASK_SOCKET = "ask.sock"
MAX_REQUEST = 262_144
ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
MAX_OPEN, MAX_PER_HOUR, MAX_ANSWER, MAX_ANSWERS = 20, 30, 16_000, 50
CARD_LINES, CARD_CHARS = 40, 3_500
AskKind = Literal["question", "merge", "approval"]

class AskPost(msgspec.Struct, frozen=True, tag="post"):
    kind: AskKind; title: str = ""; body: str = ""; pr_url: str | None = None
    head_sha: str | None = None; bead: str | None = None; poster: str = "local"
class AskGet(msgspec.Struct, frozen=True, tag="get"):    ask_id: str
class AskList(msgspec.Struct, frozen=True, tag="list"):  pass
class AskCancel(msgspec.Struct, frozen=True, tag="cancel"): ask_id: str
AskRequest = AskPost | AskGet | AskList | AskCancel

class AnswerView(msgspec.Struct, frozen=True): kind: str; operator: str; text: str; at: str
class AskView(msgspec.Struct, frozen=True):
    ask_id: str; kind: str; status: str; title: str; bead: str | None; digest12: str | None
    delivered: bool; answers: list[AnswerView]; outcome: str | None; decided_by: str | None
class AskReply(msgspec.Struct, frozen=True):
    result: Literal["posted", "ok", "refused", "failed"]; message: str
    ask: AskView | None = None; asks: list[AskView] | None = None

def validate(req: AskPost) -> str | None: ...          # R4, R16, R19; None = valid
def new_id(taken: Callable[[str], bool]) -> str: ...
def normalise_id(text: str) -> str | None: ...         # lower-cased, alphabet-checked, 4 characters
def card(row: AskRow) -> tuple[str, bool]: ...         # (text, truncated); question/merge here, approval in Task 3
def full_text(row: AskRow) -> str: ...                 # what !details sends
def list_line(row: AskRow, now: datetime) -> str: ...
```

```python
# store.py (new methods; all inside existing transaction semantics)
@dataclass(frozen=True)
class AskRow: ask_id: str; kind: str; poster: str; title: str; body: str; pr_url: str | None
    head_sha: str | None; bead: str | None; digest: str | None; truncated: bool; status: str
    outcome: str | None; decided_by: str | None; created_at: str; updated_at: str
def insert_ask(self, row: AskRow, peer_pid: int | None) -> None
def ask(self, ask_id: str) -> AskRow | None
def asks_with_status(self, *statuses: str) -> list[AskRow]
def ask_posted_since(self, iso: str) -> int
def set_ask(self, ask_id: str, status: str, *, outcome: str | None = None, decided_by: str | None = None) -> None
def add_answer(self, ask_id: str, kind: str, operator: str, message_id: str, text: str) -> int   # count after
def answers(self, ask_id: str) -> list[AnswerRow]
def ask_for_message(self, message_id: str | None) -> str | None          # P6, R7
def ask_delivered(self, ask_id: str) -> bool                              # any sent ask:<id>:* row
def add_ask_details(self, ask_id: str, operator: str, message_id: str) -> None
def ask_details_delivered(self, ask_id: str, operator: str) -> bool       # every askd:<id>:<mid>:* row sent, for one of the operator's requests
def open_ask_for_bead(self, bead: str) -> AskRow | None
```

```python
# ctl.py
class CtlServer:
    def __init__(self, path: Path, handler: Callable[[Any], Awaitable[msgspec.Struct]], audit: Audit, *,
                 max_request: int = MAX_REQUEST, request_type: Any = CtlRequest,
                 check: Callable[[Any], str | None] | None = None, kind: str = "ctl") -> None
async def request(path: Path, req: msgspec.Struct, timeout: float = 300, reply_type: Any = CtlReply) -> Any
```

  - `kind` is the audit record kind, so `ask.sock` refusals are audited as `ask`.
  - The refusal reply type comes from a `refuse(message)` callable supplied with `request_type`. For `ctl` the default is `CtlReply("refused", …)`, unchanged.

```python
# commands.py
CommandName = Literal["new", "interrupt", "tail", "restart", "ps", "details", "asks", "answer"]
@dataclass(frozen=True)
class Command: name: CommandName; arg: str | None = None; lines: int = TAIL_DEFAULT; rest: str | None = None
```

  - `!answer <id> <text>` uses `arg=id` and `rest=` the text after the ID, with internal newlines kept and the outer whitespace stripped; an empty text is a usage error.
  - Parsing `!answer` uses `text[1:].split(maxsplit=2)`, not the whitespace split.

Daemon changes:

- `run()` starts a second `ctl.CtlServer(state_dir / asks.ASK_SOCKET, self.on_ask, self.audit, max_request=asks.MAX_REQUEST, request_type=asks.AskRequest, kind="ask")` next to `ctl.sock`, and closes it in `finally`.
- `on_ask(req)` handles the four requests. `post`:
  - validates;
  - refuses if latched (R17), if `approval` is posted while `approve_bead` is unset (Task 3 fills in the rest; Task 2 refuses `approval` with "approval asks need Task 3"), or if a limit is hit (R15);
  - inserts the ask, renders the card, posts its chunks as `ask:<id>:<i>` (lane 1, unthreaded) in one transaction;
  - audits `ask/posted`.
  - `get`/`list`/`cancel` read or update the store. `cancel` of an `open` ask sets `cancelled` and threads `asknote:<id>:cancelled:0:<i>` to the card's first sent chunk (or posts it unthreaded if none was sent).
- `handle(mid, text, target)`, after the control-character check and before `commands.parse`:

```python
ask_id = self.store.ask_for_message(target)
if ask_id is not None and not text.startswith("!"):
    await self.ask_reply(mid, ask_id, text)        # R13/R14: never pasted to the agent
    return
```

- `ask_reply` checks `authorised(mid)`, then, for a question or merge ask:
  - adds an answer (status `answered` from `open` or `answered`);
  - refuses past `MAX_ANSWERS`, or a text over `MAX_ANSWER`;
  - refuses an ask that is `cancelled`/`superseded` (fixed wording);
  - then `finish(mid, "done", <spec §6 wording>, "ask")`.
  - Approval asks: Task 3.
- `!asks` lists open asks (`finish`). `!answer` resolves the ID; an unknown ID gets `No ask <id>.` and an approval ask gets the R13 hint. Otherwise it is the same as `ask_reply`.
- `details()`: if `self.store.ask_for_message(target)` names an ask, it records `add_ask_details` and sends `asks.full_text(row)` chunked as `askd:<id>:<mid>:<i>`, lane 2, threaded to `mid`. Otherwise it keeps the existing path unchanged.
- HELP: `"admind commands: !new · !interrupt · !tail [n] · !restart <unit> · !ps · !details [full] · !asks · !answer <id> <text>"`. Task 3 appends the two approval commands.

CLI (`admind ask …`):

- `post --kind K --title T (--body-file PATH|-) [--pr URL --head SHA] [--bead ID] [--from LABEL] [--json]` prints `ask <id> posted` or the JSON reply. Exit codes: 0 posted, 1 refused (the message goes to stderr), 69 daemon unavailable.
- `get <id> [--json]` prints the status and answers. `list [--json]`. `cancel <id>`.
- `wait <id> [--timeout S] [--json]` polls `get` every 2 s.
  - Exit 0 as soon as the ask has an answer or note, or is in a terminal state (`approved`, `denied`, `stale`, `superseded`, `cancelled`, `failed`, `uncertain`).
  - Exit 3 on timeout (default 540 s).
  - `CtlUnavailable` while waiting is retried until the timeout; a restart is not an error.
- Every subcommand loads settings with `_with_settings` and talks to `state_dir / asks.ASK_SOCKET`.

Steps:

- [ ] **2.1 Fake `approve-bead`.** `tests/fakes/fake_approve_bead.py` is a script run by a `#!/bin/sh` wrapper written into `tmp_path`, like the fake claude.
  - It reads `FAKE_BTQ_DB`, a JSON file of beads with prebuilt `--json` answers, and appends its argv to `FAKE_BTQ_LOG`.
  - `--json`: it prints `db[id]["json"]`, or exits 1 if `db[id]["fail_read"]`.
  - A decision: it honours `--expect-digest` (exit 3 if it differs from `db[id]["json"]["digest"]`). It honours `db[id]["decide"]`: `"ok"` (sets status closed, decision, `approved_by`/`denied_by`, the digest field, `via`, `via_ref`, and writes the DB back), `"fail"` (stderr line, exit 1, no change), `"hang"` (sleeps 600 s), `"partial"` (writes metadata, leaves the bead open, exits 1), or `"edit-before"` (changes `digest` first, then behaves like `--expect-digest`).
  - It only reads and writes inside `tmp_path`.
- [ ] **2.2 Failing tests** in `tests/test_admind_asks.py`. Unit tests:
  - `validate` (the title bounds and a one-line title, the 80-non-space body floor, the 16,000-character body cap, the merge PR URL regex and 40-hex head, the poster label regex, the bead ID regex and a leading `-`);
  - `new_id` (alphabet, uniqueness by retry);
  - `normalise_id`;
  - `card` for question and merge (first line, the "(a local process; unverified)" label, the budget and `truncated`, no 64-hex run survives `redact`);
  - `commands.parse` for `!asks`, `!answer k7m2 line1\nline2` (rest keeps the newline), `!answer`, `!answer k7m2` (usage), and HELP containing the new commands;
  - the store: `ask_for_message` returns the ID only for a **sent** `ask:`/`askd:` row and None for pending, failed, `asknote:`, `reply:` and unknown IDs; `ask_posted_since`; `answers` ordering;
  - the generalised `CtlServer`: a 100 KiB `AskPost` round-trips on `ask.sock`; a 5,000-byte `CtlRequest` on `ctl.sock` is still refused; an `ask.sock` request over 262,144 bytes is refused; mode 0600.
  - Integration tests, via `run_with`; a sent card's message ID is read from `h.fake.sent`, which records each send's returned `message_id`. Check the fake; add that field if absent, in this step:

```python
def test_question_answered_by_reply(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("hello")                                       # join signal (D5)
        reply = await h.daemon.on_ask(asks.AskPost("question", "Which relay?", "x" * 90, poster="controller"))
        assert reply.result == "posted" and reply.ask is not None
        await h.until(lambda: any("Ask " + reply.ask.ask_id in t for t in h.texts()))
        card_mid = sent_mid(h, f"ask:{reply.ask.ask_id}:0")
        h.seq += 1
        await h.fake.push_event(h.fake.message_event("use the first one", OPERATOR_HEX, f"{h.seq:064x}",
                                                     reply_to=card_mid))
        await h.until(lambda: any("Answer recorded" in t for t in h.texts()))
        view = (await h.daemon.on_ask(asks.AskGet(reply.ask.ask_id))).ask
        assert view.status == "answered" and view.answers[0].text == "use the first one"
        assert "use the first one" not in h.log.read_text()          # never pasted to the agent
    run_with(tmp_path, scenario)
```

  Further integration tests:

  - `test_question_answered_by_bang_answer` (a multi-line answer, kept verbatim);
  - `test_reply_to_non_card_still_passes_through` (a reply to an agent message reaches the fake claude log);
  - `test_reply_to_pending_card_chunk_is_not_an_answer` (the card is still pending in the outbox when the reply arrives, so it behaves as passthrough);
  - `test_merge_requires_pr_and_head` (refused, and nothing posted);
  - `test_merge_card_shows_url_and_never_merges`;
  - `test_post_refused_while_latched`;
  - `test_limits` (21st open refused; 31st within an hour refused, using a patched clock in `asks`);
  - `test_answer_limits` (over 16,000 chars refused; 51st answer refused);
  - `test_details_on_card` (sent as `askd:` in lane 2, threaded, recorded in `ask_details`);
  - `test_cancel` (status, notice, `!answer` afterwards refused);
  - `test_asks_command_lists_open`;
  - `test_existing_details_unchanged` (a summary's `!details` still works, regression);
  - `test_cli_wait_survives_restart` (run `cli.main(["ask", "wait", id, "--timeout", "10"])` in a thread while the server is closed and reopened, then answer; exit 0).
- [ ] **2.3 Run them to see them fail:** `uv run pytest tests/test_admind_asks.py -q`.
- [ ] **2.4 Implement** `asks.py`, the store tables and methods (in `SCHEMA` with `CREATE TABLE IF NOT EXISTS`, plus `CREATE INDEX IF NOT EXISTS outbox_message_id ON outbox(message_id)`), the `ctl.py` parameters, the commands, the daemon wiring and the CLI, per the interfaces above.
- [ ] **2.5 Run the gate.** Every existing test passes unchanged, except the HELP and READY_NOTICE text assertions, which are updated in this commit.
- [ ] **2.6 Commit:** `git commit -m "admind: asks over ask.sock, cards, answers by reply or !answer, !asks"`.
- [ ] **2.7 Cross-model review** of `<BASE>..HEAD`, then fix commits.

---

### Task 3: Approval asks and `!approve` / `!deny`

Depends on Task 1's PR being merged, because its flags are this task's contract.

**Files:** `approvals.py` (new), `settings.py`, `asks.py`, `commands.py`, `daemon.py`, `tests/test_admind_approvals.py` (new), `tests/test_admind_settings.py`.

**Interfaces:**

```python
# approvals.py
READ_SECONDS, DECIDE_SECONDS, MAX_OUTPUT, MAX_READ_OUTPUT = 60, 90, 1 << 20, 8 << 20
BEAD_ID = re.compile(r"[a-z0-9]{1,16}-[a-z0-9.]{1,32}")
DIGEST = re.compile(r"[0-9a-f]{64}")

class Readout(msgspec.Struct, frozen=True):    # strict: unknown or missing fields are a decode error
    format: Literal[1]; id: str; status: str; labels: list[str]; kind_approval: bool; digest: str
    posted_digest: str | None; gaps: list[str]; design_review_valid: bool | None
    adr_revision: str | None; approvers: list[str]; decision: str | None
    approved_by: str | None; denied_by: str | None; approved_digest: str | None
    denied_digest: str | None; via: str | None; via_ref: str | None; readout: dict[str, list[str]]

class BtqError(Exception): ...          # fixed wording: "unavailable", "timed out", "bad output"

class ApproveBead:
    def __init__(self, binary: Path) -> None
    async def read(self, bead: str) -> Readout                           # --json
    async def decide(self, bead: str, *, name: str, digest: str, ref: str,
                     deny_reason: str | None) -> tuple[int, str]         # (exit status, first stderr line)

def postable(r: Readout) -> str | None          # refusal reason for posting, or None
def settle(r: Readout, *, deny: bool, name: str, digest: str, ref: str) -> Literal["recorded", "open", "other"]
```

- Both methods use `asyncio.create_subprocess_exec` with `start_new_session=True`, stdin `DEVNULL`, and the environment inherited (the unit's `PATH` finds `bd`). On a timeout they `os.killpg(proc.pid, SIGKILL)`, then wait.
- The output is read with a cap; over the cap it is `BtqError("bad output")`.
- `decide` argv: `[binary, bead, f"--as={name}", "--yes", f"--expect-digest={digest}", "--via=marmot", f"--via-ref={ref}"]`, plus `["--deny", f"--note={reason}"]`.
- `settle` returns:
  - `recorded` if the bead is closed, `decision` matches, `{approved|denied}_by == name`, `{approved|denied}_digest == digest` and `via_ref == ref`;
  - `open` if the status is open;
  - else `other`.

Settings:

- `ADMIND_KEYS` gains `"approve_bead"`. `AdmindSettings` gains `approve_bead: Path | None = None`, last.
- `resolve` requires an absolute path to an existing executable file, and otherwise raises `ConfigError("[admind] approve_bead must be an absolute path to an executable")`. The value is never echoed.

Commands:

- `!approve <bead> <digest12>` gives `Command("approve", arg=bead, rest=digest12.lower())`, with exactly two arguments, `[0-9a-fA-F]{12}`, and the bead matching `BEAD_ID`.
- `!deny <bead> <reason>` gives `Command("deny", arg=bead, rest=reason)`, the reason non-empty and at most 1,000 characters after redaction.
- HELP appends `· !approve <bead> <digest> · !deny <bead> <reason>`.

Daemon:

- **Posting** (`on_ask`, kind approval) follows spec §8 "Post an approval ask":
  - `asks.approval_card(row, readout)` builds the card as spec §6, with the budget order title, ask, description; it returns `(text, truncated)`;
  - the readout is stored as JSON in `body`.
- **Plain reply to an approval card:** `add_answer(kind="note")`, then the R13 notice.
- **Deciding** follows spec §8 "Decide", steps 1–8, as `async def decide(self, mid, cmd, target)`.
  - Every refusal is `finish(mid, "done", <spec §6 wording>, "ask")` and an `ask/refused` audit with a reason word.
  - The decision run is preceded by `authorised(mid)` and followed by the read-back. Then one transaction sets the inbound row to `done`, `set_ask`, and queues the reply.
  - `ref = "marmot:" + audit.ref_id(mid)`.
- **Startup:** `recover()` leaves `deciding` asks alone; a new `reconcile_asks()` task runs once, after the first successful `check_group` with `may_post`.
  - It re-reads each `deciding` ask. `settle` gives `approved`/`denied` for `recorded`, `open` with the restart notice, and `failed` for `other`; a read error gives `uncertain`.
  - It posts its notices threaded to the card's first sent chunk, as `asknote:<id>:reconciled:0:<i>`.
- **Supersede** on posting: `open_ask_for_bead(bead)` is set to `superseded`, with `asknote:<old>:superseded:<new>:<i>` threaded to the old card.

Steps:

- [ ] **3.1 Failing unit tests** (`tests/test_admind_approvals.py`):
  - `Readout` decoding rejects an unknown field, a missing field and `format: 2`;
  - `ApproveBead.decide` argv is exactly as above. The fake logs it; check that no element starts with `-` unless it is one of the fixed flags;
  - a timeout kills the process group (the fake `hang`; the test asserts the PID is gone);
  - the output cap;
  - `settle`: every branch, including a closed bead with a different `via_ref` (`other`);
  - `postable`: closed, not `kind:approval`, gaps, an invalid `design_review`, `posted_digest` mismatch;
  - `approval_card`: the digest12 line, the exact `!approve <bead> <digest12>` line, readout lines copied verbatim, the budget, and that `truncated` is set when the description is cut;
  - `commands.parse`: `!approve` arity, case and length; `!deny` with no reason;
  - settings: the key is optional, relative paths are refused, non-executables are refused, and the error does not contain the value.
- [ ] **3.2 Failing integration tests**, each through `run_with` with `settings_overrides={"approve_bead": <fake wrapper>, "operators": (operator("op", OPERATOR_HEX), operator("llctest", SECOND_HEX))}` and fake approvers `["op"]`:
  - `test_approve_happy_path`: post the ask, the card is sent, reply `!approve <bead> <d12>` to it. The fake log's last argv equals the expected list with `--via-ref=marmot:id:<12 hex>`. The ask is `approved`; the reply says "Approved … via Marmot"; the audit has `ask/deciding` and `ask/decided`.
  - `test_unthreaded_approve_refused` and `test_approve_as_reply_to_agent_message_refused`: the fake shows no decision run.
  - `test_wrong_digest_refused`, `test_wrong_bead_refused`.
  - `test_digest_changed_before_read`: the bead JSON digest changes between post and decide, so the ask goes `stale` and no decision run happens.
  - `test_digest_changed_inside_approve_bead`: `decide="edit-before"`, so the fake exits 3, the read-back shows open, the ask goes back to `open` and the reply quotes the stderr line.
  - `test_non_approver_operator_refused`: `llctest` replies with the correct digest and gets "not a btq approver"; no decision run.
  - `test_latched_drops_approve`: latch via the store, the guard drops it, and nothing is run.
  - `test_removed_operator`: remove `llctest` through `on_ctl`, then its `!approve` is dropped by the guard.
  - `test_removal_waits_for_decision`: `decide="hang"` with a short `DECIDE_SECONDS` patch; a concurrent `on_ctl(remove)` completes only after the decision settles (`uncertain`, or `open` per the read-back).
  - `test_truncated_needs_details`: a long description makes the card shortened. `!approve` is refused; `!details` as a reply to the card, wait until all `askd:` rows are sent; then `!approve` (as a reply to a details chunk) succeeds.
  - `test_details_by_other_operator_does_not_count`.
  - `test_plain_yes_is_a_note`: the reply "yes" leaves the ask `open`, the answer kind is `note`, and nothing is run.
  - `test_replayed_message_id`: the same `!approve` event pushed twice gives one decision run.
  - `test_closed_bead_refused_at_post` and `test_gaps_refused_at_post` (gaps returned to the poster, nothing posted).
  - `test_deny_with_reason`: the argv has `--deny` and `--note=<redacted reason>`, with a token-shaped string in the reason redacted.
  - `test_approve_bead_failure_reported`: `decide="fail"`.
  - `test_partial_write_is_reported_open`: `decide="partial"`; the read-back is open, so the ask is open and the reply says nothing was recorded.
  - `test_read_failure_uncertain`: the read-back fails, so the ask is `uncertain` with fixed wording.
  - `test_restart_while_deciding`: set the ask to `deciding` and the inbound row to `executing` in `before_store`, with the fake bead closed and recorded. At start the inbound row gets the restart notice and the ask becomes `approved` with the reconcile notice.
  - `test_supersede`: a second post for the same bead; the old card's `!approve` is refused as superseded.
  - `test_not_configured`: without `approve_bead`, posting an approval ask is refused, and question asks still work.
  - `test_answer_command_refused_for_approval`.
- [ ] **3.3 Run them to see them fail**, then **3.4 implement**, then **3.5 run the gate.**
- [ ] **3.6 Commit:** `git commit -m "admind: approval asks and !approve/!deny through approve-bead, pinned to the shown digest"`.
- [ ] **3.7 Cross-model review** of Task 3's range, with a brief asking specifically for attacks on R6–R12, then fix commits.

---

### Task 4: Runbook, configuration, deployment and live acceptance

**Files:** `docs/admind.md`, `docs/configuration.md`, `examples/config.toml`.

- [ ] **4.1 Runbook.** A new section "Asks and approvals (interim)" in `docs/admind.md`: what a card looks like, the commands, `admind ask …` with exit codes, the trust change (spec §3, verbatim), the limits, the audit records, and the files table gaining `ask.sock` and the three tables. Also state the existing gap: operator names must equal btq `approvers` names for approvals.
- [ ] **4.2 Configuration.** `approve_bead` in `docs/configuration.md` and as a commented line in `examples/config.toml` (`# approve_bead = "/path/to/beads-task-queue/bin/approve-bead"`), with the rollback note (an older admind rejects the key).
- [ ] **4.3 Gate, commit:** `git commit -m "docs: admind asks and approvals relay runbook"`. Then the cross-model review of the whole `admind-relay` range, and open the PR. Report its full URL.
- [ ] **4.4 Deployment** (the operator's and controller's actions; each step marked *go* needs the controller's explicit go, and an agent does nothing here without it). These steps use placeholders: `<RUN-CHECKOUT>` is the checkout the unit's `ExecStart` runs from, `<BTQ-LIVE>` the live beads-task-queue checkout, and `<UNIT-PATH>` the `PATH` the unit runs with (read it with `systemctl --user show heterodyne-admind -p Environment`).
  1. The operator merges both PRs in GitHub.
  2. *go*: fast-forward `<BTQ-LIVE>` (`git -C <BTQ-LIVE> pull --ff-only`). It is backward compatible, because `via` defaults to `cli`, but it is a shared tool other agents use.
  3. *go*: in `<RUN-CHECKOUT>`, `git pull --ff-only && uv sync --locked`. The running daemon is not affected until it restarts.
  4. Read-only checks:
     - the policy names of the operators who will approve equal names in the btq policy `approvers`;
     - `env -i HOME="$HOME" PATH=<UNIT-PATH> <BTQ-LIVE>/bin/approve-bead <a closed kind:approval bead> --json` prints JSON (this proves `bd` and the credentials resolve under the unit's environment).
  5. *go*: add `approve_bead = "<BTQ-LIVE>/bin/approve-bead"` to `[admind]` in the host `config.toml`, **only after step 3**; then `heterodyne config check`.
  6. *go*: `systemctl --user restart heterodyne-admind`. Touch no other unit. The admin agent's tmux session is adopted. The new tables are created at start.
  7. Smoke test: `journalctl --user -u heterodyne-admind -n 50` shows no error; `admind ask list` returns an empty list.
  - **Rollback:** remove `approve_bead` from `config.toml` first (the old code exits 78 on the unknown key), then check out the previous commit in `<RUN-CHECKOUT>`, `uv sync --locked`, and restart (*go*). The new tables are left in place and ignored.
- [ ] **4.5 Live acceptance (operator, with the phone).** Use a test workstream, and throwaway `kind:approval` beads created for it. Creating them needs the operator's go; they are closed afterwards. Tick each item:
  1. `admind ask post --kind question …` from the host: the card arrives. Reply to it from the phone; `admind ask wait <id>` returns the answer.
  2. A second question answered with `!answer <id> <text>` (not a reply), multi-line.
  3. A merge ask with a real PR URL and head: the card shows the link; nothing merges.
  4. `!asks` lists what is open; `admind ask cancel` gives a cancelled notice in the thread.
  5. An approval ask on throwaway bead A, with a description long enough to be shortened:
     - `!approve A <digest12>` sent unthreaded is refused;
     - the right command as a reply with a wrong digest is refused;
     - the reply "yes" is noted, not decided;
     - `!approve` before `!details` is refused;
     - `!details` as a reply arrives in full;
     - `!approve` as a reply to the card then succeeds.
     - On the host, `approve-bead A` shows `via: marmot` and a `via_ref` whose `id:` value appears in admind's audit.
  6. Throwaway bead B: an edit on the host after the card was posted makes `!approve` report it changed (stale), and nothing is recorded.
  7. Throwaway bead C: `!deny C <reason>` records the denial, with the reason in the bead comment.
  8. From the test operator's account (not a btq approver): `!approve` on a fresh card is refused with "not a btq approver".
  9. The audit (`audit.jsonl`) has `ask/posted`, `refused`, `deciding` and `decided` records for the above, with no npub or token.
  10. Regression: `!ps`, `!tail`, `!details` on a summary, and a normal prompt all behave as before.
- [ ] **4.6** Record the acceptance result on the acceptance bead and close Tasks 1–4 per the queue protocol.

## Self-review notes (plan author)

- Every R-rule maps to a task: Task 1 covers R5, R6, R19 and R20 in btq. Task 2 covers R1–R4, R7 (cards), R13, R14 (question/merge), R15–R18. Task 3 covers R5, R6 and R8–R12, plus R13 for approval cards and R19–R20 on the admind side.
- The one open dependency is Task 1's `Queue()` behaviour under `--json` with fake credentials (step 1.1 says how to resolve it). If `Queue()` cannot be built offline, Task 1 adds a lazily built queue for `--json`, still without network.
- ADR text is not edited by this plan. The §8 and §5.4 deltas are in the spec §10, for the r14 editor.
