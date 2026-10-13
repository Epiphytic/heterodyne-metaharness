"""Negative controls for the live probe-protection verdicts: empty, incomplete or permitted output fails, and
so does any answer but the exact one the enforcing layer gives (D23, Task 9B)."""

import pytest
from probe_verdicts import (
    ENV_REJECTED,
    FILE_OPS,
    NOT_THE_PROBE,
    UNRECOGNISED,
    file_writes_refused,
    launch_shown,
    memory_access_refused,
    namespaces_refused,
    outputs_unreachable,
    overlay_outcome,
    rejection_shown,
    require,
    session_socket_checks,
    shown,
    tool_var_rejection,
)

from heterodyne.sandbox.openshell_selftest import EXEC_CHECKS, PROBES_SOURCE, _results
from heterodyne.sandbox.selftest import SelfTestFailed

MEM = ("targets 1 7\nchild 7\ngetfd EPERM\n"
       "attach 1 EPERM\nreadv 1 EPERM\nwritev 1 EPERM\nmem 1 EACCES\n"
       "attach 7 EPERM\nreadv 7 EPERM\nwritev 7 EPERM\nmem 7 EACCES\ndone\n")
FILES = "".join(f"{op} {name} EROFS\n" for op, name in FILE_OPS) + "done\n"
OWN = "mnt mnt:[4026531841]\n"
NS = OWN + "unshare-none EPERM\nunshare-user-mount EPERM\n" + OWN + "done\n"
NO_NS = "forged=written\nunshare=EPERM\ndone\n"
RAN = ("forged=written\nunshare=OK\nmaps=OK\nbind-probe=OK\nbind-cli=OK\nconnect=OK\nsent=OK\nrun=0\n"
       "done\n")
CURLS = ("probe 40\ncurl 41 socket:ENXIO socket:ENXIO\ncurl 42 gone gone\ncurl 43 gone\nprobe 40 exited\n"
         "done\n")


def test_complete_refusals_pass() -> None:
    assert memory_access_refused(0, MEM) == ""
    assert file_writes_refused(0, FILES) == ""
    assert file_writes_refused(0, FILES.replace("append token EROFS", "append token EACCES")) == ""
    assert namespaces_refused(0, NS) == ""
    assert outputs_unreachable(0, CURLS) == ""


