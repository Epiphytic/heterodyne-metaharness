"""Disposable fake effects and synthetic native SQL/CLI evidence, never live approval."""

import hashlib
import json
import sqlite3

import msgspec
import pytest

from heterodyne.actions import deployment_authority as authority
from heterodyne.actions.codec import InvalidAction, encode_record
from heterodyne.actions.deployment import (
    FLAGS,
    STEPS,
    Artifacts,
    AuthorityPins,
    FakeEffects,
    Recovery,
    Release,
    Target,
    _FixtureJournal,
    decode_release,
    open_host_executor,
)

H = "a" * 64
TARGET = Target("hermes", "hermes-prod", "revision1", "service", "release", "units", "state")
URL = "https://github.com/Epiphytic/heterodyne-metaharness/blob/" + "a" * 40 + "/docs/release.json"


def release(**changes):
    original = Release(
        "heterodyne-release.v1",
        "heterodyne_release.v1",
        "abcd",
        "execution1",
        "nonce1",
        TARGET,
        Artifacts("a" * 40, "b" * 40, H, H, H, H, H, H),
        AuthorityPins(H, H, H, H, authority.CORE_SHA256, H, H),
        H,
        H,
        ("https://github.com/Epiphytic/hermes-maintenance/blob/" + "a" * 40 + "/docs/design.md",),
        ("No deviations.",),
        FLAGS,
        1,
        100,
        Recovery(H, 200, "safe-stop", "terminal1"),
        "receipt1",
    )
    return msgspec.structs.replace(original, **changes)


def test_closed_schema_and_protected_mapping():
    r = release()
    assert decode_release(encode_record(r), (TARGET,)) == r
    raw = json.loads(encode_record(r))
    raw["argv"] = ["rm", "something"]
    with pytest.raises(InvalidAction):
        decode_release(json.dumps(raw).encode(), (TARGET,))
    with pytest.raises(InvalidAction, match="mapping"):
        decode_release(encode_record(r), (msgspec.structs.replace(TARGET, unit_id="other"),))
    with pytest.raises(InvalidAction, match="fixed-recipe"):
        decode_release(encode_record(release(flags=("held-acceptance",))), (TARGET,))


def test_recipe_and_restart(tmp_path):
    r, effects = release(), FakeEffects(H)
    journal = _FixtureJournal(tmp_path / "journal.db")
    journal.enroll(r, (TARGET,), 2)
    for ordinal, step in enumerate(STEPS):
        assert journal.advance(r, (TARGET,), effects, 3) == (
            "succeeded" if ordinal == len(STEPS) - 1 else "executing"
        )
        assert effects.calls[-1] == step
    assert effects.calls == list(STEPS)
    journal.close()
    journal = _FixtureJournal(tmp_path / "journal.db")
    journal.enroll(r, (TARGET,), 4)
    with pytest.raises(InvalidAction, match="forward-hold"):
        journal.advance(r, (TARGET,), effects, 4)
    assert effects.calls == list(STEPS)
    journal.close()


def test_intent_before_effect_and_uncertain_never_retry(tmp_path):
    r, effects = release(), FakeEffects(H)
    path = tmp_path / "journal.db"
    journal = _FixtureJournal(path)
    journal.enroll(r, (TARGET,), 2)
    assert journal.advance(r, (TARGET,), effects, 3, crash_after_effect=True) == "uncertain"
    journal.close()
    journal = _FixtureJournal(path)
    with pytest.raises(InvalidAction, match="forward-hold"):
        journal.advance(r, (TARGET,), effects, 4)
    with pytest.raises(InvalidAction, match="uncertain-hold"):
        journal.reconcile(r, H)
    assert effects.calls == [STEPS[0]]
    journal.reconcile(r, effects.state)
    journal.advance(r, (TARGET,), effects, 4)
    assert effects.calls == list(STEPS[:2])
    journal.close()


