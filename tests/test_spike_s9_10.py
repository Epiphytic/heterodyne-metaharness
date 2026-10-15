"""S9-10's analyzer and wrappers (docs/spikes/s9-10/), offline on crafted run directories: missing or empty
evidence, a capped run or any failed check gives a non-zero exit, and both wrappers pass that on (r2). Every
metadata field a check reads must be there with its type, and each trace must open with the exec of its exact
argv and end with strace's record of that process ending (r3)."""

import importlib.util
import json
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

SPIKE = Path(__file__).resolve().parents[1] / "docs" / "spikes" / "s9-10"
CLI = "/store/cli/bin/codex"
SOCK = "/run/hz-bridge/app.sock"


def hx(text: str | bytes) -> str:
    raw = text.encode() if isinstance(text, str) else text
    return "".join(f"\\x{b:02x}" for b in raw)


def dns(name: str) -> bytes:
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split("."))
    return b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + labels + b"\x00\x00\x01\x00\x01"


def execve(argv: list[str], pid: int = 7) -> str:
    args = ", ".join(f'"{hx(a)}"' for a in argv)
    return f'{pid} execve("{hx(argv[0])}", [{args}], 0x7ffd /* 4 vars */) = 0'


def ended(how: str = "exited with 0", pid: int = 7) -> str:
    return f"{pid} +++ {how} +++"


def trace(argv: list[str], *body: str, end: str | None = "exited with 0") -> str:
    """A trace of `argv`: its exec, `body`, then (unless `end` is None) strace's record of it ending."""
    return "\n".join([execve(argv), *body, *([ended(end)] if end else [])]) + "\n"


def connect(path: str) -> str:
    return f'7 connect(5, {{sa_family=AF_UNIX, sun_path="{hx(path)}"}}, 110) = 0'


def sendto(name: str) -> str:
    return f'7 sendto(6, "{hx(dns(name))}", 33, MSG_NOSIGNAL, NULL, 0) = 33'


def argv_json(run: Path) -> dict[str, object]:
    return {"home": str(run / "s" / "home"), "app_socket": f"unix://{SOCK}", "server": [CLI, "app-server"],
            "tui_new": [CLI, "--remote", f"unix://{SOCK}"]}


def evidence(run: Path, part: int, meta: dict[str, object] | None = None) -> Path:
    """A run directory with everything a passing run of `part` leaves."""
    run.mkdir(parents=True, exist_ok=True)
    meta = meta or argv_json(run)
    server, tui = cast_list(meta["server"]), cast_list(meta["tui_new"])
    files = {
        "argv.json": json.dumps(meta), "rc": "0\n", "server_ready": "yes\n", "inside": "uid=0\n",
        "inside.packages": "",
        "server.strace": trace(server, sendto("raw.githubusercontent.com"), end="exited with 0"),
        "tui.strace": trace(tui, connect(SOCK), end="killed by SIGHUP"),
        "tui.screen": "Welcome to Codex\nSign in with ChatGPT\n",
    }
    if part == 2:
        files.update({
            "argv-resume.json": json.dumps({"tui_resume": [tui[0], "resume", "t-1"]}),
            "trust.txt": "listed 0\n",
            "tui.screen": "› Reply with only the word pong.\n", "composer": "yes\n", "answered": "yes\n",
            "thread": "t-1\n", "resumed": "yes\n", "tui-resume.screen": "› Reply with only the word pong.\n",
            "tui-resume.strace": trace([tui[0], "resume", "t-1"], end="killed by SIGHUP"),
            "hooks": "SessionStart\nUserPromptSubmit\nStop\n",
        })
    for name, text in files.items():
        (run / name).write_text(text)
    return run


def cast_list(value: object) -> list[str]:
    assert isinstance(value, list)
    return [str(v) for v in value]


def analyze(run: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SPIKE / "analyze.py"), str(run), "e25", *args],  # noqa: S603
                          capture_output=True, text=True, timeout=60, check=False)


def checks(run: Path) -> dict[str, bool]:
    return json.loads((run / "result.json").read_text())["checks"]


@pytest.mark.parametrize("part", [1, 2])
def test_a_complete_passing_run_exits_zero(tmp_path: Path, part: int) -> None:
    run = evidence(tmp_path / "r", part)
    done = analyze(run, "--part", str(part))
    assert done.returncode == 0, done.stdout
    assert all(checks(run).values())


def test_the_part_must_be_named(tmp_path: Path) -> None:
    assert analyze(evidence(tmp_path / "r", 1)).returncode != 0


@pytest.mark.parametrize("part", [1, 2])
@pytest.mark.parametrize("name", ["server.strace", "tui.strace", "argv.json", "rc", "server_ready",
                                  "tui.screen", "inside", "inside.packages"])
