"""S9-10's pass conditions for one run directory (plan 4c Amendment 1 §4.5), from its traces and files:
the server was ready, the TUI connected to it, `.codex/packages` stayed empty, nothing under the home was
executed, and no DNS lookup named api.github.com. Prints one line per check and the DNS names attempted.

    python3 analyze.py <run-dir> <variant>"""

import json
import re
import sys
from pathlib import Path

HEX = re.compile(rb'"((?:\\x[0-9a-f]{2})*)"')
GITHUB_API = b"\x03api\x06github\x03com"


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


def main() -> None:
    run, variant = Path(sys.argv[1]), sys.argv[2]
    meta = json.loads((run / "argv.json").read_text())
    home = meta["home"].encode()
    lines = [ln for f in ("server.strace", "tui.strace", "tui-resume.strace") if (run / f).exists()
             for ln in (run / f).read_bytes().splitlines()]
    execs = [s[0] for ln in lines if b" execve(" in ln and (s := strings(ln))]
    under_home = [e for e in execs if e.startswith(home + b"/")]
    names = sorted({n for ln in lines if b"sendto(" in ln or b"sendmmsg(" in ln
                    for s in strings(ln) if (n := qname(s))})
    github = any(GITHUB_API in s or b"api.github.com" in s for ln in lines for s in strings(ln))
    tui = (run / "tui.strace").read_bytes().splitlines() if (run / "tui.strace").exists() else []
    sockets = sorted({s.decode("utf-8", "replace") for ln in tui if b"connect(" in ln and b"AF_UNIX" in ln
                      for s in strings(ln)})
    app = meta["app_socket"].removeprefix("unix://")
    tui_connected = any(app in s or "codex-daemon" in s for s in sockets)
    packages = Path(meta["home"]) / ".codex" / "packages"
    host_left = (sorted(str(p.relative_to(packages)) for p in packages.rglob("*")) if packages.is_dir()
                 else [])
    inside = run / "inside.packages"
    inside_left = inside.read_text().splitlines() if inside.exists() else ["<not listed>"]
    left = host_left + [ln for ln in inside_left if "No such file" not in ln]
    screen = (run / "tui.screen").read_bytes() if (run / "tui.screen").exists() else b""
    composer = "›".encode() in screen                       # Codex.prompt_marker
    ready = run / "server_ready"
    checks = {
        "server_ready": ready.exists() and ready.read_text().strip() == "yes",
        "tui_connected_to_app_socket": tui_connected,
        "tui_screen_shows_composer": composer,
        "packages_empty_on_host": left == [],
        "no_exec_under_home": not under_home,
        "no_api_github_lookup": not github,
    }
    def said(name: str) -> bool:
        return (run / name).exists() and (run / name).read_text().strip() == "yes"

    if (run / "answered").exists():                          # Part 2 (part2.sh)
        checks["tui_screen_shows_composer"] = said("composer")
        checks["new_session_answered"] = said("answered")
        checks["resume_shows_the_first_prompt"] = said("resumed")
    print(f"S9-10, {variant}: {run}")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")
    print(f"  dns names attempted: {names}")
    print(f"  unix sockets the TUI connected to: {sockets}")
    print(f"  execs under the home: {[e.decode('utf-8', 'replace') for e in under_home]}")
    print(f"  left in .codex/packages: {left[:16]}")
    (run / "result.json").write_text(json.dumps({"checks": checks, "dns": names, "sockets": sockets}))


if __name__ == "__main__":
    main()
