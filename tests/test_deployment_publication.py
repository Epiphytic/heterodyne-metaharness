"""Real disposable Git objects/archive; HTTPS and operator profile are synthetic.

No fixture performs network I/O or claims genuine approval or protected custody.
"""

import http.client
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest
from test_deployment_outage import packet
from test_deployment_step1 import TARGET

from heterodyne.actions import deployment_publication as publication
from heterodyne.actions.codec import InvalidAction, encode_record


def git(directory, *args):
    return subprocess.check_output(
        ["/usr/bin/git", "--no-replace-objects", "-C", str(directory),
         "-c", "user.name=fixture", "-c", "user.email=fixture", "-c", "core.hooksPath=/dev/null", *args],
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000"},
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def published(tmp_path, monkeypatch):
    canonical = encode_record(packet())
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-q")
    path = repository / packet().release.packet_path
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical)
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "synthetic canonical deployment packet")
    commit = git(repository, "rev-parse", "HEAD").decode().strip()
    url = ("https://github.com/Epiphytic/heterodyne-metaharness/blob/"
           + commit + "/" + packet().release.packet_path)
    archive = tmp_path / "archive.tar"
    archive.write_bytes(git(repository, "archive", "--format=tar", commit))
    obj = tmp_path / "commit.object"
    obj.write_bytes(git(repository, "cat-file", "commit", commit))
    reads = []
    def https(actual_commit, actual_path):
        reads.append((actual_commit, actual_path))
        return canonical
    monkeypatch.setattr(publication, "_https", https)
    # Equivalent protected scratch; production /run is never created.
    with tempfile.TemporaryDirectory(prefix=".publication-", dir=Path.cwd()) as directory:
        monkeypatch.setattr(publication, "_SCRATCH", Path(directory))
        with archive.open("rb") as tree, obj.open("rb") as body:
            yield canonical, url, tree.fileno(), body.fileno(), reads, repository, archive


def test_real_git_complete_tree_and_regular_packet_plus_modeled_https(published):
    original, url, archive, commit, reads, _, _ = published
    proof = publication._verify(original, (TARGET,), url, archive, commit)
    assert proof.url == url and proof.published_original == original
    assert proof.published_original_sha256 == proof.packet_sha256
    assert reads == [(proof.commit, proof.path)]


def test_wrong_commit_refuses_before_https(published):
    original, url, archive, commit, reads, _, _ = published
    changed = url.replace(url.split("/")[6], "b" * 40)
    with pytest.raises(InvalidAction, match="readback-unavailable"):
        publication._verify(original, (TARGET,), changed, archive, commit)
    assert reads == []


def test_same_commit_wrong_hosted_bytes_refuses(published, monkeypatch):
    original, url, archive, commit, _, _, _ = published
    monkeypatch.setattr(publication, "_https", lambda *args: original + b"\n")
    with pytest.raises(InvalidAction, match="hosted-packet-mismatch"):
        publication._verify(original, (TARGET,), url, archive, commit)


@pytest.mark.parametrize("mode", ["symlink", "executable"])
def test_git_mode_is_not_inferred_from_https_bytes(published, mode):
    original, _, _, _, reads, repository, archive = published
    path = repository / packet().release.packet_path
    if mode == "symlink":
        path.unlink()
        path.symlink_to("another-packet.json")
    else:
        path.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "noncanonical packet mode")
    revision = git(repository, "rev-parse", "HEAD").decode().strip()
    archive.write_bytes(git(repository, "archive", "--format=tar", revision))
    obj = archive.parent / "changed.object"
    obj.write_bytes(git(repository, "cat-file", "commit", revision))
    url = ("https://github.com/Epiphytic/heterodyne-metaharness/blob/"
           + revision + "/" + packet().release.packet_path)
    with (archive.open("rb") as tree, obj.open("rb") as body,
          pytest.raises(InvalidAction, match="packet-mode")):
        publication._verify(original, (TARGET,), url, tree.fileno(), body.fileno())
    assert reads == []