@pytest.mark.parametrize(("out", "outcome"), [
    (NO_NS, "refused"),                                                          # no namespace at all
    ("forged=written\nunshare=OK\nmaps=EACCES\ndone\n", "refused"),               # Landlock: /proc read-only
    ("forged=written\nunshare=OK\nmaps=OK\nbind-probe=EPERM\ndone\n", "refused"),
    ("forged=written\nunshare=OK\nmaps=OK\nbind-probe=OK\nbind-cli=EPERM\ndone\n", "refused"),
    (RAN, "ran"),
    (RAN.replace("sent=OK", "sent=EPIPE"), "ran"),          # connected, then rejected before the send landed
])
def test_a_complete_overlay_check_reports_its_outcome(out: str, outcome: str) -> None:
    assert overlay_outcome(0, out) == outcome


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (1, MEM),                                               # did not exit cleanly
    (0, MEM.replace("done\n", "")),                         # stopped early
    (0, MEM.replace("child 7\n", "")),                      # no child named
    (0, MEM.replace("targets 1 7\n", "")),                  # no target set
    (0, MEM.replace("targets 1 7", "targets 1")),           # its child is not a target
    (0, MEM.replace("targets 1 7", "targets 1 7 9")),       # a target with no operation at all
    (0, MEM.replace("targets 1 7", "targets 1 7 7")),
    (0, MEM.replace("attach 7", "attach 8")),               # an operation on a process not enumerated
    (0, MEM.replace("readv 1 EPERM\n", "")),                # a process missing an operation
    (0, MEM.replace("mem 1 EACCES\n", "mem 1 EACCES\nmem 1 EACCES\n")),     # one operation twice
    (0, MEM.replace("getfd EPERM\n", "")),
    (0, MEM.replace("getfd EPERM", "getfd EBADF")),         # unfiltered: no pidfd, so EBADF
    (0, MEM.replace("attach 7 EPERM", "attach 7 OK")),
    (0, MEM.replace("attach 7 EPERM", "attach 7 ERROR")),   # Codex T15 r1: not an errno at all
    (0, MEM.replace("writev 7 EPERM", "writev 7 ESRCH")),   # Codex T15 r1: the target is gone
    (0, MEM.replace("mem 7 EACCES", "mem 7 ENOENT")),       # Codex T15 r1: the target is gone
    (0, MEM.replace("mem 7 EACCES", "mem 7 EPERM")),        # refused by something but Landlock
    (0, MEM.replace("attach 1 EPERM", "attach 1 EACCES")),
    (0, MEM.replace("mem 1 EACCES", "mem 1 ?")),
    (0, MEM.replace("mem 1 EACCES", "mem 1 EACCES extra")),
])
def test_incomplete_or_permitted_memory_access_fails(rc: int, out: str) -> None:
    assert memory_access_refused(rc, out) != ""


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (1, FILES),
    (0, FILES.replace("done\n", "")),
    (0, FILES.replace("create hz-new EROFS\n", "")),                        # an operation missing
    (0, FILES + "extra x EROFS\n"),
    (0, "".join(reversed(FILES.splitlines(keepends=True)[:-1])) + "done\n"),     # out of order
    (0, FILES.replace("create hz-new EROFS", "create hz-new 127")),         # Codex T15 r1: a status
    (0, FILES.replace("rename token EROFS", "rename token OK")),
    (0, FILES.replace("append token EROFS", "append token EPERM")),
    (0, FILES.replace("append token EROFS", "append token MISSING")),       # nothing there to refuse
    (0, FILES.replace("bind p.sock EROFS", "bind p.sock PRESENT")),         # EEXIST would refuse it
    (0, FILES.replace("unlink s.sock EROFS", "unlink s.sock ENOENT")),
])
def test_incomplete_or_permitted_file_writes_fail(rc: int, out: str) -> None:
    assert file_writes_refused(rc, out) != ""


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (1, NS),
    (0, NS.replace("done\n", "")),
    (0, NS.replace("unshare-none EPERM\n", "")),
    (0, NS.replace("unshare-none EPERM", "unshare-none OK")),               # unfiltered: a no-op
    (0, NS.replace("unshare-user-mount EPERM", "unshare-user-mount OK")),
    (0, NS.replace("unshare-user-mount EPERM", "unshare-user-mount EINVAL")),
    (0, NS.replace("unshare-user-mount EPERM", "unshare-user-mount 127")),  # Codex T15 r1: no unshare
    (0, NS.replace("unshare-user-mount EPERM", "refused")),
    (0, OWN + "unshare-none EPERM\nunshare-user-mount EPERM\nmnt mnt:[4026532999]\ndone\n"),   # it moved
    (0, "mnt x\n" + NS.split("\n", 1)[1]),
])
def test_incomplete_or_permitted_namespaces_fail(rc: int, out: str) -> None:
    assert namespaces_refused(rc, out) != ""


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (1, NO_NS),                                             # the check itself failed: a setup failure
    (0, NO_NS.replace("forged=written\n", "")),            # the forged file was never written
    (0, NO_NS.replace("done\n", "")),
    (0, NO_NS.replace("unshare=EPERM", "unshare=127")),     # Codex T15 r1: a status, not an answer
    (0, NO_NS.replace("unshare=EPERM", "unshare=ENOSYS")),
    (0, NO_NS.replace("unshare=EPERM", "unshare=OK")),      # a namespace, then nothing
    (0, NO_NS.replace("unshare=EPERM\n", "unshare=EPERM\nmaps=OK\n")),      # a step after a refusal
    (0, "forged=written\nunshare=OK\nmaps=EPERM\ndone\n"),
    (0, "forged=written\nunshare=OK\nmaps=OK\nbind-probe=ENOENT\ndone\n"),
    (0, "forged=written\nunshare=OK\nmaps=OK\nbind-probe=OK\nbind-cli=EACCES\ndone\n"),
    (0, RAN.replace("run=0", "run=127")),                   # Codex T15 r1: the forged probe never ran
    (0, RAN.replace("run=0", "run=x")),
    (0, RAN.replace("connect=OK", "connect=ENOENT")),       # it never reached the channel
    (0, RAN.replace("connect=OK\n", "")),
    (0, RAN.replace("sent=OK", "sent=EBADF")),
    (0, RAN.replace("run=0\n", "")),
    (0, RAN.replace("bind-cli=OK\n", "")),
])
def test_an_incomplete_overlay_check_fails(rc: int, out: str) -> None:
    assert overlay_outcome(rc, out) not in ("refused", "ran")


