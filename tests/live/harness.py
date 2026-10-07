"""An isolated, live admind stack for end-to-end tests over the real Marmot relays (HZ_LIVE=1 only).

One temporary root per run (`/tmp/hzlive-XXXXXXXX`) holds everything:

    home/        HOME for every child (dolt's global config, btq's state and approve-bead's locks)
    hc/          HETERODYNE_CONFIG_DIR: config.toml and policy.toml for the isolated admind
    hs/          HETERODYNE_STATE_DIR: admind's state, its wn-agent home, its sockets
    btq/         BTQ_CONFIG_DIR: policy.json (approvers: tester), credentials.json, the TLS certificate
    repo/        BTQ_REPO: a git repo with a throwaway ADR and the `.beads/` connection descriptor
    dolt/        the private `dolt sql-server` (data, privileges, config) on a free loopback port
    ops/<name>/  each throwaway operator's own wn-agent home, socket and token
    logs/        every child's output (it can hold invites and keys; never printed)

Every child gets a minimal environment built from scratch (PATH, LANG, and the variables above), so no
`BEADS_*`, `BTQ_*`, `HETERODYNE_*` or `XDG_*` value of the calling shell can leak in, and every child
carries `HZ_LIVE_ROOT=<root>`. `guard` checks every resolved location before anything starts.

Processes are started in their own session (`start_new_session=True`), so each one's process group
holds its descendants (admind's wn-agent child included). Teardown signals those groups by PID, then
sweeps /proc for any process that still carries this run's `HZ_LIVE_ROOT` and kills it by PID. Nothing
is ever killed by name.

Nothing here prints a token, an nsec, an npub or a 64-hex value: diagnostics go through `mask`.
"""

import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import msgspec

from heterodyne import config as hconfig
from heterodyne.admind.settings import resolve
from heterodyne.config import paths
from heterodyne.config.secret_scan import show
from heterodyne.marmot.control import ControlClient, ControlError, FinalSent, InboundMessage, ReactionAdded
from heterodyne.marmot.nip19 import hex_to_npub

REAL_HOME = Path.home()     # captured at import, before any child environment exists
BTQ_LIVE = Path(os.environ.get("HZ_LIVE_BTQ", str(REAL_HOME / "repos" / "beads-task-queue")))
APPROVE_BEAD = BTQ_LIVE / "bin" / "approve-bead"
BTQ_SCRIPT = BTQ_LIVE / "bin" / "btq"
STUB_ADMIND = Path(__file__).with_name("stub_admind.py")
RELAYS = ("wss://relay.eu.whitenoise.chat", "wss://relay.us.whitenoise.chat")
PRODUCTION_DOLT_PORT = 3307
PRODUCTION_DATABASE = "tasks"
DATABASE = "hzlive"
PREFIX = "hzl"
ADMIND_LABEL = "heterodyne-admind"      # cli.IDENTITY_LABEL: admind init reuses an account it finds
OPERATORS = ("tester", "outsider")      # tester is a btq approver; outsider is not
BTQ_APPROVERS = ("tester",)
DESIGN_REVIEW = "reviewer=gpt-6.1-sol author=claude-opus-5-5 mode=cross-model"
READY_PREFIX = "admind is listening"
WAIT = 90.0                 # one relay round trip can take seconds; every wait is bounded

# Never used, opened or modified (the brief's isolation list, resolved against the real HOME).
FORBIDDEN = tuple(REAL_HOME / p for p in (
    ".local/state/heterodyne", ".config/heterodyne", ".config/beads-task-queue", ".hermes",
    ".local/share/beads-task-queue", ".local/state/beads-task-queue", ".local/share/whitenoise",
    ".config/systemd"))

_MASK = re.compile(r"(?i)npub1[02-9ac-hj-np-z]{20,}|nsec1[02-9ac-hj-np-z]{20,}|[0-9a-f]{32,}")


def mask(text: object, limit: int = 400) -> str:
    """Safe to print: secrets and identifiers masked (`show`, plus any run of 32+ hex), control
    characters escaped, and cut to `limit` characters."""
    out = _MASK.sub("<masked>", show(str(text), False))
    return out if len(out) <= limit else out[:limit] + "..."


class LiveError(AssertionError):
    """A harness step failed. The message is built from masked text only."""