def test_no_arbitrary_host_or_path_selector(published):
    original, url, archive, commit, reads, _, _ = published
    for changed in [url.replace("github.com", "elsewhere.invalid"), url.replace("docs/", "other/")]:
        with pytest.raises(InvalidAction, match="publication-url|publication-path"):
            publication._verify(original, (TARGET,), changed, archive, commit)
    assert reads == []


def test_source_descriptors_must_be_regular(published):
    original, url, _, commit, reads, _, _ = published
    fd = os.open(Path("/dev/null"), os.O_RDONLY)
    try:
        with pytest.raises(InvalidAction, match="object-regular"):
            publication._verify(original, (TARGET,), url, fd, commit)
    finally:
        os.close(fd)
    assert reads == []


@pytest.mark.parametrize(
    "status, encoding, refused", [(200, None, False), (302, None, True), (200, "gzip", True)],
)
def test_fixed_tls_reader_rejects_redirects_and_encoding(monkeypatch, status, encoding, refused):
    observed = []
    class Socket:
        def shutdown(self, how):
            observed.append(("shutdown", how))
    class Response:
        def __init__(self):
            self.status = status
        def getheader(self, name):
            return encoding if name == "Content-Encoding" else None
        def read(self, count):
            return b"canonical"
        def close(self):
            pass
    class Connection:
        def __init__(self, host, port, *, timeout):
            observed.append((host, port, timeout))
            self.sock = Socket()
        def connect(self):
            pass
        def request(self, method, path, *, headers):
            observed.append((method, path, headers))
        def getresponse(self):
            return Response()
        def close(self):
            pass
    def tls(connection, response, context):
        observed.append((context.verify_mode, context.check_hostname))
        response.bind(connection.sock)
    monkeypatch.setattr(publication.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(publication, "_tls", tls)
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid")
    if refused:
        with pytest.raises(InvalidAction, match="https-status"):
            publication._https("a" * 40, "docs/packet.json")
    else:
        assert publication._https("a" * 40, "docs/packet.json") == b"canonical"
    assert observed[:2] == [("raw.githubusercontent.com", 443, 10), (publication.ssl.CERT_REQUIRED, True)]
    assert observed[2] == (
        "GET", "/Epiphytic/heterodyne-metaharness/" + "a" * 40 + "/docs/packet.json",
        {"Host": "raw.githubusercontent.com", "Accept-Encoding": "identity"},
    )


@pytest.mark.parametrize("error", [EOFError(), publication.store_core._TAR_ERRORS[0]("truncated")])
def test_tar_read_failures_are_typed_before_https(published, monkeypatch, error):
    original, url, archive, commit, reads, _, _ = published
    def fail(*args):
        raise error
    monkeypatch.setattr(publication.store_core, "check_git", fail)
    with pytest.raises(InvalidAction, match="readback-unavailable"):
        publication._verify(original, (TARGET,), url, archive, commit)
    assert reads == []


def test_scratch_does_not_use_ambient_tmpdir(published, monkeypatch):
    original, url, archive, commit, _, _, _ = published
    monkeypatch.setenv("TMPDIR", "/nonexistent/untrusted-temporary-directory")
    assert publication._verify(original, (TARGET,), url, archive, commit).published_original == original


def test_actual_tls_trickle_handshake_is_interrupted_by_total_watchdog():
    # Real loopback TCP and TLS handshake, synthetic peer: no external network
    # or production CA/host qualification is claimed.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    stop = threading.Event()
    def peer():
        with listener.accept()[0] as client:
            try:
                client.recv(4096)
                client.sendall(b"\x16\x03\x03\x00\xff")
                while not stop.wait(.01):
                    client.sendall(b"\x00")
            except OSError:
                pass
    thread = threading.Thread(target=peer, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", listener.getsockname()[1], timeout=3)
    response = publication.HttpsResponse(connection, "https://fixture.invalid", .15)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    started = time.monotonic()
    try:
        with pytest.raises(OSError):
            publication._tls(connection, response, context)
        assert response.expired and time.monotonic() - started < 1
    finally:
        response.close()
        stop.set()
        thread.join(timeout=2)
        listener.close()
    assert not thread.is_alive()
