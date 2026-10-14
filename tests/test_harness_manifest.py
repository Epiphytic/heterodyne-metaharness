"""Plan 4c Task 2: the manifest's canonical encoding and digest (r16 §7 Authentication)."""

import dataclasses
import hashlib
import json
from collections.abc import Callable

import pytest
from harness_env import sample_manifest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.harness.resources import store_core as sc


def test_the_digest_is_the_sha256_of_the_canonical_encoding() -> None:
    m = sample_manifest()
    assert sc.digest(m) == "sha256:" + hashlib.sha256(sc.canonical(m)).hexdigest()


@given(st.permutations(range(5)))
def test_entry_order_does_not_change_the_digest(order: list[int]) -> None:
    m = sample_manifest()
    shuffled = dataclasses.replace(m, entries=tuple(m.entries[i] for i in order))
    assert sc.digest(shuffled) == sc.digest(m)


ENTRY_CHANGES = {"path": "cli/other", "kind": "link", "mode": 0o555, "size": 7, "nlink": 2,
                 "sha256": "0" * 8, "target": "elsewhere"}
MANIFEST_CHANGES = {"adapter": "other", "version": "9.9.9", "target": "other-target",
                    "entry_point": "cli/bin/other", "format": 2}
SOURCE_CHANGES = {"part": "defaults", "kind": "git", "name": "other", "ref": "other", "checksum": "sha256:1",
                  "origin": "other", "signature_available": True}


@pytest.mark.parametrize(("field", "value"), sorted(ENTRY_CHANGES.items()))
def test_any_entry_field_change_changes_the_digest(field: str, value: object) -> None:
    m = sample_manifest()
    changed = dataclasses.replace(m, entries=(dataclasses.replace(m.entries[0], **{field: value}),
                                              *m.entries[1:]))
    assert sc.digest(changed) != sc.digest(m)


@pytest.mark.parametrize(("field", "value"), sorted(MANIFEST_CHANGES.items()))
def test_any_manifest_field_change_changes_the_digest(field: str, value: object) -> None:
    m = sample_manifest()
    assert sc.digest(dataclasses.replace(m, **{field: value})) != sc.digest(m)


@pytest.mark.parametrize(("field", "value"), sorted(SOURCE_CHANGES.items()))
def test_any_source_field_change_changes_the_digest(field: str, value: object) -> None:
    m = sample_manifest()
    changed = dataclasses.replace(m, sources=(dataclasses.replace(m.sources[0], **{field: value}),
                                              *m.sources[1:]))
    assert sc.digest(changed) != sc.digest(m)


def test_parse_round_trips_the_canonical_encoding() -> None:
    m = sample_manifest()
    assert sc.parse(sc.canonical(m)) == m


@pytest.mark.parametrize("mangle", [
    lambda b: b.replace(b",", b", ", 1),                        # a separator canonical() never writes
    lambda b: b + b"\n",                                        # a trailing newline
    lambda b: json.dumps({**json.loads(b), "extra": 1}, sort_keys=True, separators=(",", ":")).encode(),
    lambda b: json.dumps({**json.loads(b), "entries": json.loads(b)["entries"][::-1]},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(),
    lambda b: b.replace(b'"signature_verified":false', b'"signature_verified":true'),
])
def test_parse_refuses_anything_canonical_would_not_produce(mangle: Callable[[bytes], bytes]) -> None:
    data = mangle(sc.canonical(sample_manifest()))
    with pytest.raises(sc.Mismatch) as e:
        sc.parse(data)
    assert e.value.constant == sc.MANIFEST_MALFORMED


def test_non_ascii_paths_are_utf8_and_sorted_by_their_bytes() -> None:
    names = ["cli/z", "cli/\u00e9", "cli/\U0001f600", "cli/A"]   # é is 0xC3 0xA9; the emoji is 0xF0 ...
    m = sample_manifest(paths=names)
    data = sc.canonical(m)
    assert "\u00e9".encode() in data and b"\\u00e9" not in data   # raw UTF-8, never an escape
    assert [e.path for e in sc.parse(data).entries] == sorted(names, key=lambda n: n.encode())
