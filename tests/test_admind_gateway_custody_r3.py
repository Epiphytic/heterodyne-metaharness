"""Exact immutable-publisher crash recovery; synthetic files and authority only."""

import os

import pytest
from test_admind_gateway_custody_permits import certificate, issue

pytest_plugins = ("test_admind_gateway_custody_registration", "test_admind_gateway_custody_permits")


@pytest.mark.parametrize("target", ["generation1", "generation2", "tombstone"])
def test_readback_repairs_own_link_before_unlink_alias(issued_rig, target):
    _, key, service, _, enrolled, _ = issued_rig
    first = issue(service, key)
    raw = first
    generation = 1
    if target != "generation1":
        raw = issue(service, key, certificate(issued_rig, first))
        generation = 2
    path = enrolled.root / "generations" / key / f"{generation}.json"
    if target == "tombstone":
        path = path.parent / "1.superseded.json"
    alias = path.parent / (".recovery-" + "a" * 32)
    os.link(path, alias)
    assert path.stat().st_nlink == 2
    assert issue(service, key) == raw
    assert path.stat().st_nlink == 1
    assert not alias.exists()


def test_complete_readback_retries_directory_durability(issued_rig, monkeypatch):
    _, key, service, _, _, _ = issued_rig
    first = issue(service, key)
    raw = issue(service, key, certificate(issued_rig, first))
    real = os.fsync
    calls = []

    def fsync(fd):
        import stat

        if stat.S_ISDIR(os.fstat(fd).st_mode):
            calls.append(fd)
            if len(calls) == 1:
                raise OSError("synthetic directory fsync failure")
        return real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError, match="directory fsync"):
        issue(service, key)
    assert issue(service, key) == raw
    assert len(calls) >= 2
