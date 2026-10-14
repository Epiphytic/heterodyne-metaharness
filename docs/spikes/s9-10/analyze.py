"""S9-10's pass conditions for one run directory (plan 4c Amendment 1 §4.5), from its traces and files.

    python3 analyze.py <run-dir> <variant> --part 1|2

Part 1: the run ended by itself (rc 0), the server was ready, the TUI connected to it and drew its screen
(the login screen: Part 1 has no login), `.codex/packages` stayed empty, nothing under the home was
executed, and no DNS lookup named api.github.com. Part 2 adds the composer, the new session's answer and
the resume showing the first prompt. Every file a check reads must be there, and every metadata field with
its type; without them, nothing passes. Each trace must open with the exec of its command's exact argv and
hold strace's record of that process ending (`+++ exited with N +++`, or killed by the harness's own stop
signal), so a cut trace fails; with any of that wrong, no later check passes. Prints one line per check and
exits 0 only when every check passes."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

HEX = re.compile(rb'"((?:\\x[0-9a-f]{2})*)"')
STRING = rb'"(?:\\x[0-9a-f]{2})*"'
# A successful exec with its path and argv printed whole: a cut string or array shows `...` and won't match.
ARGV = rb"\[((?:" + STRING + rb"(?:, " + STRING + rb")*)?)\]"
EXEC = re.compile(rb"(\d+) +execve\(" + STRING + rb", " + ARGV + rb", .*\) = 0$")
ENDED = re.compile(rb"(\d+) +\+\+\+ (?:exited with \d+|killed by (SIG[A-Z0-9]+)( \(core dumped\))?) \+\+\+$")
GITHUB_API = b"\x03api\x06github\x03com"
LOGIN_SCREEN = b"Sign in with ChatGPT"
COMPOSER = "›".encode()                                      # Codex.prompt_marker
# Each trace, with the file and key of the argv it traced, and the signal the harness stops it with.
STOP = {"server.strace": b"SIGTERM", "tui.strace": b"SIGHUP", "tui-resume.strace": b"SIGHUP"}
TRACES = {1: {"server.strace": ("argv.json", "server"), "tui.strace": ("argv.json", "tui_new")},
          2: {"server.strace": ("argv.json", "server"), "tui.strace": ("argv.json", "tui_new"),
              "tui-resume.strace": ("argv-resume.json", "tui_resume")}}
# Files that must hold something. inside.packages need only exist: empty is e25's result.
NONEMPTY = {1: ("argv.json", "rc", "server_ready", "inside", "tui.screen"),
            2: ("argv.json", "argv-resume.json", "rc", "server_ready", "inside", "trust.txt", "tui.screen",
                "composer", "answered", "thread", "resumed", "tui-resume.screen")}
PRESENT = ("inside.packages",)
PART_CHECKS = {1: ("tui_screen_shows_login",), 2: ("tui_screen_shows_composer", "new_session_answered",
                                                  "thread_found", "resume_shows_the_first_prompt")}
# The metadata each check reads: an absolute path, a unix:// socket, or an argv of strings run by full path.
FIELDS = {1: {"argv.json": {"home": "path", "app_socket": "socket", "server": "argv", "tui_new": "argv"}},
          2: {"argv.json": {"home": "path", "app_socket": "socket", "server": "argv", "tui_new": "argv"},
              "argv-resume.json": {"tui_resume": "argv"}}}


def strings(line: bytes) -> list[bytes]:
    return [bytes.fromhex(m.group(1).replace(b"\\x", b"").decode()) for m in HEX.finditer(line)]


def qname(packet: bytes) -> str | None:
    """The first question's name of a DNS query, or None if `packet` doesn't read as one."""
    i, labels = 12, []
    while i < len(packet) and packet[i] != 0:
        n = packet[i]
        if n > 63 or i + 1 + n > len(packet):
            return None
        labels.append(packet[i + 1:i + 1 + n].decode("ascii", "replace"))
        i += 1 + n
    return ".".join(labels) if labels and len(packet) > 12 else None


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


def root(lines: list[bytes], argv: list[str], stop: bytes) -> tuple[bool, bool]:
    """Whether the trace opens with the exec of exactly `argv`, and whether it holds that process's end."""
    first = EXEC.fullmatch(lines[0]) if lines else None
    if first is None or strings(first.group(2)) != [a.encode() for a in argv]:
        return False, False
    pid = first.group(1)
    ends = [m for ln in lines if (m := ENDED.fullmatch(ln)) and m.group(1) == pid]
    # `exited with N` (we stop it, so any N); killed only by the harness's stop signal, without a core.
    return True, len(ends) == 1 and (ends[0].group(2) is None or (ends[0].group(2) == stop
                                                                    and ends[0].group(3) is None))


def analyze(run: Path, part: int) -> tuple[dict[str, bool], dict[str, Any]]:
    missing = [f for f in (*NONEMPTY[part], *TRACES[part]) if not nonempty(run / f)]
    missing += [f for f in PRESENT if not (run / f).is_file()]
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
        traced[trace] = (run / trace).read_bytes().splitlines()
        checks[f"{trace}_shows_its_exec"], checks[f"{trace}_ran_to_its_end"] = root(
            traced[trace], meta[source][key], STOP[trace])
    complete = all(checks.values())                          # every check below needs all of the above
    # rc 124 is the cap; anything else, a failed step.
    checks = {"evidence_complete": complete, **checks, "ended_by_itself": complete and rc == "0"}
    lines = [ln for t in traced.values() for ln in t]
    home = str(meta["argv.json"]["home"]).encode()
    execs = [s[0] for ln in lines if b" execve(" in ln and (s := strings(ln))]
    under_home = [e for e in execs if e.startswith(home + b"/")]
    names = sorted({n for ln in lines if b"sendto(" in ln or b"sendmmsg(" in ln
                    for s in strings(ln) if (n := qname(s))})
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
    facts.update({"dns": names, "sockets": sockets,
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
