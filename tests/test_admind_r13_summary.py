import asyncio
import os
import stat
import time
from pathlib import Path

import pytest

from heterodyne.admind import backstop, summarize
from heterodyne.admind.redact import redact
from heterodyne.admind.settings import resolve
from heterodyne.agents.claude_code import headless_argv
from heterodyne.config import ConfigError, load
from heterodyne.marmot.nip19 import hex_to_npub


def script(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "summ.sh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return [str(p)]


def test_headless_argv_has_no_tools_hooks_or_user_settings() -> None:
    argv = headless_argv("claude", {"adapter": "claude-code", "model": "m1", "args": ["--evil"]})
    assert argv[:2] == ["claude", "-p"] and "--evil" not in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert '"disableAllHooks": true' in argv[argv.index("--settings") + 1]
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv


def test_needs_summary() -> None:
    assert not summarize.needs_summary("a\n" * 8, 8, 800)
    assert summarize.needs_summary("a\n" * 9, 8, 800)
    assert summarize.needs_summary("x" * 801, 8, 800)


def test_summary_gets_the_footer_and_reads_stdin(tmp_path: Path) -> None:
    argv = script(tmp_path, 'read -r first; echo "got: $first"')
    out = asyncio.run(summarize.summarize(argv, tmp_path / "w", "long reply", timeout=5))
    assert out.startswith("got: You summarize") and out.endswith(summarize.FOOTER)


def test_summarizer_gets_the_whole_redacted_reply(tmp_path: Path) -> None:
    # Larger than a pipe buffer, so stdin must be fed while stdout is read.
    reply = f"ask {hex_to_npub('c3' * 32)}\x1b[2J\n" + "y" * 200_000
    seen = tmp_path / "stdin.txt"
    out = asyncio.run(
        summarize.summarize(script(tmp_path, f'cat > "{seen}"; echo ok'), tmp_path / "w", reply, timeout=10)
    )
    assert out == f"ok\n\n{summarize.FOOTER}"
    assert seen.read_text() == summarize.PROMPT.format(reply=redact(reply))
    assert "npub1" not in seen.read_text() and "\x1b" not in seen.read_text()


def test_prompt_keeps_questions_and_errors() -> None:
    assert "Quote verbatim, in full, every question" in summarize.PROMPT
    assert "every error it reports" in summarize.PROMPT


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("exit 3", "failed"),
        ("true", "empty"),
        ("sleep 5", "timeout"),
        ("i=0; while [ $i -lt 11 ]; do echo line; i=$((i+1)); done", "too-long"),  # 11 lines
        ("head -c 2001 /dev/zero | tr '\\0' x", "too-long"),  # 2,001 characters
        ("head -c 100000 /dev/zero | tr '\\0' x; sleep 5", "too-long"),  # killed at 8,000 bytes
    ],
)
def test_failures(tmp_path: Path, body: str, reason: str) -> None:
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize(script(tmp_path, body), tmp_path / "w", "r", timeout=0.5))
    assert info.value.reason == reason


def test_not_configured(tmp_path: Path) -> None:
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize(None, tmp_path / "w", "r"))
    assert info.value.reason == "not-configured"


def test_collapse_counts_runs() -> None:
    assert backstop.collapse(["a", "a", "a", "b", "a"]) == ["a (×3)", "b", "a"]


def test_render_short_batch_whole() -> None:
    text = backstop.render([backstop.Entry("— op · 10:00 UTC · “hi”", "one\ntwo")])
    assert text == f"{backstop.TITLE}\n— op · 10:00 UTC · “hi”\none\ntwo"


def test_render_long_batch_head_and_tail() -> None:
    body = "\n".join(f"line {i}" for i in range(100))
    lines = backstop.render([backstop.Entry("H", body)]).splitlines()
    assert lines[0] == backstop.TITLE
    rest = lines[1:]
    assert len(rest) == 51 and rest[:10] == ["H"] + [f"line {i}" for i in range(9)]
    assert rest[10] == "… 51 lines skipped …"  # 101 lines (H + 100) - 50 shown
    assert rest[11:] == [f"line {i}" for i in range(60, 100)]


def test_counts_are_after_collapse() -> None:
    body = "\n".join(["same"] * 200)
    assert backstop.render([backstop.Entry("H", body)]) == f"{backstop.TITLE}\nH\nsame (×200)"