def test_target_exclusion_one_use_cancel_expiry_and_drift(tmp_path):
    r, effects = release(), FakeEffects(H)
    first = _FixtureJournal(tmp_path / "journal.db")
    second = _FixtureJournal(tmp_path / "journal.db")
    first.enroll(r, (TARGET,), 2)
    with pytest.raises(sqlite3.IntegrityError):
        second.enroll(release(approval_id="defg", execution_id="execution2", nonce="nonce2"), (TARGET,), 2)
    with pytest.raises(InvalidAction, match="payload-drift"):
        second.enroll(release(receipt_id="receipt2"), (TARGET,), 2)
    effects.state = "b" * 64
    with pytest.raises(InvalidAction, match="unexpected-transition"):
        first.advance(r, (TARGET,), effects, 3)
    effects.state = H
    with pytest.raises(InvalidAction, match="forward-window"):
        first.advance(r, (TARGET,), effects, 100)
    second.cancel(r.execution_id)
    with pytest.raises(InvalidAction, match="forward-hold"):
        first.advance(r, (TARGET,), effects, 3)
    assert not effects.calls
    first.close()
    second.close()


def native_fixture(tmp_path):
    r = release()
    dbpath = tmp_path / "native.db"
    db = sqlite3.connect(dbpath)
    db.executescript("""
        CREATE TABLE asks(ask_id TEXT,kind TEXT,status TEXT,question_form TEXT,truncated INTEGER,
                          card_parts INTEGER,title TEXT,body TEXT);
        CREATE TABLE ask_answers(ask_id TEXT,seq INTEGER,kind TEXT,operator TEXT,message_id TEXT,
                                 text TEXT,at TEXT,choice TEXT);
        CREATE TABLE outbox(key TEXT,status TEXT,delivery_group TEXT,ownership_kind TEXT,message_id TEXT);
        CREATE TABLE inbound(message_id TEXT,status TEXT,received_at TEXT,reply_to TEXT,sender_key TEXT);
        CREATE TABLE kv(key TEXT,value TEXT);
    """)
    db.execute(
        "INSERT INTO asks VALUES(?,?,?,?,?,?,?,?)",
        (
            r.approval_id,
            "question",
            "answered",
            "yes-no",
            0,
            1,
            "Exact release",
            authority.approval_body(r, URL, H),
        ),
    )
    db.execute(
        "INSERT INTO ask_answers VALUES(?,?,?,?,?,?,?,?)",
        (r.approval_id, 1, "answer", "bel", "b" * 64, "yes", "time", "yes"),
    )
    db.execute("INSERT INTO outbox VALUES(?,?,?,?,?)", ("ask:abcd:0", "sent", "ask:abcd", "system", "c" * 64))
    db.execute("INSERT INTO inbound VALUES(?,?,?,?,?)", ("b" * 64, "done", "time", "c" * 64, "d" * 64))
    db.executemany(
        "INSERT INTO kv VALUES(?,?)", (("group_id_hex", H), ("group_operators", json.dumps(["d" * 64])))
    )
    db.commit()
    db.close()
    native = authority.core._native("abcd", dbpath)
    response = {
        "result": "ok",
        "message": "answered",
        "ask": {
            "summary": {
                "ask_id": "abcd",
                "kind": "question",
                "status": "answered",
                "title": "Exact release",
                "delivered": True,
                "answer_count": 1,
            },
            "answers": [{key: row[key] for key in authority.core.PUBLIC_ANSWER} for row in native["answers"]],
        },
    }
    return r, native, response


def test_actual_core_synthetic_native_sql_positive(tmp_path):
    r, native, response = native_fixture(tmp_path)
    authority.core._validate(r, native, response, {"bel": "d" * 64}, authority._bindings)
    with pytest.raises(InvalidAction, match="HOLD"):
        open_host_executor()