@pytest.mark.parametrize(("rc", "out"), [
    (0, ""),
    (0, "probe 40\nprobe 40 exited\ndone\n"),               # no curl seen while the probe ran
    (0, "probe 40\ncurl 42 gone gone\ncurl 43 gone\nprobe 40 exited\ndone\n"),        # none seen whole
    (1, CURLS),
    (0, CURLS.replace("done\n", "")),
    (0, CURLS.replace("probe 40\n", "", 1)),                # the probe was never found
    (0, CURLS.replace("probe 40 exited", "probe 40 running")),   # Codex T15 r1: stopped before the probe
    (0, CURLS.replace("probe 40 exited", "probe 41 exited")),
    (0, "probe none\ndone\n"),
    (0, CURLS.replace("socket:ENXIO socket:ENXIO", "pipe:OPENED socket:ENXIO")),
    (0, CURLS.replace("socket:ENXIO socket:ENXIO", "socket:ENXIO socket:OPENED")),
    (0, CURLS.replace("socket:ENXIO socket:ENXIO", "pipe:EACCES pipe:EACCES")),     # not a socket
    (0, CURLS.replace("socket:ENXIO socket:ENXIO", "socket:ENXIO error:EACCES")),   # Codex T15 r1
    (0, CURLS.replace("curl 41", "curl x")),
])
def test_an_incomplete_or_reachable_output_check_fails(rc: int, out: str) -> None:
    assert outputs_unreachable(rc, out) != ""


@pytest.mark.parametrize("line", ["/srv/someone/.claude/.credentials.json", "Traceback (most recent call",
                                  "x" * 101, "sha=abc\x1b[0m"])
def test_a_reason_never_echoes_a_path_or_free_text(line: str) -> None:
    """Codex T15 r1: a verdict's reason reaches pytest's output, so it quotes only short lines of the
    checks' own vocabulary."""
    assert line not in shown(line)
    assert line not in memory_access_refused(0, MEM.replace("attach 1 EPERM", line))
    assert line not in file_writes_refused(0, FILES.replace("create hz-new EROFS", line))
    assert line not in outputs_unreachable(0, CURLS.replace("curl 43 gone", line))
    assert shown("attach 7 ERROR") == "'attach 7 <unrecognised>'"
    assert shown("attach 7 EPERM") == "'attach 7 EPERM'"
    assert shown("mnt mnt:[4026531841]") == "'mnt mnt:[4026531841]'"


HEX = "0123456789abcdef" * 4
SECRETS = ["hunter2", "s3cr3t", HEX, HEX.upper(), "9" * 64, f"token={HEX}", f"sk-{HEX[:20]}"]


@pytest.mark.parametrize("secret", SECRETS)
def test_a_reason_never_echoes_a_secret_or_a_64_hex_value(secret: str) -> None:
    """Codex T15 r2: a line of the checks' own characters can still hold a secret, so a reason renders
    only the checks' own words, numeric PIDs and statuses; anything else is replaced."""
    for line in (secret, f"attach 1 {secret}", f"unshare={secret}", f"curl 41 socket:{secret} gone"):
        assert secret not in shown(line)
        assert UNRECOGNISED in shown(line)
    assert secret not in memory_access_refused(0, MEM.replace("attach 1 EPERM", f"attach 1 {secret}"))
    assert secret not in memory_access_refused(0, MEM.replace("getfd EPERM", f"getfd {secret}"))
    assert secret not in file_writes_refused(0, FILES.replace("append token EROFS", f"append token {secret}"))
    assert secret not in namespaces_refused(0, NS.replace("unshare-none EPERM", f"unshare-none {secret}"))
    assert secret not in overlay_outcome(0, NO_NS.replace("unshare=EPERM", f"unshare={secret}"))
    assert secret not in overlay_outcome(0, RAN.replace("sent=OK", f"sent={secret}"))
    assert secret not in outputs_unreachable(0, CURLS.replace("socket:ENXIO socket:ENXIO",
                                                              f"socket:{secret} socket:ENXIO"))


def test_closing_the_session_socket_fails_exactly_its_checks() -> None:
    """Codex T15 r2: each check whose request goes over the session socket fails once it is closed, so
    the dead-socket control expects exactly those, in the probes' order; the self-test reports the
    combined failure as that one reason."""
    over_socket = session_socket_checks(PROBES_SOURCE.read_text())
    assert over_socket == ("control-op-rejected", "hook-event-accepted")
    assert set(over_socket) <= set(EXEC_CHECKS)
    out = "".join(f"{'FAIL' if c in over_socket else 'PASS'} {c} [reply=socket error ENOENT]\n"
                  for c in EXEC_CHECKS) + "DONE 1\n"
    with pytest.raises(SelfTestFailed) as caught:
        _results(1, out.encode(), EXEC_CHECKS)
    assert str(caught.value) == ", ".join(over_socket)


