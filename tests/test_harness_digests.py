"""Plan 4c Task 10: a digest shown on a card (E14). Only a `ComputedDigest` is grouped: a SHA-256 that `wsd`
computed itself, over a manifest's canonical bytes or a file it holds. Its display is `sha256:` and 16
groups of 4 lowercase hex, which none of admind's scanner patterns match."""

import base64
import hashlib
import os
import re
import tempfile
from pathlib import Path

import pytest
from harness_env import sample_manifest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.config import secret_scan
from heterodyne.harness import digests
from heterodyne.harness.resources import store_core as sc

DISPLAY = re.compile(r"sha256:[0-9a-f]{4}( [0-9a-f]{4}){15}")


def computed(data: bytes, tmp_path: Path) -> digests.ComputedDigest:
    path = tmp_path / "artifact"
    path.write_bytes(data)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        return digests.of_fd(fd)
    finally:
        os.close(fd)


@given(st.binary(max_size=4096))
def test_display_is_sixteen_groups_of_four_lowercase_hex_and_parses_back(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as d:
        digest = computed(data, Path(d))
    shown = digests.display(digest)
    assert DISPLAY.fullmatch(shown)
    assert shown.replace(" ", "") == "sha256:" + hashlib.sha256(data).hexdigest()
    assert digests.parse_display(shown) == digest


def test_a_manifest_digest_is_the_sha256_of_its_canonical_form() -> None:
    m = sample_manifest()
    shown = digests.display(digests.of_manifest(m))
    assert shown.replace(" ", "") == sc.digest(m)
    assert digests.parse_display(shown) == digests.of_manifest(m)


def _forms() -> list[str]:
    hexed = hashlib.sha256(b"a value").hexdigest()
    groups = [hexed[i:i + 4] for i in range(0, 64, 4)]
    return [
        hexed,                                              # bare hex
        "sha256:" + hexed,                                  # ungrouped
        "sha256:" + " ".join(groups).upper(),               # upper case
        "SHA256:" + " ".join(groups),
        "sha256:" + " ".join(groups[:15]),                  # 15 groups
        "sha256:" + " ".join([*groups, "abcd"]),            # 17 groups
        "sha256:" + "-".join(groups),                       # another separator
        "sha256:" + ":".join(groups),
        "sha256:" + "  ".join(groups),
        "sha256: " + " ".join(groups),
        "sha256:" + " ".join(groups) + " ",
        " sha256:" + " ".join(groups),
        "sha256:" + " ".join(groups) + "\n",
        "sha512-" + base64.b64encode(hashlib.sha512(b"a value").digest()).decode(),
        "",
    ]


@pytest.mark.parametrize("form", _forms())
def test_parse_display_refuses_every_other_form(form: str) -> None:
    with pytest.raises(ValueError):
        digests.parse_display(form)


@given(st.binary(max_size=1024))
def test_no_scanner_pattern_matches_a_displayed_digest(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as d:
        shown = digests.display(computed(data, Path(d)))
    assert secret_scan.sensitive_kind(shown) is None
    text = (f"Title: Add the codex 0.160.0 managed install\nDescription:\n  Manifest digest: {shown}\n"
            f"ask.effect: publish codex install 0.160.0-abcdef012345 manifest {shown}\n")
    assert secret_scan.sensitive_kind(text) is None
