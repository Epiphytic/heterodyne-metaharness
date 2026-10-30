"""Private mathematical Git proof plus fixed immutable HTTPS corroboration.

The owning protected receiver must supply its retained complete archive/commit
object descriptors. There is no production object-view opener or ambient Git,
proxy, URL selector, credential reader, boolean observation or callback here.
"""

# pyright: reportPrivateUsage=false
# One dormant source-owned composition reuses checked private Git parsing.

import hashlib
import http.client
import os
import ssl
import stat
import tempfile
from dataclasses import dataclass
from typing import BinaryIO

from heterodyne.actions.codec import MAX_BYTES, InvalidAction, encode_record
from heterodyne.actions.deployment import Target
from heterodyne.actions.deployment_authority import _PACKET
from heterodyne.actions.deployment_outage import decode_packet, packet_digest
from heterodyne.harness.fetch import HttpsResponse
from heterodyne.harness.resources import store_core

_HOST = "raw.githubusercontent.com"
_PREFIX = "/Epiphytic/heterodyne-metaharness/"
_CA = "/etc/ssl/certs/ca-certificates.crt"


@dataclass(frozen=True)
class _PublishedPacket:
    url: str
    commit: str
    path: str
    published_original: bytes
    published_original_sha256: str
    packet_sha256: str


def _retain(fd: int, limit: int) -> BinaryIO:
    """Private immutable-for-this-call copy; no caller's mutable seek state reused."""
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise InvalidAction("publication-object-regular-required")
    result = tempfile.TemporaryFile()
    offset = 0
    try:
        while data := os.pread(fd, min(1024 * 1024, limit + 1 - offset), offset):
            offset += len(data)
            if offset > limit:
                raise InvalidAction("publication-object-bound")
            result.write(data)
        result.flush()
        os.fsync(result.fileno())
        result.seek(0)
        return result
    except BaseException:
        result.close()
        raise


def _https(commit: str, path: str) -> bytes:
    """Fixed no-redirect TLS read with the existing connected-I/O watchdog.

    Host qualification must still bound platform DNS/connect behavior; this
    dormant composition does not claim to have qualified that platform route.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=_CA)
    connection = http.client.HTTPSConnection(_HOST, timeout=10, context=context)
    response = HttpsResponse(connection, "https://" + _HOST + _PREFIX + commit + "/" + path, 10)
    try:
        connection.connect()
        response.bind(connection.sock)
        connection.request("GET", _PREFIX + commit + "/" + path, headers={"Accept-Encoding": "identity"})
        response.attach(connection.getresponse())
        if response.status != 200 or response.header("Content-Encoding") not in {None, "identity"}:
            raise InvalidAction("publication-https-status")
        result = response.read(MAX_BYTES + 1)
        if len(result) > MAX_BYTES:
            raise InvalidAction("publication-packet-bound")
        return result
    finally:
        response.close()


def _verify(  # pyright: ignore[reportUnusedFunction] - owning protected receiver integration only
    original: bytes, targets: tuple[Target, ...], url: str, archive_fd: int, commit_fd: int,
) -> _PublishedPacket:
    """Original request and exact served, regular Git member must be the same packet.

    Local complete Git proof establishes commit/tree/mode. The separate fixed
    HTTPS read establishes hosted bytes at the exact immutable URL. This is not
    deployment consent, a receiver credential, or a public runtime opener.
    """
    packet = decode_packet(original, targets)
    if not _PACKET.fullmatch(url):
        raise InvalidAction("publication-url")
    commit, path = url.split("/", 7)[6:]
    if path != packet.release.packet_path or ".." in path.split("/"):
        raise InvalidAction("publication-path")
    try:
        with _retain(archive_fd, store_core.MAX_TAR_BYTES) as archive, _retain(
            commit_fd, store_core.MAX_COMMIT_OBJECT,
        ) as commit_object:
            store_core.check_git(archive.fileno(), commit_object.fileno(), commit)
            members = store_core.git_members(archive.fileno(), "sha1")
            if [(name, mode) for name, mode, _ in members if name == path] != [(path, b"100644")]:
                raise InvalidAction("publication-packet-mode")
            with store_core._tar(archive.fileno(), gz=False) as tree:
                member = tree.getmember(path)
                if member.size > MAX_BYTES:
                    raise InvalidAction("publication-packet-bound")
                stream = tree.extractfile(member)
                if stream is None:
                    raise InvalidAction("publication-packet-absent")
                retained = stream.read(MAX_BYTES + 1)
        canonical = encode_record(packet)
        if retained != canonical or decode_packet(retained, targets) != packet:
            raise InvalidAction("publication-canonical-packet-mismatch")
        served = _https(commit, path)
        if served != retained:
            raise InvalidAction("publication-hosted-packet-mismatch")
        return _PublishedPacket(
            url, commit, path, served, hashlib.sha256(served).hexdigest(), packet_digest(packet),
        )
    except (OSError, ValueError, KeyError, http.client.HTTPException, store_core.Refused) as exc:
        if isinstance(exc, InvalidAction):
            raise
        raise InvalidAction("publication-readback-unavailable") from exc
