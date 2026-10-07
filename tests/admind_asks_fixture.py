"""Shared helpers for the asks relay tests (relay plan Task 2.1): the fake approve-bead, its bead DB and
log, sent message IDs, and a group with two operators. Everything lives under the test's tmp_path."""

import json
import shlex
import stat
import sys
from pathlib import Path
from typing import Any

from fakes.settings import OPERATOR_HEX, SECOND_HEX, operator
from test_admind_daemon import Harness

FAKE_APPROVE_BEAD = Path(__file__).parent / "fakes" / "fake_approve_bead.py"
TWO = {"operators": (operator("a", OPERATOR_HEX), operator("b", SECOND_HEX))}
BOTH = json.dumps(sorted([OPERATOR_HEX, SECOND_HEX]))


def approve_bead_wrapper(tmp_path: Path) -> Path:
    """A `#!/bin/sh` approve-bead that runs the fake against tmp_path's btq.json and btq.log."""
    wrapper = tmp_path / "approve-bead"
    db, log = shlex.quote(str(tmp_path / "btq.json")), shlex.quote(str(tmp_path / "btq.log"))
    wrapper.write_text(f"#!/bin/sh\nFAKE_BTQ_DB={db} FAKE_BTQ_LOG={log} exec {shlex.quote(sys.executable)} "
                       f"{shlex.quote(str(FAKE_APPROVE_BEAD))} \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


def bead_db(tmp_path: Path, **beads: dict[str, Any]) -> Path:
    """Write the fake's beads (pass hyphenated IDs as `**{"btq-ab12c": {...}}`)."""
    db = tmp_path / "btq.json"
    db.write_text(json.dumps(beads))
    return db


def btq_log(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "btq.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def decisions(h: Harness) -> list[list[str]]:
    """The logged approve-bead calls that were decisions: those with `--yes`, wherever it is (r1-12)."""
    return [argv for argv in btq_log(h.settings.workdir) if "--yes" in argv]


def sent_mid(h: Harness, key: str) -> str | None:
    """The message ID the fake returned for the outbox row `key`, once it has been sent."""
    return next((r["_message_id"] for r in h.fake.sent if r.get("idempotency_key") == key), None)


def two_operators(h: Harness) -> None:
    """Both TWO operators confirmed in a three-member group (use with settings_overrides=TWO)."""
    h.fake.member_count = 3
    h.store.set("expected_members", "3")
    h.store.set("group_operators", BOTH)
