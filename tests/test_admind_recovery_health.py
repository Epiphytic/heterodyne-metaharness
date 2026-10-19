"""Offline health registration: local target provenance never supplies consent."""

import copy
from typing import cast

import pytest
from test_admind_recovery import BOOT, LINK, Rig, write

from heterodyne.admind import recovery


def health(tmp_path, *, legacy=False, state="unknown"):
    r = Rig(tmp_path)
    sources = cast(dict[str, str], r.graph["sources"])
    source = next(iter(sources))
    coverage = {"backup_policy": {"source": source}}
    r.graph["coverage_sha256"] = recovery.sha(coverage)
    write(r.queue / "coverage.json", coverage)
    write(r.queue / "enrollment.json", r.graph)
    backup = dict(
        schema="maintenance-backup.v1",
        operation=r.value["operation"],
        owner=dict(pid=123, start=456, boot=BOOT),
        source_sha256=sources[source],
        state=state,
        ordinal=1,
        intent_sha256="a" * 64,
        recovery_only=False,
    )
    failure = dict(schema="maintenance-backup-failure.v1", operation=backup["operation"])
    if not legacy:
        failure.update(source_sha256=backup["source_sha256"], profile_sha256=recovery.sha(r.graph["profile"]))
    write(r.queue / "backup.json", backup)
    write(r.queue / "backup-failure.json", failure)
    (r.queue / "operations" / f"{backup['operation']}.json").unlink()
    r.value.update(
        mode="finish-health",
        enrollment_sha256=recovery.sha(r.graph),
        effects_root=None,
        effect_count=0,
        local_records=[],
        original_record_sha256=recovery.sha(backup),
        backup_before_sha256=recovery.sha(backup),
        failure_before_sha256=recovery.sha(failure),
        proof_set_sha256=recovery.sha([]),
        result="health-restored",
    )
    r.obj = recovery.descriptor(recovery.canonical(r.value))
    r.key = recovery.sha(r.obj)
    r.enrolled = recovery.Enrollment(
        r.enrolled.schema, recovery.sha(r.graph), r.enrolled.source_closure, str(r.queue), {r.key: LINK}
    )
    write(r.root / "enrollment.json", r.enrolled)
    return r, backup, failure


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("state", ["unknown", "complete"])
def test_health_register_owner_not_actor_and_no_consent(tmp_path, legacy, state):
    r, backup, _ = health(tmp_path, legacy=legacy, state=state)
    try:
        assert r.register().result == "registered"
        text = r.store.db.execute("SELECT notice_text FROM maintenance_recovery_proposals").fetchone()[0]
        assert "Backup owner: pid=123 start=456 boot=" + BOOT in text
        assert "Original actor:" not in text
        assert r.service.get(r.key)["state"] == "awaiting-consent"
        with pytest.raises(recovery.Invalid):
            r.permit()
        assert not (r.root / "permits" / f"{r.key}.json").exists()
        assert recovery.decode(recovery.private_read(r.queue / "backup.json"), dict) == backup
    finally:
        r.store.close()


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("backup", "operation", BOOT),
        ("backup", "source_sha256", "0" * 64),
        ("backup", "owner", {"pid": True, "start": 0, "boot": BOOT}),
        ("backup", "owner", {"pid": 1, "start": 1.0, "boot": BOOT}),
        ("backup", "state", "other"),
        ("backup", "ordinal", True),
        ("failure", "operation", BOOT),
        ("failure", "profile_sha256", "0" * 64),
        ("failure", "source_sha256", "0" * 64),
        ("failure", "extra", 1),
    ],
)
def test_health_malformed_or_unrelated_refuses_before_notice(tmp_path, target, field, value):
    r, backup, failure = health(tmp_path)
    try:
        obj = copy.deepcopy(backup if target == "backup" else failure)
        obj[field] = value
        write(r.queue / ("backup.json" if target == "backup" else "backup-failure.json"), obj)
        # Exact hash matching cannot make a malformed/unrelated target authoritative.
        r.value.update(
            original_record_sha256=recovery.sha(obj) if target == "backup" else recovery.sha(backup),
            backup_before_sha256=recovery.sha(obj) if target == "backup" else recovery.sha(backup),
            failure_before_sha256=recovery.sha(obj) if target == "failure" else recovery.sha(failure),
        )
        r.obj = recovery.descriptor(recovery.canonical(r.value))
        with pytest.raises(recovery.Invalid):
            r.register()
        assert r.store.db.execute("SELECT count(*) FROM maintenance_recovery_proposals").fetchone()[0] == 0
        assert not r.store.pending()
    finally:
        r.store.close()


@pytest.mark.parametrize("field", ["original_record_sha256", "backup_before_sha256", "failure_before_sha256"])
def test_health_descriptor_target_hash_drift_refuses(tmp_path, field):
    r, _, _ = health(tmp_path)
    try:
        r.value[field] = "0" * 64
        r.obj = recovery.descriptor(recovery.canonical(r.value))
        with pytest.raises(recovery.Invalid):
            r.register()
        assert not r.store.pending()
    finally:
        r.store.close()


def test_health_enrollment_generation_drift_refuses(tmp_path):
    r, _, _ = health(tmp_path)
    try:
        r.graph["generation"] = "changed"
        write(r.queue / "enrollment.json", r.graph)
        with pytest.raises(recovery.Invalid):
            r.register()
        assert not r.store.pending()
    finally:
        r.store.close()


def test_health_duplicate_has_one_immutable_notice(tmp_path):
    r, _, _ = health(tmp_path)
    try:
        assert r.register().result == "registered"
        before = r.store.db.execute("SELECT * FROM maintenance_recovery_proposals").fetchall()
        pending = r.store.pending()
        assert r.register().result == "existing"
        assert r.store.db.execute("SELECT * FROM maintenance_recovery_proposals").fetchall() == before
        assert r.store.pending() == pending
    finally:
        r.store.close()


def test_health_exact_target_null_root_certificate_advances_generation(tmp_path):
    r, _, _ = health(tmp_path)
    try:
        r.deliver()
        r.message()
        assert r.permit().result == "issued"
        reply = r.permit(r.certificate())
        assert reply.view is not None and reply.view["generation"] == 2
    finally:
        r.store.close()
