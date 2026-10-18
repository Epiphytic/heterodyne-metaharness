"""Offline maintenance consent invariants; no native effects or live transport."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import msgspec
import pytest

from heterodyne.admind import recovery
from heterodyne.admind.store import Store


def test_duplicate_and_deep_json_are_refused():
    with pytest.raises(recovery.Invalid):
        recovery.decode(b'{"a":1,"a":2}', dict)
    with pytest.raises(recovery.Invalid):
        recovery.decode(("[" * 14 + "0" + "]" * 14).encode(), list)


def test_descriptor_boolean_is_not_effect_count():
    value = descriptor()
    value["effect_count"] = True
    with pytest.raises(recovery.Invalid):
        recovery.descriptor(json.dumps(value).encode())


def descriptor():
    return dict(
        schema="maintenance-recovery.v1",
        proposal_id="12345678-1234-4234-8234-123456789abc",
        operation="22345678-1234-4234-8234-123456789abc",
        mode="settle",
        enrollment_sha256="a" * 64,
        original_record_sha256="b" * 64,
        source_closure="c" * 64,
        effects_root="d" * 64,
        effect_count=1,
        local_records=[],
        backup_before_sha256=None,
        failure_before_sha256=None,
        proof_set_sha256="e" * 64,
        result="complete",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )


# All artifacts below are disposable, same-UID offline fixtures. No Queue is constructed.

ACCOUNT = "11" * 32
GROUP = "22" * 16
OPERATOR = "33" * 32
MID = "44" * 32
THREAD = "55" * 32
POLICY = "66" * 32
BOOT = "12345678-1234-4234-8234-123456789abc"
LINK = "https://github.com/example/project/blob/" + "7" * 40 + "/descriptor.json"


def write(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(recovery.canonical(value))
    path.chmod(0o600)


class Rig:
    def __init__(self, tmp: Path, store=None):
        tmp.mkdir(parents=True, exist_ok=True)
        self.store = store or Store(tmp / "store" / "admind.db")
        self.root = tmp / "recovery"
        self.queue = tmp / "queue"
        self.at = datetime.now(UTC)
        self.tick = 100_000_000_000
        self.boot = BOOT
        self.policy = POLICY
        self.service = recovery.Service(
            self.store, self.root, clock=lambda: self.at, boottime=lambda: self.tick, boot=lambda: self.boot
        )
        source = tmp / "source.py"
        source.write_text("# retained code fixture\n")
        sources = {str(source): recovery.source_hash(source)}
        coverage = {"schema": "fixture-coverage"}
        graph = dict(
            schema="queue-maintenance-fence.v1",
            uid=os.getuid(),
            root=str(self.queue),
            locks={},
            profile={},
            roots={"admind_maintenance_recovery": str(self.root)},
            sources=sources,
            coordinator_path=str(source),
            coordinator_sha256=sources[str(source)],
            generation=1,
            coverage_sha256=recovery.sha(coverage),
        )
        self.graph = graph
        write(self.queue / "enrollment.json", graph)
        write(self.queue / "coverage.json", coverage)
        self.value = descriptor()
        self.value.update(
            enrollment_sha256=recovery.sha(graph),
            source_closure=recovery.sha(sources),
            expires_at=(self.at + timedelta(hours=1)).isoformat(),
        )
        origin = dict(
            schema="operation.v1",
            operation=self.value["operation"],
            actor="original-worker",
            source_closure=self.value["source_closure"],
            owner={"pid": 1, "start": "1", "boot": BOOT},
            action="scan",
            state="unknown",
            ordinal=1,
            intent_sha256="a" * 64,
        )
        self.value["original_record_sha256"] = recovery.sha(origin)
        write(self.queue / "operations" / f"{self.value['operation']}.json", origin)
        self.obj = recovery.descriptor(recovery.canonical(self.value))
        self.key = recovery.sha(self.obj)
        self.enrolled = recovery.Enrollment(
            "admind-maintenance-recovery-enrollment.v1",
            recovery.sha(graph),
            recovery.sha(sources),
            str(self.queue),
            {self.key: LINK},
        )
        write(self.root / "enrollment.json", self.enrolled)

    def post(self, key, text):
        self.store.enqueue(key, text, None, lane=2, ownership_kind="system")

    def register(self, **kw):
        return self.service.register(
            recovery.Register(self.obj, self.obj.proof_set_sha256),
            uid=os.getuid(),
            pid=1,
            chunk_chars=2000,
            post=kw.get("post", self.post),
        )

    def deliver(self):
        self.register()
        for row in self.store.pending():
            self.store.mark_sent(row.seq, THREAD)

    def message(self, action="settle", **kw):
        args: dict[str, Any] = dict(
            account=ACCOUNT,
            group=GROUP,
            mid=MID,
            sender=OPERATOR,
            target=THREAD,
            text=f"!maintenance {action} {self.key[:24]}",
            authority=lambda: self.policy,
        )
        args.update(kw)
        return self.service.message(**args)

    def permit(self, cert=None):
        return self.service.permit(
            recovery.Permit(self.key, cert), uid=os.getuid(), authority=lambda _: self.policy
        )

    def certificate(self):
        old = self.service.get(self.key)["permit"]
        assert isinstance(old, dict)
        value = dict(
            schema="maintenance-recovery-nonconsumption.v1",
            proposal_sha256=self.key,
            permit_sha256=recovery.sha(old),
            generation=old["generation"],
            operation=self.obj.operation,
            original_record_sha256=self.obj.original_record_sha256,
            effects_root=self.obj.effects_root,
            enrollment_sha256=self.obj.enrollment_sha256,
            boot_id=old["boot_id"],
            observed_boottime_ns=self.tick,
        )
        for suffix in ("nonconsumption", "superseded"):
            write(self.queue / "recovery-v1" / f"{self.key}.{suffix}-{old['generation']}.json", value)
        return recovery.sha(value)


@pytest.fixture
def rig(tmp_path):
    value = Rig(tmp_path)
    yield value
    value.store.close()


def test_register_idempotent_and_notice_uses_existing_capacity(rig):
    assert rig.register().result == "registered"
    assert rig.register().result == "existing"
    assert rig.store.undelivered_notices() == 1
    assert rig.store.db.execute("select count(*) from asks").fetchone()[0] == 0
    assert len(rig.store.pending()) == 1


def test_registration_and_notice_are_one_atomic_transaction(rig):
    def crash(key, text):
        rig.post(key, text)
        raise RuntimeError("simulated precommit death")

    with pytest.raises(RuntimeError):
        rig.register(post=crash)
    assert rig.store.db.execute("select count(*) from maintenance_recovery_proposals").fetchone()[0] == 0
    assert not rig.store.pending()
    assert rig.store.notices_since("0") == 0
    assert rig.register().result == "registered"


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", None),
        ("target", "99" * 32),
        ("sender", "not-key"),
        ("account", ""),
        ("group", "abc"),
        ("mid", "bad"),
        ("text", "approve"),
        ("text", "quoted !maintenance settle token"),
        ("authority", lambda: None),
    ],
)
def test_authentication_and_exact_thread_required(rig, field, value):
    rig.deliver()
    with pytest.raises(recovery.Invalid):
        rig.message(**{field: value})
    assert rig.service.get(rig.key)["state"] == "awaiting-consent"


def test_undelivered_notice_never_authorizes(rig):
    rig.register()
    with pytest.raises(recovery.Invalid, match="thread"):
        rig.message()


def test_first_effective_decision_and_replay_mismatch(rig):
    rig.deliver()
    assert rig.message().message == "consented"
    first = rig.service.get(rig.key)
    assert rig.message().result == "duplicate"
    assert rig.message(mid="77" * 32, sender="88" * 32).message == "duplicate-decision"
    assert rig.service.get(rig.key) == first
    with pytest.raises(recovery.Invalid, match="replay"):
        rig.message(text=f"!maintenance revoke {rig.key[:24]}")


def test_policy_change_does_not_change_native_payload_identity(rig):
    rig.deliver()
    rig.message()
    rig.policy = "88" * 32
    assert rig.message().result == "duplicate"


def test_authority_drift_inside_transaction_refuses(rig):
    rig.deliver()
    values = iter([POLICY, "88" * 32])
    with pytest.raises(recovery.Invalid, match="drift"):
        rig.message(authority=lambda: next(values))
    assert rig.service.get(rig.key)["state"] == "awaiting-consent"


@pytest.mark.parametrize("before", [True, False])
def test_revoke_tombstones_without_ask_or_native_changes(rig, before):
    rig.deliver()
    if not before:
        rig.message()
    assert rig.message("revoke", mid="77" * 32).message == "revoked"
    assert rig.message(mid="88" * 32).message == "history-only"
    with pytest.raises(recovery.Invalid, match="not-consented"):
        rig.permit()


def test_expired_reply_is_history_only_never_reactivates(rig):
    rig.deliver()
    rig.at += timedelta(hours=2)
    assert rig.message().message == "history-only"
    assert rig.service.get(rig.key)["state"] == "awaiting-consent"


def test_permit_cannot_exist_before_consent(rig):
    rig.deliver()
    with pytest.raises(recovery.Invalid, match="not-consented"):
        rig.permit()
    assert not (rig.root / "permits").exists()


def test_permit_durable_single_generation_and_restart(rig):
    rig.deliver()
    rig.message()
    assert rig.permit().result == "issued"
    first = (rig.root / "permits" / f"{rig.key}.json").read_bytes()
    rig.tick += 40_000_000_000
    rig.service = recovery.Service(
        rig.store, rig.root, clock=lambda: rig.at, boottime=lambda: rig.tick, boot=lambda: rig.boot
    )
    assert rig.permit().result == "issued"
    assert (rig.root / "permits" / f"{rig.key}.json").read_bytes() == first
    obj = json.loads(first)
    assert obj["initiate_before_boottime_ns"] - obj["issued_boottime_ns"] == 30_000_000_000
    assert obj["generation"] == 1
    assert (rig.root / "permits" / f"{rig.key}.json").stat().st_mode & 0o777 == 0o600


def test_publication_failure_retains_exact_candidate_no_second_permit(rig, monkeypatch):
    rig.deliver()
    rig.message()
    publish = rig.service.publish_permit

    def crash(*args):
        raise OSError("disk outage")

    monkeypatch.setattr(rig.service, "publish_permit", crash)
    with pytest.raises(OSError):
        rig.permit()
    candidate = rig.service.get(rig.key)["permit"]
    monkeypatch.setattr(rig.service, "publish_permit", publish)
    rig.permit()
    assert rig.service.get(rig.key)["permit"] == candidate


def test_three_generations_require_exact_enrolled_certificates(rig):
    rig.deliver()
    rig.message()
    rig.permit()
    for generation in (2, 3):
        rig.tick += 40_000_000_000
        cert = rig.certificate()
        rig.permit(cert)
        obj = rig.service.get(rig.key)["permit"]
        assert obj["generation"] == generation
        assert (rig.root / "generations" / rig.key / f"{generation}.json").exists()
    with pytest.raises(recovery.Invalid, match="generation-cap"):
        rig.permit(rig.certificate())


@pytest.mark.parametrize("suffix", ["consumed", "issuing", "receipt"])
def test_unknown_queue_consumption_forbids_renewal(rig, suffix):
    rig.deliver()
    rig.message()
    rig.permit()
    cert = rig.certificate()
    write(rig.queue / "recovery-v1" / f"{rig.key}.{suffix}.json", {})
    with pytest.raises(recovery.Invalid, match="consumption-unknown"):
        rig.permit(cert)
    assert rig.service.get(rig.key)["generation"] == 1


def test_reboot_and_policy_revocation_hold(rig):
    rig.deliver()
    rig.message()
    rig.permit()
    cert = rig.certificate()
    rig.boot = "22345678-1234-4234-8234-123456789abc"
    with pytest.raises(recovery.Invalid, match="boot-changed"):
        rig.permit(cert)
    rig.policy = None
    with pytest.raises(recovery.Invalid, match="authority"):
        rig.permit()


def test_descriptor_conflict_and_enrollment_drift(rig):
    rig.register()
    changed = msgspec.structs.replace(rig.obj, proof_set_sha256="99" * 32)
    with pytest.raises(recovery.Invalid):
        rig.service.register(
            recovery.Register(changed, changed.proof_set_sha256),
            uid=os.getuid(),
            pid=1,
            chunk_chars=2000,
            post=rig.post,
        )
    Path(next(iter(rig.graph["sources"]))).write_text("changed source")
    with pytest.raises(recovery.Invalid, match="source-drift"):
        rig.permit()


def test_material_redaction_or_missing_link_holds_registration(rig):
    write(rig.root / "enrollment.json", msgspec.structs.replace(rig.enrolled, descriptor_links={}))
    with pytest.raises(recovery.Invalid, match="unrepresentable"):
        rig.register()


def test_daemon_ingress_uses_actual_account_group_operator_and_sent_thread(tmp_path):
    from test_admind_r1 import Unit, inbound, run

    from heterodyne.marmot.control import ReplyTo

    unit = Unit(tmp_path / "unit")
    rig = Rig(tmp_path / "rig", unit.store)
    unit.daemon.maintenance_recovery = rig.service
    rig.deliver()
    event = msgspec.structs.replace(
        inbound(f"!maintenance settle {rig.key[:24]}", MID), reply_to=ReplyTo(THREAD)
    )
    try:
        # Wrong transport account, unrelated group, and non-policy sender cannot select.
        for wrong in (
            msgspec.structs.replace(event, account_id_hex="ff" * 32),
            msgspec.structs.replace(event, group_id_hex="aa" * 16),
            msgspec.structs.replace(
                event,
                message=msgspec.structs.replace(
                    event.message,
                    sender=msgspec.structs.replace(event.message.sender, account_id_hex="bb" * 32),
                ),
            ),
        ):
            run(unit.daemon.on_message(wrong))
            assert rig.service.get(rig.key)["state"] == "awaiting-consent"
        run(unit.daemon.on_message(event))
        assert rig.service.get(rig.key)["state"] == "consented"
        run(unit.daemon.on_message(event))
        assert unit.store.db.execute("SELECT count(*) FROM maintenance_recovery_messages").fetchone()[0] == 1
        assert unit.store.inbound_status(MID) is None  # separate durable dedup, never an agent prompt
        assert unit.tmux.pasted == []
        assert unit.store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
    finally:
        unit.store.close()


def test_daemon_ingress_revoked_current_membership_does_not_consent(tmp_path):
    from test_admind_r1 import Unit, inbound, run

    from heterodyne.marmot.control import ReplyTo

    unit = Unit(tmp_path / "unit")
    rig = Rig(tmp_path / "rig", unit.store)
    unit.daemon.maintenance_recovery = rig.service
    rig.deliver()
    unit.store.set("group_operators", "[]")
    event = msgspec.structs.replace(
        inbound(f"!maintenance settle {rig.key[:24]}", MID), reply_to=ReplyTo(THREAD)
    )
    try:
        run(unit.daemon.on_message(event))
        assert rig.service.get(rig.key)["state"] == "awaiting-consent"
    finally:
        unit.store.close()


def test_local_ctl_strict_decode_and_uid_gate(tmp_path):
    import asyncio

    from heterodyne.admind import ctl
    from heterodyne.admind.audit import Audit

    async def scenario():
        calls = []

        async def handle(req, peer):
            calls.append((req, peer))
            return recovery.Reply("ok", "read-only fixture")

        server = ctl.JsonSocketServer(
            tmp_path / "r.sock",
            handle,
            Audit(tmp_path / "audit.jsonl"),
            kind="maintenance-recovery",
            max_request=recovery.MAX_BYTES,
            max_reply=recovery.MAX_BYTES,
            request_type=recovery.Request,
            check=recovery.check,
            refused=lambda _: recovery.Reply("refused", "invalid"),
            failed=lambda: recovery.Reply("failed", "fixed"),
            describe=lambda _: {"operation": "fixture"},
        )
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(server.path))
            writer.write(
                b'{"type":"maintenance.recovery.status","proposal_sha256":"a","proposal_sha256":"b"}\n'
            )
            await writer.drain()
            result = await reader.readline()
            writer.close()
            await writer.wait_closed()
            assert json.loads(result)["result"] == "refused"
            assert calls == []
            result = await ctl.request(
                server.path,
                recovery.Status("a" * 64),
                10,
                reply_type=recovery.Reply,
                max_reply=recovery.MAX_BYTES,
            )
            assert result.result == "ok"
            assert calls[0][1].uid == os.getuid()
        finally:
            await server.close()

    asyncio.run(scenario())


def test_local_cli_uses_only_socket_with_ten_second_deadline(tmp_path, monkeypatch, capsys):
    from fakes.settings import make_settings

    from heterodyne.admind import cli, ctl, recovery_cli

    settings = make_settings(tmp_path)
    seen = []

    async def request(path, req, timeout, **kwargs):
        seen.append((path, req, timeout, kwargs))
        return recovery.Reply("ok", "fixture")

    monkeypatch.setattr(ctl, "request", request)
    args = cli.build_parser().parse_args(["maintenance-recovery", "status", "a" * 64])
    assert recovery_cli.command(settings, args) == 0
    assert seen[0][0] == settings.state_dir / recovery.SOCKET
    assert seen[0][2] == 10
    assert '"result":"ok"' in capsys.readouterr().out
    assert not (settings.state_dir / "admind.db").exists()


def test_foreign_peer_and_clock_rollback_refuse_without_mutation(rig):
    with pytest.raises(recovery.Invalid, match="peer"):
        rig.service.register(
            recovery.Register(rig.obj, rig.obj.proof_set_sha256),
            uid=os.getuid() + 1,
            pid=1,
            chunk_chars=2000,
            post=rig.post,
        )
    rig.deliver()
    rig.message()
    rig.at -= timedelta(seconds=1)
    with pytest.raises(recovery.Invalid, match="clock-rollback"):
        rig.permit()
    assert rig.service.get(rig.key)["generation"] == 0


def test_certificate_payload_mismatch_or_missing_supersession_holds(rig):
    rig.deliver()
    rig.message()
    rig.permit()
    certificate = rig.certificate()
    folder = rig.queue / "recovery-v1"
    (folder / f"{rig.key}.superseded-1.json").unlink()
    with pytest.raises(FileNotFoundError):
        rig.permit(certificate)
    assert rig.service.get(rig.key)["generation"] == 1


@pytest.mark.parametrize("value", [b'{"effect_count":NaN}', b"\xff", b"{}" + b" " * 262144])
def test_nonfinite_invalid_utf8_oversize_refused(value):
    with pytest.raises(recovery.Invalid):
        recovery.descriptor(value)


def test_symlink_or_hardlinked_receipt_refused(tmp_path):
    path = tmp_path / "file"
    write(path, {})
    other = tmp_path / "other"
    other.symlink_to(path)
    with pytest.raises(OSError):
        recovery.private_read(other)
    other.unlink()
    os.link(path, other)
    with pytest.raises(recovery.Invalid, match="unsafe"):
        recovery.private_read(path)


def test_invalid_boot_cannot_create_permit_candidate(tmp_path):
    rig = Rig(tmp_path)
    rig.deliver()
    rig.message()
    rig.boot = "unknown"
    with pytest.raises(recovery.Invalid, match="boot"):
        rig.permit()
    assert rig.service.get(rig.key)["generation"] == 0


def test_data_only_link_catalog_is_finitely_bounded(tmp_path):
    rig = Rig(tmp_path)
    local = recovery.decode(recovery.private_read(rig.root / "enrollment.json"), dict)
    local["descriptor_links"] = {f"{i:064x}": LINK for i in range(129)}
    write(rig.root / "enrollment.json", local)
    with pytest.raises(recovery.Invalid, match="enrollment"):
        rig.register()


def test_authenticated_history_cap_never_replaces_first_decision(tmp_path):
    rig = Rig(tmp_path)
    rig.deliver()
    for number in range(128):
        rig.message(mid=f"{number:064x}")
    before = rig.service.get(rig.key)
    with pytest.raises(recovery.Invalid, match="message-capacity"):
        rig.message(mid=f"{128:064x}")
    assert rig.service.get(rig.key) == before
    assert rig.store.db.execute("SELECT count(*) FROM maintenance_recovery_messages").fetchone()[0] == 128


def test_ordinary_notice_backlog_limits_recovery_registration(tmp_path):
    rig = Rig(tmp_path)
    for number in range(10):
        notice = "22222" + "23456789ab"[number]
        rig.store.insert_notice(notice, "ordinary", 1, 0, 4, 0, 1, rig.at.isoformat())
        rig.post(f"notice:{notice}:0", "held")
    with pytest.raises(recovery.Invalid, match="notice-capacity"):
        rig.register()
    assert rig.store.db.execute("SELECT count(*) FROM maintenance_recovery_proposals").fetchone()[0] == 0
