from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from heterodyne.wsd.effect_slots import Clock, Sample, civil_instant, civil_slot, interval_slot


def test_clock_restart_rollback_suspend_and_unknown():
    clock = Clock(100)
    assert not clock.observe(Sample("boot", 100, 0, True))
    assert not clock.observe(Sample("boot", 110, 10, True))
    assert clock.observe(Sample("boot", 130, 30, True))
    assert not clock.observe(Sample("boot", 160, 31, True))
    assert not clock.observe(Sample("boot", 150, 32, True))
    assert not clock.observe(Sample("boot", 160, 40, False))
    assert not clock.observe(Sample("new", 160, 1, True))
    assert clock.observe(Sample("new", 190, 31, True))


def test_civil_gap_fold_and_pre_activation():
    zone = ZoneInfo("America/New_York")
    assert civil_instant(date(2026, 3, 8), time(2, 30), zone) == datetime(2026, 3, 8, 7, tzinfo=UTC)
    assert civil_instant(date(2026, 11, 1), time(1, 30), zone) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert (
        civil_slot(
            datetime(2026, 3, 8, 7, tzinfo=UTC), datetime(2026, 3, 8, 7, 1, tzinfo=UTC), time(2, 30), zone
        )
        is None
    )
    assert interval_slot(10, 11, 5) is None
    assert interval_slot(26, 11, 5) == 3
