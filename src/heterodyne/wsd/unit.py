"""A systemd user unit for wsd, rendered on the host (never committed; ADR 0001 §15, §16; docs/wsd.md)."""

from collections.abc import Mapping

from heterodyne import units
from heterodyne.config import paths

TEMPLATE = """\
[Unit]
Description=heterodyne wsd (workstream daemon)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} -m heterodyne.wsd run
Restart=always
# control-group kills only what is in this unit. The tmux server holding the agents runs in a transient
# scope of its own (sandbox/build.py), outside this cgroup, so a stop or restart leaves it running.
KillMode=control-group
RestartSec=5
RestartPreventExitStatus=78
# On SIGTERM wsd waits up to 120 s for running jobs (docs/wsd.md); the stop timeout must exceed it.
TimeoutStopSec=150
UMask=0077
{environment}
[Install]
WantedBy=default.target
"""


def render(python: str, env: Mapping[str, str], *, config_dir: str | None = None,
           state_dir: str | None = None, release: str | None = None) -> str:
    """The unit text (heterodyne.units.render). It never carries BTQ_REPO, which btq would read as the
    beads repository (docs/wsd.md); locations not given are resolved from `env` and written explicitly."""
    return units.render(TEMPLATE, python, path=env.get("PATH", ""),
                        config_dir=config_dir or str(paths.config_dir(env)),
                        state_dir=state_dir or str(paths.state_dir(env)), release=release)
