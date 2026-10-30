"""Real isolated child/FD mechanics; transport and installed profile synthetic.

No native Engine, enrolled owning launcher, consent or deployment is modeled as
qualified. The child executes actual Product producer and real Git proof.
"""

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import msgspec
import pytest
import test_deployment_publication
from test_deployment_step1 import TARGET, H, synthetic_rendering_profile  # noqa: F401

from heterodyne.actions import deployment_producer_process as producer
from heterodyne.actions import deployment_publication as publication
from heterodyne.actions.codec import InvalidAction, encode_record

published = test_deployment_publication.published


@pytest.fixture
def inputs(published, tmp_path):
    canonical, url, archive, commit, *_ = published
    original = json.dumps(json.loads(canonical), indent=2).encode()
    profile = producer._Profile(
        "heterodyne-owned-card-profile.v1", hashlib.sha256(original).hexdigest(),
        hashlib.sha256(canonical).hexdigest(), H, url, (TARGET,),
    )
    path = tmp_path / "producer-profile.json"
    path.write_bytes(encode_record(profile))
    path.chmod(0o400)
    os.fchmod(archive, 0o400)
    os.fchmod(commit, 0o400)
    with path.open("rb") as retained:
        yield original, canonical, profile, retained.fileno(), archive, commit, path


def child(inputs, *, original=None, extra="", marker=None):
    raw, canonical, _, profile, archive, commit, _ = inputs
    # Only test bootstrap remaps arbitrary fixture descriptors. A real native
    # launcher must own slots3/4/5 before its fixed enrolled invocation.
    with contextlib.ExitStack() as stack:
        duplicates = tuple(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 64) for fd in (profile, archive, commit))
        for fd in duplicates:
            stack.callback(os.close, fd)
        source = str(Path(__file__).resolve().parents[1] / "src")
        code = (
            "import sys,os; sys.path[:0]=" + repr([source, sysconfig.get_paths()["purelib"]]) + "; "
            "from heterodyne.actions import deployment_producer_process as p; "
        )
        code += (
            "from heterodyne.actions import deployment_publication as pub, deployment_authority as auth; "
            "from pathlib import Path; "
            "pub._https=lambda *args: "
            + ("(Path(" + repr(str(marker)) + ").write_text('publication'), " if marker else "(")
            + "bytes.fromhex(" + repr(canonical.hex()) + "),)[-1]; "
            "pub._SCRATCH=Path(" + repr(str(publication._SCRATCH)) + "); "
            "auth._rendering=lambda: ((),2000); "
        )
        code += "; ".join(f"os.dup2({fd},{slot})" for fd, slot in zip(duplicates, (3, 4, 5), strict=True))
        code += "; " + extra + "; sys.exit(p._entry())" if extra else "; sys.exit(p._entry())"
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-X",
             "pycache_prefix=" + str(Path(publication._SCRATCH) / "cache"),
             "-c", code],
            input=raw if original is None else original, pass_fds=duplicates,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
            capture_output=True, timeout=10, check=False,
        )


def test_actual_child_exact_original_owned_fds_and_closed_output(inputs):
    original, canonical, profile, profile_fd, archive, commit, _ = inputs
    offsets = tuple(os.lseek(fd, 0, os.SEEK_CUR) for fd in (profile_fd, archive, commit))
    result = child(inputs)
    assert (result.returncode, result.stderr) == (0, b"")
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/deployment-card/owned-process-v1.json").read_bytes(),
    )
    assert json.loads(result.stdout) == fixture["output"]
    value = producer._check_output(result.stdout, original, profile)
    assert value.operation_original == original != value.operation_canonical == canonical
    assert value.request_sha256 == hashlib.sha256(value.request_original).hexdigest()
    assert value.cards_sha256 == hashlib.sha256(msgspec.json.encode([value.card])).hexdigest()
    assert offsets == tuple(os.lseek(fd, 0, os.SEEK_CUR) for fd in (profile_fd, archive, commit))


