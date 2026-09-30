import tomllib
from pathlib import Path
from typing import Any

import pytest

from heterodyne.config import ConfigError, load
from heterodyne.config.secret_scan import check, is_reference, secret_name, secret_value

ROOT = Path(__file__).resolve().parent.parent

# Fake secret material is assembled at runtime so no literal token sits in the repo
# (keeps the Task 9 gitleaks gate quiet).
FAKE = {
    "nsec": "nsec1" + "q" * 58,
    "pem": "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
    "pem_rsa": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "sk": "sk" + "-" + "a1B2" * 8,
    "sk_ant": "sk" + "-ant-api03-" + "x9" * 20,
    "ghp": "gh" + "p_" + "A1" * 18,
    "gho": "gh" + "o_" + "A1" * 18,
    "github_pat": "github" + "_pat_" + "11" + "A" * 40,
    "slack": "xo" + "xb-" + "1234567890-abcdefghij",
    "aws": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "jwt": "ey" + "JhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxIn0." + "c2lnbmF0dXJlLXZhbHVl",
}


@pytest.mark.parametrize("name", [
    "credentials", "credential", "cred", "creds", "auth", "authorization", "bearer", "cookie",
    "passphrase", "pass", "password", "passwd", "pwd", "secret", "secrets", "token", "tokens",
    "nsec", "apikey", "api_key", "private_key", "privkey", "client_secret", "access_key",
    "session", "key", "signing_key", "auth_token", "marmot-password", "db.pwd", "apiKey",
    "authToken", "AUTH_TOKEN",
])
def test_secret_names_flagged(name: str) -> None:
    assert secret_name(name)


@pytest.mark.parametrize("name", [
    "keyboard", "monkey", "passthrough", "author", "tokenizer_model", "model", "socket",
    "key_file", "ssh_key_path", "public_key", "marmot_npub", "radicle_did", "hotkeys",
])
def test_non_secret_names_not_flagged(name: str) -> None:
    assert not secret_name(name)


def _keys(tree: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(tree, list):
        for item in tree:
            out += _keys(item)
    elif isinstance(tree, dict):
        for k, v in tree.items():
            out.append((k, v))
            if not is_reference(v):
                out += _keys(v)
    return out


def test_no_false_positives_on_shipped_config_keys() -> None:
    files = [ROOT / "src/heterodyne/defaults/defaults.toml", *sorted((ROOT / "examples").rglob("*.toml"))]
    seen: list[str] = []
    for path in files:
        tree = tomllib.loads(path.read_text())
        for key, value in _keys(tree):
            seen.append(key)
            # Only keys that hold a reference may look secret-named.
            assert secret_name(key) == is_reference(value), f"{path.name}: {key}"
    defaults = tomllib.loads((ROOT / "src/heterodyne/defaults/defaults.toml").read_text())
    for cls in {c for classes in defaults["tiers"].values() for c in classes}:
        assert not secret_name(cls), cls  # action classes are keys in policy.toml [tiers]
    assert "auth_token" in seen


@pytest.mark.parametrize("kind", sorted(FAKE))
def test_secret_values_detected_anywhere(kind: str) -> None:
    assert secret_value(FAKE[kind]) is not None
    assert secret_value(f"Bearer {FAKE[kind]}") is not None
    with pytest.raises(ConfigError, match=r"layer: a\.list\[1\]\.note: value looks like secret"):
        check({"a": {"list": [{}, {"note": FAKE[kind]}]}}, "layer")


@pytest.mark.parametrize("text", [
    "pypi.org", "sk-short", "codex", "<model-name>", "ghp_", "eyJ.not.jwt", "nsec1short",
    "-----BEGIN PUBLIC KEY-----", "AKIA", "/run/secrets/marmot", "pass show marmot/token",
])
def test_ordinary_values_not_flagged(text: str) -> None:
    assert secret_value(text) is None


def test_secret_value_inside_reference_command_rejected() -> None:
    with pytest.raises(ConfigError, match="looks like secret"):
        check({"auth_token": {"command": f"echo {FAKE['ghp']}"}}, "layer")


def test_secret_value_as_key_rejected() -> None:
    with pytest.raises(ConfigError, match="looks like secret"):
        check({"identities": {FAKE["nsec"]: {}}}, "layer")


def test_error_never_echoes_the_secret() -> None:
    with pytest.raises(ConfigError) as exc:
        check({"x": FAKE["ghp"]}, "layer")
    assert FAKE["ghp"] not in str(exc.value)


# Through load(), in every file layer.

def write(d: Path, name: str, text: str) -> None:
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    write(tmp_path, "config.toml", '[profiles.a]\nadapter = "codex"\nmodel = "m"\n')
    write(tmp_path, "policy.toml", 'approvers = ["op"]\n')
    return tmp_path


def env(d: Path) -> dict[str, str]:
    return {"HETERODYNE_CONFIG_DIR": str(d), "HOME": str(d)}


def test_credentials_name_rejected_in_host(cfg: Path) -> None:
    write(cfg, "config.toml", '[integrations.marmot]\ncredentials = "inline-secret"\n')
    with pytest.raises(ConfigError, match=r"config\.toml: integrations\.marmot\.credentials: secrets"):
        load(None, env(cfg))


@pytest.mark.parametrize(("name", "text"), [
    ("config.toml", f'[integrations.x]\nnote = "{FAKE["ghp"]}"\n'),
    ("policy.toml", f'approvers = ["op"]\n[identities.op]\nmarmot_npub = "{FAKE["nsec"]}"\n'),
    ("workstreams/w.toml", f'[repos]\nmain = ["{FAKE["aws"]}"]\n'),
])
def test_secret_value_rejected_in_every_layer(cfg: Path, name: str, text: str) -> None:
    write(cfg, name, text)
    with pytest.raises(ConfigError, match="looks like secret"):
        load("w" if name.startswith("workstreams") else None, env(cfg))


def test_secret_value_rejected_in_env_layer(cfg: Path) -> None:
    with pytest.raises(ConfigError, match=r"environment: HETERODYNE_LOG_LEVEL: value looks like secret"):
        load(None, {**env(cfg), "HETERODYNE_LOG_LEVEL": FAKE["jwt"]})


@pytest.mark.parametrize("model", ["[]", "5", "{ a = 1 }"])
def test_profile_model_must_be_string(cfg: Path, model: str) -> None:
    write(cfg, "config.toml", f'[profiles.a]\nadapter = "codex"\nmodel = {model}\n')
    with pytest.raises(ConfigError, match=r"profiles\.a\.model"):
        load(None, env(cfg))
