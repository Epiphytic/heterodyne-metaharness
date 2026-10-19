"""Plan 8 slot arithmetic and conservative civil-clock readiness, independent of producers."""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Sample:
    boot: str
    utc: float
    monotonic: float
    synchronized: bool


class Clock:
    """Readiness is earned again after every restart, boot change or discontinuity."""

    def __init__(self, high_water: float = 0) -> None:
        self.high_water = high_water
        self.previous: Sample | None = None
        self.ready = False

    def observe(self, sample: Sample) -> bool:
        old = self.previous
        finite = math.isfinite(sample.utc) and math.isfinite(sample.monotonic)
        sane = (
            finite
            and bool(sample.boot)
            and sample.synchronized
            and sample.utc >= self.high_water
            and sample.monotonic >= 0
        )
        if not sane:
            self.previous = None
            self.ready = False
            return False
        if old is None or old.boot != sample.boot:
            self.ready = False
        else:
            elapsed = sample.monotonic - old.monotonic
            jump = abs(sample.utc - old.utc - elapsed)
            if elapsed < 0 or jump > 2:
                self.previous = None
                self.ready = False
                return False
            elif elapsed >= 30:
                self.ready = True
            else:
                return self.ready  # retain the first stable baseline until thirty seconds elapse
        self.previous = sample
        return self.ready


def interval_slot(now: float, activation: float, period: int) -> int | None:
    if period <= 0 or not all(math.isfinite(v) for v in (now, activation)) or now < activation:
        return None
    return math.floor((now - activation) / period)


def civil_instant(day: date, intended: time, zone: ZoneInfo) -> datetime:
    """First offset for a fold; first valid civil instant following a gap."""
    naive = datetime.combine(day, intended.replace(tzinfo=None))
    for seconds in range(86401):
        candidate = (naive + timedelta(seconds=seconds)).replace(tzinfo=zone, fold=0)
        utc = candidate.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) == candidate.replace(tzinfo=None):
            return utc
    raise ValueError("civil date has no valid instant within one day")


def civil_slot(now: datetime, activation: datetime, intended: time, zone: ZoneInfo) -> int | None:
    if now.tzinfo is None or activation.tzinfo is None:
        raise ValueError("aware instants required")
    day = now.astimezone(zone).date()
    due = civil_instant(day, intended, zone)
    if due > now:
        day -= timedelta(days=1)
        due = civil_instant(day, intended, zone)
    return day.toordinal() if due >= activation else None
