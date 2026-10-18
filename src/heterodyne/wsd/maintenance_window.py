"""Installer-attested, nonrenewable maintenance labels. This is never an admission permission.

The trusted installer must bind this data to genuine deployment authority. Local fields cannot prove
an authenticated Marmot decision. Unknown clocks or changed authority windows label unknown, and no
label suppresses an observer predicate or diagnostic.
"""

import math
import re
from typing import Literal

import msgspec


class Window(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    authority_id: str
    authority_hash: str
    installation_id: str
    generation: str
    start_utc: float
    end_utc: float


def validate(window: Window, installation: str, generation: str) -> None:
    if (
        not re.fullmatch("[A-Za-z0-9:_-]{1,128}", window.authority_id)
        or not re.fullmatch("[0-9a-f]{64}", window.authority_hash)
        or (window.installation_id, window.generation) != (installation, generation)
        or not math.isfinite(window.start_utc)
        or not math.isfinite(window.end_utc)
        or not 0 < window.end_utc - window.start_utc <= 1800
    ):
        raise ValueError("maintenance window binding or duration")


def label(
    window: Window | None, utc: float, prior: Window | None = None
) -> Literal["inactive", "expected", "unknown"]:
    if window is None:
        return "inactive"
    if not math.isfinite(utc) or (
        prior is not None and prior.authority_id == window.authority_id and prior != window
    ):
        return "unknown"
    return "expected" if window.start_utc <= utc <= window.end_utc else "inactive"
