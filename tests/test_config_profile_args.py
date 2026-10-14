"""Plan 4c Amendment 1 Q4: a profile's `args` pass only through its adapter's allowlist, which both adapters
start empty, so no spelling of an override can reach a key the launcher owns."""

import pytest

from heterodyne.config import ConfigError
from heterodyne.config import profile_args as pa

WHERE = "profiles.p.args"


@pytest.mark.parametrize(("args", "index"), [
    (["-c", "check_for_update_on_startup=true"], 0),
    (["-ccheck_for_update_on_startup=true"], 0),
    (["--config", "features={plugins=true}"], 0),
    (["--config=features.daemon_auto_start=true"], 0),
    (["--enable", "daemon_auto_start"], 0),
    (["--enable=plugins"], 0),
    (["--disable", "apps"], 0),
    (["-p", "x"], 0),
    (["--profile=x"], 0),
    (["-xc", "k=v"], 0),
    (["--"], 0),
    (["prompt"], 0),
    ([""], 0),
])
def test_codex_refuses_every_override_spelling(args: list[str], index: int) -> None:
    with pytest.raises(ConfigError) as refused:
        pa.check("codex", args, WHERE)
    assert str(refused.value) == pa.REFUSED.format(where=WHERE, index=index, adapter="codex")


@pytest.mark.parametrize("args", [
    ["--settings", "{}"], ["--settings={}"], ["--setting-sources", "user"], ["--mcp-config", "f"],
    ["--plugin-dir=d"],
])
def test_claude_code_refuses_its_settings_and_content_flags(args: list[str]) -> None:
    with pytest.raises(ConfigError) as refused:
        pa.check("claude-code", args, WHERE)
    assert str(refused.value) == pa.REFUSED.format(where=WHERE, index=0, adapter="claude-code")


def test_both_allowlists_start_empty() -> None:
    assert pa.FLAGS == {"claude-code": {}, "codex": {}}
    pa.check("codex", [], WHERE)
    pa.check("claude-code", [], WHERE)


TEST_FLAGS = {"codex": {"--effort": True, "--quiet": False, "-e": True}}


@pytest.mark.parametrize("args", [
    ["--effort", "low"], ["--effort=low"], ["--quiet"], ["-e", "low"], ["--quiet", "--effort", "low"],
])
def test_an_allowed_flag_passes_in_its_spellings(args: list[str]) -> None:
    pa.check("codex", args, WHERE, flags=TEST_FLAGS)


@pytest.mark.parametrize(("args", "index"), [
    (["--quiet=yes"], 0),          # a flag that takes no value can't be given one
    (["-elow"], 0),                # an attached short value
    (["--quiet", "low"], 1),       # a positional after an allowed flag
    (["--effort", "low", "-c", "k=v"], 2),
])
def test_an_allowlist_refuses_what_it_cant_place(args: list[str], index: int) -> None:
    with pytest.raises(ConfigError) as refused:
        pa.check("codex", args, WHERE, flags=TEST_FLAGS)
    assert str(refused.value) == pa.REFUSED.format(where=WHERE, index=index, adapter="codex")


def test_an_allowed_flag_with_no_value_is_refused() -> None:
    with pytest.raises(ConfigError) as refused:
        pa.check("codex", ["--quiet", "--effort"], WHERE, flags=TEST_FLAGS)
    assert str(refused.value) == pa.NO_VALUE.format(where=WHERE, index=1, adapter="codex")


@pytest.mark.parametrize("args", ["--verbose", {"a": 1}, 3, None, [1], ["--x", None]])
def test_args_that_are_not_a_list_of_strings_are_refused(args: object) -> None:
    with pytest.raises(ConfigError) as refused:
        pa.check("codex", args, WHERE, flags={"codex": {"--x": True}})
    assert str(refused.value) == pa.NOT_A_LIST.format(where=WHERE)


def test_an_unknown_adapter_has_an_empty_allowlist() -> None:
    pa.check("mystery", [], WHERE)
    with pytest.raises(ConfigError):
        pa.check("mystery", ["--x"], WHERE)


def test_a_refusal_never_quotes_the_value() -> None:
    with pytest.raises(ConfigError) as refused:
        pa.check("codex", ["-c", "sk-secret-value"], WHERE)
    assert "secret" not in str(refused.value) and "-c" not in str(refused.value)
