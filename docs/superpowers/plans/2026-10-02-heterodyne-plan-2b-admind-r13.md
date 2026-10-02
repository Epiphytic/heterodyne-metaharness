# heterodyne-metaharness Plan 2b: `admind` revision 13 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** bring the shipped `admind` (plan 2) to ADR 0001 revision 13 §8:

- several operators in one group;
- membership changes made only by admind, as journaled transitions;
- one redaction applied to everything admind posts and audits;
- long replies summarized, with a deterministic backstop when summarizing fails;
- `!details` and `!details full`;
- two delivery lanes;
- an untruncated audit.

**Architecture:** the daemon keeps its turn, hold and dispatch machinery unchanged. New behaviour lives in small modules (`redact`, `membership`, `ctl`, `summarize`, `backstop`) and in a few new SQLite tables. The daemon uses them at four points:

- ingress (`on_message`);
- the end of a turn (`on_stop`);
- the outbox (`outbox_pass`);
- a new host control socket, which is how `admind operators add|remove` and `admind rearm` reach the running daemon.

**Tech Stack:** unchanged from plan 2: Python 3.12+, asyncio, sqlite3, msgspec, tmux 3.x, `wn-agent` 0.10.x, Claude Code 2.1.x; pytest, hypothesis, ruff, pyright (strict on `src/`).

**Spec:** ADR 0001 revision 13 is design-repo commit `66b3aecb639e6ec56f108e2f55d483d4dedda485`, approved in bead `btq-5ky39`. Its design review is `reviewer=gpt-6.1-sol author=claude-opus-5-5 mode=cross-model`, records r18–r20. Section numbers (§) refer to it. Task 10 copies it byte-identical into `$HZ/docs/adr/0001-workstreams-v2.md`. Wire shapes: `$HZ/docs/spikes/S4-marmot.md`; this plan's Task 1 adds `$HZ/docs/spikes/S4b-membership.md`.

## Global Constraints

- **Variables:** `$HZ` is the product repo checkout (`heterodyne-metaharness`, a clone, never the live harness or any symlink to it). No step may write any of the following of the reference install into a committed file: an install path, npub, hex account or group ID, token, relay URL or unit name.
- **Live services are off-limits:** never stop, restart, reconfigure or open the home of a running `wn-agent-*`, `hermes-*` or `hermes-workstreams*` unit, or any Marmot home other than admind's own and Task 1's throwaway homes. Never restart the user manager. Never put live `hermes-*` or `wn-agent-*` units in `restart_units`.
- **Tests:**
  - Tests never touch the network, a real `wn-agent`, the real `systemctl`, the real `claude` binary, or `~/.claude`.
  - tmux tests use only private `-L hz-test-<uuid>` servers.
  - The summarizer in tests is a fake script under `tmp_path`.
- **Python** `>=3.12`, managed with `uv`.
  - Runtime dependencies: exactly `msgspec`.
  - Async tests use `asyncio.run` inside sync test functions; no pytest-asyncio.
- **`sys.platform`** appears only in `src/heterodyne/platform.py`.
- **No model names** in `src/`. Test fixtures use made-up names such as `m1`.
- **Install-agnostic repo:** `scripts/check_install_agnostic.py` stays clean. npub literals only under `tests/fixtures/`; tests build npubs with `hex_to_npub`.
- **Config keys must not trip the secret scanner.** The new keys are `summarizer`, `reply_verbatim_lines` and `reply_verbatim_chars`.
- **Never print tokens or npubs.**
  - Errors name the config key, not the value.
  - Operator names from `policy.toml` may be shown. Pass them through `show(name, False)` when they come from a request.
- **Don't patch out the latch.** No test, flag or config may disable it.
- **Review rule** (§11.1): every code task ends with a review by a **different LLM than the implementer**.
  - If the implementer is Claude:
    `~/.codex/packages/app-server-daemon/releases/0.159.3-x86_64-unknown-linux-musl/bin/codex exec -m gpt-6.1-sol -c model_reasoning_effort=medium -s read-only -o /tmp/review-<task>.md "$(cat /tmp/brief-<task>.md)" < /dev/null`, under `timeout 900`.
    - `< /dev/null` is mandatory: without it codex waits on stdin forever.
    - Never `pkill -f`; kill by PID.
  - If the implementer is Codex/GPT: `claude -p --model claude-opus-5-5 --permission-mode plan "<brief>" > /tmp/review-<task>.md`.
  - The brief names the diff range and the ADR sections and asks for `[BLOCKING]`/`[NON-BLOCKING]` findings. Fix or rebut every blocking finding.
  - Close evidence includes `Code-Review: reviewer=<model> author=<model> mode=cross-model range=<BASE>..<HEAD>`.
- **Beads** (workstream `heterodyne`):
  - Task 1 is `kind:research`.
  - Tasks 2–10 are `kind:task`, each with `metadata.design_approval=btq-5ky39`, `metadata.adr_revision=66b3aecb639e6ec56f108e2f55d483d4dedda485` and a blocking dependency on `btq-5ky39`.
  - Tasks run in order 1 → 10. Task 5 and Task 7 also depend on Task 1, because its findings decide their constants.
  - The held operator acceptance bead `btq-llc3n` is re-scoped onto this plan and depends on Task 10.
  - Other existing beads are not touched (they wait for plan 9).
- **Branches:**
  - Integration branch `plan-2b-admind` in `$HZ`, from `main` at `68e9d4a`.
  - Each task's worktree (`btq … worktree`) branches from the integration branch's tip. The orchestrator fast-forwards `plan-2b-admind` to the task's reviewed head.
  - Nothing is pushed until Task 10. Merging is the operator's action, and every merge ask carries the full PR URL.
- **Gate before each commit:** `uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`, all clean.

## Decisions made in this plan (within the ADR; reviewers should check them)

