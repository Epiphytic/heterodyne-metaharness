"""Actual private codecs/FD and isolated child controls, not installed isolation."""

import hashlib
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import msgspec
import pytest
import test_deployment_producer_process as process_tests

from heterodyne.actions import deployment_producer_process as producer
from heterodyne.actions import deployment_profile_check as checker
from heterodyne.actions.codec import MAX_BYTES, InvalidAction, encode_record

inputs = process_tests.inputs
published = process_tests.published


def test_pure_profile_check_uses_bound_packet_without_effect_calls(inputs, monkeypatch):
    original, canonical, _, profile_fd, *_ = inputs
    monkeypatch.setattr(producer, "_prepare", lambda *args: pytest.fail("card preparation reached"))
    monkeypatch.setattr(producer, "_derive", lambda *args: pytest.fail("card rendering reached"))
    with monkeypatch.context() as guarded:
        guarded.setattr(os, "open", lambda *args: pytest.fail("path opened"))
        raw = checker._profile_check(original, profile_fd)
    assert json.loads(raw) == {
        "schema": "heterodyne-owned-profile-check.v1",
        "operation_original_sha256": hashlib.sha256(original).hexdigest(),
        "packet_sha256": hashlib.sha256(canonical).hexdigest(),
    }
    assert b"card" not in raw and b"approved" not in raw


@pytest.mark.parametrize("change", ["original", "large", "canonical", "target", "unknown", "writable"])
def test_pure_profile_check_refuses_exact_profile_or_packet_drift(inputs, change):
    original, _, profile, profile_fd, _, _, path = inputs
    if change == "original":
        original += b" "
    elif change == "large":
        original = b"x" * (MAX_BYTES + 1)
    else:
        path.chmod(0o600)
        if change == "canonical":
            profile = msgspec.structs.replace(profile, packet_sha256="0" * 64)
        elif change == "target":
            profile = msgspec.structs.replace(profile, targets=())
        raw = encode_record(profile)
        if change == "unknown":
            raw = raw[:-1] + b',"approved":true}'
        path.write_bytes(raw)
        if change != "writable":
            path.chmod(0o400)
    with pytest.raises(InvalidAction):
        checker._profile_check(original, profile_fd)


def test_pure_profile_check_retained_fd_change_is_causal(inputs, monkeypatch):
    original, _, _, profile_fd, *_ = inputs
    real = checker._bound_operation
    def change(raw, profile):
        result = real(raw, profile)
        before = os.fstat(profile_fd)
        os.utime(profile_fd, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        assert os.fstat(profile_fd).st_mode == before.st_mode
        return result
    monkeypatch.setattr(checker, "_bound_operation", change)
    with pytest.raises(InvalidAction, match="^profile-check-descriptor-changed$"):
        checker._profile_check(original, profile_fd)


def child(inputs, *, original=None, extra=""):
    """Fixture-owned child invocation, not an enrolled owning launcher."""
    raw, _, _, profile_fd, *_ = inputs
    source = str(Path(__file__).resolve().parents[1] / "src")
    # Fixture-only remap. Production owning launcher must own the fixed slots.
    duplicate = os.dup(profile_fd)
    try:
        code = (
            "import os,sys;sys.path[:0]=" + repr([source, sysconfig.get_paths()["purelib"]]) + ";"
            "from heterodyne.actions import deployment_profile_check as p, deployment_producer_process as c;"
            "c._prepare=lambda *a: (_ for _ in ()).throw(RuntimeError('unexpected-prepare'));"
            "c._derive=lambda *a: (_ for _ in ()).throw(RuntimeError('unexpected-render'));"
            f"os.dup2({duplicate},3);"
            "os.open=lambda *a: (_ for _ in ()).throw(RuntimeError('unexpected-open'));"
            + (extra + ";" if extra else "") + "sys.exit(p._entry())"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", code], input=raw if original is None else original,
            pass_fds=(duplicate,), env={"LC_ALL": "C.UTF-8"}, capture_output=True,
            timeout=10, check=False,
        )
    finally:
        os.close(duplicate)
    return result


def test_actual_pure_check_child_needs_no_archive_commit_and_does_not_call_effects(inputs):
    result = child(inputs)
    assert (result.returncode, result.stderr) == (0, b"")
    assert json.loads(result.stdout)["packet_sha256"] == hashlib.sha256(inputs[1]).hexdigest()


@pytest.mark.parametrize("change", ["original", "large", "argv", "exception", "closed"])
def test_actual_pure_check_child_fixed_refusal_no_traceback(inputs, change):
    original, extra = None, ""
    if change == "original":
        original = inputs[0] + b" "
    elif change == "large":
        original = b"x" * (MAX_BYTES + 1)
    elif change == "argv":
        extra = "sys.argv.append('untrusted-secret-marker')"
    elif change == "exception":
        extra = "p._profile_check=lambda *a: (_ for _ in ()).throw(RuntimeError('untrusted-secret-marker'))"
    else:
        extra = "os.close(3)"
    result = child(inputs, original=original, extra=extra)
    assert (result.returncode, result.stdout, result.stderr) == (2, b"", b"owned-profile-check-refused\n")
