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


def _derive(
    original: bytes, targets: tuple[Target, ...], group_hex: str, publication_url: str,
) -> _PreparedCard:
    """Shared candidate derivation under the current host rendering policy.

    The owning factory must enroll that policy; this is not publication evidence."""
    packet = decode_packet(original, targets)
    release = packet.release
    if asks.normalise_id(release.approval_id) != release.approval_id:
        raise InvalidAction("deployment-card-ask-id")
    _, request, _, parts = _approval_card(packet, publication_url, group_hex)
    canonical = encode_record(packet)
    commit, path = publication_url.split("/", 7)[6:]
    return _PreparedCard(
        operation_original=original,
        operation_original_sha256=hashlib.sha256(original).hexdigest(),
        operation_canonical=canonical,
        packet_sha256=packet_digest(packet),
        ask_id=release.approval_id,
        group_hex=group_hex,
        request_original=msgspec.json.encode(request),
        publication_url=publication_url,
        publication_commit=commit,
        publication_path=path,
        published_original_sha256=hashlib.sha256(canonical).hexdigest(),
        card=parts[0],
    )


def _prepare(  # pyright: ignore[reportUnusedFunction] - future owning protected receiver factory only
    original: bytes, targets: tuple[Target, ...], group_hex: str, publication_url: str,
    archive_fd: int, commit_fd: int,
) -> _PreparedCard:
    # Validate the exact final card once, before any network corroboration. Its
    # provisional value is never returned until the complete proof succeeds.
    candidate = _derive(original, targets, group_hex, publication_url)
    _verify(original, targets, publication_url, archive_fd, commit_fd)
    return candidate
