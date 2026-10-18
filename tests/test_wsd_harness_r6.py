"""R6 regressions: local fixtures only, no native queue or live transport."""

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import World
from fakes.fake_runtime import FakeRuntime
from test_wsd_harness import H, approve, assembled, posted, published, row, settings_for
from test_wsd_harness import ask_dir as ask_dir
from test_wsd_harness import h as harness_fixture

from heterodyne.harness import verify
from heterodyne.sandbox import build as sb
from heterodyne.wsd import ctl
from heterodyne.wsd.journal import Journal

h = harness_fixture


def test_install_reply_reports_failed_approval(h: H, monkeypatch: Any) -> None:
    monkeypatch.setattr(h.ops, "_post", lambda bead: {"result": "refused"})
    d = assembled(settings_for(h.tmp), h.journal, h.runtime, h.world, h.ops)
    reply = d.harness_one(ctl.HarnessRequest("install", adapter="codex"))
    assert reply.result == "refused"
    assert h.journal.harness_installs()[0].state == "failed"


def test_recovery_isolates_non_refusal_per_row(h: H, monkeypatch: Any, caplog: Any) -> None:
    sid, r = posted(h)
    approve(h, r.approval_bead)
    monkeypatch.setattr(h.ops, "_settle", lambda r: None)
    h.ops.advance(h.clock())
    assert row(h, sid).state == "approved"

    def broken(r: Any) -> None:
        raise OSError("private-path")

    monkeypatch.setattr(h.ops, "_settle", broken)
    h.ops.recover()
    assert row(h, sid).state == "approved"
    assert "OSError" in caplog.text and "private-path" not in caplog.text


def test_taint_path_does_not_wait_on_approval_lock(h: H) -> None:
    _, name = published(h)
    verify.taint(h.taint, "codex", name, verify.INSTALL_CHANGED)
    finished = threading.Event()

    def signal() -> None:
        assert h.ops.taint_current("codex", name)
        h.ops.tainted("codex", name)
        finished.set()

    with h.ops.lock:
        worker = threading.Thread(target=signal)
        worker.start()
        completed = finished.wait(0.5)
    worker.join(2)
    assert completed


def test_startup_does_not_wait_for_approval_decisions(tmp_path: Path, monkeypatch: Any) -> None:
    class Hooks:
        def recover(self) -> None:
            pass

        def advance(self, now: int) -> None:
            pytest.fail("approval network calls must not precede startup pickups")

    s = settings_for(tmp_path)
    j = Journal(s.journal)
    d = assembled(s, j, FakeRuntime(), World(tmp_path / "queue"), Hooks())
    monkeypatch.setattr(d, "recover_one", lambda name: None)
    monkeypatch.setattr(d, "pickup_one", lambda name, trigger: None)
    try:
        asyncio.run(d._startup())
        d.startup()
    finally:
        assert d.drain_now(5)
        j.close()


def test_list_exposes_usable_privacy_safe_selector(h: H) -> None:
    _, name = published(h)
    d = assembled(settings_for(h.tmp), h.journal, h.runtime, h.world, h.ops)
    listing = d.harness_one(ctl.HarnessRequest("list"))
    [entry] = listing.data.values()
    selector = entry["selector"]
    assert "0.160.0" not in selector
    reply = d.harness_one(ctl.HarnessRequest("verify", adapter="codex", install=selector))
    assert reply.result == "ok"
    assert h.ops.store.state("codex", name) == "published"


def test_ignored_importable_source_is_not_clean_provenance(tmp_path: Path, monkeypatch: Any) -> None:
    import subprocess

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    package = tmp_path / "src/heterodyne/__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text('"fixture"\n')
    (tmp_path / ".gitignore").write_text("*_local.py\n__pycache__/\n")
    git("add", ".")
    git("-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false",
        "commit", "-qm", "fixture")
    monkeypatch.setattr(sb.heterodyne, "__file__", str(package))
    assert sb._commit(tmp_path)
    (package.parent / "override_local.py").write_text("VALUE = 1\n")
    assert sb._commit(tmp_path) == ""


def test_loaded_queue_git_resolution_has_finite_deadline(tmp_path: Path) -> None:
    from heterodyne.wsd import btq

    binary = tmp_path / "bin/btq"
    binary.parent.mkdir()
    binary.write_text("AGENTS = ('wsd',)\nGIT_TIMEOUT = None\nclass Queue: pass\n")
    module = btq.load(tmp_path)
    assert module.GIT_TIMEOUT == 20.0


def test_source_provenance_allows_only_ignored_bytecode(tmp_path: Path, monkeypatch: Any) -> None:
    import subprocess

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    package = tmp_path / "src/heterodyne/__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text('"fixture"\n')
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    git("add", ".")
    git("-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false",
        "commit", "-qm", "fixture")
    monkeypatch.setattr(sb.heterodyne, "__file__", str(package))
    expected = sb._commit(tmp_path)
    cache = package.parent / "__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-312.pyc").write_bytes(b"fixture bytecode")
    assert expected and sb._commit(tmp_path) == expected
    (cache / "override.py").write_text("VALUE = 1\n")
    assert sb._commit(tmp_path) == ""