@pytest.mark.parametrize(
    "change", ["sender", "ownership", "context", "membership", "conflict", "cli", "heart"]
)
def test_native_auth_refusals(tmp_path, change):
    r, native, response = native_fixture(tmp_path)
    if change == "sender":
        native["inbound"][0]["sender_key"] = "e" * 64
    elif change == "ownership":
        native["cards"][0]["ownership_kind"] = "request"
    elif change == "context":
        r = release(deviations=("Changed deviations",))
    elif change == "membership":
        native["kv"][1]["value"] = json.dumps(["e" * 64])
    elif change == "conflict":
        native["answers"][0]["choice"] = "no"
    elif change == "cli":
        response["ask"]["summary"]["answer_count"] = 2
    else:
        native["answers"][0]["choice"] = "always"
    with pytest.raises(authority.core.AuthorityHold):
        authority.core._validate(r, native, response, {"bel": "d" * 64}, authority._bindings)


def test_native_capture_fixed_reader_seam_and_two_sweeps(tmp_path, monkeypatch):
    # Only the native SQL source and CLI transport are synthetic here. Production
    # paths/readers are fixed in source, never selected by an incoming request.
    r, native, response = native_fixture(tmp_path)
    monkeypatch.setattr(authority.time, "time_ns", lambda: 2)
    monkeypatch.setattr(authority.core, "_native", lambda ask, db: native)
    monkeypatch.setattr(authority.core, "_fetch", lambda ask, cli: response)
    monkeypatch.setattr(authority.core, "_operators", lambda host, queue: {"bel": "d" * 64})
    original_read = authority.core._read
    dummy = b"synthetic protected policy or endpoint bytes"

    def read(path, limit=16 * 1024 * 1024):
        if path in (authority.HOST_POLICY, authority.QUEUE_POLICY) or str(path) == authority.ADMIND:
            return dummy
        return original_read(path, limit=limit)

    monkeypatch.setattr(authority.core, "_read", read)
    here = authority.Path(authority.__file__).parent
    pins = AuthorityPins(
        *[
            hashlib.sha256(read(path)).hexdigest()
            for path in (
                here / "deployment_authority.py",
                here / "deployment.py",
                here / "codec.py",
                here / "contracts.py",
                here / "resources/hermes_native_authority.py.txt",
                authority.Path(authority.ADMIND),
                authority.Path(authority.sys.executable).resolve(),
            )
        ]
    )
    r = release(authority_sources=pins)
    native["ask"]["body"] = authority.approval_body(r, URL, H)
    receipt = authority._capture(r, (TARGET,))
    assert receipt["release_sha256"] == authority.digest(r)
    with pytest.raises(authority.core.AuthorityHold, match="source drift"):
        authority._capture(
            release(authority_sources=msgspec.structs.replace(pins, admind_sha256=H)), (TARGET,)
        )
    calls = 0

    def drift(path, limit=16 * 1024 * 1024):
        nonlocal calls
        if path == authority.HOST_POLICY:
            calls += 1
            return dummy if calls == 1 else b"changed policy"
        return read(path, limit=limit)

    monkeypatch.setattr(authority.core, "_read", drift)
    with pytest.raises(authority.core.AuthorityHold, match="policy drift"):
        authority._capture(r, (TARGET,))


@pytest.mark.parametrize(
    "changes",
    [
        {"deadline_ns": 1},
        {"deviations": ()},
        {"design_links": ("https://example.com/design.md",)},
        {"outage": "permit"},
        {"nonce": "../path"},
        {"flags": ("zero-new-quarantine", "held-acceptance")},
    ],
)
def test_bounded_release_refusals(changes):
    with pytest.raises(InvalidAction):
        decode_release(encode_record(release(**changes)), (TARGET,))


def test_duplicate_fields_refused():
    raw = encode_record(release())
    with pytest.raises(InvalidAction, match="duplicate"):
        decode_release(b'{"schema":"heterodyne-release.v1",' + raw[1:], (TARGET,))
