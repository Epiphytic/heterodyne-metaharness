"""Plan 4c Task 8: what wsd dispatches when the adapter has no selected install, and for an entry journaled
before installs were pinned (schema 2)."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, SimulatedCrash
from wsd_env import FAKE_PINS, WS, Rig, make_rig

from heterodyne.harness.selection import InstallPin
from heterodyne.wsd.beads import NEEDS_HUMAN
from heterodyne.wsd.runtime import INSTALL_UNPINNED, NOT_SELECTED, ExecDirCleared
from heterodyne.wsd.states import Reason


def started(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.ws = replace(rig.ws, models={"p-one": "model-x"})
    rig.restart()
    rig.world.add("btq-1")
    return rig


def pins_of(rig: Rig) -> list[tuple[int, str, str]]:
    return [(e.generation, e.install, e.install_digest) for e in rig.journal.launches_of(WS, "btq-1")]


def test_no_selection_escalates_config_invalid_and_journals_nothing(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.selections = {"codex": FAKE_PINS["codex"]}.get       # another adapter's selection is not this one's
    rig.restart()
    rig.pickup()
    assert rig.runtime.calls == 0 and pins_of(rig) == []
    assert rig.beads.show(WS, "btq-1").launches().entries == ()
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (row.reason, row.detail) == (Reason.CONFIG_INVALID, NOT_SELECTED)
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_an_entry_from_schema_2_dispatches_as_install_unpinned(tmp_path: Path) -> None:
    """A schema 2 journal upgrades with every entry's pin empty: its replay is dispatched as journaled,
    the runtime refuses it (`INSTALL_UNPINNED`) as a failed launch, and the next generation pins the
    current selection."""
    rig = started(tmp_path)
    rig.restart(CrashAt("pickup.entry"))           # generation 1 journaled, its bead copy not yet written
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.journal.close()
    db = sqlite3.connect(rig.root / "state" / "wsd.db")
    with db:
        db.execute("UPDATE launches SET install = '', install_digest = ''")     # as the v3 upgrade leaves it
    db.close()
    rig.restart()
    assert pins_of(rig) == [(1, "", "")]
    rig.pickup()
    [refused] = rig.runtime.attempts
    assert (refused.generation, refused.install, refused.install_digest) == (1, "", "")
    receipt = rig.journal.receipt(*rig.journal.launches_of(WS, "btq-1")[0].ident)
    assert receipt is not None and (receipt.kind, receipt.error) == ("refused", INSTALL_UNPINNED)
    rig.pickup()                    # the op's replay: the failed generation is forgotten, the next one pins
    pin: InstallPin = FAKE_PINS["claude-code"]
    assert pins_of(rig) == [(1, "", ""), (2, pin.install, pin.digest)]
    [launched] = rig.runtime.launches
    assert (launched.generation, launched.install, launched.install_digest) == (2, pin.install, pin.digest)
    assert rig.state("btq-1") == "running"


def test_an_exec_dir_cleared_notice_is_journaled_once(tmp_path: Path) -> None:
    """Codex T8 r1 B2 (Amendment 1 §4.2): the `exec_dir_cleared` journal entry carries the session, the
    generation, the adapter and the bounded summary; a repeated notice journals nothing more."""
    rig = started(tmp_path)
    rig.pickup()
    [launched] = rig.runtime.launches
    notice = ExecDirCleared(launched.session_key, launched.generation, "codex", ".codex/packages", files=1,
                            links=0, dirs=0, bytes=1, names=("leftover",))
    rig.runtime.report(WS, notice, notice)
    rig.pickup()
    rig.runtime.report(WS, notice)
    rig.pickup()
    [event] = [e for e in rig.journal.events_since(0) if e.kind == "exec_dir_cleared"]
    assert (event.ws, event.bead) == (WS, "btq-1")
    assert json.loads(event.detail) == {
        "session_key": launched.session_key, "generation": launched.generation, "adapter": "codex",
        "path": ".codex/packages", "files": 1, "links": 0, "dirs": 0, "bytes": 1, "names": ["leftover"]}
