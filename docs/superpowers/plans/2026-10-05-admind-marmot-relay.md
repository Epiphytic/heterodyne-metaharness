# admind Marmot relay for asks and approvals (interim) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a local process can post a question, a merge request or a btq approval request to admind's operator group. The operator answers from the phone, by replying or with `!answer`. They decide approvals with `!approve <bead> <digest12>` or `!deny <bead> <reason>`, sent as a reply to the card. admind records the decision through `approve-bead`, with `via=marmot`.

**Architecture:**

- New modules:
  - `asks.py` (pure): validation, IDs, card and details rendering, command-free logic;
  - `approvals.py`: the `approve-bead` subprocess runner, the readout schema and the read-back outcome mapping.
- New SQLite tables, created if absent.
- `ctl.sock`'s server code is factored into `ctl.JsonSocketServer`, and a second instance of it, with its own limits and request types, serves `ask.sock`.
- The daemon gains four commands, a reply-to-card branch in `handle`, an ask-socket handler and a startup reconcile.
- One change in `beads-task-queue`: `approve-bead` gets `--json`, `--expect-digest`, `--via` and `--via-ref`, and a per-bead lock with a full re-check before it writes.

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
- **Gate before each `$BTQ` commit:** `python3 -m unittest tests.test_approve_bead_relay tests.test_add_agent -v`, which is offline. Never run `tests/test_queue.py` or `discover` in this work: `test_queue.py` builds a real `Queue` and creates, updates and closes beads on the shared server.

## Decisions made in this plan (within the spec)

| # | Decision |
|---|---|
| P1 | `--json` readout lines are captured from `approve-bead`'s own `show_field`/`show_ask` with `contextlib.redirect_stdout`. That way they are byte-identical to the terminal readout, and no second renderer can drift. |
| P2 | `--json` needs no `--as`, because it only reads. With `--json`, the validity checks (closed, labels, gaps, design_review, posted digest) are reported as fields, and the exit status is 0. A `bd` failure still exits non-zero. |
| P3 | `approvals.ApproveBead` is the only place admind builds `approve-bead` argv. It always passes `--flag=value`, and has one method per call (`read`, `decide`). Tests swap the binary, never the class. |
| P4 | The socket server is factored into `ctl.JsonSocketServer`, with every request-specific part (decode type, check, refusal and failure replies, audit kind and fields, request and reply limits) passed in. `ctl.CtlServer(path, handler, audit)` builds today's `ctl.sock` from it, so its callers and tests are unchanged. The peer PID comes from `platform.peer_pid`. |
| P5 | Question and merge cards are redacted whole first, and their budget is counted on the redacted text. Approval cards must be unchanged by redaction (R21), so the two counts agree. |
| P6 | `Store.ask_for_message` joins `outbox` on `message_id` with `status='sent'` and `key LIKE 'ask:%' OR key LIKE 'askd:%'`. The ask ID is parsed from the key, so no new index table is needed. An index on `outbox(message_id)` is added (`CREATE INDEX IF NOT EXISTS`). |

## File map

| File | Task | Change |
|---|---|---|
| `$BTQ/bin/approve-bead` | 1 | `--json`, `--expect-digest`, `--via`, `--via-ref` |
| `$BTQ/tests/test_approve_bead_relay.py` | 1 | new, offline |
| `$BTQ/docs/PICKUP.md`, `$BTQ/README.md` | 1 | document the flags (one paragraph each) |
| `src/heterodyne/admind/asks.py` | 2 | new: request structs, validation, IDs, rendering |
| `src/heterodyne/admind/store.py` | 2, 3 | `asks`, `ask_answers`, `ask_details` (2); `ask_attempts` (3); the outbox index |
| `src/heterodyne/admind/ctl.py` | 2 | `JsonSocketServer`, `Peer`; `CtlServer` built on it; `request(max_reply=…)` |
| `src/heterodyne/platform.py` | 2 | `peer_pid` |
| `src/heterodyne/admind/commands.py` | 2, 3 | `asks`, `answer` (2); `approve`, `deny` (3); HELP |
| `src/heterodyne/admind/daemon.py` | 2, 3 | ask socket, card replies, the commands, reconcile |
| `src/heterodyne/admind/cli.py` | 2 | `admind ask post\|get\|wait\|list\|cancel` |
| `src/heterodyne/admind/approvals.py` | 3 | new: `ApproveBead`, `Readout`, `settle` |
| `src/heterodyne/admind/settings.py` | 3 | optional `approve_bead` |
| `tests/fakes/fake_approve_bead.py`, `tests/admind_asks_fixture.py` | 2 | new fake and shared fixture |
| `tests/fakes/fake_wn_agent.py` | 2 | record sent message IDs; `send_gate` |
| `tests/test_admind_asks.py` | 2 | unit and integration tests for questions and merges |
| `tests/test_admind_approvals.py` | 3 | unit and integration tests for approvals |
| `docs/admind.md`, `docs/configuration.md`, `examples/config.toml` | 4 | runbook, key, acceptance |
| `docs/adr/0001-workstreams-v2.md` | — | **not edited**; the §8 delta goes to the r14 editor (spec §10) |

---

### Task 1: `approve-bead` relay flags and the per-bead lock (beads-task-queue)

**Files:** `$BTQ/bin/approve-bead`, `$BTQ/tests/test_approve_bead_relay.py`, `$BTQ/docs/PICKUP.md`, `$BTQ/README.md`.

**Interface (the contract Task 3 calls):**

```
approve-bead ID --json
  stdout: one JSON object; exit 0 unless bd fails (1) or the bead lock is busy (4: stdout {"format": 1, "busy": true}):
  {"format": 1, "busy": false, "id": str, "status": str, "labels": [str], "kind_approval": bool,
   "digest": hex64|null, "posted_digest": str|null, "gaps": [str], "design_review_valid": bool|null,
   "adr_revision": str|null, "approvers": [str],
   "decision": str|null, "approved_by": str|null, "approved_digest": str|null, "denied_by": str|null,
   "denied_digest": str|null, "via": str|null, "via_ref": str|null, "decided": bool,
   "gate_valid": bool|null, "gate_reasons": [str],
   "links": [{"doc": int, "url": str}],
   "readout": {"title": [str], "description": [str], "ask": [str]}}
approve-bead ID --as=NAME --yes [--deny --note=TEXT] --expect-digest=HEX64 --via=marmot --via-ref=REF
  exit 0 decided (and, for an approval, the gate accepts it); 3 not the content expected;
  4 busy; 5 written but the content changed during the write; 1 any other refusal or bd failure.
  One line on stderr whenever the exit status is not 0.
```