def test_a_missing_file_fails_and_nothing_passes_on_it(tmp_path: Path, part: int, name: str) -> None:
    run = evidence(tmp_path / "r", part)
    (run / name).unlink()
    assert analyze(run, "--part", str(part)).returncode == 1
    found = checks(run)
    assert not found["evidence_complete"]
    assert not found["no_exec_under_home"] and not found["no_api_github_lookup"]


@pytest.mark.parametrize("name", ["server.strace", "tui.strace", "tui.screen"])
def test_an_empty_trace_or_screen_fails(tmp_path: Path, name: str) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / name).write_text("")
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["evidence_complete"]


def test_a_trace_of_some_other_command_fails(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / "server.strace").write_text(trace(["/usr/bin/true"]))
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["server.strace_shows_its_exec"]
    assert not checks(run)["no_exec_under_home"]


@pytest.mark.parametrize("rc", ["124", "1", "137"])
def test_a_capped_or_failed_run_exits_non_zero(tmp_path: Path, rc: str) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / "rc").write_text(rc + "\n")
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["ended_by_itself"]


@pytest.mark.parametrize("name", ["answered", "resumed", "thread", "argv-resume.json", "tui-resume.strace",
                                  "tui-resume.screen", "composer", "trust.txt"])
def test_part_2_needs_its_answer_and_resume_evidence(tmp_path: Path, name: str) -> None:
    run = evidence(tmp_path / "r", 2)
    (run / name).unlink()
    assert analyze(run, "--part", "2").returncode == 1
    found = checks(run)
    assert not found["evidence_complete"] and not found["resume_shows_the_first_prompt"]


@pytest.mark.parametrize("name", ["answered", "resumed", "composer"])
def test_part_2_fails_on_a_no(tmp_path: Path, name: str) -> None:
    run = evidence(tmp_path / "r", 2)
    (run / name).write_text("no\n")
    assert analyze(run, "--part", "2").returncode == 1


