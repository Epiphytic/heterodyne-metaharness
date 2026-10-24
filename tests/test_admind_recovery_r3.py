"""R3 failure-first regressions; disposable stores/transport fakes only."""

import os
import threading

import msgspec
import pytest
from test_admind_recovery import ACCOUNT, GROUP, MID, OPERATOR, THREAD, Rig, write
from test_admind_recovery_r2 import fresh

from heterodyne.admind import recovery


@pytest.mark.parametrize("suffix", ["consumed", "issuing", "receipt", None])
def test_expired_permit_cannot_be_superseded_without_positive_nonconsumption(tmp_path, suffix):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        rig.permit()
        rig.tick += 31_000_000_000
        old = rig.key
        if suffix is not None:
            rig.certificate()  # even a retained positive certificate cannot hide begun work
            write(rig.queue / "recovery-v1" / f"{old}.{suffix}.json", {"fixture": "begun"})
        fresh(rig)
        with pytest.raises((recovery.Invalid, OSError)):
            rig.register()
        assert rig.service.current(rig.obj.operation, old)
    finally:
        rig.store.close()


def test_reboot_before_consumption_holds_even_with_old_certificate(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        rig.permit()
        rig.certificate()
        rig.boot = "42345678-1234-4234-8234-123456789abc"
        fresh(rig)
        with pytest.raises(recovery.Invalid, match="boot-changed"):
            rig.register()
    finally:
        rig.store.close()


@pytest.mark.parametrize("generation", [True, 1.0])
def test_certificate_generation_is_exact_integer(tmp_path, generation):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        rig.permit()
        rig.certificate()
        path = rig.queue / "recovery-v1" / f"{rig.key}.nonconsumption-1.json"
        value = recovery.decode(path.read_bytes(), dict)
        value["generation"] = generation
        for suffix in ("nonconsumption", "superseded"):
            write(rig.queue / "recovery-v1" / f"{rig.key}.{suffix}-1.json", value)
        with pytest.raises(recovery.Invalid):
            rig.permit(recovery.sha(value))
    finally:
        rig.store.close()


@pytest.mark.parametrize(
    "change",
    [
        {"effects_root": None},
        {"backup_before_sha256": "a" * 64},
        {
            "local_records": [
                {
                    "adapter_sha256": "a" * 64,
                    "selector": {"type": "local", "id": "backup"},
                    "before_sha256": "b" * 64,
                    "action": "finish-health",
                }
            ]
        },
    ],
)
def test_descriptor_rejects_cross_scope_combinations(tmp_path, change):
    rig = Rig(tmp_path)
    try:
        value = msgspec.to_builtins(rig.obj)
        value.update(change)
        with pytest.raises(recovery.Invalid):
            recovery.descriptor(recovery.canonical(value))
    finally:
        rig.store.close()


def test_false_group_observation_cannot_record_consent(tmp_path):
    from test_admind_r1 import Unit, inbound, run

    from heterodyne.marmot.control import ReplyTo

    unit = Unit(tmp_path / "unit")
    rig = Rig(tmp_path / "rig", unit.store)
    unit.daemon.maintenance_recovery = rig.service
    rig.deliver()

    async def superseded(*, deadline: float | None = None):
        return False

    unit.daemon.check_group = superseded
    unit.daemon.recovery_authority = lambda sender=None: "a" * 64
    try:
        event = msgspec.structs.replace(
            inbound(f"!maintenance settle {rig.key[:24]}", MID), reply_to=ReplyTo(THREAD)
        )
        run(unit.daemon.recovery_message(event))
        assert rig.service.get(rig.key)["state"] == "awaiting-consent"
    finally:
        unit.store.close()


def test_conflict_is_fenced_when_policy_temporarily_unreadable(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        fields = recovery.MessageFields(
            account=ACCOUNT,
            group=GROUP,
            mid=MID,
            sender=OPERATOR,
            target="a" * 64,
            text=f"!maintenance settle {rig.key[:24]}",
            authority=lambda: None,
        )
        with pytest.raises(recovery.Invalid, match="replay-mismatch"):
            rig.service.message(**fields)
        rig.service.refusal(**fields, reason="replay-mismatch", post=lambda _: None)
        with pytest.raises(recovery.Invalid, match="conflict"):
            rig.permit()
    finally:
        rig.store.close()


def test_bad_feature_schema_does_not_prevent_ordinary_daemon_construction(tmp_path, monkeypatch):
    from test_admind_r1 import Unit

    def bad(*args, **kwargs):
        raise recovery.Invalid("proposal-schema")

    monkeypatch.setattr(recovery.Service, "upgrade_proposals", bad)
    unit = Unit(tmp_path)
    try:
        assert unit.daemon.maintenance_recovery is None
    finally:
        unit.store.close()


@pytest.mark.parametrize("change", [{"target_snapshot_sha256": "0" * 64}, {"extra": "field"}])
def test_certificate_binds_closed_current_snapshot(tmp_path, change):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        rig.permit()
        rig.certificate()
        path = rig.queue / "recovery-v1" / f"{rig.key}.nonconsumption-1.json"
        value = recovery.decode(path.read_bytes(), dict)
        value.update(change)
        for suffix in ("nonconsumption", "superseded"):
            write(rig.queue / "recovery-v1" / f"{rig.key}.{suffix}-1.json", value)
        with pytest.raises(recovery.Invalid):
            rig.permit(recovery.sha(value))
    finally:
        rig.store.close()


def test_permit_authority_refresh_stays_on_daemon_loop(tmp_path):
    from test_admind_r1 import Unit, run

    from heterodyne.admind import ctl

    unit = Unit(tmp_path / "unit")
    rig = Rig(tmp_path / "rig", unit.store)
    unit.daemon.maintenance_recovery = rig.service
    rig.deliver()
    rig.message()
    seen = []
    owner = threading.get_ident()

    async def group(*, deadline: float | None = None):
        return True

    def authority(sender=None):
        seen.append(threading.get_ident())
        return rig.policy

    unit.daemon.check_group = group
    unit.daemon.recovery_authority = authority
    try:
        reply = run(unit.daemon.on_recovery(recovery.Permit(rig.key), ctl.Peer(1, os.getuid(), os.getgid())))
        assert reply.result == "issued", reply
        assert len(seen) >= 3
        assert set(seen) == {owner}
    finally:
        unit.store.close()


@pytest.mark.parametrize("tick", [True, 1.0, -1])
def test_common_clock_rejects_noncanonical_sample(tmp_path, tick):
    rig = Rig(tmp_path)
    try:
        rig.tick = tick
        with pytest.raises(recovery.Invalid, match="clock"):
            rig.service.current_boot()
    finally:
        rig.store.close()


def test_disabled_recovery_socket_reports_unavailable_without_affecting_asks(tmp_path):
    from test_admind_r1 import Unit, run

    from heterodyne.admind import ctl

    unit = Unit(tmp_path)
    unit.daemon.maintenance_recovery = None
    try:
        reply = run(unit.daemon.on_recovery(recovery.Status("a" * 64), ctl.Peer(1, os.getuid())))
        assert reply.result == "unavailable"
        assert unit.store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
    finally:
        unit.store.close()


def test_real_store_backed_authority_does_not_cross_worker_store_lock(tmp_path):
    from test_admind_r1 import Unit, run

    from heterodyne.admind import ctl

    unit = Unit(tmp_path / "unit")
    rig = Rig(tmp_path / "rig", unit.store)
    unit.daemon.maintenance_recovery = rig.service
    operator = next(iter(unit.daemon.operators))
    policy = unit.daemon.recovery_authority(operator)
    assert policy is not None
    rig.policy = policy
    rig.deliver()
    rig.message(sender=operator)

    async def group(*, deadline: float | None = None):
        return True

    unit.daemon.check_group = group
    try:
        reply = run(unit.daemon.on_recovery(recovery.Permit(rig.key), ctl.Peer(1, os.getuid(), os.getgid())))
        assert reply.result == "issued", reply
    finally:
        unit.store.close()


def test_permit_refuses_consent_changed_during_authority_observation(tmp_path):
    rig = Rig(tmp_path)
    rig.deliver()
    rig.message()

    def authority(sender):
        with rig.store.transaction():
            rig.store.db.execute(
                "UPDATE maintenance_recovery_proposals SET state='revoked' WHERE hash=?", (rig.key,)
            )
        return rig.policy

    try:
        with pytest.raises(recovery.Invalid, match="proposal-changed"):
            rig.service.permit(recovery.Permit(rig.key), uid=os.getuid(), authority=authority)
        assert rig.service.get(rig.key)["permit"] is None
    finally:
        rig.store.close()
