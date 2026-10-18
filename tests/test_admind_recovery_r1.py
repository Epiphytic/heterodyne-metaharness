"""Failure-first fixes for the actual first HIGH maintenance-consent review."""

import asyncio
import os
import stat

import msgspec
import pytest
from test_admind_recovery import MID, THREAD, Rig

from heterodyne.admind import recovery


def test_exact_crash_alias_can_be_repaired_without_relaxing_one_link(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    path = root / "1.json"
    alias = root / (".recovery-" + "a" * 32)
    alias.write_bytes(b"{}")
    alias.chmod(0o600)
    os.link(alias, path)
    recovery.publish(path, b"{}")
    assert recovery.private_read(path) == b"{}"
    assert not alias.exists()
    assert path.stat().st_nlink == 1


@pytest.mark.parametrize("alias_name", ["unknown", ".recovery-not-a-uuid"])
def test_unknown_hardlink_remains_held(tmp_path, alias_name):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    path = root / "1.json"
    path.write_bytes(b"{}")
    path.chmod(0o600)
    alias = root / alias_name
    os.link(path, alias)
    with pytest.raises(recovery.Invalid, match="unsafe"):
        recovery.publish(path, b"{}")
    assert alias.exists()


def test_revoke_reserved_slot_after_all_ordinary_audit_slots_used(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        for n in range(128):
            rig.message(mid=f"{n:064x}")
        reply = rig.message("revoke", mid="f" * 64)
        assert reply.message == "revoked"
        assert rig.service.get(rig.key)["state"] == "revoked"
        assert rig.message("revoke", mid="f" * 64).result == "duplicate"
        assert rig.store.db.execute("SELECT count(*) FROM maintenance_recovery_messages").fetchone()[0] == 128
        with pytest.raises(recovery.Invalid, match="consented"):
            rig.permit()
    finally:
        rig.store.close()


def test_permit_refuses_changed_policy_even_if_same_operator_is_current(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        rig.message()
        rig.policy = "a" * 64
        with pytest.raises(recovery.Invalid, match="policy-drift"):
            rig.permit()
        assert rig.service.get(rig.key)["generation"] == 0
    finally:
        rig.store.close()


def test_duplicate_provenance_receipt_readback_survives_current_authority_loss(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        first = rig.message()
        rig.policy = None  # type: ignore[assignment]
        duplicate = rig.message()
        assert duplicate.result == "duplicate"
        assert duplicate.message == first.message
        assert duplicate.view == first.view
        with pytest.raises(recovery.Invalid, match="replay"):
            rig.message(sender="a" * 64)
    finally:
        rig.store.close()


def test_notice_discloses_actual_cross_process_revocation_residual(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.register()
        body = rig.store.pending()[0].text
        assert "30 seconds" in body
        assert "after revoke" in body
    finally:
        rig.store.close()


def test_all_created_publication_ancestors_are_private(tmp_path):
    path = tmp_path / "new-recovery" / "generations" / "key" / "1.json"
    recovery.publish(path, b"{}")
    for parent in [path.parent, path.parent.parent, path.parent.parent.parent]:
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700


def test_daemon_exact_command_durable_receipt_and_wrong_thread_refusal(tmp_path):
    from test_admind_r1 import Unit, inbound, run

    from heterodyne.marmot.control import ReplyTo

    unit = Unit(tmp_path / "unit")
    rig = Rig(tmp_path / "rig", unit.store)
    unit.daemon.maintenance_recovery = rig.service
    rig.deliver()
    try:
        ev = msgspec.structs.replace(
            inbound(f"!maintenance settle {rig.key[:24]}", MID), reply_to=ReplyTo(THREAD)
        )
        run(unit.daemon.on_message(ev))
        rows = [r for r in unit.store.pending() if r.key.startswith("maintenance-response:")]
        assert len(rows) == 1
        assert rows[0].reply_to == MID
        assert rows[0].text == "Maintenance recovery: consented."
        assert unit.store.row_owners(rows[0].seq) == (ev.message.sender.account_id_hex.lower(),)
        run(unit.daemon.on_message(ev))
        assert len([r for r in unit.store.pending() if r.key.startswith("maintenance-response:")]) == 1
        wrong = msgspec.structs.replace(
            inbound(f"!maintenance settle {rig.key[:24]}", "a" * 64), reply_to=ReplyTo("b" * 64)
        )
        run(unit.daemon.on_message(wrong))
        rows = [r for r in unit.store.pending() if r.reply_to == "a" * 64]
        assert len(rows) == 1
        assert rows[0].text == "Maintenance recovery refused: thread-unmapped."
    finally:
        unit.store.close()


def test_similar_prefix_is_not_intercepted_by_recovery(tmp_path):
    from test_admind_r1 import Unit, inbound, run

    unit = Unit(tmp_path)
    events = []

    async def normal(mid: str, text: str, target: str | None = None):
        events.append(text)

    unit.daemon.handle = normal
    try:
        commands = ["!maintenance-window tonight", "!maintenance status", "!maintenance settle bad"]
        for n, text in enumerate(commands):
            run(unit.daemon.on_message(inbound(text, f"{n + 1:064x}")))
        assert events == ["!maintenance-window tonight", "!maintenance status", "!maintenance settle bad"]
    finally:
        unit.store.close()


def test_recovery_validation_does_not_block_event_loop(tmp_path):
    import threading

    from test_admind_r1 import Unit

    from heterodyne.admind import ctl

    unit = Unit(tmp_path / "unit")
    rig = Rig(tmp_path / "rig", unit.store)
    unit.daemon.maintenance_recovery = rig.service
    entered, release = threading.Event(), threading.Event()
    original = rig.service.enrollment

    def blocked():
        entered.set()
        assert release.wait(3)
        return original()

    rig.service.enrollment = blocked

    async def scenario():
        task = asyncio.create_task(
            unit.daemon.on_recovery(
                recovery.Register(rig.obj, rig.obj.proof_set_sha256), ctl.Peer(1, os.getuid())
            )
        )
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            assert not task.done()
        finally:
            release.set()
            await task

    try:
        asyncio.run(scenario())
    finally:
        unit.store.close()


def test_refusal_receipt_is_atomic_and_changed_payload_never_selects(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()
        fields = recovery.MessageFields(
            account="11" * 32,
            group="22" * 16,
            mid=MID,
            sender="33" * 32,
            target="b" * 64,
            text=f"!maintenance settle {rig.key[:24]}",
            authority=lambda: rig.policy,
        )
        receipts = []
        rig.service.refusal(**fields, reason="thread-unmapped", post=receipts.append)
        fields["target"] = THREAD
        with pytest.raises(recovery.Invalid, match="replay-mismatch"):
            rig.service.message(**fields)
        assert rig.service.get(rig.key)["state"] == "awaiting-consent"
        assert receipts == ["Maintenance recovery refused: thread-unmapped."]
    finally:
        rig.store.close()


def test_response_failure_rolls_back_consent_and_provenance(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.deliver()

        def failed(_text):
            raise OSError("offline fixture")

        with pytest.raises(OSError):
            rig.service.message(
                account="11" * 32,
                group="22" * 16,
                mid=MID,
                sender="33" * 32,
                target=THREAD,
                text=f"!maintenance settle {rig.key[:24]}",
                authority=lambda: rig.policy,
                post=failed,
            )
        assert rig.service.get(rig.key)["state"] == "awaiting-consent"
        assert rig.store.db.execute("SELECT count(*) FROM maintenance_recovery_messages").fetchone()[0] == 0
    finally:
        rig.store.close()


def test_recovery_client_verifies_server_peer_before_request(tmp_path, monkeypatch):
    from heterodyne import platform
    from heterodyne.admind import ctl

    async def scenario():
        seen = []

        async def accept(reader, writer):
            seen.append(await reader.read())
            writer.close()
            await writer.wait_closed()

        path = tmp_path / "peer.sock"
        server = await asyncio.start_unix_server(accept, str(path))
        monkeypatch.setattr(platform, "peer_credentials_checked", lambda _sock: (1, os.getuid() + 1))
        try:
            with pytest.raises(ctl.CtlUnavailable, match="server-peer"):
                await ctl.request(
                    path, recovery.Status("a" * 64), reply_type=recovery.Reply, require_uid=os.getuid()
                )
            for _ in range(10):
                if seen:
                    break
                await asyncio.sleep(0.001)
            assert seen == [b""]
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_legacy_redaction_preserves_short_notice_thread(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.register()
        rig.store.set("outbox_needs_redaction", "1")
        rig.store.redact_pending_outbox(3200)
        row = rig.store.pending()[0]
        assert row.key.endswith(":0")  # Actual short notice IDs are not legacy chunk keys.
        rig.store.mark_sent(row.seq, THREAD)
        assert rig.message().message == "consented"
    finally:
        rig.store.close()


def test_daemon_retained_readback_after_removal_cannot_select_new_command(tmp_path):
    from test_admind_r1 import Unit, inbound, run

    from heterodyne.marmot.control import ReplyTo

    unit = Unit(tmp_path / "unit")
    rig = Rig(tmp_path / "rig", unit.store)
    unit.daemon.maintenance_recovery = rig.service
    rig.deliver()
    ev = msgspec.structs.replace(
        inbound(f"!maintenance settle {rig.key[:24]}", MID), reply_to=ReplyTo(THREAD)
    )
    try:
        run(unit.daemon.on_message(ev))
        unit.daemon.recovery_authority = lambda sender=None: None
        run(unit.daemon.on_message(ev))
        changed = msgspec.structs.replace(
            inbound(f"!maintenance revoke {rig.key[:24]}", "a" * 64), reply_to=ReplyTo(THREAD)
        )
        run(unit.daemon.on_message(changed))
        assert rig.service.get(rig.key)["state"] == "consented"
        responses = [r for r in unit.store.pending() if r.key.startswith("maintenance-response:")]
        assert len(responses) == 1
        assert unit.store.inbound_status(MID) is None
        assert unit.store.inbound_status("a" * 64) is None
    finally:
        unit.store.close()
