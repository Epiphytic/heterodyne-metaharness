"""Negative controls for the live probe-protection verdicts: empty, incomplete or permitted output fails."""

import pytest
from probe_verdicts import (
    file_writes_refused,
    memory_access_refused,
    outputs_unreachable,
    overlay_outcome,
    separate_mount_namespace,
)

MEM = "child 7\nattach 1 EPERM\nvm 1 EPERM\nmem 1 EACCES\nattach 7 EPERM\nvm 7 EPERM\nmem 7 EACCES\ndone\n"
FILES = "create=1\nappend=1\nrename=1\n"
OWN = "mnt:[4026531841]\n"
NO_NS = "forged=written\nunshare=1\ndone\n"
RAN = "forged=written\nns=entered\nbind-probe=0\nbind-cli=0\nrun=0\nunshare=0\ndone\n"
CURLS = "curl 41 socket:ENXIO socket:ENXIO\ncurl 42 gone gone\ndone\n"


def test_complete_refusals_pass() -> None:
    assert memory_access_refused(0, MEM) == ""
    assert file_writes_refused(0, FILES) == ""
    assert separate_mount_namespace(0, OWN + "refused\n") == ""
    assert separate_mount_namespace(0, OWN + "mnt:[4026532999]\n") == ""
    assert outputs_unreachable(0, CURLS) == ""


@pytest.mark.parametrize(("out", "outcome"), [
    (NO_NS, "refused"),                                                          # no namespace at all
    ("forged=written\nns=entered\nbind-probe=1\nunshare=1\ndone\n", "refused"),
    ("forged=written\nns=entered\nbind-probe=0\nbind-cli=32\nunshare=1\ndone\n", "refused"),
    (RAN, "ran"),
])
def test_a_complete_overlay_check_reports_its_outcome(out: str, outcome: str) -> None:
    assert overlay_outcome(0, out) == outcome


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (1, MEM),                                               # did not exit cleanly
    (0, MEM.replace("done\n", "")),                         # stopped early
    (0, MEM.replace("child 7\n", "")),                      # no child named
    (0, MEM.replace("attach 7", "attach 8")),               # its child not covered by every operation
    (0, MEM.replace("vm 1 EPERM\n", "")),                   # a process missing an operation
    (0, MEM.replace("attach 7 EPERM", "attach 7 ATTACHED")),
    (0, MEM.replace("mem 1 EACCES", "mem 1 ?")),            # not a recognised refusal
    (0, MEM.replace("mem 1 EACCES", "mem 1 EACCES extra")),
])
def test_incomplete_or_permitted_memory_access_fails(rc: int, out: str) -> None:
    assert memory_access_refused(rc, out) != ""


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (0, "create=1\nappend=1\n"),
    (0, FILES + "extra=1\n"),
    (0, "append=1\ncreate=1\nrename=1\n"),
    (0, FILES.replace("append=1", "append=x")),
    (0, FILES.replace("append=1", "append=")),
    (0, FILES.replace("rename=1", "rename=0")),
    (1, FILES),
])
def test_incomplete_or_permitted_file_writes_fail(rc: int, out: str) -> None:
    assert file_writes_refused(rc, out) != ""


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (0, OWN),
    (0, OWN + OWN),                                         # the same namespace
    (0, "x\nrefused\n"),
    (0, OWN + "something\n"),
    (1, OWN + "refused\n"),
])
def test_incomplete_or_shared_mount_namespace_fails(rc: int, out: str) -> None:
    assert separate_mount_namespace(rc, out) != ""


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (1, NO_NS),                                             # the check itself failed: a setup failure
    (0, NO_NS.replace("forged=written\n", "")),            # the forged file was never written
    (0, NO_NS.replace("done\n", "")),
    (0, NO_NS.replace("unshare=1", "unshare=0")),           # no namespace, yet no refusal either
    (0, NO_NS.replace("unshare=1", "unshare=x")),
    (0, RAN.replace("run=0\n", "")),                       # a missing stage
    (0, RAN.replace("ns=entered\n", "")),
    (0, RAN.replace("ns=entered", "ns=maybe")),
    (0, "forged=written\nns=entered\nbind-probe=0\nunshare=1\ndone\n"),   # stopped with no refusal
])
def test_an_incomplete_overlay_check_fails(rc: int, out: str) -> None:
    assert overlay_outcome(rc, out) not in ("refused", "ran")


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (0, "done\n"),                                          # no curl seen while the probe ran
    (0, "curl 42 gone gone\ndone\n"),                       # none seen whole
    (1, CURLS),
    (0, CURLS.replace("done\n", "")),
    (0, CURLS.replace("socket:ENXIO socket:ENXIO", "pipe:OPENED socket:ENXIO")),
    (0, CURLS.replace("socket:ENXIO socket:ENXIO", "socket:ENXIO socket:OPENED")),
    (0, CURLS.replace("socket:ENXIO socket:ENXIO", "pipe:EACCES pipe:EACCES")),     # not a socket
    (0, CURLS.replace("curl 41", "curl x")),
])
def test_an_incomplete_or_reachable_output_check_fails(rc: int, out: str) -> None:
    assert outputs_unreachable(rc, out) != ""
