"""AU-11 (btq-w1elg): each admind process runs on its profile's first account (ADR 0001 §4.4 D11, D10).

The startup replacement of a pane on another login and its crash-durable notice run over the fake tmux of
`test_admind_r1`. The process environments run the real tmux on a private socket (`tmux_guard`) with a
fake `claude`, and a shell summarizer. The redaction runs through admind's real surfaces with the fake
claude. Nothing touches a real login directory or the network.
"""

import asyncio
import dataclasses
import json
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes.settings import make_settings
from test_admind_daemon import Harness, needs_tmux, run_with
from test_admind_r1 import Unit, run
from test_admind_r13_replies import (
    LONG,
    append,
    audit_records,
    batch_arrives,
    configure,
    details_of,
    prompt,
    result,
    session,
    tool,
    waited,
)
from tmux_guard import guarded_tmux, new_test_tmux

from heterodyne.admind import cli, summarize
from heterodyne.admind import redact as redact_module
from heterodyne.admind.agent import SESSION, AdminAgent
from heterodyne.admind.daemon import LOGIN_CHANGED_NOTICE, LOGIN_CHANGED_WHY
from heterodyne.admind.redact import LOGIN_DIR, redact, set_login_dirs
from heterodyne.admind.settings import LOGIN_OVERRIDES, AdmindAccount
from heterodyne.admind.store import Store

OVERRIDES = LOGIN_OVERRIDES["claude-code"]
ACCOUNT_B = AdmindAccount("b", "ck1-" + "b" * 32, {"CLAUDE_CONFIG_DIR": "/accounts/b"}, OVERRIDES)


class Crash(BaseException):
    """The process dies here (not an Exception: nothing in admind catches it)."""


@pytest.fixture(autouse=True)
def _no_login_dirs() -> Iterator[None]:
    """Redaction state is module-wide; every test starts and ends without login directories."""
    set_login_dirs(())
    yield
    set_login_dirs(())


# --- startup: a live pane on another login is replaced, with one notice ---


def on_account_b(u: Unit) -> None:
    """The config now names account b; the pane was launched on the default login."""
    u.settings = dataclasses.replace(u.settings, agent_account=ACCOUNT_B)
    u.build()


def notices(u: Unit) -> list[tuple[str, str]]:
    rows = u.store.db.execute("SELECT key, text FROM outbox WHERE key LIKE 'login-changed:%'").fetchall()
    return [(str(k), str(t)) for k, t in rows]


def committed(u: Unit, key: str) -> str | None:
    """`key` as another connection sees it: only what is committed."""
    db = sqlite3.connect(u.settings.state_dir / "admind.db")
    try:
        row = db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])
    finally:
        db.close()


def audited_actions(u: Unit) -> list[str]:
    lines = u.audit_text().splitlines()
    return [r.get("action", "") for r in map(json.loads, lines) if r.get("kind") == "agent"]


async def restart(u: Unit) -> None:
    """A fresh daemon on the same store and pane, started as `run` starts it."""
    u.build()
    u.daemon.recover()
    await u.daemon.start_agent(startup=True)


