# S8 host-free end-to-end acceptance, as far as it is in reach (run under sandbox.sh with the repo's
# .venv python). A real CLI (ADAPTER=claude or codex) runs in a private tmux server against its scripted
# stub. The Marmot side is admind's FakeWnAgent (tests/fakes), reached through admind's own
# ControlClient; operators are scripted inbound frames. The picker cycle itself (§8.1) is not built in
# admind, so `Relay` below is a minimal in-memory stand-in for it: one picker table, the progression
# rule P, the delivery checks, the settlement rule for relaunch, and admind's paste. It has no store,
# so admind crash and restart cases are out of reach. Writes $S8_OUT/e2e.json and prints one line per
# check.
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(os.environ["S8_REPO"])
sys.path[:0] = [str(REPO / "src"), str(REPO / "tests" / "fakes")]
from fake_wn_agent import ACCOUNT, GROUP, FakeWnAgent  # noqa: E402

from heterodyne.admind.redact import redact  # noqa: E402
from heterodyne.marmot.control import ControlClient, InboundMessage, ReactionAdded  # noqa: E402

ADAPTER = os.environ.get("ADAPTER", "claude")
OUT = Path(os.environ["S8_OUT"])
D = Path(__file__).resolve().parent
PY = "/usr/bin/python3"
STOP_SECONDS = 10           # [admind] picker_stop_seconds, shortened for the spike
OPERATORS = {"c1" * 32: "alice", "c2" * 32: "bob"}
MALLORY = "d1" * 32          # a group member who is not an operator
NUMBERS = {"1️⃣": 0, "2️⃣": 1, "3️⃣": 2}
QTOOL = {"claude": "AskUserQuestion", "codex": "request_user_input"}[ADAPTER]
PORT = {"claude": 18997, "codex": 18996}[ADAPTER]    # the stub's port
SID = "5e8a0c1e-0000-4000-8000-0000000e2e01"


def T(*args: str, data: bytes | None = None) -> None:
    subprocess.run(["tmux", "-L", "s8spike", *args], input=data, check=True, capture_output=True)


@dataclass
class Picker:
    id: str
    launch: str
    turn: int
    question: str
    options: list[str]
    status: str = "open"        # open answered reserved delivered uncertain abandoned cancelled superseded
    turn_ended: bool = False
    answer: str | None = None
    by: str | None = None
    by_key: str | None = None
    route: str | None = None
    card: str | None = None
    notices: list[str] = field(default_factory=list)