def test_part_1_evidence_does_not_pass_part_2(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    assert analyze(run, "--part", "2").returncode == 1


def test_an_exec_under_the_home_fails(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    home = argv_json(run)["home"]
    tried = execve([f"{home}/.codex/tmp/arg0/x/getconf"], pid=8).replace(" = 0", " = -1 ENOENT")
    (run / "server.strace").write_text(trace([CLI, "app-server"], tried))
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["no_exec_under_home"]


def test_an_api_github_lookup_fails(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / "tui.strace").write_text(trace(cast_list(argv_json(run)["tui_new"]), connect(SOCK),
                                          sendto("api.github.com")))
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["no_api_github_lookup"]


# --- r3: metadata fields, the exact argv, and a trace that ran to its end ---

@pytest.mark.parametrize("part", [1, 2])
@pytest.mark.parametrize("field", ["home", "app_socket", "server", "tui_new"])
@pytest.mark.parametrize("bad", ["deleted", "", None, ["x"], [], "relative"])
def test_a_missing_or_malformed_metadata_field_fails_everything(tmp_path: Path, part: int, field: str,
                                                                bad: object) -> None:
    if bad == "relative" and field not in ("home", "server", "tui_new"):
        pytest.skip("only paths must be absolute")
    run = evidence(tmp_path / "r", part)
    meta = json.loads((run / "argv.json").read_text())
    if bad == "deleted":
        del meta[field]
    elif bad == "relative":
        meta[field] = "home" if field == "home" else ["codex", *meta[field][1:]]
    elif bad == ["x"] and field in ("server", "tui_new"):
        meta[field] = [meta[field][0], 7]                    # a list, but not of strings
    else:
        meta[field] = bad
    (run / "argv.json").write_text(json.dumps(meta))
    assert analyze(run, "--part", str(part)).returncode == 1
    found = checks(run)
    assert not found["evidence_complete"]
    assert not any(found.values())                           # nothing passes on incomplete evidence


def test_a_deleted_home_still_finds_the_home_execs(tmp_path: Path) -> None:
    """r3's case: the plain run's six home execs must not pass once `home` is gone from argv.json."""
    run = evidence(tmp_path / "r", 1)
    home = argv_json(run)["home"]
    tried = execve([f"{home}/.codex/tmp/arg0/x/getconf"], pid=8).replace(" = 0", " = -1 ENOENT")
    (run / "server.strace").write_text(trace([CLI, "app-server"], tried))
    meta = json.loads((run / "argv.json").read_text())
    del meta["home"]
    (run / "argv.json").write_text(json.dumps(meta))
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["no_exec_under_home"]


@pytest.mark.parametrize("bad", ["deleted", "", ["x"], []])
def test_a_missing_or_malformed_resume_argv_fails_part_2(tmp_path: Path, bad: object) -> None:
    run = evidence(tmp_path / "r", 2)
    (run / "argv-resume.json").write_text(json.dumps({} if bad == "deleted" else {"tui_resume": bad}))
    assert analyze(run, "--part", "2").returncode == 1
    assert not any(checks(run).values())


@pytest.mark.parametrize("name", ["argv.json", "argv-resume.json"])
def test_metadata_that_is_not_a_json_object_fails(tmp_path: Path, name: str) -> None:
    run = evidence(tmp_path / "r", 2)
    (run / name).write_text("[1, 2]" if name == "argv.json" else "{not json")
    assert analyze(run, "--part", "2").returncode == 1
    assert not checks(run)["evidence_complete"]


@pytest.mark.parametrize("argv", [[CLI, "--help"], [CLI], [CLI, "app-server", "--extra"], ["/other/codex",
                                                                                          "app-server"]])
def test_a_trace_of_the_same_binary_with_other_arguments_fails(tmp_path: Path, argv: list[str]) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / "server.strace").write_text(trace(argv, sendto("raw.githubusercontent.com")))
    assert analyze(run, "--part", "1").returncode == 1
    found = checks(run)
    assert not found["server.strace_shows_its_exec"] and not found["no_api_github_lookup"]


def test_the_exact_exec_must_open_the_trace(tmp_path: Path) -> None:
    """The traced root is the trace's first process; a child's exec of the same argv later doesn't count."""
    run = evidence(tmp_path / "r", 1)
    lines = [execve(["/bin/sh", "-c", "x"]), execve([CLI, "app-server"], 8), ended()]
    (run / "server.strace").write_text("\n".join(lines) + "\n")
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["server.strace_shows_its_exec"]


def test_a_failed_root_exec_fails(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / "server.strace").write_text(trace([CLI, "app-server"]).replace(") = 0", ") = -1 ENOENT"))
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["server.strace_shows_its_exec"]


def test_an_abbreviated_argv_fails(tmp_path: Path) -> None:
    """strace marks a cut string or array with `...`: that argv can't be shown to match."""
    run = evidence(tmp_path / "r", 1)
    line = execve([CLI, "app-server"]).replace(f'"{hx("app-server")}"', f'"{hx("app-ser")}"...')
    (run / "server.strace").write_text("\n".join([line, ended()]) + "\n")
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["server.strace_shows_its_exec"]


@pytest.mark.parametrize("part", [1, 2])
@pytest.mark.parametrize("name", ["server.strace", "tui.strace", "tui-resume.strace"])
def test_a_trace_cut_to_its_first_line_fails(tmp_path: Path, part: int, name: str) -> None:
    run = evidence(tmp_path / "r", part)
    if not (run / name).is_file():
        pytest.skip("part 1 has no resume")
    first = (run / name).read_text().splitlines()[0]
    (run / name).write_text(first + "\n")
    assert analyze(run, "--part", str(part)).returncode == 1
    found = checks(run)
    assert not found["evidence_complete"] and not found[f"{name}_ran_to_its_end"]
    assert not any(v for k, v in found.items() if not k.endswith(("_shows_its_exec", "_ran_to_its_end")))


@pytest.mark.parametrize("part", [1, 2])
def test_an_api_github_lookup_fails_cut_off_or_not(tmp_path: Path, part: int) -> None:
    """r3's case: the lookup fails the run, and cutting the trace before the lookup fails it too."""
    run = evidence(tmp_path / "r", part)
    argv = cast_list(argv_json(run)["tui_new"])
    looked = sendto("api.github.com")
    (run / "tui.strace").write_text(trace(argv, connect(SOCK), looked, end="killed by SIGHUP"))
    assert analyze(run, "--part", str(part)).returncode == 1
    assert not checks(run)["no_api_github_lookup"]
    (run / "tui.strace").write_text("\n".join([execve(argv), connect(SOCK)]) + "\n")
    assert analyze(run, "--part", str(part)).returncode == 1
    assert not checks(run)["evidence_complete"] and not checks(run)["no_api_github_lookup"]


@pytest.mark.parametrize(("name", "end"), [
    ("server.strace", "killed by SIGKILL"), ("server.strace", "killed by SIGSEGV (core dumped)"),
    ("server.strace", "killed by SIGHUP"), ("server.strace", "killed by SIGTERM"),
    ("tui.strace", "killed by SIGTERM"),
    ("tui.strace", "killed by SIGABRT"),
])
def test_a_root_killed_by_anything_but_its_command_s_allowed_end_fails(tmp_path: Path, name: str,
                                                                       end: str) -> None:
    run = evidence(tmp_path / "r", 1)
    argv = cast_list(argv_json(run)["server" if name == "server.strace" else "tui_new"])
    (run / name).write_text(trace(argv, connect(SOCK), end=end))
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)[f"{name}_ran_to_its_end"]


