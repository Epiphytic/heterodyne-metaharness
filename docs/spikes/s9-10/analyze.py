"""S9-10's pass conditions for one run directory (plan 4c Amendment 1 §4.5), from its traces and files.

    python3 analyze.py <run-dir> <variant> --part 1|2

Part 1: the run ended by itself (rc 0), the server was ready, the TUI connected to it and drew its screen
(the login screen: Part 1 has no login), `.codex/packages` stayed empty, nothing under the home was
executed, and no DNS lookup named api.github.com. Part 2 adds the composer, the new session's answer, the
resume showing the first prompt, no hook failure on either screen, and the shim's spool showing the hooks
ran (UserPromptSubmit). Every file a check reads must be there, and every metadata field with
its type; without them, nothing passes. Each trace must open with the exec of its command's exact argv and
hold strace's record of that process ending as its last event (`+++ exited with N +++`; a TUI may instead
be killed by the harness's SIGHUP), so a cut trace fails; with any of that wrong, no later check passes.
Prints one line per check and exits 0 only when every check passes."""

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, cast

HEX = re.compile(rb'"((?:\\x[0-9a-f]{2})*)"')
STRING = rb'"(?:\\x[0-9a-f]{2})*"'
# A successful exec with its path and argv printed whole: a cut string or array shows `...` and won't match.
ARGV = rb"\[((?:" + STRING + rb"(?:, " + STRING + rb")*)?)\]"
EXEC = re.compile(rb"(\d+) +execve\(" + STRING + rb", " + ARGV + rb", .*\) = 0$")
ENDED = re.compile(rb"(\d+) +\+\+\+ (?:exited with \d+|killed by (SIG[A-Z0-9]+)( \(core dumped\))?) \+\+\+$")
# DNS names are read only from complete sends to port 53 (btq-wi1xz): a sendto's address, or a sendmmsg
# entry's own msg_name. A NULL address (a connected socket) is never read. A count is at most 6 digits, so a
# hostile one can't reach int()'s digit limit; a longer one doesn't match and its send is dropped.
COUNT = rb"(\d{1,6})"
PORT_53 = re.compile(rb"\{sa_family=(?:AF_INET, sin_port|AF_INET6, sin6_port)=htons\(53\), [^{}]*\}")
SENDTO = re.compile(rb"\bsendto\(\d+, (" + STRING + rb")(\.\.\.)?, " + COUNT
                    + rb", [^,]*, (NULL|\{[^{}]*\}), \d+")
SENDMMSG = re.compile(rb"\bsendmmsg\(\d+, \[")
VLEN = re.compile(rb"\], " + COUNT + rb", ")                # the array's end, then sendmmsg's own count
IOV = rb"\{iov_base=(" + STRING + rb")(\.\.\.)?, iov_len=" + COUNT + rb"\}"
IOVS = re.compile(IOV)
ANY_IOV = rb"\{iov_base=" + STRING + rb"(?:\.\.\.)?, iov_len=\d{1,6}\}"
MSG = re.compile(rb"\{msg_hdr=\{msg_name=(NULL|\{[^{}]*\}), msg_namelen=\d+, msg_iov=\[(" + ANY_IOV
                 + rb"(?:, " + ANY_IOV + rb")*)\], msg_iovlen=" + COUNT
                 + rb", msg_controllen=0, msg_flags=[^{}\[\],]*\}(?:, msg_len=\d+)?\}")
LABEL = re.compile(rb"[A-Za-z0-9_-]{1,63}")
GITHUB_API = b"\x03api\x06github\x03com"
LOGIN_SCREEN = b"Sign in with ChatGPT"
COMPOSER = "›".encode()                                      # Codex.prompt_marker
# The one signal each command may die of, beside `exited with N`: a TUI, of the harness's SIGHUP. The
# app-server exits 0 on the harness's SIGTERM (r3's Part 1 re-run), so it must exit.
STOP: dict[str, bytes | None] = {"server.strace": None, "tui.strace": b"SIGHUP",
                                 "tui-resume.strace": b"SIGHUP"}
