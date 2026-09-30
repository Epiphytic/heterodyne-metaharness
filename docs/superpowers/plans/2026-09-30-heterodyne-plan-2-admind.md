# heterodyne-metaharness Plan 2: `admind` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship `admind`, the independent admin override channel. It has its own Marmot identity in a two-member group with the operator. Operator text goes byte-for-byte to a persistent admin agent session, and replies come back as thread replies. It also provides deterministic `!` commands, an append-only audit log, and relaying of `wsd`'s local alert files (ADR §8, §6.2).

**Architecture:** `admind` is one asyncio process under its own service unit. It supervises a private `wn-agent` child with its own home, socket and bearer token. It talks to that child over the `marmot.agent-control.v2` NDJSON protocol that spike S4 confirmed, and decodes every inbound frame strictly with `msgspec`. The admin agent is an interactive `claude` session in a private tmux server. Operator text is pasted into it; the agent's replies come from its `Stop` hook, never from screen scraping. SQLite in WAL mode holds replay protection, an outbox with idempotency keys, alert relay state and the agent session ID. A separate JSONL file is the append-only audit log.

**Tech Stack:** Python 3.12+, asyncio, sqlite3, msgspec (first runtime dependency, ADR §16), tmux 3.x, `wn-agent` 0.10.x (external; not vendored), Claude Code 2.1.x (external), systemd user units; pytest, hypothesis, ruff, pyright (strict on `src/`).

**Spec:** ADR 0001 revision 12, design-repo commit `d6e997c271eaba25b587012b1447d9c58a582ce3`, approved in bead `btq-freh` (synced byte-identical to `$HZ/docs/adr/0001-workstreams-v2.md`). Section numbers (§) refer to it. The Marmot wire shapes are in `$HZ/docs/spikes/S4-marmot.md`, and the plan-2 acceptance items are in `$HZ/docs/spikes/GATE.md`.

## Global Constraints

- **Variables:** `$HZ` and `$BTQ_REPO` as defined in the roadmap. No step may write an install path, npub, hex account or group ID, token, relay URL or unit name of the reference install into a committed file.
- **Live services are off-limits:** never stop, restart, reconfigure or open the home of the running `wn-agent-*`, `hermes-*` or `hermes-workstreams*` units, or any Marmot home other than the new admind home created in Task 10. Tests never touch the network, a real `wn-agent`, the real `systemctl`, or the operator's `~/.claude`.
- **Old harness is off-limits:** as in plan 1, never modify the old harness checkout or any symlink to it.
- **Python** `>=3.12`, managed with `uv`. **Runtime dependencies: exactly `msgspec`.** Dev dependencies are unchanged (`pytest`, `hypothesis`, `ruff`, `pyright`). Async tests use `asyncio.run` inside sync test functions; no pytest-asyncio.
- **`sys.platform`** appears only in `src/heterodyne/platform.py` (§3.2). Service control goes through `heterodyne.services`, which is chosen by the host config's `platform.service_manager`.
- **No model names or agent pairings** in `src/` (§4.1). Test fixtures use made-up model names such as `m1`.
- **Install-agnostic repo** (§15): `scripts/check_install_agnostic.py` must stay clean. npub literals may appear only under `tests/fixtures/`. Tests build npubs at runtime with `hex_to_npub`.
- **Config keys must not trip the secret scanner.** A key whose name contains `auth`, `token`, `session`, `secret`, `key` (final segment) and so on must hold a `{ file = … }` or `{ command = … }` reference. This plan's new keys avoid those words. A one-key table whose only key is `command` or `file` is treated as a secret reference, which is why the adapter executable key is `binary`.
- **Never print tokens or npubs.** Errors name the config key, not the value. `admind init` suppresses `wn-agent bootstrap` output, which contains invite details.
- **Review rule** (§11.1): every code task ends with a review by a **different LLM than the implementer**.
  - If the implementer is Claude: `codex exec -m gpt-6-sol -c model_reasoning_effort=medium -s read-only -o /tmp/review.md "<brief>"`.
  - If the implementer is Codex/GPT: `claude -p --model claude-opus-5-5 --permission-mode plan "<brief>" > /tmp/review.md`.
  - The brief names the diff range and the ADR sections, and asks for `[BLOCKING]`/`[NON-BLOCKING]` findings. Fix or rebut every blocking finding.
  - The close evidence includes `Code-Review: reviewer=<model> author=<model> mode=cross-model range=<BASE>..<HEAD>`.
- **Beads:** each task below is one bead in workstream `heterodyne`.
  - Code tasks (1–9, 11) are `kind:task` with `metadata.design_approval=btq-freh` and `metadata.adr_revision=d6e997c271eaba25b587012b1447d9c58a582ce3`, plus a blocking link to `btq-freh`.
  - Task 10 (live acceptance) is `kind:research`.
  - Existing beads are not touched (they wait for plan 9).
- **Commits:** at every green step, on branch `plan-2-admind` in `$HZ`. Nothing is pushed until Task 11. Merging is the operator's action.
- **Gate before each commit:** `uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`, all clean.

## Decisions made in this plan (within the ADR; reviewers should check them)

| # | Decision | Why | ADR |
|---|---|---|---|
| D1 | `admind` spawns and supervises its own `wn-agent` child in the same unit. The child has its own home (0700), its socket under `<home>/ctl/`, and a bearer token file (0600) that admind generates. | §8 says "its own Marmot identity and connection" and "keys readable only by the admind unit". One unit owning the child is the closest match. S4 showed that a `wn-agent` home cannot be shared with another process. | §8 |
| D2 | The admin agent's reply is the `Stop` hook's `last_assistant_message` (optional in Claude Code 2.1.283's hook schema). If that is absent, admind falls back to the last assistant text block in the session transcript that the hook names. | §2: agent state comes from hooks, never screen scraping. `!tail` is the only pane read, and it is an explicit operator command. | §2, §8 |
| D3 | Operator text containing C0/C1 control characters (other than tab and newline) is **refused with a reply**, never altered. Text starting with `!` is always a command, and an unknown `!word` is an error reply. | Byte-for-byte means never modified. A control character can break out of bracketed paste. A leading `!` switches Claude Code's input box to bash mode. A mistyped `!restrat` must not reach the agent. | §8 |
| D4 | Any membership or admin change event, or a member count other than 2, **latches** admind: every inbound message is dropped and nothing is posted. The latch holds until the operator runs `admind rearm` locally. | §8: it "refuses to operate if the group has more than two members". `group_info` gives a count, not a member list (S4), so a swap that keeps the count at 2 is visible only as an event. Posting while latched would leak to whoever joined. | §3.4, §8 |
| D5 | The S4 "join signal" for admind is the **first MLS-authenticated message from the operator** in the group. Until it arrives, admind posts nothing: no ready notice and no alerts. After it, admind posts the ready notice and releases held alerts. | GATE.md: messages sent before a member joins are invisible to them, and no other join signal is established. Task 10 verifies this live. | GATE.md |
| D6 | Processing is at most once. The message ID is claimed before dispatch. A message claimed but not delivered when admind stopped gets a "resend if still needed" reply after restart, and is never replayed to the agent. | Replaying a prompt, or `!restart`, twice is worse than asking the operator to resend. | §3.4 (replay) |
| D7 | The admin agent supports the `claude-code` adapter only in this plan. A `codex` admin profile is a config error with a clear message. | §8 says any adapter can be configured. The Codex adapter and its hook questions (S1 unsettled) belong to plan 4, which adds Codex support to `admind` (carry-forward below). | §4.2, §8 |
| D8 | The agent session is persistent through its ID: admind records it, adopts a live tmux session after a restart, resumes with `--resume <id>` after a crash, and starts fresh only on `!new`. Three launches in a row without a `SessionStart` hook stop relaunching until `!new`. | §8 "persistent interactive session". The crash-loop stop keeps a broken agent from spinning. | §8, §10 |

**Carry-forwards (not done here):**
- **Plan 4:** Codex as the admin adapter (D7).
- **Plan 6:**
  - verify the reaction-removal contract (S4 item 2);
  - verify `group_leave`'s self-event (S4 item 3); admind never leaves its group, so it does not depend on this;
  - reuse `heterodyne.marmot.control` and D5's join signal, if Task 10 confirms it, for the workstream groups.

## File structure (this plan)

```
$HZ/
  pyproject.toml                                  Task 1 (msgspec, admind script, pyright extraPaths)
  src/heterodyne/marmot/__init__.py               Task 1
  src/heterodyne/marmot/nip19.py                  Task 1   npub <-> hex (bech32)
  src/heterodyne/marmot/control.py                Task 1   wn-agent control client, strict event types
  src/heterodyne/config/paths.py                  Task 2   `expand` made public
  src/heterodyne/config/policy.py                 Task 2   `operators` interpreted
  src/heterodyne/defaults/defaults.toml           Task 2   [adapters.*] binary, [admind] defaults
  src/heterodyne/admind/__init__.py               Task 2
  src/heterodyne/admind/settings.py               Task 2   AdmindSettings, resolve()
  src/heterodyne/services.py                      Task 2 (UNIT_NAME), Task 6 (Systemd)
  src/heterodyne/admind/store.py                  Task 3   SQLite state
  src/heterodyne/admind/audit.py                  Task 3   append-only JSONL
  src/heterodyne/admind/guard.py                  Task 4   ingress verdicts
  src/heterodyne/admind/chunk.py                  Task 4   reply splitting
  src/heterodyne/admind/commands.py               Task 4 (parse), Task 6 (CommandRunner)
  src/heterodyne/admind/alerts.py                 Task 4   alert-file contract
  src/heterodyne/tmux.py                          Task 5   tmux wrapper (reused by plan 4)
  src/heterodyne/agents/__init__.py               Task 5
  src/heterodyne/agents/claude_code.py            Task 5   interactive argv
  src/heterodyne/admind/hook.py                   Task 5   hook socket server + hook client
  src/heterodyne/admind/agent.py                  Task 5   AdminAgent
  src/heterodyne/admind/wnagent.py                Task 7   wn-agent child supervisor
  src/heterodyne/admind/unit.py                   Task 7   systemd unit rendering
  src/heterodyne/admind/cli.py                    Task 7 (init, rearm, unit, hook), Task 8 (run)
  src/heterodyne/admind/__main__.py               Task 7
  src/heterodyne/admind/daemon.py                 Task 8
  examples/config.toml, examples/policy.toml      Task 2
  tests/fixtures/nip19.json                       Task 1
  tests/fakes/__init__.py                         Task 1
  tests/fakes/fake_wn_agent.py                    Task 1
  tests/fakes/settings.py                         Task 4
  tests/fakes/fake_claude.py                      Task 8
  tests/test_nip19.py, tests/test_control.py      Task 1
  tests/test_admind_settings.py, tests/test_policy.py (extended)   Task 2
  tests/test_admind_store.py                      Task 3
  tests/test_admind_logic.py                      Task 4
  tests/test_tmux.py, tests/test_admind_agent.py  Task 5
  tests/test_services.py, tests/test_admind_commands.py   Task 6
  tests/test_admind_cli.py                        Task 7
  tests/test_admind_daemon.py                     Task 8
  docs/admind.md                                  Task 9   runbook and alert contract
  docs/configuration.md, docs/security-model.md, docs/install.md, README.md   Tasks 2, 9
  docs/spikes/admind-acceptance.md                Task 10
```

Task order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11. Tasks 3–6 depend only on 1–2, so they can be reordered if needed.

---

### Task 1: Marmot primitives — npub codec, control client, fake `wn-agent`

**Files:**
- Modify: `pyproject.toml`
- Create: `src/heterodyne/marmot/__init__.py`, `src/heterodyne/marmot/nip19.py`, `src/heterodyne/marmot/control.py`
- Create: `tests/fixtures/nip19.json`, `tests/fakes/__init__.py`, `tests/fakes/fake_wn_agent.py`
- Test: `tests/test_nip19.py`, `tests/test_control.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `heterodyne.marmot.nip19`: `npub_to_hex(npub: str) -> str`, `hex_to_npub(hexkey: str) -> str`, `Nip19Error(ValueError)`.
  - `heterodyne.marmot.control`:
    - errors and constants: `PROTOCOL`, `MAX_FRAME`, `ControlError(message, code, retryable)`, `ProtocolError(ControlError)`;
    - structs: `Sender`, `Message`, `ReplyTo`, `InboundMessage`, `ReactionAdded`, `GroupStateChanged`, `OtherEvent`, `Event` (a union), `Account`, `AccountList`, `GroupInfo`, `GroupCreated`, `FinalSent`;
    - `ControlClient(socket_path: Path, token: str | None, timeout: float = 30.0)` with async `account_list()`, `group_info(account, group)`, `group_create(account, name, members)`, `send_final(account, group, text, reply_to, key)`, and `subscribe(account, group) -> AsyncIterator[Event]`.
  - `tests/fakes/fake_wn_agent.py`: `FakeWnAgent` (see code).

- [ ] **Step 1: Add the dependency, the console script and pyright's test path**

```bash
cd "$HZ" && uv add 'msgspec>=0.19'
```

Then edit `pyproject.toml` so these sections read:

```toml
[project.scripts]
heterodyne = "heterodyne.cli:main"
admind = "heterodyne.admind.cli:main"

[tool.pyright]
include = ["src", "tests", "scripts"]
strict = ["src"]
pythonVersion = "3.12"
venvPath = "."
venv = ".venv"
extraPaths = ["tests"]
```

(`heterodyne.admind.cli` is created in Task 7. Until then, the console script entry is only metadata, and nothing imports it.)

Run: `uv sync && uv run python -c "import msgspec; print(msgspec.__version__)"`
Expected: a version `>= 0.19`.

- [ ] **Step 2: Write the failing tests for the npub codec**

`tests/fixtures/nip19.json` is the NIP-19 specification's own example key (public, not an install value). It was **committed together with this plan** because the install-agnostic checker allows npub literals only under `tests/fixtures/`. It already exists; do not recreate it.

`tests/test_nip19.py`:

```python
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.marmot.nip19 import Nip19Error, hex_to_npub, npub_to_hex

VECTOR = json.loads((Path(__file__).parent / "fixtures" / "nip19.json").read_text())


def test_spec_vector_both_ways() -> None:
    assert npub_to_hex(VECTOR["npub"]) == VECTOR["hex"]
    assert hex_to_npub(VECTOR["hex"]) == VECTOR["npub"]


def test_uppercase_is_accepted_and_mixed_case_rejected() -> None:
    assert npub_to_hex(VECTOR["npub"].upper()) == VECTOR["hex"]
    mixed = VECTOR["npub"][:10] + VECTOR["npub"][10:].upper()
    with pytest.raises(Nip19Error, match="mixed case"):
        npub_to_hex(mixed)


@pytest.mark.parametrize("mutate", [
    lambda s: s[:-1] + ("q" if s[-1] != "q" else "p"),   # checksum
    lambda s: "nsec" + s[4:],                               # wrong prefix
    lambda s: s.replace("1", "b", 1),                       # no separator
    lambda s: s[:20] + "o" + s[21:],                        # 'o' is not in the bech32 alphabet
    lambda s: s[:-7],                                       # truncated
])
def test_invalid_inputs_raise_without_echoing_the_value(mutate: object) -> None:
    bad = mutate(VECTOR["npub"])  # type: ignore[operator]
    with pytest.raises(Nip19Error) as exc:
        npub_to_hex(bad)
    assert bad not in str(exc.value)


def test_hex_must_be_32_bytes() -> None:
    with pytest.raises(Nip19Error):
        hex_to_npub("ab" * 31)
    with pytest.raises(Nip19Error):
        hex_to_npub("zz" * 32)


@given(st.binary(min_size=32, max_size=32))
def test_round_trip(raw: bytes) -> None:
    assert npub_to_hex(hex_to_npub(raw.hex())) == raw.hex()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_nip19.py -q`
Expected: FAIL (`ModuleNotFoundError: heterodyne.marmot`).

- [ ] **Step 4: Implement the codec**

`src/heterodyne/marmot/__init__.py`:

```python
"""Marmot (MLS over Nostr) integration: the wn-agent control protocol and NIP-19 keys."""
```

`src/heterodyne/marmot/nip19.py`:

```python
"""NIP-19 `npub` encoding: bech32 (BIP-173) over a 32-byte public key.

Only public keys are handled here; nothing in heterodyne decodes secret keys. Error messages never
echo the input, because an npub is an install-specific value (ADR 0001 §15).
"""

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
_HRP = "npub"


class Nip19Error(ValueError):
    pass


def _polymod(values: list[int]) -> int:
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i, gen in enumerate(_GEN):
            if (top >> i) & 1:
                chk ^= gen
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool) -> list[int]:
    acc = 0
    bits = 0
    out: list[int] = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or value >> frombits:
            raise Nip19Error("invalid data value")
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or (acc << (tobits - bits)) & maxv:
        raise Nip19Error("invalid padding")
    return out


def npub_to_hex(npub: str) -> str:
    """The 64-character lowercase hex public key encoded by `npub`."""
    if npub != npub.lower() and npub != npub.upper():
        raise Nip19Error("npub has mixed case")
    text = npub.lower()
    sep = text.rfind("1")
    hrp, data_part = text[:sep], text[sep + 1:]
    if sep < 0 or hrp != _HRP or len(data_part) < 7:
        raise Nip19Error("not an npub")
    try:
        data = [_CHARSET.index(c) for c in data_part]
    except ValueError:
        raise Nip19Error("npub contains a character outside the bech32 alphabet") from None
    if _polymod(_hrp_expand(hrp) + data) != 1:
        raise Nip19Error("npub checksum does not match")
    raw = _convertbits(data[:-6], 5, 8, pad=False)
    if len(raw) != 32:
        raise Nip19Error("npub does not encode a 32-byte key")
    return bytes(raw).hex()


def hex_to_npub(hexkey: str) -> str:
    try:
        raw = bytes.fromhex(hexkey)
    except ValueError:
        raise Nip19Error("public key is not hex") from None
    if len(raw) != 32:
        raise Nip19Error("public key must be 32 bytes")
    data = _convertbits(list(raw), 8, 5, pad=True)
    polymod = _polymod(_hrp_expand(_HRP) + data + [0] * 6) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return _HRP + "1" + "".join(_CHARSET[d] for d in data + checksum)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_nip19.py -q`
Expected: PASS.

- [ ] **Step 6: Write the fake `wn-agent`**

`tests/fakes/__init__.py`: empty file.

`tests/fakes/fake_wn_agent.py`:

```python
"""An in-process fake of the wn-agent control socket (marmot.agent-control.v2), per spike S4.

It serves one NDJSON request per connection, except `subscribe_inbound`, which acks and then
streams whatever the test pushes with `push_event`. It checks the bearer token, dedups
`send_final` by idempotency key, and records every request.
"""

import asyncio
import contextlib
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROTOCOL = "marmot.agent-control.v2"
ACCOUNT = "a1" * 32
GROUP = "b2" * 32


class FakeWnAgent:
    def __init__(self, socket_path: Path, token: str | None = "test-token", member_count: int = 2) -> None:
        self.socket_path = socket_path
        self.token = token
        self.member_count = member_count
        self.accounts: list[dict[str, Any]] = [{"account_id_hex": ACCOUNT, "local_signing": True}]
        self.group_id = GROUP
        self.requests: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.fail_sends = 0          # the next N send_final calls fail with a retryable error
        self.fail_group_info = False
        self.on_send: Callable[[dict[str, Any]], None] | None = None   # called after each new send
        self._keys: dict[str, str] = {}
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)

    async def push_event(self, event: dict[str, Any]) -> None:
        for queue in self._subscribers:
            await queue.put(event)

    async def wait_subscribed(self, timeout: float = 5.0) -> None:
        async def poll() -> None:
            while not self._subscribers:
                await asyncio.sleep(0.02)
        await asyncio.wait_for(poll(), timeout)

    def message_event(self, text: str, sender: str, message_id: str, *, is_self: bool = False,
                      group: str | None = None, reply_to: str | None = None) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": "inbound_message", "account_id_hex": ACCOUNT, "group_id_hex": group or self.group_id,
            "message": {"message_id_hex": message_id, "text": text, "recorded_at": 1790738645,
                        "sender": {"account_id_hex": sender, "display_name": None, "is_self": is_self}},
            "mentions_self": False,
        }
        if reply_to:
            event["reply_to"] = {"message_id_hex": reply_to, "availability": "available"}
        return event

    async def _reply(self, writer: asyncio.StreamWriter, request_id: str, body: dict[str, Any]) -> None:
        frame = {"marmot_agent_control": PROTOCOL, "id": request_id, **body}
        writer.write(json.dumps(frame).encode() + b"\n")
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line)
            self.requests.append(req)
            rid = req.get("id", "")
            if self.token is not None and req.get("auth_token") != self.token:
                await self._reply(writer, rid, {"type": "error", "code": "unauthorized",
                                                "message": "bad token", "retryable": False})
                return
            kind = req.get("type")
            if kind == "account_list":
                await self._reply(writer, rid, {"type": "account_list", "accounts": self.accounts})
            elif kind == "group_info":
                if self.fail_group_info:
                    await self._reply(writer, rid, {"type": "error", "code": "unavailable",
                                                    "message": "down", "retryable": True})
                    return
                await self._reply(writer, rid, {
                    "type": "group_info", "account_id_hex": req["account_id_hex"],
                    "group_id_hex": req["group_id_hex"], "agent_created": True,
                    "member_count": self.member_count, "is_direct": True})
            elif kind == "group_create":
                await self._reply(writer, rid, {"type": "group_created", "group_id_hex": self.group_id,
                                                "agent_created": True, "pending_welcome_count": 0})
            elif kind == "send_final":
                await self._send_final(writer, rid, req)
            elif kind == "subscribe_inbound":
                await self._subscribe(writer, rid)
            else:
                await self._reply(writer, rid, {"type": "error", "code": "unsupported",
                                                "message": str(kind), "retryable": False})
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _send_final(self, writer: asyncio.StreamWriter, rid: str, req: dict[str, Any]) -> None:
        if self.fail_sends > 0:
            self.fail_sends -= 1
            await self._reply(writer, rid, {"type": "error", "code": "relay_unavailable",
                                            "message": "try later", "retryable": True})
            return
        key = req.get("idempotency_key")
        if key is not None and key in self._keys:
            message_id = self._keys[key]
        else:
            message_id = hashlib.sha256(f"{len(self.sent)}:{req['text']}".encode()).hexdigest()
            self.sent.append(req)
            if key is not None:
                self._keys[key] = message_id
            if self.on_send is not None:
                self.on_send(req)
        await self._reply(writer, rid, {"type": "final_sent", "message_ids_hex": [message_id],
                                        "maintenance_disposition": "ready"})

    async def _subscribe(self, writer: asyncio.StreamWriter, rid: str) -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            await self._reply(writer, rid, {"type": "ack"})
            while True:
                event = await queue.get()
                await self._reply(writer, rid, event)
        finally:
            self._subscribers.remove(queue)