@pytest.mark.parametrize("code", ["0", "1", "143"])
def test_a_root_that_exited_ran_to_its_end(tmp_path: Path, code: str) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / "server.strace").write_text(trace([CLI, "app-server"], end=f"exited with {code}"))
    done = analyze(run, "--part", "1")
    assert done.returncode == 0, done.stdout


def test_only_the_root_s_end_counts(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / "server.strace").write_text("\n".join([execve([CLI, "app-server"]), ended(pid=8)]) + "\n")
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["server.strace_ran_to_its_end"]


def test_lines_after_the_root_s_end_are_still_read(tmp_path: Path) -> None:
    """A child can outlive the root: its lookups still count."""
    run = evidence(tmp_path / "r", 1)
    child = sendto("api.github.com").replace("7", "8", 1)
    (run / "server.strace").write_text(trace([CLI, "app-server"]) + child + "\n")
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["no_api_github_lookup"]


# --- the wrappers: a stand-in `timeout` replaces the namespace run ---

STUB = '''#!{python}
import json, os, sys
sys.path.insert(0, {tests!r})
from test_spike_s9_10 import evidence
args = sys.argv[1:]
run = __import__("pathlib").Path(args[args.index("sh") + 2])
if os.environ["STUB_MODE"] == "good":
    meta = json.loads((run / "argv.json").read_text())
    evidence(run, int(os.environ["STUB_PART"]), meta)
    sys.exit(0)
sys.exit(124)
'''


def wrapper(tmp_path: Path, script: str, mode: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    cli = tmp_path / "cli"
    (cli / "bin").mkdir(parents=True)
    (cli / "bin" / "codex").write_text("#!/bin/sh\nexit 0\n")
    (cli / "bin" / "codex").chmod(0o755)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "timeout").write_text(STUB.format(python=sys.executable, tests=str(Path(__file__).parent)))
    (stubs / "timeout").chmod(0o755)
    runtime = tmp_path / "xdg"
    runtime.mkdir(mode=0o700)
    login = tmp_path / "login.json"
    login.write_text("{}")
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "XDG_RUNTIME_DIR": str(runtime),
           "STUB_MODE": mode, "STUB_PART": "1" if script == "part1.sh" else "2", "S9_10_LOGIN": str(login)}
    out = tmp_path / "out"
    done = subprocess.run(["sh", str(SPIKE / script), str(cli), str(out)], env=env,  # noqa: S603, S607
                          capture_output=True, text=True, timeout=120, check=False)
    return done, runtime


@pytest.mark.parametrize("script", ["part1.sh", "part2.sh"])
def test_a_capped_run_makes_the_wrapper_fail(tmp_path: Path, script: str) -> None:
    done, runtime = wrapper(tmp_path, script, "cap")
    assert done.returncode != 0, done.stdout + done.stderr
    assert "verdict: FAIL" in done.stdout
    assert list(runtime.iterdir()) == []                     # both variants ran and each home was deleted


@pytest.mark.parametrize("script", ["part1.sh", "part2.sh"])
def test_a_wrapper_passes_when_both_variants_pass(tmp_path: Path, script: str) -> None:
    done, runtime = wrapper(tmp_path, script, "good")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.count("verdict: PASS") == 2
    assert list(runtime.iterdir()) == []


@pytest.mark.parametrize("script", ["inner.sh", "inner2.sh"])
def test_the_wrappers_keep_strace_s_exit_records(script: str) -> None:
    """`-qq` drops the `+++ exited/killed +++` records the analyzer needs; the harness signals each traced
    root itself (strace signalled would detach without one) and waits for its record."""
    text = (SPIKE / script).read_text()
    assert 'TRACE="-f -q -s 300' in text and "-qq" not in text
    assert "stop_root" in text


# --- r3 lead refinements: the root's end is its final event, a whole last line, and the wrapper's marker ---

def test_a_root_event_after_its_end_fails(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / "server.strace").write_text(trace([CLI, "app-server"], end=None) + ended() + "\n"
                                       + sendto("raw.githubusercontent.com") + "\n")
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["server.strace_ran_to_its_end"]


