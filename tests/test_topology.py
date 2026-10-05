# -*- coding: utf-8 -*-
"""Topology checks that need no STK licence -- run with ``python tests/test_topology.py``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import ConstellationConfig, parse_stk_time  # noqa: E402
from src.topology import (  # noqa: E402
    _mean_chord_km,
    inter_plane_pairs,
    inter_plane_worst_chord_km,
    intra_plane_pairs,
    isl_pairs,
    line_of_sight_limit_km,
    wrap_index,
)
from src.utils.palette import distinct_rgb_colors  # noqa: E402

# The reference configuration from sat-net/src/utils/sim_config.py.
REFERENCE = ConstellationConfig(
    total_sats=1584, num_planes=72, phasing=11, inclination_deg=53.0
)
# A small constellation used for the structural checks below.
SMALL = ConstellationConfig(total_sats=24, num_planes=6, phasing=1, inclination_deg=53.0)


def test_walker_delta_elements() -> None:
    config = SMALL  # T=24, P=6, F=1 -> S=4, RAAN step 60, phase step 15
    assert config.num_sat_per_plane == 4
    assert config.raan_step_deg == 60.0
    assert config.phase_step_deg == 15.0

    assert [config.plane_raan_deg(p) for p in range(1, 7)] == [0, 60, 120, 180, 240, 300]
    # Plane 1 stays 15 deg ahead of plane 0's reference; plane p is p*15 deg ahead.
    assert [config.true_anomaly_deg(1, s) for s in range(1, 5)] == [15, 105, 195, 285]
    assert [config.true_anomaly_deg(2, s) for s in range(1, 5)] == [30, 120, 210, 300]


def test_walker_star_spreads_raan_over_180() -> None:
    star = ConstellationConfig(total_sats=24, num_planes=6, phasing=1, is_delta=False)
    assert star.raan_step_deg == 30.0


def test_wrap_index_matches_reference_expression() -> None:
    """``wrap_index`` must equal ``(sat + F) % S or S`` from sat-net."""
    sats, phasing = REFERENCE.num_sat_per_plane, REFERENCE.phasing  # 22, 11
    assert (sats, phasing) == (22, 11)
    for sat in range(1, sats + 1):
        expected = int((sat + phasing) % sats) or sats
        assert wrap_index(sat, phasing, sats) == expected, sat


def test_link_counts_and_uniqueness() -> None:
    links = isl_pairs(SMALL)
    pairs = [frozenset((link.source, link.target)) for link in links]
    assert len(pairs) == len(set(pairs)), "isl_pairs returned duplicates"
    # One ring link plus one cross-plane link per satellite.
    assert len(links) == 2 * SMALL.total_sats
    assert sum(1 for link in links if link.kind == "intra") == SMALL.total_sats
    assert sum(1 for link in links if link.kind == "inter") == SMALL.total_sats
    # A ring in each plane: every satellite appears in exactly two intra links.
    assert len(list(intra_plane_pairs(SMALL))) == SMALL.total_sats
    assert len(list(inter_plane_pairs(SMALL))) == SMALL.total_sats


def test_last_plane_wrap_is_a_true_neighbour() -> None:
    """The wrap-around link must be as tight as a normal inter-plane link.

    Walking the RAAN circle advances the argument of latitude by exactly ``F``
    intra-plane spacings, so ``Sat{P}_{n}`` pairs with ``Sat{1}_{n+F}`` modulo S.
    If that is right, the phase residual of the wrap link equals the residual of
    an ordinary adjacent-plane link -- not something a naive ``+1`` would give.
    """
    config = REFERENCE
    sats = config.num_sat_per_plane
    ordinary = config.phase_step_deg  # residual of a normal adjacent-plane link

    wrapped = [
        link for link in inter_plane_pairs(config) if link.source.startswith(f"Sat{config.num_planes}_")
    ]
    assert len(wrapped) == sats

    for link in wrapped:
        _, sat = link.source.split("_")
        _, target_sat = link.target.split("_")
        # Plane P (1-based) vs plane 1: the phase difference of the two planes is
        # (P-1) * phase_step, which is what the index offset has to cancel.
        delta = (int(target_sat) - int(sat)) * (360.0 / sats) - (config.num_planes - 1) * config.phase_step_deg
        # Residual mod 360 must shrink to the same size as an ordinary link.
        residual = min(abs(delta % 360.0), abs((delta % 360.0) - 360.0))
        assert abs(residual - ordinary) < 1e-9, (link, residual, ordinary)


def test_auto_offset_keeps_reference_config_at_same_index() -> None:
    """F is small here, so the same-index neighbour really is the nearest one."""
    assert REFERENCE.resolved_inter_plane_offset == 0


def test_auto_offset_finds_a_reachable_neighbour_when_same_index_is_not() -> None:
    """The 20/10/45 case: same-index pairs are ~9900 km apart and never visible.

    Every ISL must at least be closer than the line-of-sight limit, otherwise
    the run silently produces zero inter-plane links.
    """
    config = ConstellationConfig(
        total_sats=200, num_planes=20, phasing=45, inclination_deg=53.0
    )
    offset = config.resolved_inter_plane_offset
    assert offset == -3

    limit = line_of_sight_limit_km(config.semi_major_axis_km)
    assert inter_plane_worst_chord_km(config, offset) < limit
    # The same-index pairing the old hardcoded rule produced is way out of range.
    assert inter_plane_worst_chord_km(config, 0) > limit


def test_auto_offset_is_the_nearest_available() -> None:
    """No other offset beats the chosen one on mean separation."""
    config = ConstellationConfig(
        total_sats=200, num_planes=20, phasing=45, inclination_deg=53.0
    )
    chosen = config.resolved_inter_plane_offset
    spacing = 360.0 / config.num_sat_per_plane

    def mean(offset: int) -> float:
        return _mean_chord_km(
            config.semi_major_axis_km, config.inclination_deg, config.raan_step_deg,
            config.phase_step_deg, spacing, offset,
        )

    best = mean(chosen)
    for offset in range(-5, 6):
        assert best <= mean(offset) + 1e-9, offset


def test_explicit_offset_overrides_auto() -> None:
    pinned = ConstellationConfig(
        total_sats=200, num_planes=20, phasing=45, inclination_deg=53.0,
        inter_plane_offset=0,
    )
    assert pinned.resolved_inter_plane_offset == 0


def test_wrap_link_covers_offset_plus_phasing() -> None:
    """Plane P closes onto plane 1 one full phasing step beyond a forward link."""
    config = ConstellationConfig(
        total_sats=200, num_planes=20, phasing=45, inclination_deg=53.0
    )
    offset = config.resolved_inter_plane_offset
    wraps = [l for l in inter_plane_pairs(config) if l.source.startswith("Sat20_")]
    assert len(wraps) == config.num_sat_per_plane
    for link in wraps:
        sat = int(link.source.split("_")[1])
        target = int(link.target.split("_")[1])
        expected = ((sat + offset + config.phasing - 1) % config.num_sat_per_plane) + 1
        assert target == expected, link


def test_palette_is_distinct_and_deterministic() -> None:
    colors = distinct_rgb_colors(6)
    assert len(colors) == 6
    assert len(set(colors)) == 6
    assert colors == distinct_rgb_colors(6)
    assert distinct_rgb_colors(0) == []


def test_invalid_constellation_rejected() -> None:
    for kwargs in ({"total_sats": 25, "num_planes": 6}, {"total_sats": 0, "num_planes": 6}):
        try:
            ConstellationConfig(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_parse_stk_time() -> None:
    assert parse_stk_time("1 Oct 2026 00:00:00.000").year == 2026
    assert parse_stk_time("11 Oct 2026 23:59:59.500").second == 59


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception as error:  # noqa: BLE001 - report every failure, keep going
            failures += 1
            print(f"FAIL {test.__name__}: {type(error).__name__}: {error}")
        else:
            print(f"ok   {test.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