@pytest.mark.parametrize(
    "change", [
        "original", "canonical", "group", "request", "card", "url",
        "unknown", "duplicate", "whitespace", "oversize",
    ],
)
def test_stdout_correlation_refuses_tampered_values(inputs, change):
    original, _, profile, *_ = inputs
    result = child(inputs)
    assert result.returncode == 0
    value = json.loads(result.stdout)
    field = {"original": "operation_original", "canonical": "operation_canonical", "group": "group_hex",
             "request": "request_original", "card": "card", "url": "publication_url"}.get(change)
    if field:
        if change in {"original", "canonical", "request"}:
            value[field] = base64.b64encode(base64.b64decode(value[field]) + b" ").decode()
        else:
            value[field] = value[field] + "a"
    elif change == "unknown":
        value["approved"] = True
    if change == "whitespace":
        raw = result.stdout + b" "
    elif change == "oversize":
        raw = b"x" * (256 * 1024 + 1)
    elif change == "duplicate":
        raw = result.stdout[:-1] + b',"schema":"heterodyne-owned-card.v1"}'
    else:
        raw = msgspec.json.encode(value)
    reason = ("correlation" if field else "bound" if change == "oversize"
              else "encoding" if change == "whitespace" else "shape")
    with pytest.raises(InvalidAction, match="producer-output-" + reason):
        producer._check_output(raw, original, profile)


@pytest.mark.parametrize(
    "change", ["original", "large", "writable", "aliased", "unknown-profile", "argv", "closed"],
)
def test_real_child_refuses_before_working_publication_fixture(inputs, change, tmp_path):
    original, _, _, _, archive, _, path = inputs
    extra = ""
    if change == "original":
        original += b" "
    elif change == "large":
        original = b"x" * 32769
    elif change == "writable":
        path.chmod(0o600)
    elif change == "aliased":
        extra = "os.dup2(4,5)"
    elif change == "unknown-profile":
        value = json.loads(path.read_bytes())
        value["approved"] = True
        path.chmod(0o600)
        path.write_text(json.dumps(value))
        path.chmod(0o400)
    elif change == "closed":
        extra = "os.close(5)"
    else:
        extra = "sys.argv.append('unexpected')"
    marker = tmp_path / "publication-marker"
    result = child(inputs, original=original, extra=extra, marker=marker)
    assert (result.returncode, result.stdout, result.stderr) == (2, b"", b"owned-card-producer-refused\n")
    assert not marker.exists()


def test_descriptor_must_be_readonly_not_merely_readonly_mode(inputs):
    original, _, _, profile, archive, commit, _ = inputs
    path = Path(f"/proc/self/fd/{archive}").resolve()
    path.chmod(0o600)
    with path.open("r+b") as writable:
        path.chmod(0o400)
        with pytest.raises(InvalidAction, match="descriptor-custody"):
            producer._produce(original, profile, writable.fileno(), commit)


def test_hardlinked_profile_is_not_private_custody(inputs):
    original, _, _, profile, archive, commit, path = inputs
    (path.parent / "profile-copy").hardlink_to(path)
    with pytest.raises(InvalidAction, match="descriptor-custody"):
        producer._produce(original, profile, archive, commit)


def test_nonregular_descriptor_refuses_before_read(inputs):
    original, _, _, profile, _, commit, _ = inputs
    read, write = os.pipe()
    try:
        with pytest.raises(InvalidAction, match="descriptor-custody"):
            producer._produce(original, profile, read, commit)
    finally:
        os.close(read)
        os.close(write)


def test_writable_guard_is_causal_with_working_child_publication(inputs, tmp_path):
    # Removing only descriptor custody in this test child must reach the working
    # publication stub and succeed, rather than fail on missing host scratch.
    path = inputs[-1]
    path.chmod(0o600)
    marker = tmp_path / "reached"
    refused = child(inputs, marker=marker)
    assert refused.returncode == 2 and not marker.exists()
    bypass = (
        "p._identity=lambda fd: tuple(getattr(os.fstat(fd), k) for k in "
        "('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))"
    )
    successful = child(inputs, marker=marker, extra=bypass)
    assert (successful.returncode, successful.stderr) == (0, b"")
    assert marker.read_text() == "publication"


@pytest.mark.parametrize("change,reason", [
    ("original", "original-binding"), ("writable", "descriptor-custody"),
    ("alias", "descriptor-alias"), ("unknown", "invalid-schema"),
])
def test_exact_prepublication_guard_refusal(inputs, change, reason, monkeypatch):
    original, _, _, profile, archive, commit, path = inputs
    def forbidden(*args):
        pytest.fail("prepublication guard did not refuse")
    monkeypatch.setattr(publication, "_https", forbidden)
    if change == "original":
        original += b" "
    elif change == "writable":
        path.chmod(0o600)
    elif change == "alias":
        commit = archive
    else:
        value = json.loads(path.read_bytes())
        value["approved"] = True
        path.chmod(0o600)
        path.write_bytes(msgspec.json.encode(value))
        path.chmod(0o400)
    with pytest.raises(InvalidAction, match=reason):
        producer._produce(original, profile, archive, commit)