def test_a_truncated_last_line_fails(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    child = sendto("raw.githubusercontent.com").replace("7", "8", 1)
    (run / "server.strace").write_text(trace([CLI, "app-server"]) + child[:40])
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["server.strace_ran_to_its_end"]


@pytest.mark.parametrize("part", [1, 2])
@pytest.mark.parametrize("name", ["server.strace", "tui.strace", "tui-resume.strace"])
def test_the_wrapper_s_unended_marker_fails(tmp_path: Path, part: int, name: str) -> None:
    """stop_root writes <trace>.unended when the record didn't come within its bound."""
    run = evidence(tmp_path / "r", part)
    if not (run / name).is_file():
        pytest.skip("part 1 has no resume")
    (run / f"{name}.unended").write_text("no end record within 10 s\n")
    assert analyze(run, "--part", str(part)).returncode == 1
    assert not checks(run)[f"{name}_ran_to_its_end"]


@pytest.mark.parametrize("script", ["inner.sh", "inner2.sh"])
def test_the_wrappers_bound_the_wait_and_mark_a_timeout(script: str) -> None:
    text = (SPIKE / script).read_text()
    assert '-lt 20 ]' in text and '.unended"' in text                # 20 half-second polls: 10 s


@pytest.mark.parametrize("script", ["part1.sh", "part2.sh"])
def test_the_outer_wrappers_keep_the_markers(script: str) -> None:
    assert "strace.unended" in (SPIKE / script).read_text()


def test_the_server_must_exit_and_a_tui_may_die_of_its_hangup(tmp_path: Path) -> None:
    """r3 re-run: the app-server exits 0 on SIGTERM, so only `exited with` passes for it; the TUI dies of the
    SIGHUP, which passes for a TUI only."""
    run = evidence(tmp_path / "r", 1)
    assert analyze(run, "--part", "1").returncode == 0
    (run / "server.strace").write_text(trace([CLI, "app-server"], end="killed by SIGTERM"))
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["server.strace_ran_to_its_end"]


@pytest.mark.parametrize("cut", [60, 120])
def test_a_truncated_first_line_fails(tmp_path: Path, cut: int) -> None:
    """The root's exec cut mid-line (path whole, argv not) doesn't show the command, and nothing after it."""
    run = evidence(tmp_path / "r", 1)
    (run / "server.strace").write_text(execve([CLI, "app-server"])[:cut])
    assert analyze(run, "--part", "1").returncode == 1
    found = checks(run)
    assert not found["server.strace_shows_its_exec"] and not found["evidence_complete"]


def test_a_tui_s_core_dumped_hangup_fails(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    (run / "tui.strace").write_text(trace(cast_list(argv_json(run)["tui_new"]), connect(SOCK),
                                          end="killed by SIGHUP (core dumped)"))
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["tui.strace_ran_to_its_end"]


# --- Part 2's hooks: the shipped shim with no session socket (after the EDZixy run) ---

HOOK_FAILURES = ["• Hook failed\n  └ hook exited with code 2\n",
                 "• Blocked by hook\n  └ python3: can't open file\n", "hook exited with code 1\n"]


@pytest.mark.parametrize("screen", ["tui.screen", "tui-resume.screen"])
@pytest.mark.parametrize("failure", HOOK_FAILURES)
def test_a_hook_failure_on_screen_fails_by_name(tmp_path: Path, screen: str, failure: str) -> None:
    run = evidence(tmp_path / "r", 2)
    with (run / screen).open("a") as f:
        f.write(failure)
    assert analyze(run, "--part", "2").returncode == 1
    found = checks(run)
    assert found["evidence_complete"] and not found["no_hook_failure"]


def test_a_missing_hooks_file_is_incomplete_evidence(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 2)
    (run / "hooks").unlink()
    assert analyze(run, "--part", "2").returncode == 1
    found = checks(run)
    assert not found["evidence_complete"] and not found["hooks_ran"] and not found["no_hook_failure"]


@pytest.mark.parametrize("text", ["", "SessionStart\nStop\n", "UserPromptSubmitted\n"])
def test_hooks_without_the_prompt_event_fail(tmp_path: Path, text: str) -> None:
    run = evidence(tmp_path / "r", 2)
    (run / "hooks").write_text(text)
    assert analyze(run, "--part", "2").returncode == 1
    assert not checks(run)["hooks_ran"]


def test_part_1_is_unchanged_by_the_hook_checks(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    assert analyze(run, "--part", "1").returncode == 0
    assert "hooks_ran" not in checks(run) and "no_hook_failure" not in checks(run)


def steps(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SPIKE / "part2_steps.py"), *args],  # noqa: S603
                          capture_output=True, text=True, timeout=60, check=False)


def test_run_files_writes_the_shipped_shim(tmp_path: Path) -> None:
    from heterodyne.session import shim
    out = tmp_path / "run"
    out.mkdir()
    assert steps("run-files", str(out)).returncode == 0
    assert (out / "shim.py").read_bytes() == Path(shim.__file__).read_bytes()
    assert not (out / "token").exists() and not (out / "shim.json").exists()   # no socket: the no-wsd path


def test_the_hooks_step_prints_only_event_names(tmp_path: Path) -> None:
    session = tmp_path / "s"
    spool = session / "home" / ".hz" / "spool.jsonl"
    spool.parent.mkdir(parents=True)
    events = [{"hook_event_name": "SessionStart"}, {"hook_event_name": "UserPromptSubmit", "prompt": "pong?"},
              {"hook_event_name": 7}, {"no": "name"}]
    spool.write_text("".join(json.dumps({"spooled": True, "payload": e}) + "\n" for e in events) + "{bad\n")
    done = steps("hooks", str(session))
    assert done.returncode == 0, done.stderr
    assert done.stdout == "SessionStart\nUserPromptSubmit\n"


def test_the_hooks_step_with_no_spool_prints_nothing(tmp_path: Path) -> None:
    done = steps("hooks", str(tmp_path / "s"))
    assert done.returncode == 0 and done.stdout == ""


def test_part2_copies_the_hooks_and_inner2_writes_them() -> None:
    assert " hooks " in (SPIKE / "part2.sh").read_text().replace("\\\n", " ")
    assert 'part2_steps.py" hooks "$SESSION" > "$R/hooks"' in (SPIKE / "inner2.sh").read_text()


# --- btq-wi1xz: only well-formed queries sent to port 53 are listed as DNS names ---

def load_analyzer() -> Any:
    spec = importlib.util.spec_from_file_location("s9_10_analyze", SPIKE / "analyze.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYZER = load_analyzer()
NAME = re.compile(r"[A-Za-z0-9_-]{1,63}(\.[A-Za-z0-9_-]{1,63})*")
COOKIE = struct.pack(">HH", 10, 8) + b"\x01\x02\x03\x04\x05\x06\x07\x08"   # one EDNS option: CODE 10, 8 bytes
# A TLS ClientHello's first bytes, with a label planted where a DNS question would start (offset 12).
LONGEST = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])        # 255 bytes on the wire
HELLO = b"\x16\x03\x01\x00\xc8\x01\x00\x00\xc4\x03\x03\x00" + b"\x0bPLANTEDMARK\x00" + bytes(range(40))