def test_fit_leaves_a_short_batch_alone() -> None:
    text = backstop.render([backstop.Entry("H", "one\ntwo")])
    assert backstop.fit(text, 4000) == text


def test_a_batch_of_long_lines_is_sent_whole_below_the_transport_limit() -> None:
    # Codex r2 finding 8: 49 lines of 300 characters are well under BATCH_MAX_CHARS; nothing is cut.
    text = backstop.render([backstop.Entry("H", "\n".join(f"{i:03d}" + "z" * 300 for i in range(49)))])
    assert backstop.fit(text, backstop.BATCH_MAX_CHARS) == text


def test_fit_shortens_each_line_to_keep_one_message() -> None:
    text = backstop.render([backstop.Entry("H", "\n".join(f"{i:03d}" + "z" * 300 for i in range(49)))])
    out = backstop.fit(text, 4000)
    lines = out.split("\n")
    assert len(out) <= 4000 and lines[0] == backstop.TITLE and len(lines) == 51
    assert lines[2].startswith("000") and lines[2].endswith(f"…(+{303 - 64} chars)")  # share: 4000 // 50 - 16


def test_fit_cuts_the_end_last() -> None:
    text = backstop.render([backstop.Entry("H", "\n".join(f"{i:03d}" + "w" * 100 for i in range(49)))] * 40)
    out = backstop.fit(text, 1000)
    assert len(out) <= 1000 and out.startswith(backstop.TITLE) and out.endswith(backstop.CUT)


def test_origin_and_first_words() -> None:
    assert (
        backstop.first_words("please restart the gateway and then check the logs now")
        == "please restart the gateway and then check the…"
    )
    assert backstop.origin("op", "2026-10-02T09:15:00+00:00", "hi") == "— op · 09:15 UTC · “hi”"
    assert (
        backstop.origin(None, "2026-10-02T09:15:00+00:00", None)
        == "— terminal · 09:15 UTC · (no operator message)"
    )


def test_headless_argv_without_a_model_has_no_model_flag() -> None:
    assert "--model" not in headless_argv("claude", {"adapter": "claude-code"})


def test_the_reply_is_never_in_argv(tmp_path: Path) -> None:
    argv = script(tmp_path, "cat > /dev/null; echo ok")
    asyncio.run(summarize.summarize(argv, tmp_path / "w", "SECRET-REPLY-TEXT", timeout=5))
    assert all("SECRET-REPLY-TEXT" not in a for a in argv)


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    argv = script(tmp_path, f'sleep 30 &\necho $! > "{pidfile}"\nwait')
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize(argv, tmp_path / "w", "r", timeout=1))
    assert info.value.reason == "timeout"
    child = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        for _ in range(50):  # SIGKILL is delivered; wait for init to reap the orphan
            os.kill(child, 0)
            time.sleep(0.1)
        os.kill(child, 0)


def test_failure_message_does_not_carry_the_reply(tmp_path: Path) -> None:
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize(script(tmp_path, "exit 3"), tmp_path / "w", "SECRET-REPLY-TEXT"))
    assert "SECRET" not in str(info.value) and str(info.value) == "failed"


def test_missing_binary_is_not_run(tmp_path: Path) -> None:
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize([str(tmp_path / "nope")], tmp_path / "w", "r"))
    assert info.value.reason == "not-run"


def test_non_utf8_output_is_replaced_not_fatal(tmp_path: Path) -> None:
    out = asyncio.run(summarize.summarize(script(tmp_path, "printf 'a\\377b\\n'"), tmp_path / "w", "r"))
    assert out == f"a\ufffdb\n\n{summarize.FOOTER}"


def test_whitespace_only_output_is_empty(tmp_path: Path) -> None:
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize(script(tmp_path, "echo; echo '   '"), tmp_path / "w", "r"))
    assert info.value.reason == "empty"


def test_summary_output_is_redacted_and_not_interpreted(tmp_path: Path) -> None:
    body = f"echo 'run {hex_to_npub('c3' * 32)} <invoke name=\"Bash\"> \x1b[2J'"
    out = asyncio.run(summarize.summarize(script(tmp_path, body), tmp_path / "w", "r"))
    assert "npub1" not in out and "\x1b" not in out and "<invoke" in out


