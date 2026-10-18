"""Pure immutable unit rendering. Rendering never enables or starts any unit."""

from collections.abc import Mapping

from heterodyne import units
from heterodyne.wsd.schedule_manifest import ScheduleManifest

SERVICE = """[Unit]
Description=heterodyne scheduled {job}

[Service]
Type=oneshot
ExecStart={{python}} -m heterodyne.wsd scheduled-wake {job}
TimeoutStartSec=10
UMask=0077
{{environment}}
"""
TIMER = """[Unit]
Description=heterodyne scheduled {job} timer

[Timer]
OnBootSec=5
OnUnitActiveSec={seconds}
AccuracySec=1
RandomizedDelaySec=0
WakeSystem=false
Unit=heterodyne-{job}.service

[Install]
WantedBy=timers.target
"""


def render(manifest: ScheduleManifest, python: str, env: Mapping[str, str]) -> dict[str, str]:
    if manifest.version != 1 or not {"pickup", "reconcile"} <= set(manifest.jobs):
        raise ValueError("scheduled unit manifest")
    result: dict[str, str] = {}
    for job, seconds in (("pickup", manifest.backstop_seconds), ("reconcile", manifest.reconcile_seconds)):
        if type(seconds) is not int or not 1 <= seconds <= 86400:
            raise ValueError("scheduled cadence")
        result[f"heterodyne-{job}.service"] = units.render(
            SERVICE.format(job=job),
            python,
            path=env.get("PATH", ""),
            config_dir=manifest.config.path,
            state_dir=manifest.state.path,
            release=manifest.release_hash,
        )
        result[f"heterodyne-{job}.timer"] = TIMER.format(job=job, seconds=seconds)
    if "babysitter" in manifest.jobs:
        result["heterodyne-babysitter.service"] = units.render(
            SERVICE.format(job="babysitter").replace("scheduled-wake babysitter", "babysitter"),
            python,
            path=env.get("PATH", ""),
            config_dir=manifest.config.path,
            state_dir=manifest.state.path,
            release=manifest.release_hash,
        )
        result["heterodyne-babysitter.timer"] = TIMER.format(job="babysitter", seconds=300)
    return result
