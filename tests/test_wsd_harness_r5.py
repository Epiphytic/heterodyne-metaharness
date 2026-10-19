"""R5 safety regressions; private stores, fake queue and temporary Git only."""

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import World
from fakes.fake_runtime import FakeRuntime
from test_wsd_harness import (
    H,
    approve,
    assembled,
    posted,
    published,
    row,
    settings_for,
)
from test_wsd_harness import (
    ask_dir as ask_dir,
)
from test_wsd_harness import (
    h as h,
)

from heterodyne.harness import verify
from heterodyne.sandbox import build as sb
from heterodyne.wsd import ctl, daemon
from heterodyne.wsd.harness import BAD_VERSION, HarnessRefused
from heterodyne.wsd.journal import Journal


def test_async_startup_recovers_harness_before_any_workstream(tmp_path: Path, monkeypatch: Any) -> None:
    order = []

    class Hooks:
        def recover(self):
            order.append("harness")

        def advance(self, now):
            order.append("advance")

    s = settings_for(tmp_path)
    j = Journal(s.journal)
    d = assembled(s, j, FakeRuntime(), World(tmp_path / "queue"), Hooks())
    monkeypatch.setattr(d, "recover_one", lambda name: order.append("recover"))
    monkeypatch.setattr(d, "pickup_one", lambda name, trigger: order.append("pickup"))
    try:
        asyncio.run(d._startup())
        assert order[0] == "harness" and order.index("harness") < order.index("pickup")
    finally:
        assert d.drain_now(5)
        j.close()


def test_harness_dispatch_refuses_after_stopping(h: H) -> None:
    s = settings_for(h.tmp)
    d = assembled(s, h.journal, h.runtime, h.world, h.ops)
    d.drain_now(1)
    req = ctl.CtlRequest("harness", harness=ctl.HarnessRequest("list"))
    with pytest.raises(daemon.Stopping):
        asyncio.run(d._dispatch(req))


@pytest.mark.parametrize("version", ["1/../../other", "1?x", "1#x", "", "a" * 129])
def test_bad_release_version_refused_before_fetch(h: H, monkeypatch: Any, version: str) -> None:
    calls = []
    monkeypatch.setattr(h.ops, "_fetch", lambda *a: calls.append(a))
    with pytest.raises(HarnessRefused):
        h.ops.install("codex", version)
    assert not calls and h.journal.harness_installs() == []


def test_version_mismatch_never_creates_approval(h: H) -> None:
    h.opener.files.update(
        {key.replace("0.160.0", "0.161.0"): value for key, value in list(h.opener.files.items())}
    )
    with pytest.raises(HarnessRefused) as caught:
        h.ops.install("codex", "0.161.0")
    assert caught.value.constant == BAD_VERSION
    assert h.journal.harness_installs() == []


def test_stale_taint_does_not_recreate_alert(h: H) -> None:
    _, install = published(h)
    verify.taint(h.taint, "codex", install, verify.INSTALL_CHANGED)
    h.ops.tainted("codex", install)
    h.ops.verify("codex", install)
    h.ops.tainted("codex", install)
    assert not list(h.alerts.glob("*.json"))


def test_publish_wrong_name_keeps_approval_open(h: H, monkeypatch: Any) -> None:
    sid, r = posted(h)
    approve(h, r.approval_bead)
    real = h.store.publish

    def wrong(*args):
        return real(*args) + "-wrong"

    monkeypatch.setattr(h.store, "publish", wrong)
    h.ops.advance(h.clock())
    assert row(h, sid).state == "approved"


def test_harness_fault_is_logged_with_no_exception_text(h: H, caplog: Any, monkeypatch: Any) -> None:
    d = assembled(settings_for(h.tmp), h.journal, h.runtime, h.world, h.ops)

    def fail(*args):
        raise sqlite3.OperationalError("private-value")

    monkeypatch.setattr(h.ops, "list", fail)
    reply = d.harness_one(ctl.HarnessRequest("list"))
    assert reply.result == "failed" and "OperationalError" in caplog.text
    assert "private-value" not in caplog.text


def test_ancestor_git_repository_is_not_code_provenance(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    nested = tmp_path / "venv/lib/python3.12"
    nested.mkdir(parents=True)
    (tmp_path / "unrelated").write_text("unrelated")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "unrelated",
        ],
        check=True,
    )
    assert sb._commit(nested) == ""


def test_harness_request_lane_accounts_for_running_work(h: H, monkeypatch: Any) -> None:
    entered, release = threading.Event(), threading.Event()
    d = assembled(settings_for(h.tmp), h.journal, h.runtime, h.world, h.ops)

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return daemon.CtlReply("ok", "done")

    monkeypatch.setattr(d, "harness_one", blocked)

    async def exercise():
        task = asyncio.create_task(d._dispatch(ctl.CtlRequest("harness", harness=ctl.HarnessRequest("list"))))
        try:
            for _ in range(500):
                if entered.is_set():
                    break
                await asyncio.sleep(0.002)
            assert entered.is_set()
            assert not d.drain_now(0.01)  # Included in the same bounded drain as every native worker.
        finally:
            release.set()
            await task

    asyncio.run(exercise())
    assert d.drain_now(1)