def query(name: str = "example.com", *, qd: int = 1, an: int = 0, ns: int = 0, ar: int = 0,
          flags: int = 0x0100, tail: bytes = b"") -> bytes:
    """dns(name) with its header's flags and counts set, and `tail` after the question."""
    return struct.pack(">HHHHHH", 0x1234, flags, qd, an, ns, ar) + dns(name)[12:] + tail


def opt(rdata: bytes = b"", *, rdlength: int | None = None, owner: bytes = b"\x00", rtype: int = 41) -> bytes:
    """An OPT record: owner name, TYPE, CLASS (UDP size 1232), TTL 0, RDLENGTH, RDATA."""
    size = len(rdata) if rdlength is None else rdlength
    return owner + struct.pack(">HHIH", rtype, 1232, 0, size) + rdata


def inet(port: int, v6: bool = False) -> str:
    if v6:
        return (f"{{sa_family=AF_INET6, sin6_port=htons({port}), sin6_flowinfo=htonl(0), "
                f'inet_pton(AF_INET6, "fd00::3", &sin6_addr), sin6_scope_id=0}}')
    return f'{{sa_family=AF_INET, sin_port=htons({port}), sin_addr=inet_addr("10.0.2.3")}}'


def sendto_to(addr: str, buf: bytes) -> str:
    size = 0 if addr == "NULL" else 28 if "AF_INET6" in addr else 16
    return f'7 sendto(6, "{hx(buf)}", {len(buf)}, MSG_NOSIGNAL, {addr}, {size}) = {len(buf)}'


def iov(buf: bytes, *, length: int | None = None, cut: bool = False) -> str:
    """One strace iovec; `cut` marks it truncated, as strace does past -s."""
    return f'{{iov_base="{hx(buf)}"{"..." if cut else ""}, iov_len={len(buf) if length is None else length}}}'


def sendmmsg_of(entries: list[tuple[str, list[bytes | str]]], *, abbreviated: bool = False) -> str:
    """A sendmmsg of one message per entry: its address and its iovecs (bytes, or an iov() string)."""
    shown = []
    for addr, vecs in entries:
        items = [v if isinstance(v, str) else iov(v) for v in vecs]
        size = 0 if addr == "NULL" else 28 if "AF_INET6" in addr else 16
        total = sum(len(v) for v in vecs if isinstance(v, bytes))
        shown.append(f"{{msg_hdr={{msg_name={addr}, msg_namelen={size}, msg_iov=[{', '.join(items)}], "
                     f"msg_iovlen={len(items)}, msg_controllen=0, msg_flags=0}}, msg_len={total}}}")
    more = ", ..." if abbreviated else ""
    n = len(entries) + (1 if abbreviated else 0)
    return f"7 sendmmsg(6, [{', '.join(shown)}{more}], {n}, MSG_NOSIGNAL) = {n}"


