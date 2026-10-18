"""Actual second HIGH regressions over isolated stores and fake ingress only."""

import asyncio
import os
import threading
from datetime import timedelta

import msgspec
import pytest
from test_admind_recovery import ACCOUNT, GROUP, MID, OPERATOR, THREAD, Rig, write

from heterodyne.admind import recovery


def fresh(rig):
    old = rig.key
    rig.obj = msgspec.structs.replace(
        rig.obj,
        proposal_id="32345678-1234-4234-8234-123456789abc",
        expires_at=(rig.at + timedelta(hours=1)).isoformat(),
    )
    rig.key = recovery.sha(rig.obj)
    rig.enrolled = msgspec.structs.replace(
        rig.enrolled,
        descriptor_links={
            **rig.enrolled.descriptor_links,
            rig.key: next(iter(rig.enrolled.descriptor_links.values())),
        },
    )
    write(rig.root / "enrollment.json", rig.enrolled)
    return old


@pytest.mark.parametrize("reason", ["expired", "revoked", "cap", "boot", "policy"])
def test_fresh_proposal_preserves_history_and_needs_own_consent(tmp_path, reason):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        if reason == "expired":
            rig.at += timedelta(hours=2)
        elif reason == "revoked":
            rig.message("revoke", mid="a" * 64)
        else:
            rig.permit()
            rig.tick += 31_000_000_000
            if reason == "cap":
                for _ in range(2):
                    rig.permit(rig.certificate())
                    rig.tick += 31_000_000_000
            elif reason == "boot":
                rig.boot = "42345678-1234-4234-8234-123456789abc"
            else:
                rig.policy = "a" * 64
        old = fresh(rig)
        before = rig.service.get(old)
        assert rig.register().result == "registered"
        assert rig.service.get(old) == before
        with pytest.raises(recovery.Invalid, match="superseded"):
            rig.service.permit(recovery.Permit(old), uid=os.getuid(), authority=lambda _: rig.policy)
        with pytest.raises(recovery.Invalid, match="consented"):
            rig.permit()
        rig.deliver()
        rig.message(mid="b" * 64)
        reply = rig.permit()
        assert reply.view is not None and reply.view["generation"] == 1
        assert rig.store.db.execute("SELECT count(*) FROM maintenance_recovery_proposals").fetchone()[0] == 2
    finally:
        rig.store.close()


def test_fresh_proposal_cannot_overlap_still_usable_old_permit(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        rig.permit()
        old = fresh(rig)
        with pytest.raises(recovery.Invalid, match="permit-active"):
            rig.register()
        assert rig.service.get(old)["generation"] == 1
    finally:
        rig.store.close()


@pytest.mark.parametrize("delta", [timedelta(days=-1), timedelta(days=2)])
def test_authenticated_revoke_survives_invalid_live_time(tmp_path, delta):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        original = rig.at
        rig.at += delta
        assert rig.message("revoke", mid="a" * 64).message == "revoked"
        rig.at = original
        with pytest.raises(recovery.Invalid, match="consented"):
            rig.permit()
    finally:
        rig.store.close()


def test_conflicting_deciding_native_message_fences_permits(tmp_path):
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
            authority=lambda: rig.policy,
        )
        with pytest.raises(recovery.Invalid, match="replay-mismatch"):
            rig.service.message(**fields)
        rig.service.refusal(**fields, reason="replay-mismatch", post=lambda _: None)
        with pytest.raises(recovery.Invalid, match="conflict"):
            rig.permit()
        assert rig.service.get(rig.key)["generation"] == 0
    finally:
        rig.store.close()