class Relay:
    def __init__(self, fake: FakeWnAgent, client: ControlClient) -> None:
        self.fake, self.client = fake, client
        self.launch = ""
        self.turn = 0
        self.busy = False
        self.inflight: tuple[str, str] | None = None     # (picker id, pasted text) awaiting its prompt
        self.pickers: dict[str, Picker] = {}
        self.queue: list[str] = []
        self.operators = dict(OPERATORS)
        self.log: list[dict] = []
        self.sessionstarts = 0
        self.wake = asyncio.Event()

    def note(self, kind: str, **kw) -> None:
        self.log.append({"t": round(time.time(), 3), "kind": kind, **kw})

    # -- Marmot side --------------------------------------------------------------------------
    async def post(self, text: str, reply_to: str | None = None) -> str:
        sent = await self.client.send_final(ACCOUNT, GROUP, redact(text), reply_to, uuid.uuid4().hex)
        self.note("posted", text=text.splitlines()[0][:120], reply_to=reply_to)
        return sent.message_ids_hex[0]

    async def inbound(self, ev) -> None:
        if isinstance(ev, InboundMessage):
            key, text = ev.message.sender.account_id_hex, ev.message.text
            target = ev.reply_to.message_id_hex if ev.reply_to else None
            if key not in self.operators:
                self.note("dropped", why="not an operator", text=text)
                return
            if text.startswith("!asks cancel "):
                return await self.cancel(text.split()[2], key)
            if text.startswith("!answer "):
                _, pid, ans = text.split(" ", 2)
                p = self.pickers.get(pid)
                return await self.answer(p, key, "!answer", self.choice(p, ans))
            p = next((p for p in self.pickers.values() if p.card and p.card == target), None)
            if p is not None:
                return await self.answer(p, key, "reply", self.choice(p, text))
            self.note("passthrough", text=text)      # not exercised: the harness pastes prompts itself
        elif isinstance(ev, ReactionAdded):
            key = ev.actor.account_id_hex
            p = next((p for p in self.pickers.values() if p.card == ev.target_message_id_hex), None)
            if key not in self.operators:
                self.note("dropped", why="not an operator", emoji=ev.emoji)
                return
            if p is not None and ev.event_id_hex and ev.emoji in NUMBERS and NUMBERS[ev.emoji] < len(p.options):
                await self.answer(p, key, "reaction", p.options[NUMBERS[ev.emoji]])

    @staticmethod
    def choice(p: Picker | None, text: str) -> str:
        """A bare option number selects that option; anything else is a free-text answer."""
        t = text.strip()
        if p and t.isdigit() and 1 <= int(t) <= len(p.options):
            return p.options[int(t) - 1]
        return t

    async def answer(self, p: Picker | None, key: str, route: str, text: str) -> None:
        if p is None:
            return
        name = self.operators[key]
        if p.status == "open":                      # the compare-and-set: first valid answer wins
            p.status, p.answer, p.by, p.by_key, p.route = "answered", text, name, key, route
            self.note("answered", picker=p.id, by=name, route=route)
            self.evaluate(p)
        elif p.status == "abandoned":
            await self.post("This question came from a previous agent session; your answer was not "
                            "delivered.", p.card)
        elif p.status in ("cancelled", "superseded"):
            await self.post(f"Picker {p.id} was {p.status}.", p.card)
        else:
            await self.post(f"Picker {p.id} was already answered by {p.by}.", p.card)

    async def cancel(self, pid: str, key: str) -> None:
        p = self.pickers.get(pid)
        if p and p.status in ("open", "answered"):
            p.status = "cancelled"
            self.queue = [q for q in self.queue if q != pid]
            self.note("cancelled", picker=pid, by=self.operators[key])
            await self.post(f"Picker {pid} cancelled.", p.card)
        elif p:
            await self.post(f"Picker {pid} is {p.status}; cancelling changes nothing.", p.card)

    # -- hook side ----------------------------------------------------------------------------
    async def hook(self, launch: str, pl: dict) -> str | None:
        ev = pl.get("hook_event_name")
        current = launch == self.launch
        self.note("hook", event=ev, current=current, tool=pl.get("tool_name"))
        if not current:
            return None                             # stale or foreign: no effect at all
        if ev == "SessionStart":
            self.sessionstarts += 1
        elif ev == "UserPromptSubmit":
            self.busy = True
            self.turn += 1
            if self.inflight and self.inflight[1].splitlines()[0] in (pl.get("prompt") or ""):
                self.pickers[self.inflight[0]].status = "delivered"
                self.note("delivered", picker=self.inflight[0])
                self.inflight = None
        elif ev == "PreToolUse" and pl.get("tool_name") == QTOOL:
            return await self.picker(pl)
        elif ev == "Stop":
            self.busy = False
            for p in self.pickers.values():
                if p.launch == launch and p.turn <= self.turn and p.status in ("open", "answered"):
                    p.turn_ended = True
                    self.evaluate(p)
            self.wake.set()
        return None

    def deny(self, reason: str) -> str:
        return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                  "permissionDecision": "deny",
                                                  "permissionDecisionReason": reason}})

    async def picker(self, pl: dict) -> str:
        q = pl["tool_input"]["questions"][0]
        options = [o["label"] for o in q.get("options", [])]
        pid = secrets.token_hex(2)
        card = (f"Picker {pid} from the admin agent: {q['question']}\n"
                + "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))
                + "\nReply with a number or your own answer, or react with the option's number.")
        if redact(card) != card:
            self.note("refused", why="redaction")
            return self.deny("Your question can't be shown to the operators because part of it would be "
                             "redacted. Ask again in plain text, without that content.")
        for old in self.pickers.values():           # supersede: one open picker per launch
            if old.launch == self.launch and old.status in ("open", "answered"):
                old.status = "superseded"
        p = Picker(pid, self.launch, self.turn, q["question"], options)
        self.pickers[pid] = p
        self.note("picker", picker=pid)
        asyncio.get_running_loop().create_task(self.post_card(p, card))   # from the outbox, afterwards
        return self.deny(f"Your question was sent to the operators as picker {pid}; end your turn now, "
                         "and their answer will arrive as your next message.")

    async def post_card(self, p: Picker, card: str) -> None:
        p.card = await self.post(card)
        await asyncio.sleep(STOP_SECONDS)
        if not p.turn_ended and p.status in ("open", "answered"):
            p.notices.append("missing-stop")
            await self.post(f"Picker {p.id}: the agent's turn hasn't ended ({'has' if p.answer else 'no'} "
                            "answer). !tail to look, !asks cancel, or !new.", p.card)

    # -- progression and delivery ---------------------------------------------------------------
    def evaluate(self, p: Picker) -> None:
        if p.status == "answered" and p.turn_ended and p.id not in self.queue:
            self.queue.append(p.id)
            self.note("P", picker=p.id)
            self.wake.set()

    async def dispatcher(self) -> None:
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.queue and not self.busy and self.inflight is None:
                p = self.pickers[self.queue.pop(0)]
                if p.status != "answered":
                    continue
                if p.launch != self.launch:
                    p.status = "abandoned"
                    await self.post("Your answer was not delivered: the agent was relaunched.", p.card)
                    continue
                if p.by_key not in self.operators:
                    self.note("invalidated", picker=p.id, by=p.by)
                    p.status, p.answer, p.by, p.by_key = "open", None, None, None
                    continue
                text = f"[picker {p.id}, answered by {p.by} by {p.route}]\n{p.answer}"
                p.status = "reserved"
                self.inflight = (p.id, text)
                self.busy = True
                self.note("reserved", picker=p.id)
                await asyncio.to_thread(paste, text)
                self.note("pasted", picker=p.id)

    def relaunch_settle(self) -> None:
        for p in self.pickers.values():
            if p.launch == self.launch:
                if p.status == "reserved":
                    p.status = "uncertain"
                elif p.status in ("open", "answered"):
                    p.status = "abandoned"
        self.inflight, self.busy = None, False