def listed(tmp_path: Path, *body: str) -> tuple[dict[str, Any], str]:
    """result.json and stdout of a passing Part 1 run whose server trace holds `body`."""
    run = evidence(tmp_path / "r", 1)
    (run / "server.strace").write_text(trace([CLI, "app-server"], *body))
    done = analyze(run, "--part", "1")
    return json.loads((run / "result.json").read_text()), done.stdout


@pytest.mark.parametrize(("packet", "name"), [
    (dns("example.com"), "example.com"), (dns("_dns-sd._udp.Local-1"), "_dns-sd._udp.Local-1"),
    (query(ar=1, tail=opt()), "example.com"), (query(ar=1, tail=opt(COOKIE)), "example.com"),
    (query(LONGEST), LONGEST),
], ids=["plain", "underscore-hyphen", "opt", "opt-option", "255-bytes"])
def test_query_name_reads_a_well_formed_query(packet: bytes, name: str) -> None:
    assert ANALYZER.query_name(packet) == name


FIXED = opt()[1:]                                            # the 10 fixed bytes after the root name


REFUSED = {
    "qd0": query(qd=0), "qd2": query(qd=2), "response": query(flags=0x8180), "opcode": query(flags=0x2900),
    "an1": query(an=1), "ns1": query(ns=1), "ar2": query(ar=2, tail=opt() + opt()),
    "label64": query("a" * 64 + ".com"), "name257": query(".".join(["a" * 63] * 4)),
    "pointer": query()[:12] + b"\x03www\xc0\x0c\x00\x01\x00\x01",
    "unended": query()[:12] + b"\x07example", "short-type": query()[:12] + b"\x07example\x03com\x00\x00\x01",
    "no-type": query()[:-4], "space": query("exa mple.com"), "star": query("ex*mple.com"),
    "non-ascii": query()[:12] + b"\x03ex\xe9\x03com\x00\x00\x01\x00\x01",
    "root": query()[:12] + b"\x00\x00\x01\x00\x01", "trailing": query(tail=b"\x00"),
    "empty": b"", "short-header": query()[:11],
    # ARCOUNT 1: exactly one OPT record must follow
    "opt-missing": query(ar=1), "opt-owner": query(ar=1, tail=opt(owner=b"\x01a\x00")),
    "opt-type": query(ar=1, tail=opt(rtype=1)),
    **{f"opt-cut-{k}": query(ar=1, tail=b"\x00" + FIXED[:k]) for k in range(10)},
    "rdlength-long": query(ar=1, tail=opt(COOKIE, rdlength=len(COOKIE) + 1)),
    "rdlength-short": query(ar=1, tail=opt(COOKIE, rdlength=len(COOKIE) - 1)),
    "option-overruns": query(ar=1, tail=opt(struct.pack(">HH", 10, 9) + COOKIE[4:])),
    "option-short": query(ar=1, tail=opt(struct.pack(">HH", 10, 7) + COOKIE[4:])),
    "rdata-left": query(ar=1, tail=opt(COOKIE + b"\x00")),
    "after-opt": query(ar=1, tail=opt() + b"\x00"),
    "after-opt-option": query(ar=1, tail=opt(COOKIE) + b"\x00\x00"),
    "tls": HELLO,
}


@pytest.mark.parametrize("packet", REFUSED.values(), ids=REFUSED.keys())
def test_query_name_refuses_anything_else(packet: bytes) -> None:
    assert ANALYZER.query_name(packet) is None


@given(st.one_of(st.binary(max_size=600), st.builds(lambda b: query()[:12] + b, st.binary(max_size=300)),
                 st.builds(lambda b: query(ar=1) + b, st.binary(max_size=40))))
def test_query_name_never_raises_and_gives_only_a_name(packet: bytes) -> None:
    name = ANALYZER.query_name(packet)
    assert name is None or NAME.fullmatch(name)