def binary(name: str) -> str:
    """An absolute path, resolved with the caller's PATH before any child HOME exists."""
    found = shutil.which(name) or shutil.which(name, path=str(REAL_HOME / ".local" / "bin"))
    if found is None:
        raise LiveError(f"{name} is not installed")
    return str(Path(found).resolve())


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(pred: Callable[[], Any], timeout: float, what: str, every: float = 0.25) -> Any:
    """Poll `pred` until it returns something truthy; raise LiveError naming `what` on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        if time.monotonic() > deadline:
            raise LiveError(f"timed out after {timeout:.0f}s waiting for {what}")
        time.sleep(every)


def under(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def guard(root: Path, checked: Mapping[str, Path], port: int, database: str) -> None:
    """Fail before anything starts unless every resolved location is under `root`, none is under a
    forbidden production location, and the beads endpoint is the private one."""
    bad = [name for name, p in checked.items() if not under(p, root)]
    bad += [name for name, p in checked.items() if any(under(p, f) or under(f, p) for f in FORBIDDEN)]
    if bad:
        raise LiveError(f"isolation guard: these resolve outside the temp root: {sorted(set(bad))}")
    if port == PRODUCTION_DOLT_PORT or database == PRODUCTION_DATABASE:
        raise LiveError("isolation guard: the beads endpoint is the production one")


class Procs:
    """Every process the harness starts, each in its own session; stopped by process group, by PID."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.marker = f"HZ_LIVE_ROOT={root}".encode()
        self.started: list[tuple[str, subprocess.Popen[bytes]]] = []
        self.lock = threading.Lock()

    def start(self, name: str, argv: list[str], env: Mapping[str, str], log: Path,
              cwd: Path | None = None) -> subprocess.Popen[bytes]:
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            proc = subprocess.Popen(argv, env=dict(env), cwd=cwd, stdin=subprocess.DEVNULL, stdout=fd,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            os.close(fd)
        with self.lock:
            self.started.append((name, proc))
        return proc

    def run(self, argv: list[str], env: Mapping[str, str], timeout: float, stdin: bytes | None = None,
            cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
        """A short-lived child, in its own session; its whole group is killed if it outlives `timeout`."""
        proc = subprocess.Popen(argv, env=dict(env), cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        with self.lock:
            self.started.append((Path(argv[0]).name, proc))
        try:
            out, err = proc.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.stop(proc, grace=0)
            what = f"{Path(argv[0]).name} {mask(argv[1:3])}"
            raise LiveError(f"{what} timed out after {timeout:.0f}s") from None
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)

    @staticmethod
    def stop(proc: subprocess.Popen[bytes], grace: float = 15.0) -> None:
        """SIGTERM the child's process group (its pgid is its PID), then SIGKILL it after `grace`."""
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(grace)
            except subprocess.TimeoutExpired:
                pass
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)     # descendants left in the group, even if the leader exited
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(5)

    def marked(self) -> list[int]:
        """PIDs of live processes whose environment carries this run's HZ_LIVE_ROOT."""
        found: list[int] = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                environ = (entry / "environ").read_bytes()
            except OSError:
                continue
            if self.marker in environ.split(b"\0"):
                found.append(int(entry.name))
        return found

    def stop_all(self) -> list[int]:
        """Stop everything, newest first; then kill by PID whatever still carries the marker. Returns the
        PIDs the sweep found (it should find none)."""
        with self.lock:
            started = list(reversed(self.started))
        for _, proc in started:
            self.stop(proc)
        leftovers = self.marked()
        for pid in leftovers:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        return leftovers


def send_reaction(client: ControlClient, account: str, group: str, target: str, emoji: str) -> FinalSent:
    """`send_reaction` per spike S4 (the src client has none yet): `app_event_sent` with message IDs."""
    return asyncio.run(client.call({"type": "send_reaction", "account_id_hex": account, "group_id_hex": group,
                                    "target_message_id_hex": target, "emoji": emoji},
                                   "app_event_sent", FinalSent))


@dataclass
class Seen:
    """One event an operator's subscription delivered."""
    kind: str                   # "message" or "reaction"
    message_id: str
    sender: str
    is_self: bool
    text: str = ""
    reply_to: str | None = None
    emoji: str | None = None
    target: str | None = None
    at: float = field(default_factory=time.monotonic)


class Operator:
    """A throwaway operator: a private wn-agent in its own home, driven over its control socket."""

    def __init__(self, stack: "Stack", name: str) -> None:
        self.stack = stack
        self.name = name
        self.home = stack.root / "ops" / name
        self.socket = self.home / "ctl" / "wn-agent.sock"
        self.token_file = self.home / "control.token"
        self.account = ""
        self.npub = ""
        self.group = ""
        self.client: ControlClient | None = None
        self.events: list[Seen] = []
        self.cond = threading.Condition()
        self.stopping = threading.Event()
        self.subscribed = threading.Event()
        self.thread: threading.Thread | None = None

    def __repr__(self) -> str:      # never the account or npub
        return f"Operator({self.name})"

    # --- setup --------------------------------------------------------------------------------
    def start(self, admind_npub: str) -> None:
        """Start this operator's wn-agent and bootstrap its account, allowing only the isolated admind
        to invite it."""
        (self.home / "ctl").mkdir(parents=True, mode=0o700)
        self.home.chmod(0o700)
        write_private(self.token_file, secrets.token_hex(32) + "\n")
        self.stack.procs.start(f"wn-agent:{self.name}", [
            self.stack.wn_agent, "--home", str(self.home), "--socket", str(self.socket),
            "--auth-token-file", str(self.token_file), *relay_args()], self.stack.env,
            self.stack.logs / f"wn-agent-{self.name}.log")
        boot = self.stack.procs.run([
            self.stack.wn_agent, "bootstrap", "--home", str(self.home), "--socket", str(self.socket),
            "--auth-token-file", str(self.token_file), "--label", f"hz-{self.name}",
            "--allow-welcomer", admind_npub, "--invite-policy", "allowlist", "--no-quic", "--json",
            "--wait-for-socket", "30", *relay_args()], self.stack.env, timeout=120)
        if boot.returncode != 0:
            raise LiveError(f"wn-agent bootstrap for {self.name} failed (exit {boot.returncode})")
        info = json.loads(boot.stdout)          # holds invite details: parsed, never printed
        self.account = str(info["account_id_hex"]).lower()
        self.npub = hex_to_npub(self.account)
        token = self.token_file.read_text().strip()
        self.client = ControlClient(self.socket, token, timeout=60)

    def joined(self, group: str, members: int) -> bool:
        """This operator's wn-agent knows the group with the expected member count (welcome accepted)."""
        try:
            info = asyncio.run(self._client.group_info(self.account, group))
        except ControlError:
            return False
        return info.member_count == members

    def subscribe(self, group: str) -> None:
        self.group = group
        self.thread = threading.Thread(target=self._subscription, name=f"sub-{self.name}", daemon=True)
        self.thread.start()
        if not self.subscribed.wait(30):
            raise LiveError(f"{self.name}'s subscription was not acknowledged")

    def _subscription(self) -> None:
        async def ack() -> None:
            self.subscribed.set()

        async def loop() -> None:
            while not self.stopping.is_set():
                try:
                    async for ev in self._client.subscribe(self.account, self.group, on_ack=ack):
                        self._record(ev)
                except ControlError:
                    if self.stopping.is_set():
                        return
                    await asyncio.sleep(1)      # wn-agent restarted or the socket dropped: resubscribe
        with contextlib.suppress(Exception):
            asyncio.run(loop())

    def _record(self, ev: object) -> None:
        seen: Seen | None = None
        if isinstance(ev, InboundMessage) and ev.group_id_hex.lower() == self.group:
            m = ev.message
            reply_to = None if ev.reply_to is None else ev.reply_to.message_id_hex.lower()
            seen = Seen("message", m.message_id_hex.lower(), m.sender.account_id_hex.lower(),
                        m.sender.is_self, text=m.text, reply_to=reply_to)
        elif isinstance(ev, ReactionAdded) and ev.group_id_hex.lower() == self.group:
            seen = Seen("reaction", "", ev.actor.account_id_hex.lower(), ev.actor.is_self, emoji=ev.emoji,
                        target=ev.target_message_id_hex.lower())
        if seen is not None:
            with self.cond:
                self.events.append(seen)
                self.cond.notify_all()

    @property
    def _client(self) -> ControlClient:
        if self.client is None:
            raise LiveError(f"{self.name} is not started")
        return self.client

    # --- the test API -------------------------------------------------------------------------
    def send(self, text: str) -> str:
        """Post a top-level message; returns its message ID."""
        return self._send(text, None)

    def reply(self, message_id: str, text: str) -> str:
        """Post `text` as a reply to `message_id`; returns the reply's message ID."""
        return self._send(text, message_id)

    def _send(self, text: str, reply_to: str | None) -> str:
        sent = asyncio.run(self._client.send_final(self.account, self.group, text, reply_to,
                                                   f"hz-{uuid.uuid4().hex}"))
        return sent.message_ids_hex[0].lower()

    def react(self, message_id: str, emoji: str) -> str:
        """React to `message_id`; returns the reaction event's ID."""
        return send_reaction(self._client, self.account, self.group, message_id, emoji).message_ids_hex[0]

    def from_admind(self) -> list[Seen]:
        with self.cond:
            return [e for e in self.events if e.kind == "message" and e.sender == self.stack.admind_account]

    def wait_message(self, pred: Callable[[Seen], bool], what: str, timeout: float = WAIT) -> Seen:
        """The first message from admind matching `pred`, waiting up to `timeout` seconds."""
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for e in self.events:
                    if e.kind == "message" and e.sender == self.stack.admind_account and pred(e):
                        return e
                left = deadline - time.monotonic()
                if left <= 0:
                    recent = [mask(e.text, 120) for e in self.events[-5:] if e.kind == "message"]
                    raise LiveError(f"{self.name}: timed out after {timeout:.0f}s waiting for {what}; "
                                    f"last messages seen: {recent}")
                self.cond.wait(min(left, 1.0))

    def wait_reply(self, to: str, contains: str | None = None, timeout: float = WAIT) -> str:
        """The text of admind's reply threaded to message `to` (containing `contains`, if given)."""
        hit = self.wait_message(lambda e: e.reply_to == to and (contains is None or contains in e.text),
                                f"admind's reply{'' if contains is None else ' with ' + repr(contains)}",
                                timeout)
        return hit.text

    def wait_card(self, ask_id: str, timeout: float = WAIT) -> list[str]:
        """The message IDs of ask `ask_id`'s card chunks, in order, once this operator has received every
        one. The IDs come from admind's outbox; arrival here proves the operator sees the same IDs."""
        ids: list[str] = wait_for(lambda: self.stack.card_message_ids(ask_id), timeout,
                                  f"ask {ask_id}'s card to be sent")
        for mid in ids:
            self.wait_message(lambda e, mid=mid: e.message_id == mid, f"ask {ask_id}'s card", timeout)
        return ids

    def stop(self) -> None:
        self.stopping.set()


def relay_args() -> list[str]:
    return [arg for relay in RELAYS for arg in ("--relay", relay)]


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def toml_str(value: str) -> str:
    return json.dumps(value)      # a JSON string is a valid TOML basic string for these values


class Stack:
    """The isolated stack: private beads, btq config, admind (stub agent) and two throwaway operators."""

    def __init__(self) -> None:
        self.wn_agent = binary("wn-agent")
        self.bd = binary("bd")
        self.dolt = binary("dolt")
        self.git = binary("git")
        self.openssl = binary("openssl")
        self.root = Path(tempfile.mkdtemp(prefix="hzlive-", dir="/tmp"))
        self.root.chmod(0o700)
        self.procs = Procs(self.root)
        self.timings: dict[str, float] = {}
        self.closed = False
        self.home = self.root / "home"
        self.hconfig = self.root / "hc"
        self.hstate = self.root / "hs"
        self.btq_dir = self.root / "btq"
        self.repo = self.root / "repo"
        self.dolt_dir = self.root / "dolt"
        self.logs = self.root / "logs"
        self.workdir = self.root / "work"
        for d in (self.home, self.hconfig, self.hstate, self.btq_dir, self.repo, self.dolt_dir / "data",
                  self.dolt_dir / "cfg", self.logs, self.workdir, self.root / "ops"):
            d.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.port = free_port()
        self.passwords = {"root": secrets.token_hex(16), "bel": secrets.token_hex(16)}
        self.env = self._env()
        self.admind_state = self.hstate / "admind"
        self.admind_home = self.admind_state / "marmot"
        self.admind_account = ""
        self.group = ""
        self.adr_sha = ""
        self.ops = {name: Operator(self, name) for name in OPERATORS}
        self.admind: subprocess.Popen[bytes] | None = None
        self.sweep_found: list[int] = []

    def _env(self) -> dict[str, str]:
        """A minimal environment for every child, built from scratch."""
        root = str(self.root)
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "TERM": "dumb",
            "HZ_LIVE_ROOT": root, "HOME": str(self.home), "TMPDIR": str(self.root / "tmp"),
            "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_STATE_HOME": str(self.home / ".local/state"),
            "XDG_DATA_HOME": str(self.home / ".local/share"), "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_RUNTIME_DIR": str(self.root / "run"), "TMUX_TMPDIR": str(self.root / "run"),
            "HETERODYNE_CONFIG_DIR": str(self.hconfig), "HETERODYNE_STATE_DIR": str(self.hstate),
            "BTQ_CONFIG_DIR": str(self.btq_dir), "BTQ_POLICY": str(self.btq_dir / "policy.json"),
            "BTQ_REPO": str(self.repo), "BTQ_DOLT_HOST": "127.0.0.1", "BTQ_DOLT_PORT": str(self.port),
            "BTQ_DOLT_DATABASE": DATABASE, "BD_NON_INTERACTIVE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def timed(self, step: str, fn: Callable[[], None]) -> None:
        start = time.monotonic()
        fn()
        self.timings[step] = round(time.monotonic() - start, 1)

    # --- lifecycle ----------------------------------------------------------------------------
    def start(self) -> None:
        for d in ("tmp", "run"):
            (self.root / d).mkdir(mode=0o700)
        self.timed("guard", self.check_isolation)
        self.timed("beads", self.start_beads)
        self.timed("admind identity", self.create_admind_identity)
        self.timed("operators", self.start_operators)
        self.write_policy()
        self.check_isolation(with_admind=True)
        self.timed("admind init", self.admind_init)
        self.timed("operators joined", self.wait_joined)
        for op in self.ops.values():
            op.subscribe(self.group)
        self.timed("admind start", self.start_admind)
        self.timed("join signal", self.join_signal)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for op in self.ops.values():
            op.stop()
        start = time.monotonic()
        self.sweep_found = self.procs.stop_all()
        self.timings["teardown"] = round(time.monotonic() - start, 1)
        if os.environ.get("HZ_LIVE_KEEP") != "1":
            shutil.rmtree(self.root, ignore_errors=True)

    def check_isolation(self, with_admind: bool = False) -> None:
        """Every location the children will resolve, computed the way they compute it."""
        env = self.env
        checked: dict[str, Path] = {name: Path(env[name]) for name in (
            "HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
            "XDG_RUNTIME_DIR", "TMUX_TMPDIR", "HETERODYNE_CONFIG_DIR", "HETERODYNE_STATE_DIR",
            "BTQ_CONFIG_DIR", "BTQ_POLICY", "BTQ_REPO")}
        checked["heterodyne config_dir"] = paths.config_dir(env)
        checked["heterodyne state_dir"] = paths.state_dir(env)
        checked["dolt data"] = self.dolt_dir
        checked.update({f"operator {n} home": op.home for n, op in self.ops.items()})
        for name, value in self.btq_locations().items():
            if name in ("config_dir", "repo", "credentials", "tls_cert"):
                checked[f"btq {name}"] = Path(value)
        locs = self.btq_locations()
        if locs["dolt_port"] != str(self.port) or locs["dolt_database"] != DATABASE:
            raise LiveError("isolation guard: btq does not resolve the private beads endpoint")
        if with_admind:
            s = resolve(hconfig.load(env=env), env)
            checked.update({"admind state_dir": s.state_dir, "admind marmot_home": s.marmot_home,
                            "admind workdir": s.workdir, "admind alerts_dir": s.alerts_dir})
            if s.approve_bead != APPROVE_BEAD:
                raise LiveError("isolation guard: admind's approve_bead is not the btq checkout's")
            if s.relays != RELAYS:
                raise LiveError("isolation guard: admind's relays are not the configured test relays")
        guard(self.root, checked, int(locs["dolt_port"]), locs["dolt_database"])

    def btq_locations(self) -> dict[str, str]:
        """btq's own `locations()` under the child environment (bin/btq is imported, never run)."""
        code = ("import importlib.machinery, importlib.util, json, sys\n"
                "loader = importlib.machinery.SourceFileLoader('btq', sys.argv[1])\n"
                "spec = importlib.util.spec_from_loader('btq', loader)\n"
                "mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)\n"
                "print(json.dumps(mod.locations()))\n")
        out = self.procs.run([sys.executable, "-c", code, str(BTQ_SCRIPT)], self.env, timeout=30)
        if out.returncode != 0:
            raise LiveError("cannot read btq's locations")
        return json.loads(out.stdout)

    # --- beads ----------------------------------------------------------------------------------
    def start_beads(self) -> None:
        """A private dolt sql-server (TLS available, as btq requires it) with one database for bd."""
        crt, key = self.btq_dir / "server.crt", self.btq_dir / "server.key"
        self.must([self.openssl, "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "2",
                   "-keyout", str(key), "-out", str(crt), "-subj", "/CN=hz-live beads",
                   "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], "openssl")
        for k, v in (("user.name", "hz-live"), ("user.email", "hz-live@localhost")):
            self.must([self.dolt, "config", "--global", "--add", k, v], "dolt config")
        cfg = self.dolt_dir / "dolt.yaml"
        # Plaintext is allowed on this private loopback server only so that `bd init` (which ignores
        # the TLS flag before metadata exists) can run as root; btq's clients always use TLS.
        cfg.write_text(f"""log_level: warning
behavior:
  autocommit: true
  dolt_transaction_commit: false
listener:
  host: 127.0.0.1
  port: {self.port}
  require_secure_transport: false
  tls_cert: {crt}
  tls_key: {key}
data_dir: {self.dolt_dir / 'data'}
cfg_dir: {self.dolt_dir / 'cfg'}
privilege_file: {self.dolt_dir / 'cfg' / 'privileges.db'}
""")
        self.procs.start("dolt", [self.dolt, "sql-server", "--config", str(cfg)], self.env,
                         self.logs / "dolt.log", cwd=self.dolt_dir)

        def listening() -> bool:
            with socket.socket() as s:
                return s.connect_ex(("127.0.0.1", self.port)) == 0
        wait_for(listening, 30, "the private dolt sql-server")
        sql = (f"CREATE DATABASE {DATABASE}; "
               f"CREATE USER 'bel'@'%' IDENTIFIED BY '{self.passwords['bel']}'; "
               f"GRANT ALL PRIVILEGES ON {DATABASE}.* TO 'bel'@'%';")
        self.must([self.dolt, "--host", "127.0.0.1", "--port", str(self.port), "--user", "root",
                   "--password", "", "--no-tls", "sql", "-q", sql], "dolt sql (create database)")
        # A throwaway ADR to pin, in a git repo with no remote (cards then say "read it on the host").
        git = [self.git, "-C", str(self.repo), "-c", "user.name=hz-live", "-c", "user.email=hz@localhost"]
        self.must([*git, "init", "-q"], "git init")
        (self.repo / "docs" / "adr").mkdir(parents=True)
        (self.repo / "docs" / "adr" / "0001-live.md").write_text(
            "# ADR 0001: live harness\n\nA throwaway ADR for the admind live harness. It decides nothing.\n")
        self.must([*git, "add", "docs"], "git add")
        self.must([*git, "commit", "-q", "-m", "throwaway ADR"], "git commit")
        self.adr_sha = self.must([*git, "rev-parse", "HEAD"], "git rev-parse").strip()
        self.must([self.bd, "init", "--server", "--external", "--database", DATABASE,
                   "--server-host", "127.0.0.1", "--server-port", str(self.port), "--server-user", "root",
                   "--prefix", PREFIX, "--non-interactive", "--skip-agents", "--skip-hooks"], "bd init",
                  env={**self.env, "BEADS_DOLT_PASSWORD": ""}, cwd=self.repo)
        write_private(self.btq_dir / "credentials.json", json.dumps({"bel": self.passwords["bel"]}) + "\n")
        (self.btq_dir / "policy.json").write_text(json.dumps({"approvers": list(BTQ_APPROVERS)}) + "\n")

    def bd_env(self) -> dict[str, str]:
        """What btq's Queue passes to bd: the `bel` credential over TLS to the private server."""
        return {**self.env, "BEADS_DOLT_PASSWORD": self.passwords["bel"], "BEADS_DOLT_SERVER_USER": "bel",
                "BEADS_DOLT_SERVER_TLS": "true", "SSL_CERT_FILE": str(self.btq_dir / "server.crt"),
                "BEADS_DIR": str(self.repo / ".beads"), "BEADS_DOLT_SERVER_HOST": "127.0.0.1",
                "BEADS_DOLT_SERVER_PORT": str(self.port), "BEADS_DOLT_SERVER_DATABASE": DATABASE}

    def must(self, argv: list[str], what: str, env: Mapping[str, str] | None = None, cwd: Path | None = None,
             stdin: bytes | None = None, timeout: float = 60) -> str:
        out = self.procs.run(argv, env or self.env, timeout, stdin=stdin, cwd=cwd)
        if out.returncode != 0:
            err = mask(out.stderr.decode(errors="replace"))
            raise LiveError(f"{what} failed (exit {out.returncode}): {err}")
        return out.stdout.decode()

    def bd_json(self, *args: str) -> Any:
        out = self.must([self.bd, "-C", str(self.repo), "--actor", "hz-live", *args, "--json"],
                        f"bd {args[0]}", env=self.bd_env())
        return json.loads(out or "null")

    def create_approval_bead(self, title: str, description: str | None = None, why: str = "Prove the relay",
                             extra: str = "") -> str:
        """An open kind:approval bead with a complete ask pinning the throwaway ADR; returns its ID."""
        description = description if description is not None else (
            "A throwaway approval bead for the admind live harness. It pins a throwaway ADR in a temporary "
            "git repository and is recorded in a private beads database that is deleted after the run. "
            "Approving it changes nothing outside the temporary root. " * 2 + extra)
        meta = {"adr_revision": self.adr_sha, "design_review": DESIGN_REVIEW,
                "ask": {"why": why, "effect": ["records a test decision in the private beads database"],
                        "excludes": ["anything outside the temporary root"],
                        "risks": ["none: the database and repository are throwaway"],
                        "refs": [{"id": self.adr_sha, "kind": "file", "path": "docs/adr/0001-live.md",
                                  "repo": str(self.repo)}]}}
        made = self.bd_json("create", title, "-d", description, "-l", "kind:approval",
                            "--metadata", json.dumps(meta))
        return str(made["id"])

    def edit_bead(self, bead: str, title: str | None = None, description: str | None = None) -> None:
        args = ["update", bead]
        if title is not None:
            args += ["--title", title]
        if description is not None:
            args += ["--description", description]
        self.bd_json(*args)

    def approve_bead_json(self, bead: str) -> dict[str, Any]:
        """`approve-bead <bead> --json`, as admind runs it (same environment)."""
        return json.loads(self.must([str(APPROVE_BEAD), bead, "--json"], "approve-bead --json"))

    # --- admind ---------------------------------------------------------------------------------
    def write_config(self) -> None:
        relays = ", ".join(toml_str(r) for r in RELAYS)
        (self.hconfig / "config.toml").write_text(f"""[platform]
os = "linux"
service_manager = "systemd"
sandbox = "bubblewrap"

[profiles.admin]
adapter = "claude-code"
model = "stub"

[adapters.claude-code]
binary = "/bin/false"

[admind]
profile = "admin"
workdir = {toml_str(str(self.workdir))}
restart_units = []
start_timeout_seconds = 3600
group_check_seconds = 20
alert_poll_seconds = 5
approve_bead = {toml_str(str(APPROVE_BEAD))}

[admind.marmot]
wn_agent = {toml_str(self.wn_agent)}
relays = [{relays}]
""")

    def create_admind_identity(self) -> None:
        """admind's identity before its group exists, so the operators can allow it as their only
        welcomer: the same home, token file and label `admind init` uses (init reuses the account)."""
        self.write_config()
        home = self.admind_home
        for d in (self.admind_state, home, home / "ctl"):
            d.mkdir(mode=0o700, exist_ok=True)
        token = home / "control.token"
        write_private(token, secrets.token_hex(32) + "\n")
        sock = home / "ctl" / "wn-agent.sock"
        child = self.procs.start("wn-agent:admind-identity", [
            self.wn_agent, "--home", str(home), "--socket", str(sock), "--auth-token-file", str(token),
            *relay_args()], self.env, self.logs / "wn-agent-admind-identity.log")
        try:
            out = self.must([self.wn_agent, "bootstrap", "--home", str(home), "--socket", str(sock),
                             "--auth-token-file", str(token), "--label", ADMIND_LABEL,
                             "--invite-policy", "deny", "--no-quic", "--json", "--wait-for-socket", "30",
                             *relay_args()],
                            "wn-agent bootstrap (admind)", timeout=120)
            self.admind_account = str(json.loads(out)["account_id_hex"]).lower()
        finally:
            self.procs.stop(child)

    def start_operators(self) -> None:
        npub = hex_to_npub(self.admind_account)
        for op in self.ops.values():
            op.start(npub)

    def write_policy(self) -> None:
        names = ", ".join(toml_str(n) for n in OPERATORS)
        idents = "".join(f"\n[identities.{n}]\nmarmot_npub = {toml_str(op.npub)}\n"
                         for n, op in self.ops.items())
        (self.hconfig / "policy.toml").write_text(f"operators = [{names}]\napprovers = [{names}]\n{idents}")

    def admind_init(self) -> None:
        out = self.admind_cmd("init", timeout=180)
        if out.returncode != 0:
            raise LiveError(f"admind init failed (exit {out.returncode}): {mask(out.stderr.decode())}")
        self.group = wait_for(lambda: self.store_get("group_id_hex"), 5, "admind's group ID")

    def wait_joined(self) -> None:
        members = 1 + len(self.ops)
        for op in self.ops.values():
            wait_for(lambda op=op: op.joined(self.group, members), 180, f"{op.name} to join admind's group",
                     every=2)

    def start_admind(self) -> None:
        self.admind = self.procs.start("admind", [sys.executable, str(STUB_ADMIND)], self.env,
                                       self.logs / "admind.log")

        def answering() -> bool:
            if self.admind is not None and self.admind.poll() is not None:
                raise LiveError(f"the stub admind exited with status {self.admind.returncode}")
            return self.admind_cmd("ask", "list", timeout=40).returncode == 0
        wait_for(answering, 90, "the stub admind to answer on ask.sock", every=1)

    def join_signal(self) -> None:
        """admind posts nothing until an operator speaks (docs/admind.md §2 step 5). `!asks` is handled
        by admind itself, so nothing reaches the (stub) agent."""
        mid = self.ops["tester"].send("!asks")
        for op in self.ops.values():
            op.wait_message(lambda e: e.text.startswith(READY_PREFIX), "admind's ready notice")
        self.ops["tester"].wait_reply(mid)

    def admind_cmd(self, *args: str, stdin: bytes | None = None,
                   timeout: float = 200) -> subprocess.CompletedProcess[bytes]:
        argv = [sys.executable, "-m", "heterodyne.admind", *args]
        return self.procs.run(argv, self.env, timeout, stdin=stdin)

    def ask_json(self, *args: str, stdin: bytes | None = None) -> dict[str, Any]:
        out = self.admind_cmd("ask", *args, "--json", stdin=stdin)
        if not out.stdout.strip():
            raise LiveError(f"admind ask {args[0]} printed nothing (exit {out.returncode}): "
                            f"{mask(out.stderr.decode())}")
        return json.loads(out.stdout)

    def post_question_reply(self, title: str, body: str) -> dict[str, Any]:
        return self.ask_json("post", "--kind", "question", "--title", title, "--body-file", "-", "--from",
                             "hz-live", stdin=body.encode())

    def post_question(self, title: str, body: str) -> str:
        return self._posted(self.post_question_reply(title, body))

    def post_approval_reply(self, bead: str) -> dict[str, Any]:
        return self.ask_json("post", "--kind", "approval", "--bead", bead, "--from", "hz-live")

    def post_approval(self, bead: str) -> str:
        return self._posted(self.post_approval_reply(bead))

    @staticmethod
    def _posted(reply: dict[str, Any]) -> str:
        if reply.get("result") != "posted" or not reply.get("ask"):
            raise LiveError(f"the ask was not posted: {mask(reply.get('message'))}")
        return str(reply["ask"]["summary"]["ask_id"])

    def ask_get(self, ask_id: str) -> dict[str, Any]:
        return self.ask_json("get", ask_id)

    def ask_list(self) -> dict[str, Any]:
        return self.ask_json("list")

    def ask_cancel(self, ask_id: str) -> subprocess.CompletedProcess[bytes]:
        return self.admind_cmd("ask", "cancel", ask_id)

    def wait_ask(self, ask_id: str, pred: Callable[[dict[str, Any]], bool], what: str,
                 timeout: float = WAIT) -> dict[str, Any]:
        return wait_for(lambda: (lambda r: r if pred(r) else None)(self.ask_get(ask_id)), timeout,
                        f"ask {ask_id}: {what}", every=1)

    # --- reading admind's state (read only) -------------------------------------------------------
    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.admind_state / 'admind.db'}?mode=ro", uri=True, timeout=5)

    def store_get(self, key: str) -> str | None:
        with contextlib.closing(self._db()) as db:
            row = db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def card_message_ids(self, ask_id: str) -> list[str] | None:
        """The sent message IDs of ask `ask_id`'s card chunks, in order, once every chunk is sent."""
        with contextlib.closing(self._db()) as db:
            row = db.execute("SELECT card_parts FROM asks WHERE ask_id = ?", (ask_id,)).fetchone()
            if row is None:
                return None
            rows = db.execute("SELECT key, message_id FROM outbox WHERE key LIKE ? AND status = 'sent'",
                              (f"ask:{ask_id}:%",)).fetchall()
        parts = sorted(((int(str(k).rsplit(":", 1)[1]), str(m).lower()) for k, m in rows if m))
        return [m for _, m in parts] if len(parts) == int(row[0]) else None

    def audit_text(self) -> str:
        return (self.admind_state / "audit.jsonl").read_text()

    def audit_records(self) -> list[dict[str, Any]]:
        return [msgspec.json.decode(line) for line in self.audit_text().splitlines() if line.strip()]
