"""A systemd user unit for admind, rendered on the host (never committed; ADR 0001 §15, §16)."""

from collections.abc import Mapping

from heterodyne import units
from heterodyne.config import paths

TEMPLATE = """\
[Unit]
Description=heterodyne admind (admin override channel)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} -m heterodyne.admind run
Restart=always
# control-group (the default, stated here on purpose) kills only what is in this unit: wn-agent, which
# must die with admind because its Marmot home has a single opener. The admin agent's tmux server runs
# in its own transient scope, outside this cgroup, so a stop or restart leaves the agent running.
KillMode=control-group
RestartSec=5
RestartPreventExitStatus=78
UMask=0077
{environment}
[Install]
WantedBy=default.target
"""


def render(python: str, env: Mapping[str, str], *, config_dir: str | None = None,
           state_dir: str | None = None, release: str | None = None) -> str:
    """The unit text (heterodyne.units.render). PATH is `env`'s, so the service finds the same `claude`
    and `wn-agent` as the shell that rendered it; locations not given are resolved from `env` as
    heterodyne reads them, and written into the unit explicitly."""
    return units.render(TEMPLATE, python, path=env.get("PATH", ""),
                        config_dir=config_dir or str(paths.config_dir(env)),
                        state_dir=state_dir or str(paths.state_dir(env)), release=release)
