from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentdown.core.clock import FixedClock, SystemClock


def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()
    assert now.utcoffset() == timedelta(0)


def test_fixed_clock_advances() -> None:
    start = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    clock = FixedClock(start)
    assert clock.now() == start
    clock.advance(timedelta(minutes=5))
    assert clock.now() == start + timedelta(minutes=5)


@pytest.mark.parametrize(
    "bad",
    [
        datetime(2026, 10, 8, 12, 0),  # naive  # noqa: DTZ001
        datetime(2026, 10, 8, 12, 0, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_fixed_clock_rejects_non_utc(bad: datetime) -> None:
    with pytest.raises(ValueError):
        FixedClock(bad)
