"""7hokw approved ledger/reaper cases 1–7; clocks and backend observations are offline."""

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from heterodyne.admind import alerts
from heterodyne.config.secret_scan import sensitive_kind
from heterodyne.sandbox import creates, reaper

BOX = "hz0123456789abg3"


@pytest.fixture
def ledger(tmp_path: Path) -> creates.Ledger:
    book = creates.Ledger(tmp_path / "creates", tmp_path / "alerts")
    assert book.put(BOX, "", key="session", ws="alpha", bead="btq-one", generation=3,
                    deadline=100, pane="agent", at=0)
    return book


@pytest.mark.parametrize("bad", [None, "other", "observed", "malformed", "list", "link", "fifo",
                                 "large", "how-list"])
def test_only_valid_settlement_evidence_settles(ledger: creates.Ledger, bad: str | None) -> None:
    path = ledger.directory / f"{BOX}.settled.json"
    if bad == "link":
        target = ledger.directory / "target"
        target.write_text(json.dumps({"sandbox": BOX, "how": "returned"}))
        path.symlink_to(target)
    elif bad == "fifo":
        os.mkfifo(path)
    elif bad is not None:
        value: object = {"sandbox": BOX, "how": bad}
        if bad == "other":
            value = {"sandbox": "hz000000000000g1", "how": "returned"}
        if bad == "list":
            value = []
        if bad == "how-list":
            value = {"sandbox": BOX, "how": []}
        path.write_text("{" if bad == "malformed" else "x" * 4097 if bad == "large" else json.dumps(value))
    assert ledger.put(BOX, "gone", by="sweep", at=1)
    assert ledger.observe(BOX, "sweep", 1)
    assert not creates.settled(ledger.directory, BOX)
    assert not creates.gone(ledger.directory, BOX)


@pytest.mark.parametrize("how", sorted(creates.KINDS))
def test_settlement_needs_no_submission_but_gone_needs_settlement(ledger: creates.Ledger, how: str) -> None:
    (ledger.directory / f"{BOX}.json").unlink()
    assert ledger.put(BOX, "settled", how=how, at=1)
    assert creates.settled(ledger.directory, BOX)
    assert creates.gone(ledger.directory, BOX) == (how == "never_submitted")
    assert ledger.put(BOX, "gone", by="watcher", at=2)
    assert creates.gone(ledger.directory, BOX)


def test_evidence_is_private_durable_and_first_writer_wins(ledger: creates.Ledger) -> None:
    assert ledger.directory.stat().st_mode & 0o777 == 0o700
    assert ledger.put(BOX, "settled", how="returned", at=1)
    before = (ledger.directory / f"{BOX}.settled.json").read_bytes()
    assert ledger.put(BOX, "settled", how="operator", at=2)
    assert (ledger.directory / f"{BOX}.settled.json").read_bytes() == before
    assert all(p.stat().st_mode & 0o077 == 0 for p in ledger.directory.iterdir())
    assert not any(p.name.endswith(".tmp") for p in ledger.directory.iterdir())


class LoopStopped(BaseException):
    pass


def drive(ledger: creates.Ledger, *, passes: int, step: Callable[[int], None] = lambda _: None,
          delete: Callable[[str], bool] = lambda _: True, listed: Callable[[], set[str]] = set) -> list[str]:
    now, events, count = [float(100 + 3 * reaper.LINGER_SECONDS)], list[str](), [0]

    def sleep(seconds: float) -> None:
        now[0] += seconds
        count[0] += 1
        step(count[0])
        if count[0] >= passes:
            raise LoopStopped

    reaper.reap(BOX, 100, delete, lambda _: events.append("kill"), clock=lambda: now[0], sleep=sleep,
                settled=lambda: creates.settled(ledger.directory, BOX), ledger=ledger, listed=listed)
    return events


def test_an_unsettled_watcher_never_exits_and_escalates_once(ledger: creates.Ledger) -> None:
    deleted: list[str] = []
    before: list[bytes] = []

    def step(_: int) -> None:
        data = (ledger.alerts / f"create-{BOX}.json").read_bytes()
        before.append(data)

    with pytest.raises(LoopStopped):
        drive(ledger, passes=5, step=step, delete=lambda name: deleted.append(name) or True)
    assert deleted == [BOX] * 5 and len(set(before)) == 1
    assert not creates.gone(ledger.directory, BOX)