TRACES = {1: {"server.strace": ("argv.json", "server"), "tui.strace": ("argv.json", "tui_new")},
          2: {"server.strace": ("argv.json", "server"), "tui.strace": ("argv.json", "tui_new"),
              "tui-resume.strace": ("argv-resume.json", "tui_resume")}}
# Files that must hold something. inside.packages and hooks need only exist: hooks_ran judges hooks.
NONEMPTY = {1: ("argv.json", "rc", "server_ready", "inside", "tui.screen"),
            2: ("argv.json", "argv-resume.json", "rc", "server_ready", "inside", "trust.txt", "tui.screen",
                "composer", "answered", "thread", "resumed", "tui-resume.screen")}
PRESENT = {1: ("inside.packages",), 2: ("inside.packages", "hooks")}
# What Codex 0.160 draws for a hook that failed or blocked (S9-10 Part 2's EDZixy run).
HOOK_FAILURE = (b"Hook failed", b"Blocked by hook", b"hook exited with code")
PART_CHECKS = {1: ("tui_screen_shows_login",), 2: ("tui_screen_shows_composer", "new_session_answered",
                                                  "thread_found", "resume_shows_the_first_prompt",
                                                  "no_hook_failure", "hooks_ran")}
# The metadata each check reads: an absolute path, a unix:// socket, or an argv of strings run by full path.
FIELDS = {1: {"argv.json": {"home": "path", "app_socket": "socket", "server": "argv", "tui_new": "argv"}},
          2: {"argv.json": {"home": "path", "app_socket": "socket", "server": "argv", "tui_new": "argv"},
              "argv-resume.json": {"tui_resume": "argv"}}}


def strings(line: bytes) -> list[bytes]:
    return [bytes.fromhex(m.group(1).replace(b"\\x", b"").decode()) for m in HEX.finditer(line)]


def unhex(quoted: bytes) -> bytes:
    return bytes.fromhex(quoted[1:-1].replace(b"\\x", b"").decode())


def query_name(packet: bytes) -> str | None:
    """The name a well-formed single-question DNS query asks for, or None for anything else: QR and opcode
    0, one question, no answers or authority, and nothing after the question but one optional OPT record."""
    if len(packet) < 12:
        return None
    flags, qd, an, ns, ar = struct.unpack(">5H", packet[2:12])
    if flags & 0xF800 or qd != 1 or an or ns or ar > 1:
        return None
    i, labels = 12, []
    while i < len(packet) and packet[i] != 0:
        n = packet[i]                                        # over 63 is also a compression pointer
        if n > 63 or i + 1 + n > len(packet) or not LABEL.fullmatch(label := packet[i + 1:i + 1 + n]):
            return None
        labels.append(label.decode())
        i += 1 + n
    end = i + 1 + 4                                          # the root byte, QTYPE and QCLASS
    if i >= len(packet) or not labels or i + 1 - 12 > 255 or end > len(packet):
        return None
    name = ".".join(labels)
    if not ar:
        return name if end == len(packet) else None
    # One OPT record: the root name, then TYPE 41, CLASS, TTL and RDLENGTH, then RDATA ending the packet.
    if len(packet) < end + 11 or packet[end] != 0:
        return None
    rtype, _, _, rdlength = struct.unpack(">HHIH", packet[end + 1:end + 11])
    i, rdata_end = end + 11, end + 11 + rdlength
    if rtype != 41 or rdata_end != len(packet):
        return None
    while i + 4 <= rdata_end:                                # whole options (CODE, LENGTH, data) fill RDATA
        i += 4 + struct.unpack(">H", packet[i + 2:i + 4])[0]
    return name if i == rdata_end else None