def snap(label: str) -> None:
    out = subprocess.run(["tmux", "-L", "s8spike", "capture-pane", "-p", "-t", "=agent:"], capture_output=True, text=True)
    (OUT / f"{label}.pane.txt").write_text(out.stdout)


def paste(text: str) -> None:
    """admind's paste (heterodyne.tmux.Tmux.paste): bracketed paste, a pause, then Enter."""
    T("load-buffer", "-b", "s8", "-", data=text.encode())
    T("paste-buffer", "-p", "-d", "-b", "s8", "-t", "=agent:")
    time.sleep(0.3)
    T("send-keys", "-t", "=agent:", "Enter")


# -- the agent ----------------------------------------------------------------------------------
def configure() -> None:
    (OUT / "work").mkdir(exist_ok=True)
    if ADAPTER == "claude":
        c = OUT / "claude"
        c.mkdir(exist_ok=True)
        json.dump({"hasCompletedOnboarding": True, "theme": "dark", "bypassPermissionsModeAccepted": True,
                   "projects": {str(OUT / "work"): {"hasTrustDialogAccepted": True}}},
                  open(c / ".claude.json", "w"))
        json.dump({"claudeAiOauth": {"accessToken": "dummy-access", "refreshToken": "dummy-refresh",
                                     "expiresAt": 4102444800000, "scopes": ["user:inference", "user:profile"],
                                     "subscriptionType": "max"}}, open(c / ".credentials.json", "w"))
        os.chmod(c / ".credentials.json", 0o600)
    else:
        (OUT / "codex").mkdir(exist_ok=True)


def codex_config() -> None:
    """Rewritten before every launch, so the hook-trust entries the setup step appends are this launch's only."""
    (OUT / "codex" / "config.toml").write_text(
        'model = "gpt-stub"\nmodel_provider = "stub"\n[model_providers.stub]\nname = "stub"\n'
        f'base_url = "http://127.0.0.1:{PORT}/v1"\nenv_key = "STUB_API_KEY"\nwire_api = "responses"\n'
        f'[projects."{OUT / "work"}"]\ntrust_level = "trusted"\n')


