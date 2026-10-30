"""A digest shown on an approval card (plan 4c E14). Only a `ComputedDigest` is ever grouped for display: a
SHA-256 that `wsd` computed itself, over a manifest's canonical bytes (`of_manifest`) or a file it holds open
(`of_fd`). A digest read from anywhere else is never given this form, so a card cannot show a value it was
handed. The display is `sha256:` and 16 space-separated groups of 4 lowercase hex: no secret scanner pattern
matches it, and `parse_display` takes back that exact form and no other."""

import hashlib
import os
import re
from dataclasses import dataclass

from heterodyne.harness.resources import store_core as sc

_DISPLAY = re.compile(r"sha256:((?:[0-9a-f]{4} ){15}[0-9a-f]{4})")
_CHUNK = 1 << 16


@dataclass(frozen=True)
class ComputedDigest:
    """A SHA-256 `wsd` computed; build one with `of_manifest` or `of_fd` only."""

    hex: str


def of_manifest(m: sc.Manifest) -> ComputedDigest:
    return ComputedDigest(hashlib.sha256(sc.canonical(m)).hexdigest())


def of_fd(fd: int) -> ComputedDigest:
    """The SHA-256 of the file open on `fd`, read from its start; the offset is left at its end."""
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, _CHUNK):
        h.update(chunk)
    return ComputedDigest(h.hexdigest())


def display(digest: ComputedDigest) -> str:
    return "sha256:" + " ".join(digest.hex[i:i + 4] for i in range(0, 64, 4))


def parse_display(text: str) -> ComputedDigest:
    """The digest `display` shows as `text`; any other form raises ValueError."""
    match = _DISPLAY.fullmatch(text)
    if match is None:
        raise ValueError("not a displayed digest")
    return ComputedDigest(match.group(1).replace(" ", ""))
