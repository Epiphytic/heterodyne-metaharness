"""Private exact whole-packet card producer, not a consent or receiver capability.

The protected receiver factory must own this pinned producer and retained bytes.
No function accepts a caller-written card or caller assertion of equivalence.
"""

# pyright: reportPrivateUsage=false
# Private fixed-source composition; no public consent DTO.

import hashlib
from dataclasses import dataclass

import msgspec

from heterodyne.actions.codec import InvalidAction, encode_record
from heterodyne.actions.deployment import Target
from heterodyne.actions.deployment_outage import decode_packet, packet_digest
from heterodyne.actions.deployment_outage_authority import _approval_card
from heterodyne.actions.deployment_publication import _verify
from heterodyne.admind import asks


@dataclass(frozen=True)
class _PreparedCard:
    """Internal value, no public/external compatibility or provenance claim."""

    operation_original: bytes
    operation_original_sha256: str
    operation_canonical: bytes
    packet_sha256: str
    ask_id: str
    group_hex: str
    request_original: bytes
    publication_url: str
    publication_commit: str
    publication_path: str
    published_original_sha256: str
    card: str


def _prepare(  # pyright: ignore[reportUnusedFunction] - future owning protected receiver factory only
    original: bytes, targets: tuple[Target, ...], group_hex: str, publication_url: str,
    archive_fd: int, commit_fd: int,
) -> _PreparedCard:
    packet = decode_packet(original, targets)
    release = packet.release
    if asks.normalise_id(release.approval_id) != release.approval_id:
        raise InvalidAction("deployment-card-ask-id")
    # Validate the exact final card once, before any network corroboration. Its
    # provisional value is never returned until the complete proof succeeds.
    _, request, _, parts = _approval_card(packet, publication_url, group_hex)
    publication = _verify(original, targets, publication_url, archive_fd, commit_fd)
    return _PreparedCard(
        original, hashlib.sha256(original).hexdigest(), encode_record(packet), packet_digest(packet),
        release.approval_id, group_hex, msgspec.json.encode(request),
        publication.url, publication.commit, publication.path,
        publication.published_original_sha256, parts[0],
    )
