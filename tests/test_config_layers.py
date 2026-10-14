"""`check_profiles` runs the profile-args allowlist (plan 4c Amendment 1 Q4) through `config.load`, so
`config check`, wsd's start and admind's start all refuse such a profile."""

from pathlib import Path

import pytest

from heterodyne.config import ConfigError, load
from heterodyne.config import profile_args as pa


def write(d: Path, name: str, text: str) -> None:
    (d / name).write_text(text)


def env(d: Path) -> dict[str, str]:
    return {"HETERODYNE_CONFIG_DIR": str(d), "HOME": str(d)}


@pytest.mark.parametrize(("adapter", "args"), [
    ("codex", '["-c", "features.plugins=true"]'), ("claude-code", '["--settings", "{}"]'),
    ("codex", '"-c k=v"'),
])
def test_config_load_refuses_a_profile_with_a_refused_arg(tmp_path: Path, adapter: str, args: str) -> None:
    write(tmp_path, "config.toml", f'[profiles.p]\nadapter = "{adapter}"\nargs = {args}\n')
    write(tmp_path, "policy.toml", 'approvers = ["op"]\n')
    with pytest.raises(ConfigError) as refused:
        load(None, env(tmp_path))
    where = "profiles.p.args"
    assert str(refused.value) in (pa.REFUSED.format(where=where, index=0, adapter=adapter),
                                  pa.NOT_A_LIST.format(where=where))


def test_config_load_accepts_a_profile_with_no_args(tmp_path: Path) -> None:
    write(tmp_path, "config.toml",
          '[profiles.p]\nadapter = "codex"\nargs = []\n[profiles.q]\nadapter = "codex"\n')
    write(tmp_path, "policy.toml", 'approvers = ["op"]\n')
    assert set(load(None, env(tmp_path)).values["profiles"]) == {"p", "q"}