| # | Decision | Why | ADR |
|---|---|---|---|
| B1 | A new `admind/redact.py` replaces each matched secret, npub or 64-or-more-hex run with a marker. It keeps newline and tab and escapes every other C0, DEL and C1 character. Every post goes through it (in `Admind.post`, and on the whole text before chunking), as do the audit, the summarizer's input and `!details`. | `config.secret_scan.show` replaces the **whole** string when anything in it matches. An operator message that holds a commit hash would vanish from the audit, and a reply would become one marker. Newline and tab are layout, not terminal control, and a reply without newlines is unreadable. | §8 Redaction, Audit |
| B2 | `AdmindSettings.operators` is a tuple of `Operator(name, npub, hex)`. Operators without `marmot_npub` are skipped; none left, or two with one key, is a `ConfigError`. The daemon re-reads `policy.toml` through an injected `load_operators()` for each add or remove and after each commit. | "Every entry in `operators` with a Marmot npub is an admind operator"; a temporary operator is added to `policy.toml` while admind runs. | §8 Operators |
| B3 | The trusted member count is kv `expected_members`. On an initialised install that lacks it (a plan-2 group, which always had exactly two members) it is set to `2` at startup and audited as `migrated`. | Upgrading in place must not latch or trust a wrong count. | §8 Group membership |
| B4 | `PeerError(ControlError)` is raised only for a wn-agent `error` frame: "wn-agent reported failure". Any other `ControlError` (timeout, closed socket, protocol error) is "unknown". | The commit/abort rule needs exactly that distinction. | §8 transition step 4 |
| B5 | `membership.settle(reported, count, pending)` is a pure function: `ok` with `to` commits, `failed` with `from` aborts, everything else latches. An event or latch during the transition forces `latch`. | Directly testable; matches steps 3–4. | §8 transition |
| B6 | Host commands reach the daemon over `state_dir/ctl.sock`: 0600, in the 0700 state directory, one JSON request per connection. `admind rearm` now needs a running daemon, because only the daemon can read the member count (S4: a wn-agent home can't be opened by a second process). | ADR: "The command asks the running daemon". `rearm` "takes the current member count as trusted". | §8 Group membership |
| B7 | During a transition the in-memory flag `changing` blocks dispatch and posting, through `authorised()`, `may_post()` and `_flush`. `guard_lock` serializes the transition with every `check_group`. | Step 1: "serializes it with every guard check and holds dispatch and outbound posting". | §8 transition step 1 |
| B8 | Summarizer launch: `claude -p --model M --tools "" --setting-sources project --settings '{"disableAllHooks": true}' --strict-mcp-config --no-session-persistence --output-format text`, prompt on stdin. It runs in an empty private directory `state_dir/summarizer` and the profile's `args` are **not** appended. Admind appends the footer. Output that is empty, or longer than 16 lines or 2,000 characters, is a failure. | Headless, read-only, without tools, and unaffected by user-level hooks or MCP servers. `--bare` would need an API key. The limits stop a runaway "summary" from replacing the backstop. | §8 Replies |
| B9 | Summary jobs are persisted (`turns.status = 'summarizing'`) and worked serially by `summary_loop`; startup requeues them. | A restart must not lose a reply. | §8 Replies |
| B10 | A backstop batch longer than `chunk_chars` (very long lines) goes out as consecutive chunks, each recorded for `!details`; nothing is truncated. A fixed title line, not counted in the 50, opens every batch. | The operator asked for accuracy over brevity; the title tells the operator why they see a batch and how to get everything. | §8 Backstop |
| B11 | New tables: `prompts` (who sent each operator message, when, first words), `turns` (one per reply: redacted text, origin, transcript path and end offset), `batches`, and `posts` (outbox key to turn or batch). | `!details` must work after a restart. | §8 `!details` |
| B12 | `!details` uses the command's `reply_to`: the outbox row with that `message_id`, then its `posts` row. Without a reply target it uses the latest summary or batch. Verbatim replies get records too, so `!details full` works on them. | ADR text; lookups are exact. | §8 `!details` |
| B13 | `!details full` reads the transcript only up to the size recorded at that turn's `Stop`, and only from the turn's last real user prompt onwards. It renders `tool_use` and `tool_result` blocks; thinking blocks are never read out. | It must show the tool calls of that turn, not of a later one. | §8 `!details full` |
| B14 | `outbox.lane` (1 or 2). `Store.next_pending()` returns the lowest lane first, then by sequence, and is re-read after every send. | "Only when the first lane is empty", and urgent messages are never stuck. | §8 Delivery lanes |
| B15 | A pending membership record found at startup latches before `recover()` or anything else runs. | Step 5. | §8 transition step 5 |

## File map

| File | Change |
|---|---|
| `src/heterodyne/admind/redact.py` | **new**: `redact(text) -> str` |
| `src/heterodyne/admind/membership.py` | **new**: `Pending`, `settle` |
| `src/heterodyne/admind/ctl.py` | **new**: control socket server and client |
| `src/heterodyne/admind/summarize.py` | **new**: `needs_summary`, `summarize`, `SummaryFailed` |
| `src/heterodyne/admind/backstop.py` | **new**: `Entry`, `origin`, `first_words`, `collapse`, `render` |
| `src/heterodyne/admind/settings.py` | `Operator`, `operators`, summarizer and verbatim keys |
| `src/heterodyne/admind/guard.py` | several operators, an expected count |
| `src/heterodyne/admind/store.py` | lanes, `prompts`, `turns`, `batches`, `posts` |
| `src/heterodyne/admind/commands.py` | `!details [full]` |
| `src/heterodyne/admind/hook.py` | `transcript_size`, `turn_tool_calls` |
| `src/heterodyne/admind/daemon.py` | wiring (Tasks 2–9) |
| `src/heterodyne/admind/cli.py` | `init` for all operators, `operators add|remove`, `rearm` via the daemon |
| `src/heterodyne/agents/claude_code.py` | `headless_argv` |
| `src/heterodyne/marmot/control.py` | `PeerError`, `group_member_add`, `group_member_remove` |
| `src/heterodyne/defaults/defaults.toml` | `reply_verbatim_lines = 8`, `reply_verbatim_chars = 800` |
| `tests/fakes/fake_wn_agent.py` | membership requests and failure modes |
| `tests/fakes/settings.py` | operators tuple, new keys |
| `tests/test_admind_r13_*.py` | **new** test files, one per task |
| `docs/admind.md`, `docs/configuration.md`, `docs/security-model.md`, `docs/adr/…`, `docs/reviews/…`, `docs/spikes/S4b-membership.md` | Tasks 1 and 10 |

---

### Task 1: Verify membership operations and the summarizer launch (`kind:research`)

**Files:**
- Create: `docs/spikes/S4b-membership.md`

This task checks real behaviour, using throwaway identities only. It writes no product code. Its findings set the constants in Task 5 (error codes) and Task 7 (argv). **If a finding contradicts the ADR, stop:** release the bead with the finding as the reason, and the orchestrator escalates to the operator. An example is a `member_count` that doesn't change until the invitee accepts.

- [ ] **Step 1: Prepare three throwaway homes.** Run each in its own `mktemp -d /tmp/s4b-XXXX`, never under `~/.local/share` or a live home.

```bash
S4B=$(mktemp -d /tmp/s4b-XXXX); chmod 700 "$S4B"
for who in admin opA opB; do
  mkdir -m 700 -p "$S4B/$who/ctl"; head -c 32 /dev/urandom | xxd -p -c 64 > "$S4B/$who/token"; chmod 600 "$S4B/$who/token"
done
# RELAYS: the same relay list as [admind.marmot] relays in the host config (read it; do not commit it)
for who in admin opA opB; do
  wn-agent --home "$S4B/$who" --socket "$S4B/$who/ctl/wn.sock" --auth-token-file "$S4B/$who/token" \
    $(for r in $RELAYS; do printf -- '--relay %s ' "$r"; done) > "$S4B/$who/log" 2>&1 &
  echo $! > "$S4B/$who/pid"
done
for who in admin opA opB; do
  wn-agent bootstrap --home "$S4B/$who" --socket "$S4B/$who/ctl/wn.sock" --auth-token-file "$S4B/$who/token" \
    --label "s4b-$who" --invite-policy deny --no-quic --json --wait-for-socket 30 \
    $(for r in $RELAYS; do printf -- '--relay %s ' "$r"; done) > "$S4B/$who/bootstrap.json"
done
```

Never print `bootstrap.json`, a token or an npub. Read the account hex with `jq -r` into shell variables only.

- [ ] **Step 2: Write a small client.** Put it at `$S4B/ctl.py`, and use it for every request below. It prints only `type`, `code`, `retryable`, `member_count` and `pending_welcome_count`.

```python
import asyncio, json, sys
from pathlib import Path
from heterodyne.marmot.control import ControlClient, ControlError

async def main() -> None:
    home, payload = Path(sys.argv[1]), json.loads(sys.argv[2])
    client = ControlClient(home / "ctl" / "wn.sock", (home / "token").read_text().strip())
    try:
        frame = await client.call(payload, payload.pop("_expect"), dict)
        print({k: frame.get(k) for k in ("type", "member_count", "pending_welcome_count")})
    except ControlError as exc:
        print({"error": exc.code, "retryable": exc.retryable})

asyncio.run(main())
```

Run it with `uv run python "$S4B/ctl.py" HOME 'JSON'` from `$HZ`. `call(..., dict)` decodes the whole frame. `KNOWN_ERROR_CODES` turns unknown codes into `unrecognised`, so for this step only, also run with the code allowlist bypassed: temporarily print `exc.detail`'s **code-like first token**, never its free text.

- [ ] **Step 3: Record the facts below in `docs/spikes/S4b-membership.md`, each with PASS/FAIL and the observed values.**
  1. `group_create` by admin with `members=[opA]`, then `group_info.member_count`. Expected: 2.
  2. `group_member_add` of opB. Record the response type, the `member_count` immediately after the response (before opB accepts), and the count after opB accepts its welcome. **The ADR's commit rule assumes the count is `from + 1` right after a successful response.**
  3. `group_member_remove` of opB: response, then count.
  4. `group_member_add` of opA, who is already a member: error code and `retryable`, then count (expected unchanged).
  5. `group_member_remove` of opB again (not a member): error code and count.
  6. With admin subscribed through `subscribe_inbound` during 2–3: confirm **no** `group_state_changed` reaches admin's own subscription. With opA subscribed: record the `change` values opA sees.
  7. Kill the admin child (by PID) between sending a `group_member_add` and reading its reply, and record what the client raises. This is the "lost reply" path.

- [ ] **Step 4: Check the summarizer launch shape with the real CLI.** This is the only place the real `claude` runs in this plan. Use an empty directory and send nothing sensitive.

```bash
D=$(mktemp -d /tmp/s4b-sum-XXXX)
printf 'Summarize in one line: the build passed and 3 tests were skipped.\n' | (cd "$D" && timeout 90 claude -p \
  --model "$(python3 -c 'import tomllib,os;print(tomllib.load(open(os.path.expanduser("~/.config/heterodyne/config.toml"),"rb"))["profiles"]["claude-opus"]["model"])' 2>/dev/null || echo sonnet)" \
  --tools "" --setting-sources project --settings '{"disableAllHooks": true}' --strict-mcp-config \
  --no-session-persistence --output-format text); echo "exit=$?"
ls -A "$D"
```

Record:
- the exit status, and that one line of text came back;
- that `$D` is still empty;
- that no new entry appeared in the queue or brain hook state. Compare `sqlite3 ~/.hermes/workstreams/harness.sqlite3 'select count(*) from brain_offers'` before and after; if the database is absent, record that.

If `--tools ""` is rejected by this CLI version, record the error and the working alternative (`--disallowedTools` listing every tool, or `--allowedTools` with nothing). Task 7 uses whatever is recorded here.

- [ ] **Step 5: Clean up.** Have admin remove opA. Kill each child by the PID in `$S4B/*/pid`, then `rm -rf "$S4B" "$D"`. Record cleanup in the doc.

- [ ] **Step 6: Commit the doc and close the bead.**

```bash
git add docs/spikes/S4b-membership.md
git commit -m "docs(spikes): S4b membership operations and summarizer launch shape"
```

The close evidence lists every check's PASS/FAIL and states whether the ADR's assumptions hold.

---

### Task 2: One redaction, and an untruncated audit

**Files:**
- Create: `src/heterodyne/admind/redact.py`
- Modify: `src/heterodyne/admind/daemon.py` (module docstring, `AUDIT_TEXT_CHARS`, `own_text`, `post`, `reply`, `on_stop`, `relay_alert`)
- Test: `tests/test_admind_r13_redact.py`

**Interfaces:**
- Produces: `heterodyne.admind.redact.redact(text: str) -> str`. Later tasks call it for every post, the summarizer input, the audit and `!details`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_redact.py
import asyncio
import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from heterodyne.admind.redact import redact
from heterodyne.marmot.nip19 import hex_to_npub

HEX = "ab" * 32
NPUB = hex_to_npub("c3" * 32)


def test_replaces_only_the_match() -> None:
    assert redact(f"commit {HEX} is bad") == "commit <redacted hex key> is bad"
    assert redact(f"ask {NPUB}, please") == "ask <redacted npub>, please"
    assert redact("token ghp_" + "A" * 30 + " end") == "token <redacted GitHub token> end"


def test_longer_hex_runs_are_hidden_whole() -> None:
    assert redact("x" + "f" * 65 + "y") == "x<redacted hex key>y"


def test_pem_block_is_hidden_to_its_end() -> None:
    pem = "-----BEGIN " + "PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"   # split: gitleaks
    assert redact(f"a\n{pem}\nb") == "a\n<redacted PEM private key>\nb"


def test_layout_kept_controls_escaped() -> None:
    assert redact("a\tb\nc\rd\x1b[2Je\x9b") == "a\tb\nc\\x0dd\\x1b[2Je\\x9b"


@given(st.text())
def test_idempotent(text: str) -> None:
    once = redact(text)
    assert redact(once) == once


@given(st.text(alphabet="0123456789abcdefABCDEF xyz\n", min_size=0, max_size=300))
def test_no_hex_key_survives(text: str) -> None:
    import re
    assert re.search(r"[0-9A-Fa-f]{64}", redact(text)) is None


@given(st.text())
def test_no_control_survives(text: str) -> None:
    out = redact(text)
    assert all(c in "\n\t" or not (ord(c) < 32 or 127 <= ord(c) <= 159) for c in out)
```

Also add a daemon test to the same file, using the `Harness` from `tests/test_admind_daemon.py`. Import it with `from test_admind_daemon import Harness, run_with`, which is the existing pattern in the `test_admind_t9r*.py` files. The test checks that the audit keeps a long operator message whole and that a reply's hex is redacted in the chat:

```python
from test_admind_daemon import Harness, run_with  # noqa: E402  (shared harness)
from admind_waits import wait_until  # noqa: E402


def test_audit_is_whole_and_posts_are_redacted(tmp_path: Path) -> None:
    long_text = "line " * 1000 + HEX          # over the old 2,000-char cut

    async def scenario(h: Harness) -> None:
        await h.say("hello")                           # join signal
        await wait_until(lambda: any("listening" in t for t in h.texts()))
        await h.say(long_text)
        await wait_until(lambda: any(r.get("kind") == "inbound" and len(r.get("text", "")) > 4000
                                     for r in (json.loads(x) for x in h.settings.state_dir.joinpath(
                                         "audit.jsonl").read_text().splitlines())))
        h.daemon.post("probe", f"see {HEX}", None)
        await wait_until(lambda: any("see <redacted hex key>" == t for t in h.texts()))

    h = run_with(tmp_path, scenario)
    records = [json.loads(x) for x in (h.settings.state_dir / "audit.jsonl").read_text().splitlines()]
    inbound = [r for r in records if r.get("kind") == "inbound" and "line line" in r.get("text", "")]
    assert inbound and inbound[-1]["text"].endswith("<redacted hex key>")
    assert all(HEX not in t for t in h.texts())
```

(If `run_with` needs tmux and tmux is absent, mark the test with the same `needs_tmux` skip the daemon tests use.)

- [ ] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/test_admind_r13_redact.py -q`. Expected: `ModuleNotFoundError: heterodyne.admind.redact`.

- [ ] **Step 3: Implement `redact.py`.**

```python
"""One redaction for everything admind posts, summarizes or audits (ADR 0001 §8, revision 13).

Each match is replaced by a marker and the rest of the text is kept: secrets (the patterns of
`config.secret_scan`, with a PEM block hidden to its END line), npubs, and runs of 64 or more hex
digits. Newline and tab are kept (they are layout); every other C0, DEL and C1 character is escaped as
`\\xNN`. Idempotent: markers and escapes contain nothing that matches again.
"""

import re

from heterodyne.config.secret_scan import IDENTIFIER_VALUES, SECRET_VALUES

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
                  re.DOTALL)
_SECRETS = tuple((kind, pattern) for kind, pattern in SECRET_VALUES if kind != "PEM private key")
_NPUB = dict(IDENTIFIER_VALUES)["npub"]
_HEX_RUN = re.compile(r"[0-9A-Fa-f]{64,}")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def redact(text: str) -> str:
    text = _PEM.sub("<redacted PEM private key>", text)
    for kind, pattern in _SECRETS:
        text = pattern.sub(f"<redacted {kind}>", text)
    text = _NPUB.sub("<redacted npub>", text)
    text = _HEX_RUN.sub("<redacted hex key>", text)
    return _CONTROLS.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
```

- [ ] **Step 4: Wire it into the daemon.**
  - `from heterodyne.admind.redact import redact`.
  - Delete `AUDIT_TEXT_CHARS`. `own_text` becomes `return redact(text)`, with the docstring "The operator's own text for the audit log, whole (revision 13), redacted (B1)."
  - `post` becomes:

```python
    def post(self, key: str, text: str, reply_to: str | None, lane: int = 1) -> None:
        """Queue one message. Everything admind posts is redacted here (B1); callers that chunk redact the
        whole text first, so a value can't escape redaction by straddling a chunk boundary."""
        if self.store.enqueue(key, redact(text), reply_to):
            self.wake.set()
```

  (`lane` is stored from Task 4; until then `enqueue` ignores it, so here call `self.store.enqueue(key, redact(text), reply_to)` and keep the parameter.)
  - `reply` chunks `redact(text)` instead of `text`.
  - In `on_stop`, `parts = chunk.split(redact(text), self.s.chunk_chars)`.
  - In `relay_alert`, `text = redact(alerts.render(name, alert, self.s.chunk_chars))`.
  - Update the module docstring's "Output safety" list. The fourth and fifth bullets become:
    - "an operator's own text, audited whole after `redact`";
    - "the admin agent's reply, a summary, a batch, `!details` or the `!tail` screen, relayed to the operators in chat after `redact`, never written to the audit log".

- [ ] **Step 5: Run the tests and confirm they pass.** Run `uv run pytest tests/test_admind_r13_redact.py -q`, then the full gate. Fix any existing test that asserted the 2,000-character cut or an unredacted reply (`grep -rn "AUDIT_TEXT_CHARS\|2000" tests/`). Each such fix must keep the test's intent and use the new behaviour.

- [ ] **Step 6: Commit.**

```bash
git add src/heterodyne/admind/redact.py src/heterodyne/admind/daemon.py tests/
git commit -m "feat(admind): one redaction for posts and audit; untruncated audit (ADR r13 §8)"
```

- [ ] **Step 7: Cross-model review** of `BASE..HEAD` against §8 Redaction and Audit; fix blocking findings.

---

### Task 3: Several operators and a trusted member count

**Files:**
- Modify: `src/heterodyne/admind/settings.py`, `src/heterodyne/admind/guard.py`, `src/heterodyne/admind/daemon.py` (`__init__`, `on_message`, `check_group`, `run`), `src/heterodyne/admind/cli.py` (`init`), `tests/fakes/settings.py`, `tests/test_admind_logic.py`, `tests/test_admind_settings.py`, `tests/test_admind_cli.py`
- Test: `tests/test_admind_r13_operators.py`

**Interfaces:**
- Produces:
  - `settings.Operator(name: str, npub: str, hex: str)`, a frozen dataclass;
  - `settings.operators(cfg: Config) -> tuple[Operator, ...]`;
  - `AdmindSettings.operators: tuple[Operator, ...]`, which replaces `operator_hex` and `operator_npub`;
  - `guard.Verdict(action, reason, operator: str | None = None)`;
  - `guard.judge_message(ev, *, group_id: str, operators: Mapping[str, str], latched: bool)`, where `operators` maps hex to name;
  - `guard.judge_member_count(count: int, expected: int) -> Verdict`;
  - `Admind.expected_members() -> int`;
  - `Admind.load_operators: Callable[[], tuple[Operator, ...]]`;
  - `Admind.operators: dict[str, str]` (hex → name);
  - `tests/fakes/settings.py` exports `OPERATOR_HEX`, `SECOND_HEX = "d4" * 32` and `operator(name, hex) -> Operator`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_operators.py
from pathlib import Path

import pytest
from fakes.settings import OPERATOR_HEX, SECOND_HEX, make_settings, operator

from heterodyne.admind import guard
from heterodyne.admind.settings import resolve
from heterodyne.config import ConfigError, load
from heterodyne.marmot.control import GroupStateChanged, InboundMessage, Message, Sender
from heterodyne.marmot.nip19 import hex_to_npub
from test_admind_settings import BASE_CONFIG  # noqa: E402

GROUP = "b2" * 32


def policy(d: Path, body: str) -> dict[str, str]:
    (d / "config.toml").write_text(BASE_CONFIG)
    (d / "policy.toml").write_text(body)
    return {"HETERODYNE_CONFIG_DIR": str(d), "HETERODYNE_STATE_DIR": str(d / "state"), "HOME": str(d)}


def two_ops(extra: str = "") -> str:
    return (f'approvers = ["a", "b", "c"]\noperators = ["a", "b", "c"]\n'
            f'[identities.a]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n'
            f'[identities.b]\nmarmot_npub = "{hex_to_npub(SECOND_HEX)}"\n{extra}')


def test_every_operator_with_an_npub(tmp_path: Path) -> None:
    env = policy(tmp_path, two_ops())          # "c" has no identity: skipped
    s = resolve(load(None, env), env)
    assert [(o.name, o.hex) for o in s.operators] == [("a", OPERATOR_HEX), ("b", SECOND_HEX)]


def test_no_operator_with_an_npub_is_an_error(tmp_path: Path) -> None:
    env = policy(tmp_path, 'approvers = ["a"]\noperators = ["a"]\n')
    with pytest.raises(ConfigError, match="at least one"):
        resolve(load(None, env), env)


def test_duplicate_keys_are_an_error(tmp_path: Path) -> None:
    body = (f'approvers = ["a", "b"]\noperators = ["a", "b"]\n'
            f'[identities.a]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n'
            f'[identities.b]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n')
    env = policy(tmp_path, body)
    with pytest.raises(ConfigError, match="same marmot_npub"):
        resolve(load(None, env), env)


def msg(sender: str) -> InboundMessage:
    return InboundMessage("a1" * 32, GROUP, Message("0" * 64, Sender(sender, False), "hi", 1))


def test_guard_names_the_operator() -> None:
    ops = {OPERATOR_HEX: "a", SECOND_HEX: "b"}
    v = guard.judge_message(msg(SECOND_HEX.upper()), group_id=GROUP, operators=ops, latched=False)
    assert (v.action, v.operator) == ("process", "b")
    assert guard.judge_message(msg("e5" * 32), group_id=GROUP, operators=ops, latched=False).action == "drop"


def test_member_count_against_expected() -> None:
    assert guard.judge_member_count(3, 3).action == "process"
    v = guard.judge_member_count(2, 3)
    assert v.action == "latch" and v.reason == "group has 2 members, expected 3"
```

Add a daemon test, built with the shared harness:
- two operators (`make_settings(tmp_path, operators=(operator("a", OPERATOR_HEX), operator("b", SECOND_HEX)))`);
- `FakeWnAgent(member_count=3)`;
- kv `expected_members = "3"`.

It asserts:
1. a message from `SECOND_HEX` is processed, and its audit `inbound` record has `operator == "b"`;
2. a stranger is dropped;
3. with `expected_members` absent and `member_count=2`, startup sets it to `"2"` and audits `{"kind": "guard", "action": "migrated"}`.

(`Harness.__init__` takes the settings from `make_settings`; add an optional `settings_overrides` argument to `Harness` in `tests/test_admind_daemon.py` and pass it through.)

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `ImportError: cannot import name 'SECOND_HEX'` / `operator`.

- [ ] **Step 3: Implement the settings.** In `settings.py`:

```python
@dataclass(frozen=True)
class Operator:
    name: str
    npub: str
    hex: str


def operators(cfg: Config) -> tuple[Operator, ...]:
    """Every `policy.toml` operator with `identities.<name>.marmot_npub` (ADR 0001 §8, revision 13)."""
    found: list[Operator] = []
    seen: set[str] = set()
    for name in cfg.policy.operators:
        npub = cfg.policy.identities.get(name, {}).get("marmot_npub")
        if not npub:
            continue
        where = f"policy.toml: identities.{show(name, False)}.marmot_npub"
        try:
            key = npub_to_hex(npub).lower()
        except Nip19Error as exc:
            raise ConfigError(f"{where} is not a valid npub ({exc})") from None
        if key in seen:
            raise ConfigError(f"{where} is the same marmot_npub as another operator's")
        seen.add(key)
        found.append(Operator(name, npub, key))
    if not found:
        raise ConfigError("policy.toml: admind needs at least one entry in operators with "
                          "identities.<name>.marmot_npub")
    return tuple(found)
```

Delete `_operator`. In `AdmindSettings`, replace `operator_hex` and `operator_npub` with `operators: tuple[Operator, ...]`, and in `resolve` pass `operators=operators(cfg)`.

`tests/fakes/settings.py`:

```python
OPERATOR_HEX = "c3" * 32
SECOND_HEX = "d4" * 32


def operator(name: str, key: str) -> Operator:
    return Operator(name, hex_to_npub(key), key)
```

and in `make_settings` the base uses `operators=(operator("op", OPERATOR_HEX),)`.

- [ ] **Step 4: Implement the guard.**

```python
@dataclass(frozen=True)
class Verdict:
    action: Literal["process", "drop", "ignore", "latch"]
    reason: str
    operator: str | None = None


def judge_message(ev: InboundMessage, *, group_id: str, operators: Mapping[str, str],
                  latched: bool) -> Verdict:
    if ev.group_id_hex.lower() != group_id:
        return Verdict("drop", "message from another group")
    sender = ev.message.sender
    if sender.is_self:
        return Verdict("ignore", "admind's own message")
    name = operators.get(sender.account_id_hex.lower())
    if name is None:
        return Verdict("drop", "sender is not an operator")
    if latched:
        return Verdict("drop", "admind is latched; run `admind rearm` on the host")
    return Verdict("process", "operator message", name)


def judge_member_count(count: int, expected: int) -> Verdict:
    if count == expected:
        return Verdict("process", f"{count} members, as expected")
    return Verdict("latch", f"group has {count} members, expected {expected}")
```

Update the module docstring:
- "Only MLS-authenticated messages from an operator's exact key ...";
- "a member count other than the trusted expected count".

- [ ] **Step 5: Implement the daemon and CLI changes.**
  - `Admind.__init__` gains a keyword `load_operators: Callable[[], tuple[Operator, ...]] | None = None`:

```python
        self.load_operators = load_operators or (lambda: settings.operators)
        self.operators: dict[str, str] = {o.hex: o.name for o in settings.operators}
```

  - `expected_members`:

```python
    def expected_members(self) -> int:
        """The trusted member count (B3). Absent only before the startup migration has run."""
        return int(self.store.get("expected_members") or "2")
```

  - `run()` starts with:

```python
        if self.store.get("expected_members") is None:     # a plan-2 group: always two members (B3)
            self.store.set("expected_members", "2")
            self.audit.write("guard", action="migrated", expected_members=2)
```

  - `check_group`: `verdict = guard.judge_member_count(info.member_count, self.expected_members())`.
  - `on_message`: `verdict = guard.judge_message(ev, group_id=self.group, operators=self.operators, latched=self.latched())`. The `inbound` audit record gets `operator=verdict.operator`.
  - `cli.init`:
    - `created = await client.group_create(account, s.group_name, [o.npub for o in s.operators])`;
    - in the same block, `store.set("expected_members", str(1 + len(s.operators)))`;
    - the final message becomes "Created admind's identity and its group with N operator(s). Each operator accepts the invite in their Marmot client; then start admind and send any message in the group: admind answers once it sees one of you."
  - `cli._run_with_child` passes `load_operators=lambda: resolve(hconfig.load(), os.environ).operators` to `Admind`.

- [ ] **Step 6: Update the existing tests.** `test_admind_logic.py` passes `operators={OP: "op"}`. `test_admind_settings.py` asserts `s.operators[0].hex == OPERATOR_HEX`. `test_admind_cli.py` checks that no operator's npub is in the output. Run the full gate.

- [ ] **Step 7: Commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): several operators and a trusted member count (ADR r13 §8)"
```

Cross-model review against §8 Operators and the latch rule.

---

### Task 4: Two delivery lanes

**Files:**
- Modify: `src/heterodyne/admind/store.py`, `src/heterodyne/admind/daemon.py` (`post`, `outbox_pass`)
- Test: `tests/test_admind_r13_lanes.py`

**Interfaces:**
- Produces:
  - `Store.enqueue(key, text, reply_to, lane: int = 1) -> bool`;
  - `Store.next_pending() -> OutboxRow | None`;
  - `OutboxRow.lane: int`;
  - `Admind.post(key, text, reply_to, lane=1)` now stores the lane.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_lanes.py
import sqlite3
from pathlib import Path

from heterodyne.admind.store import Store


def test_lane_one_first_then_lane_two(tmp_path: Path) -> None:
    s = Store(tmp_path / "a.db")
    s.enqueue("d1", "details 1", None, lane=2)
    s.enqueue("d2", "details 2", None, lane=2)
    s.enqueue("c1", "command reply", None)
    row = s.next_pending()
    assert row is not None and row.key == "c1" and row.lane == 1
    s.mark_sent(row.seq, None)
    row = s.next_pending()
    assert row is not None and row.key == "d1"
    s.mark_sent(row.seq, None)
    s.enqueue("alert", "urgent", None)          # arrives while lane 2 is being sent
    row = s.next_pending()
    assert row is not None and row.key == "alert"


def test_old_database_gains_the_lane_column(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, "
                     "reply_to TEXT, text TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL "
                     "DEFAULT 0, message_id TEXT); INSERT INTO outbox(key, text, status) VALUES ('k', 't', "
                     "'pending');")
    db.commit()
    db.close()
    s = Store(tmp_path / "old.db")
    row = s.next_pending()
    assert row is not None and row.lane == 1
```

Add a daemon test with the shared harness. While `!details`-style lane-2 rows are pending, a command reply queued after them is sent before the remaining lane-2 rows. Use `h.fake.on_send` to queue a lane-1 post the first time a lane-2 text is sent, then assert the order of `h.texts()`.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `TypeError: enqueue() got an unexpected keyword argument 'lane'`.

- [ ] **Step 3: Implement the store changes.**
  - In `SCHEMA`'s `outbox`, add `lane INTEGER NOT NULL DEFAULT 1 CHECK (lane IN (1, 2)),` after `reply_to`.
  - After `executescript(SCHEMA)` in `Store.__init__`:

```python
        columns = {str(r[1]) for r in self.db.execute("PRAGMA table_info(outbox)")}
        if "lane" not in columns:       # a database created before revision 13
            self.db.execute("ALTER TABLE outbox ADD COLUMN lane INTEGER NOT NULL DEFAULT 1")
```

  - `OutboxRow` gains `lane: int`.
  - `enqueue` inserts `lane`.
  - `pending()` orders `BY lane, seq` and selects `lane`.
  - New method:

```python
    @_locked
    def next_pending(self) -> OutboxRow | None:
        """The next message to send: lane 1 (commands, alerts, notices, summaries, batches, verbatim
        replies) before lane 2 (`!details`), each in order (B14)."""
        r = self.db.execute("SELECT seq, key, reply_to, text, attempts, lane FROM outbox "
                            "WHERE status = 'pending' ORDER BY lane, seq LIMIT 1").fetchone()
        return None if r is None else OutboxRow(int(r[0]), str(r[1]), None if r[2] is None else str(r[2]),
                                                str(r[3]), int(r[4]), int(r[5]))
```

  - `relay_alert`'s insert names `lane` explicitly as `1`.

- [ ] **Step 4: Implement the daemon changes.** `post` passes `lane` to `enqueue`. In `outbox_pass`, replace `for row in self.store.pending():` with:

```python
        while self.may_post():              # rechecked per row: a latch mid-batch stops the rest
            row = self.store.next_pending()     # re-read each time: a new lane-1 row goes next (B14)
            if row is None:
                break
```

  Keep the loop body as it is (`continue` after `mark_failed`, `break` after a retry).

- [ ] **Step 5: Run the gate. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): two outbox delivery lanes (ADR r13 §8)"
```

---

### Task 5: Journaled membership transitions

**Files:**
- Create: `src/heterodyne/admind/membership.py`
- Modify: `src/heterodyne/marmot/control.py`, `tests/fakes/fake_wn_agent.py`, `src/heterodyne/admind/daemon.py`
- Test: `tests/test_admind_r13_membership.py`

**Interfaces:**
- Consumes:
  - `Admind.expected_members()`, `Admind.load_operators` and `Admind.operators` (Task 3);
  - `docs/spikes/S4b-membership.md` (Task 1): the error codes to allowlist.
- Produces:
  - `control.PeerError(ControlError)`;
  - `ControlClient.group_member_add(account, group, members: list[str]) -> MembershipUpdated`;
  - `ControlClient.group_member_remove(account, group, members: list[str]) -> MembershipUpdated`;
  - `membership.Pending`;
  - `membership.settle(reported: Reported, count: int | None, pending: Pending) -> Outcome`;
  - `Admind.change_membership(op: str, name: str) -> tuple[str, str]`, whose result is one of `committed`, `aborted`, `latched` or `refused`;
  - `Admind.rearm() -> tuple[str, str]`, whose result is `rearmed` or `refused`;
  - `Admind.changing: bool`;
  - `Admind.guard_lock: asyncio.Lock`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_membership.py
import pytest

from heterodyne.admind.membership import Pending, settle

P = Pending("add", "b", "d4" * 32, 2, 3, "2026-10-02T00:00:00+00:00")


@pytest.mark.parametrize(("reported", "count", "outcome"), [
    ("ok", 3, "commit"),
    ("ok", 2, "latch"),            # success reported, count disagrees
    ("ok", None, "latch"),         # count unreadable
    ("failed", 2, "abort"),
    ("failed", 3, "latch"),        # failure reported, but the change happened
    ("unknown", 2, "latch"),       # lost reply: count alone never confirms
    ("unknown", 3, "latch"),
])
def test_settle(reported: str, count: int | None, outcome: str) -> None:
    assert settle(reported, count, P) == outcome  # type: ignore[arg-type]


def test_pending_round_trips() -> None:
    assert Pending.load(P.dump()) == P
```

Daemon scenarios, with the shared harness, `FakeWnAgent(member_count=2)`, and a `load_operators` stub that returns operators `op` and `b`:

1. **commit:** `change_membership("add", "b")` returns `("committed", …)`:
   - `expected_members == "3"`;
   - no `membership_pending`;
   - the fake received a `group_member_add` with `members == [SECOND_HEX]`;
   - audit records `pending` then `committed`;
   - a notice "Operator b was added to the group." is posted.
2. **abort:** with `fake.membership_mode = "fail"`, the call returns `("aborted", …)`, `expected_members == "2"`, and nothing is latched.
3. **latch on disagreement:** `"ok-no-count"` (success, count unchanged) and `"fail-count"` (error, count changed) each return `("latched", …)`, with `latched` set and `membership_pending` kept.
4. **latch on a lost reply:** with `"hang"`, a `ControlClient` timeout of 0.5 s gives `("latched", …)`.
5. **event during the transition:** the fake pushes a `group_state_changed` `member_removed` while it holds the add (mode `"slow"`, a 0.3 s delay before replying). Result: `latched`.
6. **holds:** while mode `"slow"` holds the add, `h.daemon.may_post()` and `h.daemon.authorised()` are false, and an operator prompt sent meanwhile is not pasted until the transition ends.
7. **refusals, each changing nothing:** unknown name; removing the last operator (`expected_members` 2, remove `op`); latched; a pending record already present.
8. **startup latch:** with `membership_pending` set before `run()`, the daemon latches before dispatching anything and audits the reason.
9. **rearm:** after (3), `rearm()` reads the count, sets `expected_members` to it, clears `latched` and `membership_pending`, and posting resumes. With `fake.fail_group_info = True` it returns `("refused", …)` and changes nothing. With a count of 1 it is refused.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `ModuleNotFoundError: heterodyne.admind.membership`.

- [ ] **Step 3: Implement `membership.py`.**

```python
"""Membership changes as journaled transitions (ADR 0001 §8, revision 13). Pure; the daemon acts."""

import json
from dataclasses import asdict, dataclass
from typing import Literal

Reported = Literal["ok", "failed", "unknown"]
Outcome = Literal["commit", "abort", "latch"]


@dataclass(frozen=True)
class Pending:
    op: Literal["add", "remove"]
    name: str
    member_hex: str
    from_count: int
    to_count: int
    started: str

    def dump(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def load(text: str) -> "Pending":
        raw = json.loads(text)
        return Pending(raw["op"], raw["name"], raw["member_hex"], int(raw["from_count"]),
                       int(raw["to_count"]), raw["started"])


def settle(reported: Reported, count: int | None, pending: Pending) -> Outcome:
    """Commit only on reported success with the `to` count; abort only on reported failure with the
    `from` count; anything else latches. A count alone never confirms a change (step 4)."""
    if reported == "ok" and count == pending.to_count:
        return "commit"
    if reported == "failed" and count == pending.from_count:
        return "abort"
    return "latch"
```

- [ ] **Step 4: Implement the control client.** In `control.py`:

```python
class PeerError(ControlError):
    """wn-agent answered with an `error` frame: it reported failure (B4). Every other ControlError
    (timeout, closed socket, malformed frame) leaves the outcome unknown."""


class MembershipUpdated(msgspec.Struct, frozen=True):
    group_id_hex: str
```

- `decode_head` raises `PeerError(...)` (same arguments) instead of `ControlError` for an error frame.
- Add each error code recorded in `docs/spikes/S4b-membership.md` to `KNOWN_ERROR_CODES` (for example the codes for adding a present member and removing an absent one).
- Add the two client methods:

```python
    async def group_member_add(self, account: str, group: str, members: list[str]) -> MembershipUpdated:
        return await self.call({"type": "group_member_add", "account_id_hex": account, "group_id_hex": group,
                                "members": members, "initial_admins": []},
                               "group_membership_updated", MembershipUpdated)

    async def group_member_remove(self, account: str, group: str, members: list[str]) -> MembershipUpdated:
        return await self.call({"type": "group_member_remove", "account_id_hex": account,
                                "group_id_hex": group, "members": members},
                               "group_membership_updated", MembershipUpdated)
```

- [ ] **Step 5: Implement the fake.** In `FakeWnAgent.__init__`: `self.membership_mode = "ok"` and `self.membership_delay = 0.3`. In `_handle`:

```python
            elif kind in ("group_member_add", "group_member_remove"):
                await self._membership(writer, rid, kind, req)
```

```python
    async def _membership(self, writer: asyncio.StreamWriter, rid: str, kind: str, req: dict[str, Any]) -> None:
        mode = self.membership_mode
        delta = len(req["members"]) * (1 if kind == "group_member_add" else -1)
        if mode == "hang":
            await asyncio.sleep(3600)
        if mode == "slow":
            await asyncio.sleep(self.membership_delay)
        if mode in ("ok", "slow", "fail-count"):
            self.member_count += delta
        if mode in ("fail", "fail-count"):
            await self._reply(writer, rid, {"type": "error", "code": "not_group_admin",
                                            "message": "refused", "retryable": False})
            return
        await self._reply(writer, rid, {"type": "group_membership_updated", "group_id_hex": self.group_id,
                                        "pending_welcome_count": 0})
```

(`"ok-no-count"` falls through to a success reply without changing the count.)

- [ ] **Step 6: Implement the daemon changes.**
  - `__init__`:

```python
        self.changing = False                   # a membership transition is in progress (B7)
        self.guard_lock = asyncio.Lock()        # serializes transitions with every group check
```

  - `may_post()` and `authorised()` both gain `and not self.changing`, and `_flush`'s first gate becomes `if self.latched() or not self.group_ok or not self.observing or self.changing:`.
  - Wrap the body of `check_group` in `async with self.guard_lock:`. It is never called while the lock is held: the transition and `rearm` call `group_info` directly.
  - `run()`, before `recover()` and after the B3 migration:

```python
        if self.store.get("membership_pending") is not None:      # B15: a count can't tell which change
            self.latch("a membership change was interrupted; check the group's members in your client, "
                       "then run `admind rearm` on the host")
```

  - New methods:

```python
    async def change_membership(self, op: str, name: str) -> tuple[str, str]:
        """`admind operators add|remove NAME` (ADR 0001 §8, revision 13). Returns (result, message);
        the message is admind's own wording plus the operator's policy name."""
        shown_name = show(name, False)
        async with self.guard_lock:
            if self.latched():
                return "refused", "admind is latched; check the group, then run `admind rearm` first."
            if self.store.get("membership_pending") is not None:
                return "refused", "a membership change is already pending; run `admind rearm`."
            target = {o.name: o for o in self.load_operators()}.get(name)
            if target is None:
                return "refused", (f"{shown_name} is not an operator with a marmot_npub in policy.toml "
                                   "(add it there first; remove it there only after this command).")
            try:
                count = (await self.client.group_info(self.account, self.group)).member_count
            except ControlError as exc:
                return "refused", f"could not read the group's member count ({exc.code}); nothing changed."
            expected = self.expected_members()
            if count != expected:
                self.latch(f"group has {count} members, expected {expected}")
                return "latched", f"the group has {count} members, not the expected {expected}; latched."
            to = expected + 1 if op == "add" else expected - 1
            if to < 2:
                return "refused", "the last operator can't be removed."
            pending = membership.Pending("add" if op == "add" else "remove", name, target.hex, expected, to,
                                         now())
            self.changing = True
            try:
                self.store.set("membership_pending", pending.dump())
                self.audit.write("membership", action="pending", op=pending.op, operator=shown_name,
                                 from_count=expected, to_count=to)
                reported = await self.member_call(pending)
                try:
                    after: int | None = (await self.client.group_info(self.account, self.group)).member_count
                except ControlError:
                    after = None
                outcome = membership.settle(reported, after, pending)
                if self.latched():                  # an event arrived during the transition (step 3)
                    outcome = "latch"
                return self.settle_membership(pending, outcome, reported, after)
            finally:
                self.changing = False
                self.wake.set()

    async def member_call(self, pending: membership.Pending) -> membership.Reported:
        try:
            if pending.op == "add":
                await self.client.group_member_add(self.account, self.group, [pending.member_hex])
            else:
                await self.client.group_member_remove(self.account, self.group, [pending.member_hex])
        except PeerError as exc:
            self.audit.write("membership", action="refused-by-wn-agent", code=exc.code)
            return "failed"
        except ControlError as exc:
            self.audit.write("membership", action="no-answer", code=exc.code)
            return "unknown"
        return "ok"

    def settle_membership(self, pending: membership.Pending, outcome: membership.Outcome,
                          reported: str, after: int | None) -> tuple[str, str]:
        shown_name = show(pending.name, False)
        if outcome == "commit":
            with self.store.transaction():
                self.store.set("expected_members", str(pending.to_count))
                self.store.delete("membership_pending")
                verb = "added to" if pending.op == "add" else "removed from"
                self.post(f"membership:{pending.started}:{pending.op}", f"Operator {shown_name} was {verb} the "
                          "group.", None)
            self.operators = {o.hex: o.name for o in self.load_operators()}
            self.audit.write("membership", action="committed", op=pending.op, operator=shown_name,
                             member_count=pending.to_count)
            return "committed", f"Operator {shown_name} was {verb} the group ({pending.to_count} members)."
        if outcome == "abort":
            self.store.delete("membership_pending")
            self.audit.write("membership", action="aborted", op=pending.op, operator=shown_name)
            return "aborted", "wn-agent refused the change and the member count is unchanged; nothing changed."
        self.latch(f"membership change {pending.op} ended unconfirmed (reported {reported}, "
                   f"count {'unknown' if after is None else after}, expected {pending.to_count})")
        return "latched", ("the change could not be confirmed, so admind latched. Check the group's members "
                           "in your client, then run `admind rearm` on the host.")

    async def rearm(self) -> tuple[str, str]:
        """`admind rearm`: trust the current member count and clear the latch and any pending change."""
        async with self.guard_lock:
            try:
                count = (await self.client.group_info(self.account, self.group)).member_count
            except ControlError as exc:
                return "refused", f"could not read the group's member count ({exc.code}); nothing changed."
            if count < 2:
                return "refused", f"the group has {count} member(s) and no operator; nothing changed."
            previous = self.store.get("latched")
            with self.store.transaction():
                self.store.set("expected_members", str(count))
                self.store.delete("membership_pending")
                self.store.delete("latched")
            self.audit.write("guard", action="rearm", member_count=count,
                             previous=None if previous is None else show(previous, False))
        await self.check_group()
        self.wake.set()
        return "rearmed", f"Cleared the latch; the trusted member count is now {count}."
```

  Imports: `from heterodyne.admind import membership` and `from heterodyne.marmot.control import PeerError`.

  The latch reasons above are built only from fixed words, numbers and the allowlisted `op`, as `latch()` requires.

- [ ] **Step 7: Run the gate. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): journaled membership transitions and rearm (ADR r13 §8)"
```

Review against the five transition steps, the latch and `rearm`.

---

### Task 6: Host control socket and CLI

**Files:**
- Create: `src/heterodyne/admind/ctl.py`
- Modify: `src/heterodyne/admind/daemon.py` (`run`, `on_ctl`), `src/heterodyne/admind/cli.py`
- Test: `tests/test_admind_r13_ctl.py`

**Interfaces:**
- Consumes: `Admind.change_membership` and `Admind.rearm` (Task 5).
- Produces:
  - `ctl.CTL_SOCKET = "ctl.sock"`;
  - `ctl.CtlRequest(op: Literal["add", "remove", "rearm"], name: str | None = None)`;
  - `ctl.CtlReply(result: str, message: str)`;
  - `ctl.CtlServer(path, handler, audit)`, with `start()` and `close()`;
  - `ctl.request(path, req, timeout=120.0) -> CtlReply`;
  - the CLI subcommands `admind operators add NAME`, `admind operators remove NAME` and `admind rearm`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_ctl.py
import asyncio
import os
import stat
from pathlib import Path

from heterodyne.admind import ctl
from heterodyne.admind.audit import Audit


def test_round_trip_and_mode(tmp_path: Path) -> None:
    seen: list[ctl.CtlRequest] = []

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        seen.append(req)
        return ctl.CtlReply("committed", "ok")

    async def body() -> None:
        path = tmp_path / "s" / ctl.CTL_SOCKET
        server = ctl.CtlServer(path, handler, Audit(tmp_path / "s" / "audit.jsonl"))
        await server.start()
        try:
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            reply = await ctl.request(path, ctl.CtlRequest("add", "b"))
            assert reply == ctl.CtlReply("committed", "ok")
            bad = await ctl.request(path, ctl.CtlRequest("add", "../x"))
            assert bad.result == "refused"
        finally:
            await server.close()

    asyncio.run(body())
    assert seen == [ctl.CtlRequest("add", "b")]


def test_no_daemon(tmp_path: Path) -> None:
    async def body() -> None:
        try:
            await ctl.request(tmp_path / ctl.CTL_SOCKET, ctl.CtlRequest("rearm"), timeout=1)
        except ctl.CtlUnavailable:
            return
        raise AssertionError("expected CtlUnavailable")

    asyncio.run(body())
```

CLI tests in `tests/test_admind_cli.py` style:
- with a stub server on the socket path, `main(["operators", "add", "b"])` prints the reply's message and exits 0 for `committed`, 1 otherwise;
- `main(["rearm"])` with no server exits 1 and prints "admind is not running";
- `main(["operators", "add"])` exits 2 with the fixed argparse message.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `ModuleNotFoundError: heterodyne.admind.ctl`.

- [ ] **Step 3: Implement `ctl.py`.**

```python
"""admind's host control socket (ADR 0001 §8, revision 13; plan 2b B6).

`admind operators add|remove NAME` and `admind rearm` run on the host as the service user and ask the
running daemon, which owns the wn-agent connection. One JSON request per connection, one JSON reply.
The socket is 0600 inside the 0700 state directory; anything able to use it can already act as admind.
"""

import asyncio
import contextlib
import os
import re
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.admind.audit import Audit
from heterodyne.admind.store import private_dir

CTL_SOCKET = "ctl.sock"
MAX_REQUEST = 4096
READ_SECONDS = 5.0
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class CtlRequest(msgspec.Struct, frozen=True):
    op: Literal["add", "remove", "rearm"]
    name: str | None = None


class CtlReply(msgspec.Struct, frozen=True):
    result: str
    message: str


class CtlUnavailable(Exception):
    """No daemon is listening (or it did not answer)."""


Handler = Callable[[CtlRequest], Awaitable[CtlReply]]


class CtlServer:
    def __init__(self, path: Path, handler: Handler, audit: Audit) -> None:
        self.path = path
        self.handler = handler
        self.audit = audit
        self.server: asyncio.Server | None = None

    async def start(self) -> None:
        private_dir(self.path.parent)
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISSOCK(os.lstat(self.path).st_mode):    # a stale socket from an earlier run
                self.path.unlink()
        self.server = await asyncio.start_unix_server(self._handle, path=str(self.path),
                                                      limit=MAX_REQUEST + 1)
        os.chmod(self.path, 0o600)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 2)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), READ_SECONDS)
                req = msgspec.json.decode(line, type=CtlRequest)
            except (TimeoutError, ValueError, msgspec.DecodeError):
                reply = CtlReply("refused", "malformed request")
            else:
                if req.op != "rearm" and (req.name is None or not NAME.fullmatch(req.name)):
                    reply = CtlReply("refused", "NAME must be an operator name from policy.toml")
                else:
                    self.audit.write("ctl", op=req.op, name=req.name)
                    reply = await self.handler(req)
            writer.write(msgspec.json.encode(reply) + b"\n")
            await writer.drain()
        except Exception as exc:  # noqa: BLE001 - one bad client must not end the server
            self.audit.write("ctl", action="failed", error=type(exc).__name__)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def request(path: Path, req: CtlRequest, timeout: float = 120.0) -> CtlReply:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 5)
    except (OSError, TimeoutError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    try:
        writer.write(msgspec.json.encode(req) + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        return msgspec.json.decode(line, type=CtlReply)
    except (OSError, TimeoutError, msgspec.DecodeError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
```

The `NAME` check runs before the audit, so a hostile name is never logged. Names that pass it hold only `[A-Za-z0-9._-]`.

- [ ] **Step 4: Implement the daemon side.** In `Admind`:

```python
    async def on_ctl(self, req: ctl.CtlRequest) -> ctl.CtlReply:
        if req.op == "rearm":
            result, message = await self.rearm()
        else:
            result, message = await self.change_membership(req.op, req.name or "")
        if result in ("committed", "rearmed"):
            await self.flush()          # prompts held during the transition may go now
        return ctl.CtlReply(result, message)
```

In `run()`, after `await server.start()`:

```python
            control = ctl.CtlServer(self.s.state_dir / ctl.CTL_SOCKET, self.on_ctl, self.audit)
            await control.start()
```

Close it in the `finally` next to `server.close()`. Initialise `control = None` before the `try`, and close it only if it is set.

- [ ] **Step 5: Implement the CLI.** In `build_parser`:

```python
    ops = sub.add_parser("operators", help="add or remove a group member (an operator in policy.toml)")
    ops.add_argument("action", choices=["add", "remove"])
    ops.add_argument("name")
    sub.add_parser("rearm", help="trust the group's current member count and clear the latch "
                                 "(check the members in your client first)")
```

Replace the old `rearm(s, store, audit)` with:

```python
def ask_daemon(s: AdmindSettings, req: ctl.CtlRequest) -> int:
    try:
        reply = asyncio.run(ctl.request(s.state_dir / ctl.CTL_SOCKET, req))
    except ctl.CtlUnavailable:
        print("admind is not running (or did not answer). Start it first; it starts latched if a "
              "membership change was interrupted.", file=sys.stderr)
        return 1
    print(reply.message)
    return 0 if reply.result in ("committed", "rearmed") else 1
```

`_with_settings` routes `rearm` to `ask_daemon(s, ctl.CtlRequest("rearm"))`, and `operators` to `ask_daemon(s, ctl.CtlRequest(args.action, args.name))`. Pass `args` through `_with_settings(args)` instead of just the command name. The `rearm` printout now comes from the daemon. Before running it, the operator checks the group's members in their client: the reminder stays in `--help` and in `docs/admind.md`.

- [ ] **Step 6: Run the gate and update `test_admind_cli.py`'s old rearm tests to the new behaviour. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): host control socket; operators add|remove and rearm via the daemon"
```

---

### Task 7: Summarizer runner and backstop renderer

**Files:**
- Create: `src/heterodyne/admind/summarize.py`, `src/heterodyne/admind/backstop.py`
- Modify: `src/heterodyne/agents/claude_code.py`, `src/heterodyne/admind/settings.py`, `src/heterodyne/defaults/defaults.toml`, `tests/fakes/settings.py`
- Test: `tests/test_admind_r13_summary.py`

**Interfaces:**
- Consumes: `redact` (Task 2) and the argv recorded by Task 1.
- Produces:
  - `claude_code.headless_argv(binary, profile) -> list[str]`;
  - `AdmindSettings` gains `summarizer: Mapping[str, Any] | None`, `summarizer_binary: str | None`, `reply_verbatim_lines: int` and `reply_verbatim_chars: int`;
  - from `summarize`: `SUMMARY_TIMEOUT = 60.0`, `FOOTER`, `SummaryFailed(reason)`, `needs_summary(text, lines, chars) -> bool`, and `async summarize(argv, cwd, reply, timeout) -> str`;
  - from `backstop`: `BATCH_SECONDS = 60.0`, `TITLE`, `Entry(origin, text)`, `first_words(text) -> str`, `origin(operator, at_iso, words) -> str`, `collapse(lines) -> list[str]` and `render(entries) -> str`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_summary.py
import asyncio
import stat
from pathlib import Path

import pytest

from heterodyne.admind import backstop, summarize
from heterodyne.agents.claude_code import headless_argv


def script(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "summ.sh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return [str(p)]


def test_headless_argv_has_no_tools_hooks_or_user_settings() -> None:
    argv = headless_argv("claude", {"adapter": "claude-code", "model": "m1", "args": ["--evil"]})
    assert argv[:2] == ["claude", "-p"] and "--evil" not in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert '"disableAllHooks": true' in argv[argv.index("--settings") + 1]
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv


def test_needs_summary() -> None:
    assert not summarize.needs_summary("a\n" * 8, 8, 800)
    assert summarize.needs_summary("a\n" * 9, 8, 800)
    assert summarize.needs_summary("x" * 801, 8, 800)


def test_summary_gets_the_footer_and_reads_stdin(tmp_path: Path) -> None:
    argv = script(tmp_path, 'read -r first; echo "got: $first"')
    out = asyncio.run(summarize.summarize(argv, tmp_path / "w", "long reply", timeout=5))
    assert out.startswith("got: You summarize") and out.endswith(summarize.FOOTER)


@pytest.mark.parametrize(("body", "reason"), [
    ("exit 3", "failed"), ("true", "empty"), ("sleep 5", "timeout"),
    ("i=0; while [ $i -lt 30 ]; do echo line; i=$((i+1)); done", "too-long"),
])
def test_failures(tmp_path: Path, body: str, reason: str) -> None:
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize(script(tmp_path, body), tmp_path / "w", "r", timeout=0.5))
    assert info.value.reason == reason


def test_not_configured(tmp_path: Path) -> None:
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize(None, tmp_path / "w", "r"))
    assert info.value.reason == "not-configured"


def test_collapse_counts_runs() -> None:
    assert backstop.collapse(["a", "a", "a", "b", "a"]) == ["a (×3)", "b", "a"]


def test_render_short_batch_whole() -> None:
    text = backstop.render([backstop.Entry("— op · 10:00 UTC · “hi”", "one\ntwo")])
    assert text == f"{backstop.TITLE}\n— op · 10:00 UTC · “hi”\none\ntwo"


def test_render_long_batch_head_and_tail() -> None:
    body = "\n".join(f"line {i}" for i in range(100))
    lines = backstop.render([backstop.Entry("H", body)]).splitlines()
    assert lines[0] == backstop.TITLE
    rest = lines[1:]
    assert len(rest) == 51 and rest[:10] == ["H"] + [f"line {i}" for i in range(9)]
    assert rest[10] == "… 51 lines skipped …"          # 101 lines (H + 100) - 50 shown
    assert rest[11:] == [f"line {i}" for i in range(60, 100)]


def test_counts_are_after_collapse() -> None:
    body = "\n".join(["same"] * 200)
    assert backstop.render([backstop.Entry("H", body)]) == f"{backstop.TITLE}\nH\nsame (×200)"


def test_origin_and_first_words() -> None:
    assert backstop.first_words("please restart the gateway and then check the logs now") == \
        "please restart the gateway and then check the…"
    assert backstop.origin("op", "2026-10-02T09:15:00+00:00", "hi") == "— op · 09:15 UTC · “hi”"
    assert backstop.origin(None, "2026-10-02T09:15:00+00:00", None) == \
        "— terminal · 09:15 UTC · (no operator message)"
```

Settings tests, in the same file and in `test_admind_settings.py` style:
- `summarizer = "admin"` resolves `summarizer_binary == "claude"`;
- an unknown summarizer profile is a `ConfigError` naming `[admind] summarizer`;
- `reply_verbatim_lines = 0` is a `ConfigError`;
- the defaults are 8 and 800.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `ImportError: cannot import name 'backstop'`.

- [ ] **Step 3: Implement `headless_argv`.** In `claude_code.py` (if Task 1 recorded a different no-tools flag, use that flag here and in the test):

```python
def headless_argv(binary: str, profile: Mapping[str, Any]) -> list[str]:
    """A one-shot run that reads its prompt on stdin and prints text: no tools, no hooks, no MCP
    servers, no user or local settings, no saved session. The profile's `args` are not appended: they
    could re-enable tools (plan 2b B8)."""
    argv = [binary, "-p"]
    model = profile.get("model")
    if isinstance(model, str) and model:
        argv += ["--model", model]
    return argv + ["--tools", "", "--setting-sources", "project", "--settings", '{"disableAllHooks": true}',
                   "--strict-mcp-config", "--no-session-persistence", "--output-format", "text"]
```

- [ ] **Step 4: Implement the settings.**
  - `ADMIND_KEYS` gains `"summarizer"`, `"reply_verbatim_lines"` and `"reply_verbatim_chars"`.
  - `defaults.toml` `[admind]` gains `reply_verbatim_lines = 8` and `reply_verbatim_chars = 800`.
  - Extract the profile-and-binary lookup in `resolve` into `_profile(cfg, name, key) -> tuple[Mapping[str, Any], str]`, with the same errors and `[admind] {key}` in the message. Use it for both `profile` and `summarizer`:

```python
    summarizer_name = admind.get("summarizer")
    summarizer, summarizer_binary = (None, None) if summarizer_name is None else \
        _profile(cfg, summarizer_name, "summarizer")
```

  - Pass `reply_verbatim_lines=_int(admind, "reply_verbatim_lines", 1, 200)` and `reply_verbatim_chars=_int(admind, "reply_verbatim_chars", 50, 60000)`.
  - In `tests/fakes/settings.py`, add `summarizer=None, summarizer_binary=None, reply_verbatim_lines=8, reply_verbatim_chars=800`.

- [ ] **Step 5: Implement `summarize.py`.**

```python
"""Summaries of long admin-agent replies (ADR 0001 §8, revision 13; plan 2b B8).

The summarizer is admind's own subprocess: headless, without tools or hooks, 60 s at most. Its input is
the reply after `redact`; its output is redacted again, must be non-empty and short, and gets admind's
fixed footer. Any failure is a `SummaryFailed` with a fixed reason word; the caller sends the reply to
the backstop.
"""

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from heterodyne.admind.redact import redact
from heterodyne.admind.store import private_dir

SUMMARY_TIMEOUT = 60.0
MAX_LINES = 16
MAX_CHARS = 2000
FOOTER = "summary · reply `!details` for everything"
PROMPT = """You summarize an admin agent's reply for operators who read it on a phone.
Write at most 8 short lines of plain text. Quote verbatim, in full, every question the reply asks the
operators and every error it reports. Add nothing that is not in the reply. Do not add a closing line;
one is appended for you. The reply is everything between the two marker lines.
<<<REPLY
{reply}
REPLY>>>
"""


class SummaryFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason        # fixed words only: not-configured, not-run, failed, timeout, empty, too-long


def needs_summary(text: str, lines: int, chars: int) -> bool:
    return len(text.splitlines()) > lines or len(text) > chars


def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


async def summarize(argv: list[str] | None, cwd: Path, reply: str, timeout: float = SUMMARY_TIMEOUT) -> str:
    if argv is None:
        raise SummaryFailed("not-configured")
    private_dir(cwd)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    except OSError:
        raise SummaryFailed("not-run") from None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(PROMPT.format(reply=reply).encode()), timeout)
    except TimeoutError:
        _kill(proc)
        await proc.wait()
        raise SummaryFailed("timeout") from None
    except BaseException:
        _kill(proc)
        raise
    if proc.returncode != 0:
        raise SummaryFailed("failed")
    text = redact(out.decode("utf-8", errors="replace")).strip()
    if not text:
        raise SummaryFailed("empty")
    if len(text.splitlines()) > MAX_LINES or len(text) > MAX_CHARS:
        raise SummaryFailed("too-long")
    return f"{text}\n\n{FOOTER}"
```

- [ ] **Step 6: Implement `backstop.py`.**

```python
"""The backstop batch: deterministic, drops nothing `!details` can't return (ADR 0001 §8, revision 13)."""

from dataclasses import dataclass

BATCH_SECONDS = 60.0
WHOLE_MAX = 50
HEAD = 10
TAIL = 40
TITLE = "⚠️ Replies batched (summaries unavailable) · reply `!details` for everything"
WORDS = 8
WORDS_CHARS = 60


@dataclass(frozen=True)
class Entry:
    origin: str
    text: str


def first_words(text: str) -> str:
    words = text.split()
    shown = " ".join(words[:WORDS])
    if len(words) > WORDS or len(shown) > WORDS_CHARS:
        shown = shown[:WORDS_CHARS].rstrip() + "…"
    return shown


def origin(operator: str | None, at_iso: str, words: str | None) -> str:
    who = operator or "terminal"
    said = f"“{words}”" if words else "(no operator message)"
    return f"— {who} · {at_iso[11:16]} UTC · {said}"


def collapse(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        k = j - i + 1
        out.append(lines[i] if k == 1 else f"{lines[i]} (×{k})")
        i = j + 1
    return out


def render(entries: list[Entry]) -> str:
    lines: list[str] = []
    for e in entries:
        lines.append(e.origin)
        lines.extend(e.text.splitlines() or [""])
    lines = collapse(lines)
    if len(lines) > WHOLE_MAX:
        lines = lines[:HEAD] + [f"… {len(lines) - HEAD - TAIL} lines skipped …"] + lines[-TAIL:]
    return "\n".join([TITLE, *lines])
```

  (`origin` takes the first words already redacted and shortened by `first_words`.)

- [ ] **Step 7: Run the gate. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): summarizer runner and backstop renderer (ADR r13 §8)"
```

---

### Task 8: The reply pipeline in the daemon

**Files:**
- Modify: `src/heterodyne/admind/store.py`, `src/heterodyne/admind/hook.py`, `src/heterodyne/admind/daemon.py`
- Test: `tests/test_admind_r13_replies.py`

**Interfaces:**
- Consumes: Tasks 2, 4 and 7.
- Produces:
  - Store: `record_prompt(mid, operator, words)`, `prompt(mid) -> PromptRow | None`, `add_turn(...) -> int`, `turn(turn_id) -> TurnRow | None`, `turns_with_status(status) -> list[TurnRow]`, `set_turn_status(turn_id, status, batch_id=None)`, `open_batch(now: float) -> int`, `due_batches(now: float, seconds: float) -> list[int]`, `batch_turns(batch_id) -> list[TurnRow]`, `close_batch(batch_id)`, `record_post(key, kind, turn_id, batch_id)` and `details_target(message_id: str | None) -> PostRow | None`.
  - Hook module: `transcript_size(ev: HookEvent) -> int | None`.
  - Daemon: `Admind.summaries: asyncio.Queue[int]`, `summary_loop()`, `summarize_turn(turn_id)`, `to_backstop(turn_id)`, `batch_loop()` and `close_due_batches()`, plus the replaceable attributes `summarizer_argv`, `summary_timeout`, `batch_seconds`, `batch_poll` and `wallclock`.

- [ ] **Step 1: Write the failing tests.** Use the shared harness. Set a fake summarizer with `h.daemon.summarizer_argv = script(...)`, and set `h.daemon.batch_seconds = 0.3` and `h.daemon.batch_poll = 0.05`. The fake claude's `Stop` carries the reply given in `last_assistant_message`; reuse the existing daemon-test pattern that sends a prompt and fires a `Stop`. Scenarios:

1. A short reply (≤8 lines, ≤800 chars) is posted verbatim, threaded to the prompt, with key `reply:<session>:<n>:0`. A `turns` row with status `verbatim` and a `posts` row of kind `verbatim` exist.
2. A long reply with summarizer `echo "short summary"` is posted as one message, `"short summary\n\n" + FOOTER`, threaded to the prompt. The long text itself is never sent. The turn's status is `summarized`.
3. With the summarizer failing (`exit 1`), timing out (`sleep 5` with `summary_timeout=0.3`), returning nothing (`true`), or not configured (`None`), each case leads to one unthreaded message within `batch_seconds`. It starts with `backstop.TITLE`, contains the origin header `— op · HH:MM UTC · “<first words>”`, and contains the reply. The audit has `{"kind": "summary", "action": "failed", "reason": <word>}` and `{"kind": "backstop", "action": "sent"}`.
4. Two failing long replies within one window produce **one** batch holding both, in order, each under its own origin.
5. Restart: a turn left `summarizing` (insert it with `add_turn` before `run()`) is summarized after startup.
6. A hex value in the reply never appears in any sent text.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `AttributeError: 'Admind' object has no attribute 'summarizer_argv'`.

- [ ] **Step 3: Implement the store.** Append to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS prompts (
    message_id TEXT PRIMARY KEY, operator TEXT NOT NULL, received_at TEXT NOT NULL, words TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
    turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    session TEXT NOT NULL,
    reply_to TEXT,
    origin TEXT NOT NULL,
    text TEXT NOT NULL,
    transcript TEXT,
    transcript_end INTEGER,
    status TEXT NOT NULL CHECK (status IN ('verbatim', 'summarizing', 'summarized', 'batched')),
    batch_id INTEGER,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'sent')));