def launch(relay: Relay, resume: bool = False) -> None:
    relay.launch = secrets.token_hex(8)
    hook = f"{PY} {D / 'e2ehook.py'} {OUT / 'hook.sock'} {relay.launch}"
    events = {"SessionStart": None, "UserPromptSubmit": None, "Stop": None, "PreToolUse": QTOOL}
    hooks = {ev: [{**({"matcher": m} if m else {}), "hooks": [{"type": "command", "command": hook}]}]
             for ev, m in events.items()}
    if ADAPTER == "claude":
        (OUT / "hooks.json").write_text(json.dumps({"hooks": hooks}))
        how = "--resume" if resume else "--session-id"
        cmd = (f"env CLAUDE_CONFIG_DIR={OUT / 'claude'} ANTHROPIC_BASE_URL=http://127.0.0.1:{PORT} "
               "CLAUDE_CODE_MAX_RETRIES=0 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 "
               f"claude {how} {SID} --permission-mode bypassPermissions --settings {OUT / 'hooks.json'} "
               "--name admin-agent")
    else:
        (OUT / "codex" / "hooks.json").write_text(json.dumps({"hooks": hooks}))
        codex_config()
        # the nonce changed the hook commands, so they need trusting again before this launch
        subprocess.run([PY, str(D / "cx-trust-hooks.py"), str(OUT / "work")], check=True, capture_output=True,
                       env={**os.environ, "CODEX_HOME": str(OUT / "codex")})
        sub = "resume --last " if resume else ""
        cmd = (f"env CODEX_HOME={OUT / 'codex'} STUB_API_KEY=dummy-not-a-key codex {sub}"
               "--dangerously-bypass-approvals-and-sandbox" + (" --no-daemon" if os.environ.get("NODAEMON") else ""))
    T("-f", "/dev/null", "new-session", "-d", "-s", "agent", "-x", "200", "-y", "50", "-c", str(OUT / "work"),
      cmd + "; sleep 600")
    relay.note("launched", launch=relay.launch, resume=resume)


def stub_hits(marker: str) -> int:
    """Model requests (with tools, so not title or side calls) whose newest user text holds `marker`."""
    n = 0
    for line in open(OUT / "stub.jsonl"):
        r = json.loads(line)
        if not r.get("tools"):
            continue
        texts = (r.get("last_text") or "") if ADAPTER == "codex" else " ".join(
            b.get("text", "") for b in r.get("last_user", []))
        if ADAPTER == "codex" and r.get("last_type") != "message":
            continue
        n += marker in texts
    return n


async def until(pred, timeout: float, step: float = 0.1) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(step)
    return pred()