def test_permit_repair_publishes_outside_writer_transaction(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        rig.permit()
        original = rig.service.publish_permit
        observed = []

        def publish(key, data):
            observed.append(rig.store.db.in_transaction)
            return original(key, data)

        monkeypatch.setattr(rig.service, "publish_permit", publish)
        rig.permit()
        assert observed == [False]
    finally:
        rig.store.close()


@pytest.mark.parametrize("kind", ["question", "merge", "approval"])
def test_source_owned_bead_restricts_only_approval_posts(tmp_path, kind):
    from test_admind_approvals import BEAD
    from test_admind_operations import call, request
    from test_admind_r1 import Unit

    from heterodyne.admind import asks, ctl

    unit = Unit(tmp_path)
    try:
        call(unit, request("withdraw"))
        post = asks.AskPost(kind, title="Title", body="Question", bead=BEAD)
        result = asyncio.run(unit.daemon.serialised_post(post, ctl.Peer(1, os.getuid())))
        if kind == "approval":
            assert result.result == "refused" and "source-owned" in result.message
        else:
            assert result.result != "refused", result
    finally:
        unit.store.close()


@pytest.mark.parametrize("failure", [recovery.Invalid("policy-drift"), OSError("fixture")])
def test_cancelled_recovery_handler_preserves_cancellation_on_worker_failure(tmp_path, monkeypatch, failure):
    from test_admind_r1 import Unit

    from heterodyne.admind import ctl

    unit = Unit(tmp_path / "unit")
    rig = Rig(tmp_path / "rig", unit.store)
    unit.daemon.maintenance_recovery = rig.service
    entered, release = threading.Event(), threading.Event()

    def blocked(*args, **kw):
        entered.set()
        assert release.wait(3)
        raise failure

    monkeypatch.setattr(rig.service, "register", blocked)

    async def scenario():
        task = asyncio.create_task(
            unit.daemon.on_recovery(
                recovery.Register(rig.obj, rig.obj.proof_set_sha256), ctl.Peer(1, os.getuid())
            )
        )
        while not entered.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
    finally:
        unit.store.close()


def test_legacy_schema_upgrade_is_atomic_and_preserves_all_original_bytes(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        rig.permit()
        before = rig.store.db.execute("SELECT * FROM maintenance_recovery_proposals").fetchall()
        with rig.store.transaction():
            rig.store.db.execute("DROP TABLE maintenance_recovery_current")
            rig.store.db.execute("ALTER TABLE maintenance_recovery_proposals RENAME TO saved")
            rig.store.db.execute(
                recovery.SCHEMA.split(";")[0].replace(
                    "operation TEXT NOT NULL,", "operation TEXT NOT NULL UNIQUE,"
                )
            )
            rig.store.db.execute("INSERT INTO maintenance_recovery_proposals SELECT * FROM saved")
            rig.store.db.execute("DROP TABLE saved")
        service = recovery.Service(rig.store, rig.root)
        assert [tuple(r) for r in rig.store.db.execute("SELECT * FROM maintenance_recovery_proposals")] == [
            tuple(r) for r in before
        ]
        assert service.current(rig.obj.operation, rig.key)
        # Restart is idempotent after the exact legacy migration.
        recovery.Service(rig.store, rig.root)
        assert rig.store.db.execute("SELECT count(*) FROM maintenance_recovery_current").fetchone()[0] == 1
    finally:
        rig.store.close()


def test_legacy_upgrade_refuses_unknown_trigger_without_losing_history(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.register()
        with rig.store.transaction():
            rig.store.db.execute("DROP TABLE maintenance_recovery_current")
            rig.store.db.execute("ALTER TABLE maintenance_recovery_proposals RENAME TO saved")
            rig.store.db.execute(
                recovery.SCHEMA.split(";")[0].replace(
                    "operation TEXT NOT NULL,", "operation TEXT NOT NULL UNIQUE,"
                )
            )
            rig.store.db.execute("INSERT INTO maintenance_recovery_proposals SELECT * FROM saved")
            rig.store.db.execute("DROP TABLE saved")
            rig.store.db.execute(
                "CREATE TRIGGER unexpected AFTER DELETE ON maintenance_recovery_proposals BEGIN SELECT 1; END"
            )
        before = list(rig.store.db.iterdump())
        with pytest.raises(recovery.Invalid, match="proposal-schema"):
            recovery.Service(rig.store, rig.root)
        assert list(rig.store.db.iterdump()) == before
    finally:
        rig.store.close()


def test_conflict_fence_survives_full_diagnostic_capacity_and_restart(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        for n in range(128):
            rig.service.refusal(
                account=ACCOUNT,
                group=GROUP,
                mid=f"{n:064x}",
                sender=OPERATOR,
                target=THREAD,
                text="invalid",
                reason="token",
                authority=lambda: rig.policy,
                post=lambda _: None,
            )
        rig.service.refusal(
            account=ACCOUNT,
            group=GROUP,
            mid=MID,
            sender=OPERATOR,
            target=None,
            text="different",
            reason="replay-mismatch",
            authority=lambda: rig.policy,
            post=lambda _: None,
        )
        rig.service = recovery.Service(
            rig.store, rig.root, clock=lambda: rig.at, boottime=lambda: rig.tick, boot=lambda: rig.boot
        )
        with pytest.raises(recovery.Invalid, match="conflict"):
            rig.permit()
        assert rig.store.db.execute("SELECT count(*) FROM maintenance_recovery_refusals").fetchone()[0] == 128
    finally:
        rig.store.close()


def test_superseded_proposal_late_settle_never_reactivates(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        old = fresh(rig)
        rig.register()
        reply = rig.service.message(
            account=ACCOUNT,
            group=GROUP,
            mid=MID,
            sender=OPERATOR,
            target=THREAD,
            text=f"!maintenance settle {old[:24]}",
            authority=lambda: rig.policy,
        )
        assert reply.message == "history-only"
        assert rig.service.get(old)["state"] == "awaiting-consent"
        assert rig.service.get(rig.key)["state"] == "awaiting-consent"
    finally:
        rig.store.close()


def test_finish_health_exact_null_effect_root_certificate(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.obj = msgspec.structs.replace(
            rig.obj,
            mode="finish-health",
            result="health-restored",
            effects_root=None,
            effect_count=0,
            proof_set_sha256=recovery.sha([]),
            backup_before_sha256="a" * 64,
            failure_before_sha256="b" * 64,
        )
        rig.key = recovery.sha(rig.obj)
        rig.enrolled = msgspec.structs.replace(
            rig.enrolled, descriptor_links={rig.key: next(iter(rig.enrolled.descriptor_links.values()))}
        )
        write(rig.root / "enrollment.json", rig.enrolled)
        rig.deliver()
        rig.message()
        rig.permit()
        certificate = rig.certificate()
        reply = rig.permit(certificate)
        assert reply.view is not None and reply.view["generation"] == 2
    finally:
        rig.store.close()


def test_unknown_boot_cannot_supersede_an_initiable_permit(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        rig.permit()
        old = fresh(rig)
        rig.boot = ""
        with pytest.raises(recovery.Invalid, match="boot"):
            rig.register()
        assert rig.service.current(rig.obj.operation, old)
    finally:
        rig.store.close()


@pytest.mark.parametrize("foreign", ["group", "sender", "account"])
def test_recovery_command_keeps_guard_drop_forensics(tmp_path, foreign):
    import json

    from test_admind_r1 import STRANGER, Unit, inbound, run

    unit = Unit(tmp_path)
    event = inbound("!maintenance settle " + "a" * 24, MID)
    if foreign == "sender":
        event = inbound("!maintenance settle " + "a" * 24, MID, sender=STRANGER)
    if foreign == "group":
        event = msgspec.structs.replace(event, group_id_hex="aa" * 16)
    elif foreign == "account":
        event = msgspec.structs.replace(event, account_id_hex="bb" * 32)
    try:
        run(unit.daemon.on_message(event))
        records = [json.loads(line) for line in unit.daemon.audit.path.read_text().splitlines()]
        drops = [row for row in records if row["kind"] == "drop"]
        assert len(drops) == 1
        assert "text" not in drops[0]
        assert "sender_prefix" in drops[0]
        assert unit.store.db.execute("SELECT count(*) FROM maintenance_recovery_messages").fetchone()[0] == 0
    finally:
        unit.store.close()


def test_fresh_proposal_post_failure_rolls_back_current_pointer_and_history(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        old = fresh(rig)
        before = list(rig.store.db.iterdump())

        def fail(key, text):
            raise OSError("disposable delivery failure")

        with pytest.raises(OSError):
            rig.service.register(
                recovery.Register(rig.obj, rig.obj.proof_set_sha256),
                uid=os.getuid(),
                pid=1,
                chunk_chars=2000,
                post=fail,
            )
        assert list(rig.store.db.iterdump()) == before
        assert rig.service.current(rig.obj.operation, old)
    finally:
        rig.store.close()


def test_legacy_upgrade_interrupted_after_copy_rolls_back_every_change(tmp_path):
    import sqlite3

    rig = Rig(tmp_path)
    try:
        rig.register()
        with rig.store.transaction():
            rig.store.db.execute("DROP TABLE maintenance_recovery_current")
            rig.store.db.execute("ALTER TABLE maintenance_recovery_proposals RENAME TO saved")
            rig.store.db.execute(
                recovery.SCHEMA.split(";")[0].replace(
                    "operation TEXT NOT NULL,", "operation TEXT NOT NULL UNIQUE,"
                )
            )
            rig.store.db.execute("INSERT INTO maintenance_recovery_proposals SELECT * FROM saved")
            rig.store.db.execute("DROP TABLE saved")
        before = list(rig.store.db.iterdump())
        rig.store.db.set_authorizer(
            lambda action, name, *_: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_DROP_TABLE and name == "maintenance_recovery_proposals"
                else sqlite3.SQLITE_OK
            )
        )
        with pytest.raises(sqlite3.DatabaseError):
            recovery.Service(rig.store, rig.root)
        rig.store.db.set_authorizer(None)
        assert list(rig.store.db.iterdump()) == before
        recovery.Service(rig.store, rig.root)
        assert rig.service.current(rig.obj.operation, rig.key)
    finally:
        rig.store.db.set_authorizer(None)
        rig.store.close()