def dns_payloads(line: bytes) -> tuple[list[bytes], int]:
    """The complete payloads one trace line sends to port 53, and how many other payloads it sends. A
    `<... resumed>` line holds no arguments; an `<unfinished ...>` one holds them all and is read as usual."""
    if b" resumed>" in line:
        return [], 0
    if b"sendmmsg(" in line:
        if (start := SENDMMSG.search(line)) is not None:
            payloads, dropped, entries, i = [], 0, 0, start.end()
            while (msg := MSG.match(line, i)) is not None:   # each entry on its own, iovecs joined in order
                iovs = list(IOVS.finditer(msg.group(2)))
                whole = all(v.group(2) is None and len(unhex(v.group(1))) == int(v.group(3)) for v in iovs)
                if PORT_53.fullmatch(msg.group(1)) and whole and int(msg.group(3)) == len(iovs):
                    payloads.append(b"".join(unhex(v.group(1)) for v in iovs))
                else:
                    dropped += 1
                entries, i = entries + 1, msg.end()
                if (vlen := VLEN.match(line, i)) is not None and int(vlen.group(1)) == entries:
                    return payloads, dropped
                if line.startswith(b"]", i):                 # a vlen unlike the entries shown: all of it
                    return [], entries
                if not line.startswith(b", ", i):
                    break
                i += 2
        return [], max(1, line.count(b"{msg_hdr="))         # an abbreviated or unreadable array: all of it
    if b"sendto(" not in line:
        return [], 0
    call = SENDTO.search(line)
    if (call is None or call.group(2) is not None or not PORT_53.fullmatch(call.group(4))
            or len(buf := unhex(call.group(1))) != int(call.group(3))):
        return [], 1
    return [buf], 0


def nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def said(run: Path, name: str) -> bool:
    return nonempty(run / name) and (run / name).read_text().strip() == "yes"


def valid(kind: str, value: object) -> bool:
    if kind == "socket":
        return isinstance(value, str) and value.startswith("unix:///")
    if kind == "path":
        return isinstance(value, str) and value.startswith("/") and value != "/"
    items = cast(list[object], value) if isinstance(value, list) else []
    return bool(items) and all(isinstance(a, str) and a for a in items) and valid("path", items[0])


def metadata(run: Path, part: int) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Each metadata file's object, and what is missing or malformed in them."""
    meta: dict[str, dict[str, Any]] = {}
    bad: list[str] = []
    for name, fields in FIELDS[part].items():
        try:
            loaded: object = json.loads((run / name).read_text()) if nonempty(run / name) else None
        except (ValueError, UnicodeDecodeError):
            loaded = None
        if not isinstance(loaded, dict):
            bad.append(name)
            continue
        meta[name] = cast(dict[str, Any], loaded)
        bad += [f"{name}:{k}" for k, kind in fields.items() if not valid(kind, meta[name].get(k))]
    return meta, bad


def root(raw: bytes, argv: list[str], stop: bytes | None) -> tuple[bool, bool]:
    """Whether the trace opens with the exec of exactly `argv`, and whether its last event for that process
    is the process's end, on a whole last line."""
    lines = raw.splitlines()
    first = EXEC.fullmatch(lines[0]) if lines else None
    if first is None or strings(first.group(2)) != [a.encode() for a in argv]:
        return False, False
    pid = first.group(1)
    own = [ln for ln in lines if ln.split(b" ", 1)[0] == pid]
    last = ENDED.fullmatch(own[-1])
    ends = sum(1 for ln in own if ENDED.fullmatch(ln))
    # `exited with N` (we stop it, so any N); killed only by this command's own stop signal, without a core.
    return True, (raw.endswith(b"\n") and last is not None and ends == 1
                  and (last.group(2) is None or (stop is not None and last.group(2) == stop
                                                 and last.group(3) is None)))