async def main() -> None:
    configure()
    stub = "clstub.py" if ADAPTER == "claude" else "cxstub.py"
    stub_proc = subprocess.Popen([PY, str(D / stub), str(PORT)], env={**os.environ, "STUB_LOG": str(OUT / "stub.jsonl")},
                                 stderr=open(OUT / "stub.err", "w"))
    fake = FakeWnAgent(OUT / "wn.sock")
    await fake.start()
    client = ControlClient(OUT / "wn.sock", fake.token, timeout=5)
    relay = Relay(fake, client)
    checks: list[dict] = []

    def check(name: str, ok: bool, **detail) -> None:
        checks.append({"check": name, "ok": bool(ok), **detail})
        print(("PASS " if ok else "FAIL ") + name, json.dumps(detail) if detail else "", flush=True)

    async def on_conn(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        msg = json.loads(await r.readline())
        out = await relay.hook(msg["launch"], msg["payload"])
        w.write(json.dumps({"stdout": out}).encode() + b"\n")
        await w.drain()
        w.close()

    await asyncio.start_unix_server(on_conn, path=str(OUT / "hook.sock"))

    async def subscribe() -> None:
        async for ev in client.subscribe(ACCOUNT, GROUP):
            await relay.inbound(ev)

    sub = asyncio.create_task(subscribe())
    await fake.wait_subscribed()
    disp = asyncio.create_task(relay.dispatcher())
    mid = iter(f"{i:064x}" for i in range(1, 10_000))

    async def say(key: str, text: str, reply_to: str | None = None) -> None:
        await fake.push_event(fake.message_event(text, key, next(mid), reply_to=reply_to))

    async def react(key: str, emoji: str, target: str) -> None:
        await fake.push_event(fake.reaction_event(emoji, key, next(mid), target))

    def delay(s: float) -> None:
        (OUT / "delay").write_text(str(s))

    def stops() -> int:
        return sum(1 for e in relay.log if e["kind"] == "hook" and e["event"] == "Stop" and e["current"])

    def sent_texts() -> list[str]:
        return [s["text"] for s in fake.sent]

    async def ask(prompt: str = "PICK a deploy target") -> Picker | None:
        n = len(relay.pickers)
        await asyncio.to_thread(paste, prompt)
        if not await until(lambda: len(relay.pickers) > n, 30):
            return None
        p = list(relay.pickers.values())[-1]
        await until(lambda: p.card is not None, 10)
        return p

    async def delivered(p: Picker, timeout: float = 30) -> bool:
        return await until(lambda: p.status == "delivered", timeout)

    try:
        launch(relay)
        if ADAPTER == "claude":
            check("SessionStart with no terminal input", await until(lambda: relay.sessionstarts == 1, 30))
        else:
            await asyncio.sleep(6)          # Codex fires SessionStart with the first prompt, not at launch
        await asyncio.sleep(2)

        # 1. The whole cycle: picker, denial, card, operator answer after Stop, answer pasted once.
        delay(0)
        p = await ask()
        check("1 picker became a card", p is not None and p.card is not None,
              card=sent_texts()[-1].splitlines()[0] if p else None)
        await until(lambda: p.turn_ended, 20)
        check("1 Stop after the denied picker set the turn-ended mark", p.turn_ended)
        await say("c1" * 32, "2", p.card)
        check("1 answer delivered after Stop", await delivered(p), status=p.status)
        await asyncio.sleep(3)
        check("1 agent received the attributed answer exactly once", stub_hits(f"[picker {p.id}, answered by alice") == 1,
              hits=stub_hits(f"[picker {p.id}, answered by alice"))
        # 2. A second answer gets "already answered".
        n = len(fake.sent)
        await say("c2" * 32, "1", p.card)
        await until(lambda: len(fake.sent) > n, 5)
        check("2 second answer told 'already answered'", any("already answered by alice" in t for t in sent_texts()[n:]))
        await asyncio.sleep(2)
        check("2 second answer not pasted", stub_hits(f"[picker {p.id}, answered by bob") == 0)

        # 3. A non-operator's answer never reaches the agent; then an operator's number reaction does.
        await until(lambda: not relay.busy, 15)
        p = await ask()
        await until(lambda: p.turn_ended, 20)
        await say(MALLORY, "1", p.card)
        await react(MALLORY, "1️⃣", p.card)
        await asyncio.sleep(3)
        check("3 non-operator answer and reaction dropped", p.status == "open" and stub_hits(f"[picker {p.id}") == 0)
        await react("c2" * 32, "1️⃣", p.card)
        check("3 operator's number reaction delivered", await delivered(p), answer=p.answer, route=p.route)
        await asyncio.sleep(2)

        # 4. Redactable content: no card, and the denial asks for plain text.
        await until(lambda: not relay.busy, 15)
        n_cards, n_p = len(fake.sent), len(relay.pickers)
        await asyncio.to_thread(paste, "PICKSECRET a deploy key")
        await until(lambda: any(e["kind"] == "refused" for e in relay.log), 20)
        await asyncio.sleep(3)
        check("4 redactable picker not posted", len(fake.sent) == n_cards and len(relay.pickers) == n_p)
        check("4 denial asking for plain text reached the model", "Ask again in plain text" in (OUT / "stub.jsonl").read_text())

        # 5. An answer before Stop waits for it (the stub holds its post-denial reply for 6 s).
        await until(lambda: not relay.busy, 15)
        delay(6)
        p = await ask()
        await say("c1" * 32, "staging", p.card)
        await asyncio.sleep(1)
        check("5 early answer selected but not pasted before Stop", p.status == "answered" and not p.turn_ended)
        ok = await delivered(p, 30)
        stop_t = next((e["t"] for e in relay.log if e["kind"] == "P" and e["picker"] == p.id), None)
        paste_t = next((e["t"] for e in relay.log if e["kind"] == "pasted" and e["picker"] == p.id), None)
        check("5 early answer pasted only after Stop", ok and stop_t and paste_t and paste_t >= stop_t)
        delay(0)
        await asyncio.sleep(2)

        # 6. Answer selected, then the answerer is revoked before delivery: invalidated, picker reopens.
        await until(lambda: not relay.busy, 15)
        delay(6)
        p = await ask()
        await say("c1" * 32, "production", p.card)
        await asyncio.sleep(0.5)
        del relay.operators["c1" * 32]
        await until(lambda: p.turn_ended, 20)
        await asyncio.sleep(1)
        check("6 revoked answerer's answer invalidated, picker open again",
              p.status == "open" and stub_hits(f"[picker {p.id}") == 0)
        await say("c2" * 32, "staging", p.card)
        check("6 next valid answer delivered at once (mark kept)", await delivered(p, 15), by=p.by)
        relay.operators["c1" * 32] = "alice"
        delay(0)
        await asyncio.sleep(2)

        # 7. !interrupt instead of Stop: no turn-ended mark, no paste, then the missing-Stop notice.
        await until(lambda: not relay.busy, 15)
        delay(30)
        p = await ask()
        await say("c1" * 32, "staging", p.card)
        await asyncio.sleep(1)
        n_stop = stops()
        T("send-keys", "-t", "=agent:", "Escape")      # admind's !interrupt
        relay.busy = False                             # !interrupt clears busy without a Stop
        relay.wake.set()
        await asyncio.sleep(STOP_SECONDS + 2)
        check("7 interrupt fired no Stop and set no mark", stops() == n_stop and not p.turn_ended)
        check("7 answer not pasted after interrupt", p.status == "answered" and stub_hits(f"[picker {p.id}") == 0)
        check("7 missing-Stop notice posted once", p.notices == ["missing-stop"])
        await say("c1" * 32, f"!asks cancel {p.id}")
        await until(lambda: p.status == "cancelled", 5)
        check("7 !asks cancel closes it", p.status == "cancelled")
        snap("after-interrupt")
        delay(0)
        await asyncio.sleep(1)

        # 8. A Stop from another launch (a stale nonce) does not set the mark.
        await until(lambda: not relay.busy, 15)
        delay(30)
        p = await ask()
        out = await relay.hook("not-the-current-launch", {"hook_event_name": "Stop", "session_id": "x"})
        check("8 stale-launch Stop ignored", out is None and not p.turn_ended)

        # 9. A relaunch between answer and delivery abandons the picker; a late answer is told so.
        await say("c1" * 32, "production", p.card)
        await asyncio.sleep(0.5)
        T("kill-session", "-t", "agent")
        await asyncio.sleep(2)
        left = subprocess.run(["pgrep", "-c", "-x", ADAPTER], capture_output=True, text=True).stdout.strip()
        check("9 no CLI process outlives its tmux session", left == "0", left=left)
        relay.relaunch_settle()
        delay(0)
        launch(relay, resume=True)
        check("9 relaunch abandons the answered, never-reserved picker", p.status == "abandoned")
        n = len(fake.sent)
        await say("c2" * 32, "staging", p.card)
        await until(lambda: len(fake.sent) > n, 5)
        check("9 later answer told it came from a previous session", any(
            "previous agent session" in t for t in sent_texts()[n:]))
        if ADAPTER == "claude":
            check("9 resumed launch reached SessionStart with no input", await until(lambda: relay.sessionstarts == 2, 30))
        await asyncio.sleep(3)
        check("9 abandoned answer never pasted", stub_hits(f"[picker {p.id}") == 0)

        # 10. After the relaunch the cycle still works (fresh nonce in the hook command).
        p = await ask()
        if p is None:
            check("10 cycle works after relaunch (via !answer)", False, why="no picker from the new launch")
        else:
            await until(lambda: p.turn_ended, 20)
            await say("c1" * 32, "!answer " + p.id + " staging")
            check("10 cycle works after relaunch (via !answer)", await delivered(p), route=p.route)
        await asyncio.sleep(2)
    finally:
        snap("final")
        json.dump({"adapter": ADAPTER, "checks": checks, "log": relay.log,
                   "sent": [{"text": redact(s["text"])[:300], "reply_to": s.get("reply_to_message_id_hex")}
                            for s in fake.sent]}, open(OUT / "e2e.json", "w"), indent=1)

    sub.cancel()
    disp.cancel()
    T("kill-server")
    stub_proc.kill()
    await fake.stop()
    print(f"{sum(c['ok'] for c in checks)}/{len(checks)} checks passed")


asyncio.run(main())
