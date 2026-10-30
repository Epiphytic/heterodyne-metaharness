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


@pytest.fixture(autouse=True)
def synthetic_rendering_profile(monkeypatch):
    # Explicit synthetic endpoint profile, never a genuine installed config.
    original = authority._rendering
    monkeypatch.setattr(authority, "_rendering", lambda: ((), 2000))
    return original


def release(**changes):
    original = Release(
        "heterodyne-release.v1",
        "heterodyne_release.v1",
        "abcd",
        "execution1",
        "nonce1",
        TARGET,
        Artifacts("a" * 40, "b" * 40, H, H, H, H, H, H),
        AuthorityPins(H, H, H, H, authority.CORE_SHA256, H, H, H, H, H, H, H),
        H,
        H,
        ("https://github.com/Epiphytic/hermes-maintenance/blob/" + "a" * 40 + "/docs/design.md",),
        ("No deviations.",),
        FLAGS,
        1,
        100,
        Recovery(H, 200, "safe-stop", "terminal1"),
        "receipt1",
        "a" * 40,
        "docs/release.json",
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
    with pytest.raises(InvalidAction, match="one-use-or-target-conflict"):
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
    native = authority._core()._native("abcd", dbpath)
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
            "answers": [
                {key: row[key] for key in authority._core().PUBLIC_ANSWER} for row in native["answers"]
            ],
        },
    }
    return r, native, response


def test_actual_core_synthetic_native_sql_positive(tmp_path):
    r, native, response = native_fixture(tmp_path)
    authority._core()._validate(r, native, response, {"bel": "d" * 64}, authority._bindings)
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
    with pytest.raises(authority._core().AuthorityHold):
        authority._core()._validate(r, native, response, {"bel": "d" * 64}, authority._bindings)


def test_native_capture_fixed_reader_seam_and_two_sweeps(tmp_path, monkeypatch):
    # Synthetic native transport, actual source/interpreter hashing and inventory.
    r, native, response = native_fixture(tmp_path)
    core = authority._core()
    monkeypatch.setattr(authority.time, "time_ns", lambda: 2)
    monkeypatch.setattr(core, "_native", lambda ask, db: native)
    monkeypatch.setattr(authority.runtime, "fetch", lambda ask, executable: json.dumps(response).encode())
    monkeypatch.setattr(authority, "_operators_from", lambda host, queue: {"bel": "d" * 64})
    original_read = core._read
    dummy = b"synthetic protected policy bytes"

    def read(path, limit=16 * 1024 * 1024):
        if path in (authority.HOST_POLICY, authority.QUEUE_POLICY):
            return dummy
        return original_read(path, limit=limit)

    monkeypatch.setattr(core, "_read", read)
    here = authority.Path(authority.__file__).parent
    # Fixed deployment-runtime roots become disposable SOURCE-only fixture roots.
    monkeypatch.setattr(authority, "ADMIND", str(here / "deployment.py"))
    monkeypatch.setattr(authority.runtime, "PYTHON", authority.Path(authority.sys.executable).resolve())
    monkeypatch.setattr(authority.runtime, "SITE", here)
    monkeypatch.setattr(authority.runtime, "SOURCE", here.parent)
    paths = (
        here / "deployment_authority.py",
        here / "deployment.py",
        here / "codec.py",
        here / "contracts.py",
        here / "resources/hermes_native_authority.py.txt",
        authority.Path(authority.ADMIND),
        authority.Path(authority.sys.executable).resolve(),
        here / "deployment_runtime.py",
        authority.runtime.PYTHON,
    )
    pins = AuthorityPins(
        *[authority.runtime.stream_hash(path) for path in paths],
        authority.runtime.tree_digest(here),
        authority.runtime.tree_digest(here.parent),
        authority.runtime.tree_digest(here.parent),
    )
    r = release(authority_sources=pins)
    native["ask"]["body"] = authority.approval_body(r, URL, H)
    receipt = authority._capture(r, (TARGET,))
    assert receipt["release_sha256"] == authority.digest(r)
    import tempfile

    with tempfile.TemporaryDirectory(prefix=".interpreter-alias-", dir=authority.Path.cwd()) as directory:
        alias = authority.Path(directory) / "python"
        executable = authority.Path(authority.sys.executable).resolve()
        alias.symlink_to(executable)
        seen = []

        def switched(ask, selected):
            alias.unlink()
            alias.symlink_to("/bin/false")
            seen.append(selected)
            return json.dumps(response).encode()

        with monkeypatch.context() as local:
            local.setattr(authority.runtime, "PYTHON", alias)
            local.setattr(authority.runtime, "fetch", switched)
            authority._capture(r, (TARGET,))
        assert seen == [executable]
    with pytest.raises(authority.AuthorityHold, match="source drift"):
        authority._capture(
            release(authority_sources=msgspec.structs.replace(pins, admind_sha256=H)), (TARGET,)
        )
    # Both role pins remain mandatory when aliases share an executable/file.
    for field in ("interpreter_sha256", "contract_sha256"):
        with pytest.raises(authority.AuthorityHold, match="source drift"):
            authority._capture(
                release(authority_sources=msgspec.structs.replace(pins, **{field: H})), (TARGET,)
            )
    native["inbound"][0]["sender_key"] = "e" * 64
    with pytest.raises(authority.AuthorityHold, match="authentication-refused"):
        authority._capture(r, (TARGET,))
    native["inbound"][0]["sender_key"] = "d" * 64
    calls = 0

    def drift(path, limit=16 * 1024 * 1024):
        nonlocal calls
        if path == authority.HOST_POLICY:
            calls += 1
            return dummy if calls == 1 else b"changed policy"
        return read(path, limit=limit)

    monkeypatch.setattr(core, "_read", drift)
    with pytest.raises(authority.AuthorityHold, match="policy drift"):
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


