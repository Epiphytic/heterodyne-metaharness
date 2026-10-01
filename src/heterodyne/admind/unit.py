"""A systemd user unit for admind, rendered on the host (never committed; ADR 0001 §15, §16)."""

from collections.abc import Mapping

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


def render(python: str, env: Mapping[str, str]) -> str:
    """The unit text. PATH and location variables are captured from `env` so the service finds the
    same `claude`, `wn-agent` and config as the shell that rendered it; `%` is escaped for systemd."""
    values = {"python": python, **{k: env[k] for k in PASSED_THROUGH if env.get(k)}}
    for name, value in values.items():
        if _UNSAFE & set(value):
            raise ValueError(f"{name} contains whitespace, a quote or a backslash; "
                             "systemd would split or unquote it")
    environment = "".join(f"Environment={k}={env[k].replace('%', '%%')}\n"
                          for k in PASSED_THROUGH if env.get(k))
    return TEMPLATE.format(python=python.replace("%", "%%"), environment=environment)