```

- [ ] **Step 7: Write the failing tests for the control client**

`tests/test_control.py`:

```python
import asyncio
import json
from pathlib import Path

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent

from heterodyne.marmot.control import (
    MAX_FRAME,
    ControlClient,
    ControlError,
    GroupStateChanged,
    InboundMessage,
    OtherEvent,
    ProtocolError,
    decode_event,
)


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_requests_carry_protocol_id_and_token(tmp_path: Path) -> None:
    async def body() -> None:
        fake = FakeWnAgent(tmp_path / "s.sock")
        await fake.start()
        client = ControlClient(tmp_path / "s.sock", "test-token")
        accounts = await client.account_list()
        assert accounts.accounts[0].account_id_hex == ACCOUNT
        assert accounts.accounts[0].local_signing is True
        info = await client.group_info(ACCOUNT, fake.group_id)
        assert info.member_count == 2
        req = fake.requests[-1]
        assert req["marmot_agent_control"] == "marmot.agent-control.v2"
        assert req["auth_token"] == "test-token" and len(req["id"]) == 32
        await fake.stop()
    run(body())


def test_error_frames_raise_control_error_with_code(tmp_path: Path) -> None:
    async def body() -> None:
        fake = FakeWnAgent(tmp_path / "s.sock")
        await fake.start()
        with pytest.raises(ControlError) as exc:
            await ControlClient(tmp_path / "s.sock", "wrong").account_list()
        assert exc.value.code == "unauthorized" and exc.value.retryable is False
        assert exc.value.detail == "bad token" and "bad token" not in str(exc.value)
        await fake.stop()
    run(body())


def test_send_final_is_idempotent_by_key(tmp_path: Path) -> None:
    async def body() -> None:
        fake = FakeWnAgent(tmp_path / "s.sock")
        await fake.start()
        client = ControlClient(tmp_path / "s.sock", "test-token")
        a = await client.send_final(ACCOUNT, fake.group_id, "hi", None, "k1")
        b = await client.send_final(ACCOUNT, fake.group_id, "hi", None, "k1")
        assert a.message_ids_hex == b.message_ids_hex and len(fake.sent) == 1
        assert fake.sent[0]["reply_to_message_id_hex"] is None
        assert fake.sent[0]["idempotency_key"] == "k1"
        await fake.stop()
    run(body())


def test_missing_socket_is_a_retryable_socket_error(tmp_path: Path) -> None:
    with pytest.raises(ControlError) as exc:
        run(ControlClient(tmp_path / "absent.sock", None, timeout=1).account_list())
    assert exc.value.code == "socket_io" and exc.value.retryable


def test_subscribe_yields_typed_events(tmp_path: Path) -> None:
    async def body() -> list[object]:
        fake = FakeWnAgent(tmp_path / "s.sock")
        await fake.start()
        client = ControlClient(tmp_path / "s.sock", "test-token")
        got: list[object] = []

        async def consume() -> None:
            async for event in client.subscribe(ACCOUNT, fake.group_id):
                got.append(event)
                if len(got) == 3:
                    return

        task = asyncio.create_task(consume())
        await fake.wait_subscribed()
        await fake.push_event(fake.message_event("hello", "c3" * 32, "d4" * 32, reply_to="e5" * 32))
        await fake.push_event({"type": "group_state_changed", "account_id_hex": ACCOUNT,
                               "group_id_hex": fake.group_id, "event_id_hex": "f6" * 32,
                               "change": "member_added"})
        await fake.push_event({"type": "message_edited", "account_id_hex": ACCOUNT})
        await asyncio.wait_for(task, 5)
        await fake.stop()
        return got
    got = run(body())
    msg = got[0]
    assert isinstance(msg, InboundMessage)
    assert msg.message.text == "hello" and msg.message.sender.account_id_hex == "c3" * 32
    assert msg.reply_to is not None and msg.reply_to.message_id_hex == "e5" * 32
    assert isinstance(got[1], GroupStateChanged) and got[1].change == "member_added"
    assert isinstance(got[2], OtherEvent) and got[2].type == "message_edited"


def _frame(**body: object) -> bytes:
    return json.dumps({"marmot_agent_control": "marmot.agent-control.v2", "id": "r1", **body}).encode()


def test_decode_event_is_strict_for_known_types() -> None:
    # sender.is_self missing: a known type with a missing field is a protocol error, not a partial dict.
    bad = _frame(type="inbound_message", account_id_hex="aa", group_id_hex="bb",
                 message={"message_id_hex": "cc", "text": "x", "recorded_at": 1,
                          "sender": {"account_id_hex": "dd"}})
    with pytest.raises(ProtocolError):
        decode_event(bad, "r1")


def test_decode_event_checks_protocol_and_id() -> None:
    with pytest.raises(ProtocolError, match="id"):
        decode_event(_frame(type="ack"), "other")
    wrong = json.dumps({"marmot_agent_control": "v1", "id": "r1", "type": "ack"}).encode()
    with pytest.raises(ProtocolError, match="protocol"):
        decode_event(wrong, "r1")


def test_oversized_frames_are_rejected_before_sending(tmp_path: Path) -> None:
    client = ControlClient(tmp_path / "s.sock", None)
    with pytest.raises(ControlError, match="too large"):
        run(client.send_final(ACCOUNT, "b2" * 32, "x" * MAX_FRAME, None, "k"))
```

- [ ] **Step 8: Run the tests to verify they fail**

Run: `uv run pytest tests/test_control.py -q`
Expected: FAIL (`ModuleNotFoundError: heterodyne.marmot.control`).

- [ ] **Step 9: Implement the control client**

`src/heterodyne/marmot/control.py`:

```python
"""Client for the wn-agent control socket: `marmot.agent-control.v2`, NDJSON over a Unix socket.

Request and event shapes are the ones spike S4 confirmed (docs/spikes/S4-marmot.md). Every frame is
decoded strictly with msgspec (ADR 0001 §16): a known event type with a missing or mistyped field is a
ProtocolError, never a partially trusted dict. Unknown event types become `OtherEvent` so callers can
log and ignore them. The sender of a message is taken only from its MLS-authenticated `sender`
metadata, never from the text (§3.4).
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import msgspec

PROTOCOL = "marmot.agent-control.v2"
MAX_FRAME = 1024 * 1024


class ControlError(Exception):
    """`str(exc)` is always admind's own wording. A peer's free-text error message may echo keys or IDs,
    so it is kept in `detail`, which goes only to the local audit log, never to stderr or the group."""

    def __init__(self, message: str, code: str = "agent_control_error", retryable: bool = False,
                 detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.detail = detail


class ProtocolError(ControlError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="protocol_error", retryable=False)


class _Head(msgspec.Struct):
    marmot_agent_control: str
    id: str
    type: str


class _Error(msgspec.Struct):
    code: str = "agent_control_error"
    message: str = "agent control error"
    retryable: bool = False


class Account(msgspec.Struct, frozen=True):
    account_id_hex: str
    local_signing: bool = False


class AccountList(msgspec.Struct, frozen=True):
    accounts: list[Account]


class GroupInfo(msgspec.Struct, frozen=True):
    group_id_hex: str
    member_count: int


class GroupCreated(msgspec.Struct, frozen=True):
    group_id_hex: str


class FinalSent(msgspec.Struct, frozen=True):
    message_ids_hex: list[str]


class _Ack(msgspec.Struct, frozen=True):
    type: str


class Sender(msgspec.Struct, frozen=True):
    account_id_hex: str
    is_self: bool
    display_name: str | None = None


class Message(msgspec.Struct, frozen=True):
    message_id_hex: str
    sender: Sender
    text: str
    recorded_at: int


class ReplyTo(msgspec.Struct, frozen=True):
    message_id_hex: str


class InboundMessage(msgspec.Struct, frozen=True):
    account_id_hex: str
    group_id_hex: str
    message: Message
    reply_to: ReplyTo | None = None


class ReactionAdded(msgspec.Struct, frozen=True):
    account_id_hex: str
    group_id_hex: str
    target_message_id_hex: str
    actor: Sender
    emoji: str


class GroupStateChanged(msgspec.Struct, frozen=True):
    account_id_hex: str
    group_id_hex: str
    change: str
    detail: str | None = None


class OtherEvent(msgspec.Struct, frozen=True):
    type: str


Event = InboundMessage | ReactionAdded | GroupStateChanged | OtherEvent
_EVENT_TYPES: dict[str, type[InboundMessage] | type[ReactionAdded] | type[GroupStateChanged]] = {
    "inbound_message": InboundMessage,
    "reaction_added": ReactionAdded,
    "group_state_changed": GroupStateChanged,
}


def decode_head(line: bytes, request_id: str) -> str:
    """Validate protocol, correlation ID and error frames; return the frame's `type`."""
    try:
        head = msgspec.json.decode(line, type=_Head)
    except msgspec.DecodeError as exc:
        raise ProtocolError(f"malformed control frame: {exc}") from exc
    if head.marmot_agent_control != PROTOCOL:
        raise ProtocolError("wrong control protocol")
    if head.id != request_id:
        raise ProtocolError("response id does not match the request id")
    if head.type == "error":
        err = msgspec.json.decode(line, type=_Error)
        raise ControlError(f"wn-agent returned error {err.code}", err.code, err.retryable, detail=err.message)
    return head.type


def decode_event(line: bytes, request_id: str) -> Event:
    kind = decode_head(line, request_id)
    struct = _EVENT_TYPES.get(kind)
    if struct is None:
        return OtherEvent(type=kind)
    try:
        return msgspec.json.decode(line, type=struct)
    except msgspec.DecodeError as exc:
        raise ProtocolError(f"{kind}: {exc}") from exc


class ControlClient:
    def __init__(self, socket_path: Path, token: str | None, timeout: float = 30.0) -> None:
        self.socket_path = socket_path
        self.token = token
        self.timeout = timeout

    def _frame(self, payload: dict[str, Any], request_id: str) -> bytes:
        envelope: dict[str, Any] = {"marmot_agent_control": PROTOCOL, "id": request_id, **payload}
        if self.token:
            envelope["auth_token"] = self.token
        frame = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
        if len(frame) > MAX_FRAME:
            raise ControlError("control frame too large", "frame_too_large")
        return frame

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path), limit=MAX_FRAME + 1), self.timeout)
        except (OSError, TimeoutError) as exc:
            raise ControlError(f"cannot connect to wn-agent: {exc}", "socket_io", True) from exc

    async def _readline(self, reader: asyncio.StreamReader, timeout: float | None) -> bytes:
        try:
            if timeout is None:
                line = await reader.readline()
            else:
                line = await asyncio.wait_for(reader.readline(), timeout)
        except TimeoutError as exc:
            raise ControlError("timed out waiting for wn-agent", "timeout", True) from exc
        except ValueError as exc:  # LimitOverrunError surfaces as ValueError from readline()
            raise ProtocolError("control frame too large") from exc
        except OSError as exc:
            raise ControlError(f"wn-agent socket error: {exc}", "socket_io", True) from exc
        if not line:
            raise ControlError("wn-agent closed the connection", "socket_closed", True)
        return line

    async def _write(self, writer: asyncio.StreamWriter, frame: bytes) -> None:
        writer.write(frame)
        try:
            await asyncio.wait_for(writer.drain(), self.timeout)
        except (OSError, TimeoutError) as exc:
            raise ControlError(f"cannot write to wn-agent: {exc}", "socket_io", True) from exc

    async def call[T](self, payload: dict[str, Any], kind: type[T]) -> T:
        request_id = uuid.uuid4().hex
        frame = self._frame(payload, request_id)
        reader, writer = await self._open()
        try:
            await self._write(writer, frame)
            line = await self._readline(reader, self.timeout)
            decode_head(line, request_id)
            try:
                return msgspec.json.decode(line, type=kind)
            except msgspec.DecodeError as exc:
                raise ProtocolError(f"unexpected {payload['type']} response: {exc}") from exc
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def account_list(self) -> AccountList:
        return await self.call({"type": "account_list"}, AccountList)

    async def group_info(self, account: str, group: str) -> GroupInfo:
        return await self.call({"type": "group_info", "account_id_hex": account, "group_id_hex": group},
                               GroupInfo)

    async def group_create(self, account: str, name: str, members: list[str]) -> GroupCreated:
        return await self.call({"type": "group_create", "account_id_hex": account, "name": name,
                                "members": members, "description": None, "relays": None}, GroupCreated)

    async def send_final(self, account: str, group: str, text: str, reply_to: str | None,
                         key: str) -> FinalSent:
        return await self.call({"type": "send_final", "account_id_hex": account, "group_id_hex": group,
                                "text": text, "reply_to_message_id_hex": reply_to,
                                "idempotency_key": key}, FinalSent)

    async def subscribe(self, account: str, group: str) -> AsyncIterator[Event]:
        """Yield inbound events until the connection ends, which raises a retryable ControlError."""
        request_id = uuid.uuid4().hex
        frame = self._frame({"type": "subscribe_inbound", "account_id_hex": account,
                             "group_id_hex": group}, request_id)
        reader, writer = await self._open()
        try:
            await self._write(writer, frame)
            ack = await self._readline(reader, self.timeout)
            decode_head(ack, request_id)
            if msgspec.json.decode(ack, type=_Ack).type != "ack":
                raise ProtocolError("subscribe_inbound was not acknowledged")
            while True:
                yield decode_event(await self._readline(reader, None), request_id)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
```

- [ ] **Step 10: Run the tests and the full gate**

Run: `uv run pytest tests/test_nip19.py tests/test_control.py -q && uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`
Expected: all PASS; the checker prints nothing and exits 0.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml uv.lock src/heterodyne/marmot tests/fakes tests/test_nip19.py tests/test_control.py
git commit -m "Marmot primitives: npub codec, strict wn-agent control client, fake wn-agent (plan 2 task 1)"
```

---

### Task 2: Configuration — `operators` policy, admind settings, adapter binaries

**Files:**
- Modify: `src/heterodyne/config/paths.py` (make `expand` public)
- Modify: `src/heterodyne/config/policy.py` (interpret `operators`)
- Modify: `src/heterodyne/defaults/defaults.toml`
- Create: `src/heterodyne/services.py` (only `UNIT_NAME` in this task)
- Create: `src/heterodyne/admind/__init__.py`, `src/heterodyne/admind/settings.py`
- Modify: `examples/config.toml`, `examples/policy.toml`, `docs/configuration.md`
- Test: `tests/test_policy.py` (extend), `tests/test_admind_settings.py`

**Interfaces:**
- Consumes: `heterodyne.config.load`, `Config`, `ConfigError`, `layers.table_at/as_table/string_list`, `secret_scan.show`; `nip19.npub_to_hex`.
- Produces:
  - `paths.expand(value: str, env: Mapping[str, str]) -> Path`.
  - `Policy.operators: tuple[str, ...]`.
  - `heterodyne.services.UNIT_NAME: re.Pattern[str]`.
  - `heterodyne.admind.settings`: `AdmindSettings` (frozen dataclass; fields below) and `resolve(cfg: Config, env: Mapping[str, str]) -> AdmindSettings`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_policy.py`:

```python
def test_operators_must_be_approvers() -> None:
    from heterodyne.config.policy import build_policy
    policy = build_policy({"approvers": ["op"], "operators": ["op"]}, {}, {})
    assert policy.operators == ("op",)
    with pytest.raises(ConfigError, match="not in approvers"):
        build_policy({"approvers": ["op"], "operators": ["other"]}, {}, {})
    with pytest.raises(ConfigError, match="list of approver names"):
        build_policy({"approvers": ["op"], "operators": "op"}, {}, {})
```

(If `tests/test_policy.py` does not already import `pytest` and `ConfigError`, add `import pytest` and `from heterodyne.config import ConfigError` at the top.)

`tests/test_admind_settings.py`:

```python
from pathlib import Path

import pytest

from heterodyne.admind.settings import resolve
from heterodyne.config import ConfigError, load
from heterodyne.marmot.nip19 import hex_to_npub

OPERATOR_HEX = "c3" * 32

BASE_CONFIG = """
[platform]
os = "linux"
service_manager = "systemd"
sandbox = "bubblewrap"
[profiles.admin]
adapter = "claude-code"
model = "m1"
[admind]
profile = "admin"
restart_units = ["wsd.service", "runner@x.service"]  # install-agnostic: allow=email (systemd template unit name)
[admind.marmot]
relays = ["wss://relay.example.org"]
"""


def write(d: Path, config: str = BASE_CONFIG, operators: str = '["op"]') -> dict[str, str]:
    (d / "config.toml").write_text(config)
    (d / "policy.toml").write_text(
        f'approvers = ["op"]\noperators = {operators}\n'
        f'[identities.op]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n')
    return {"HETERODYNE_CONFIG_DIR": str(d), "HETERODYNE_STATE_DIR": str(d / "state"), "HOME": str(d)}


def test_resolves_defaults_and_operator(tmp_path: Path) -> None:
    env = write(tmp_path)
    s = resolve(load(None, env), env)
    assert s.operator_hex == OPERATOR_HEX
    assert s.adapter_binary == "claude"
    assert s.profile["model"] == "m1"
    assert s.workdir == tmp_path
    assert s.restart_units == ("wsd.service", "runner@x.service")  # install-agnostic: allow=email (template unit)
    assert s.chunk_chars == 4000 and s.alert_poll_seconds == 5
    assert s.state_dir == tmp_path / "state" / "admind"
    assert s.alerts_dir == tmp_path / "state" / "alerts"
    assert s.marmot_home == tmp_path / "state" / "admind" / "marmot"
    assert s.relays == ("wss://relay.example.org",)
    assert s.service_manager == "systemd"


@pytest.mark.parametrize(("patch", "message"), [
    ('[admind]\nprofile = "nope"\n', "not defined"),
    ('[profiles.cx]\nadapter = "codex"\n[admind]\nprofile = "cx"\n', "supports"),
    ('[admind]\nprofile = "admin"\nrestart_units = ["../etc/passwd"]\n', "not a unit name"),
    ('[admind]\nprofile = "admin"\nrestart_units = ["-evil.service"]\n', "not a unit name"),
    ('[admind]\nprofile = "admin"\nchunk_chars = 10\n', "chunk_chars"),
    ('[admind]\nprofile = "admin"\nalert_poll_seconds = true\n', "alert_poll_seconds"),
    ('[admind]\nprofile = "admin"\nsurprise = 1\n', "unknown keys"),
    ('[admind]\nprofile = "admin"\n[admind.marmot]\nrelays = []\n', "relays"),
    ('[admind]\nprofile = "admin"\n[admind.marmot]\nrelays = ["https://x"]\n', "relays"),
    ('[admind]\nprofile = "admin"\n[admind.marmot]\nrelays = ["wss://r"]\nhome = "rel/path"\n', "absolute"),
])
def test_invalid_admind_config_is_rejected(tmp_path: Path, patch: str, message: str) -> None:
    config = BASE_CONFIG.split("[admind]")[0] + patch
    if "[admind.marmot]" not in patch:
        config += '[admind.marmot]\nrelays = ["wss://relay.example.org"]\n'
    env = write(tmp_path, config)
    with pytest.raises(ConfigError, match=message):
        resolve(load(None, env), env)


def test_exactly_one_operator_with_a_valid_npub(tmp_path: Path) -> None:
    env = write(tmp_path, operators="[]")
    with pytest.raises(ConfigError, match="exactly one"):
        resolve(load(None, env), env)
    env = write(tmp_path)
    (tmp_path / "policy.toml").write_text(
        'approvers = ["op"]\noperators = ["op"]\n[identities.op]\nmarmot_npub = "npub1bad"\n')
    with pytest.raises(ConfigError, match="not a valid npub") as exc:
        resolve(load(None, env), env)
    assert "npub1bad" not in str(exc.value)


