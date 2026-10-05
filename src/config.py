# -*- coding: utf-8 -*-
"""Configuration objects for the STK Walker-Delta network generator.

The Walker notation used here is the classic ``i: T/P/F`` (Wertz), matching
``sat-net/src/utils/sim_config.py``:

    T  total number of satellites
    P  number of orbital planes
    F  inter-plane phasing factor

Terminology and formulas
------------------------
Let ``S = T / P`` be the satellites per plane. For plane index ``k`` (0-based)
and satellite index ``j`` (0-based) inside that plane:

    Delta:  RAAN(k) = k * 360/P              u(k, j) = j * 360/S + (k+1) * F * 360/T
    Star:   RAAN(k) = k * 180/P              u(k, j) = j * 360/S + (k+1) * F * 360/T

The ``(k+1)`` (rather than ``k``) reproduces the cumulative ``+= F*360/T``
accumulator in ``STKManager.create_constellation`` of the reference project. It
is a global rotation of the whole pattern and therefore does not change which
satellites are neighbours of which -- see ``docs`` in README.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# STK's UTCG "DateFormat" unit produces strings like ``1 Oct 2026 00:00:00.000``.
STK_TIME_FORMAT = "%d %b %Y %H:%M:%S.%f"
STK_TIME_FORMAT_NO_MS = "%d %b %Y %H:%M:%S"


def parse_stk_time(value: str) -> datetime:
    """Parse a UTCG timestamp as returned by STK data providers.

    Data providers emit *nanosecond* precision -- ``1 Oct 2026 00:00:00.000000000``
    -- while ``datetime.strptime``'s ``%f`` accepts at most six digits, so the
    fraction is trimmed to microseconds first. The scenario is set from
    strings that carry milliseconds; anything finer than a microsecond is far
    below the graph sampling step.
    """
    text = value.strip()
    if "." in text:
        head, _, fraction = text.partition(".")
        text = f"{head}.{fraction[:6].ljust(6, '0')}"

    for fmt in (STK_TIME_FORMAT, STK_TIME_FORMAT_NO_MS):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised STK time string: {value!r}")


def format_stk_time(value: datetime) -> str:
    """Render a datetime the way STK expects it when setting scenario times."""
    return value.strftime(STK_TIME_FORMAT)[:-3]  # keep milliseconds only


@dataclass(frozen=True)
class ScenarioConfig:
    """Scenario time window and sampling step."""

    name: str = "sat-offload"
    start: str = "1 Oct 2026 00:00:00.000"
    stop: str = "1 Oct 2026 02:00:00.000"
    step_seconds: float = 60.0

    @property
    def start_dt(self) -> datetime:
        return parse_stk_time(self.start)

    @property
    def stop_dt(self) -> datetime:
        return parse_stk_time(self.stop)


@dataclass(frozen=True)
class ConstellationConfig:
    """A Walker Delta (or Star) constellation, in ``i: T/P/F`` notation."""

    name: str = "WalkerDelta"
    total_sats: int = 24
    num_planes: int = 6
    phasing: int = 1

    # Orbit shape / orientation (two-body seed elements).
    semi_major_axis_km: float = 6928.0  # ~550 km altitude on an Earth-sized sphere
    inclination_deg: float = 53.0
    eccentricity: float = 0.0
    arg_perigee_deg: float = 0.0

    # Walker *Delta* spreads RAANs over the full 360 deg; a Walker *Star* uses
    # 180 deg. ``sat-net`` switches between the two with the ``is_delta`` flag.
    is_delta: bool = True

    #: In-plane index offset for cross-plane links. ``None`` derives the
    #: geometrically nearest neighbour; set an int to pin it.
    inter_plane_offset: int | None = None

    def __post_init__(self) -> None:
        if self.num_planes < 1:
            raise ValueError("num_planes must be >= 1")
        if self.total_sats < 1:
            raise ValueError("total_sats must be >= 1")
        if self.total_sats % self.num_planes:
            raise ValueError(
                f"total_sats ({self.total_sats}) must be divisible by "
                f"num_planes ({self.num_planes})"
            )

    @property
    def num_sat_per_plane(self) -> int:
        return self.total_sats // self.num_planes

    @property
    def raan_spread_deg(self) -> float:
        return 360.0 if self.is_delta else 180.0

    @property
    def raan_step_deg(self) -> float:
        """RAAN spacing between adjacent planes."""
        return self.raan_spread_deg / self.num_planes

    @property
    def phase_step_deg(self) -> float:
        """Argument-of-latitude advance from one plane to the next."""
        return self.phasing * 360.0 / self.total_sats

    def plane_raan_deg(self, plane: int) -> float:
        """RAAN of a 1-based plane index."""
        return (plane - 1) * self.raan_step_deg

    def true_anomaly_deg(self, plane: int, sat: int) -> float:
        """Argument of latitude of a 1-based (plane, satellite) pair."""
        spacing = 360.0 / self.num_sat_per_plane
        return ((sat - 1) * spacing + plane * self.phase_step_deg) % 360.0

    @property
    def resolved_inter_plane_offset(self) -> int:
        """The cross-plane index offset actually used, geometry-derived if unset."""
        if self.inter_plane_offset is not None:
            return self.inter_plane_offset
        # Imported lazily: src.topology imports this module.
        from src.topology import nearest_inter_plane_offset

        return nearest_inter_plane_offset(
            self.semi_major_axis_km,
            self.inclination_deg,
            self.raan_step_deg,
            self.phase_step_deg,
            self.num_sat_per_plane,
        )

    @staticmethod
    def satellite_name(plane: int, sat: int) -> str:
        """Instance name used throughout -- matches ``sat-net``'s ``Sat{plane}_{sat}``."""
        return f"Sat{plane}_{sat}"

    def describe(self) -> str:
        kind = "Delta" if self.is_delta else "Star"
        return (
            f"Walker {kind} {self.inclination_deg:g}: "
            f"{self.total_sats}/{self.num_planes}/{self.phasing} "
            f"({self.num_sat_per_plane} sats/plane, "
            f"a = {self.semi_major_axis_km:g} km)"
        )
