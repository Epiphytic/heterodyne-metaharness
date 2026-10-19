"""Disposable custody sources and files; these fixtures never authenticate production."""

import dataclasses
import hashlib
import os
from unittest.mock import Mock

import pytest
from test_admind_gateway_custody_decisions import answer, ready

from heterodyne.admind import gateway_custody as custody

pytest_plugins = ("test_admind_gateway_custody_registration",)


@pytest.fixture
def issued_rig(rig, tmp_path):
    store, key, service, authority = ready(rig)
    answer(service, key)
    root = tmp_path / "product"
    queue = tmp_path / "queue"
    root.mkdir(mode=0o700)
    queue.mkdir(mode=0o700)
    obj = rig[1]
    enrollment = custody.Publication(
        root, queue, obj.enrollment_sha256, obj.source_closure, obj.profile_sha256
    )
    service.publication = Mock(return_value=enrollment)
    return store, key, service, authority, enrollment, rig


def issue(service, key, certificate=None):
    return service.issue_permit(custody.GatewayCustodyPermit(key, certificate), uid=os.getuid())


def test_first_permit_durable_publication_and_exact_repair(issued_rig):
    store, key, service, authority, enrollment, rig = issued_rig
    first = issue(service, key)
    assert custody.permit(first).permit_generation == 1
    assert first == issue(service, key)
    assert (enrollment.root / "permits" / f"{key}.json").read_bytes() == first
    path = enrollment.root / "generations" / key / "1.json"
    assert path.read_bytes() == first and path.stat().st_mode & 0o777 == 0o600
    assert len(service.get(key, uid=os.getuid())["bundle"]["permits"]) == 1


def certificate(issued_rig, previous):
    _, key, service, _, enrollment, rig = issued_rig
    obj = rig[1]
    prior = custody.permit(previous)
    proof = custody.Nonconsumption(
        "gateway-custody-nonconsumption.v1",
        key,
        hashlib.sha256(previous).hexdigest(),
        prior.permit_generation,
        key,
        obj.chain_root,
        obj.boot_id,
        str(int(prior.issued_boottime_ns) + 1),
    )
    raw = custody.canonical(proof)
    for suffix in ("nonconsumption", "superseded"):
        p = enrollment.queue_root / f"{key}.{suffix}-{prior.permit_generation}.json"
        p.write_bytes(raw)
        p.chmod(0o600)
    observed = dataclasses.replace(rig[2], boottime_ns=int(proof.observed_boottime_ns) + 1)
    service.source.return_value = observed
    return hashlib.sha256(raw).hexdigest()


def test_three_generations_are_linked_and_original_expiry_retained(issued_rig):
    _, key, service, _, enrollment, rig = issued_rig
    old = issue(service, key)
    for generation in (2, 3):
        proof = certificate(issued_rig, old)
        new = issue(service, key, proof)
        record = custody.permit(new)
        assert record.permit_generation == generation
        assert record.previous_permit_sha256 == hashlib.sha256(old).hexdigest()
        old = new
    proof = certificate(issued_rig, old)
    with pytest.raises(custody.Invalid, match="generation"):
        issue(service, key, proof)
    assert len(service.get(key, uid=os.getuid())["bundle"]["permits"]) == 3


@pytest.mark.parametrize("suffix", ["consumed", "issuing", "receipt"])
def test_consumption_or_unknown_never_renews(issued_rig, suffix):
    _, key, service, _, enrollment, _ = issued_rig
    prior = issue(service, key)
    proof = certificate(issued_rig, prior)
    (enrollment.queue_root / f"{key}.{suffix}.json").write_bytes(b"unknown")
    with pytest.raises(custody.Invalid, match="consumption"):
        issue(service, key, proof)
    assert len(service.get(key, uid=os.getuid())["bundle"]["permits"]) == 1


@pytest.mark.parametrize("change", ["source", "policy", "boot", "expiry", "enrollment"])
def test_current_authority_and_exact_live_source_required(issued_rig, change):
    _, key, service, authority, enrollment, rig = issued_rig
    if change == "source":
        service.source.return_value = dataclasses.replace(rig[2], descriptor=b"{}")
    elif change == "policy":
        authority.return_value = "1" * 64
    elif change == "boot":
        service.source.return_value = dataclasses.replace(rig[2], boot_id="other")
    elif change == "expiry":
        service.source.return_value = dataclasses.replace(rig[2], boottime_ns=int(rig[1].expires_boottime_ns))
    else:
        service.publication.return_value = dataclasses.replace(enrollment, enrollment_sha256="1" * 64)
    with pytest.raises(custody.Invalid):
        issue(service, key)
    assert not (enrollment.root / "permits").exists()
    assert "permits" not in service.get(key, uid=os.getuid())["bundle"]