def test_socket_path_length_is_checked(tmp_path: Path) -> None:
    deep = tmp_path / ("d" * 120)
    config = BASE_CONFIG.replace('relays = ["wss://relay.example.org"]',
                                 f'relays = ["wss://relay.example.org"]\nhome = "{deep}"')
    env = write(tmp_path, config)
    with pytest.raises(ConfigError, match="too long"):
        resolve(load(None, env), env)


def test_admind_is_not_settable_from_a_workstream(tmp_path: Path) -> None:
    env = write(tmp_path)
    (tmp_path / "workstreams").mkdir()
    (tmp_path / "workstreams" / "w.toml").write_text('[admind]\nprofile = "admin"\n')
    with pytest.raises(ConfigError, match="not allowed in a workstream"):
        load("w", env)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_policy.py tests/test_admind_settings.py -q`
Expected: FAIL (`operators` unknown to `Policy`; `heterodyne.admind` missing).

- [ ] **Step 3: Make `expand` public in `paths.py`**

In `src/heterodyne/config/paths.py`, rename `_expand` to `expand` (and its docstring stays), and update its two call sites in `config_dir` and `state_dir` to `expand(...)`. No behaviour change.

- [ ] **Step 4: Interpret `operators` in `policy.py`**

In `src/heterodyne/config/policy.py`, add the field to `Policy` (last, with a default, so existing constructors still work):

```python
@dataclass(frozen=True)
class Policy:
    approvers: tuple[str, ...] = ()
    identities: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])
    tiers: dict[str, str] = field(default_factory=dict[str, str])
    operators: tuple[str, ...] = ()
```

and in `build_policy`, after the approvers check, replace the `return` with:

```python
    operators: Any = raw.get("operators", [])
    if not isinstance(operators, list) or not all(isinstance(o, str) for o in cast(list[Any], operators)):
        raise ConfigError("policy.toml: operators must be a list of approver names")
    missing = [o for o in cast(list[str], operators) if o not in approvers]
    if missing:
        raise ConfigError(f"policy.toml: operators {show(missing)} are not in approvers")
    return Policy(approvers=tuple(cast(list[str], approvers)),
                  identities=_identities(raw.get("identities", {})),
                  tiers=effective_tiers(default_tiers, _tier_overrides(raw.get("tiers", {})), restrict),
                  operators=tuple(cast(list[str], operators)))
```

- [ ] **Step 5: Add the defaults**

Append to `src/heterodyne/defaults/defaults.toml`:

```toml
# Executable of each adapter's CLI. Hosts override with an absolute path if it is not on PATH.
[adapters.claude-code]
binary = "claude"

[adapters.codex]
binary = "codex"

# Admin override channel (§8). `profile` has no default: the host must name one.
[admind]
workdir = "~"
restart_units = []
chunk_chars = 4000
alert_poll_seconds = 5
group_check_seconds = 60
start_timeout_seconds = 60
group_name = "heterodyne admin"

[admind.marmot]
wn_agent = "wn-agent"
relays = []
```

- [ ] **Step 6: Add `UNIT_NAME`**

`src/heterodyne/services.py`:

```python
"""Service-manager control (ADR 0001 §3.2). The backend comes from host config, never sys.platform."""

import re

# A unit name that can't be read as an option, a path or a glob.
UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9@_.:-]*\.(?:service|target|timer|socket)")
```

- [ ] **Step 7: Implement the settings resolver**

`src/heterodyne/admind/__init__.py`:

```python
"""admind: the independent admin override channel (ADR 0001 §8)."""
```

`src/heterodyne/admind/settings.py`:

```python
"""admind settings from the merged host config and the host policy (ADR 0001 §8, §15).

`[admind]` lives in host `config.toml` only: the workstream layer rejects it (it is not one of
`WORKSTREAM_KEYS`), and environment overrides cover locations only. The operator comes from
`policy.toml` (`operators`, then `identities.<name>.marmot_npub`), which is host-only.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heterodyne.config import Config, ConfigError, paths
from heterodyne.config.layers import as_table, string_list, table_at
from heterodyne.config.secret_scan import show
from heterodyne.marmot.nip19 import Nip19Error, npub_to_hex
from heterodyne.services import UNIT_NAME

ADMIND_KEYS = frozenset({"profile", "workdir", "restart_units", "chunk_chars", "alert_poll_seconds",
                         "group_check_seconds", "start_timeout_seconds", "group_name", "marmot"})
MARMOT_KEYS = frozenset({"wn_agent", "home", "relays"})
ADMIN_ADAPTERS = ("claude-code",)
MAX_SOCKET_PATH = 100  # bytes; sun_path is 108 on Linux and 104 on macOS


@dataclass(frozen=True)
class AdmindSettings:
    profile: Mapping[str, Any]
    adapter_binary: str
    workdir: Path
    restart_units: tuple[str, ...]
    chunk_chars: int
    alert_poll_seconds: float
    group_check_seconds: float
    start_timeout_seconds: float
    group_name: str
    wn_agent: str
    marmot_home: Path
    relays: tuple[str, ...]
    operator_hex: str
    operator_npub: str
    state_dir: Path
    alerts_dir: Path
    service_manager: str


def resolve(cfg: Config, env: Mapping[str, str]) -> AdmindSettings:
    admind = table_at(cfg.values, "admind", "config")
    _only(admind, ADMIND_KEYS, "[admind]")
    marmot = table_at(admind, "marmot", "config: admind")
    _only(marmot, MARMOT_KEYS, "[admind.marmot]")

    profile_name = admind.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        raise ConfigError("[admind] profile must name a profile from [profiles]")
    profile = as_table(table_at(cfg.values, "profiles", "config").get(profile_name))
    if profile is None:
        raise ConfigError(f"[admind] profile {show(profile_name)} is not defined in [profiles]")
    adapter = profile.get("adapter")
    if adapter not in ADMIN_ADAPTERS:
        raise ConfigError(f"[admind] profile {show(profile_name)} uses adapter {show(adapter)}; "
                          f"the admin agent supports {list(ADMIN_ADAPTERS)} so far")
    binary = cfg.get(f"adapters.{adapter}.binary")
    if not isinstance(binary, str) or not binary:
        raise ConfigError(f"adapters.{adapter}.binary must be a non-empty string")

    units = tuple(string_list(admind.get("restart_units", []), "[admind] restart_units"))
    for unit in units:
        if not UNIT_NAME.fullmatch(unit):
            raise ConfigError(f"[admind] restart_units: {show(unit)} is not a unit name")

    relays = tuple(string_list(marmot.get("relays", []), "[admind.marmot] relays"))
    if not relays or not all(r.startswith(("wss://", "ws://")) for r in relays):
        raise ConfigError("[admind.marmot] relays must be a non-empty list of ws:// or wss:// URLs")

    state = paths.state_dir(env)
    home_value = marmot.get("home")
    home = (state / "admind" / "marmot" if home_value is None
            else _abs(home_value, env, "[admind.marmot] home"))
    if len(str(home / "ctl" / "wn-agent.sock").encode()) > MAX_SOCKET_PATH:
        raise ConfigError(f"[admind.marmot] home is too long for a Unix socket path "
                          f"(max {MAX_SOCKET_PATH} bytes including ctl/wn-agent.sock)")

    operator_npub, operator_hex = _operator(cfg)
    service_manager = cfg.get("platform.service_manager")
    if not isinstance(service_manager, str) or not service_manager:
        raise ConfigError("[platform] service_manager is not set; run `heterodyne setup`")

    return AdmindSettings(
        profile=profile, adapter_binary=binary,
        workdir=_abs(admind.get("workdir", "~"), env, "[admind] workdir"),
        restart_units=units,
        chunk_chars=_int(admind, "chunk_chars", 200, 60000),
        alert_poll_seconds=_seconds(admind, "alert_poll_seconds"),
        group_check_seconds=_seconds(admind, "group_check_seconds"),
        start_timeout_seconds=_seconds(admind, "start_timeout_seconds"),
        group_name=_text(admind, "group_name", "[admind]"),
        wn_agent=_text(marmot, "wn_agent", "[admind.marmot]"),
        marmot_home=home, relays=relays,
        operator_hex=operator_hex, operator_npub=operator_npub,
        state_dir=state / "admind", alerts_dir=state / "alerts",
        service_manager=service_manager,
    )


def _operator(cfg: Config) -> tuple[str, str]:
    operators = cfg.policy.operators
    if len(operators) != 1:
        raise ConfigError(f"policy.toml: admind needs exactly one entry in operators, found {len(operators)}")
    name = operators[0]
    where = f"policy.toml: identities.{show(name, False)}.marmot_npub"
    npub = cfg.policy.identities.get(name, {}).get("marmot_npub")
    if not npub:
        raise ConfigError(f"{where} is missing")
    try:
        return npub, npub_to_hex(npub)
    except Nip19Error as exc:
        raise ConfigError(f"{where} is not a valid npub ({exc})") from None


def _only(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown keys {show(unknown)} (allowed: {sorted(allowed)})")


def _abs(value: Any, env: Mapping[str, str], where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a path")
    path = paths.expand(value, env)
    if not path.is_absolute():
        raise ConfigError(f"{where} must be an absolute path or start with ~/")
    return path


def _int(table: Mapping[str, Any], key: str, lo: int, hi: int) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise ConfigError(f"[admind] {key} must be an integer from {lo} to {hi}")
    return value


def _seconds(table: Mapping[str, Any], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 3600:
        raise ConfigError(f"[admind] {key} must be a number of seconds, more than 0 and at most 3600")
    return float(value)


def _text(table: Mapping[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} {key} must be a non-empty string")
    return value
```

- [ ] **Step 8: Update the examples**

In `examples/policy.toml`, add this line directly after the `approvers = [...]` line (it must come before the first `[table]` header):

```toml
operators = ["<approver-name>"]   # the one approver admind accepts messages from (ADR §8)
```

Append to `examples/config.toml`:

```toml
# Admin override channel (ADR §8; see docs/admind.md). Host config only.
[admind]
profile = "reviewer"                  # the admin agent's profile; its adapter must be claude-code
restart_units = ["<unit-name>.service"]   # the only units `!restart` accepts

[admind.marmot]
relays = ["<wss-relay-url>"]
```

- [ ] **Step 9: Update `docs/configuration.md`**

- In the **Policy** section, change "Only three are interpreted today" to "Four are interpreted today", and add the bullet:
  `- operators: a list of approver names allowed to use the admin channel. Each must be in approvers. admind needs exactly one, with an identities.<name>.marmot_npub.`
- In **Built-in defaults**, add: "It also sets each adapter's executable (`[adapters.<adapter>] binary`) and admind's defaults (see below)."
- Add a section `### Admin channel (`[admind]`)` after **Host config**. It holds a table with one row per key: `profile` (required; claude-code only for now), `workdir` (default `~`), `restart_units`, `chunk_chars` (200–60000, default 4000), `alert_poll_seconds` (default 5), `group_check_seconds` (default 60), `start_timeout_seconds` (default 60), `group_name`, `marmot.wn_agent` (default `wn-agent`), `marmot.home` (default `<state>/admind/marmot`), and `marmot.relays` (required, ws/wss URLs). Add the rule that `[admind]` is host-only: a workstream file can't set it.
- Update the sentence "Nothing writes to the state directory yet" to: "`admind` writes `<state>/admind/` (its database, audit log, hook socket and Marmot home) and reads `<state>/alerts/`."

- [ ] **Step 10: Run the tests and the full gate**

Run: `uv run pytest -q && uv run ruff check && uv run pyright && uv run python scripts/check_install_agnostic.py`
Expected: PASS, including the existing `tests/test_setup.py`. The example `operators = ["<approver-name>"]` is in approvers, so setup still validates. The placeholder npub is not decoded until admind starts.

- [ ] **Step 11: Commit**

```bash
git add src/heterodyne/config src/heterodyne/defaults src/heterodyne/services.py src/heterodyne/admind examples docs/configuration.md tests/test_policy.py tests/test_admind_settings.py
git commit -m "admind settings, policy operators and adapter binaries (plan 2 task 2)"
```

---

### Task 3: Durable state — SQLite store and append-only audit log

**Files:**
- Create: `src/heterodyne/admind/store.py`, `src/heterodyne/admind/audit.py`
- Test: `tests/test_admind_store.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Store(path: Path)` with:
    - key-value: `get(key) -> str | None`, `set(key, value)`, `delete(key)`;
    - inbound: `claim_inbound(message_id) -> bool`, `set_inbound(message_id, status)`, `inbound_with_status(status) -> list[str]`;
    - outbox: `enqueue(key, text, reply_to) -> bool`, `pending() -> list[OutboxRow]`, `mark_sent(seq, message_id)`, `mark_attempt(seq) -> int`, `mark_failed(seq)`;
    - alerts: `relay_alert(name, key, text) -> bool`, `relayed(name) -> bool`;
    - `close()`.
  - `OutboxRow(seq, key, reply_to, text, attempts)`, and `now() -> str` (UTC ISO-8601, seconds).
  - `Audit(path: Path)` with `write(kind: str, **fields: object) -> None`.
  - Well-known `kv` keys (used by later tasks): `group_id_hex`, `account_id_hex`, `agent_session`, `session_started`, `launches_without_start`, `operator_seen_at`, `latched`, `in_flight`, `reply_seq`.

- [ ] **Step 1: Write the failing tests**

`tests/test_admind_store.py`:

```python
import json
import os
import stat
from pathlib import Path

import pytest

from heterodyne.admind.audit import Audit
from heterodyne.admind.store import Store


def test_files_are_private(tmp_path: Path) -> None:
    old = os.umask(0)
    try:
        Store(tmp_path / "a" / "admind.db")
        Audit(tmp_path / "a" / "audit.jsonl").write("test")
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "a" / "admind.db").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "a" / "audit.jsonl").stat().st_mode) == 0o600


def test_kv_round_trip_and_persistence(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    assert s.get("k") is None
    s.set("k", "v1")
    s.set("k", "v2")
    s.close()
    s = Store(tmp_path / "db")
    assert s.get("k") == "v2"
    s.delete("k")
    assert s.get("k") is None


def test_claim_inbound_is_once_only(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    assert s.claim_inbound("m1") is True
    assert s.claim_inbound("m1") is False
    assert s.inbound_with_status("received") == ["m1"]
    s.set_inbound("m1", "dispatched")
    assert s.inbound_with_status("received") == []
    with pytest.raises(Exception, match="CHECK"):
        s.set_inbound("m1", "bogus")


def test_outbox_order_retry_and_dedup(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    assert s.enqueue("k1", "one", None) is True
    assert s.enqueue("k2", "two", "m1") is True
    assert s.enqueue("k1", "dup", None) is False
    rows = s.pending()
    assert [(r.key, r.text, r.reply_to) for r in rows] == [("k1", "one", None), ("k2", "two", "m1")]
    assert s.mark_attempt(rows[0].seq) == 1
    assert s.mark_attempt(rows[0].seq) == 2
    s.mark_sent(rows[0].seq, "x1")
    s.mark_failed(rows[1].seq)
    assert s.pending() == []


def test_store_is_usable_from_worker_threads(tmp_path: Path) -> None:
    import concurrent.futures
    s = Store(tmp_path / "db")
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda i: s.enqueue(f"k{i}", "t", None), range(50)))
    assert len(s.pending()) == 50


def test_relay_alert_is_atomic_and_once(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    assert s.relay_alert("a1", "alert:a1", "text") is True
    assert s.relay_alert("a1", "alert:a1", "text") is False
    assert s.relayed("a1") and not s.relayed("a2")
    assert [r.key for r in s.pending()] == ["alert:a1"]


def test_audit_appends_json_lines(tmp_path: Path) -> None:
    a = Audit(tmp_path / "audit.jsonl")
    a.write("inbound", message_id="m1", text="héllo\nworld")
    a.write("drop", reason="x")
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    assert first["kind"] == "inbound" and first["text"] == "héllo\nworld" and "ts" in first
    assert json.loads(lines[1])["kind"] == "drop"


def test_audit_refuses_a_symlink(tmp_path: Path) -> None:
    (tmp_path / "target").write_text("")
    (tmp_path / "audit.jsonl").symlink_to(tmp_path / "target")
    with pytest.raises(OSError):
        Audit(tmp_path / "audit.jsonl").write("x")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_admind_store.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the store**

`src/heterodyne/admind/store.py`:

```python
"""admind's durable state: one SQLite file in WAL mode (ADR 0001 §8).

- `inbound`: every operator message ID admind has accepted, for replay protection and at-most-once
  delivery (a `received` row that never reached `dispatched` is answered after a restart, never
  replayed to the agent).
- `outbox`: replies and notices, sent in order with a stable idempotency key, so a resend after a crash
  is deduplicated by wn-agent (S4 step 4).
- `alerts`: alert files already relayed.
- `kv`: small named values (group, account, agent session, latch, ...).
"""

import functools
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inbound (
    message_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('received', 'dispatched', 'dropped')),
    received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    reply_to TEXT,
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    message_id TEXT);
CREATE TABLE IF NOT EXISTS alerts (name TEXT PRIMARY KEY, relayed_at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class OutboxRow:
    seq: int
    key: str
    reply_to: str | None
    text: str
    attempts: int


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Serialize a Store method: the daemon calls the store from the event loop and from worker threads."""
    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0].lock:  # type: ignore[attr-defined]  # args[0] is the Store
            return method(*args, **kwargs)
    return wrapper


def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


class Store:
    def __init__(self, path: Path) -> None:
        private_dir(path.parent)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        os.close(fd)
        path.chmod(0o600)
        self.lock = threading.RLock()
        # check_same_thread=False: calls are serialized by self.lock (see _locked), not by thread.
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    @_locked
    def close(self) -> None:
        self.db.close()

    @_locked
    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    @_locked
    def set(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    @_locked
    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM kv WHERE key = ?", (key,))

    @_locked
    def claim_inbound(self, message_id: str) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO inbound(message_id, status, received_at) "
                              "VALUES (?, 'received', ?)", (message_id, now()))
        return cur.rowcount == 1

    @_locked
    def set_inbound(self, message_id: str, status: str) -> None:
        self.db.execute("UPDATE inbound SET status = ? WHERE message_id = ?", (status, message_id))

    @_locked
    def inbound_with_status(self, status: str) -> list[str]:
        rows = self.db.execute("SELECT message_id FROM inbound WHERE status = ? ORDER BY received_at, rowid",
                               (status,)).fetchall()
        return [str(r[0]) for r in rows]

    @_locked
    def enqueue(self, key: str, text: str, reply_to: str | None) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO outbox(key, reply_to, text, status) "
                              "VALUES (?, ?, ?, 'pending')", (key, reply_to, text))
        return cur.rowcount == 1

    @_locked
    def pending(self) -> list[OutboxRow]:
        rows = self.db.execute("SELECT seq, key, reply_to, text, attempts FROM outbox "
                               "WHERE status = 'pending' ORDER BY seq").fetchall()
        return [OutboxRow(int(r[0]), str(r[1]), None if r[2] is None else str(r[2]), str(r[3]), int(r[4]))
                for r in rows]

    @_locked
    def mark_sent(self, seq: int, message_id: str | None) -> None:
        self.db.execute("UPDATE outbox SET status = 'sent', message_id = ? WHERE seq = ?", (message_id, seq))

    @_locked
    def mark_attempt(self, seq: int) -> int:
        self.db.execute("UPDATE outbox SET attempts = attempts + 1 WHERE seq = ?", (seq,))
        row = self.db.execute("SELECT attempts FROM outbox WHERE seq = ?", (seq,)).fetchone()
        return int(row[0])

    @_locked
    def mark_failed(self, seq: int) -> None:
        self.db.execute("UPDATE outbox SET status = 'failed' WHERE seq = ?", (seq,))

    @_locked
    def relayed(self, name: str) -> bool:
        return self.db.execute("SELECT 1 FROM alerts WHERE name = ?", (name,)).fetchone() is not None

    @_locked
    def relay_alert(self, name: str, key: str, text: str) -> bool:
        """Record the alert and queue its message in one transaction; False if already relayed."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cur = self.db.execute("INSERT OR IGNORE INTO alerts(name, relayed_at) VALUES (?, ?)", (name, now()))
            if cur.rowcount != 1:
                self.db.execute("ROLLBACK")
                return False
            self.db.execute("INSERT OR IGNORE INTO outbox(key, reply_to, text, status) "
                            "VALUES (?, NULL, ?, 'pending')", (key, text))
            self.db.execute("COMMIT")
            return True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
```

- [ ] **Step 4: Implement the audit log**

`src/heterodyne/admind/audit.py`:

```python
"""admind's append-only audit log: one JSON object per line (ADR 0001 §8).