def test_profile_mutation_during_read_is_causal(inputs, monkeypatch):
    _, _, _, fd, _, _, path = inputs
    pread = os.pread
    def changed(actual, size, offset):
        raw = pread(actual, size, offset)
        if actual == fd:
            path.chmod(0o600)
            with path.open("ab") as stream:
                stream.write(b" ")
            path.chmod(0o400)
        return raw
    monkeypatch.setattr(producer.os, "pread", changed)
    with pytest.raises(InvalidAction, match="profile-changed-or-bound"):
        producer._profile(fd)


def test_descriptor_mutation_after_actual_publication_read_is_causal(inputs, monkeypatch):
    original, canonical, _, profile, archive, commit, _ = inputs
    reached = []
    def changed(*args):
        reached.append(True)
        os.fchmod(archive, 0o600)
        os.fchmod(archive, 0o400)
        return canonical
    monkeypatch.setattr(publication, "_https", changed)
    with pytest.raises(InvalidAction, match="descriptor-changed"):
        producer._produce(original, profile, archive, commit)
    assert reached == [True]


def test_production_output_size_guard_after_preparation(inputs, monkeypatch):
    original, _, _, profile, archive, commit, _ = inputs
    monkeypatch.setattr(producer, "_OUTPUT_LIMIT", 1)
    with pytest.raises(InvalidAction, match="output-bound"):
        producer._produce(original, profile, archive, commit)


def test_hosted_packet_mismatch_after_network_is_typed(inputs, monkeypatch):
    original, _, _, profile, archive, commit, _ = inputs
    reached = []
    def mismatch(*args):
        reached.append(True)
        return b"different hosted bytes"
    monkeypatch.setattr(publication, "_https", mismatch)
    with pytest.raises(InvalidAction, match="hosted-packet-mismatch"):
        producer._produce(original, profile, archive, commit)
    assert reached == [True]


def test_nested_output_is_typed_refusal(inputs):
    original, _, profile, *_ = inputs
    with pytest.raises(InvalidAction, match="output-shape"):
        producer._check_output(b"[" * 200000, original, profile)


@pytest.mark.parametrize("extra", [
    "os.close(0);sys.stdin=None",
    "p._produce=lambda *args: (_ for _ in ()).throw(RecursionError())",
    "p._produce=lambda *args: (_ for _ in ()).throw(MemoryError())",
    "p._produce=lambda *args: (_ for _ in ()).throw(KeyError('fixture'))",
])
def test_real_child_exception_boundary_is_closed(inputs, extra):
    result = child(inputs, extra=extra)
    assert (result.returncode, result.stdout, result.stderr) == (2, b"", b"owned-card-producer-refused\n")


def test_preparation_and_output_share_exact_ask_normalisation(inputs, monkeypatch):
    from heterodyne.actions import deployment_card as card
    original, _, profile, profile_fd, archive, commit, _ = inputs
    result = child(inputs)
    assert result.returncode == 0
    monkeypatch.setattr(card.asks, "normalise_id", lambda value: None)
    with pytest.raises(InvalidAction, match="deployment-card-ask-id"):
        card._prepare(original, profile.targets, profile.group_hex, profile.publication_url, archive, commit)
    with pytest.raises(InvalidAction, match="deployment-card-ask-id"):
        producer._check_output(result.stdout, original, profile)


@pytest.mark.parametrize("change", ["path", "inner-commit"])
def test_exact_profile_publication_context_refuses_before_read(inputs, monkeypatch, change):
    original, _, profile, profile_fd, archive, commit, path = inputs
    reached = []
    monkeypatch.setattr(publication, "_https", lambda *args: reached.append(True))
    if change == "path":
        url = profile.publication_url.removesuffix(".json") + "-wrong.json"
    else:
        operation = json.loads(original)
        url = profile.publication_url.replace(profile.publication_url.split("/")[6],
                                              operation["release"]["packet_commit"])
    profile = msgspec.structs.replace(profile, publication_url=url)
    path.chmod(0o600)
    path.write_bytes(encode_record(profile))
    path.chmod(0o400)
    with pytest.raises(InvalidAction, match="outer-packet-publication-url"):
        producer._produce(original, profile_fd, archive, commit)
    assert reached == []


def test_actual_profile_byte_bound_is_causal(inputs):
    path = inputs[-1]
    path.chmod(0o600)
    path.write_bytes(b" " * 32769)
    path.chmod(0o400)
    with pytest.raises(InvalidAction, match="profile-changed-or-bound"):
        producer._profile(inputs[3])