def test_revoke_prevents_publication_even_of_retained_generation(issued_rig):
    _, key, service, _, _, _ = issued_rig
    issue(service, key)
    answer(service, key, action="revoke", mid="2" * 64)
    with pytest.raises(custody.Invalid, match="not-consented"):
        issue(service, key)


def test_absent_production_publication_port_holds(issued_rig):
    _, key, service, _, _, _ = issued_rig
    service.publication = None
    with pytest.raises(custody.Invalid, match="publication-unavailable"):
        issue(service, key)


def test_publication_failure_retains_exact_generation_without_reissue(issued_rig, monkeypatch):
    _, key, service, _, enrollment, _ = issued_rig
    publish = custody.recovery.publish
    monkeypatch.setattr(custody.recovery, "publish", Mock(side_effect=OSError("synthetic ENOSPC")))
    with pytest.raises(OSError):
        issue(service, key)
    retained = service.get(key, uid=os.getuid())["bundle"]["permits"][0]
    monkeypatch.setattr(custody.recovery, "publish", publish)
    assert issue(service, key) == custody.canonical(retained)
    assert len(service.get(key, uid=os.getuid())["bundle"]["permits"]) == 1


def test_renewal_certificate_exact_tombstone_required(issued_rig):
    _, key, service, _, enrollment, _ = issued_rig
    old = issue(service, key)
    proof = certificate(issued_rig, old)
    (enrollment.queue_root / f"{key}.superseded-1.json").write_bytes(b"{}")
    with pytest.raises(custody.Invalid):
        issue(service, key, proof)
    assert len(service.get(key, uid=os.getuid())["bundle"]["permits"]) == 1


def test_unsafe_root_component_and_symlink_permit_hold(issued_rig, tmp_path):
    _, key, service, _, enrollment, _ = issued_rig
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    service.publication.return_value = dataclasses.replace(enrollment, root=alias / "nested")
    with pytest.raises(custody.Invalid):
        issue(service, key)
    assert not (actual / "nested").exists()


def test_window_cannot_cross_immutable_original_expiry(issued_rig):
    _, key, service, _, enrollment, rig = issued_rig
    service.source.return_value = dataclasses.replace(
        rig[2], boottime_ns=int(rig[1].expires_boottime_ns) - custody.WINDOW_NS + 1
    )
    with pytest.raises(custody.Invalid, match="proposal-expiring"):
        issue(service, key)
    assert not (enrollment.root / "permits").exists()


def test_repair_does_not_republish_superseded_or_consumed_authority(issued_rig):
    _, key, service, _, _, _ = issued_rig
    previous = issue(service, key)
    certificate(issued_rig, previous)
    with pytest.raises(custody.Invalid, match="permit-superseded"):
        issue(service, key)


def test_publication_conflict_retains_candidate_and_never_changes_uuid(issued_rig):
    _, key, service, _, enrollment, _ = issued_rig
    path = enrollment.root / "generations" / key / "1.json"
    path.parent.mkdir(mode=0o700, parents=True)
    path.parent.parent.chmod(0o700)
    path.write_bytes(b"different original")
    path.chmod(0o600)
    with pytest.raises(custody.Invalid, match="publication-conflict"):
        issue(service, key)
    retained = service.get(key, uid=os.getuid())["bundle"]["permits"][0]
    with pytest.raises(custody.Invalid, match="publication-conflict"):
        issue(service, key)
    assert service.get(key, uid=os.getuid())["bundle"]["permits"] == [retained]
    assert path.read_bytes() == b"different original"


def test_noncanonical_or_foreign_certificate_never_advances_generation(issued_rig):
    _, key, service, _, enrollment, _ = issued_rig
    old = issue(service, key)
    certificate(issued_rig, old)
    original = enrollment.queue_root / f"{key}.nonconsumption-1.json"
    changed = original.read_bytes() + b"\n"
    original.write_bytes(changed)
    (enrollment.queue_root / f"{key}.superseded-1.json").write_bytes(changed)
    with pytest.raises(custody.Invalid, match="noncanonical"):
        issue(service, key, hashlib.sha256(changed).hexdigest())
    assert len(service.get(key, uid=os.getuid())["bundle"]["permits"]) == 1