Each record carries `ts` (UTC) and `kind`, plus the fields of the event (sender, text, action, result).
The file is opened per write with O_APPEND and fsync'd, so a crash never leaves a half-written earlier
record, and rotation by an external tool is safe. It is created 0600 and never followed through a
symlink. It is kept separate from beads on purpose.
"""

import json
import os
from pathlib import Path

from heterodyne.admind.store import now, private_dir


class Audit:
    def __init__(self, path: Path) -> None:
        self.path = path
        private_dir(path.parent)

    def write(self, kind: str, **fields: object) -> None:
        record = {"ts": now(), "kind": kind, **fields}
        data = (json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode()
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
```

- [ ] **Step 5: Run the tests and the full gate**

Run: `uv run pytest tests/test_admind_store.py -q && uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/heterodyne/admind/store.py src/heterodyne/admind/audit.py tests/test_admind_store.py
git commit -m "admind durable state: SQLite store and append-only audit log (plan 2 task 3)"
```

---

### Task 4: Pure logic — ingress guard, chunking, command parsing, alert contract

**Files:**
- Create: `src/heterodyne/admind/guard.py`, `src/heterodyne/admind/chunk.py`, `src/heterodyne/admind/commands.py`, `src/heterodyne/admind/alerts.py`
- Create: `tests/fakes/settings.py`
- Test: `tests/test_admind_logic.py`

**Interfaces:**
- Consumes: `control.InboundMessage`, `control.GroupStateChanged`; `AdmindSettings`.
- Produces:
  - guard: `Verdict(action: Literal["process","drop","ignore","latch"], reason: str)`, `judge_message(ev, *, group_id, operator_hex, latched) -> Verdict`, `judge_group_change(ev, *, group_id) -> Verdict`, `judge_member_count(count: int) -> Verdict`, and `MEMBERSHIP_CHANGES`.
  - `chunk.split(text: str, limit: int) -> list[str]`.
  - commands: `Command(name, arg=None, lines=40)`, `CommandError(ValueError)`, `parse(text) -> Command | None`, `has_control_chars(text) -> bool`, and the constants `HELP`, `TAIL_DEFAULT`, `TAIL_MAX`.
  - alerts: `ALERT_NAME`, `Alert(id, created_at, text)`, `scan(directory: Path) -> list[tuple[str, Alert | None]]`, and `render(name, alert, limit) -> str`.
  - `tests/fakes/settings.py`: `make_settings(tmp_path: Path, **overrides) -> AdmindSettings`.

- [ ] **Step 1: Write the settings helper for tests**

`tests/fakes/settings.py`:

```python
"""A complete AdmindSettings for tests, without going through config files."""

import dataclasses
from pathlib import Path
from typing import Any

from heterodyne.admind.settings import AdmindSettings
from heterodyne.marmot.nip19 import hex_to_npub

OPERATOR_HEX = "c3" * 32


def make_settings(tmp_path: Path, **overrides: Any) -> AdmindSettings:
    base = AdmindSettings(
        profile={"adapter": "claude-code", "model": "m1", "args": []},
        adapter_binary="claude", workdir=tmp_path, restart_units=("fake.service",),
        chunk_chars=4000, alert_poll_seconds=0.1, group_check_seconds=0.5, start_timeout_seconds=5,
        group_name="heterodyne admin", wn_agent="wn-agent", marmot_home=tmp_path / "marmot",
        relays=("wss://relay.example.org",), operator_hex=OPERATOR_HEX,
        operator_npub=hex_to_npub(OPERATOR_HEX), state_dir=tmp_path / "state" / "admind",
        alerts_dir=tmp_path / "state" / "alerts", service_manager="systemd")
    return dataclasses.replace(base, **overrides)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_admind_logic.py`:

```python
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.admind import alerts, chunk, commands, guard
from heterodyne.marmot.control import GroupStateChanged, InboundMessage, Message, Sender

OP = "c3" * 32
GROUP = "b2" * 32


def msg(sender: str = OP, *, is_self: bool = False, group: str = GROUP, text: str = "hi") -> InboundMessage:
    return InboundMessage(account_id_hex="a1" * 32, group_id_hex=group,
                          message=Message(message_id_hex="d4" * 32, text=text, recorded_at=1,
                                          sender=Sender(account_id_hex=sender, is_self=is_self)))


def test_guard_accepts_only_the_operator_in_the_group() -> None:
    assert guard.judge_message(msg(), group_id=GROUP, operator_hex=OP, latched=False).action == "process"
    upper = msg(OP.upper(), group=GROUP.upper())
    assert guard.judge_message(upper, group_id=GROUP, operator_hex=OP, latched=False).action == "process"
    assert guard.judge_message(msg("e5" * 32), group_id=GROUP, operator_hex=OP, latched=False).action == "drop"
    assert guard.judge_message(msg(group="f6" * 32), group_id=GROUP, operator_hex=OP,
                               latched=False).action == "drop"
    assert guard.judge_message(msg(is_self=True), group_id=GROUP, operator_hex=OP,
                               latched=False).action == "ignore"
    latched = guard.judge_message(msg(), group_id=GROUP, operator_hex=OP, latched=True)
    assert latched.action == "drop" and "latched" in latched.reason


@pytest.mark.parametrize("change", sorted(guard.MEMBERSHIP_CHANGES))
def test_membership_changes_latch(change: str) -> None:
    ev = GroupStateChanged(account_id_hex="a1" * 32, group_id_hex=GROUP, change=change)
    assert guard.judge_group_change(ev, group_id=GROUP).action == "latch"


def test_other_group_changes_are_ignored() -> None:
    ev = GroupStateChanged(account_id_hex="a1" * 32, group_id_hex=GROUP, change="group_renamed", detail="x")
    assert guard.judge_group_change(ev, group_id=GROUP).action == "ignore"
    elsewhere = GroupStateChanged(account_id_hex="a1" * 32, group_id_hex="f6" * 32, change="member_added")
    assert guard.judge_group_change(elsewhere, group_id=GROUP).action == "ignore"


@pytest.mark.parametrize(("count", "action"), [(1, "latch"), (2, "process"), (3, "latch")])
def test_member_count(count: int, action: str) -> None:
    assert guard.judge_member_count(count).action == action


@given(st.text(), st.integers(min_value=1, max_value=50))
def test_chunks_reassemble_exactly(text: str, limit: int) -> None:
    parts = chunk.split(text, limit)
    assert "".join(parts) == text
    assert all(0 < len(p) <= limit for p in parts)


def test_chunks_prefer_line_breaks() -> None:
    text = "a" * 30 + "\n" + "b" * 30
    assert chunk.split(text, 40) == ["a" * 30 + "\n", "b" * 30]
    assert chunk.split("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]
    assert chunk.split("", 10) == []


@pytest.mark.parametrize(("text", "expected"), [
    ("hello", None),
    ("!new", commands.Command("new")),
    ("!interrupt", commands.Command("interrupt")),
    ("!ps", commands.Command("ps")),
    ("!tail", commands.Command("tail", lines=40)),
    ("!tail 7", commands.Command("tail", lines=7)),
    ("!restart wsd.service", commands.Command("restart", arg="wsd.service")),
])
def test_parse_commands(text: str, expected: commands.Command | None) -> None:
    assert commands.parse(text) == expected


@pytest.mark.parametrize("text", ["!", "!restrat wsd", "!new now", "!tail x", "!tail 0", "!tail 99999",
                                  "!restart", "!restart a b", "! ps"])
def test_parse_errors(text: str) -> None:
    with pytest.raises(commands.CommandError):
        commands.parse(text)


def test_control_characters() -> None:
    assert not commands.has_control_chars("tab\tand\nnewline and émoji 👍")
    for bad in ("\x1b[201~", "\x00", "\r", "\x7f", "\x9b"):
        assert commands.has_control_chars(f"a{bad}b")


def test_alert_scan_and_render(tmp_path: Path) -> None:
    (tmp_path / "b-2.json").write_text(json.dumps({"id": "b-2", "created_at": "2026-09-30T00:00:00Z",
                                                   "text": "card undelivered"}))
    (tmp_path / "a1.json").write_text("{not json")
    (tmp_path / ".c3.json.tmp").write_text("{}")
    (tmp_path / "bad name.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("x")
    found = alerts.scan(tmp_path)
    assert [name for name, _ in found] == ["a1", "b-2", "bad name"]
    assert found[0][1] is None and found[2][1] is None
    alert = found[1][1]
    assert alert is not None and alert.text == "card undelivered"
    assert alerts.render("b-2", alert, 4000).startswith("🚨 wsd alert (2026-09-30T00:00:00Z): card undelivered")
    long = alerts.Alert(id="x", created_at="t", text="y" * 5000)
    assert len(alerts.render("x", long, 1000)) <= 1000
    assert "malformed" in alerts.render("a1", None, 4000)
    assert alerts.scan(tmp_path / "missing") == []


def test_alert_id_must_match_file_name(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps({"id": "b", "created_at": "t", "text": "x"}))
    assert alerts.scan(tmp_path) == [("a", None)]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_admind_logic.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Implement the guard**

`src/heterodyne/admind/guard.py`:

```python
"""Ingress decisions for admind (ADR 0001 §3.4, §8). Pure functions; the daemon acts on the verdicts.

- Only MLS-authenticated messages from the operator's exact key, in admind's own group, are processed.
- Any membership or admin change, or a member count other than two, latches admind (plan decision D4):
  `group_info` reports a count, not a member list, so a swap that keeps the count is visible only as
  an event.
"""

from dataclasses import dataclass
from typing import Literal

from heterodyne.marmot.control import GroupStateChanged, InboundMessage

MEMBERSHIP_CHANGES = frozenset({"member_added", "member_removed", "member_left", "admin_added",
                                "admin_removed"})


@dataclass(frozen=True)
class Verdict:
    action: Literal["process", "drop", "ignore", "latch"]
    reason: str


def judge_message(ev: InboundMessage, *, group_id: str, operator_hex: str, latched: bool) -> Verdict:
    if ev.group_id_hex.lower() != group_id:
        return Verdict("drop", "message from another group")
    sender = ev.message.sender
    if sender.is_self:
        return Verdict("ignore", "admind's own message")
    if sender.account_id_hex.lower() != operator_hex:
        return Verdict("drop", "sender is not the operator")
    if latched:
        return Verdict("drop", "admind is latched; run `admind rearm` on the host")
    return Verdict("process", "operator message")


def judge_group_change(ev: GroupStateChanged, *, group_id: str) -> Verdict:
    if ev.group_id_hex.lower() != group_id:
        return Verdict("ignore", "change in another group")
    if ev.change in MEMBERSHIP_CHANGES:
        return Verdict("latch", f"group membership changed ({ev.change})")
    return Verdict("ignore", f"group change {ev.change}")


def judge_member_count(count: int) -> Verdict:
    if count == 2:
        return Verdict("process", "two members")
    return Verdict("latch", f"group has {count} members, not 2")
```

- [ ] **Step 5: Implement chunking**

`src/heterodyne/admind/chunk.py`:

```python
"""Split a reply into message-sized chunks whose concatenation is exactly the original (ADR §8)."""


def split(text: str, limit: int) -> list[str]:
    """Chunks of at most `limit` characters, cut after a newline when one falls in the second half."""
    if limit < 1:
        raise ValueError("limit must be positive")
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        newline = rest.rfind("\n", 0, limit)
        cut = newline + 1 if newline >= limit // 2 else limit
        out.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        out.append(rest)
    return out
```

- [ ] **Step 6: Implement command parsing**

`src/heterodyne/admind/commands.py`:

```python
"""admind's built-in `!` commands (ADR 0001 §8). No LLM is involved.

Any text starting with `!` is a command: it is never passed to the agent (plan decision D3), because a
mistyped command must not become a prompt, and a leading `!` switches Claude Code's input to bash mode.
"""

import re
from dataclasses import dataclass
from typing import Literal

HELP = "admind commands: !new · !interrupt · !tail [n] · !restart <unit> · !ps"
TAIL_DEFAULT = 40
TAIL_MAX = 500
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

CommandName = Literal["new", "interrupt", "tail", "restart", "ps"]


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    name: CommandName
    arg: str | None = None
    lines: int = TAIL_DEFAULT


def has_control_chars(text: str) -> bool:
    """True if `text` has a C0 or C1 control character other than tab and newline."""
    return _CONTROL.search(text) is not None


def parse(text: str) -> Command | None:
    if not text.startswith("!"):
        return None
    if text[1:2].isspace() or len(text) == 1:
        raise CommandError(f"Empty command. {HELP}")
    name, *args = text[1:].split()
    if name in ("new", "interrupt", "ps"):
        if args:
            raise CommandError(f"!{name} takes no arguments.")
        return Command(name)  # type: ignore[arg-type]  # narrowed by the membership test
    if name == "tail":
        if len(args) > 1 or (args and not args[0].isdecimal()):
            raise CommandError(f"Usage: !tail [n], with n from 1 to {TAIL_MAX}.")
        lines = int(args[0]) if args else TAIL_DEFAULT
        if not 1 <= lines <= TAIL_MAX:
            raise CommandError(f"Usage: !tail [n], with n from 1 to {TAIL_MAX}.")
        return Command("tail", lines=lines)
    if name == "restart":
        if len(args) != 1:
            raise CommandError("Usage: !restart <unit>")
        return Command("restart", arg=args[0])
    raise CommandError(f"Unknown command !{name}. {HELP}")
```

Pyright does not narrow `name` to the `Literal` through `in (...)`. Instead of the `type: ignore`, write the first branch as three `if name == "new": return Command("new")` style checks, or use `cast(CommandName, name)`. Use whichever keeps `pyright` strict clean. The behaviour is the same.

- [ ] **Step 7: Implement the alert contract**

`src/heterodyne/admind/alerts.py`:

```python
"""The local alert-file contract between `wsd` and admind (ADR 0001 §6.2, §8).

`wsd` (plans 6 and 8) writes one file per alert into `<state>/alerts/`:

- name: `<id>.json`, where `<id>` matches ALERT_NAME;
- written atomically: to a dotfile first, then renamed into place (admind ignores dotfiles);
- content: `{"id": "<id>", "created_at": "<RFC 3339 UTC>", "text": "<plain text>"}`.

admind relays each file once, as a top-level message, and never modifies or deletes it: cleanup is the
writer's job. A file that doesn't follow the contract is relayed as a fixed "malformed alert" notice
that names the file, so a broken writer is still noticed.
"""

import re
from pathlib import Path

import msgspec

ALERT_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
MALFORMED = "🚨 A malformed alert file was found: {name}.json. Check the alert directory on the host."


class Alert(msgspec.Struct, frozen=True):
    id: str
    created_at: str
    text: str


def scan(directory: Path) -> list[tuple[str, Alert | None]]:
    """Alerts in name order. None marks a malformed file (bad name, bad JSON, or id != name)."""
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        return []
    found: list[tuple[str, Alert | None]] = []
    for path in entries:
        if path.name.startswith(".") or path.suffix != ".json" or not path.is_file():
            continue
        name = path.stem
        alert: Alert | None = None
        if ALERT_NAME.fullmatch(name):
            try:
                alert = msgspec.json.decode(path.read_bytes(), type=Alert)
            except (OSError, msgspec.DecodeError):
                alert = None
            if alert is not None and alert.id != name:
                alert = None
        found.append((name, alert))
    return found


def render(name: str, alert: Alert | None, limit: int) -> str:
    if alert is None:
        return MALFORMED.format(name=name[:64])[:limit]
    text = f"🚨 wsd alert ({alert.created_at}): {alert.text}"
    if len(text) > limit:
        marker = " … (truncated; see the alert file)"
        text = text[: limit - len(marker)] + marker
    return text
```

- [ ] **Step 8: Run the tests and the full gate**

Run: `uv run pytest tests/test_admind_logic.py -q && uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/heterodyne/admind/guard.py src/heterodyne/admind/chunk.py src/heterodyne/admind/commands.py src/heterodyne/admind/alerts.py tests/fakes/settings.py tests/test_admind_logic.py
git commit -m "admind ingress guard, chunking, ! command parsing and alert contract (plan 2 task 4)"
```

---

### Task 5: The admin agent — tmux, Claude argv, hook socket, `AdminAgent`

**Files:**
- Create: `src/heterodyne/tmux.py`, `src/heterodyne/agents/__init__.py`, `src/heterodyne/agents/claude_code.py`
- Create: `src/heterodyne/admind/hook.py`, `src/heterodyne/admind/agent.py`
- Test: `tests/test_tmux.py`, `tests/test_admind_agent.py`

**Interfaces:**
- Consumes: `Store`, `Audit`, `AdmindSettings`.
- Produces:
  - `Tmux(socket_name: str, binary: str = "tmux")` with `has_session(name)`, `new_session(name, cwd, argv)`, `pane_dead(name)`, `paste(name, text)`, `send_key(name, key)`, `capture(name, lines) -> str`, `kill(name)`, `kill_server()`; plus `TmuxError`.
  - `claude_code.interactive_argv(binary, profile, *, session_id, resume, settings_file, name) -> list[str]`.
  - hook:
    - `HookEvent(hook_event_name, session_id, transcript_path=None, last_assistant_message=None)`;
    - `HookServer(path, queue, audit)` with async `start()` and `close()`;
    - `hook_main(argv: list[str], stdin: bytes) -> int`, `reply_text(ev) -> str`, `last_assistant_text(path) -> str`, `hook_command(socket: Path) -> str`, `settings_json(command: str) -> str`.
  - agent: `AdminAgent(tmux, store, settings, hook_socket)` with `ensure_running() -> str` (`"adopted" | "resumed" | "launched"`), `started(session_id)`, `new() -> str`, `send(text)`, `interrupt()`, `tail(lines) -> str`, `alive() -> bool`, and the `session_id` property; plus `AgentStuck(RuntimeError)`, `SESSION = "admin"` and `TMUX_SOCKET = "heterodyne-admind"`.

- [ ] **Step 1: Write the failing tmux tests (real tmux, skipped if absent)**

`tests/test_tmux.py`:

```python
import shutil
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from heterodyne.tmux import Tmux

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


@pytest.fixture
def tmux() -> Iterator[Tmux]:
    t = Tmux(f"hz-test-{uuid.uuid4().hex[:8]}")
    yield t
    t.kill_server()


def wait_for(pred, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_paste_is_byte_for_byte_and_submits(tmux: Tmux, tmp_path: Path) -> None:
    out = tmp_path / "out"
    tmux.new_session("s", tmp_path, [sys.executable, "-c",
                                     f"import sys; open({str(out)!r}, 'w').write(sys.stdin.readline())"])
    assert tmux.has_session("s") and not tmux.has_session("s-other")
    text = 'héllo 👍 "quotes" $HOME `x` \\ tab\there'
    tmux.paste("s", text)
    wait_for(lambda: out.exists() and out.read_text() != "")
    assert out.read_text() == text + "\n"


def test_dead_pane_is_kept_for_capture(tmux: Tmux, tmp_path: Path) -> None:
    tmux.new_session("s", tmp_path, [sys.executable, "-c", "print('bye')"])
    wait_for(lambda: tmux.pane_dead("s"))
    assert "bye" in tmux.capture("s", 20)
    tmux.kill("s")
    assert not tmux.has_session("s")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tmux.py -q`
Expected: FAIL (`ModuleNotFoundError: heterodyne.tmux`).

- [ ] **Step 3: Implement the tmux wrapper**

`src/heterodyne/tmux.py`:

```python
"""A small tmux wrapper on a private server socket (`tmux -L <name>`).