- `digest` is null when `btq.ask_digest` returns None (a missing or malformed `metadata.ask`); `gaps` then says why.
- `decided` is true if any of `decision`, `approved_*`, `denied_*` or `via_ref` is present.
- `gate_valid` is null unless the bead is closed with `decision=approve`; then it is `queue.approval_valid(issue, adr_revision)`, with its stderr reasons captured into `gate_reasons`.
- `design_review_valid` is null when the bead has no `design_review`.
- `links`: for each `--doc N` ref, if `git -C <ref repo> remote get-url origin` is `https://github.com/O/R(.git)`, or the SSH form (user `git`, host `github.com`, path `O/R(.git)`), then:
  - a file ref gets `https://github.com/O/R/blob/<id>/<path>`, with the path percent-encoded by `urllib.parse.quote(path, safe="/")`, so `#`, `?`, `%` and spaces stay part of the path (r3-3);
  - a commit ref gets `/commit/<id>`;
  - a range ref gets `/compare/<a>...<b>`.

  Read `btq.Resolution` and `doc_pages` for the exact ref fields before implementing.
- `--expect-digest` must be 64 lowercase hex characters. It is compared at the first computation and again at the pre-write re-check (exit 3, "The ask is not the one you were shown (expected …12, now …12); nothing written.").
- **Lock:** every invocation that reads or decides, `--json` included, holds `fcntl.flock(LOCK_EX)` on `~/.local/state/beads-task-queue/approve-bead/<sha256(id)[:16]>.lock` from before the first `show` until after the last write. The directory is mode 0700. The wait is 60 s (polling `LOCK_NB` every 0.2 s), then exit 4 "busy". `--dry-run` and the read-only `--tree`/`--doc` paths also take it. Every `bd` child is started with the lock file descriptor inherited (`pass_fds=(lock_fd,)`), so the lock is held until the last process that could write has exited, even if `approve-bead` itself dies first (r3-1). A read-back therefore waits for any surviving writer.
- **Pre-write re-check, under the lock:**
  - re-`show` the bead;
  - refuse (exit 1, nothing written) unless it is open, still `kind:approval`, has no decision field, and `--as` is still in the policy approvers;
  - the digest equals `args.expect_digest or digest` (exit 3).
- **Post-write read:** after `close`, recompute the digest of the closed bead. If it differs from the written one, exit 5 "written, but the bead changed during the write; btq will reject this approval".
- `--via` is `cli` (default) or `marmot`. `--via-ref` needs `--via=marmot` and must match `[A-Za-z0-9:._-]{1,80}`; anything else is a usage error (exit 2 from argparse, before the lock or any `bd` call).
- Fields written: `via=<via>` and, if given, `via_ref=<ref>`. Comment: `Approved by NAME at T via approve-bead (Marmot, admind message REF); context_digest=…`.
- `bd -C` uses `queue.repo` (the resolved `BTQ_REPO`), not the module constant `btq.REPO`, so the two can't disagree. The default is the same path.

Steps:

- [ ] **1.1 Failing tests.** Create `tests/test_approve_bead_relay.py`:

