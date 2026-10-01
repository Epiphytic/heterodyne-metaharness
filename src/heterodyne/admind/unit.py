"""A systemd user unit for admind, rendered on the host (never committed; ADR 0001 §15, §16)."""

from collections.abc import Mapping

from heterodyne.config.secret_scan import identifier_kind

TEMPLATE = """\
[Unit]
Description=heterodyne admind (admin override channel)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} -m heterodyne.admind run
Restart=always
RestartSec=5
RestartPreventExitStatus=78
UMask=0077
{environment}
[Install]
WantedBy=default.target
"""
PASSED_THROUGH = ("PATH", "HETERODYNE_CONFIG_DIR", "HETERODYNE_STATE_DIR", "XDG_CONFIG_HOME",
                  "XDG_STATE_HOME")
_UNSAFE = set(" \t\n\"'\\")


def _control(ch: str) -> bool:
    return ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F


def render(python: str, env: Mapping[str, str]) -> str:
    """The unit text. PATH and location variables are captured from `env` so the service finds the
    same `claude`, `wn-agent` and config as the shell that rendered it; `%` is escaped for systemd."""
    values = {"python": python, **{k: env[k] for k in PASSED_THROUGH if env.get(k)}}
    for name, value in values.items():
        if _UNSAFE & set(value) or any(_control(ch) for ch in value):
            raise ValueError(f"{name} contains whitespace, a quote, a backslash or a control "
                             "character; systemd would split or unquote it")
        kind = identifier_kind(value)
        if kind:  # the unit must be installable verbatim, so refuse rather than redact
            raise ValueError(f"{name} contains an {kind}, which must not be printed; "
                             "use a path without it")
    environment = "".join(f"Environment={k}={env[k].replace('%', '%%')}\n"
                          for k in PASSED_THROUGH if env.get(k))
    return TEMPLATE.format(python=python.replace("%", "%%"), environment=environment)
