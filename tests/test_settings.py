# -*- coding: utf-8 -*-
"""Config-loading checks -- run with ``python tests/test_settings.py``."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.settings import EARTH_RADIUS_KM, ConfigError, load_settings  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_CONFIG = PROJECT_ROOT / "config.yaml"


def write_config(text: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    handle.write(text)
    handle.close()
    return Path(handle.name)


def expect_error(text: str, fragment: str) -> None:
    path = write_config(text)
    try:
        load_settings(path)
    except ConfigError as error:
        assert fragment in str(error), f"expected {fragment!r} in {error!r}"
    else:
        raise AssertionError(f"expected ConfigError containing {fragment!r}")


def test_shipped_config_loads() -> None:
    """Structure only -- the shipped constellation is retuned often, so asserting
    particular plane/satellite counts here just breaks the suite on every edit."""
    settings = load_settings(SHIPPED_CONFIG)
    constellation = settings.constellation

    assert constellation.name in {"WalkerDelta", "WalkerStar"}
    assert constellation.total_sats == constellation.num_planes * constellation.num_sat_per_plane
    assert constellation.num_planes >= 1 and constellation.num_sat_per_plane >= 1
    assert constellation.semi_major_axis_km > 0
    assert 0.0 <= constellation.inclination_deg <= 180.0
    assert settings.scenario.step_seconds > 0
    assert settings.scenario.start_dt < settings.scenario.stop_dt
    assert settings.run.mode in {"desktop", "engine"}
    assert settings.run.log_level in {"DEBUG", "INFO", "WARNING", "ERROR"}
    assert settings.visualization.link_width >= 1
    assert constellation.resolved_inter_plane_offset is not None


def test_altitude_becomes_semi_major_axis() -> None:
    settings = load_settings(write_config(
        "constellation:\n  altitude: 550.0\n  planes: 4\n  sats_per_plane: 4\n"
    ))
    assert abs(settings.constellation.semi_major_axis_km - (EARTH_RADIUS_KM + 550.0)) < 1e-9


def test_semi_major_axis_overrides_altitude() -> None:
    settings = load_settings(write_config(
        "constellation:\n  altitude: 550.0\n  semi_major_axis: 7000.0\n"
        "  planes: 4\n  sats_per_plane: 4\n"
    ))
    assert settings.constellation.semi_major_axis_km == 7000.0


def test_star_flag_flips_raan_spread() -> None:
    delta = load_settings(write_config("constellation:\n  planes: 4\n  sats_per_plane: 4\n"))
    star = load_settings(write_config(
        "constellation:\n  planes: 4\n  sats_per_plane: 4\n  star: true\n"
    ))
    assert delta.constellation.is_delta and delta.constellation.raan_spread_deg == 360.0
    assert not star.constellation.is_delta and star.constellation.raan_spread_deg == 180.0


def test_engine_mode_disables_graphics() -> None:
    desktop = load_settings(write_config("run:\n  mode: desktop\n"))
    engine = load_settings(write_config("run:\n  mode: engine\n"))
    assert desktop.run.show_graphics
    assert engine.run.use_engine and not engine.run.show_graphics


def test_visualization_defaults_off() -> None:
    settings = load_settings(write_config(""))
    assert not settings.visualization.enabled
    assert not settings.visualization.draw_links


def test_visualization_needs_both_switches() -> None:
    both = load_settings(write_config(
        "visualization:\n  enabled: true\n  links: true\n  link_width: 3\n"
    ))
    assert both.visualization.enabled and both.visualization.draw_links
    assert both.visualization.link_width == 3

    # Links alone draw nothing: nothing would keep STK around to look at them.
    links_only = load_settings(write_config("visualization:\n  links: true\n"))
    assert not links_only.visualization.draw_links

    # Keeping STK open without asking for lines is a valid, if plain, setup.
    hold_only = load_settings(write_config("visualization:\n  enabled: true\n"))
    assert hold_only.visualization.enabled and not hold_only.visualization.draw_links


def test_bad_link_width_rejected() -> None:
    expect_error("visualization:\n  link_width: 0\n", "must be >= 1")
    expect_error("visualization:\n  link_width: thick\n", "must be a number")


def test_inter_plane_offset_auto_or_pinned() -> None:
    auto = load_settings(write_config(
        "constellation:\n  planes: 20\n  sats_per_plane: 10\n  phasing: 45\n"
        "  inter_plane_offset: auto\n"
    ))
    assert auto.constellation.inter_plane_offset is None
    assert auto.constellation.resolved_inter_plane_offset == -3

    default = load_settings(write_config("constellation:\n  planes: 4\n  sats_per_plane: 4\n"))
    assert default.constellation.inter_plane_offset is None

    pinned = load_settings(write_config(
        "constellation:\n  planes: 20\n  sats_per_plane: 10\n  phasing: 45\n"
        "  inter_plane_offset: 0\n"
    ))
    assert pinned.constellation.resolved_inter_plane_offset == 0

    expect_error("constellation:\n  inter_plane_offset: nearest\n", "must be 'auto' or an integer")
    expect_error("constellation:\n  inter_plane_offset: 1.5\n", "must be 'auto' or an integer")


def test_unknown_section_rejected() -> None:
    expect_error("constelation:\n  planes: 4\n", "unknown section")


def test_unknown_key_rejected() -> None:
    # A typo here would otherwise silently build a different constellation.
    expect_error("constellation:\n  sats_per_plan: 22\n", "unknown key")
    expect_error("visualization:\n  line_visible: true\n", "unknown key")


def test_wrong_types_rejected() -> None:
    expect_error("constellation:\n  planes: \"many\"\n", "must be a number")
    expect_error("run:\n  progress: yes please\n", "must be true or false")
    expect_error("run:\n  mode: gui\n", "'mode' must be one of")
    expect_error("run:\n  log_level: LOUD\n", "'log_level' must be one of")


def test_bad_plane_count_rejected() -> None:
    # ConstellationConfig requires total_sats % num_planes == 0; 5 planes x 0 is invalid.
    expect_error("constellation:\n  planes: 0\n", "must be >= 1")


def test_missing_file_rejected() -> None:
    try:
        load_settings(PROJECT_ROOT / "does-not-exist.yaml")
    except ConfigError as error:
        assert "not found" in str(error)
    else:
        raise AssertionError("expected ConfigError for a missing file")


def test_empty_config_falls_back_to_defaults() -> None:
    settings = load_settings(write_config(""))
    assert settings.constellation.total_sats == 6 * 4
    assert settings.run.mode == "desktop"


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
