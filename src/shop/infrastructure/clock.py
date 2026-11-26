"""Системний годинник."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Повертає поточний час у UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)
