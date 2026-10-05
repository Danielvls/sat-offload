# -*- coding: utf-8 -*-
"""Load the YAML run configuration.

The config file is the single source of truth -- neither entry point takes
individual flags, only ``--config``. Loading is **strict**: an unknown section
or key is an error rather than a silently ignored typo, because a mistyped
``sats_per_plane`` would otherwise quietly build a different constellation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from src.config import ConstellationConfig, ScenarioConfig

#: Earth equatorial radius, WGS-84 (km). Used only to turn ``altitude`` into a.
EARTH_RADIUS_KM = 6378.137

DEFAULT_CONFIG_PATH = Path("config.yaml")

_SCENARIO_KEYS = {"name", "start", "stop", "step"}
_CONSTELLATION_KEYS = {
    "planes", "sats_per_plane", "phasing", "inclination",
    "altitude", "semi_major_axis", "eccentricity", "star",
    "inter_plane_offset",
}
_OUTPUT_KEYS = {"directory", "overwrite"}
_RUN_KEYS = {"mode", "visible", "colors", "with_lla", "progress", "log_level"}
_VISUALIZATION_KEYS = {"enabled", "links", "link_width"}

_SECTIONS = {
    "scenario": _SCENARIO_KEYS,
    "constellation": _CONSTELLATION_KEYS,
    "output": _OUTPUT_KEYS,
    "run": _RUN_KEYS,
    "visualization": _VISUALIZATION_KEYS,
}

_MODES = {"desktop", "engine"}
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


class ConfigError(Exception):
    """Raised for a missing file, malformed YAML, or an invalid key."""


@dataclass(frozen=True)
class OutputConfig:
    directory: Path = Path("data/slices")
    overwrite: bool = False


@dataclass(frozen=True)
class RunConfig:
    """How to drive STK. ``engine`` mode is headless and cannot draw graphics."""

    mode: str = "desktop"
    visible: bool = True
    colors: bool = True
    with_lla: bool = False
    progress: bool = True
    log_level: str = "INFO"

    @property
    def use_engine(self) -> bool:
        return self.mode == "engine"

    @property
    def show_graphics(self) -> bool:
        """Colours are only possible outside engine mode."""
        return self.colors and not self.use_engine


@dataclass(frozen=True)
class VisualizationConfig:
    """What to leave behind in the STK GUI after ``main.py`` builds the network.

    STK Desktop started with ``userControl=True`` outlives the Python process, so
    with ``enabled`` the scenario simply stays on screen instead of being shut
    down the moment the script ends.
    """

    enabled: bool = False
    links: bool = False
    link_width: int = 2

    @property
    def draw_links(self) -> bool:
        return self.enabled and self.links


@dataclass(frozen=True)
class Settings:
    scenario: ScenarioConfig
    constellation: ConstellationConfig
    output: OutputConfig
    run: RunConfig
    visualization: VisualizationConfig
    source: Path

    def describe(self) -> str:
        target = "STK Engine (headless)" if self.run.use_engine else "STK Desktop"
        return f"{self.constellation.describe()} | {target}"


def _check_sections(data: Mapping[str, Any], source: Path) -> None:
    unknown = set(data) - set(_SECTIONS)
    if unknown:
        raise ConfigError(
            f"{source}: unknown section(s): {', '.join(sorted(unknown))}. "
            f"Known sections: {', '.join(sorted(_SECTIONS))}"
        )


def _section(data: Mapping[str, Any], name: str, source: Path) -> Mapping[str, Any]:
    section = data.get(name)
    if section is None:
        return {}
    if not isinstance(section, Mapping):
        raise ConfigError(f"{source}: [{name}] must be a mapping, got {type(section).__name__}")
    unknown = set(section) - _SECTIONS[name]
    if unknown:
        raise ConfigError(
            f"{source}: unknown key(s) in [{name}]: {', '.join(sorted(unknown))}. "
            f"Known keys: {', '.join(sorted(_SECTIONS[name]))}"
        )
    return section


def _number(section: Mapping[str, Any], key: str, where: str, default: Any = None):
    if key not in section:
        return default
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: '{key}' must be a number, got {value!r}")
    return value


def _flag(section: Mapping[str, Any], key: str, where: str, default: bool) -> bool:
    if key not in section:
        return default
    value = section[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: '{key}' must be true or false, got {value!r}")
    return value


def _text(section: Mapping[str, Any], key: str, where: str, default: str) -> str:
    if key not in section:
        return default
    value = section[key]
    if not isinstance(value, str):
        raise ConfigError(f"{where}: '{key}' must be a string, got {value!r}")
    return value


def _build_scenario(section: Mapping[str, Any], source: Path) -> ScenarioConfig:
    where = f"{source} [scenario]"
    return ScenarioConfig(
        name=_text(section, "name", where, "sat-offload"),
        start=_text(section, "start", where, "1 Oct 2026 00:00:00.000"),
        stop=_text(section, "stop", where, "1 Oct 2026 02:00:00.000"),
        step_seconds=float(_number(section, "step", where, 60.0)),
    )


def _build_constellation(section: Mapping[str, Any], source: Path) -> ConstellationConfig:
    where = f"{source} [constellation]"

    planes = _number(section, "planes", where, 6)
    sats_per_plane = _number(section, "sats_per_plane", where, 4)
    if planes is None or planes < 1:
        raise ConfigError(f"{where}: 'planes' must be >= 1, got {planes!r}")
    if sats_per_plane is None or sats_per_plane < 1:
        raise ConfigError(f"{where}: 'sats_per_plane' must be >= 1, got {sats_per_plane!r}")
    planes, sats_per_plane = int(planes), int(sats_per_plane)

    # semi_major_axis wins over altitude when both are given.
    semi_major_axis = _number(section, "semi_major_axis", where)
    if semi_major_axis is None:
        altitude = _number(section, "altitude", where, 550.0)
        semi_major_axis = EARTH_RADIUS_KM + altitude

    is_delta = not _flag(section, "star", where, False)
    offset = _inter_plane_offset(section, where)

    return ConstellationConfig(
        name="WalkerDelta" if is_delta else "WalkerStar",
        total_sats=planes * sats_per_plane,
        num_planes=planes,
        phasing=int(_number(section, "phasing", where, 1)),
        semi_major_axis_km=float(semi_major_axis),
        inclination_deg=float(_number(section, "inclination", where, 53.0)),
        eccentricity=float(_number(section, "eccentricity", where, 0.0)),
        is_delta=is_delta,
        inter_plane_offset=offset,
    )


def _inter_plane_offset(section: Mapping[str, Any], where: str) -> int | None:
    """``"auto"`` (the default) means derive it from the orbit geometry."""
    if "inter_plane_offset" not in section:
        return None
    value = section["inter_plane_offset"]
    if isinstance(value, str):
        if value.strip().lower() != "auto":
            raise ConfigError(
                f"{where}: 'inter_plane_offset' must be 'auto' or an integer, got {value!r}"
            )
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"{where}: 'inter_plane_offset' must be 'auto' or an integer, got {value!r}"
        )
    return value


def _build_output(section: Mapping[str, Any], source: Path) -> OutputConfig:
    where = f"{source} [output]"
    directory = _text(section, "directory", where, "data/slices")
    return OutputConfig(
        directory=Path(directory),
        overwrite=_flag(section, "overwrite", where, False),
    )


def _build_run(section: Mapping[str, Any], source: Path) -> RunConfig:
    where = f"{source} [run]"
    mode = _text(section, "mode", where, "desktop").lower()
    if mode not in _MODES:
        raise ConfigError(f"{where}: 'mode' must be one of {sorted(_MODES)}, got {mode!r}")

    log_level = _text(section, "log_level", where, "INFO").upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(
            f"{where}: 'log_level' must be one of {sorted(_LOG_LEVELS)}, got {log_level!r}"
        )

    return RunConfig(
        mode=mode,
        visible=_flag(section, "visible", where, True),
        colors=_flag(section, "colors", where, True),
        with_lla=_flag(section, "with_lla", where, False),
        progress=_flag(section, "progress", where, True),
        log_level=log_level,
    )


def _build_visualization(section: Mapping[str, Any], source: Path) -> VisualizationConfig:
    where = f"{source} [visualization]"
    width = _number(section, "link_width", where, 2)
    if width is None or width < 1:
        raise ConfigError(f"{where}: 'link_width' must be >= 1, got {width!r}")
    return VisualizationConfig(
        enabled=_flag(section, "enabled", where, False),
        links=_flag(section, "links", where, False),
        link_width=int(width),
    )


def load_settings(path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    """Read and validate a YAML config file."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"{path}: invalid YAML: {error}") from error

    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path}: top level must be a mapping of sections")

    _check_sections(raw, path)

    scenario = _build_scenario(_section(raw, "scenario", path), path)
    constellation = _build_constellation(_section(raw, "constellation", path), path)
    output = _build_output(_section(raw, "output", path), path)
    run = _build_run(_section(raw, "run", path), path)
    visualization = _build_visualization(_section(raw, "visualization", path), path)

    return Settings(
        scenario=scenario, constellation=constellation,
        output=output, run=run, visualization=visualization, source=path,
    )


__all__ = [
    "ConfigError", "OutputConfig", "RunConfig", "Settings", "VisualizationConfig",
    "load_settings", "DEFAULT_CONFIG_PATH", "EARTH_RADIUS_KM",
]