def test_actual_serve_recovers_before_pickup(tmp_path: Path, monkeypatch: Any) -> None:
    s = settings_for(tmp_path)
    j = Journal(s.journal)
    order = []

    class Hooks:
        def recover(self):
            order.append("harness")

        def advance(self, now):
            order.append("advance")

    d = assembled(s, j, FakeRuntime(), World(tmp_path / "queue"), Hooks())

    async def exercise():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def stopped_pickup(*args):
            order.append("pickup")
            loop.call_soon_threadsafe(stop.set)

        monkeypatch.setattr(d, "recover_one", lambda name: order.append("recover"))
        monkeypatch.setattr(d, "pickup_one", stopped_pickup)
        await d.serve(stop)

    try:
        asyncio.run(exercise())
        assert order[0] == "harness" and order.index("recover") < order.index("pickup")
    finally:
        d.drain_now(5)
        j.close()


def test_cli_data_does_not_echo_upstream_version(h: H) -> None:
    from test_wsd_harness import GHP, published_ghp

    published_ghp(h)
    d = assembled(settings_for(h.tmp), h.journal, h.runtime, h.world, h.ops)
    for request in (ctl.HarnessRequest("list"), ctl.HarnessRequest("prune")):
        reply = d.harness_one(request)
        assert reply.result == "ok" and reply.data
        assert GHP not in json.dumps(reply.data)


def test_journal_row_and_event_roll_back_together(h: H, monkeypatch: Any) -> None:
    def failed(*args):
        h.journal.db.execute("INSERT INTO nonexistent_fixture_table VALUES (1)")

    monkeypatch.setattr(h.journal, "harness_event", failed)
    with pytest.raises(sqlite3.OperationalError):
        h.ops.install("codex", None)
    assert h.journal.harness_installs() == [] and h.world.created == []
    # Store staging is a separate durability domain; it stays unapproved until its existing TTL sweep.
    assert list((h.store.root / ".staged").glob("*.json"))


def test_failed_preflight_does_not_stage(h: H, monkeypatch: Any) -> None:
    from contextlib import contextmanager

    @contextmanager
    def busy():
        raise sqlite3.OperationalError("fixture busy")
        yield

    monkeypatch.setattr(h.journal, "transaction", busy)
    with pytest.raises(sqlite3.OperationalError):
        h.ops.install("codex", None)
    assert not list((h.store.root / ".staged").glob("*.json"))


def test_wrong_publish_receipt_remains_unselectable_and_unprunable(h: H, monkeypatch: Any) -> None:
    sid, r = posted(h)
    approve(h, r.approval_bead)
    real = h.store.publish

    def wrong(*args):
        return real(*args) + "-wrong"

    monkeypatch.setattr(h.store, "publish", wrong)
    h.ops.advance(h.clock())
    [info] = h.store.list()
    assert row(h, sid).state == "approved" and h.ops.in_use(info.adapter, info.name)
    with pytest.raises(HarnessRefused):
        h.ops.use(info.adapter, info.name)
    assert h.ops.prune(None).installs == []
    monkeypatch.setattr(h.store, "publish", real)
    h.ops.recover()  # An exact subsequent outcome settles the original approval without a new publish.
    assert row(h, sid).state == "published"


def test_source_provenance_requires_clean_exact_package(tmp_path: Path, monkeypatch: Any) -> None:
    import subprocess

    package = tmp_path / "src/heterodyne/__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text('"fixture package"\n')

    def git(*args):
        return (
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
            .stdout.decode()
            .strip()
        )

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    monkeypatch.setattr(sb.heterodyne, "__file__", str(package))
    expected = git("rev-parse", "HEAD")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong"))
    assert sb._commit(tmp_path) == expected
    package.write_text('"changed running code"\n')
    assert sb._commit(tmp_path) == ""


def test_approval_worker_remains_in_owning_lane(h: H, monkeypatch: Any) -> None:
    entered, release = threading.Event(), threading.Event()

    def ask(*args):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(h.ops, "_ask", ask)
    d = assembled(settings_for(h.tmp), h.journal, h.runtime, h.world, h.ops)

    async def exercise():
        task = asyncio.create_task(
            d._dispatch(ctl.CtlRequest("harness", harness=ctl.HarnessRequest("install", adapter="codex")))
        )
        try:
            for _ in range(500):
                if entered.is_set():
                    break
                await asyncio.sleep(0.002)
            assert entered.is_set() and not task.done()
            assert not d.drain_now(0.01)
            assert all(not worker.daemon for worker in h.ops.workers)
        finally:
            release.set()
            await task

    asyncio.run(exercise())
    assert d.drain_now(1)