```python
"""approve-bead's relay flags, offline: a fake bd serves beads from a JSON file in a temp dir.
Nothing here may reach a Dolt server or the real ~/.config/beads-task-queue (asserted in setUp)."""
import json, os, subprocess, sys, tempfile, textwrap, threading, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE_BD = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time
    db, log, repo = os.environ["FAKE_BD_DB"], os.environ["FAKE_BD_LOG"], os.environ["FAKE_BD_REPO"]
    argv = [a for a in sys.argv[1:] if a != "--json"]
    assert argv[:2] == ["-C", repo], argv            # finding r1-14: the resolved queue repo
    args = argv[4:] if argv[2] == "--actor" else argv[2:]
    with open(log, "a") as f:
        f.write(json.dumps(args) + "\\n")
    beads = json.load(open(db))
    hook = os.environ.get("FAKE_BD_ON")              # e.g. "show:2:edit" = edit the description on the 2nd show
    cmd = args[0]
    if cmd == "show":
        count = len([l for l in open(log) if l.startswith('["show"')])
        if hook and hook.startswith(f"show:{count}:edit"):
            beads[args[1]]["description"] += " (edited)"; json.dump(beads, open(db, "w"))
        print(json.dumps([beads[args[1]]]))
    elif cmd == "comments" and len(args) == 2:
        print("[]")
    elif cmd == "update":
        bead, rest = beads[args[1]], args[2:]
        while rest:
            flag, value, rest = rest[0], rest[1], rest[2:]
            if flag == "--set-metadata":
                k, v = value.split("=", 1); bead.setdefault("metadata", {})[k] = v
        if hook == "update:edit":
            bead["description"] += " (raced)"
        json.dump(beads, open(db, "w")); print("{}")
    elif cmd == "comments":
        print("{}")
    elif cmd == "close":
        beads[args[1]]["status"] = "closed"; json.dump(beads, open(db, "w")); print("{}")
    else:
        print("unexpected bd call", file=sys.stderr); sys.exit(1)
''')
```

  The fixture (`setUp`):

  - makes a temp root with `home/`, `config/` (`credentials.json` with `{"bel": "x"}`, `policy.json` with approvers `["alice", "bob"]`), `repo/` (a `git init` repo holding `docs/adr/0001-x.md`, committed, with remote `origin` set to `https://github.com/o/r.git`), `bin/bd` (the fake) and `db.json`;
  - builds the env from scratch: `HOME`, `PATH=<tmp>/bin:/usr/bin:/bin`, `BTQ_CONFIG_DIR`, `BTQ_POLICY`, `BTQ_REPO`, `FAKE_BD_*`. It asserts that no value points under the real home;
  - builds `approval-1`: open, labels `["kind:approval"]`, a complete ask (`effect`, `excludes`, `why`, `risks`, and `refs` with the pinned ADR file at the commit), `adr_revision`, and a valid `design_review`.

  `btq.Queue()` performs no connectivity probe (it only reads files), so no lazy-queue change is needed.

  Tests (each runs `bin/approve-bead` as a subprocess with that env; helpers `run(*args)` and `writes()`, the logged `update`/`comments add`/`close` calls):

  - `test_json_is_read_only_and_complete`: the keys equal the interface set; `digest` equals `btq.ask_digest` computed in-process; `readout["ask"]` equals what `show_ask` prints; `links` holds the blob URL; `writes()` is empty.
  - `test_json_reports_checks_instead_of_exiting`: a closed bead, a bead without `kind:approval`, and a bead with a gap (empty `why`) each exit 0 with the fields set.
  - `test_json_malformed_ask`: a missing `metadata.ask` gives `digest: null` and non-empty `gaps`; a string `metadata` does the same.
  - `test_expect_digest_mismatch_writes_nothing`: `--expect-digest=` + `"0"*64` exits 3, and nothing is written.
  - `test_expect_digest_recheck`: `FAKE_BD_ON=show:2:edit` gives exit 3 and no writes.
  - `test_recheck_refuses_existing_decision`: a second decision after the first closed the bead exits 1 with no second write. So does a bead whose label was removed between reads (a hook variant).
  - `test_lock_serialises`: hold the lock file with `flock` from the test, start a decision in a thread, check it has not written after 1 s, release, check it writes. With `APPROVE_BEAD_LOCK_SECONDS=1` (an env override the script reads; default 60), a held lock gives exit 4, and `--json` gives the busy reply.
  - `test_post_write_race_detected`: `FAKE_BD_ON=update:edit` gives exit 5, and `gate_valid` on a following `--json` is false.
  - `test_marmot_decision_records_via`: `--as=alice --yes --expect-digest=<d> --via=marmot --via-ref=marmot:id:0123456789ab` exits 0. The metadata has `via=marmot`, `via_ref=…` and `approved_digest=<d>`; the comment names the ref; `--json` afterwards gives `gate_valid: true`, `decided: true`.
  - `test_deny_via_marmot`: `--deny --note=too broad` writes `decision=deny` and `via=marmot`, and `gate_valid` is null.
  - `test_default_via_is_cli`: no `--via` gives `via=cli` and no `via_ref`.
  - `test_via_ref_requires_marmot_and_pattern`: `--via-ref=x` without `--via=marmot`, and `--via=marmot --via-ref='a b'`, exit 2 with an empty `bd` log.
  - `test_via_ref_is_not_a_digest_key`: `'via_ref'.endswith('_digest')` is False (a rename can't slip past `approval_valid`).
  - `test_lock_outlives_parent`: the fake bd, when asked to `update`, forks a grandchild that keeps its inherited fds and sleeps 2 s, and `approve-bead` is killed with SIGKILL right after the `update` starts. A `--json` read started at once with `APPROVE_BEAD_LOCK_SECONDS=1` gives the busy reply; one started after the grandchild exits succeeds.
  - `test_links_forms`: an SSH remote gives the same URL; a file named `docs/a#b?c%d e.md` gives `…/blob/<id>/docs/a%23b%3Fc%25d%20e.md`; a non-GitHub remote gives no link.
- [ ] **1.2 Run them to see them fail:** `python3 -m unittest tests.test_approve_bead_relay -v` gives errors on unknown arguments.
- [ ] **1.3 Implement** in `bin/approve-bead`:
  - add the arguments and validate them right after `parse_args` with `parser.error`;
  - add `bead_lock(id)` as a context manager around everything after `Queue()` construction;
  - with `--json`, skip the `--as` check, compute as now, capture `show_field`/`show_ask` with `redirect_stdout(io.StringIO())`, capture `approval_valid`'s stderr with `redirect_stderr`, print the object and return;
  - add the digest checks, the widened pre-write re-check, `via`/`via_ref`, the comment, the post-write read, and `bd -C queue.repo`.
- [ ] **1.4 Run the offline gate:** `python3 -m unittest tests.test_approve_bead_relay tests.test_add_agent -v`. Do **not** run `tests/test_queue.py`: it constructs a real `Queue` against the shared server and writes to it.
- [ ] **1.5 Docs:** one paragraph in `docs/PICKUP.md` (under approvals) and `README.md`: the flags exist for admind's Marmot relay; every decision takes a per-bead lock; `via` is provenance only and is not checked by `approval_valid`.
- [ ] **1.6 Commit:** `git add bin/approve-bead tests/test_approve_bead_relay.py docs/PICKUP.md README.md && git commit -m "approve-bead: --json readout, --expect-digest, --via/--via-ref and a per-bead lock for the admind Marmot relay"`.
- [ ] **1.7 Cross-model review**, fix, then open the PR (`gh pr create`). Report the full PR URL to the controller for the operator's merge.

---

### Task 2: Asks: store, socket, CLI, cards, answers

**Files:** `asks.py` (new), `store.py`, `ctl.py`, `platform.py`, `commands.py`, `daemon.py`, `cli.py`, `tests/fakes/fake_approve_bead.py` (new), `tests/fakes/fake_wn_agent.py` (record each send's message ID), `tests/test_admind_asks.py` (new).

**Interfaces:**

```python
# ctl.py: the shared server (finding r1-8). CtlServer keeps its name, behaviour, replies and audit records.
class Peer(NamedTuple):
    pid: int | None

class JsonSocketServer[Req, Rep: msgspec.Struct]:
    def __init__(self, path: Path, handler: Callable[[Req, Peer], Awaitable[Rep]], audit: Audit, *,
                 kind: str,                                   # audit record kind: "ctl" or "ask"
                 max_request: int, max_reply: int,
                 request_type: type[Req] | UnionType,
                 check: Callable[[Req], str | None],          # a refusal reason, or None
                 refused: Callable[[str], Rep],               # the reply for a refusal or a malformed request
                 failed: Callable[[], Rep],                   # the reply when the handler raises
                 describe: Callable[[Req], dict[str, object]]) -> None   # audit fields for a request
    async def start(self) -> None
    async def close(self) -> None

def CtlServer(path: Path, handler: Callable[[CtlRequest], Awaitable[CtlReply]], audit: Audit) -> JsonSocketServer:
    """Exactly today's ctl.sock: kind "ctl", 4096 bytes, CtlRequest, the existing check, refusal and
    failure wording, and audit fields op/name. The handler ignores the peer."""

async def request(path: Path, req: msgspec.Struct, *, timeout: float = 300, reply_type: type = CtlReply,
                  max_reply: int = 65_536) -> Any      # open_unix_connection(limit=max_reply + 2)
```

  - A reply over `max_reply` is replaced by `failed()`, and audited as `reply-too-large`.
  - The existing `test_admind_r13_ctl.py` must pass unchanged.

```python
# platform.py (the only module allowed to read sys.platform)
def peer_pid(sock: socket.socket) -> int | None     # SO_PEERCRED on Linux, LOCAL_PEERPID on macOS, else None
```

```python
# asks.py
ASK_SOCKET = "ask.sock"
MAX_REQUEST, MAX_REPLY = 262_144, 1_048_576
ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
MAX_OPEN, MAX_PER_HOUR, MAX_IN_FLIGHT = 20, 30, 2
MAX_ANSWER, MAX_ANSWERS, MAX_ANSWER_TOTAL = 16_000, 50, 64_000
CARD_LINES, CARD_CHARS = 40, 3_500
ACTIVE = ("open", "answered", "deciding", "uncertain")      # counted by MAX_OPEN; blocked is terminal (r2-4)
AskKind = Literal["question", "merge", "approval"]

class AskPost(msgspec.Struct, frozen=True, tag="post", forbid_unknown_fields=True):
    kind: AskKind; title: str = ""; body: str = ""; pr_url: str | None = None
    head_sha: str | None = None; bead: str | None = None; poster: str = "local"
class AskGet(msgspec.Struct, frozen=True, tag="get", forbid_unknown_fields=True):    ask_id: str
class AskList(msgspec.Struct, frozen=True, tag="list", forbid_unknown_fields=True):  pass
class AskCancel(msgspec.Struct, frozen=True, tag="cancel", forbid_unknown_fields=True): ask_id: str
AskRequest = AskPost | AskGet | AskList | AskCancel

class AnswerView(msgspec.Struct, frozen=True): kind: str; operator: str; text: str; at: str
class AskSummary(msgspec.Struct, frozen=True):
    ask_id: str; kind: str; status: str; title: str; bead: str | None; digest12: str | None
    delivered: bool; answer_count: int; outcome: str | None; decided_by: str | None
class AskView(msgspec.Struct, frozen=True):
    summary: AskSummary; answers: list[AnswerView]
class AskReply(msgspec.Struct, frozen=True):
    result: Literal["posted", "ok", "refused", "failed"]; message: str
    ask: AskView | None = None; asks: list[AskSummary] | None = None

def check(req: AskRequest) -> str | None: ...        # R4, R16, R19 for posts; the ID form for the others
def refused(message: str) -> AskReply: ...
def failed() -> AskReply: ...                         # "admind hit an internal error; see the audit log."
def describe(req: AskRequest) -> dict[str, object]: ...   # op, kind, ask_id, lengths; never text
def new_id(taken: Callable[[str], bool]) -> str: ...
def normalise_id(text: str) -> str | None: ...       # lower-cased, alphabet-checked, 4 characters
def question_card(row: AskRow) -> tuple[str, bool]: ...   # (redacted text, truncated); budget counted after redaction
def full_text(row: AskRow) -> str: ...               # what !details sends, redacted
def list_line(row: AskRow, now: datetime) -> str: ...
```

```python
# store.py (new methods; writes join the caller's transaction)
@dataclass(frozen=True)
class AskRow:
    ask_id: str; kind: str; poster: str; title: str; body: str; pr_url: str | None
    head_sha: str | None; bead: str | None; digest: str | None; truncated: bool; card_parts: int
    status: str; outcome: str | None; decided_by: str | None; created_at: str; updated_at: str
def insert_ask(self, row: AskRow, peer_pid: int | None) -> None
def ask(self, ask_id: str) -> AskRow | None
def asks_with_status(self, *statuses: str) -> list[AskRow]
def recent_asks(self, limit: int) -> list[AskRow]                        # terminal ones, newest first
def ask_posted_since(self, iso: str) -> int
def set_ask(self, ask_id: str, status: str, *, outcome: str | None = None, decided_by: str | None = None) -> None
def add_answer(self, ask_id: str, kind: str, operator: str, message_id: str, text: str) -> None
def answers(self, ask_id: str) -> list[AnswerRow]
def answer_totals(self, ask_id: str) -> tuple[int, int]                   # (count, characters)
def ask_for_message(self, message_id: str | None) -> str | None          # P6, R7
def card_delivered(self, ask_id: str) -> bool                             # every ask:<id>:0..card_parts-1 sent
def first_card_message(self, ask_id: str) -> str | None                  # for threading notices
def add_ask_details(self, ask_id: str, operator: str, message_id: str, parts: int) -> None
def details_delivered(self, ask_id: str, operator: str) -> bool          # every part of one of the operator's requests sent
def ask_for_bead(self, bead: str, *statuses: str) -> AskRow | None
```

```python
# commands.py
CommandName = Literal["new", "interrupt", "tail", "restart", "ps", "details", "asks", "answer"]
@dataclass(frozen=True)
class Command:
    name: CommandName; arg: str | None = None; lines: int = TAIL_DEFAULT; rest: str | None = None
```

  - `!answer <id> <text>` gives `arg=id` and `rest=` the text after the ID: internal newlines kept, outer whitespace stripped. An empty text is a usage error.
  - Parsing `!answer` uses `text[1:].split(maxsplit=2)`, not the whitespace split.

Daemon changes:

- `run()` starts a second server, `JsonSocketServer(state_dir / asks.ASK_SOCKET, self.on_ask, self.audit, kind="ask", max_request=asks.MAX_REQUEST, max_reply=asks.MAX_REPLY, request_type=asks.AskRequest, check=asks.check, refused=asks.refused, failed=asks.failed, describe=asks.describe)`, next to `ctl.sock`, and closes it in `finally`.
- `on_ask(req, peer)` handles the four requests.
- `post`:
  1. Refuse if `self.posts_in_flight >= MAX_IN_FLIGHT`. Otherwise increment the counter (decrement in `finally`) and take `ask_post_lock` (R22).
  2. Refuse if latched (R17) or over a limit (R15). For an `approval` ask, Task 2 refuses with "approval asks are not available yet"; Task 3 replaces this.
  3. In one transaction: insert the ask with `card_parts`; queue the card's chunks as `ask:<id>:<i>` (lane 1, unthreaded).
  4. Audit `ask/posted`, with `peer.pid`.
- `get` returns `AskView`; `list` returns the active summaries plus `recent_asks(20)`.
- `cancel` of an `open` or `answered` ask sets `cancelled` and threads `asknote:<id>:cancelled:0:<i>` to `first_card_message` (unthreaded if none was sent).
- `handle(mid, text, target)`, after the control-character check and before `commands.parse`:

```python
ask_id = self.store.ask_for_message(target)
if ask_id is not None and not text.startswith("!"):
    await self.ask_reply(mid, ask_id, text)        # R13/R14: never pasted to the agent
    return
```

- `ask_reply` sets the inbound row to `executing`, then checks `authorised(mid)` (else `deny`). For a question or merge ask:
  - refuse when the ask is `cancelled`/`superseded`, when the text is over `MAX_ANSWER`, or when the count or the total would pass `MAX_ANSWERS`/`MAX_ANSWER_TOTAL` (fixed wording each);
  - otherwise, in one transaction: add an answer (status `answered`), then `finish(mid, "done", <spec §6 wording>, "ask")`.
  - Approval asks: Task 3.
- `!asks` lists the active asks (`finish`). `!answer` resolves the ID; an unknown ID gets `No ask <id>.` and an approval ask the R13 hint. Otherwise it is the same as `ask_reply`.
- `details()`: if `self.store.ask_for_message(target)` names an ask, send `asks.full_text(row)`:
  - in one transaction, chunk it as `askd:<id>:<mid>:<i>` (lane 2, threaded to `mid`) and call `add_ask_details(…, parts=n)`;
  - audit `ask/details`;
  - otherwise the existing path is unchanged.
- HELP: `"admind commands: !new · !interrupt · !tail [n] · !restart <unit> · !ps · !details [full] · !asks · !answer <id> <text>"`. Task 3 appends the two approval commands.

CLI (`admind ask …`; all subcommands call `ctl.request(…, reply_type=asks.AskReply, max_reply=asks.MAX_REPLY)`):

- `post --kind K --title T (--body-file PATH|-) [--pr URL --head SHA] [--bead ID] [--from LABEL] [--json]` prints `ask <id> posted` or the JSON reply. Exit codes: 0 posted, 1 refused (the message goes to stderr), 69 daemon unavailable.
- `get <id> [--json]` prints the status and answers. `list [--json]`. `cancel <id>`.
- `wait <id> [--timeout S] [--json]` polls `get` every 2 s.
  - Exit 0 as soon as the ask has an answer or note, or is in a terminal state (`approved`, `denied`, `stale`, `superseded`, `cancelled`, `blocked`).
  - Exit 3 on timeout (default 540 s).
  - `CtlUnavailable` is retried until the timeout; a restart is not an error.

Steps:

- [ ] **2.1 Fakes.**
  - `FakeWnAgent` changes:
    - it appends the message ID it returns to each `sent` record, as `req["_message_id"]`; existing tests ignore the field;
    - it gains `send_gate: asyncio.Event | None`, like `info_gate`: while it is set to an unset Event, `send_final` waits.
  - `tests/fakes/fake_approve_bead.py` is a script run by a `#!/bin/sh` wrapper written into `tmp_path`, like the fake claude. It implements Task 1's interface from a JSON DB (`FAKE_BTQ_DB`) and appends its argv to `FAKE_BTQ_LOG`.
  - `--json` prints the bead's stored readout object, exits 1 if `fail_read`, and gives the busy reply if `busy`.
  - A decision: honours `--expect-digest` (exit 3), `busy` (exit 4), and the bead's `decide` mode:
    - `"ok"`: closes the bead and sets `decision`, `*_by`, `*_digest`, `via`, `via_ref`, `decided`, and `gate_valid` (from the bead's `gate` field, default true);
    - `"fail"`: a stderr line, exit 1, no change;
    - `"hang"`: sleeps 600 s;
    - `"orphan"`: forks a grandchild (same process group) that sleeps 1 s and then writes the decision, and exits 1 at once (used in Task 3, r3-1);
    - `"partial"`: writes the decision fields, leaves the bead open, exits 1;
    - `"foreign"`: closes it with `approved_by` set to another name;
    - `"edit-before"`: changes `digest` first, then behaves as with `--expect-digest`.
  - It only touches `tmp_path`. Shared helpers in `tests/admind_asks_fixture.py`:
    - `approve_bead_wrapper(tmp_path)`;
    - `bead_db(tmp_path, **beads)`;
    - `decisions(h)`: the logged argv lists that are decisions, selected by `--yes` being present, not by position (finding r1-12);
    - `sent_mid(h, key)`;
    - `two_operators(h)`: `fake.member_count = 3`, `expected_members = 3`, `group_operators = both`, as `test_admind_r13_operators.two_in_group`.
- [ ] **2.2 Failing tests** in `tests/test_admind_asks.py`. Unit tests:
  - `asks.check`: the title bounds and a one-line title, the 80-non-space body floor, the 16,000-character body cap, the merge PR URL regex and 40-hex head, the poster label regex, the bead ID regex and a leading `-`, a malformed ID;
  - `new_id` (alphabet, uniqueness by retry) and `normalise_id`;
  - `question_card` for question and merge: the first line, the "(a local process; unverified)" label, the budget counted after redaction, `truncated`, a token in the body redacted;
  - `commands.parse` for `!asks`, `!answer k7m2 line1\nline2` (`rest` keeps the newline), `!answer`, and `!answer k7m2` (usage); HELP contains the new commands;
  - the store:
    - `ask_for_message` gives the ID only for a **sent** `ask:`/`askd:` row, and None for pending, failed, `asknote:`, `reply:` and unknown IDs;
    - `card_delivered` is false when the last part is pending or failed, and when `card_parts` rows are missing;
    - `details_delivered` likewise;
    - `ask_posted_since`, `answer_totals`;
  - `JsonSocketServer`:
    - a 100 KiB `AskPost` round-trips on `ask.sock`; an `ask.sock` request over 262,144 bytes is refused;
    - a malformed or unknown-tag request gets `asks.refused`, and a raising handler gets `asks.failed`, audited as kind `ask`;
    - a reply over `max_reply` becomes `failed`; mode 0600;
    - `test_admind_r13_ctl.py` is untouched and passes.
- Integration tests, via `run_with`:

```python
def test_question_answered_by_reply(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("hello")                                       # join signal (D5)
        reply = await h.daemon.on_ask(asks.AskPost("question", "Which relay?", "x" * 90, poster="controller"),
                                      ctl.Peer(None))
        assert reply.result == "posted" and reply.ask is not None
        ask_id = reply.ask.summary.ask_id
        await h.until(lambda: sent_mid(h, f"ask:{ask_id}:0") is not None)
        h.seq += 1
        await h.fake.push_event(h.fake.message_event("use the first one", OPERATOR_HEX, f"{h.seq:064x}",
                                                     reply_to=sent_mid(h, f"ask:{ask_id}:0")))
        await h.until(lambda: any("Answer recorded" in t for t in h.texts()))
        view = (await h.daemon.on_ask(asks.AskGet(ask_id), ctl.Peer(None))).ask
        assert view.summary.status == "answered" and view.answers[0].text == "use the first one"
        assert "use the first one" not in h.log.read_text()          # never pasted to the agent
    run_with(tmp_path, scenario)
```

  Further integration tests:

  - `test_question_answered_by_bang_answer` (multi-line, kept verbatim);
  - `test_reply_to_non_card_still_passes_through` (a reply to an agent message reaches the fake claude log);
  - `test_card_delivery_tracking` (hold the outbox with `send_gate` after the first chunk of a two-chunk card. A reply to chunk 0 is an answer, because that row is sent, but `card_delivered` stays false until the gate opens);
  - `test_merge_requires_pr_and_head` (refused, nothing posted);
  - `test_merge_card_shows_url_and_never_merges`;
  - `test_post_refused_while_latched`;
  - `test_limits` (the 21st active ask refused; the 31st within an hour refused, with a patched clock in `asks`). Concurrency limits need an await inside a post, so they are tested in Task 3, with approval posts;
  - `test_answer_limits` (over 16,000 characters refused; the 51st answer refused; the total over 64,000 refused);
  - `test_details_on_card` (`askd:` in lane 2, threaded, `ask_details.parts` recorded);
  - `test_cancel` (status, notice, `!answer` afterwards refused);
  - `test_asks_command_lists_non_terminal`;
  - `test_large_get_and_list` (an ask with 64,000 characters of answers; `get` through the real socket and client; `list` with 20 open and 20 recent asks, under 1 MiB);
  - `test_existing_details_unchanged` (a summary's `!details` still works);
  - `test_cli_wait_survives_restart` (run `cli.main(["ask", "wait", id, "--timeout", "10"])` in a thread while the server is closed and reopened, then answer; exit 0).
- [ ] **2.3 Run them to see them fail:** `uv run pytest tests/test_admind_asks.py -q`.
- [ ] **2.4 Implement**, per the interfaces above:
  - `asks.py`;
  - the store tables, the index and the methods (in `SCHEMA`, `CREATE … IF NOT EXISTS`);
  - `ctl.JsonSocketServer`, with `CtlServer` rebuilt on it;
  - `platform.peer_pid`;
  - the commands, the daemon wiring and the CLI.
- [ ] **2.5 Run the gate.** Every existing test passes unchanged, except the HELP and READY_NOTICE text assertions, which are updated in this commit.
- [ ] **2.6 Commit:** `git commit -m "admind: asks over ask.sock, cards, answers by reply or !answer, !asks"`.
- [ ] **2.7 Cross-model review** of `<BASE>..HEAD`, then fix commits.

---

### Task 3: Approval asks and `!approve` / `!deny`

Depends on Task 1's PR being merged, because its interface is this task's contract.

**Files:** `approvals.py` (new), `settings.py`, `asks.py`, `store.py` (`ask_attempts`), `commands.py`, `daemon.py`, `tests/test_admind_approvals.py` (new), `tests/test_admind_settings.py`.

**Interfaces:**

```python
# approvals.py
READ_SECONDS, DECIDE_SECONDS, MAX_OUTPUT, MAX_READ_OUTPUT = 60, 90, 1 << 20, 8 << 20
BEAD_ID = re.compile(r"[a-z0-9]{1,16}-[a-z0-9.]{1,32}")
DIGEST = re.compile(r"[0-9a-f]{64}")
PINNED_DIGEST_LINE = re.compile(r"^(\s*pinned digest \(recomputed\): )([0-9a-f]{12})[0-9a-f]{52}$")

class Link(msgspec.Struct, frozen=True, forbid_unknown_fields=True): doc: int; url: str
class Readout(msgspec.Struct, frozen=True, forbid_unknown_fields=True):   # Task 1's --json, field for field
    format: Literal[1]; busy: bool; id: str; status: str; labels: list[str]; kind_approval: bool
    digest: str | None; posted_digest: str | None; gaps: list[str]; design_review_valid: bool | None
    adr_revision: str | None; approvers: list[str]; decision: str | None
    approved_by: str | None; approved_digest: str | None; denied_by: str | None; denied_digest: str | None
    via: str | None; via_ref: str | None; decided: bool; gate_valid: bool | None; gate_reasons: list[str]
    links: list[Link]; readout: dict[str, list[str]]

class Busy(msgspec.Struct, frozen=True): format: Literal[1]; busy: Literal[True]

class BtqError(Exception): ...          # fixed wording: "unavailable", "timed out", "bad output"

@dataclass(frozen=True)
class Attempt:
    ask_id: str; message_id: str; action: Literal["approve", "deny"]; operator: str; ref: str
    digest: str; note: str | None

class ApproveBead:
    def __init__(self, binary: Path) -> None
    async def read(self, bead: str) -> Readout | Busy                     # --json
    async def decide(self, a: Attempt, bead: str) -> tuple[int, str]     # (exit status, first stderr line)

def postable(r: Readout) -> str | None     # refusal reason for posting (spec §8 step 2), or None
def settle(r: Readout, a: Attempt) -> Literal["recorded", "untouched", "blocked"]
```

- **Running a child** (both methods): `asyncio.create_subprocess_exec` with `start_new_session=True`, stdin `DEVNULL`, and the environment inherited (the unit's `PATH` finds `bd`). Output is read with a cap; over the cap it is `BtqError("bad output")`.
- **Reaping** (R23): the child runs inside `try/finally`. On every exit path, a normal exit included (a timeout, `CancelledError`, an overflow, any exception, or the child's own exit), admind calls `os.killpg(proc.pid, SIGKILL)` unconditionally, ignoring `ProcessLookupError`, and then `await proc.wait()`, both shielded from cancellation, before settling or re-raising. It must not skip the kill when `proc.returncode` is already set: the parent may have exited while a descendant (`bd`) in its group is still running (r3-1). Reusing the group ID is not a risk: while any member of the group lives, Linux does not hand its ID out as a new PID, and once the group is empty the kill only raises `ProcessLookupError`.
- **Decision argv:** `[binary, bead, f"--as={a.operator}", "--yes", f"--expect-digest={a.digest}", "--via=marmot", f"--via-ref={a.ref}"]`, plus `["--deny", f"--note={a.note}"]` for a deny.
- **`settle`:**
  - `recorded`: the bead is closed, `decision` equals the action, `{approved|denied}_by == a.operator`, `{approved|denied}_digest == a.digest`, and `via_ref == a.ref`;
  - `untouched`: open, and `decided` is false;
  - `blocked`: anything else.
  - A `Busy` read-back is retried 3 times, 20 s apart; if still busy, the outcome is `uncertain` (spec §7).

Card rendering (`asks.py`):

- `approval_card(row, r: Readout, chunk_chars: int) -> ApprovalCard | None`.
  - `ApprovalCard(card_chunks: list[str], details_chunks: list[str], truncated: bool)`; None when R21 fails.
  - Digest lines are rewritten with `PINNED_DIGEST_LINE` to `\1\2…`. Links are appended under their refs.
  - The budget is spent on title, ask, description; the decision lines are appended after it.
  - The chunks are `chunk.split(redact(text), chunk_chars)`, exactly what `Admind.reply` would queue. R21 holds when `redact(text) == text` for both texts and `redact(c) == c` for every chunk.
  - The daemon queues these chunks as they are, with `post` per chunk; it never re-splits them. The details chunks are stored in `body` with the readout, so a later `!details` sends the checked chunks, not a fresh rendering (r2-1).

Settings:

- `ADMIND_KEYS` gains `"approve_bead"`, and `AdmindSettings` gains `approve_bead: Path | None = None`, last.
- `resolve` requires an absolute path to an existing executable file, and otherwise raises `ConfigError("[admind] approve_bead must be an absolute path to an executable")`. The value is never echoed.

Store: the `ask_attempts` table (spec §5), with these methods:

- `begin_attempt(a) -> bool`: one transaction. It sets the ask `open → deciding` and `asks.attempt = a.message_id`, and inserts the row; it returns False, changing nothing, if the ask is not `open`.
- `current_attempt(ask_id) -> Attempt | None`: the row named by `asks.attempt`.
- `close_attempt(ask_id, message_id, *, expect_status, settled, exit_status, new_status, outcome, decided_by) -> bool`: one compare-and-set transaction. It does nothing and returns False unless the ask's status is `expect_status` and its `attempt` is `message_id`. Otherwise it sets `settled` and `exit_status`, and the ask's status, outcome and `decided_by`. It clears `asks.attempt` unless `settled == "uncertain"`. The caller queues its reply or notice inside the same transaction.
- `recovery_snapshot() -> list[tuple[str, str, str]]`: (ask ID, attempt message ID, status) for every `deciding`/`uncertain` ask. The reconcile passes that status as `expect_status` (r3-2).

Commands:

- `!approve <bead> <digest12>` gives `Command("approve", arg=bead, rest=digest12.lower())`. It takes exactly two arguments, the digest matching `[0-9a-fA-F]{12}` and the bead matching `BEAD_ID`.
- `!deny <bead> <reason>` gives `Command("deny", arg=bead, rest=reason)`. The reason is non-empty and at most 1,000 characters after redaction.
- HELP appends `· !approve <bead> <digest> · !deny <bead> <reason>`.

Daemon:

- **Posting** (`on_ask`, kind approval) follows spec §8 "Post an approval ask", steps 1–5, under `ask_post_lock` with the in-flight counter. The readout is stored as JSON in `body`.
- **Plain reply to an approval card:** `add_answer(kind="note")`, then the R13 notice.
- **Deciding** follows spec §8 "Decide", steps 1–8, as `async def decide(self, mid, cmd, target)`.
  - Every refusal is `finish(mid, "done", <spec §6 wording>, "ask")` plus an `ask/refused` audit with a reason word.
  - Step 4 is `begin_attempt`. Every later exit (stale, each refusal, the authorisation failure, the settlement) goes through `close_attempt` with `expect_status="deciding"`, so no attempt is ever left unresolved by a refusal (r2-2).
  - Step 6 is `if not self.authorised(mid)` immediately followed by `await approve_bead.decide(…)`, with no other await in between.
  - `ref = "marmot:" + audit.ref_id(mid)`.
  - `latched_during = self.latched()` is computed after the run.
- **Startup reconcile** (R26):
  - `recover()` leaves `deciding` asks alone.
  - In `run()`, after `recover()` and before the `TaskGroup` starts any loop, `snapshot = store.recovery_snapshot()`.
  - A `reconcile` task in the group handles each (ask, attempt, status) in the snapshot under `async with self.work_lock`:
    - read back;
    - `close_attempt(…, expect_status=<the status in the snapshot>)`, mapping `recorded` to `approved`/`denied`, `untouched` to `open` (with the restart notice), `blocked` to `blocked`, and a read error or busy to `uncertain` (the attempt is kept);
    - queue its notice threaded to `first_card_message`, as `asknote:<id>:reconciled:<n>:<i>`, in the same transaction; it is sent once admind may post.
  - `!asks` runs the same routine, already in the worker under `work_lock`, for the current `uncertain` asks before listing.
- **Supersede** on posting: an `open`/`answered` ask for the bead becomes `superseded`, with `asknote:<old>:superseded:<new>:<i>` threaded to the old card. A `deciding`/`uncertain` ask for the bead makes the post refused (R22); a `blocked` bead is refused by `postable`, because it has decision fields.

Steps:

- [ ] **3.1 Failing unit tests** (`tests/test_admind_approvals.py`):
  - `Readout` decoding: rejects an unknown field, a missing field and `format: 2`; accepts `digest: null`; decodes `Busy`.
  - `ApproveBead.decide`: the argv is exactly as above (the fake logs it).
  - Reaping:
    - a timeout kills the process group (the fake `hang`; the test asserts the PID is gone);
    - cancelling the awaiting task also kills and reaps it;
    - an output overflow kills and reaps it;
    - `test_descendant_killed_after_parent_exit`: the fake (`decide="orphan"`) forks a grandchild in its process group that sleeps 1 s and then writes the decision into the fake bead state, and the fake exits 1 at once. After `decide` returns, the grandchild's PID is gone, and 2 s later the fake bead state is still undecided (r3-1).
  - `settle`: every branch, including a closed bead with a different `via_ref`, a foreign `approved_by`, and open with `decided: true`.
  - `postable`: closed, not `kind:approval`, gaps, `digest: null`, an invalid `design_review`, a `posted_digest` mismatch, `decided: true`.
  - `approval_card`:
    - the digest12 line, and the exact `!approve <bead> <digest12>` line, after the budget;
    - readout lines copied verbatim, except the digest lines, which are cut to 12 hex digits;
    - links;
    - `truncated` set when the description is cut;
    - None for a readout line holding a token-shaped string, a 64-hex run outside a digest line, or `-----BEGIN PRIVATE KEY-----` with no END;
    - None for a text that survives redaction whole but has a chunk that does not. Build it by searching `chunk_chars` and the padding so that a chunk boundary falls inside a value that one of `SECRET_VALUES` matches only once it is cut. Keep the found example as a fixed fixture;
    - the chunks returned are exactly those the daemon queues (the integration test compares the outbox rows with them).
  - `commands.parse`: `!approve` arity, case and length; `!deny` with no reason.
  - Settings: the key is optional; relative paths and non-executables are refused; the error does not contain the value.
- [ ] **3.2 Failing integration tests.** Each runs through `run_with` with:
  - `settings_overrides={"approve_bead": approve_bead_wrapper(tmp_path), "operators": (operator("op", OPERATOR_HEX), operator("llctest", SECOND_HEX))}`;
  - `before=two_operators`, so the fixture starts with confirmed membership and a member count of 3, not latched (finding r1-12);
  - fake approvers `["op"]`.

  Decision runs are selected with `decisions(h)`, by their `--yes` argument.

  - `test_approve_happy_path`: post, the whole card is sent, reply `!approve <bead> <d12>` to it.
    - `decisions(h) == [[…expected argv…]]`, with `--via-ref=marmot:id:<12 hex>`, and the `ask_attempts` row matches.
    - The ask is `approved`; the reply says "Approved … btq's design gate accepts it".
    - The audit has `ask/deciding` and `ask/decided` with `gate_valid: true`.
  - `test_gate_rejects_is_reported`: the fake's `gate: false` makes the reply say "the gate rejects it". With empty `gate_reasons`, the reply uses the fallback wording.
  - `test_unthreaded_approve_refused` and `test_approve_as_reply_to_agent_message_refused`: `decisions(h) == []`.
  - `test_wrong_digest_refused`, `test_wrong_bead_refused`.
  - `test_card_not_fully_delivered`: the last chunk is held by `send_gate` (or failed with `fail_sends`), so `!approve` is refused.
  - `test_digest_changed_before_read`: the digest is changed between post and decide, so the ask is `stale` and no decision runs.
  - `test_digest_changed_inside_approve_bead`: `decide="edit-before"` makes the fake exit 3; the read-back is `untouched`, so the ask is `open` and the reply quotes the stderr line.
  - `test_non_approver_operator_refused`: `llctest` replies with the right digest; "not a btq approver"; no decision.
  - `test_latched_drops_approve`: latch through the store; the guard drops the command; nothing runs.
  - `test_removed_operator`: remove `llctest` through `on_ctl`; its `!approve` is then dropped by the guard.
  - `test_removal_waits_for_decision`: `decide="hang"` with `DECIDE_SECONDS` patched to 1. A concurrent `on_ctl(remove)` completes only after the decision settles.
  - `test_latch_during_decision_completes`: with a gated fake decision, a `GroupStateChanged` latch arrives mid-run. The decision completes, the audit has `latched_during: true`, and no reply is sent while latched.
  - `test_truncated_needs_details`: a long description shortens the card.
    - `!approve` is refused;
    - `!details` as a reply to the card; wait until all of its `askd:` parts are sent;
    - `!approve` as a reply to a details chunk then succeeds.
  - `test_details_by_other_operator_does_not_count`.
  - `test_redacted_content_refused_at_post`: three beads (a token, a stray 64-hex run, an unterminated PEM header) are each refused with the "decide it at the terminal" reason, and nothing is posted.
  - `test_plain_yes_is_a_note`: the reply "yes" leaves the ask `open`; the answer kind is `note`; nothing runs.
  - `test_replayed_message_id`: the same `!approve` event pushed twice gives one decision.
  - `test_closed_bead_refused_at_post`, `test_gaps_refused_at_post` (the gaps go back to the poster; nothing posted), `test_malformed_ask_refused_at_post` (`digest: null`).
  - `test_deny_with_reason`: the argv has `--deny` and `--note=<redacted reason>`; a token-shaped string in the reason is redacted.
  - `test_approve_bead_failure_reported`: `decide="fail"`, so the ask is `open` and the reply quotes the stderr line.
  - `test_partial_write_blocks`: `decide="partial"` makes the read-back show open with `decided: true`. The ask is `blocked`, and a later `!approve` is refused.
  - `test_foreign_decision_blocks`: `decide="foreign"`, so `blocked`.
  - `test_read_failure_uncertain_then_retried`: the read-back fails, so the ask is `uncertain`. Once the fake recovers, `!asks` retries and settles it.
  - `test_busy_read_back`: a fake `busy` makes the read-back retry, then `uncertain`.
  - `test_refused_then_retried_then_restart`: an `!approve` refused at step 5 (not an approver), then a successful one, then a restart. Only the second attempt is current; the first is `refused`; the reconcile has nothing to do.
  - `test_uncertain_then_reconciled_at_restart`.
  - `test_reconcile_snapshot_statuses`: a restart with ask A `deciding` and ask B `uncertain` (attempt kept). The snapshot holds both with their statuses; A settles from `deciding` and B from `uncertain`. Changing B's status in the store before the reconcile makes its compare-and-set fail, with an `ask/conflict` audit (r3-2).
  - `test_reconcile_does_not_touch_live_attempt`: a restart snapshot with ask A `deciding`, while a live decision on ask B pauses (gated fake read) after `begin_attempt`. The reconcile settles A only; B is untouched and completes.
  - `test_blocked_asks_do_not_exhaust_quota`: 20 `blocked` asks, then a question post is admitted.
  - `test_restart_while_deciding`: in `before_store`, set the ask to `deciding`, the inbound row to `executing`, and the `ask_attempts` row, with the fake bead closed by that attempt. At start, the inbound row gets `RESTARTED_NOTICE` and the ask becomes `approved` with the reconcile notice. A variant where the bead is closed by a different `via_ref` becomes `blocked`.
  - `test_supersede`: a second post for the same bead; the old card's `!approve` is refused as superseded.
  - `test_post_refused_while_bead_deciding`.
  - `test_concurrent_posts_respect_limit`: with the fake `--json` gated, `asyncio.gather` of 5 approval posts at 18 active asks admits exactly 2, and a third concurrent post is refused "busy".
  - `test_latch_during_post_read_refused`: latch while the post's `--json` is gated; the post is refused and nothing is queued.
  - `test_not_configured`: without `approve_bead`, an approval post is refused, and question asks still work.
  - `test_answer_command_refused_for_approval`.
- [ ] **3.3 Run them to see them fail**, then **3.4 implement**, then **3.5 run the gate.**
- [ ] **3.6 Commit:** `git commit -m "admind: approval asks and !approve/!deny through approve-bead, pinned to the shown digest"`.
- [ ] **3.7 Cross-model review** of Task 3's range, with a brief asking specifically for attacks on R6–R12 and R21–R23, then fix commits.

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
     - `env -i HOME="$HOME" PATH=<UNIT-PATH> <BTQ-LIVE>/bin/approve-bead <a closed kind:approval bead> --json` prints JSON. This proves `bd` and the credentials resolve under the unit's environment.
     - The unit's `HOME` is the operator's terminal `HOME`, so relay and terminal decisions share one lock directory (`~/.local/state/beads-task-queue/approve-bead/`).
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
     - The reply said whether btq's design gate accepts it.
     - The card's ref links open the pinned file on GitHub.
  6. Throwaway bead B: an edit on the host after the card was posted makes `!approve` report it changed (stale), and nothing is recorded.
  7. Throwaway bead C: `!deny C <reason>` records the denial, with the reason in the bead comment.
  8. From the test operator's account (not a btq approver): `!approve` on a fresh card is refused with "not a btq approver".
  9. A throwaway bead D whose description holds a fake token-shaped string: the post is refused with "decide it at the terminal", and nothing reaches the phone.
  10. The audit (`audit.jsonl`) has `ask/posted`, `refused`, `deciding` and `decided` records for the above, with no npub or token.
  11. Regression: `!ps`, `!tail`, `!details` on a summary, and a normal prompt all behave as before.
- [ ] **4.6** Record the acceptance result on the acceptance bead and close Tasks 1–4 per the queue protocol.

## Self-review notes (plan author)

- Every R-rule maps to a task: Task 1 covers R5, R6, R19 and R20 in btq. Task 2 covers R1–R4, R7 (cards), R13, R14 (question/merge), R15–R18 and R24. Task 3 covers R5, R6, R8–R12 and R21–R23, plus R13 for approval cards and R19–R20 on the admind side. Task 1 also covers R25 (links).
- `btq.Queue()` only reads files (policy, credentials) and creates its state directory, so Task 1's tests build it offline with temp `HOME` and `BTQ_*` paths.
- Review r1 findings and where they land: 1 (Task 1 lock and re-check), 2 (Task 3 `ask_attempts`, reaping), 3 (R21, Task 3 `approval_card`), 4 (Task 1 `gate_valid`, Task 3 `settle`/`blocked`), 5 (R11, Task 3 test), 6 (R8, `card_parts`), 7 (R22, Task 3 tests), 8 (P4), 9 (R24, Task 2), 10 (nullable digest), 11 (offline btq gate), 12 (`two_operators`, `decisions(h)`), 13 (R25 links, spec §10 §5.9 delta, swap wording), 14 (`queue.repo`; no lazy queue).
- ADR text is not edited by this plan. The §8 and §5.4 deltas are in the spec §10, for the r14 editor.
- Review r2 findings and where they land:
  - 1: R21 is checked per queued chunk; `approval_card` returns the chunks; the details chunks are stored and resent as they are.
  - 2: `asks.attempt`, `begin_attempt`/`close_attempt` compare-and-set, `settled` values.
  - 3: R26 startup snapshot, reconcile under `work_lock`.
  - 4: `blocked` is terminal and outside `ACTIVE`.
  - 5: the fallback wording for empty `gate_reasons`.
- Review r3 findings and where they land:
  - 1: reaping kills the process group on every exit path, a normal exit included; Task 1's lock is inherited by `bd`; `test_descendant_killed_after_parent_exit` and `test_lock_outlives_parent`.
  - 2: `recovery_snapshot` returns the status; `test_reconcile_snapshot_statuses`.
  - 3: percent-encoded blob paths; `test_links_forms`.