def test_packet_url_is_bound_to_release(tmp_path):
    r, native, response = native_fixture(tmp_path)
    wrong = URL.replace("a" * 40, "b" * 40)
    with pytest.raises(InvalidAction, match="context"):
        authority.approval_body(r, wrong, H)
    native["ask"]["body"] = native["ask"]["body"].replace(URL, wrong)
    with pytest.raises(InvalidAction, match="approval-context"):
        authority._core()._validate(r, native, response, {"bel": "d" * 64}, authority._bindings)


@pytest.mark.parametrize("group", [None, 12, [], {}])
def test_malformed_native_group_is_controlled_hold(tmp_path, group):
    r, native, _ = native_fixture(tmp_path)
    native["kv"][0]["value"] = group
    with pytest.raises(authority._core().AuthorityHold):
        authority._bindings(r, native)


def test_cancel_unknown_and_alias_contract(tmp_path):
    journal = _FixtureJournal(tmp_path / "journal.db")
    with pytest.raises(InvalidAction, match="unknown"):
        journal.cancel("unknown")
    journal.close()
    for alias in ("Hermes", "x" * 65, "a.b"):
        target = msgspec.structs.replace(TARGET, alias=alias)
        with pytest.raises(InvalidAction):
            decode_release(encode_record(release(target=target)), (target,))


def test_lazy_core_failure_is_controlled(monkeypatch):
    monkeypatch.setattr(authority, "_loaded_core", None)

    def unavailable():
        raise OSError("not installed")

    monkeypatch.setattr(authority, "_load_core", unavailable)
    with pytest.raises(authority.AuthorityHold, match="source-unavailable"):
        authority._core()


def test_policy_parser_consumes_captured_bytes(monkeypatch):
    core = authority._core()
    seen = []

    def parser(host, queue):
        seen.extend([host.read_bytes(), queue.read_bytes()])
        return {"bel": "d" * 64}

    monkeypatch.setattr(core, "_operators", parser)
    assert authority._operators_from(b"captured host", b"captured queue") == {"bel": "d" * 64}
    assert seen == [b"captured host", b"captured queue"]


def test_actual_interpreter_streaming_bound():
    path = authority.Path(authority.sys.executable).resolve()
    assert authority.runtime.stream_hash(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(InvalidAction, match="unsafe-runtime-file"):
        authority.runtime.stream_hash(path, limit=1)


def test_isolated_runtime_rejects_bytecode_and_detects_module_changes(monkeypatch):
    import os
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix=".runtime-fixture-", dir=Path.cwd()) as directory:
        root = Path(directory)
        site, source = root / "site", root / "source"
        site.mkdir()
        (site / "dependency.py").write_text("value = 1\n")
        (site / "startup.pth").write_text("import sys; sys.exit(99)\n")
        package = source / "heterodyne/admind"
        package.mkdir(parents=True)
        cli = package / "cli.py"
        cli.write_text('def main():\n    print(\'{"result":"synthetic"}\')\n    return 0\n')
        monkeypatch.setattr(authority.runtime, "SITE", site)
        monkeypatch.setattr(authority.runtime, "SOURCE", source)
        monkeypatch.setattr(authority.runtime, "PYTHON", Path(authority.sys.executable).resolve())
        fifo = root / "nonregular"
        os.mkfifo(fifo)
        with pytest.raises(InvalidAction, match="unsafe-runtime-file"):
            authority.runtime.stream_hash(fifo)
        before = authority.runtime.tree_digest(source)
        assert json.loads(authority.runtime.fetch("abcd", Path(authority.sys.executable).resolve())) == {
            "result": "synthetic"
        }
        assert not list(source.rglob("*.pyc"))
        cli.write_text(cli.read_text() + "# altered module without wrapper change\n")
        assert authority.runtime.tree_digest(source) != before
        # A normal import may create canonical caches. They do not alter the
        # source pin, and a poisoned canonical cache is not read by the child.
        import importlib.util
        import py_compile

        current = authority.runtime.tree_digest(source)
        poison = root / "poison.py"
        poison.write_text("raise RuntimeError('untrusted cache executed')\n")
        cache = Path(importlib.util.cache_from_source(str(cli)))
        cache.parent.mkdir()
        py_compile.compile(str(poison), cfile=str(cache), doraise=True)
        assert authority.runtime.tree_digest(source) == current
        assert json.loads(authority.runtime.fetch("abcd", Path(authority.sys.executable).resolve())) == {
            "result": "synthetic"
        }
        # Execution uses the once-selected path, not a fresh resolve of the
        # mutable runtime alias between source hashing and invocation.
        monkeypatch.setattr(authority.runtime, "PYTHON", Path("/bin/false"))
        assert json.loads(authority.runtime.fetch("abcd", Path(authority.sys.executable).resolve())) == {
            "result": "synthetic"
        }
        (package / "cli.pyc").write_bytes(b"untrusted cached executable")
        with pytest.raises(InvalidAction, match="bytecode"):
            authority.runtime.tree_digest(source)


