"""A complete AdmindSettings for tests, without going through config files."""

import dataclasses
from pathlib import Path
from typing import Any

from heterodyne.admind.settings import AdmindSettings, Operator
from heterodyne.marmot.nip19 import hex_to_npub

OPERATOR_HEX = "c3" * 32
SECOND_HEX = "d4" * 32


def operator(name: str, key: str) -> Operator:
    return Operator(name, hex_to_npub(key), key)


def make_settings(tmp_path: Path, **overrides: Any) -> AdmindSettings:
    base = AdmindSettings(
        profile={"adapter": "claude-code", "model": "m1", "args": []},
        adapter_binary="claude", workdir=tmp_path, restart_units=("fake.service",),
        chunk_chars=4000, alert_poll_seconds=0.1, group_check_seconds=0.5, start_timeout_seconds=5,
        turn_notice_seconds=3, group_name="heterodyne admin", wn_agent="wn-agent",
        marmot_home=tmp_path / "marmot",
        relays=("wss://relay.example.org",), operators=(operator("op", OPERATOR_HEX),),
        state_dir=tmp_path / "state" / "admind",
        alerts_dir=tmp_path / "state" / "alerts", service_manager="systemd")
    return dataclasses.replace(base, **overrides)
