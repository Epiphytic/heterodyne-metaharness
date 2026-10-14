"""S9-10's analyzer and wrappers (docs/spikes/s9-10/), offline on crafted run directories: missing or empty
evidence, a capped run or any failed check gives a non-zero exit, and both wrappers pass that on (r2)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "docs" / "spikes" / "s9-10"
CLI = "/store/cli/bin/codex"
SOCK = "/run/hz-bridge/app.sock"


def hx(text: str | bytes) -> str:
    raw = text.encode() if isinstance(text, str) else text
    return "".join(f"\\x{b:02x}" for b in raw)


def dns(name: str) -> bytes:
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split("."))
    return b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + labels + b"\x00\x00\x01\x00\x01"


def execve(path: str) -> str:
    return f'7 execve("{hx(path)}", ["{hx(path)}"], 0x7ffd /* 4 vars */) = 0'


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
    server, tui = cast_list(meta["server"])[0], cast_list(meta["tui_new"])[0]
    files = {
        "argv.json": json.dumps(meta), "rc": "0\n", "server_ready": "yes\n", "inside": "uid=0\n",
        "inside.packages": "",
        "server.strace": "\n".join([execve(server), sendto("raw.githubusercontent.com")]) + "\n",
        "tui.strace": "\n".join([execve(tui), connect(SOCK)]) + "\n",
        "tui.screen": "Welcome to Codex\nSign in with ChatGPT\n",
    }
    if part == 2:
        files.update({
            "argv-resume.json": json.dumps({"tui_resume": [tui, "resume", "t-1"]}), "trust.txt": "listed 0\n",
            "tui.screen": "› Reply with only the word pong.\n", "composer": "yes\n", "answered": "yes\n",
            "thread": "t-1\n", "resumed": "yes\n", "tui-resume.screen": "› Reply with only the word pong.\n",
            "tui-resume.strace": execve(tui) + "\n",
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
    (run / "server.strace").write_text(execve("/usr/bin/true") + "\n")
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
    with (run / "server.strace").open("a") as f:
        f.write(execve(f"{home}/.codex/tmp/arg0/x/getconf").replace(" = 0", " = -1 ENOENT") + "\n")
    assert analyze(run, "--part", "1").returncode == 1
    assert not checks(run)["no_exec_under_home"]


def test_an_api_github_lookup_fails(tmp_path: Path) -> None:
    run = evidence(tmp_path / "r", 1)
    with (run / "tui.strace").open("a") as f:
        f.write(sendto("api.github.com") + "\n")
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
