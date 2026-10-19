"""Read-only managed-install consistency checks for private source captures.

The caller supplies an already-open private installation fd and an independently admitted manifest
digest and adapter versions. This module neither discovers a host store nor authenticates captures,
loaded code, historical installation integrity, or authority. Operational proof roles remain HOLD.
The complete store-core parser, fd walk, hash checks and mutation checks are reused directly.
"""

import os
from dataclasses import dataclass

from heterodyne.harness.resources import store_core as sc

from .codec import Hold

MAX_META_BYTES = 1_048_576
MAX_ENTRIES = 4096
MAX_FILE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class InstallProjection:
    adapter: str
    version: str
    manifest_digest: str
    entries: int
    file_bytes: int

    def require_authority(self) -> None:
        raise Hold("install_acquisition_and_loaded_provenance_unavailable")


def verify(
    *,
    adapter: str,
    versions: tuple[str, ...],
    manifest_digest: str,
    manifest_bytes: bytes,
    record_bytes: bytes,
    root_fd: int,
) -> InstallProjection:
    """Compare a sampled private tree to configured expected metadata without any write.

    Expected metadata must be admitted independently of the capture. A successful result is only a
    projection, even when an untrusted caller chooses its own matching manifest. No proof adapter is
    registered by this function. Walk limits remain store-core's 200,000 entries and depth 64;
    manifest input is additionally bounded to 4,096 entries and hashing to 64 MiB in total.
    """
    if len(manifest_bytes) > MAX_META_BYTES or len(record_bytes) > MAX_META_BYTES:
        raise Hold("install_projection_bound")
    try:
        manifest = sc.parse(manifest_bytes)
        record = sc.parse_record(record_bytes)
        if sc.canonical_record(record) != record_bytes:
            raise Hold("install_projection_metadata_unavailable")
    except (sc.Refused, ValueError, TypeError, RecursionError):
        raise Hold("install_projection_metadata_unavailable") from None
    size = sum(entry.size for entry in manifest.entries if entry.kind == "file")
    if len(manifest.entries) > MAX_ENTRIES or size > MAX_FILE_BYTES:
        raise Hold("install_projection_bound")
    if (
        adapter not in ("codex", "claude")
        or manifest.adapter != adapter
        or manifest.version not in versions
        or sc.digest(manifest) != manifest_digest
    ):
        raise Hold("install_projection_expected_metadata_differs")
    budget = MAX_FILE_BYTES

    def read(fd: int, amount: int) -> bytes:
        nonlocal budget
        data = os.read(fd, min(amount, budget + 1))
        budget -= len(data)
        if budget < 0:
            raise Hold("install_projection_bound")
        return data

    try:
        # The original record is checked before and after hashing; never generate a new record to
        # make a changed capture pass. check_fast refuses unexpected files before they are hashed.
        sc.check_fast(root_fd, manifest, record)
        if sc.entries_of(root_fd, read=read) != manifest.entries:
            raise Hold("install_projection_tree_unavailable")
        sc.check_fast(root_fd, manifest, record)
    except (sc.Refused, OSError, ValueError):
        raise Hold("install_projection_tree_unavailable") from None
    return InstallProjection(adapter, manifest.version, manifest_digest, len(manifest.entries), size)