def test_a_pane_on_another_login_is_replaced_at_startup_with_one_notice(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    seen: dict[str, str | None] = {}

    async def scenario() -> None:
        mid = await u.say("job A")
        assert u.store.get("in_flight") == mid
        on_account_b(u)
        real_kill = u.tmux.kill

        def kill(name: str) -> None:
            seen["replace_pending"] = committed(u, "replace_pending")
            seen["pending_notice"] = committed(u, "pending_notice")
            real_kill(name)
        u.tmux.kill = kill                                  # type: ignore[method-assign]
        await u.daemon.start_agent(startup=True)
        assert seen == {"replace_pending": "login-changed", "pending_notice": "login-changed:1"}
        assert u.store.get("in_flight") is None
        assert (f"No reply to this message: {LOGIN_CHANGED_WHY}.", mid) in [(t, r) for _, t, r in u.outbox()]
        assert u.store.get("agent_account") == ACCOUNT_B.key and u.agent.login_matches()
        assert u.store.get("agent_session") != "S1"         # a fresh session, never a resume
        assert u.store.get("replace_pending") is None and u.store.get("pending_notice") is None
        assert "adopted" not in audited_actions(u) and "launched-login-changed" in audited_actions(u)
        assert notices(u) == [("login-changed:1", LOGIN_CHANGED_NOTICE)]
        await restart(u)                                    # the replacement is adopted; no second notice
        assert "adopted" in audited_actions(u)
        assert notices(u) == [("login-changed:1", LOGIN_CHANGED_NOTICE)]
    run(scenario())


def test_a_matching_pane_is_adopted_without_a_notice(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    run(restart(u))
    assert audited_actions(u)[-2:] == ["adopted", "adopted-hold"] and notices(u) == []


def crash_on_first(u: Unit, attr: str, after: bool = False) -> None:
    """The first call of `u.tmux.<attr>` crashes the process: before it acts, or `after` it acted."""
    real = getattr(u.tmux, attr)
    armed = [True]

    def wrapper(*a: Any, **kw: Any) -> None:
        if armed[0]:
            armed[0] = False
            if after:
                real(*a, **kw)
            raise Crash
        real(*a, **kw)
    setattr(u.tmux, attr, wrapper)


def crash_flush_before_commit(u: Unit) -> None:
    """Inside the flush transaction, after the notice is queued and before the commit."""
    real = u.store.delete

    def delete(key: str) -> None:
        if key == "pending_notice":
            raise Crash
        real(key)
    u.store.delete = delete                                 # type: ignore[method-assign]


@pytest.mark.parametrize("point", ["before-kill", "after-spawn", "after-clear", "in-flush", "after-flush"])
def test_the_notice_survives_a_crash_at_every_point(tmp_path: Path, point: str) -> None:
    u = Unit(tmp_path)
    on_account_b(u)

    async def scenario() -> None:
        if point == "before-kill":
            crash_on_first(u, "kill")
        elif point == "after-spawn":
            crash_on_first(u, "new_session", after=True)
        elif point == "after-clear":
            def crash() -> None:
                raise Crash
            u.daemon.flush_pending_notice = crash           # type: ignore[method-assign]
        elif point == "in-flush":
            crash_flush_before_commit(u)
        if point == "after-flush":
            await u.daemon.start_agent(startup=True)
        else:
            with pytest.raises(Crash):
                await u.daemon.start_agent(startup=True)
            assert notices(u) == []                         # nothing was posted before the crash
            assert u.store.get("pending_notice") == "login-changed:1"
        if point == "before-kill":
            assert u.store.get("replace_pending") == "login-changed"
            assert u.tmux.has_session(SESSION) and not u.agent.login_matches()    # the old pane
        if point == "after-clear":
            assert u.store.get("replace_pending") is None and u.agent.login_matches()
        u.store = Store(u.settings.state_dir / "admind.db")     # the restarted process's own connection
        await restart(u)
        assert notices(u) == [("login-changed:1", LOGIN_CHANGED_NOTICE)]
        assert u.agent.login_matches() and u.store.get("pending_notice") is None
        assert u.store.get("agent_session") != "S1"
        if point == "after-clear":
            assert audited_actions(u)[-2:] == ["adopted", "adopted-hold"]   # the new pane, held
        await restart(u)
        assert len(notices(u)) == 1
    run(scenario())


def test_a_failed_start_still_posts_the_notice(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    on_account_b(u)

    def broken(*a: Any, **kw: Any) -> None:
        raise OSError("tmux is broken")
    u.tmux.new_session = broken                             # type: ignore[method-assign]
    run(u.daemon.start_agent(startup=True))
    assert u.daemon.stuck is not None
    assert notices(u) == [("login-changed:1", LOGIN_CHANGED_NOTICE)]


# --- process environments ---

ENV_LOGGER = """#!/bin/sh
{{
  printf 'dir=%s\\n' "${{CLAUDE_CONFIG_DIR-<unset>}}"
  for v in {names}; do eval "test -n \\"\\${{$v+x}}\\"" && printf 'has=%s\\n' "$v"; done
}} > "{log}"
exec sleep 600
"""


def env_logger(tmp_path: Path, name: str) -> tuple[Path, Path]:
    log = tmp_path / f"{name}.env"
    script = tmp_path / name
    script.write_text(ENV_LOGGER.format(names=" ".join(OVERRIDES), log=log))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, log


def logged(log: Path) -> tuple[str, set[str]]:
    deadline = time.monotonic() + 10
    while not log.exists() or not log.read_text().endswith("\n") or "dir=" not in log.read_text():
        if time.monotonic() > deadline:
            raise AssertionError("the fake claude did not log its environment")
        time.sleep(0.05)
    time.sleep(0.2)                                         # the `has=` lines follow the `dir=` line
    lines = log.read_text().splitlines()
    return lines[0].removeprefix("dir="), {line.removeprefix("has=") for line in lines[1:]}


@needs_tmux
@pytest.mark.parametrize("account", [None, ACCOUNT_B])
def test_a_stale_tmux_server_cannot_choose_the_panes_login(tmp_path: Path,
                                                           account: AdmindAccount | None) -> None:
    tmux = new_test_tmux()
    try:
        tmux.new_session("keeper", tmp_path, ["sleep", "600"])         # the server outlives admind
        assert tmux.socket_path is not None
        guarded_tmux(tmux.socket_path, "set-environment", "-g", "CLAUDE_CONFIG_DIR", "/stale")
        guarded_tmux(tmux.socket_path, "set-environment", "-g", "CLAUDE_CODE_OAUTH_TOKEN", "x")
        binary, log = env_logger(tmp_path, "claude")
        overrides: dict[str, Any] = {"adapter_binary": str(binary)}
        if account is not None:
            overrides["agent_account"] = account
        s = make_settings(tmp_path, **overrides)
        agent = AdminAgent(tmux, Store(s.state_dir / "admind.db"), s, s.state_dir / "hook.sock")
        assert agent.ensure_running() == "launched"
        directory, present = logged(log)
        if account is None:
            assert directory == "<unset>" and present == {"CLAUDE_CODE_OAUTH_TOKEN"}  # today's token stays
        else:
            assert directory == "/accounts/b" and present == set()
    finally:
        tmux.kill_server()
        assert tmux.socket_path is not None
        tmux.socket_path.unlink(missing_ok=True)


SUMMARY_ENV = """#!/bin/sh
cat > /dev/null
printf '%s' "${{CLAUDE_CONFIG_DIR-<unset>}}" > "{seen}"
for v in CLAUDE_CONFIG_DIR {names}; do eval "test -n \\"\\${{$v+x}}\\"" && echo "$v"; done
exit 0
"""


@needs_tmux
def test_agent_and_summarizer_each_see_only_their_own_login(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    account_c = AdmindAccount("c", "ck1-" + "c" * 32, {"CLAUDE_CONFIG_DIR": str(tmp_path / "c")}, OVERRIDES)
    seen = tmp_path / "summarizer.dir"
    script = tmp_path / "summ.sh"
    script.write_text(SUMMARY_ENV.format(seen=seen, names=" ".join(OVERRIDES)))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    out = asyncio.run(summarize.summarize([str(script)], tmp_path / "w", "reply", timeout=10,
                                          account=account_c))
    assert out.splitlines()[0] == "CLAUDE_CONFIG_DIR" and out.count("\n") == 2     # names only; no override
    tmux = new_test_tmux()
    try:
        binary, log = env_logger(tmp_path, "claude")
        s = make_settings(tmp_path, adapter_binary=str(binary), agent_account=ACCOUNT_B)
        AdminAgent(tmux, Store(s.state_dir / "admind.db"), s, s.state_dir / "hook.sock").ensure_running()
        agent_dir, present = logged(log)
    finally:
        tmux.kill_server()
        assert tmux.socket_path is not None
        tmux.socket_path.unlink(missing_ok=True)
    assert present == set()
    assert agent_dir == ACCOUNT_B.set["CLAUDE_CONFIG_DIR"] and seen.read_text() == str(tmp_path / "c")
    assert agent_dir != seen.read_text()


def test_without_an_account_the_summarizer_inherits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    assert summarize.child_env(None) is None
    default = AdmindAccount("default", "ck1-" + "0" * 32, {}, ("CLAUDE_CONFIG_DIR",))
    env = summarize.child_env(default)
    assert env is not None and "CLAUDE_CODE_OAUTH_TOKEN" in env and "CLAUDE_CONFIG_DIR" not in env
    assert env == {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}


# --- redaction of configured login directories (D10, qualified) ---


def test_every_literal_form_is_redacted_and_names_keep_their_boundary() -> None:
    set_login_dirs(["~/.claude-b", "/srv/u/.claude-b", "/data/real-b"])
    assert redact("a ~/.claude-b/.credentials.json b") == f"a {LOGIN_DIR}/.credentials.json b"
    assert redact("x=/srv/u/.claude-b, y=/data/real-b.") == f"x={LOGIN_DIR}, y={LOGIN_DIR}."
    assert redact("/srv/u/.claude-bb and ~/.claude-b_old and /data/real-b2 and /data/real-b.bak") == \
        "/srv/u/.claude-bb and ~/.claude-b_old and /data/real-b2 and /data/real-b.bak"
    assert redact("in /data/real-b... and /data/real-b.") == f"in {LOGIN_DIR}... and {LOGIN_DIR}."
    assert redact("~/.claude and /srv/u/.claude") == "~/.claude and /srv/u/.claude"   # default: visible
    once = redact("see /srv/u/.claude-b")
    assert redact(once) == once
    assert redact_module.redact_continuation("see /srv/u/.cla", "ude-b now") == "<redacted fragment> now"
    set_login_dirs(())
    assert redact("/srv/u/.claude-b") == "/srv/u/.claude-b"


def linked_forms(tmp_path: Path) -> list[str]:
    """Account b's three forms: configured, `~`-expanded (a symlink) and canonical."""
    home = tmp_path / "home"
    (tmp_path / "real-b").mkdir()
    home.mkdir()
    (home / ".claude-b").symlink_to(tmp_path / "real-b")
    forms = ["~/.claude-b", str(home / ".claude-b"), str(tmp_path / "real-b")]
    set_login_dirs(forms)
    return forms


def guarded_summarizer(forms: list[str]) -> Any:
    """A summarizer that fails if its input holds any form (so an unredacted input ends in the backstop),
    and whose own output names the canonical form (which admind must redact)."""
    def before(h: Harness) -> None:
        configure()(h)
        listed = h.settings.workdir / "forms.txt"
        listed.write_text("".join(f"{f}\n" for f in forms))
        script = h.settings.workdir / "guarded-summ.sh"
        script.write_text(f'#!/bin/sh\nif grep -qF -f "{listed}"; then exit 3; fi\n'
                          f'echo "summary of {forms[2]}"\n')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        h.daemon.summarizer_argv = [str(script)]
    return before


def leaks(texts: list[str], forms: list[str]) -> list[str]:
    return [t for t in texts if any(f in t for f in forms)]


def all_forms(forms: list[str]) -> str:
    return " | ".join(forms)


@needs_tmux
def test_login_dirs_never_leave_through_replies_tail_details_summaries_or_audit(tmp_path: Path) -> None:
    forms = linked_forms(tmp_path)

    async def scenario(h: Harness) -> None:
        mid = await h.say(f"short {all_forms(forms)}")                       # verbatim reply
        await waited(h, lambda: any(t.startswith("echo: short") for t in h.texts()), "the verbatim reply")
        sid = await session(h)
        path = tmp_path / f"{sid}.jsonl"
        append(path, [prompt("go")])
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, str(path), prompt="go"))
        await waited(h, lambda: h.store.get("turn_start") is not None, "the turn to start")
        append(path, [tool("Bash", command=f"ls {all_forms(forms)}"), result(f"in {forms[1]}")])
        await h.daemon.hooks.put(h.event("Stop", sid, str(path), f"{LONG} {all_forms(forms)}"))
        await waited(h, lambda: any(t.endswith(summarize.FOOTER) for t in h.texts()), "the summary")
        summary = next(t for t in h.texts() if t.endswith(summarize.FOOTER))
        assert summary.startswith(f"summary of {LOGIN_DIR}")                # its own output, redacted
        ordinary = await details_of(h)
        assert LONG in ordinary and LOGIN_DIR in ordinary
        h.seq += 1
        full_mid = f"{h.seq:064x}"
        await h.fake.push_event(h.fake.message_event("!details full", h.settings.operators[0].hex, full_mid))
        await waited(h, lambda: h.store.db.execute(
            "SELECT COUNT(*) FROM outbox WHERE key LIKE ? AND status = 'sent'",
            (f"details:{full_mid}:%",)).fetchone()[0] > 0, "!details full")
        await asyncio.sleep(0.5)
        full = "".join(r[0] for r in h.store.db.execute(
            "SELECT text FROM outbox WHERE key LIKE ? ORDER BY seq", (f"details:{full_mid}:%",)))
        assert "▸ Bash" in full and LOGIN_DIR in full
        screen = f"$ ls {all_forms(forms)}"
        h.tmux.capture = lambda name, lines: screen          # type: ignore[method-assign]
        await h.say("!tail 50")
        await waited(h, lambda: any(t.startswith("`") and "$ ls" in t and LOGIN_DIR in t for t in h.texts()),
                     "the !tail output")
        h.audit.write("probe", field=forms[0], other=f"at {forms[2]}/x")
        assert mid and leaks(h.texts(), forms) == []
        assert leaks([json.dumps(r) for r in audit_records(h)], forms) == []
        assert any(r.get("field") == LOGIN_DIR for r in audit_records(h))

    run_with(tmp_path, scenario, guarded_summarizer(forms), {"chunk_chars": 4000})


@needs_tmux
def test_login_dirs_never_leave_through_the_backstop(tmp_path: Path) -> None:
    forms = linked_forms(tmp_path)

    async def scenario(h: Harness) -> None:
        await h.say(f"{LONG} {all_forms(forms)}")
        text = await batch_arrives(h)
        assert LONG in text and LOGIN_DIR in text
        row = h.store.db.execute("SELECT message_id FROM outbox WHERE key LIKE 'batch:%'").fetchone()
        ordinary = await details_of(h, str(row[0]))
        assert LONG in ordinary and LOGIN_DIR in ordinary
        assert leaks(h.texts(), forms) == []
        assert leaks([json.dumps(r) for r in audit_records(h)], forms) == []

    run_with(tmp_path, scenario, configure("exit 1", batch=0.3, timeout=1.0))


def test_the_summarizer_sees_only_redacted_input(tmp_path: Path) -> None:
    """Directly: an unredacted input would make the guarded summarizer fail."""
    forms = linked_forms(tmp_path)
    listed = tmp_path / "forms.txt"
    listed.write_text("".join(f"{f}\n" for f in forms))
    script = tmp_path / "summ.sh"
    script.write_text(f'#!/bin/sh\nif grep -qF -f "{listed}"; then exit 3; fi\necho ok\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    out = asyncio.run(summarize.summarize([str(script)], tmp_path / "w", all_forms(forms), timeout=10))
    assert out.startswith("ok")
    set_login_dirs(())
    with pytest.raises(summarize.SummaryFailed):            # the guard itself works
        asyncio.run(summarize.summarize([str(script)], tmp_path / "w", all_forms(forms), timeout=10))


def test_cli_installs_the_login_dirs_before_anything_runs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = make_settings(tmp_path, login_dirs=("/x/acct",))
    monkeypatch.setattr(cli, "resolve", lambda cfg, env: s)
    monkeypatch.setattr(cli.hconfig, "load", lambda: None)
    assert cli._settings_only() is s
    assert redact("/x/acct") == LOGIN_DIR

