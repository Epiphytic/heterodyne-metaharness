"""Pure explicit contexts keep legacy redaction behavior and global callers independent."""

import subprocess
import types

import pytest

from heterodyne.admind import redact as current


@pytest.fixture
def baseline():
    # Exact approved source, not a second hand-maintained redaction implementation.
    source = subprocess.check_output(
        [
            "git",
            "show",
            "07637745fe75dc614109a0b8ba72d3489f262a97:src/heterodyne/admind/redact.py",
        ],
        text=True,
    )
    module = types.ModuleType("approved_redaction_baseline")
    exec(compile(source, "<approved-redaction-baseline>", "exec"), module.__dict__)  # noqa: S102 - exact pinned source
    return module


@pytest.mark.parametrize(
    "value",
    [
        "ordinary text with\nlayout\tand punctuation",
        "/private/account/name and /private/account/name.bak and /private/account/namex",
        "-----BEGIN " + "PRIVATE KEY-----\nfixture only\n-----END " + "PRIVATE KEY-----",
        "a" * 64,
        "x" + "f" * 63 + "\x08" + "a",
        "npub1" + "q" * 58,
        "\x00\x01\x7f\x80\x9f end",
        "f" * 64 + "-----BEGIN " + "PRIVATE KEY----- unclosed",
        "token ghp_" + "a" * 40,
    ],
)
def test_explicit_context_matches_approved_kernel(baseline, value):
    directories = ("/private/account", "/private/account/name")
    baseline.set_login_dirs(directories)
    expected = baseline.redact(value)
    assert current.redact_with_dirs(value, directories) == expected
    assert current.redact_with_dirs(expected, directories) == expected


def test_interleaved_explicit_context_does_not_mutate_global_callers(baseline):
    previous = current._login_dirs
    try:
        current.set_login_dirs(("/global/login",))
        baseline.set_login_dirs(("/global/login",))
        value = "/global/login /first/login /second/login"
        before = current.redact(value)
        assert before == baseline.redact(value)
        for _ in range(3):
            assert current.redact_with_dirs(value, ("/first/login",)) == (
                "/global/login <redacted login dir> /second/login"
            )
            assert current.redact_with_dirs(value, ("/second/login",)) == (
                "/global/login /first/login <redacted login dir>"
            )
            assert current.redact(value) == before
        assert list(current._spans(value)) == list(baseline._spans(value))
        assert current.redact_continuation("f" * 32, "f" * 32 + " tail") == (
            baseline.redact_continuation("f" * 32, "f" * 32 + " tail")
        )
    finally:
        current._login_dirs = previous


def test_unstable_backstop_uses_the_same_legacy_loop(baseline, monkeypatch):
    monkeypatch.setattr(baseline, "_pass", lambda text: text + "x")
    monkeypatch.setattr(current, "_pass", lambda text: text + "x")
    assert current.redact("fixture") == baseline.redact("fixture") == current.UNSTABLE
    monkeypatch.setattr(current, "_pass_with_dirs", lambda text, directories: text + "x")
    assert current.redact_with_dirs("fixture", ()) == baseline.UNSTABLE