def test_a_late_create_is_observed_before_delete_and_only_operator_settles(ledger: creates.Ledger) -> None:
    listed: set[str] = set()
    deletes: list[bool] = []

    def step(n: int) -> None:
        if n == 1:
            listed.add(BOX)
        if n == 2:
            assert not creates.settled(ledger.directory, BOX)
            assert ledger.put(BOX, "settled", how="operator", by="wsctl", at=200)

    def delete(_: str) -> bool:
        if BOX in listed:
            deletes.append(creates.read(ledger.directory / f"{BOX}.observed.json", BOX) is not None)
        listed.clear()
        return True

    drive(ledger, passes=4, step=step, delete=delete, listed=lambda: listed)
    assert deletes == [True] and creates.gone(ledger.directory, BOX)
    assert (ledger.alerts / f"create-{BOX}-settled.json").is_file()


@pytest.mark.parametrize("observed", [False, True])
def test_alert_contract_and_sensitivity(ledger: creates.Ledger, observed: bool) -> None:
    if observed:
        ledger.observe(BOX, "watcher", 123)
    assert ledger.alert(BOX, 200)
    ledger.put(BOX, "settled", how="operator", at=201)
    assert ledger.alert(BOX, 202, close=True)
    scanned = alerts.scan(ledger.alerts)
    assert len(scanned) == 2
    for name, alert in scanned:
        assert alert is not None
        assert sensitive_kind(alert.text) is None
        assert "withheld" not in alerts.render(name, alert, 10000)
    last = scanned[-1][1]
    assert last is not None
    assert ("observed at least once" if observed else "never observed") in last.text


def test_failed_alert_write_retries_and_never_ends_watch(ledger: creates.Ledger,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    real, attempts = creates.write_once, []

    def write(path: Path, body: bytes) -> bool:
        if path.parent == ledger.alerts:
            attempts.append(path)
            if len(attempts) < 3:
                return False
        return real(path, body)

    monkeypatch.setattr(creates, "write_once", write)
    with pytest.raises(LoopStopped):
        drive(ledger, passes=4)
    assert len(attempts) == 4 and attempts[0].is_file()


def test_failed_observed_write_still_deletes(ledger: creates.Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    real, deletes = ledger.put, []
    monkeypatch.setattr(ledger, "put", lambda name, kind, **kw: False if kind == "observed" else
                        real(name, kind, **kw))
    with pytest.raises(LoopStopped):
        drive(ledger, passes=2, listed=lambda: {BOX}, delete=lambda n: deletes.append(n) or True)
    assert deletes == [BOX, BOX] and not creates.settled(ledger.directory, BOX)


def test_failed_gone_write_keeps_watcher(ledger: creates.Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger.put(BOX, "settled", how="returned", at=1)
    real, attempts = ledger.put, []

    def put(name: str, kind: str, **kw: object) -> bool:
        if kind == "gone":
            attempts.append(kind)
            if len(attempts) < 3:
                return False
        return real(name, kind, **kw)

    monkeypatch.setattr(ledger, "put", put)
    drive(ledger, passes=4)
    assert len(attempts) == 3 and creates.gone(ledger.directory, BOX)


def test_settlement_must_precede_confirmed_delete(ledger: creates.Ledger) -> None:
    calls: list[str] = []

    def delete(_: str) -> bool:
        calls.append("delete")
        ledger.put(BOX, "settled", how="operator", at=1)
        return True

    drive(ledger, passes=3, delete=delete)
    assert len(calls) == 2   # first deletion happened while unsettled; it cannot justify gone


def test_ready_is_written_only_after_valid_args_and_open_ledger(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "creates"
    ready = directory / f".ready-{BOX}-nonce"
    argv = ["--deadline", "100", "--openshell", "fake", "--podman", "fake", "--image", "fake",
            "--record", str(tmp_path / "session.json"), "--generation", "3", "--marker", str(tmp_path / "m"),
            "--pane", "agent", "--tmux", "tmux", "--tmux-socket", "test", "--ledger", str(directory),
            "--alerts", str(tmp_path / "alerts"), "--ready", str(ready), BOX]
    with pytest.raises(SystemExit):
        reaper.main([*argv, "--invalid"])
    assert not ready.exists()
    directory.write_text("not a directory")
    with pytest.raises(OSError):
        reaper.main(argv)
    assert not ready.exists()
    directory.unlink()
    called: list[bool] = []

    def reap(*_: object, **kw: object) -> None:
        called.append(ready.read_text() == "ready")
        assert "ledger" in kw

    monkeypatch.setattr(reaper, "reap", reap)
    assert reaper.main(argv) == 0 and called == [True]