Targets use exact matching: `=name` for sessions and `=name:` for the window or pane (tmux 3.4 rejects
`=name` as a window target). Text is passed to tmux as UTF-8 bytes, so a C locale under a service
manager can't mangle it. Pastes use bracketed paste (`paste-buffer -p`), so newlines inside the text do
not submit it early. The pane is kept after its process exits (`remain-on-exit`) so its last screen
can still be read.
"""

import subprocess
import time
import uuid
from pathlib import Path


class TmuxError(RuntimeError):
    pass


class Tmux:
    def __init__(self, socket_name: str, binary: str = "tmux") -> None:
        self.socket_name = socket_name
        self.binary = binary

    def _run(self, *args: str, data: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        proc = subprocess.run([self.binary, "-L", self.socket_name, *args], input=data,
                              capture_output=True, timeout=15, check=False)
        if check and proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip()
            raise TmuxError(f"tmux {args[0]} failed: {detail}")
        return proc

    def has_session(self, name: str) -> bool:
        return self._run("has-session", "-t", f"={name}", check=False).returncode == 0

    def new_session(self, name: str, cwd: Path, argv: list[str]) -> None:
        self._run("new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", str(cwd), "--", *argv)
        self._run("set-option", "-w", "-t", f"={name}:", "remain-on-exit", "on")

    def pane_dead(self, name: str) -> bool:
        proc = self._run("display-message", "-p", "-t", f"={name}:", "#{pane_dead}")
        return proc.stdout.decode().strip() == "1"

    def paste(self, name: str, text: str) -> None:
        buffer = f"hz-{uuid.uuid4().hex}"
        self._run("load-buffer", "-b", buffer, "-", data=text.encode("utf-8"))
        try:
            self._run("paste-buffer", "-p", "-d", "-b", buffer, "-t", f"={name}:")
            time.sleep(0.3)  # let the application finish reading the paste before Enter arrives
            self._run("send-keys", "-t", f"={name}:", "Enter")
        finally:
            self._run("delete-buffer", "-b", buffer, check=False)

    def send_key(self, name: str, key: str) -> None:
        self._run("send-keys", "-t", f"={name}:", key)

    def capture(self, name: str, lines: int) -> str:
        proc = self._run("capture-pane", "-p", "-J", "-S", f"-{lines}", "-t", f"={name}:")
        return proc.stdout.decode("utf-8", "replace").rstrip("\n")

    def kill(self, name: str) -> None:
        self._run("kill-session", "-t", f"={name}", check=False)

    def kill_server(self) -> None:
        self._run("kill-server", check=False)
```

- [ ] **Step 4: Run to verify the tmux tests pass**

Run: `uv run pytest tests/test_tmux.py -q`
Expected: PASS (or SKIPPED where tmux is absent).

- [ ] **Step 5: Write the failing agent and hook tests**

`tests/test_admind_agent.py`:

```python
import asyncio
import contextlib
import json
import socket
from pathlib import Path

from fakes.settings import make_settings

from heterodyne.admind.agent import AdminAgent, AgentStuck
from heterodyne.admind.audit import Audit
from heterodyne.admind.hook import (
    HookEvent,
    HookServer,
    hook_command,
    hook_main,
    last_assistant_text,
    reply_text,
    settings_json,
)
from heterodyne.admind.store import Store
from heterodyne.agents.claude_code import interactive_argv


class FakeTmux:
    def __init__(self) -> None:
        self.sessions: dict[str, list[str]] = {}
        self.dead: set[str] = set()
        self.pasted: list[str] = []
        self.keys: list[str] = []

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, name: str, cwd: Path, argv: list[str]) -> None:
        self.sessions[name] = argv

    def pane_dead(self, name: str) -> bool:
        return name in self.dead

    def paste(self, name: str, text: str) -> None:
        self.pasted.append(text)

    def send_key(self, name: str, key: str) -> None:
        self.keys.append(key)

    def capture(self, name: str, lines: int) -> str:
        return f"screen of {name}, {lines} lines"

    def kill(self, name: str) -> None:
        self.sessions.pop(name, None)
        self.dead.discard(name)


def test_interactive_argv() -> None:
    profile = {"adapter": "claude-code", "model": "m1", "args": ["--verbose"]}
    new = interactive_argv("claude", profile, session_id="S", resume=False,
                           settings_file=Path("/x/s.json"), name="admin")
    assert new == ["claude", "--session-id", "S", "--model", "m1", "--permission-mode", "bypassPermissions",
                   "--settings", "/x/s.json", "--name", "admin", "--verbose"]
    again = interactive_argv("claude", {"adapter": "claude-code"}, session_id="S", resume=True,
                             settings_file=Path("/x/s.json"), name="admin")
    assert again[:3] == ["claude", "--resume", "S"] and "--model" not in again


def make_agent(tmp_path: Path) -> tuple[AdminAgent, FakeTmux, Store]:
    s = make_settings(tmp_path)
    store = Store(s.state_dir / "admind.db")
    tmux = FakeTmux()
    return AdminAgent(tmux, store, s, s.state_dir / "hook.sock"), tmux, store  # type: ignore[arg-type]


def test_launch_resume_adopt_and_new(tmp_path: Path) -> None:
    agent, tmux, store = make_agent(tmp_path)
    assert agent.ensure_running() == "launched"
    sid = agent.session_id
    assert sid is not None and tmux.sessions["admin"][1:3] == ["--session-id", sid]
    settings = json.loads((tmp_path / "state" / "admind" / "claude-settings.json").read_text())
    assert set(settings["hooks"]) == {"SessionStart", "Stop"}
    assert agent.ensure_running() == "adopted"
    agent.started(sid)
    tmux.dead.add("admin")                      # crashed after a successful start
    assert agent.ensure_running() == "resumed"
    assert tmux.sessions["admin"][1:3] == ["--resume", sid]
    assert agent.new() == "launched"
    assert agent.session_id != sid


def test_crash_loop_stops_after_three_launches(tmp_path: Path) -> None:
    agent, tmux, _ = make_agent(tmp_path)
    for _ in range(3):
        tmux.kill("admin")
        agent.ensure_running()
    tmux.kill("admin")
    try:
        agent.ensure_running()
    except AgentStuck:
        pass
    else:
        raise AssertionError("expected AgentStuck")
    assert agent.new() == "launched"            # !new resets the counter


def test_send_interrupt_tail(tmp_path: Path) -> None:
    agent, tmux, _ = make_agent(tmp_path)
    assert agent.tail(5) == "(no admin agent session)"
    agent.ensure_running()
    agent.send("hello\nworld")
    agent.interrupt()
    assert tmux.pasted == ["hello\nworld"] and tmux.keys == ["Escape"]
    assert agent.tail(5) == "screen of admin, 5 lines"


def test_reply_text_prefers_the_hook_field(tmp_path: Path) -> None:
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "final"},
                                                      {"type": "text", "text": "answer"}]}},
    ]) + "\nnot json\n")
    assert last_assistant_text(transcript) == "final\nanswer"
    assert reply_text(HookEvent("Stop", "S", str(transcript), "from hook")) == "from hook"
    assert reply_text(HookEvent("Stop", "S", str(transcript), None)) == "final\nanswer"
    # a transcript path that isn't this session's file is not read
    assert reply_text(HookEvent("Stop", "OTHER", str(transcript), None)) == ""
    assert last_assistant_text(tmp_path / "missing.jsonl") == ""


def test_hook_round_trip_over_the_socket(tmp_path: Path) -> None:
    sock = tmp_path / "hook.sock"

    async def body() -> HookEvent:
        queue: asyncio.Queue[HookEvent] = asyncio.Queue()
        server = HookServer(sock, queue, Audit(tmp_path / "audit.jsonl"))
        await server.start()
        stdin = json.dumps({"hook_event_name": "Stop", "session_id": "S", "last_assistant_message": "ok",
                            "stop_hook_active": False, "cwd": "/x"}).encode()
        rc = await asyncio.to_thread(hook_main, ["--socket", str(sock)], stdin)
        assert rc == 0
        ev = await asyncio.wait_for(queue.get(), 5)
        await server.close()
        return ev
    ev = asyncio.run(body())
    assert ev == HookEvent("Stop", "S", None, "ok")


def test_hook_socket_is_private(tmp_path: Path) -> None:
    sock = tmp_path / "hook.sock"

    async def body() -> int:
        server = HookServer(sock, asyncio.Queue(), Audit(tmp_path / "audit.jsonl"))
        await server.start()
        mode = sock.stat().st_mode & 0o777
        await server.close()
        return mode
    assert asyncio.run(body()) == 0o600
    assert not sock.exists()


def test_hook_never_fails_the_agent(tmp_path: Path) -> None:
    assert hook_main(["--socket", str(tmp_path / "absent.sock")], b'{"hook_event_name":"Stop"}') == 0
    assert hook_main(["--socket", str(tmp_path / "absent.sock")], b"not json") == 0
    assert hook_main([], b"{}") == 0


def test_hook_server_drops_garbage(tmp_path: Path) -> None:
    sock = tmp_path / "hook.sock"

    async def body() -> int:
        queue: asyncio.Queue[HookEvent] = asyncio.Queue()
        server = HookServer(sock, queue, Audit(tmp_path / "audit.jsonl"))
        await server.start()

        def send() -> None:
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(str(sock))
                s.sendall(b'{"no": "fields"}\n')
        await asyncio.to_thread(send)
        await asyncio.sleep(0.2)
        await server.close()
        return queue.qsize()
    assert asyncio.run(body()) == 0
    assert "hook" in (tmp_path / "audit.jsonl").read_text()


def test_hook_server_drops_oversized_frames(tmp_path: Path) -> None:
    sock = tmp_path / "hook.sock"

    async def body() -> int:
        queue: asyncio.Queue[HookEvent] = asyncio.Queue()
        server = HookServer(sock, queue, Audit(tmp_path / "audit.jsonl"))
        await server.start()

        def send() -> None:
            # admind closes the connection mid-frame, so the client may see a broken pipe.
            with contextlib.suppress(OSError), socket.socket(socket.AF_UNIX) as s:
                s.connect(str(sock))
                s.sendall(b'{"hook_event_name":"Stop","session_id":"S","last_assistant_message":"'
                          + b"x" * (2 * 1024 * 1024) + b'"}\n')
        await asyncio.to_thread(send)
        await asyncio.sleep(0.3)
        await server.close()
        return queue.qsize()
    assert asyncio.run(body()) == 0


def test_settings_json_wires_both_hooks(tmp_path: Path) -> None:
    command = hook_command(tmp_path / "hook sock")
    assert "'" in command                      # the socket path is shell-quoted
    data = json.loads(settings_json(command))
    for event in ("SessionStart", "Stop"):
        assert data["hooks"][event] == [{"hooks": [{"type": "command", "command": command}]}]
```

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/test_admind_agent.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 7: Implement the Claude Code argv builder**

`src/heterodyne/agents/__init__.py`:

```python
"""Agent adapters: pure launch-shape builders per harness CLI (ADR 0001 §4). Plan 4 adds AgentRuntime."""
```

`src/heterodyne/agents/claude_code.py`:

```python
"""The `claude-code` adapter's interactive launch shape (ADR 0001 §4.2; Claude Code 2.1.x flags)."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast


def interactive_argv(binary: str, profile: Mapping[str, Any], *, session_id: str, resume: bool,
                     settings_file: Path, name: str) -> list[str]:
    """Launch (or resume) an interactive session with a fixed ID, hooks from `settings_file`, and
    permission prompts bypassed. The profile's `args` are appended verbatim."""
    argv = [binary, "--resume" if resume else "--session-id", session_id]
    model = profile.get("model")
    if isinstance(model, str) and model:
        argv += ["--model", model]
    argv += ["--permission-mode", "bypassPermissions", "--settings", str(settings_file), "--name", name]
    args = profile.get("args", [])
    if isinstance(args, list):
        argv += [str(a) for a in cast(list[Any], args)]
    return argv
```

- [ ] **Step 8: Implement the hook socket and the hook client**

`src/heterodyne/admind/hook.py`:

```python
"""The admin agent's hooks reach admind over a private Unix socket (ADR 0001 §2, §8).

Claude Code runs `python -m heterodyne.admind hook --socket <path>` on SessionStart and Stop. The hook
forwards its stdin JSON as one line and **always exits 0**: a Stop hook that exits 2 would block the
agent from stopping, and admind being down must never wedge the admin agent (it is the recovery path).
The agent's reply is the Stop hook's `last_assistant_message`. If a Claude Code build omits that
optional field, the fallback is the last assistant text in the session's own transcript file
(plan decision D2). The screen is never scraped.

Trust boundary: the socket is 0600 in a 0700 directory, so only the service user can write to it, and
events for any session but the current one are dropped. A process already running as the service user
can still forge a reply event; that is the same-user residual risk ADR §3.4 accepts.
"""

import argparse
import asyncio
import contextlib
import json
import os
import shlex
import socket
import sys
from pathlib import Path
from typing import Any, cast

import msgspec

from heterodyne.admind.audit import Audit

MAX_HOOK_FRAME = 1024 * 1024


class HookEvent(msgspec.Struct, frozen=True):
    hook_event_name: str
    session_id: str
    transcript_path: str | None = None
    last_assistant_message: str | None = None


def hook_command(sock: Path) -> str:
    return f"{shlex.quote(sys.executable)} -m heterodyne.admind hook --socket {shlex.quote(str(sock))}"


def settings_json(command: str) -> str:
    entry = [{"hooks": [{"type": "command", "command": command}]}]
    return json.dumps({"hooks": {"SessionStart": entry, "Stop": entry}}, indent=2)


def hook_main(argv: list[str], stdin: bytes) -> int:
    parser = argparse.ArgumentParser(prog="admind hook", add_help=False)
    parser.add_argument("--socket")
    args, _ = parser.parse_known_args(argv)
    if not args.socket:
        print("admind hook: --socket is required", file=sys.stderr)
        return 0
    try:
        line = json.dumps(json.loads(stdin), separators=(",", ":")).encode() + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(2)
            conn.connect(args.socket)
            conn.sendall(line)
    except (OSError, ValueError) as exc:
        print(f"admind hook: not delivered ({type(exc).__name__})", file=sys.stderr)
    return 0


def last_assistant_text(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    text = ""
    for line in lines:
        try:
            record: Any = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or cast(dict[str, Any], record).get("type") != "assistant":
            continue
        message: Any = cast(dict[str, Any], record).get("message")
        content: Any = cast(dict[str, Any], message).get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        parts = [cast(dict[str, Any], block)["text"] for block in cast(list[Any], content)
                 if isinstance(block, dict) and cast(dict[str, Any], block).get("type") == "text"
                 and isinstance(cast(dict[str, Any], block).get("text"), str)]
        if parts:
            text = "\n".join(parts)
    return text


def reply_text(ev: HookEvent) -> str:
    if ev.last_assistant_message:
        return ev.last_assistant_message
    if ev.transcript_path:
        path = Path(ev.transcript_path)
        if path.name == f"{ev.session_id}.jsonl":
            return last_assistant_text(path)
    return ""


class HookServer:
    def __init__(self, path: Path, queue: asyncio.Queue[HookEvent], audit: Audit) -> None:
        self.path = path
        self.queue = queue
        self.audit = audit
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        old = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._handle, path=str(self.path),
                                                           limit=MAX_HOOK_FRAME + 1)
        finally:
            os.umask(old)
        self.path.chmod(0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)
        self.path.unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            await self.queue.put(msgspec.json.decode(line, type=HookEvent))
        except (TimeoutError, ValueError, msgspec.DecodeError) as exc:
            self.audit.write("hook", result="dropped", error=type(exc).__name__)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
```

(`msgspec.DecodeError` is a `ValueError` subclass, which is harmless. It is listed for clarity.)

- [ ] **Step 9: Implement `AdminAgent`**

`src/heterodyne/admind/agent.py`:

```python
"""The persistent admin agent session (ADR 0001 §8; plan decision D8).

One interactive `claude` runs in tmux session `admin` on admind's private tmux server. Its session ID is
recorded in the store:
- `ensure_running` adopts a live session;
- it resumes the recorded ID (with `--resume`) once that ID has been seen to start;
- otherwise it launches with a fresh ID;
- three launches in a row without a SessionStart hook raise AgentStuck until `new()`.
It runs as the service user, unsandboxed, with permission prompts bypassed: a deliberate operator
decision (§8).
"""

import os
import uuid
from pathlib import Path
from typing import Protocol

from heterodyne.admind.hook import hook_command, settings_json
from heterodyne.admind.settings import AdmindSettings
from heterodyne.admind.store import Store
from heterodyne.agents.claude_code import interactive_argv

SESSION = "admin"
TMUX_SOCKET = "heterodyne-admind"
MAX_LAUNCHES_WITHOUT_START = 3


class AgentStuck(RuntimeError):
    pass


class TmuxLike(Protocol):
    def has_session(self, name: str) -> bool: ...
    def new_session(self, name: str, cwd: Path, argv: list[str]) -> None: ...
    def pane_dead(self, name: str) -> bool: ...
    def paste(self, name: str, text: str) -> None: ...
    def send_key(self, name: str, key: str) -> None: ...
    def capture(self, name: str, lines: int) -> str: ...
    def kill(self, name: str) -> None: ...


class AdminAgent:
    def __init__(self, tmux: TmuxLike, store: Store, settings: AdmindSettings, hook_socket: Path) -> None:
        self.tmux = tmux
        self.store = store
        self.settings = settings
        self.hook_socket = hook_socket
        self.settings_file = settings.state_dir / "claude-settings.json"

    @property
    def session_id(self) -> str | None:
        return self.store.get("agent_session")

    def alive(self) -> bool:
        return self.tmux.has_session(SESSION) and not self.tmux.pane_dead(SESSION)

    def _write_settings(self) -> None:
        tmp = self.settings_file.with_name(f".{self.settings_file.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(settings_json(hook_command(self.hook_socket)))
        tmp.replace(self.settings_file)

    def ensure_running(self) -> str:
        if self.alive() and self.session_id is not None:
            return "adopted"
        self.tmux.kill(SESSION)  # a dead pane kept for !tail, or a session admind has no record of
        launches = int(self.store.get("launches_without_start") or "0")
        if launches >= MAX_LAUNCHES_WITHOUT_START:
            raise AgentStuck(f"the admin agent did not start after {launches} launches; use !tail, then !new")
        sid = self.session_id
        resume = sid is not None and self.store.get("session_started") == sid
        if sid is None or not resume:
            sid = str(uuid.uuid4())
            self.store.set("agent_session", sid)
        self.store.set("launches_without_start", str(launches + 1))
        self._write_settings()
        argv = interactive_argv(self.settings.adapter_binary, self.settings.profile, session_id=sid,
                                resume=resume, settings_file=self.settings_file, name=SESSION)
        self.tmux.new_session(SESSION, self.settings.workdir, argv)
        return "resumed" if resume else "launched"

    def started(self, session_id: str) -> None:
        self.store.set("session_started", session_id)
        self.store.set("launches_without_start", "0")

    def new(self) -> str:
        self.tmux.kill(SESSION)
        self.store.delete("agent_session")
        self.store.set("launches_without_start", "0")
        return self.ensure_running()

    def send(self, text: str) -> None:
        self.tmux.paste(SESSION, text)

    def interrupt(self) -> None:
        self.tmux.send_key(SESSION, "Escape")

    def tail(self, lines: int) -> str:
        if not self.tmux.has_session(SESSION):
            return "(no admin agent session)"
        return self.tmux.capture(SESSION, lines)
```

- [ ] **Step 10: Run the tests and the full gate**

Run: `uv run pytest tests/test_tmux.py tests/test_admind_agent.py -q && uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/heterodyne/tmux.py src/heterodyne/agents src/heterodyne/admind/hook.py src/heterodyne/admind/agent.py tests/test_tmux.py tests/test_admind_agent.py
git commit -m "admin agent: tmux wrapper, claude argv, hook socket, persistent session (plan 2 task 5)"
```

---

### Task 6: Service control and the `!` command runner

**Files:**
- Modify: `src/heterodyne/services.py`
- Modify: `src/heterodyne/admind/commands.py` (add `CommandRunner`)
- Test: `tests/test_services.py`, `tests/test_admind_commands.py`

**Interfaces:**
- Consumes: `UNIT_NAME`, `AdminAgent`, `Command`.
- Produces:
  - services: `UnitStatus(unit, active, sub, since)` with `.line() -> str`; the `ServiceManager` Protocol (`restart(unit) -> tuple[bool, str]`, `status(unit) -> UnitStatus`); `Systemd(binary="systemctl")`; `for_backend(name: str) -> ServiceManager`.
  - `commands.CommandRunner(agent, services, restart_units, wn_alive: Callable[[], bool])` with `.run(cmd: Command) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_services.py`:

```python
import os
import stat
from pathlib import Path

import pytest

from heterodyne.config import ConfigError
from heterodyne.services import Systemd, UnitStatus, for_backend

FAKE = """#!/bin/sh
echo "$@" >> "{log}"
case "$2" in
  restart) [ "$4" = "bad.service" ] && {{ echo "Unit bad.service not found." >&2; exit 5; }}; exit 0;;
  show) printf 'ActiveState=active\\nSubState=running\\nActiveEnterTimestamp=Wed 2026-09-30 10:00:00 UTC\\n';;
esac
"""


def fake_systemctl(tmp_path: Path) -> tuple[str, Path]:
    log = tmp_path / "calls"
    script = tmp_path / "systemctl"
    script.write_text(FAKE.format(log=log))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), log


def test_restart_and_status_use_user_scope_and_end_of_options(tmp_path: Path) -> None:
    binary, log = fake_systemctl(tmp_path)
    sd = Systemd(binary)
    assert sd.restart("wsd.service") == (True, "")
    ok, detail = sd.restart("bad.service")
    assert not ok and "not found" in detail
    st = sd.status("wsd.service")
    assert st == UnitStatus("wsd.service", "active", "running", "Wed 2026-09-30 10:00:00 UTC")
    assert st.line() == "wsd.service: active (running) since Wed 2026-09-30 10:00:00 UTC"
    calls = log.read_text().splitlines()
    assert calls[0] == "--user restart -- wsd.service"
    assert calls[2].startswith("--user show --property=ActiveState,SubState,ActiveEnterTimestamp -- ")


def test_invalid_unit_names_never_reach_systemctl(tmp_path: Path) -> None:
    binary, log = fake_systemctl(tmp_path)
    with pytest.raises(ValueError):
        Systemd(binary).restart("--now")
    assert not log.exists()


def test_backend_selection() -> None:
    assert isinstance(for_backend("systemd"), Systemd)
    with pytest.raises(ConfigError, match="launchd"):
        for_backend("launchd")
    assert os.name == "posix"
```

`tests/test_admind_commands.py`:

```python
from heterodyne.admind.commands import Command, CommandRunner
from heterodyne.services import UnitStatus


class Agent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def new(self) -> str:
        self.calls.append("new")
        return "launched"

    def interrupt(self) -> None:
        self.calls.append("interrupt")

    def tail(self, lines: int) -> str:
        return f"{lines} lines"

    def alive(self) -> bool:
        return True


class Services:
    def __init__(self) -> None:
        self.restarted: list[str] = []

    def restart(self, unit: str) -> tuple[bool, str]:
        self.restarted.append(unit)
        return (unit != "broken.service", "exit 1")

    def status(self, unit: str) -> UnitStatus:
        return UnitStatus(unit, "active", "running", "")


def runner() -> tuple[CommandRunner, Agent, Services]:
    agent, services = Agent(), Services()
    units = ("wsd.service", "broken.service")
    return CommandRunner(agent, services, units, lambda: False), agent, services  # type: ignore[arg-type]


def test_restart_only_allowlisted_units() -> None:
    r, _, services = runner()
    assert r.run(Command("restart", arg="wsd.service")) == "Restarted wsd.service."
    assert "failed" in r.run(Command("restart", arg="broken.service"))
    refused = r.run(Command("restart", arg="sshd.service"))
    assert refused.startswith("Refused") and services.restarted == ["wsd.service", "broken.service"]


