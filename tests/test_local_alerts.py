import hashlib
import json
from typing import Literal

from heterodyne.local_alerts import Intent, publish, settle


def intent(episode: str = "failure", kind: Literal["failure", "recovery", "retirement"] = "failure"):
    return Intent("p8", "a" * 32, 1, episode, kind, "2026-10-18T00:00:00Z", "Fixed local diagnostic.")


def test_exact_identity_encoding_and_restart_idempotence(tmp_path):
    i = intent()
    expected = hashlib.sha256(
        json.dumps([1, "p8", "a" * 32, 1, "failure"], separators=(",", ":")).encode()
    ).hexdigest()
    assert i.filename() == "p8_" + expected[:32] + ".json"
    assert publish(tmp_path, i) == "written"
    assert publish(tmp_path, i) == "existing"
    payload = (tmp_path / i.filename()).read_bytes()
    assert payload == i.payload() and not payload.endswith(b"\n")
    assert not settle(tmp_path, i, hashlib.sha256(payload).hexdigest(), "published")
    assert settle(tmp_path, i, hashlib.sha256(payload).hexdigest(), "cleared")
    assert publish(tmp_path, intent("next")) == "written"
    assert (tmp_path / i.filename()).exists()  # retain settled bytes until capacity is needed


def test_capacity_reserves_overflow_and_never_deletes_foreign(tmp_path):
    for number in range(255):
        (tmp_path / f"foreign{number}.json").write_text("{}")
    assert publish(tmp_path, intent()) == "coalesced"
    assert len(list(tmp_path.glob("*.json"))) == 256
    assert publish(tmp_path, intent("next")) == "coalesced"
    assert len(list(tmp_path.glob("*.json"))) == 256


def test_exact_payload_conflict_holds_and_recovery_settles_after_publish(tmp_path):
    i = intent("recovery", "recovery")
    assert publish(tmp_path, i) == "written"
    sha = hashlib.sha256(i.payload()).hexdigest()
    assert settle(tmp_path, i, sha, "published")
    changed = Intent(i.namespace, i.owner_store_uuid, 1, i.episode_id, i.kind, i.created_at, "Other text.")
    assert publish(tmp_path, changed) == "held"


def test_sensitive_text_never_enters_shared_alert_file(tmp_path):
    original = intent()
    secret = Intent(
        original.namespace, original.owner_store_uuid, 1, original.episode_id,
        original.kind, original.created_at, "nsec1" + "q" * 58,
    )
    assert publish(tmp_path, secret) == "held"
    assert not list(tmp_path.glob("*.json"))