HOME = "/srv/someone/.local/bin/claude"
CAPS = "workload process 12 (thread 13) holds capabilities"
PEER = ("launch self-test failed: agent-path-channel: a peer that is not the probe connected "
        "(freeze attempts: 1, 2)")


@pytest.mark.parametrize(("reason", "expect"), [
    (NOT_THE_PROBE, NOT_THE_PROBE),
    (CAPS, CAPS),
    (f"no {HOME} ancestor inside the workload netns", "no <cli> ancestor inside the workload netns"),
    ("/proc/12 unreadable (Permission denied)", "/proc/<pid> unreadable"),
    (f"{ENV_REJECTED}CLAUDE_CODE_X", f"{ENV_REJECTED}CLAUDE_CODE_X"),
    (f"{ENV_REJECTED}AWS_SECRET_ACCESS_KEY CLAUDE_CODE_X", f"{ENV_REJECTED}{UNRECOGNISED} CLAUDE_CODE_X"),
    (f"{ENV_REJECTED}{HEX}", f"{ENV_REJECTED}{UNRECOGNISED}"),
    (f"something new at {HOME}", UNRECOGNISED),
])
def test_a_channel_rejection_is_shown_only_in_its_fixed_words(reason: str, expect: str) -> None:
    """Codex T15 r2: a peer's rejection can name the CLI's path or any variable; it is shown in its
    fixed wording, with only the CLI's agent-only tool variables named."""
    assert rejection_shown(reason, {"CLAUDE_CODE_X"}) == expect


@pytest.mark.parametrize(("said", "expect"), [
    ("launch self-test failed: control-op-rejected, hook-event-accepted",
     "launch self-test failed: control-op-rejected, hook-event-accepted"),
    (PEER, PEER),
    ("launch self-test failed: agent-path-channel: failed checks: other-accounts, hunter2",
     f"launch self-test failed: agent-path-channel: failed checks: other-accounts, {UNRECOGNISED}"),
    (f"launch self-test failed: agent-path-channel: unexpected checks: token={HEX}",
     f"launch self-test failed: agent-path-channel: unexpected checks: {UNRECOGNISED}"),
    (f"launch self-test failed: agent-path-channel: {HOME}", f"launch self-test failed: agent-path-channel: "
                                                             f"{UNRECOGNISED}"),
    (f"launch self-test failed: {HEX}", f"launch self-test failed: {UNRECOGNISED}"),
    (f"{HOME}: no such file", "a launch failure outside the self-test"),
])
def test_a_launch_failure_is_shown_only_in_its_fixed_words(said: str, expect: str) -> None:
    assert launch_shown(said) == expect


@pytest.mark.parametrize("reasons", [
    [f"no {HOME} ancestor inside the workload netns"],
    [f"{ENV_REJECTED}CLAUDE_CODE_X TOKEN_{HEX}"],
    [f"{ENV_REJECTED}CLAUDE_CODE_X", f"no {HOME} ancestor inside the workload netns"],
    [f"{ENV_REJECTED}{HOME}={HEX}"],
])
def test_a_failed_check_on_a_raw_reason_shows_neither_a_path_nor_a_64_hex_value(reasons: list[str]) -> None:
    """Codex T15 r3: an assert's rewriting prints its operands raw, so the live controls check raw reasons
    through `require`, which fails with only the sanitised text: the tool-variable control's check here."""
    why = tool_var_rejection(reasons, {"CLAUDE_CODE_X"})
    with pytest.raises(pytest.fail.Exception) as caught:
        require(not why, why)
    text = str(caught.value)
    assert text and HOME not in text and HEX not in text and "TOKEN_" not in text


def test_a_tool_variable_rejection_passes_only_for_tool_variables() -> None:
    assert tool_var_rejection([f"{ENV_REJECTED}CLAUDE_CODE_X"], {"CLAUDE_CODE_X"}) == ""
    assert tool_var_rejection([f"{ENV_REJECTED}"], {"CLAUDE_CODE_X"}) != ""
    assert tool_var_rejection([], {"CLAUDE_CODE_X"}) != ""
    require(True, "never shown")