def test_other_commands() -> None:
    r, agent, _ = runner()
    assert "fresh" in r.run(Command("new"))
    assert r.run(Command("interrupt")) == "Sent Esc to the admin agent."
    assert r.run(Command("tail", lines=12)) == "```\n12 lines\n```"
    assert agent.calls == ["new", "interrupt"]
    ps = r.run(Command("ps"))
    assert "wsd.service: active (running)" in ps
    assert "wn-agent (admind): down" in ps and "admin agent: running" in ps
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_services.py tests/test_admind_commands.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement the service manager**

Replace `src/heterodyne/services.py` with:

```python
"""Service-manager control (ADR 0001 §3.2). The backend comes from host config, never sys.platform.

v1 is Linux-only (§12), so only systemd user units are implemented. launchd is phase 2.
"""

import re
import subprocess
from dataclasses import dataclass
from typing import Protocol

from heterodyne.config.errors import ConfigError

# A unit name that can't be read as an option, a path or a glob.
UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9@_.:-]*\.(?:service|target|timer|socket)")


@dataclass(frozen=True)
class UnitStatus:
    unit: str
    active: str
    sub: str
    since: str

    def line(self) -> str:
        text = f"{self.unit}: {self.active} ({self.sub})"
        return f"{text} since {self.since}" if self.since else text


class ServiceManager(Protocol):
    def restart(self, unit: str) -> tuple[bool, str]: ...
    def status(self, unit: str) -> UnitStatus: ...


def _check(unit: str) -> None:
    if not UNIT_NAME.fullmatch(unit):
        raise ValueError(f"not a unit name: {unit!r}")


class Systemd:
    def __init__(self, binary: str = "systemctl") -> None:
        self.binary = binary

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([self.binary, "--user", *args], capture_output=True, encoding="utf-8",
                              errors="replace", timeout=120, check=False)

    def restart(self, unit: str) -> tuple[bool, str]:
        _check(unit)
        proc = self._run("restart", "--", unit)
        return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[:500]

    def status(self, unit: str) -> UnitStatus:
        _check(unit)
        proc = self._run("show", "--property=ActiveState,SubState,ActiveEnterTimestamp", "--", unit)
        fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
        return UnitStatus(unit, fields.get("ActiveState", "unknown"), fields.get("SubState", "unknown"),
                          fields.get("ActiveEnterTimestamp", ""))


def for_backend(name: str) -> ServiceManager:
    if name == "systemd":
        return Systemd()
    raise ConfigError(f"service manager {name!r} is not supported yet; v1 supports systemd only "
                      "(launchd is phase 2)")
```

- [ ] **Step 4: Add `CommandRunner` to `commands.py`**

Append to `src/heterodyne/admind/commands.py`:

```python
class AgentControl(Protocol):
    def new(self) -> str: ...
    def interrupt(self) -> None: ...
    def tail(self, lines: int) -> str: ...
    def alive(self) -> bool: ...


class CommandRunner:
    """Executes parsed commands and returns the reply text. Synchronous: the daemon runs it in a thread."""

    def __init__(self, agent: AgentControl, services: ServiceManager, restart_units: tuple[str, ...],
                 wn_alive: Callable[[], bool]) -> None:
        self.agent = agent
        self.services = services
        self.restart_units = restart_units
        self.wn_alive = wn_alive

    def run(self, cmd: Command) -> str:
        if cmd.name == "new":
            self.agent.new()
            return "Started a fresh admin agent session."
        if cmd.name == "interrupt":
            self.agent.interrupt()
            return "Sent Esc to the admin agent."
        if cmd.name == "tail":
            return f"```\n{self.agent.tail(cmd.lines)}\n```"
        if cmd.name == "restart":
            unit = cmd.arg or ""
            if unit not in self.restart_units:
                allowed = ", ".join(self.restart_units) or "empty"
                return f"Refused: {unit} is not in admind's restart allowlist ({allowed})."
            ok, detail = self.services.restart(unit)
            return f"Restarted {unit}." if ok else f"Restart of {unit} failed: {detail}"
        lines = [self.services.status(unit).line() for unit in self.restart_units]
        lines.append(f"wn-agent (admind): {'running' if self.wn_alive() else 'down'}")
        lines.append(f"admin agent: {'running' if self.agent.alive() else 'not running'}")
        return "\n".join(lines)
```

and add these imports at the top of the file:

```python
from collections.abc import Callable
from typing import Literal, Protocol

from heterodyne.services import ServiceManager
```

(`AgentStuck` from `agent.new()` propagates to the daemon, which reports it; see Task 8.)

- [ ] **Step 5: Run the tests and the full gate**

Run: `uv run pytest tests/test_services.py tests/test_admind_commands.py -q && uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/heterodyne/services.py src/heterodyne/admind/commands.py tests/test_services.py tests/test_admind_commands.py
git commit -m "systemd service control and the admind ! command runner (plan 2 task 6)"
```

---

### Task 7: `wn-agent` supervision and the `admind` CLI (`init`, `rearm`, `unit`, `hook`)

**Files:**
- Create: `src/heterodyne/admind/wnagent.py`, `src/heterodyne/admind/unit.py`, `src/heterodyne/admind/cli.py`, `src/heterodyne/admind/__main__.py`
- Test: `tests/test_admind_cli.py`

**Interfaces:**
- Consumes: `ControlClient`, `Store`, `Audit`, `AdmindSettings`, `settings.resolve`, `hook.hook_main`.
- Produces:
  - `WnAgent(binary, home, relays, audit)` with:
    - attributes `socket_path` and `token_path`;
    - `prepare()`, `token() -> str`, `argv() -> list[str]`, `bootstrap_argv(label) -> list[str]`;
    - async `start(client, wait=30.0)`, `supervise(client)`, `stop()`, `account(client) -> str`;
    - `alive() -> bool`;
    - plus `WnAgentError`.
  - `unit.render(python: str, env: Mapping[str, str]) -> str`.
  - `cli.main(argv: list[str] | None = None) -> int` with subcommands `init`, `rearm`, `unit`, `hook`, and (Task 8) `run`. `EX_CONFIG = 78`.

- [ ] **Step 1: Write the failing tests**

`tests/test_admind_cli.py`:

```python
import asyncio
import os
import stat
import sys
from pathlib import Path

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent
from fakes.settings import make_settings

from heterodyne.admind import cli, unit
from heterodyne.admind.audit import Audit
from heterodyne.admind.store import Store
from heterodyne.admind.wnagent import WnAgent, WnAgentError
from heterodyne.marmot.control import ControlClient

FAKE_WN = """#!{python}
import asyncio, sys
sys.path.insert(0, {tests!r})
from pathlib import Path
from fakes.fake_wn_agent import FakeWnAgent
args = sys.argv[1:]
if args and args[0] == "bootstrap":
    # Like the real CLI, bootstrap is a client of the running daemon's socket, not a second home opener.
    import json, socket
    token = Path(args[args.index("--auth-token-file") + 1]).read_text().strip()
    with socket.socket(socket.AF_UNIX) as conn:
        conn.connect(args[args.index("--socket") + 1])
        conn.sendall(json.dumps({{"marmot_agent_control": "marmot.agent-control.v2", "id": "b",
                                  "type": "account_list", "auth_token": token}}).encode() + b"\\n")
        ok = b'"account_list"' in conn.recv(65536)
    with Path({log!r}).open("a") as log:
        log.write("bootstrap " + " ".join(args[1:]) + "\\n")
    sys.exit(0 if ok else 1)
sock = Path(args[args.index("--socket") + 1])
token = Path(args[args.index("--auth-token-file") + 1]).read_text().strip()
async def main():
    fake = FakeWnAgent(sock, token)
    await fake.start()
    await asyncio.Event().wait()
asyncio.run(main())
"""


def fake_wn_agent(tmp_path: Path) -> tuple[str, Path]:
    log = tmp_path / "wn.log"
    script = tmp_path / "wn-agent"
    tests = str(Path(__file__).parent)
    script.write_text(FAKE_WN.format(python=sys.executable, tests=tests, log=str(log)))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), log


def test_wn_agent_argv_token_and_private_home(tmp_path: Path) -> None:
    wn = WnAgent("wn-agent", tmp_path / "home", ("wss://a", "wss://b"), Audit(tmp_path / "audit.jsonl"))
    wn.prepare()
    assert stat.S_IMODE((tmp_path / "home").stat().st_mode) == 0o700
    assert stat.S_IMODE(wn.token_path.stat().st_mode) == 0o600
    token = wn.token()
    wn.prepare()
    assert wn.token() == token and len(token) == 64
    assert stat.S_IMODE(wn.socket_path.parent.stat().st_mode) == 0o700
    assert wn.argv() == ["wn-agent", "--home", str(tmp_path / "home"), "--socket", str(wn.socket_path),
                         "--auth-token-file", str(wn.token_path), "--relay", "wss://a", "--relay", "wss://b"]
    assert "--invite-policy" in wn.bootstrap_argv("heterodyne-admind")
    assert "deny" in wn.bootstrap_argv("heterodyne-admind")


def test_token_file_must_be_private_and_not_a_symlink(tmp_path: Path) -> None:
    wn = WnAgent("wn-agent", tmp_path / "home", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
    wn.prepare()
    wn.token_path.chmod(0o644)
    with pytest.raises(WnAgentError, match="0600"):
        wn.token()
    wn.token_path.unlink()
    (tmp_path / "elsewhere").write_text("x" * 64)
    (tmp_path / "elsewhere").chmod(0o600)
    wn.token_path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(WnAgentError, match="cannot open"):
        wn.token()


def test_start_supervise_and_stop_a_fake_wn_agent(tmp_path: Path) -> None:
    binary, _ = fake_wn_agent(tmp_path)

    async def body() -> None:
        wn = WnAgent(binary, tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        wn.prepare()
        client = ControlClient(wn.socket_path, wn.token(), timeout=2)
        await wn.start(client, wait=15)
        assert wn.alive() and await wn.account(client) == ACCOUNT
        await wn.stop()
        assert not wn.alive()
    asyncio.run(body())


def test_start_fails_when_the_binary_exits(tmp_path: Path) -> None:
    async def body() -> None:
        wn = WnAgent("false", tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        wn.prepare()
        with pytest.raises(WnAgentError, match="exited"):
            await wn.start(ControlClient(wn.socket_path, wn.token(), timeout=1), wait=5)
    asyncio.run(body())


def test_account_requires_exactly_one_local_signing_account(tmp_path: Path) -> None:
    async def body() -> None:
        fake = FakeWnAgent(tmp_path / "s.sock", None)
        fake.accounts = []
        await fake.start()
        wn = WnAgent("wn-agent", tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
        with pytest.raises(WnAgentError, match="0 local-signing"):
            await wn.account(ControlClient(tmp_path / "s.sock", None))
        await fake.stop()
    asyncio.run(body())


def test_init_creates_identity_and_group_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    binary, log = fake_wn_agent(tmp_path)
    s = make_settings(tmp_path, wn_agent=binary)
    store = Store(s.state_dir / "admind.db")
    assert asyncio.run(cli.init(s, store, Audit(s.state_dir / "audit.jsonl"))) == 0
    assert store.get("group_id_hex") == "b2" * 32 and store.get("account_id_hex") == ACCOUNT
    out = capsys.readouterr().out
    assert "Accept the invite" in out and s.operator_npub not in out
    assert "bootstrap" in log.read_text()
    assert asyncio.run(cli.init(s, store, Audit(s.state_dir / "audit.jsonl"))) == 1


def test_rearm_clears_the_latch(tmp_path: Path) -> None:
    s = make_settings(tmp_path)
    store = Store(s.state_dir / "admind.db")
    store.set("latched", "group has 3 members, not 2")
    assert cli.rearm(s, store, Audit(s.state_dir / "audit.jsonl")) == 0
    assert store.get("latched") is None
    assert "rearm" in (s.state_dir / "audit.jsonl").read_text()


def test_unit_rendering_escapes_and_refuses_bad_paths() -> None:
    text = unit.render("/opt/venv/bin/python", {"PATH": "/usr/bin:/opt/50%/bin",
                                                "HETERODYNE_CONFIG_DIR": "/etc/hz", "OTHER": "x"})
    assert "ExecStart=/opt/venv/bin/python -m heterodyne.admind run" in text
    assert "Environment=PATH=/usr/bin:/opt/50%%/bin" in text
    assert "Environment=HETERODYNE_CONFIG_DIR=/etc/hz" in text and "OTHER" not in text
    assert "RestartPreventExitStatus=78" in text and "UMask=0077" in text
    for bad in ("/opt/my venv/python", '/opt/"q"/python'):
        with pytest.raises(ValueError):
            unit.render(bad, {"PATH": "/usr/bin"})


def test_hook_subcommand_exits_zero_even_when_admind_is_down(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"hook_event_name":"Stop"}')))
    assert cli.main(["hook", "--socket", str(tmp_path / "absent.sock")]) == 0


def test_run_without_init_is_a_config_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("HETERODYNE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("HETERODYNE_STATE_DIR", str(tmp_path / "state"))
    assert cli.main(["run"]) == cli.EX_CONFIG      # no [admind] profile configured
    assert os.environ["HETERODYNE_CONFIG_DIR"] == str(cfg)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_admind_cli.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement the `wn-agent` supervisor**

`src/heterodyne/admind/wnagent.py`:

```python
"""admind's private wn-agent child (ADR 0001 §8; plan decision D1).

The child has its own home (0700) and control socket, and a bearer token that admind generates on
first use (0600, never logged or printed). It runs inside admind's unit, so the unit owns the identity.
S4 showed that a wn-agent home can't be opened by a second process, so nothing else may use this home
while admind runs.
"""

import asyncio
import contextlib
import os
import secrets
import stat
from pathlib import Path

from heterodyne.admind.audit import Audit
from heterodyne.admind.store import private_dir
from heterodyne.marmot.control import ControlClient, ControlError


class WnAgentError(RuntimeError):
    pass


