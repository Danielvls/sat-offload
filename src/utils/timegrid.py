# -*- coding: utf-8 -*-
"""A fixed-step time grid, used to snap STK access samples onto graph snapshots.

Replaces ``sat-net``'s ``pandas.date_range`` based helper so this project needs
no pandas dependency. STK reports access samples at whatever instants it likes;
we map each sample to the nearest grid point with ``bisect``.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta
from typing import Iterator, Optional

from src.config import parse_stk_time


class TimeGrid:
    """Inclusive, fixed-step sequence of timestamps."""

    def __init__(self, start: datetime, stop: datetime, step_seconds: float):
        if step_seconds <= 0:
            raise ValueError("step_seconds must be positive")
        if stop < start:
            raise ValueError("stop must not precede start")

        self.start = start
        self.stop = stop
        self.step_seconds = float(step_seconds)

        total_seconds = (stop - start).total_seconds()
        count = int(total_seconds // self.step_seconds) + 1
        self._times = [start + timedelta(seconds=i * self.step_seconds) for i in range(count)]

    def __len__(self) -> int:
        return len(self._times)

    def __iter__(self) -> Iterator[datetime]:
        return iter(self._times)

    def __getitem__(self, index: int) -> datetime:
        return self._times[index]

    @property
    def times(self) -> list[datetime]:
        return list(self._times)

    def index_of(self, moment: datetime) -> Optional[int]:
        """Nearest grid index for ``moment``, or ``None`` if it falls outside.

        Samples further than half a step outside the window are dropped rather
        than clamped, so a stray access sample cannot land on the wrong slice.
        """
        offset = (moment - self.start).total_seconds() / self.step_seconds
        index = int(round(offset))
        if index < 0 or index >= len(self._times):
            return None
        # Guard against float drift pushing a sample onto the wrong neighbour.
        if abs(self._times[index] - moment) > timedelta(seconds=self.step_seconds / 2):
            return None
        return index

    def index_of_stk_time(self, value: str) -> Optional[int]:
        """Convenience wrapper for a raw STK time string."""
        return self.index_of(parse_stk_time(value))

    def iso(self, index: int) -> str:
        return self._times[index].isoformat()

    def nearest_before(self, moment: datetime) -> Optional[int]:
        """Index of the greatest grid point ``<= moment`` (or ``None``)."""
        position = bisect.bisect_right(self._times, moment)
        return position - 1 if position > 0 else None