CREATE TABLE IF NOT EXISTS posts (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('verbatim', 'summary', 'batch')),
    turn_id INTEGER,
    batch_id INTEGER);
```

Row types and methods (each `@_locked`):

```python
@dataclass(frozen=True)
class PromptRow:
    operator: str
    received_at: str
    words: str


@dataclass(frozen=True)
class TurnRow:
    turn_id: int
    key: str
    session: str
    reply_to: str | None
    origin: str
    text: str
    transcript: str | None
    transcript_end: int | None
    status: str
    batch_id: int | None


@dataclass(frozen=True)
class PostRow:
    key: str
    kind: str
    turn_id: int | None
    batch_id: int | None


_TURN = ("turn_id, key, session, reply_to, origin, text, transcript, transcript_end, status, batch_id")


def _turn(r: tuple[object, ...]) -> TurnRow:
    return TurnRow(int(r[0]), str(r[1]), str(r[2]), None if r[3] is None else str(r[3]), str(r[4]), str(r[5]),
                   None if r[6] is None else str(r[6]), None if r[7] is None else int(r[7]), str(r[8]),
                   None if r[9] is None else int(r[9]))
```

```python
    @_locked
    def record_prompt(self, message_id: str, operator: str, words: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO prompts(message_id, operator, received_at, words) "
                        "VALUES (?, ?, ?, ?)", (message_id, operator, now(), words))

    @_locked
    def prompt(self, message_id: str) -> PromptRow | None:
        r = self.db.execute("SELECT operator, received_at, words FROM prompts WHERE message_id = ?",
                            (message_id,)).fetchone()
        return None if r is None else PromptRow(str(r[0]), str(r[1]), str(r[2]))

    @_locked
    def add_turn(self, key: str, session: str, reply_to: str | None, origin: str, text: str,
                 transcript: str | None, transcript_end: int | None, status: str) -> int:
        self.db.execute("INSERT OR IGNORE INTO turns(key, session, reply_to, origin, text, transcript, "
                        "transcript_end, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, session, reply_to, origin, text, transcript, transcript_end, status, now()))
        return int(self.db.execute("SELECT turn_id FROM turns WHERE key = ?", (key,)).fetchone()[0])

    @_locked
    def turn(self, turn_id: int) -> TurnRow | None:
        r = self.db.execute(f"SELECT {_TURN} FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        return None if r is None else _turn(r)

    @_locked
    def turns_with_status(self, status: str) -> list[TurnRow]:
        return [_turn(r) for r in self.db.execute(f"SELECT {_TURN} FROM turns WHERE status = ? ORDER BY turn_id",
                                                  (status,)).fetchall()]

    @_locked
    def set_turn_status(self, turn_id: int, status: str, batch_id: int | None = None) -> None:
        self.db.execute("UPDATE turns SET status = ?, batch_id = ? WHERE turn_id = ?", (status, batch_id, turn_id))

    @_locked
    def open_batch(self, at: float) -> int:
        """The open batch, or a new one opened at `at` (a batch opens with its first reply)."""
        r = self.db.execute("SELECT batch_id FROM batches WHERE status = 'open' ORDER BY batch_id LIMIT 1").fetchone()
        if r is not None:
            return int(r[0])
        return int(self.db.execute("INSERT INTO batches(opened_at, status) VALUES (?, 'open')", (at,)).lastrowid or 0)

    @_locked
    def due_batches(self, at: float, seconds: float) -> list[int]:
        rows = self.db.execute("SELECT batch_id FROM batches WHERE status = 'open' AND opened_at + ? <= ? "
                               "ORDER BY batch_id", (seconds, at)).fetchall()
        return [int(r[0]) for r in rows]

    @_locked
    def batch_turns(self, batch_id: int) -> list[TurnRow]:
        return [_turn(r) for r in self.db.execute(f"SELECT {_TURN} FROM turns WHERE batch_id = ? ORDER BY turn_id",
                                                  (batch_id,)).fetchall()]

    @_locked
    def close_batch(self, batch_id: int) -> None:
        self.db.execute("UPDATE batches SET status = 'sent' WHERE batch_id = ?", (batch_id,))

    @_locked
    def record_post(self, key: str, kind: str, turn_id: int | None, batch_id: int | None) -> None:
        self.db.execute("INSERT OR IGNORE INTO posts(key, kind, turn_id, batch_id) VALUES (?, ?, ?, ?)",
                        (key, kind, turn_id, batch_id))

    @_locked
    def details_target(self, message_id: str | None) -> PostRow | None:
        """The record behind a posted message (B12), or the latest summary or batch without a target."""
        if message_id is not None:
            r = self.db.execute("SELECT p.key, p.kind, p.turn_id, p.batch_id FROM outbox o JOIN posts p "
                                "ON p.key = o.key WHERE o.message_id = ?", (message_id,)).fetchone()
        else:
            r = self.db.execute("SELECT p.key, p.kind, p.turn_id, p.batch_id FROM posts p JOIN outbox o "
                                "ON o.key = p.key WHERE p.kind IN ('summary', 'batch') "
                                "ORDER BY o.seq DESC LIMIT 1").fetchone()
        return None if r is None else PostRow(str(r[0]), str(r[1]), None if r[2] is None else int(r[2]),
                                              None if r[3] is None else int(r[3]))
```

- [ ] **Step 4: Implement `transcript_size` in `hook.py`.**

```python
def transcript_size(ev: HookEvent) -> int | None:
    """The size of this session's transcript now (at its Stop), so `!details full` can later read exactly
    this turn (plan 2b B13). Same path rules as the reply fallback; never follows a symlink or blocks."""
    if not ev.transcript_path:
        return None
    path = Path(ev.transcript_path)
    if path.name != f"{ev.session_id}.jsonl" or not path.is_absolute():
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        return st.st_size if stat.S_ISREG(st.st_mode) else None
    finally:
        os.close(fd)
```

- [ ] **Step 5: Implement the daemon changes.**
  - Imports: `from heterodyne.admind import backstop, summarize` and `from heterodyne.agents.claude_code import headless_argv`.
  - `__init__`:

```python
        self.summaries: asyncio.Queue[int] = asyncio.Queue()
        self.summarizer_argv: list[str] | None = (
            None if settings.summarizer is None or settings.summarizer_binary is None
            else headless_argv(settings.summarizer_binary, settings.summarizer))
        self.summary_timeout = summarize.SUMMARY_TIMEOUT    # replaced in tests
        self.batch_seconds = backstop.BATCH_SECONDS         # replaced in tests
        self.batch_poll = 1.0                               # replaced in tests
        self.wallclock: Callable[[], float] = time.time     # replaced in tests; batches persist across restarts
```

  - In `on_message`, right after the `inbound` audit record, add `self.store.record_prompt(mid, verdict.operator or "?", backstop.first_words(redact(text)))`.
  - `run()` adds two loops to the task group, `("summaries", self.summary_loop)` and `("batches", self.batch_loop)`. Before the task group starts, requeue: `for row in self.store.turns_with_status("summarizing"): self.summaries.put_nowait(row.turn_id)`.
  - `on_stop`:
    - Before the second `async with self.turn_lock()`, add `end = await self.transcript_end(ev)`.
    - Inside that lock, replace the posting block from `text = raw if raw.strip() else NO_REPLY` to the `with self.store.transaction():` that posts the parts, keeping the `if current:` clean-up exactly as it is:

```python
            text = redact(raw if raw.strip() else NO_REPLY)
            reply_seq = int(self.store.get("reply_seq") or "0") + 1
            self.store.set("reply_seq", str(reply_seq))
            reply_to = anchor if current else None
            key = f"reply:{ev.session_id}:{reply_seq}"
            verbatim = not summarize.needs_summary(text, self.s.reply_verbatim_lines, self.s.reply_verbatim_chars)
            parts = chunk.split(text, self.s.chunk_chars) if verbatim else []
            with self.store.transaction():      # reply record, its post, cleared turn and idle state: one step
                turn_id = self.store.add_turn(key, ev.session_id, reply_to, self.origin_for(reply_to), text,
                                              ev.transcript_path, end, "verbatim" if verbatim else "summarizing")
                for i, part in enumerate(parts):
                    self.post(f"{key}:{i}", part, reply_to)
                    self.store.record_post(f"{key}:{i}", "verbatim", turn_id, None)
                if current:
                    ...                         # unchanged
            if not verbatim:
                self.summaries.put_nowait(turn_id)
```

    - The final `reply` audit record gains `mode="verbatim" if verbatim else "summary"` and keeps `chars` and `chunks=len(parts)`.
  - New helpers:

```python
    async def transcript_end(self, ev: HookEvent) -> int | None:
        try:
            return await asyncio.wait_for(asyncio.to_thread(transcript_size, ev), 2.0)
        except TimeoutError:
            return None

    def origin_for(self, mid: str | None) -> str:
        row = None if mid is None else self.store.prompt(mid)
        if row is None:
            return backstop.origin(None, now(), None)
        return backstop.origin(row.operator, row.received_at, row.words)

    async def summary_loop(self) -> None:
        while True:
            await self.summarize_turn(await self.summaries.get())

    async def summarize_turn(self, turn_id: int) -> None:
        row = self.store.turn(turn_id)
        if row is None or row.status != "summarizing":
            return
        try:
            text = await summarize.summarize(self.summarizer_argv, self.s.state_dir / "summarizer", row.text,
                                             self.summary_timeout)
            with self.store.transaction():
                for i, part in enumerate(chunk.split(text, self.s.chunk_chars)):
                    self.post(f"{row.key}:s{i}", part, row.reply_to)
                    self.store.record_post(f"{row.key}:s{i}", "summary", turn_id, None)
                self.store.set_turn_status(turn_id, "summarized")
        except summarize.SummaryFailed as exc:
            self.audit_quietly("summary", action="failed", reason=exc.reason, turn=turn_id)
            self.to_backstop(turn_id)
        except Exception as exc:  # noqa: BLE001 - any pipeline failure goes to the backstop (§8)
            self.audit_quietly("summary", action="failed", reason="internal", error=type(exc).__name__,
                               turn=turn_id)
            self.to_backstop(turn_id)
        else:
            self.audit_quietly("summary", action="sent", turn=turn_id)

    def to_backstop(self, turn_id: int) -> None:
        with self.store.transaction():
            batch = self.store.open_batch(self.wallclock())
            self.store.set_turn_status(turn_id, "batched", batch)
        self.audit_quietly("backstop", action="queued", turn=turn_id, batch=batch)

    async def batch_loop(self) -> None:
        while True:
            await self._sleep(self.batch_poll)
            self.close_due_batches()

    def close_due_batches(self) -> None:
        for batch in self.store.due_batches(self.wallclock(), self.batch_seconds):
            rows = self.store.batch_turns(batch)
            text = redact(backstop.render([backstop.Entry(r.origin, r.text) for r in rows]))
            parts = chunk.split(text, self.s.chunk_chars)
            with self.store.transaction():
                for i, part in enumerate(parts):
                    self.post(f"batch:{batch}:{i}", part, None)
                    self.store.record_post(f"batch:{batch}:{i}", "batch", None, batch)
                self.store.close_batch(batch)
            self.audit_quietly("backstop", action="sent", batch=batch, replies=len(rows),
                               lines=len(text.splitlines()), chunks=len(parts))
```

  (`transcript_size` is imported from `hook`. `origin_for` uses `now()` for a turn begun at the terminal.)

- [ ] **Step 6: Run the gate.** Existing tests that expect a long reply to come back verbatim must be adapted. They set `reply_verbatim_lines` and `reply_verbatim_chars` high in their settings (`make_settings(..., reply_verbatim_lines=200, reply_verbatim_chars=60000)`), which keeps their intent: those tests are about turn state, not summaries.

- [ ] **Step 7: Commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): summarized replies with a batching backstop (ADR r13 §8)"
```

---

### Task 9: `!details` and `!details full`

**Files:**
- Modify: `src/heterodyne/admind/commands.py`, `src/heterodyne/admind/hook.py`, `src/heterodyne/admind/daemon.py` (`on_message`, `handle`, new `details`)
- Test: `tests/test_admind_r13_details.py`

**Interfaces:**
- Consumes: the Task 8 records, lane 2 (Task 4), and `redact`.
- Produces:
  - `commands.Command("details", arg="full" | None)`;
  - `hook.turn_tool_calls(path: Path, end: int, limit: int = MAX_TRANSCRIPT) -> str`;
  - `Admind.details(mid, cmd, target)`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_details.py
import json
from pathlib import Path

import pytest

from heterodyne.admind import commands
from heterodyne.admind.hook import turn_tool_calls


def test_parse_details() -> None:
    assert commands.parse("!details") == commands.Command("details")
    assert commands.parse("!details full") == commands.Command("details", arg="full")
    with pytest.raises(commands.CommandError):
        commands.parse("!details everything")
    assert "!details [full]" in commands.HELP


def write(path: Path, records: list[dict[str, object]]) -> int:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path.stat().st_size


def test_tool_calls_of_this_turn_only(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = write(t, [
        {"type": "user", "message": {"content": "old prompt"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Old", "input": {}}]}},
        {"type": "user", "message": {"content": "check the gateway"}},
        {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "secret plan"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "systemctl --user status x"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "active (running)"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "It is running."}]}},
    ])
    with t.open("a") as fh:                            # the next turn, after this Stop
        fh.write(json.dumps({"type": "user", "message": {"content": "later"}}) + "\n")
    out = turn_tool_calls(t, end)
    assert "▸ Bash" in out and "systemctl --user status x" in out and "◂ active (running)" in out
    assert "Old" not in out and "secret plan" not in out and "later" not in out


def test_no_tool_calls(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = write(t, [{"type": "user", "message": {"content": "hi"}},
                    {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}])
    assert turn_tool_calls(t, end) == "(no tool calls in this turn)"
```

Daemon scenarios with the shared harness:

1. After a summarized reply, a `!details` that **replies to the summary's message ID** (`h.fake.message_event(..., reply_to=<summary id from h.fake.sent / outbox>)`) returns the full redacted reply under its origin. It is chunked at `chunk_chars`, posted in lane 2 and threaded to the `!details` message.
2. `!details` without a reply target expands the latest summary or batch.
3. On a batch, it returns every reply in order, each under its origin.
4. `!details full` adds the turn's tool calls (from a transcript the fake writes) and never shows thinking.
5. `!details` replying to a message with no record (for example `READY_NOTICE`) answers "!details: that message has no details. Reply to a summary or a batch." and posts nothing in lane 2.
6. While a long `!details` is still in lane 2, an alert relayed meanwhile is sent before the remaining chunks. Use `chunk_chars=200` and a long reply.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `CommandError: Unknown command !details`.

- [ ] **Step 3: Implement the command parsing.** In `commands.py`:
  - `HELP = "admind commands: !new · !interrupt · !tail [n] · !restart <unit> · !ps · !details [full]"`;
  - `CommandName` gains `"details"`;
  - in `parse`:

```python
    if name == "details":
        if args not in ([], ["full"]):
            raise CommandError("Usage: !details [full], as a reply to a summary or batch (or alone, for the latest).")
        return Command("details", arg="full" if args else None)
```

  `CommandRunner.run` never receives `details`, because the daemon handles it. Raise `CommandError("internal")` there if it ever does.

- [ ] **Step 4: Implement `turn_tool_calls` in `hook.py`.**

```python
def _read_range(path: Path, end: int, limit: int) -> str | None:
    """Bytes [end - limit, end) of a regular file, opened without following symlinks or blocking."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        start = max(0, end - limit)
        raw = os.pread(fd, end - start, start)
    except OSError:
        return None
    finally:
        os.close(fd)
    if start:
        raw = raw.partition(b"\n")[2]
    return raw.decode("utf-8", errors="replace")


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") if isinstance(b, dict) and b.get("type") == "text" else "[non-text result]"
                 for b in cast(list[Any], content)]
        return "\n".join(p for p in parts if isinstance(p, str))
    return "[non-text result]"


def turn_tool_calls(path: Path, end: int, limit: int = MAX_TRANSCRIPT) -> str:
    """The tool calls and results of the turn that ended at byte `end` (plan 2b B13): from that turn's
    last real user prompt on. Thinking is never read out."""
    content = _read_range(path, end, limit)
    if content is None:
        return "(the transcript could not be read)"
    out: list[str] = []
    for line in content.splitlines():
        try:
            record: Any = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        kind = cast(dict[str, Any], record).get("type")
        blocks = _blocks(cast(dict[str, Any], record))
        if kind == "user":
            if isinstance(blocks, str) or (blocks and any(b.get("type") == "text" for b in blocks)):
                out = []                                    # a real prompt starts the turn
                continue
            for b in blocks or []:
                if b.get("type") == "tool_result":
                    out.append(f"◂ {_result_text(b.get('content'))}")
        elif kind == "assistant" and isinstance(blocks, list):
            for b in blocks:
                if b.get("type") == "tool_use":
                    out.append(f"▸ {b.get('name')} {json.dumps(b.get('input'), ensure_ascii=False)}")
    return "\n".join(out) if out else "(no tool calls in this turn)"
```

- [ ] **Step 5: Implement the daemon changes.**
  - `on_message` passes `target = ev.reply_to.message_id_hex.lower() if ev.reply_to is not None else None` to `self.handle(mid, text, target)`.
  - `handle(self, mid, text, target=None)`, after `self.store.set_inbound(mid, "executing")`:

```python
            if cmd.name == "details":
                await self.details(mid, cmd, target)
                return
```

  - New method:

```python
    async def details(self, mid: str, cmd: commands.Command, target: str | None) -> None:
        """`!details [full]`: the full redacted reply (or every reply of a batch), each under its origin,
        in lane 2 and threaded to the command (ADR 0001 §8, revision 13)."""
        if not self.authorised():
            self.deny(mid, "command")
            return
        post = self.store.details_target(target)
        if post is None:
            why = ("that message has no details. Reply to a summary or a batch." if target is not None
                   else "there is no summary or batch yet.")
            self.finish(mid, "done", f"!details: {why}", "cmd")
            return
        rows = ([r for r in [self.store.turn(post.turn_id)] if r is not None] if post.turn_id is not None
                else self.store.batch_turns(post.batch_id or 0))
        full = cmd.arg == "full"
        sections: list[str] = []
        for row in rows:
            body = row.text
            if full:
                body += "\n\n" + await self.tool_calls(row)
            sections.append(f"{row.origin}\n{body}")
        text = redact("\n\n".join(sections))
        with self.store.transaction():
            self.store.set_inbound(mid, "done")
            for i, part in enumerate(chunk.split(text, self.s.chunk_chars)):
                self.post(f"details:{mid}:{i}", part, mid, lane=2)
        self.audit.write("command", message_id=mid, command="details", full=full, replies=len(rows),
                         chars=len(text))

    async def tool_calls(self, row: TurnRow) -> str:
        if row.transcript is None or row.transcript_end is None:
            return "(tool calls are not available for this turn)"
        path = Path(row.transcript)
        if path.name != f"{row.session}.jsonl" or not path.is_absolute():
            return "(tool calls are not available for this turn)"
        try:
            return await asyncio.wait_for(asyncio.to_thread(turn_tool_calls, path, row.transcript_end),
                                          self.extract_timeout)
        except TimeoutError:
            return "(the transcript could not be read in time)"
```

  (Import `TurnRow` from the store and `turn_tool_calls` from the hook module. The paths stored for a turn passed the same session-name rule when it was recorded.)

- [ ] **Step 6: Run the gate. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): !details and !details full in the second delivery lane (ADR r13 §8)"
```

---

### Task 10: Docs, ADR sync and the PR

**Files:**
- Modify: `docs/admind.md`, `docs/configuration.md`, `docs/security-model.md`
- Replace: `docs/adr/0001-workstreams-v2.md`, byte-identical with the design repo at `66b3aec`
- Create: `docs/reviews/0001-design-review-r18.md`, `-r19.md` and `-r20.md`; update `docs/reviews/0001-design-review-r1-response.md` from the design repo

- [ ] **Step 1: Sync the ADR and review records.**

```bash
D=~/repos/hermes-workstreams-v2; git -C "$D" rev-parse HEAD   # must print 66b3aecb639e6ec56f108e2f55d483d4dedda485 on branch design
for f in docs/adr/0001-workstreams-v2.md docs/reviews/0001-design-review-r18.md docs/reviews/0001-design-review-r19.md \
         docs/reviews/0001-design-review-r20.md docs/reviews/0001-design-review-r1-response.md; do
  git -C "$D" show 66b3aec:"$f" > "$f"
done
cmp <(git -C "$D" show 66b3aec:docs/adr/0001-workstreams-v2.md) docs/adr/0001-workstreams-v2.md && echo identical
```

- [ ] **Step 2: Update `docs/admind.md`.** Add these sections:
  - **Operators:** every `policy.toml` operator with a `marmot_npub`; adding and removing them with `admind operators add|remove NAME`.
  - **Temporary operators,** step by step: add the entry to `policy.toml`, run `admind operators add NAME`, run the test, run `admind operators remove NAME`, then delete the entry.
  - **The latch and `admind rearm`:** check the members in your client first; rearm needs the daemon running.
  - **Replies:** verbatim up to 8 lines and 800 characters; summaries with the footer; the backstop batch, with its title, origins, `(×k)` and skipped-lines line; `!details` and `!details full`; the two lanes.
  - **Audit:** whole messages, redacted, with operator names, and the new record kinds `membership`, `summary`, `backstop` and `ctl`.
  - **Upgrading from plan 2:** the `expected_members` migration, and that a pending record latches on startup.

- [ ] **Step 3: Update `docs/configuration.md`.** Document `summarizer`, `reply_verbatim_lines` and `reply_verbatim_chars`, with defaults and the claude-code-only note. Update `docs/security-model.md`:
  - the redaction applies to everything posted (B1);
  - the summarizer runs without tools, hooks, MCP servers or user settings (B8);
  - the control socket's trust boundary (B6);
  - the residual count-preserving-swap risk, unchanged.

- [ ] **Step 4: Run the full gate. Then commit and push.**

```bash
git add docs
git commit -m "docs(admind): revision 13 operators, membership, replies, details; sync ADR 0001 r13"
git push -u origin plan-2b-admind
gh pr create --base main --head plan-2b-admind --title "admind: ADR 0001 revision 13 (plan 2b)" \
  --body "Implements ADR 0001 r13 §8 (66b3aec, approved btq-5ky39): several operators, journaled membership, one redaction, summarized replies with a batching backstop, !details [full], two delivery lanes, untruncated audit. Per-task cross-model reviews are recorded on the plan 2b beads."
```

The PR body ends with the attribution lines the session requires. Merging is the operator's action: report the **full PR URL** to the operator.

- [ ] **Step 5: Hand over to acceptance.** Re-scope `btq-llc3n` (operator acceptance) as orchestrator. It is unassigned and blocked, so park it per PICKUP "Orchestrating the task graph", then make these changes and note them on the bead:
  - acceptance steps, run while the operator watches:
    - add a generated test key with `admind operators add`;
    - byte-exact passthrough from the test key;
    - a long reply summarized;
    - a forced summarizer failure leading to a batch;
    - `!details` and `!details full`;
    - `admind operators remove`;
    - a deliberate latch and `rearm`;
  - a dependency on this task's bead, plus a note that results go into a follow-up PR from `main` after the merge.

## Self-review notes (plan author)

- **Spec coverage (§8 r13):**

| §8 requirement | Task |
|---|---|
| Operators | 3 |
| Ingress | 3 (existing replay and group checks kept) |
| Join signal | unchanged; generic |
| Membership init | 3 |
| add/remove via the daemon | 5 and 6 |
| Transition steps 1–5 | 5 |
| rearm | 5 and 6 |
| Verify wn-agent first | 1 |
| Latch | 3 and 5 |
| Temporary operators | 10 docs, plus the acceptance in `btq-llc3n` |
| Redaction | 2 |
| Replies, verbatim | 8 |
| Summaries | 7 and 8 |
| Backstop | 7 and 8 |
| `!details` | 8 and 9 |
| `!details full` | 9 |
| Records across restarts | 8 |
| Lanes | 4 |
| Audit | 2, plus the new kinds in 5, 6 and 8 |
| Commands | 9 |
| §11 tests | each task |

- **Type consistency:**
  - `Operator.hex` is used by guard, daemon and CLI.
  - `PostRow` and `TurnRow` are defined in Task 8 and used in Task 9.
  - `post(..., lane=)` is defined in Task 2 and stored in Task 4.
  - `summarizer_argv` is defined in Task 8 and is `None` when not configured.