class WnAgent:
    def __init__(self, binary: str, home: Path, relays: tuple[str, ...], audit: Audit) -> None:
        self.binary = binary
        self.home = home
        self.relays = relays
        self.audit = audit
        self.socket_path = home / "ctl" / "wn-agent.sock"
        self.token_path = home / "control.token"
        self.proc: asyncio.subprocess.Process | None = None

    def prepare(self) -> None:
        if self.home.is_symlink():
            raise WnAgentError("[admind.marmot] home must not be a symlink")
        private_dir(self.home)
        private_dir(self.socket_path.parent)
        try:
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600)
        except FileExistsError:
            return
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(32) + "\n")

    def token(self) -> str:
        """The bearer token, read only from a regular 0600 file owned by this user (never via a symlink)."""
        try:
            fd = os.open(self.token_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            raise WnAgentError(f"cannot open the control token file ({type(exc).__name__})") from None
        with os.fdopen(fd) as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
                raise WnAgentError("the control token file must be a regular file owned by this user, mode 0600")
            value = fh.read().strip()
        if len(value) < 32:
            raise WnAgentError("the control token file is empty or too short")
        return value

    def _relay_args(self) -> list[str]:
        return [arg for relay in self.relays for arg in ("--relay", relay)]

    def argv(self) -> list[str]:
        return [self.binary, "--home", str(self.home), "--socket", str(self.socket_path),
                "--auth-token-file", str(self.token_path), *self._relay_args()]

    def bootstrap_argv(self, label: str) -> list[str]:
        return [self.binary, "bootstrap", "--home", str(self.home), "--socket", str(self.socket_path),
                "--auth-token-file", str(self.token_path), "--label", label, "--invite-policy", "deny",
                "--no-quic", "--json", "--wait-for-socket", "30", *self._relay_args()]

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, client: ControlClient, wait: float = 30.0) -> None:
        self.prepare()
        self.proc = await asyncio.create_subprocess_exec(*self.argv(), stdin=asyncio.subprocess.DEVNULL)
        self.audit.write("wn-agent", action="start", pid=self.proc.pid)
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            if self.proc.returncode is not None:
                raise WnAgentError(f"wn-agent exited with status {self.proc.returncode} during startup")
            try:
                await client.account_list()
                return
            except ControlError:
                if asyncio.get_running_loop().time() > deadline:
                    await self.stop()
                    raise WnAgentError(f"wn-agent did not answer on its socket within {wait:.0f}s") from None
                await asyncio.sleep(0.25)

    async def supervise(self, client: ControlClient) -> None:
        """Restart the child whenever it exits, with backoff up to 60s. Runs until cancelled."""
        delay = 1.0
        while True:
            assert self.proc is not None  # noqa: S101 (start() ran first)
            status = await self.proc.wait()
            self.audit.write("wn-agent", action="exited", status=status)
            await asyncio.sleep(delay)
            try:
                await self.start(client)
                delay = 1.0
            except WnAgentError as exc:
                self.audit.write("wn-agent", action="restart-failed", error=str(exc))
                delay = min(delay * 2, 60.0)

    async def stop(self) -> None:
        if self.proc is None or self.proc.returncode is not None:
            return
        self.proc.terminate()
        try:
            await asyncio.wait_for(self.proc.wait(), 10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
            await self.proc.wait()

    async def account(self, client: ControlClient) -> str:
        accounts = [a for a in (await client.account_list()).accounts if a.local_signing]
        if len(accounts) != 1:
            raise WnAgentError(f"admind's wn-agent home has {len(accounts)} local-signing accounts, "
                               "expected exactly 1 (run `admind init`)")
        return accounts[0].account_id_hex.lower()
```

If `ruff` rejects the `assert` in `supervise` (S101), replace it with `if self.proc is None: raise WnAgentError("supervise() before start()")`. Do not suppress the rule.

- [ ] **Step 4: Implement unit rendering**

`src/heterodyne/admind/unit.py`:

```python
"""A systemd user unit for admind, rendered on the host (never committed; ADR 0001 §15, §16)."""

from collections.abc import Mapping

TEMPLATE = """\
[Unit]
Description=heterodyne admind (admin override channel)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} -m heterodyne.admind run
Restart=always
RestartSec=5
RestartPreventExitStatus=78
UMask=0077
{environment}
[Install]
WantedBy=default.target
"""
PASSED_THROUGH = ("PATH", "HETERODYNE_CONFIG_DIR", "HETERODYNE_STATE_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME")
_UNSAFE = set(" \t\n\"'\\")


def render(python: str, env: Mapping[str, str]) -> str:
    """The unit text. PATH and location variables are captured from `env` so the service finds the
    same `claude`, `wn-agent` and config as the shell that rendered it; `%` is escaped for systemd."""
    values = {"python": python, **{k: env[k] for k in PASSED_THROUGH if env.get(k)}}
    for name, value in values.items():
        if _UNSAFE & set(value):
            raise ValueError(f"{name} contains whitespace, a quote or a backslash; "
                             "systemd would split or unquote it")
    environment = "".join(f"Environment={k}={env[k].replace('%', '%%')}\n"
                          for k in PASSED_THROUGH if env.get(k))
    return TEMPLATE.format(python=python.replace("%", "%%"), environment=environment)
```

- [ ] **Step 5: Implement the CLI (without `run`; Task 8 adds it)**

`src/heterodyne/admind/__main__.py`:

```python
import sys

from heterodyne.admind.cli import main

sys.exit(main())
```

`src/heterodyne/admind/cli.py`:

```python
"""`admind` command line: init, run, rearm, unit, hook (ADR 0001 §8)."""

import argparse
import asyncio
import os
import subprocess
import sys

from heterodyne import config as hconfig
from heterodyne.admind import unit
from heterodyne.admind.audit import Audit
from heterodyne.admind.settings import AdmindSettings, resolve
from heterodyne.admind.store import Store
from heterodyne.admind.wnagent import WnAgent, WnAgentError
from heterodyne.marmot.control import ControlClient, ControlError

EX_CONFIG = 78  # sysexits: configuration error; the unit does not restart on it
IDENTITY_LABEL = "heterodyne-admind"


def _load() -> tuple[AdmindSettings, Store, Audit]:
    s = resolve(hconfig.load(), os.environ)
    return s, Store(s.state_dir / "admind.db"), Audit(s.state_dir / "audit.jsonl")


async def init(s: AdmindSettings, store: Store, audit: Audit) -> int:
    """Create admind's identity (if its home has none) and the two-member group with the operator."""
    if store.get("group_id_hex"):
        print("admind is already initialised; its group exists. To start over, stop admind and remove "
              f"{s.state_dir} (this abandons the old identity and group).")
        return 1
    wn = WnAgent(s.wn_agent, s.marmot_home, s.relays, audit)
    wn.prepare()
    client = ControlClient(wn.socket_path, wn.token())
    try:
        await wn.start(client)
        accounts = [a for a in (await client.account_list()).accounts if a.local_signing]
        if not accounts:
            # bootstrap is a client of the running child's socket (it takes --socket and
            # --wait-for-socket), so the child must be running first; S4's "runtime root is already in
            # use" applies to direct-mode `wn` commands, not to socket clients.
            # Output holds invite details, so it is captured, never printed.
            proc = await asyncio.to_thread(subprocess.run, wn.bootstrap_argv(IDENTITY_LABEL),
                                           capture_output=True, timeout=120, check=False)
            if proc.returncode != 0:
                print(f"wn-agent bootstrap failed (exit {proc.returncode})", file=sys.stderr)
                return 1
        account = await wn.account(client)
        created = await client.group_create(account, s.group_name, [s.operator_npub])
        store.set("account_id_hex", account)
        store.set("group_id_hex", created.group_id_hex.lower())
        audit.write("init", action="group_created")
    except (WnAgentError, ControlError) as exc:
        audit.write("init", action="failed", error=str(exc), detail=getattr(exc, "detail", ""))
        print(f"admind init failed: {exc}", file=sys.stderr)  # admind's own wording only; see ControlError
        return 1
    finally:
        await wn.stop()
    print("Created admind's identity and its group with the operator. Accept the invite in your Marmot "
          "client, start admind, then send any message in the group: admind answers once it sees you.")
    return 0


def rearm(s: AdmindSettings, store: Store, audit: Audit) -> int:
    reason = store.get("latched")
    store.delete("latched")
    audit.write("guard", action="rearm", previous=reason)
    print(f"Cleared the latch ({reason or 'was not latched'}). Check the group's member list in your "
          "client first: admind re-checks the member count, but can't see a one-for-one swap.")
    return 0


def _with_settings(fn: str) -> int:
    try:
        s, store, audit = _load()
    except hconfig.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EX_CONFIG
    if fn == "init":
        return asyncio.run(init(s, store, audit))
    if fn == "rearm":
        return rearm(s, store, audit)
    return run(s, store, audit)


def run(s: AdmindSettings, store: Store, audit: Audit) -> int:
    raise NotImplementedError  # Task 8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="admind", description="heterodyne admin override channel")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create admind's Marmot identity and its group with the operator")
    sub.add_parser("run", help="run the daemon (normally from its service unit)")
    sub.add_parser("rearm", help="clear the membership latch after checking the group")
    sub.add_parser("unit", help="print a systemd user unit for this install")
    hook = sub.add_parser("hook", help="(internal) forward an agent hook event to admind")
    hook.add_argument("--socket")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"]:
        from heterodyne.admind.hook import hook_main
        return hook_main(argv[1:], sys.stdin.buffer.read())
    args = build_parser().parse_args(argv)
    if args.command == "unit":
        try:
            print(unit.render(sys.executable, os.environ), end="")
        except ValueError as exc:
            print(f"cannot render the unit: {exc}", file=sys.stderr)
            return 1
        return 0
    return _with_settings(args.command)
```

In `test_run_without_init_is_a_config_exit`, `run` is reached only after `_load()`. With no `[admind] profile`, `_load()` raises `ConfigError`, so `EX_CONFIG` is returned before `NotImplementedError` can happen. Task 8 replaces `run`.

- [ ] **Step 6: Run the tests and the full gate**

Run: `uv run pytest tests/test_admind_cli.py -q && uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/heterodyne/admind/wnagent.py src/heterodyne/admind/unit.py src/heterodyne/admind/cli.py src/heterodyne/admind/__main__.py tests/test_admind_cli.py
git commit -m "admind: private wn-agent supervision, init/rearm/unit/hook CLI (plan 2 task 7)"
```

---

### Task 8: The daemon — `admind run`, recovery, outbox, alert relay, integration test

**Files:**
- Create: `src/heterodyne/admind/daemon.py`, `tests/fakes/fake_claude.py`
- Modify: `src/heterodyne/admind/cli.py` (implement `run`)
- Test: `tests/test_admind_daemon.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `Admind(settings, client, store, audit, agent, runner, account, group)` with async `run()`, plus the constants `READY_NOTICE`, `RESTARTED_NOTICE`, `NO_REPLY` and `NOT_READY`. `cli.run` wires the real parts together.

**Behaviour (the tests pin each line):**

1. **Startup**, in this order:
   - recover: each `received` inbound row becomes `dropped`, and the operator gets `RESTARTED_NOTICE` as a reply to it;
   - check the group (a latch if the count is not 2, audited if the check fails);
   - start the hook server;
   - `agent.ensure_running()`: `adopted` means ready; `launched` or `resumed` wait for SessionStart; `AgentStuck` is audited and reported on the operator's next message;
   - then run the loops: inbound, hook, outbox, alerts and group check.
2. **Inbound message:**
   - Guard verdict: `ignore` does nothing; `drop` is audited with sender, reason and text.
   - Replay: `claim_inbound`, and a duplicate is audited and dropped.
   - Count check: `group_info`. A count other than 2 latches and drops the message. A `ControlError` drops it with the reply "could not verify the group".
   - First operator message: set `operator_seen_at` and enqueue `READY_NOTICE` (top-level, key `ready`).
   - `!` command: parse, then run it in a thread and reply to the message.
     - `!new` also clears readiness.
     - A `CommandError` becomes a reply.
     - `AgentStuck` becomes a reply.
   - Control characters: the message is refused with a reply and marked `dropped`.
   - Otherwise the message joins the in-memory queue. **One prompt at a time:** the head of the queue is pasted only when the agent is ready and nothing is in flight. It is then marked `dispatched`, recorded as `in_flight` in the store, and audited.
   - `in_flight` is cleared by the next Stop (which replies to it), by `!interrupt` (Claude Code runs no Stop hook for a user interrupt), by `!new`, and by a SessionStart (a restarted agent has no turn in progress). The last three reply "No reply to this message: …" to the abandoned message.
   - If the agent is stuck (`AgentStuck`), held messages are dropped with a reply suggesting `!tail`, then `!new`.
   - If the agent is not ready, the message is held in memory. It is flushed in order on SessionStart. If it has waited longer than `start_timeout_seconds`, the operator gets `NOT_READY` once per launch.
   - If the paste fails (`TmuxError`), `ensure_running()` is called and the message is held.
3. **Group state change:**
   - A membership kind latches.
   - Any other kind, a reaction or an unknown event is audited only.
   - A `ProtocolError` or `ControlError` from the subscription is audited, and the subscription reconnects with backoff (1s doubling to 30s).
4. **Hook event:**
   - A session ID other than the current one is audited and ignored.
   - SessionStart runs `agent.started(id)`, sets ready and flushes held messages.
   - Stop takes `reply_text(ev)`, or `NO_REPLY` if it is empty. The text is chunked, and each chunk is enqueued with key `reply:<session>:<n>:<i>` as a reply to `in_flight` (top-level if none). `n` comes from `reply_seq` in the store. Then the next queued message is dispatched.
5. **Outbox:**
   - Pending rows are sent in order.
   - A retryable error is retried after `min(60, 2**attempts)` seconds, from the head of the queue. After 10 attempts, or on a non-retryable error, the row is marked failed and audited.
   - The outbox wakes on enqueue, or every 5 seconds.
   - **Outbound gate** (`may_post`), checked before every row, not once per batch: not latched, the operator has been seen (D5), and the last group check succeeded (`group_ok`). Recovery notices, replies and alerts all wait behind it.
6. **Alerts:** every `alert_poll_seconds`, if the operator has been seen and admind isn't latched, each unrelayed file is relayed with `store.relay_alert(name, "alert:<name>", render(...))`, then the outbox is woken.
7. **Group check:** every `group_check_seconds`, `group_info` runs; a count other than 2 latches.
8. **Latching** writes `latched=<reason>` to the store and audits it once. It is never cleared by admind, only by `admind rearm`.

- [ ] **Step 1: Write the fake `claude`**

`tests/fakes/fake_claude.py`:

```python
"""A fake interactive `claude` for integration tests: it fires the hooks from --settings.

On start it runs the SessionStart hooks. For each line typed into its terminal it runs the Stop hooks
with `last_assistant_message = "echo: <line>"`. It appends its argv to $FAKE_CLAUDE_LOG.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["FAKE_CLAUDE_LOG"]).open("a") as log:
    log.write(json.dumps(args) + "\n")
settings = json.loads(Path(args[args.index("--settings") + 1]).read_text())
flag = "--session-id" if "--session-id" in args else "--resume"
session = args[args.index(flag) + 1]


def fire(event: str, **extra: object) -> None:
    payload = json.dumps({"hook_event_name": event, "session_id": session, **extra}).encode()
    for group in settings["hooks"].get(event, []):
        for hook in group["hooks"]:
            # Claude Code runs hook commands through a shell; so does the fake, so quoting bugs in
            # hook_command() fail here. The command comes from admind's own generated settings file.
            subprocess.run(hook["command"], shell=True, input=payload, check=False)  # noqa: S602


fire("SessionStart", source="startup")
for line in sys.stdin:
    fire("Stop", last_assistant_message=f"echo: {line.rstrip(chr(10))}", stop_hook_active=False)
```

- [ ] **Step 2: Write the failing daemon tests**

`tests/test_admind_daemon.py`:

```python
import asyncio
import json
import os
import shutil
import stat
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent
from fakes.settings import OPERATOR_HEX, make_settings

from heterodyne.admind.agent import TMUX_SOCKET, AdminAgent
from heterodyne.admind.audit import Audit
from heterodyne.admind.commands import CommandRunner
from heterodyne.admind.daemon import NO_REPLY, READY_NOTICE, RESTARTED_NOTICE, Admind
from heterodyne.admind.store import Store
from heterodyne.marmot.control import ControlClient
from heterodyne.services import UnitStatus
from heterodyne.tmux import Tmux

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
FAKE_CLAUDE = Path(__file__).parent / "fakes" / "fake_claude.py"
STRANGER = "e5" * 32


class Services:
    def restart(self, unit: str) -> tuple[bool, str]:
        return True, ""

    def status(self, unit: str) -> UnitStatus:
        return UnitStatus(unit, "active", "running", "")


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        wrapper = tmp_path / "claude"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_CLAUDE} \"$@\"\n")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        self.log = tmp_path / "claude.log"
        os.environ["FAKE_CLAUDE_LOG"] = str(self.log)
        self.settings = make_settings(tmp_path, adapter_binary=str(wrapper))
        self.store = Store(self.settings.state_dir / "admind.db")
        self.store.set("group_id_hex", "b2" * 32)
        self.audit = Audit(self.settings.state_dir / "audit.jsonl")
        self.fake = FakeWnAgent(tmp_path / "wn.sock")
        self.tmux = Tmux(f"hz-test-{uuid.uuid4().hex[:8]}")
        self.agent = AdminAgent(self.tmux, self.store, self.settings, self.settings.state_dir / "hook.sock")
        runner = CommandRunner(self.agent, Services(), ("fake.service",), lambda: True)
        self.daemon = Admind(self.settings, ControlClient(tmp_path / "wn.sock", "test-token", timeout=5),
                             self.store, self.audit, self.agent, runner, ACCOUNT, "b2" * 32)
        self.seq = 0

    async def say(self, text: str, sender: str = OPERATOR_HEX) -> str:
        self.seq += 1
        mid = f"{self.seq:064x}"
        await self.fake.push_event(self.fake.message_event(text, sender, mid))
        return mid

    def texts(self) -> list[str]:
        return [r["text"] for r in self.fake.sent]

    async def until(self, pred: Callable[[], bool], timeout: float = 15) -> None:
        async def poll() -> None:
            while not pred():
                await asyncio.sleep(0.05)
        await asyncio.wait_for(poll(), timeout)


def run_with(tmp_path: Path, scenario: Callable[[Harness], Awaitable[None]],
             before: Callable[[Harness], Any] | None = None) -> Harness:
    h = Harness(tmp_path)
    if before:
        before(h)

    async def body() -> None:
        await h.fake.start()
        task = asyncio.create_task(h.daemon.run())
        try:
            await h.fake.wait_subscribed(10)
            await scenario(h)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await h.fake.stop()
    try:
        asyncio.run(body())
    finally:
        h.tmux.kill_server()
    return h


def test_operator_round_trip_ready_notice_and_threaded_reply(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        mid = await h.say("hello there")
        await h.until(lambda: "echo: hello there" in h.texts())
        assert h.texts()[0] == READY_NOTICE
        reply = next(r for r in h.fake.sent if r["text"] == "echo: hello there")
        assert reply["reply_to_message_id_hex"] == mid
        assert h.store.inbound_with_status("dispatched") == [mid]
    h = run_with(tmp_path, scenario)
    audit = (h.settings.state_dir / "audit.jsonl").read_text()
    assert '"kind": "inbound"' in audit and '"kind": "dispatch"' in audit
    assert TMUX_SOCKET == "heterodyne-admind"


def test_two_quick_messages_each_get_their_own_threaded_reply(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("warm up")
        await h.until(lambda: "echo: warm up" in h.texts())
        first = await h.say("one")
        second = await h.say("two")
        await h.until(lambda: "echo: two" in h.texts())
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["echo: one"] == first and by_text["echo: two"] == second
    run_with(tmp_path, scenario)


def test_recovery_notice_waits_for_a_good_group_check(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.store.set("operator_seen_at", "2026-09-30T00:00:00+00:00")
        h.store.claim_inbound("99" * 32)
        h.fake.fail_group_info = True

    async def scenario(h: Harness) -> None:
        await asyncio.sleep(1.5)
        assert h.fake.sent == []                    # group unverified: nothing leaves
        h.fake.fail_group_info = False
        await h.until(lambda: RESTARTED_NOTICE in h.texts())
    run_with(tmp_path, scenario, before)


def test_recovery_notice_waits_for_the_operator(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.store.claim_inbound("99" * 32)            # claimed, but the operator was never seen

    async def scenario(h: Harness) -> None:
        await asyncio.sleep(1.5)
        assert h.fake.sent == []
        await h.say("hi")
        await h.until(lambda: RESTARTED_NOTICE in h.texts())
    run_with(tmp_path, scenario, before)


def test_latch_mid_batch_stops_the_rest(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("hi")
        await h.until(lambda: "echo: hi" in h.texts())
        sent = len(h.fake.sent)
        h.fake.on_send = lambda _req: h.store.set("latched", "test")
        for i in range(3):
            h.store.enqueue(f"batch:{i}", f"b{i}", None)
        h.daemon.wake.set()
        await h.until(lambda: len(h.fake.sent) > sent)
        await asyncio.sleep(0.5)
        assert len(h.fake.sent) == sent + 1
    run_with(tmp_path, scenario)


def test_non_operator_dropped_silently_and_commands_answered(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("let me in", sender=STRANGER)
        await h.say("!ps")
        await h.until(lambda: any("fake.service: active (running)" in t for t in h.texts()))
        await h.say("!restrat wsd")
        await h.until(lambda: any(t.startswith("Unknown command !restrat") for t in h.texts()))
        await h.say("bad \x1b[201~ paste")
        await h.until(lambda: any("control characters" in t for t in h.texts()))
        assert not any("let me in" in t for t in h.texts())
    h = run_with(tmp_path, scenario)
    assert "sender is not the operator" in (h.settings.state_dir / "audit.jsonl").read_text()


def test_member_count_latches_and_stops_all_posting(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("first")
        await h.until(lambda: "echo: first" in h.texts())
        sent = len(h.fake.sent)
        h.fake.member_count = 3
        await h.say("second")
        await h.until(lambda: h.store.get("latched") is not None)
        (h.settings.alerts_dir).mkdir(parents=True, exist_ok=True)
        (h.settings.alerts_dir / "a1.json").write_text(json.dumps(
            {"id": "a1", "created_at": "t", "text": "x"}))
        await asyncio.sleep(1.0)
        assert len(h.fake.sent) == sent
        h.fake.member_count = 2
        await h.say("third")
        await asyncio.sleep(1.0)
        assert len(h.fake.sent) == sent            # still latched until `admind rearm`
    h = run_with(tmp_path, scenario)
    assert "group has 3 members" in (h.store.get("latched") or "")


def test_membership_event_latches(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.fake.push_event({"type": "group_state_changed", "account_id_hex": ACCOUNT,
                                 "group_id_hex": "b2" * 32, "event_id_hex": "f6" * 32,
                                 "change": "member_added"})
        await h.until(lambda: h.store.get("latched") is not None)
    run_with(tmp_path, scenario)


def test_alerts_wait_for_the_operator_then_relay_once(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.settings.alerts_dir.mkdir(parents=True)
        (h.settings.alerts_dir / "a1.json").write_text(json.dumps(
            {"id": "a1", "created_at": "2026-09-30T00:00:00Z", "text": "card undelivered"}))

    async def scenario(h: Harness) -> None:
        await asyncio.sleep(0.8)
        assert h.fake.sent == []                    # operator not seen yet (plan decision D5)
        await h.say("hi")
        await h.until(lambda: any("card undelivered" in t for t in h.texts()))
        await asyncio.sleep(0.5)
        assert sum("card undelivered" in t for t in h.texts()) == 1
    run_with(tmp_path, scenario, before)


def test_restart_recovery_answers_undelivered_messages(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.store.set("operator_seen_at", "2026-09-30T00:00:00+00:00")
        h.store.claim_inbound("99" * 32)

    async def scenario(h: Harness) -> None:
        await h.until(lambda: RESTARTED_NOTICE in h.texts())
        row = next(r for r in h.fake.sent if r["text"] == RESTARTED_NOTICE)
        assert row["reply_to_message_id_hex"] == "99" * 32
    h = run_with(tmp_path, scenario, before)
    assert h.store.inbound_with_status("dropped") == ["99" * 32]


def test_outbox_retries_transient_failures_in_order(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        h.fake.fail_sends = 2
        await h.say("one")
        await h.until(lambda: "echo: one" in h.texts(), timeout=30)
        assert h.texts().index(READY_NOTICE) < h.texts().index("echo: one")
    run_with(tmp_path, scenario)


def test_empty_reply_gets_a_placeholder(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        mid = await h.say("hi")
        await h.until(lambda: "echo: hi" in h.texts())
        from heterodyne.admind.hook import HookEvent
        await h.daemon.hooks.put(HookEvent("Stop", h.agent.session_id or "", None, None))
        await h.until(lambda: NO_REPLY in h.texts())
        row = next(r for r in h.fake.sent if r["text"] == NO_REPLY)
        assert row["reply_to_message_id_hex"] == mid
    run_with(tmp_path, scenario)


def test_stop_for_another_session_is_ignored(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("hi")
        await h.until(lambda: "echo: hi" in h.texts())
        sent = len(h.fake.sent)
        from heterodyne.admind.hook import HookEvent
        await h.daemon.hooks.put(HookEvent("Stop", "not-the-session", None, "forged"))
        await asyncio.sleep(0.5)
        assert len(h.fake.sent) == sent
    run_with(tmp_path, scenario)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_admind_daemon.py -q`
Expected: FAIL (`ModuleNotFoundError: heterodyne.admind.daemon`).

- [ ] **Step 4: Implement the daemon**

`src/heterodyne/admind/daemon.py`:

```python
"""The admind daemon (ADR 0001 §8). See plan 2, Task 8, "Behaviour", for the contract the tests pin."""

import asyncio
import contextlib
import time

from heterodyne.admind import alerts, chunk, commands, guard
from heterodyne.admind.agent import AdminAgent, AgentStuck
from heterodyne.admind.audit import Audit
from heterodyne.admind.hook import HookEvent, HookServer, reply_text
from heterodyne.admind.settings import AdmindSettings
from heterodyne.admind.store import Store, now
from heterodyne.marmot.control import (
    ControlClient,
    ControlError,
    GroupStateChanged,
    InboundMessage,
    ReactionAdded,
)
from heterodyne.tmux import TmuxError

READY_NOTICE = ("admind is listening. Your messages go to the admin agent verbatim; replies come back "
                "in thread. " + commands.HELP)
RESTARTED_NOTICE = "⚠️ admind restarted before this message reached the admin agent. Resend it if it is still needed."
NO_REPLY = "(the admin agent's turn ended without a text reply)"
NOT_READY = "The admin agent has not started yet; your message is queued. Use !tail to see its screen."
CONTROL_REFUSED = "Not delivered: the message contains terminal control characters."
UNVERIFIED = "Not delivered: admind could not verify the group membership. Try again shortly."
MAX_SEND_ATTEMPTS = 10


class Admind:
    def __init__(self, settings: AdmindSettings, client: ControlClient, store: Store, audit: Audit,
                 agent: AdminAgent, runner: commands.CommandRunner, account: str, group: str) -> None:
        self.s = settings
        self.client = client
        self.store = store
        self.audit = audit
        self.agent = agent
        self.runner = runner
        self.account = account
        self.group = group
        self.hooks: asyncio.Queue[HookEvent] = asyncio.Queue()
        self.ready = asyncio.Event()
        self.held: list[tuple[str, str]] = []
        self.launched_at = time.monotonic()
        self.stuck: str | None = None
        self.not_ready_sent = False
        self.group_ok = False
        self.wake = asyncio.Event()

    # --- state helpers -------------------------------------------------------------------------
    def latched(self) -> bool:
        return self.store.get("latched") is not None

    def latch(self, reason: str) -> None:
        if not self.latched():
            self.store.set("latched", reason)
            self.audit.write("guard", action="latch", reason=reason)

    def may_post(self) -> bool:
        """Outbound gate, checked immediately before every send: not latched, the operator has been
        seen in the group (D5), and the last membership check succeeded."""
        return (not self.latched() and self.group_ok
                and self.store.get("operator_seen_at") is not None)

    def post(self, key: str, text: str, reply_to: str | None) -> None:
        if self.store.enqueue(key, text, reply_to):
            self.wake.set()

    def reply(self, mid: str, text: str, tag: str) -> None:
        for i, part in enumerate(chunk.split(text, self.s.chunk_chars)):
            self.post(f"{tag}:{mid}:{i}", part, mid)

    # --- lifecycle -----------------------------------------------------------------------------
    async def run(self) -> None:
        for mid in self.store.inbound_with_status("received"):
            self.store.set_inbound(mid, "dropped")
            self.post(f"restarted:{mid}", RESTARTED_NOTICE, mid)
            self.audit.write("recover", message_id=mid, action="answered-restarted")
        await self.check_group()
        server = HookServer(self.s.state_dir / "hook.sock", self.hooks, self.audit)
        await server.start()
        try:
            await self.start_agent()
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.inbound_loop())
                tg.create_task(self.hook_loop())
                tg.create_task(self.outbox_loop())
                tg.create_task(self.alerts_loop())
                tg.create_task(self.group_loop())
        finally:
            await server.close()

    async def start_agent(self) -> None:
        self.ready.clear()
        self.launched_at = time.monotonic()
        self.not_ready_sent = False
        try:
            mode = await asyncio.to_thread(self.agent.ensure_running)
        except (AgentStuck, TmuxError) as exc:
            self.stuck = str(exc)
            self.audit.write("agent", action="start-failed", error=str(exc))
            return
        self.stuck = None
        self.audit.write("agent", action=mode, session=self.agent.session_id)
        if mode == "adopted":
            self.ready.set()

    async def check_group(self) -> bool:
        try:
            info = await self.client.group_info(self.account, self.group)
        except ControlError as exc:
            self.group_ok = False
            self.audit.write("guard", action="group-check-failed", error=str(exc), detail=exc.detail)
            return False
        verdict = guard.judge_member_count(info.member_count)
        self.group_ok = verdict.action != "latch"
        if not self.group_ok:
            self.latch(verdict.reason)
            return False
        self.wake.set()
        return True

    # --- inbound -------------------------------------------------------------------------------
    async def inbound_loop(self) -> None:
        delay = 1.0
        while True:
            try:
                async for event in self.client.subscribe(self.account, self.group):
                    delay = 1.0
                    if isinstance(event, InboundMessage):
                        await self.on_message(event)
                    elif isinstance(event, GroupStateChanged):
                        verdict = guard.judge_group_change(event, group_id=self.group)
                        if verdict.action == "latch":
                            self.latch(verdict.reason)
                        else:
                            self.audit.write("event", change=event.change, action="ignored")
                    elif isinstance(event, ReactionAdded):
                        self.audit.write("event", kind_detail="reaction", emoji=event.emoji, action="ignored")
                    else:
                        self.audit.write("event", kind_detail=event.type, action="ignored")
            except ControlError as exc:
                self.audit.write("subscribe", action="reconnect", error=str(exc), code=exc.code)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def on_message(self, ev: InboundMessage) -> None:
        verdict = guard.judge_message(ev, group_id=self.group, operator_hex=self.s.operator_hex,
                                      latched=self.latched())
        mid = ev.message.message_id_hex.lower()
        if verdict.action == "ignore":
            return
        if verdict.action == "drop":
            self.audit.write("drop", message_id=mid, sender=ev.message.sender.account_id_hex,
                             reason=verdict.reason, text=ev.message.text)
            return
        if not self.store.claim_inbound(mid):
            self.audit.write("drop", message_id=mid, reason="replayed message id")
            return
        text = ev.message.text
        self.audit.write("inbound", message_id=mid, sender=ev.message.sender.account_id_hex, text=text)
        if not await self.check_group():
            self.store.set_inbound(mid, "dropped")
            if not self.latched():
                self.reply(mid, UNVERIFIED, "unverified")   # sent once a later check succeeds
            return
        if self.store.get("operator_seen_at") is None:
            self.store.set("operator_seen_at", now())
            self.post("ready", READY_NOTICE, None)
        await self.handle(mid, text)

    async def handle(self, mid: str, text: str) -> None:
        try:
            cmd = commands.parse(text)
        except commands.CommandError as exc:
            self.store.set_inbound(mid, "dispatched")
            self.reply(mid, str(exc), "cmd")
            return
        if cmd is not None:
            self.store.set_inbound(mid, "dispatched")
            if cmd.name == "new":
                # Before the command runs: the new session's SessionStart may arrive while it runs.
                self.ready.clear()
                self.launched_at = time.monotonic()
                self.not_ready_sent = False
                self.stuck = None
                self.abandon_in_flight("the admin agent session was replaced by !new")
            try:
                result = await asyncio.to_thread(self.runner.run, cmd)
            except (AgentStuck, TmuxError) as exc:
                result = f"!{cmd.name} failed: {exc}"
                if cmd.name == "new":
                    self.stuck = str(exc)
            if cmd.name == "interrupt":
                # Claude Code does not run the Stop hook for a user interrupt, so the turn ends here.
                self.abandon_in_flight("interrupted by !interrupt")
            self.audit.write("command", message_id=mid, command=cmd.name, arg=cmd.arg, result=result)
            self.reply(mid, result, "cmd")
            await self.flush()
            return
        if commands.has_control_chars(text):
            self.store.set_inbound(mid, "dropped")
            self.reply(mid, CONTROL_REFUSED, "refused")
            return
        self.held.append((mid, text))
        await self.flush()

    async def flush(self) -> None:
        if self.stuck is not None:
            for mid, _ in self.held:
                self.store.set_inbound(mid, "dropped")
                self.reply(mid, f"Not delivered: the admin agent is not running ({self.stuck}). "
                                "Use !tail, then !new.", "stuck")
            self.held.clear()
            return
        if not self.ready.is_set():
            waited = time.monotonic() - self.launched_at
            if self.held and waited > self.s.start_timeout_seconds and not self.not_ready_sent:
                self.not_ready_sent = True
                self.reply(self.held[-1][0], NOT_READY, "notready")
            return
        # One prompt at a time, so each Stop answers exactly the message in flight.
        if not self.held or self.store.get("in_flight") is not None:
            return
        mid, text = self.held[0]
        try:
            await asyncio.to_thread(self.agent.send, text)
        except TmuxError as exc:
            self.audit.write("agent", action="send-failed", error=str(exc), message_id=mid)
            await self.start_agent()
            return
        self.held.pop(0)
        self.store.set_inbound(mid, "dispatched")
        self.store.set("in_flight", mid)
        self.audit.write("dispatch", message_id=mid, session=self.agent.session_id)

    def abandon_in_flight(self, why: str) -> None:
        mid = self.store.get("in_flight")
        if mid is not None:
            self.store.delete("in_flight")
            self.reply(mid, f"No reply to this message: {why}.", "abandoned")
            self.audit.write("agent", action="abandon", message_id=mid, reason=why)

    # --- hooks ---------------------------------------------------------------------------------
    async def hook_loop(self) -> None:
        while True:
            ev = await self.hooks.get()
            if ev.session_id != self.agent.session_id:
                self.audit.write("hook", event=ev.hook_event_name, action="ignored-other-session")
                continue
            if ev.hook_event_name == "SessionStart":
                # A (re)started session has no turn in progress: whatever was in flight is lost.
                self.abandon_in_flight("the admin agent restarted before answering; resend if needed")
                self.agent.started(ev.session_id)
                self.audit.write("agent", action="session-start", session=ev.session_id,
                                 transcript=ev.transcript_path)
                self.ready.set()
                await self.flush()
            elif ev.hook_event_name == "Stop":
                seq = int(self.store.get("reply_seq") or "0") + 1
                self.store.set("reply_seq", str(seq))
                text = reply_text(ev) or NO_REPLY
                reply_to = self.store.get("in_flight")   # None: a turn the operator didn't start
                self.store.delete("in_flight")
                for i, part in enumerate(chunk.split(text, self.s.chunk_chars)):
                    self.post(f"reply:{ev.session_id}:{seq}:{i}", part, reply_to)
                self.audit.write("reply", session=ev.session_id, reply_to=reply_to, text=text)
                await self.flush()

    # --- outbound ------------------------------------------------------------------------------
    async def outbox_loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), 5)
            self.wake.clear()
            if self.held and not self.ready.is_set():
                await self.flush()          # emits NOT_READY once the start timeout passes
            for row in self.store.pending():
                if not self.may_post():     # rechecked per row: a latch mid-batch stops the rest
                    break
                try:
                    sent = await self.client.send_final(self.account, self.group, row.text, row.reply_to,
                                                        row.key)
                except ControlError as exc:
                    attempts = self.store.mark_attempt(row.seq)
                    if exc.retryable and attempts < MAX_SEND_ATTEMPTS:
                        self.audit.write("send", key=row.key, action="retry", attempts=attempts, code=exc.code)
                        await asyncio.sleep(min(60, 2 ** attempts))
                        self.wake.set()
                        break
                    self.store.mark_failed(row.seq)
                    self.audit.write("send", key=row.key, action="failed", code=exc.code, detail=exc.detail)
                    continue
                self.store.mark_sent(row.seq, sent.message_ids_hex[0] if sent.message_ids_hex else None)
                self.audit.write("send", key=row.key, action="sent")

    async def alerts_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.alert_poll_seconds)
            if not self.may_post():
                continue
            for name, alert in await asyncio.to_thread(alerts.scan, self.s.alerts_dir):
                if self.store.relayed(name):
                    continue
                if self.store.relay_alert(name, f"alert:{name}", alerts.render(name, alert, self.s.chunk_chars)):
                    self.audit.write("alert", name=name, malformed=alert is None)
                    self.wake.set()

    async def group_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.group_check_seconds)
            await self.check_group()
```

Residual race (documented in `docs/admind.md`): if a Stop hook is already on its way when `!interrupt` clears `in_flight`, that reply is threaded to the next dispatched message instead. Replies are still verbatim agent output, only the thread anchor can be off.

`hook_loop` calls `self.agent.started()` from the event loop. That call is a store write only, so it doesn't need a thread.

- [ ] **Step 5: Implement `admind run` in `cli.py`**

Replace the `run` stub in `src/heterodyne/admind/cli.py` with:

```python
def run(s: AdmindSettings, store: Store, audit: Audit) -> int:
    group = store.get("group_id_hex")
    if group is None:
        print("admind is not initialised: run `admind init` first", file=sys.stderr)
        return EX_CONFIG
    try:
        services = for_backend(s.service_manager)
    except hconfig.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EX_CONFIG
    return asyncio.run(_serve(s, store, audit, group, services))


async def _serve(s: AdmindSettings, store: Store, audit: Audit, group: str, services: ServiceManager) -> int:
    wn = WnAgent(s.wn_agent, s.marmot_home, s.relays, audit)
    wn.prepare()
    client = ControlClient(wn.socket_path, wn.token())
    try:
        await wn.start(client)
        account = await wn.account(client)
    except (WnAgentError, ControlError) as exc:
        print(f"admind: {exc}", file=sys.stderr)
        await wn.stop()
        return 1
    agent = AdminAgent(Tmux(TMUX_SOCKET), store, s, s.state_dir / "hook.sock")
    runner = CommandRunner(agent, services, s.restart_units, wn.alive)
    daemon = Admind(s, client, store, audit, agent, runner, account, group)
    audit.write("admind", action="start", group=group)
    main_task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    if main_task is not None:
        loop.add_signal_handler(signal.SIGTERM, main_task.cancel)
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(wn.supervise(client))
            tg.create_task(daemon.run())
    except asyncio.CancelledError:
        audit.write("admind", action="stop")
        return 0
    finally:
        await wn.stop()
    return 0
```

and add these imports:

```python
import signal

from heterodyne.admind.agent import TMUX_SOCKET, AdminAgent
from heterodyne.admind.commands import CommandRunner
from heterodyne.admind.daemon import Admind
from heterodyne.services import ServiceManager, for_backend
from heterodyne.tmux import Tmux
```

- [ ] **Step 6: Run the tests and the full gate**

Run: `uv run pytest tests/test_admind_daemon.py -q && uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`
Expected: PASS. The daemon tests take roughly 20–40 seconds because of real tmux and fake-claude startup. If one is flaky, raise its `until` timeout rather than adding sleeps before the assertion.

- [ ] **Step 7: Commit**

```bash
git add src/heterodyne/admind/daemon.py src/heterodyne/admind/cli.py tests/fakes/fake_claude.py tests/test_admind_daemon.py
git commit -m "admind daemon: guarded passthrough, hooks, outbox, alert relay, recovery (plan 2 task 8)"
```

---

### Task 9: Docs — runbook, alert contract, security model, README

**Files:**
- Create: `docs/admind.md`
- Modify: `docs/security-model.md`, `docs/install.md`, `README.md`

**Interfaces:** none (docs only). The checker must stay clean: use `<placeholders>`, `~/` paths and no real hosts, npubs or unit names of the reference install.

- [ ] **Step 1: Write `docs/admind.md`**

Sections, in this order:
1. **What it is.** It is §8 in two paragraphs. Include the independence list and the deliberate-privilege decision, linked to the r1 response.
2. **Install:**
   - host config: `[admind]` in `config.toml`, and `operators` plus `identities.<name>.marmot_npub` in `policy.toml`;
   - `admind init`: it creates the identity and group, and the operator accepts the invite in their Marmot client;
   - `admind unit > ~/.config/systemd/user/heterodyne-admind.service`;
   - `systemctl --user daemon-reload && systemctl --user enable --now heterodyne-admind`;
   - send any message to the group, and admind replies with the ready notice.
   - Note that `admind unit` captures the current shell's `PATH`, so render it from a shell where `claude` and `wn-agent` resolve.
3. **Before first use of the admin agent:** Claude Code may show its bypass-permissions acceptance and workspace trust dialogs on the first launch in `workdir`. Accept them once with `tmux -L heterodyne-admind attach -t admin` (detach with `Ctrl-b d`). `!tail` shows the screen.
4. **Using it:** passthrough, thread replies, chunking; each `!` command with an example; why control characters are refused; why `!` text never reaches the agent.
5. **The latch:**
   - what triggers it (count ≠ 2, any membership or admin event);
   - that admind goes silent while latched;
   - how to check the group in the client, then run `admind rearm`;
   - the residual risk: a one-for-one swap made on the control socket by a same-user process is invisible, as in ADR §3.4. Likewise a same-user process can write to the hook socket and forge a reply for the current session.
   - the `!interrupt` thread-anchor race (Task 8, Step 4 note).
6. **Alert relay contract:** copy the `alerts.py` module docstring (directory, name pattern, atomic rename, JSON shape, relayed once, never deleted, held until the operator has been seen).
7. **Files:** a table of what lives under `<state>/admind/` (`admind.db`, `audit.jsonl`, `hook.sock`, `claude-settings.json`, `marmot/` with its `control.token` and `ctl/`) and `<state>/alerts/`, with modes.
8. **Troubleshooting:**
   - wn-agent does not start: another process holds the home (S4 "runtime root is already in use");
   - the agent keeps exiting (`AgentStuck`, then `!new`);
   - no replies: check the Stop hook with `!tail`, and look for `hook` records in the audit log;
   - a message answered with "admind restarted…" (D6).
9. **Known limits:** Codex is not supported as the admin adapter yet (D7); systemd only (v1); the join signal is the operator's first message (D5).

- [ ] **Step 2: Update the other docs**

- `docs/security-model.md`:
  - The implementation-status paragraph now says that `admind` exists (plan 2), in addition to the configuration side.
  - In the admind section, add the latch (D4), control-character refusal (D3), the private `wn-agent` child with its own token (D1), and at-most-once delivery (D6).
- `docs/install.md`: add an "Admin channel (`admind`)" step that links to `docs/admind.md`.
- `README.md`: in the status or feature list, say that `admind` is implemented, and link to `docs/admind.md`.

- [ ] **Step 3: Check and commit**

Run: `uv run python scripts/check_install_agnostic.py && uv run pytest -q`
Expected: clean and PASS.

```bash
git add docs/admind.md docs/security-model.md docs/install.md README.md
git commit -m "admind runbook, alert contract and security-model update (plan 2 task 9)"
```

---

### Task 10: Live acceptance with the operator (`kind:research`)

**Files:**
- Create: `$HZ/docs/spikes/admind-acceptance.md` (results table, with no install values)

This task runs on the reference host with the operator. **Ask the operator before step 1.** `admind init` publishes a new Marmot identity to public relays and invites the operator, which is an outward-facing action.

- [ ] **Step 1: Configure the host layer** (not committed):
  - `[admind] profile`, `restart_units = ["heterodyne-admind-selftest.service"]`, and `[admind.marmot] relays`;
  - `policy.toml`: `operators = [...]`.
  - Create the scratch unit `~/.config/systemd/user/heterodyne-admind-selftest.service` (`Type=oneshot`, `ExecStart=/bin/true`, `RemainAfterExit=yes`).
  - **Do not** put any live `hermes-*` or `wn-agent-*` unit in `restart_units` during acceptance.
- [ ] **Step 2: `admind init`.** The operator accepts the invite on their phone.
- [ ] **Step 3: Install and start the unit.** Record that the audit log shows `start` and the agent `launched`. Accept Claude's first-run dialogs over `tmux attach` if they appear.
- [ ] **Step 4: Held alert.** Before the operator writes anything, drop `~/.local/state/heterodyne/alerts/accept-1.json` using the contract shape. Verify that nothing is posted.
- [ ] **Step 5: Join signal (D5, GATE.md acceptance item).** The operator sends "hi". Verify all of these:
  - the ready notice arrives and is visible on the phone;
  - the held alert arrives;
  - the agent's reply arrives as a threaded reply to "hi".
  Record whether the operator saw each one. This establishes, or refutes, the first operator message as the join signal.
- [ ] **Step 6: Passthrough.** Send a multi-line message with emoji and quotes, and ask the agent to repeat it exactly. Compare the audit log's `inbound.text` with what the agent echoed.
- [ ] **Step 7: Commands.** Check each of these:
  - `!tail 20`;
  - `!ps`, which lists the self-test unit;
  - `!restart heterodyne-admind-selftest.service`, which succeeds;
  - `!restart hermes-gateway.service`, which is refused and leaves the gateway untouched;
  - `!interrupt` during a long turn: confirm no Stop-hook reply arrives for the interrupted turn (the audit log shows `abandon`, and no `reply` for it), and that a queued second message is then answered in its own thread;
  - `!new`, after which a fresh session replies;
  - `!restrat`, which gets the unknown-command reply.
- [ ] **Step 8: Restart resilience.** Run `systemctl --user restart heterodyne-admind`. Verify that the agent is adopted or resumed, not relaunched fresh (check the audit log), that no reply is duplicated, and that the next message works.
- [ ] **Step 9: Record** the results in `docs/spikes/admind-acceptance.md`, as a PASS/FAIL table per step plus notes. Record the S4 carry-forwards as follows:
  - reaction removal: not exercised by admind, so plan 6;
  - `group_leave` self-event: admind never leaves, so plan 6.
  The latch is covered by the Task 8 integration tests. A live latch test would need a third identity that admind would have to add, and admind has no add command, so it is not run live.
- [ ] **Step 10: Commit** the results document. Leave admind running if the operator wants it as their recovery channel from now on. Otherwise stop and disable the unit. The identity and group stay either way.

---

### Task 11: Open the PR

- [ ] **Step 1: Final whole-range review.** Run a cross-model review of `018daf6..HEAD`, covering the whole plan-2 range, against ADR §3.4, §6.2, §8, §10, §15 and §16 and this plan's D1–D8. Fix or rebut every blocking finding.
- [ ] **Step 2: Push and open the PR**

```bash
cd "$HZ" && git push -u origin plan-2-admind
gh pr create --title "Plan 2: admind, the admin override channel" --body-file /tmp/pr-body.md
```

The PR body lists: what ships (Tasks 1–9); D1–D8; the acceptance results (Task 10); the carry-forwards to plans 4 and 6; the evidence lines of each task's review; and that merging is the operator's action. Wait for CI (ubuntu, macos and secrets) to pass. On macOS the tmux-dependent tests skip if tmux is absent.

---

## Self-review notes (completed while writing)

- **ADR coverage (§8):**

  | Requirement | Where |
  |---|---|
  | Own unit | Task 7 unit and Task 8 `run` |
  | Own identity and connection | D1, Task 7 `init` |
  | Two-member group, refused otherwise | D4, Tasks 4 and 8 |
  | Operator's exact npub only, dropped and logged otherwise | Task 4 guard, Task 8 |
  | Byte-for-byte passthrough to a persistent session of the `admin` profile | D2, D3, D8, Tasks 5 and 8 |
  | Verbatim chunked thread replies | Tasks 4 and 8 |
  | Service user, bypassed prompts, no sandbox | Task 5 argv |
  | Keys readable only by the unit | D1: 0700 home, 0600 token, `UMask=0077` |
  | `!new !interrupt !tail !restart !ps` | Tasks 4 and 6 |
  | Append-only JSONL audit | Task 3 |
  | Alert-directory relay (§6.2) | Tasks 4 and 8, contract in Task 9 |
  | §10 "everything wedged → admind", no dependency on `wsd`, Hermes, beads or the gatekeeper | nothing in `heterodyne.admind` imports them |
  | GATE.md plan-2 items | join signal: D5 and Task 10; reaction removal and self-leave: carried to plan 6 with reasons |

- **Placeholders:** none. Two places tell the implementer to choose between two equivalent forms so that pyright and ruff pass: the Task 4 `Literal` narrowing, and the Task 7 `assert`. Both forms are given in full.
- **Type consistency:**
  - `AdmindSettings` fields are used identically in Tasks 2, 4 (`make_settings`), 5, 7 and 8.
  - The store keys listed in Task 3 are the ones Tasks 5, 7 and 8 use.
  - `ControlClient` methods match between Tasks 1, 7 and 8.
  - `CommandRunner(agent, services, restart_units, wn_alive)` matches between Tasks 6 and 8.