@pytest.mark.parametrize(("send", "names"), [
    (sendto_to(inet(53), dns("v4.example")), ["v4.example"]),
    (sendto_to(inet(53, v6=True), dns("v6.example")), ["v6.example"]),
    (sendto_to("NULL", dns("null.example")), []),
    (sendto_to(inet(443), dns("tls.example")), []),
    (sendto_to(inet(5353), dns("mdns.example")), []),
    (sendto_to(inet(53), dns("cut.example")).replace('", ', '"..., ', 1), []),
    (sendto_to(inet(53), dns("long.example")).replace(f", {len(dns('long.example'))}, ", ", 99, ", 1), []),
], ids=["v4-53", "v6-53", "null", "443", "5353", "truncated", "length-mismatch"])
def test_only_sends_to_port_53_are_listed(tmp_path: Path, send: str, names: list[str]) -> None:
    result, _ = listed(tmp_path, send)
    assert result["dns"] == names
    assert result["dns_dropped"] == 1 - len(names)


def test_a_null_send_after_a_connect_to_53_is_not_listed(tmp_path: Path) -> None:
    connected = f"7 connect(6, {inet(53)}, 16) = 0"
    result, _ = listed(tmp_path, connected, sendto_to("NULL", dns("connected.example")))
    assert result["dns"] == [] and result["dns_dropped"] == 1


def test_an_unfinished_send_is_read_and_its_resumption_yields_nothing(tmp_path: Path) -> None:
    send = sendto_to(inet(53), dns("slow.example")).rsplit(") = ", 1)[0] + " <unfinished ...>"
    result, _ = listed(tmp_path, send, "7 <... sendto resumed>) = 34")
    assert result["dns"] == ["slow.example"] and result["dns_dropped"] == 0


def test_sendmmsg_lists_only_its_port_53_entries(tmp_path: Path) -> None:
    line = sendmmsg_of([(inet(53), [dns("good.example")]), (inet(443), [dns("other.example")]),
                        ("NULL", [dns("null.example")])])
    result, _ = listed(tmp_path, line)
    assert result["dns"] == ["good.example"] and result["dns_dropped"] == 2


def test_sendmmsg_joins_a_query_split_across_iovecs(tmp_path: Path) -> None:
    packet = dns("split.example")
    head, rest = packet[:12], packet[12:]
    result, _ = listed(tmp_path, sendmmsg_of([(inet(53), [head, rest])]))
    assert result["dns"] == ["split.example"] and result["dns_dropped"] == 0


@pytest.mark.parametrize("bad", ["cut", "length"])
def test_a_split_query_with_a_bad_iovec_is_dropped(tmp_path: Path, bad: str) -> None:
    packet = dns("split.example")
    head, rest = packet[:12], packet[12:]
    last = iov(rest, cut=True) if bad == "cut" else iov(rest, length=len(rest) + 1)
    result, out = listed(tmp_path, sendmmsg_of([(inet(53), [head, last])]))
    assert result["dns"] == [] and result["dns_dropped"] == 1
    assert "split" not in out


def test_ipv6_sendmmsg_of_a_and_aaaa_gives_one_name(tmp_path: Path) -> None:
    aaaa = dns("both.example")[:-4] + b"\x00\x1c\x00\x01"
    result, _ = listed(tmp_path, sendmmsg_of([(inet(53, v6=True), [dns("both.example")]),
                                             (inet(53, v6=True), [aaaa])]))
    assert result["dns"] == ["both.example"] and result["dns_dropped"] == 0


def test_an_abbreviated_sendmmsg_array_is_dropped_whole(tmp_path: Path) -> None:
    line = sendmmsg_of([(inet(53), [dns("one.example")]), (inet(53), [dns("two.example")])], abbreviated=True)
    result, out = listed(tmp_path, line)
    assert result["dns"] == [] and result["dns_dropped"] == 2
    assert "example" not in out


def test_a_tls_record_never_reaches_the_output(tmp_path: Path) -> None:
    mixed = sendmmsg_of([(inet(53), [dns("real.example")]), (inet(443), [HELLO]), (inet(53), [HELLO])])
    result, out = listed(tmp_path, sendto_to(inet(443), HELLO), sendto_to(inet(53), HELLO), mixed)
    assert result["dns"] == ["real.example"] and result["dns_dropped"] == 4
    assert "PLANTEDMARK" not in out and "PLANTEDMARK" not in json.dumps(result)
    assert result["passed"]


def test_at_most_32_names_are_listed(tmp_path: Path) -> None:
    sends = [sendto_to(inet(53), dns(f"n{i:02d}.example")) for i in range(40)]
    result, _ = listed(tmp_path, *sends)
    assert result["dns"] == [f"n{i:02d}.example" for i in range(32)] and result["dns_dropped"] == 0


def test_api_github_in_a_non_dns_send_still_fails(tmp_path: Path) -> None:
    hello = HELLO + b"\x00\x00\x0e" + b"api.github.com"            # an SNI-shaped mention, sent to 443
    result, _ = listed(tmp_path, sendto_to(inet(443), hello))
    assert result["dns"] == [] and not result["passed"]
    assert not result["checks"]["no_api_github_lookup"]
