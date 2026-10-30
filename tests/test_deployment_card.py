"""Actual Product renderer and Git proof, with synthetic HTTPS/profile only."""

import hashlib
import json
from pathlib import Path

import pytest
import test_deployment_publication
from test_deployment_step1 import TARGET, H, synthetic_rendering_profile  # noqa: F401

from heterodyne.actions import deployment_authority as authority
from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment_card import _prepare
from heterodyne.harness.digests import parse_display

published = test_deployment_publication.published


def test_exact_noncanonical_operation_and_immutable_outer_card(published):
    canonical, url, archive, commit, _, _, _ = published
    raw = json.dumps(json.loads(canonical), indent=2).encode()
    prepared = _prepare(raw, (TARGET,), H, url, archive, commit)
    assert prepared.operation_original == raw
    assert prepared.operation_original_sha256 == hashlib.sha256(raw).hexdigest()
    assert raw != prepared.operation_canonical == canonical
    assert prepared.packet_sha256 == hashlib.sha256(canonical).hexdigest()
    assert prepared.publication_url == url
    assert prepared.published_original_sha256 == prepared.packet_sha256
    request = json.loads(prepared.request_original)
    assert (request["type"], request["kind"], request["form"], request["recommended"]) == (
        "post", "question", "yes-no", "yes",
    )
    assert prepared.card.startswith("QUESTION: yes/no\n")
    assert "OUTAGE DEPLOYMENT:" in prepared.card
    assert "Outer Packet: " + url in prepared.card
    assert "Inner Release: " in prepared.card
    line = next(
        line for line in prepared.card.splitlines() if line.startswith("Protected deployment SHA256: ")
    )
    assert parse_display(line.removeprefix("Protected deployment SHA256: ")).hex == prepared.packet_sha256
    assert "<redacted" not in prepared.card and "Choices: yes (recommended) · no" in prepared.card


def test_actual_owned_endpoint_bound_refuses_before_post(published, monkeypatch):
    raw, url, archive, commit, reads, _, _ = published
    monkeypatch.setattr(authority, "_rendering", lambda: ((), 1000))
    with pytest.raises(InvalidAction, match="single-part|transport-bound"):
        _prepare(raw, (TARGET,), H, url, archive, commit)
    assert reads == []


def test_no_caller_written_card_or_unpublished_packet(published):
    raw, url, archive, commit, _, _, _ = published
    operation = json.loads(raw)
    operation["card"] = "approved"
    with pytest.raises(InvalidAction):
        _prepare(json.dumps(operation).encode(), (TARGET,), H, url, archive, commit)


def test_fixed_native_interop_fixture_matches_actual_renderer_and_git(published):
    raw, url, archive, commit, _, _, _ = published
    path = Path(__file__).parent / "fixtures/deployment-card/whole-packet-v2.json"
    fixture = json.loads(path.read_text())
    original = bytes.fromhex(fixture["prepared"]["operation_original"])
    assert json.loads(original) == json.loads(raw)
    prepared = _prepare(original, (TARGET,), H, url, archive, commit)
    observed = {k: v.hex() if isinstance(v, bytes) else v for k, v in vars(prepared).items()}
    assert observed == fixture["prepared"]


def test_one_owned_rendering_profile_snapshot(published, monkeypatch):
    raw, url, archive, commit, _, _, _ = published
    original = authority._rendering
    calls = []
    def profile():
        calls.append(True)
        return original()
    monkeypatch.setattr(authority, "_rendering", profile)
    _prepare(raw, (TARGET,), H, url, archive, commit)
    assert len(calls) == 1