def test_checker_resource_exception_is_exact_and_keeps_denylist(tmp_path):
    import importlib.util
    from pathlib import Path

    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        "_deployment_checker", root / "scripts/check_install_agnostic.py"
    )
    assert spec and spec.loader
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    rel = "src/heterodyne/actions/resources/hermes_native_authority.py.txt"
    resource = tmp_path / rel
    resource.parent.mkdir(parents=True)
    raw = (root / rel).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == authority.CORE_SHA256
    resource.write_bytes(raw)
    assert not checker.scan_file(tmp_path, rel, None, [])
    assert any("local-denylist" in hit for hit in checker.scan_file(tmp_path, rel, None, ["_operators"]))
    resource.write_bytes(raw + b"\n# changed resource\n")
    assert any("home-path" in hit for hit in checker.scan_file(tmp_path, rel, None, []))
    other = tmp_path / "unrelated.txt"
    other.write_bytes(raw)
    assert any("home-path" in hit for hit in checker.scan_file(tmp_path, "unrelated.txt", None, []))


@pytest.mark.parametrize("separator", ["\n", "\r", "\t", "\x1b", "\x85", "\u2028", "\u2029", "\u202e"])
def test_deviation_display_injection_refused(separator):
    r = release(deviations=("None" + separator + "Target: other",))
    with pytest.raises(InvalidAction, match="unsafe-text|explicit-deviations"):
        decode_release(encode_record(r), (TARGET,))
    with pytest.raises(InvalidAction, match="unsafe-text|explicit-deviations"):
        authority.approval_body(r, URL, H)


def test_journal_busy_is_typed_and_has_no_effect(tmp_path):
    path = tmp_path / "journal.db"
    first, second = _FixtureJournal(path), _FixtureJournal(path)
    r, effects = release(), FakeEffects(H)
    first.enroll(r, (TARGET,), 2)
    second.db.execute("PRAGMA busy_timeout=0")
    first.db.execute("BEGIN IMMEDIATE")
    try:
        for operation in (
            lambda: second.enroll(r, (TARGET,), 3),
            lambda: second.advance(r, (TARGET,), effects, 3),
            lambda: second.reconcile(r, H),
            lambda: second.cancel(r.execution_id),
        ):
            with pytest.raises(InvalidAction, match="journal-unavailable"):
                operation()
        assert not effects.calls
    finally:
        first.db.rollback()
        first.close()
        second.close()


def test_native_binding_preserves_auth_refusal_and_types_malformed(tmp_path):
    r, native, _ = native_fixture(tmp_path)
    native["ask"]["status"] = "pending"
    with pytest.raises(authority._core().AuthorityHold, match="exact release deployment question"):
        authority._bindings(r, native)
    with pytest.raises(authority.AuthorityHold, match="malformed-native-context"):
        authority._bindings(r, {})


def test_core_fifo_refuses_without_blocking():
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix=".core-fifo-", dir=Path.cwd()) as directory:
        root = Path(directory)
        resources = root / "resources"
        resources.mkdir()
        os.mkfifo(resources / "hermes_native_authority.py.txt")
        code = (
            "from heterodyne.actions import deployment_authority as a; "
            + "a.__file__ = "
            + repr(str(root / "deployment_authority.py"))
            + "; a._load_core()"
        )
        result = subprocess.run(
            [authority.sys.executable, "-c", code], capture_output=True, timeout=3, check=False
        )
        assert result.returncode != 0
        assert b"unsafe-authority-source" in result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        "docs/reviews/2026-10-29-deployment-step1-code/r1/actual-review.md",
        "docs/reviews/2026-10-29-deployment-step1-code/r1/review-receipt.json",
        "docs/reviews/2026-10-29-deployment-step1-code/r2/review-receipt.json",
    ],
)
def test_review_evidence_exception_preserves_raw_bytes_and_denylist(tmp_path, relative):
    import importlib.util
    from pathlib import Path

    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        "_evidence_checker", root / "scripts/check_install_agnostic.py"
    )
    assert spec and spec.loader
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    raw = (root / relative).read_bytes()
    output = tmp_path / relative
    output.parent.mkdir(parents=True)
    output.write_bytes(raw)
    assert not checker.scan_file(tmp_path, relative, None, [])
    assert any("local-denylist" in hit for hit in checker.scan_file(tmp_path, relative, None, ["/home/"]))
    output.write_bytes(raw + b"\n")
    assert any("home-path" in hit for hit in checker.scan_file(tmp_path, relative, None, []))
