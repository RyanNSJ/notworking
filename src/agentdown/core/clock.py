"""Injectable UTC clock. Detector, rate-limit and fingerprint code must take a Clock,
never call datetime.now() directly, so tests can control time."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock for tests: starts at a given UTC instant and only moves when told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None or start.utcoffset() != timedelta(0):
            raise ValueError("FixedClock requires a UTC-aware datetime")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
