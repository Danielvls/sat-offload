# -*- coding: utf-8 -*-
"""Which satellite pairs are wired together.

Pure functions over :class:`~src.config.ConstellationConfig` -- no STK import --
so the neighbour logic can be reasoned about and tested on its own.

Two link families, matching ``STKManager.get_sat_access`` in ``sat-net``:

``intra``
    The ring inside each orbital plane: ``Sat{p}_{n}`` <-> ``Sat{p}_{n+1}``,
    wrapping from the last satellite back to the first.

``inter``
    Between neighbouring planes at the *same* in-plane index:
    ``Sat{p}_{n}`` <-> ``Sat{p+1}_{n}``.

    The last plane wraps around to plane 1 with a phasing offset. Walking the
    full RAAN circle advances the argument of latitude by ``F`` intra-plane
    spacings, because::

        sum over all planes of (F * 360/T)  =  P * F * 360 / (P * S)
                                           =  F * 360/S

    so ``Sat{P}_{n}`` pairs with ``Sat{1}_{n+F}`` (mod ``S``). That is exactly
    the ``(sat_num + F) % (T/P) or (T/P)`` expression in the reference project,
    and it holds for any ``T/P/F`` -- not just the reference's 1584/72/11.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterator, Literal

from src.config import ConstellationConfig

LinkKind = Literal["intra", "inter"]

#: Earth equatorial radius, WGS-84 (km) -- only used to model line of sight.
EARTH_RADIUS_KM = 6378.137


# --------------------------------------------------------------- geometry ---
#
# The in-plane index offset between neighbouring planes is *not* always zero.
# Walking one plane advances the argument of latitude by ``phi = F*360/T``, so
# the satellite that lines up with ``Sat{p}_{n}`` in plane ``p+1`` sits at index
# ``n + offset`` with ``offset ~ -phi / (360/S)``. Hardcoding offset 0 -- as the
# reference project does -- only works while ``F`` is small: for
# ``200/20/45`` the same-index pair is ~9900 km apart and never sees each other
# at all, so not one inter-plane link is ever built.
#
# Which offset is right is decided by actual chord distance rather than by the
# phase residual alone, because the RAAN separation contributes too and the two
# criteria disagree once ``phi`` is large.


def satellite_position(
    radius_km: float, inclination_deg: float, raan_deg: float, arg_lat_deg: float
) -> tuple[float, float, float]:
    """Cartesian ECI position of a circular orbit, in km."""
    inc, raan, u = map(math.radians, (inclination_deg, raan_deg, arg_lat_deg))
    return (
        radius_km * (math.cos(u) * math.cos(raan) - math.sin(u) * math.cos(inc) * math.sin(raan)),
        radius_km * (math.cos(u) * math.sin(raan) + math.sin(u) * math.cos(inc) * math.cos(raan)),
        radius_km * math.sin(u) * math.sin(inc),
    )


def line_of_sight_limit_km(radius_km: float) -> float:
    """Longest chord between two satellites at ``radius_km`` that clears Earth."""
    return 2.0 * math.sqrt(radius_km ** 2 - EARTH_RADIUS_KM ** 2)


def _mean_chord_km(
    radius_km: float,
    inclination_deg: float,
    raan_step_deg: float,
    phase_step_deg: float,
    spacing_deg: float,
    offset: int,
    samples: int = 360,
) -> float:
    """Mean separation between a satellite and its offset neighbour over one orbit."""
    total = 0.0
    for step in range(samples):
        u = 360.0 * step / samples
        near = satellite_position(radius_km, inclination_deg, 0.0, u)
        far = satellite_position(
            radius_km, inclination_deg, raan_step_deg,
            u + phase_step_deg + offset * spacing_deg,
        )
        total += math.dist(near, far)
    return total / samples


@lru_cache(maxsize=64)
def nearest_inter_plane_offset(
    radius_km: float,
    inclination_deg: float,
    raan_step_deg: float,
    phase_step_deg: float,
    sats_per_plane: int,
) -> int:
    """In-plane index offset that gives the tightest cross-plane link.

    Sweeps every offset around the ring and returns the one with the smallest
    mean separation over an orbit. Cached, since the sweep is geometry-only.
    """
    spacing = 360.0 / sats_per_plane
    half = sats_per_plane // 2
    candidates = range(-half, half + 1)
    return min(
        candidates,
        key=lambda offset: _mean_chord_km(
            radius_km, inclination_deg, raan_step_deg, phase_step_deg, spacing, offset
        ),
    )


def inter_plane_worst_chord_km(config: ConstellationConfig, offset: int, samples: int = 360) -> float:
    """Worst-case separation for a cross-plane link -- compare against the LOS limit."""
    spacing = 360.0 / config.num_sat_per_plane
    worst = 0.0
    for step in range(samples):
        u = 360.0 * step / samples
        near = satellite_position(config.semi_major_axis_km, config.inclination_deg, 0.0, u)
        far = satellite_position(
            config.semi_major_axis_km, config.inclination_deg, config.raan_step_deg,
            u + config.phase_step_deg + offset * spacing,
        )
        worst = max(worst, math.dist(near, far))
    return worst


@dataclass(frozen=True)
class ISLLink:
    """One inter-satellite link to compute in STK."""

    source: str
    target: str
    kind: LinkKind

    @property
    def key(self) -> str:
        return f"{self.source}_to_{self.target}"


def wrap_index(sat: int, offset: int, sats_per_plane: int) -> int:
    """1-based index ``sat + offset`` wrapped onto ``1..sats_per_plane``."""
    return ((sat + offset - 1) % sats_per_plane) + 1


def intra_plane_pairs(config: ConstellationConfig) -> Iterator[ISLLink]:
    """Ring links inside every plane (one per satellite)."""
    sats = config.num_sat_per_plane
    for plane in range(1, config.num_planes + 1):
        for sat in range(1, sats + 1):
            nxt = sat + 1 if sat < sats else 1
            yield ISLLink(
                config.satellite_name(plane, sat),
                config.satellite_name(plane, nxt),
                "intra",
            )


def inter_plane_pairs(config: ConstellationConfig) -> Iterator[ISLLink]:
    """Links between adjacent planes, including the phasing wrap-around.

    ``Sat{p}_{n}`` pairs with ``Sat{p+1}_{n+offset}``, where ``offset`` is the
    geometrically nearest index (see :func:`nearest_inter_plane_offset`), not
    necessarily zero.
    """
    planes = config.num_planes
    sats = config.num_sat_per_plane
    offset = config.resolved_inter_plane_offset
    # Closing plane P onto plane 1 covers the same phase advance as a forward
    # step *plus* the full RAAN circle, which is exactly ``F`` more satellite
    # spacings:  sum over all planes of phi = P * F * 360/T = F * (360/S).
    wrap_offset = offset + config.phasing

    for plane in range(1, planes + 1):
        for sat in range(1, sats + 1):
            if plane < planes:
                yield ISLLink(
                    config.satellite_name(plane, sat),
                    config.satellite_name(plane + 1, wrap_index(sat, offset, sats)),
                    "inter",
                )
            else:
                yield ISLLink(
                    config.satellite_name(plane, sat),
                    config.satellite_name(1, wrap_index(sat, wrap_offset, sats)),
                    "inter",
                )


def isl_pairs(config: ConstellationConfig) -> list[ISLLink]:
    """Every intra- and inter-plane link, de-duplicated."""
    seen: set[frozenset[str]] = set()
    links: list[ISLLink] = []
    for link in (*intra_plane_pairs(config), *inter_plane_pairs(config)):
        key = frozenset((link.source, link.target))
        if key in seen:
            continue
        seen.add(key)
        links.append(link)
    return links