def analyze(run: Path, part: int) -> tuple[dict[str, bool], dict[str, Any]]:
    missing = [f for f in (*NONEMPTY[part], *TRACES[part]) if not nonempty(run / f)]
    missing += [f for f in PRESENT[part] if not (run / f).is_file()]
    meta, malformed = metadata(run, part)
    rc = (run / "rc").read_text().strip() if nonempty(run / "rc") else "<none>"
    facts: dict[str, Any] = {"rc": rc, "missing": missing, "malformed": malformed}
    gated = ["ended_by_itself", "server_ready", "tui_connected_to_app_socket", "packages_empty",
             "no_exec_under_home", "no_api_github_lookup", *PART_CHECKS[part]]
    if missing or malformed:                                 # nothing passes on a missing file or field
        return {"evidence_complete": False, **{f"{t}_{c}": False for t in TRACES[part]
                                              for c in ("shows_its_exec", "ran_to_its_end")},
                **dict.fromkeys(gated, False)}, facts
    checks: dict[str, bool] = {}
    traced: dict[str, list[bytes]] = {}
    for trace, (source, key) in TRACES[part].items():
        raw = (run / trace).read_bytes()
        traced[trace] = raw.splitlines()
        shown, ended = root(raw, meta[source][key], STOP[trace])
        # The wrapper's marker: the record didn't come within stop_root's bound.
        checks[f"{trace}_shows_its_exec"] = shown
        checks[f"{trace}_ran_to_its_end"] = ended and not (run / f"{trace}.unended").exists()
    complete = all(checks.values())                          # every check below needs all of the above
    # rc 124 is the cap; anything else, a failed step.
    checks = {"evidence_complete": complete, **checks, "ended_by_itself": complete and rc == "0"}
    lines = [ln for t in traced.values() for ln in t]
    home = str(meta["argv.json"]["home"]).encode()
    execs = [s[0] for ln in lines if b" execve(" in ln and (s := strings(ln))]
    under_home = [e for e in execs if e.startswith(home + b"/")]
    payloads, dropped = [], 0
    for ln in lines:
        sent, other = dns_payloads(ln)
        payloads += sent
        dropped += other
    found = [query_name(p) for p in payloads]
    names = sorted({n for n in found if n})[:32]
    github = any(GITHUB_API in s or b"api.github.com" in s for ln in lines for s in strings(ln))
    sockets = sorted({s.decode("utf-8", "replace") for ln in traced["tui.strace"]
                      if b"connect(" in ln and b"AF_UNIX" in ln for s in strings(ln)})
    app = str(meta["argv.json"]["app_socket"]).removeprefix("unix://")
    packages = Path(home.decode()) / ".codex" / "packages"
    host_left = sorted(str(p.relative_to(packages)) for p in packages.rglob("*")) if packages.is_dir() else []
    inside = (run / "inside.packages").read_text().splitlines() if (run / "inside.packages").is_file() else []
    left = host_left + [ln for ln in inside if "No such file" not in ln]
    screen = (run / "tui.screen").read_bytes() if nonempty(run / "tui.screen") else b""
    checks.update({
        "server_ready": complete and said(run, "server_ready"),
        "tui_connected_to_app_socket": complete and any(app in s for s in sockets),
        "packages_empty": complete and left == [],
        "no_exec_under_home": complete and not under_home,
        "no_api_github_lookup": complete and not github,
    })
    if part == 1:
        checks["tui_screen_shows_login"] = complete and LOGIN_SCREEN in screen
    else:
        checks["tui_screen_shows_composer"] = complete and said(run, "composer") and COMPOSER in screen
        checks["new_session_answered"] = complete and said(run, "answered")
        checks["thread_found"] = complete and nonempty(run / "thread")
        checks["resume_shows_the_first_prompt"] = complete and said(run, "resumed")
        screens = screen + (run / "tui-resume.screen").read_bytes() if complete else b""
        hooks = (run / "hooks").read_text(errors="replace").split() if complete else []
        checks["no_hook_failure"] = complete and not any(f in screens for f in HOOK_FAILURE)
        checks["hooks_ran"] = complete and "UserPromptSubmit" in hooks
    facts.update({"dns": names, "dns_dropped": dropped + found.count(None), "sockets": sockets,
                  "under_home": [e.decode("utf-8", "replace") for e in under_home], "left": left[:16]})
    return checks, facts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("variant")
    ap.add_argument("--part", type=int, choices=(1, 2), required=True)
    args = ap.parse_args()
    checks, facts = analyze(args.run, args.part)
    passed = all(checks.values())
    print(f"S9-10 Part {args.part}, {args.variant}: {args.run}")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")
    for name, value in facts.items():
        print(f"  {name}: {value}")
    print(f"  verdict: {'PASS' if passed else 'FAIL'}")
    (args.run / "result.json").write_text(json.dumps({"part": args.part, "passed": passed, "checks": checks,
                                                      **facts}))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