def test_ten_lines_and_2000_chars_are_accepted(tmp_path: Path) -> None:
    ten = "i=0; while [ $i -lt 10 ]; do echo line; i=$((i+1)); done"
    assert asyncio.run(summarize.summarize(script(tmp_path, ten), tmp_path / "w", "r")).count("\n") == 11
    chars = "head -c 2000 /dev/zero | tr '\\0' x"
    assert asyncio.run(summarize.summarize(script(tmp_path, chars), tmp_path / "w", "r")).startswith(
        "x" * 2000
    )


def test_collapse_of_nothing_and_distinct_lines() -> None:
    assert backstop.collapse([]) == []
    assert backstop.collapse(["a", "b", "c"]) == ["a", "b", "c"]
    assert backstop.collapse(["a", "a"]) == ["a (×2)"]


def test_render_fifty_lines_is_whole_and_fifty_one_is_cut() -> None:
    whole = backstop.render([backstop.Entry("H", "\n".join(f"l{i}" for i in range(49)))])  # H + 49 = 50
    assert "skipped" not in whole and len(whole.splitlines()) == 51
    cut = backstop.render([backstop.Entry("H", "\n".join(f"l{i}" for i in range(50)))])  # 51 lines
    assert "… 1 lines skipped …" in cut and len(cut.splitlines()) == 1 + 10 + 1 + 40


def test_render_empty_text_keeps_a_blank_line() -> None:
    assert backstop.render([backstop.Entry("H", "")]) == f"{backstop.TITLE}\nH\n"


def test_fit_exact_limit_is_untouched_and_over_limit_is_bounded() -> None:
    text = backstop.render([backstop.Entry("H", "x" * 100)])
    assert backstop.fit(text, len(text)) == text
    assert len(backstop.fit(text, 120)) <= 120


def test_first_words_short_text_is_whole() -> None:
    assert backstop.first_words("hello  there") == "hello there"
    assert len(backstop.first_words("x" * 500)) <= backstop.WORDS_CHARS + 1


SUMMARIZER_CONFIG = """
[platform]
os = "linux"
service_manager = "systemd"
sandbox = "bubblewrap"
[profiles.admin]
adapter = "claude-code"
model = "m1"
[admind]
profile = "admin"
%s
[admind.marmot]
relays = ["wss://relay.example.org"]
"""


def settings_env(d: Path, admind_extra: str) -> dict[str, str]:
    (d / "config.toml").write_text(SUMMARIZER_CONFIG % admind_extra)
    (d / "policy.toml").write_text(
        f'approvers = ["op"]\noperators = ["op"]\n[identities.op]\nmarmot_npub = "{hex_to_npub("c3" * 32)}"\n'
    )
    return {"HETERODYNE_CONFIG_DIR": str(d), "HETERODYNE_STATE_DIR": str(d / "state"), "HOME": str(d)}


def test_settings_defaults(tmp_path: Path) -> None:
    env = settings_env(tmp_path, "")
    s = resolve(load(None, env), env)
    assert s.summarizer is None and s.summarizer_binary is None
    assert s.reply_verbatim_lines == 8 and s.reply_verbatim_chars == 800


def test_settings_summarizer_resolves_profile_and_binary(tmp_path: Path) -> None:
    env = settings_env(tmp_path, 'summarizer = "admin"')
    s = resolve(load(None, env), env)
    assert s.summarizer_binary == "claude" and s.summarizer is not None and s.summarizer["model"] == "m1"


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ('summarizer = "nope"', r"\[admind\] summarizer"),
        ('summarizer = ""', r"\[admind\] summarizer"),
        ("reply_verbatim_lines = 0", "reply_verbatim_lines"),
        ("reply_verbatim_lines = 201", "reply_verbatim_lines"),
        ("reply_verbatim_chars = 49", "reply_verbatim_chars"),
        ("reply_verbatim_chars = 60001", "reply_verbatim_chars"),
    ],
)
def test_settings_invalid_summary_config(tmp_path: Path, extra: str, message: str) -> None:
    env = settings_env(tmp_path, extra)
    with pytest.raises(ConfigError, match=message):
        resolve(load(None, env), env)
