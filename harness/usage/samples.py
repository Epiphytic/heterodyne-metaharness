"""Validated quota observations and UTC estimate windows."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math

INTERVALS = ('5h', 'daily', 'weekly', 'monthly')
THRESHOLDS = (50, 80, 90, 95, 99, 100)


def number(value):
    if isinstance(value, bool):
        raise ValueError('boolean is not a quota value')
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError('quota value must be finite and nonnegative')
    return result


def window(interval, now):
    dt = datetime.fromtimestamp(now, timezone.utc)
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == '5h':
        start = int(now // 18000) * 18000
        return start, start + 18000
    if interval == 'daily':
        start, end = day, day + timedelta(days=1)
    elif interval == 'weekly':
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=7)
    elif interval == 'monthly':
        start = day.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
    else:
        raise ValueError('unknown interval: ' + interval)
    return int(start.timestamp()), int(end.timestamp())


@dataclass(frozen=True)
class Sample:
    source: str
    model: str
    interval: str
    start: int
    end: int
    percent: float
    method: str
    observed: float

    def __post_init__(self):
        if not self.source or not self.model or self.interval not in INTERVALS:
            raise ValueError('sample requires source, model and supported interval')
        number(self.percent)
        if not number(self.start) <= number(self.observed) < number(self.end):
            raise ValueError('sample observation outside window')

    def data(self):
        return asdict(self)
