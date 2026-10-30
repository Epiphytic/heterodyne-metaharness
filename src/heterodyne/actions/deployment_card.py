"""Private exact whole-packet card producer, not a consent or receiver capability.

The protected receiver factory must own this pinned producer and retained bytes.
No function accepts a caller-written card or caller assertion of equivalence.
"""

# pyright: reportPrivateUsage=false
# Private fixed-source composition; no public consent DTO.

import hashlib
from dataclasses import dataclass

import msgspec

from heterodyne.actions import deployment_authority as authority
from heterodyne.actions.codec import InvalidAction, encode_record
from heterodyne.actions.deployment import Target
from heterodyne.actions.deployment_outage import decode_packet, packet_digest
from heterodyne.actions.deployment_outage_authority import APPROVAL_TITLE, approval_body
from heterodyne.actions.deployment_publication import _verify
from heterodyne.admind import asks
from heterodyne.admind.redact import redact_with_dirs
from heterodyne.admind.store import AskRow

_POSTER = "protected-deploy"


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
    publication = _verify(original, targets, publication_url, archive_fd, commit_fd)
    packet = decode_packet(original, targets)
    directories, chunk_chars = authority._rendering()
    release = packet.release
    if asks.normalise_id(release.approval_id) != release.approval_id:
        raise InvalidAction("deployment-card-ask-id")
    body = approval_body(packet, publication.url, group_hex)
    request = asks.AskPost(
        "question", title=APPROVAL_TITLE, body=body, poster=_POSTER,
        form="yes-no", recommended="yes",
    )
    if asks.check(request) is not None:
        raise InvalidAction("deployment-card-request-refused")
    form, choices = asks.stored_form(request)
    at = asks.stamp(asks.now())
    row = AskRow(
        release.approval_id, "question", _POSTER, APPROVAL_TITLE, body,
        None, None, None, None, False, 1, "open", None, None, at, at,
        question_form=form, choices=choices,
    )
    # checked_card_chunks alone begins with the ordinary redacting context
    # renderer. Refuse whole-context changes first, then packed-part changes.
    if redact_with_dirs(asks.context(row), directories) != asks.context(row):
        raise InvalidAction("deployment-card-redaction")
    try:
        parts, truncated = asks.checked_card_chunks(row, chunk_chars)
    except asks.chunk.TooWide as exc:
        raise InvalidAction("deployment-card-transport-bound") from exc
    if type(parts) is not list or truncated or len(parts) != 1:
        raise InvalidAction("deployment-card-exact-single-part-required")
    if (any(redact_with_dirs(part, directories) != part for part in parts)
        or asks.card_chunks(row, chunk_chars) != (parts, False)):
        raise InvalidAction("deployment-card-renderer-mismatch")
    return _PreparedCard(
        original, hashlib.sha256(original).hexdigest(), encode_record(packet), packet_digest(packet),
        release.approval_id, group_hex, msgspec.json.encode(request),
        publication.url, publication.commit, publication.path,
        publication.published_original_sha256, parts[0],
    )
