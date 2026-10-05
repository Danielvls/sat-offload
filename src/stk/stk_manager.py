# -*- coding: utf-8 -*-
"""Build a Walker-Delta constellation and its ISL network in STK.

Adapted from ``sat-net/src/stk/stk_manager.py``. The scenario flow is the same
-- scenario, two-body satellites, STK access per satellite pair, one NetworkX
graph per time slice -- with three changes worth knowing about:

1.  **Graphics need a real STK application.** STK Engine started with
    ``noGraphics=True`` raises ``STKRuntimeError: NoGraphics property is set to
    true`` on *any* ``Graphics`` / ``VO`` touch, so ``apply_plane_colors`` is
    skipped (loudly) in engine mode. Run with STK Desktop to see colours.

2.  **Per-plane colour does not take a bare system name.**
    ``sat.VO.OrbitSystems.Add`` wants a fully qualified name such as
    ``CentralBody/Earth Inertial System``, and the element inherits its colour
    until ``Inherit`` is cleared::

        element = sat.VO.OrbitSystems.Add("CentralBody/Earth Inertial System")
        element.Inherit = False
        element.Color = Colors.FromRGB(r, g, b)

    Setting ``Color`` first fails with ``Cannot modify read-only attribute``.

3.  **No pandas / tqdm.** Time slices come from :class:`~src.utils.timegrid.TimeGrid`
    and progress goes through :mod:`logging`.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Optional

import networkx as nx

from agi.stk12.stkobjects import (
    AgEClassicalLocation,
    AgEClassicalSizeShape,
    AgEOrientationAscNode,
    AgESTKObjectType,
    AgEVeGfxAttributes,
    AgEVePropagatorType,
)
from agi.stk12.stkutil import AgEOrbitStateType
from agi.stk12.utilities.colors import Colors

from src.config import ConstellationConfig, ScenarioConfig
from src.topology import (
    ISLLink,
    inter_plane_worst_chord_km,
    isl_pairs,
    line_of_sight_limit_km,
)
from src.utils.palette import distinct_rgb_colors
from src.utils.progress import make_bar
from src.utils.timegrid import TimeGrid

#: Fully qualified name of the 3D reference system whose colour drives the orbit
#: track. ``OrbitSystems.Add`` rejects the bare ``"Inertial"`` spelling.
DEFAULT_ORBIT_SYSTEM = "CentralBody/Earth Inertial System"


class STKManager:
    """Owns one STK application, one scenario, and the derived network."""

    def __init__(
        self,
        scenario: ScenarioConfig,
        constellation: ConstellationConfig,
        *,
        use_engine: bool = False,
        visible: bool = True,
        progress: bool = True,
        keep_access: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.scenario_config = scenario
        self.constellation_config = constellation
        self.use_engine = use_engine
        self.visible = visible
        self.show_progress = progress
        #: Hold on to each access object so ``show_links`` can draw it later.
        self.keep_access = keep_access
        self.logger = logger or logging.getLogger(__name__)

        self.stk = None
        self.stk_root = None
        self.scenario = None
        self.constellation = None

        self.satellites: list = []
        self.satellites_by_name: dict[str, object] = {}
        self.links: list[ISLLink] = []
        self.graph_list: list[nx.Graph] = []
        #: ``(link, access)`` pairs, kept only when ``keep_access`` is set.
        self.access_list: list[tuple[ISLLink, object]] = []

        #: False under STK Engine; every graphics call must be guarded by it.
        self.graphics_available = not use_engine

        self.grid = TimeGrid(
            scenario.start_dt, scenario.stop_dt, scenario.step_seconds
        )

    # ------------------------------------------------------------------ setup

    def launch(self) -> None:
        """Start STK Engine (headless) or STK Desktop (for graphics)."""
        if self.use_engine:
            from agi.stk12.stkengine import STKEngine

            self.logger.info("Launching STK Engine (noGraphics=True)...")
            self.stk = STKEngine.StartApplication(noGraphics=True)
            self.stk_root = self.stk.NewObjectRoot()
        else:
            from agi.stk12.stkdesktop import STKDesktop

            self.logger.info("Launching STK Desktop (visible=%s)...", self.visible)
            self.stk = STKDesktop.StartApplication(visible=self.visible, userControl=True)
            self.stk_root = self.stk.Root

        # Make every timestamp STK hands back parseable by parse_stk_time().
        self.stk_root.UnitPreferences.SetCurrentUnit("DateFormat", "UTCG")

    def create_scenario(self) -> None:
        """Create a fresh scenario and the per-slice graph containers."""
        if self.stk_root.CurrentScenario:
            self.stk_root.CloseScenario()

        self.logger.info("Creating scenario %r", self.scenario_config.name)
        self.stk_root.NewScenario(self.scenario_config.name)
        self.scenario = self.stk_root.CurrentScenario
        self.scenario.SetTimePeriod(self.scenario_config.start, self.scenario_config.stop)

        self.graph_list = []
        for moment in self.grid:
            graph = nx.Graph()
            graph.graph["time"] = moment.isoformat()
            self.graph_list.append(graph)

        if self.graphics_available:
            self.stk_root.Rewind()

    # ---------------------------------------------------------- constellation

    def _build_satellite(self, name: str, raan_deg: float, true_anomaly_deg: float):
        """Create one satellite from classical elements and propagate it."""
        config = self.constellation_config
        satellite = self.scenario.Children.New(AgESTKObjectType.eSatellite, name)
        satellite.SetPropagatorType(AgEVePropagatorType.ePropagatorTwoBody)

        propagator = satellite.Propagator
        keplerian = propagator.InitialState.Representation.ConvertTo(
            AgEOrbitStateType.eOrbitStateClassical
        )
        keplerian.SizeShapeType = AgEClassicalSizeShape.eSizeShapeSemimajorAxis
        keplerian.Orientation.AscNodeType = AgEOrientationAscNode.eAscNodeRAAN
        keplerian.LocationType = AgEClassicalLocation.eLocationTrueAnomaly

        keplerian.SizeShape.SemiMajorAxis = config.semi_major_axis_km
        keplerian.SizeShape.Eccentricity = config.eccentricity
        keplerian.Orientation.Inclination = config.inclination_deg
        keplerian.Orientation.ArgOfPerigee = config.arg_perigee_deg
        keplerian.Orientation.AscNode.Value = raan_deg
        keplerian.Location.Value = true_anomaly_deg

        propagator.InitialState.Representation.Assign(keplerian)
        propagator.Propagate()
        return satellite

    def create_constellation(self) -> list:
        """Create the Walker Delta constellation and its STK constellation object."""
        config = self.constellation_config
        self.logger.info("Creating %s", config.describe())

        self.constellation = self.scenario.Children.New(
            AgESTKObjectType.eConstellation, config.name
        )

        total = config.total_sats
        planes = config.num_planes
        sats_per_plane = config.num_sat_per_plane

        bar = make_bar(total, "Creating constellation", unit="sat",
                       disable=not self.show_progress)
        self.stk_root.BeginUpdate()
        try:
            for plane in range(1, planes + 1):
                raan = config.plane_raan_deg(plane)
                # refresh=False: set_postfix_str refreshes by default, which would
                # defeat mininterval and redraw the bar on every single iteration.
                bar.set_postfix_str(f"Plane {plane}/{planes}", refresh=False)
                for sat in range(1, sats_per_plane + 1):
                    name = config.satellite_name(plane, sat)
                    satellite = self._build_satellite(
                        name, raan, config.true_anomaly_deg(plane, sat)
                    )

                    self.satellites.append(satellite)
                    self.satellites_by_name[name] = satellite
                    self.constellation.Objects.AddObject(satellite)

                    # Every time slice knows the full node set up front, so an
                    # isolated satellite still appears (as a degree-0 node).
                    for graph in self.graph_list:
                        graph.add_node(name, plane=plane, index=sat)

                    bar.update(1)
        finally:
            self.stk_root.EndUpdate()
            bar.close()

        self.logger.info("Created %d satellites in %d planes",
                         len(self.satellites), config.num_planes)
        return self.satellites

    # --------------------------------------------------------------- graphics

    def apply_plane_colors(self, orbit_system: str = DEFAULT_ORBIT_SYSTEM) -> bool:
        """Give each orbital plane its own colour, in both 2D and 3D graphics.

        Returns ``False`` when graphics are unavailable (STK Engine) instead of
        raising, so a headless run can still produce the network.
        """
        if not self.graphics_available:
            self.logger.warning(
                "Skipping plane colours: %s mode cannot touch Graphics.",
                "STK Engine" if self.use_engine else "current",
            )
            return False

        config = self.constellation_config
        palette = distinct_rgb_colors(config.num_planes)

        self.stk_root.BeginUpdate()
        try:
            for plane, rgb in enumerate(palette, start=1):
                color = Colors.FromRGB(*rgb)
                for sat in range(1, config.num_sat_per_plane + 1):
                    satellite = self.satellites_by_name[
                        config.satellite_name(plane, sat)
                    ]

                    # 2D attributes must be in "basic" mode before Color applies.
                    satellite.Graphics.SetAttributesType(AgEVeGfxAttributes.eAttributesBasic)
                    satellite.Graphics.Attributes.Color = color
                    satellite.Graphics.UseInstNameLabel = True

                    # 3D orbit track. Inherit must be cleared first, otherwise
                    # STK reports the Color property as read-only.
                    element = satellite.VO.OrbitSystems.Add(orbit_system)
                    element.Inherit = False
                    element.Color = color
                    element.IsVisible = True
                    # An inertial reference system is drawn as a track behind the
                    # satellite; persist it so the whole orbit ring is visible.
                    element.PersistForAllPasses = True
        finally:
            self.stk_root.EndUpdate()

        self.logger.info("Coloured %d planes (%s)", config.num_planes, orbit_system)
        return True

    # ------------------------------------------------------------- link build

    def build_links(self) -> list[ISLLink]:
        """Compute STK access for every ISL and fill the per-slice graphs."""
        self.links = isl_pairs(self.constellation_config)
        self.logger.info(
            "Computing %d ISLs (%d intra, %d inter) over %d time slices",
            len(self.links),
            sum(1 for link in self.links if link.kind == "intra"),
            sum(1 for link in self.links if link.kind == "inter"),
            len(self.grid),
        )

        self._warn_if_links_unreachable()

        total = len(self.links)
        linked = 0  # pairs with at least one slice in line of sight
        samples = 0  # total node-time observations written

        bar = make_bar(total, "Computing access", unit="link",
                       disable=not self.show_progress)
        try:
            for link in self.links:
                found = self._compute_access(link)
                samples += found
                linked += 1 if found else 0
                bar.set_postfix_str(f"{link.kind} | linked {linked}", refresh=False)
                bar.update(1)
        finally:
            bar.close()

        self.logger.info(
            "%d/%d ISLs were in line of sight at least once (%d samples total)",
            linked, total, samples,
        )
        return self.links

    def _warn_if_links_unreachable(self) -> None:
        """Say so up front when the constellation geometry cannot support ISLs.

        Cheaper than waiting out a long access run only to find zero edges, and
        the usual cause -- a phasing factor that leaves no inter-plane neighbour
        within line of sight -- is invisible from the config alone.
        """
        config = self.constellation_config
        limit = line_of_sight_limit_km(config.semi_major_axis_km)

        worst_intra = 2.0 * config.semi_major_axis_km * math.sin(
            math.radians(180.0 / config.num_sat_per_plane)
        )
        if worst_intra > limit:
            self.logger.warning(
                "Same-plane neighbours are %.0f km apart but line of sight caps at "
                "%.0f km: NO intra-plane links will form. Increase sats_per_plane.",
                worst_intra, limit,
            )

        if config.num_planes < 2:
            return

        offset = config.resolved_inter_plane_offset
        worst_inter = inter_plane_worst_chord_km(config, offset)
        if worst_inter > limit:
            self.logger.warning(
                "Cross-plane links (index offset %+d) reach %.0f km but line of sight "
                "caps at %.0f km: some or all inter-plane links will never form. "
                "Adjust phasing/planes, or set constellation.inter_plane_offset.",
                offset, worst_inter, limit,
            )

        if offset != 0:
            self.logger.info(
                "Cross-plane neighbour is at in-plane index offset %+d "
                "(phase step %.1f deg vs in-plane spacing %.1f deg), not the same index.",
                offset, config.phase_step_deg, 360.0 / config.num_sat_per_plane,
            )

    def _compute_access(self, link: ISLLink) -> int:
        """Sample range for one pair and stamp it onto the matching slices.

        Returns the number of samples that landed on the time grid -- zero when
        the pair is never in line of sight over the scenario window.
        """
        source = self.satellites_by_name[link.source]
        target = self.satellites_by_name[link.target]

        access = source.GetAccessToObject(target)
        access.ComputeAccess()

        if self.keep_access:
            self.access_list.append((link, access))

        provider = access.DataProviders.GetDataPrvTimeVarFromPath("AER Data/Default")
        result = provider.ExecElements(
            self.scenario.StartTime,
            self.scenario.StopTime,
            self.scenario_config.step_seconds,
            ["Time", "Range"],
        )

        # STK returns one dataset per contiguous access interval.
        samples = 0
        for interval_index in range(result.Intervals.Count):
            interval = result.Intervals[interval_index]
            times = interval.DataSets.GetDataSetByName("Time").GetValues()
            ranges = interval.DataSets.GetDataSetByName("Range").GetValues()

            for moment, distance in zip(times, ranges):
                slice_index = self.grid.index_of_stk_time(moment)
                if slice_index is None:
                    continue
                self.graph_list[slice_index].add_edge(
                    link.source, link.target, range=distance, kind=link.kind
                )
                samples += 1

        return samples

    # ------------------------------------------------------------- attributes

    def add_satellite_lat_lon(self) -> None:
        """Tag every node with its latitude/longitude on each time slice.

        Mirrors ``get_sat_lla`` in the reference project, but resolves the node
        index directly instead of building a name->index map per slice.
        """
        names = list(self.satellites_by_name)
        bar = make_bar(len(names), "Reading LLA state", unit="sat",
                       disable=not self.show_progress)

        self.stk_root.BeginUpdate()
        try:
            for name in names:
                satellite = self.satellites_by_name[name]
                provider = satellite.DataProviders.GetDataPrvTimeVarFromPath(
                    "LLA State/TrueOfDateRotating"
                )
                result = provider.ExecElements(
                    self.scenario.StartTime,
                    self.scenario.StopTime,
                    self.scenario_config.step_seconds,
                    ["Time", "Lat", "Lon"],
                )
                times = result.DataSets.GetDataSetByName("Time").GetValues()
                lats = result.DataSets.GetDataSetByName("Lat").GetValues()
                lons = result.DataSets.GetDataSetByName("Lon").GetValues()

                for moment, lat, lon in zip(times, lats, lons):
                    slice_index = self.grid.index_of_stk_time(moment)
                    if slice_index is None:
                        continue
                    self.graph_list[slice_index].nodes[name]["lat"] = lat
                    self.graph_list[slice_index].nodes[name]["lon"] = lon
                bar.update(1)
        finally:
            self.stk_root.EndUpdate()
            bar.close()

        self.logger.info("Added lat/lon to %d slices", len(self.graph_list))

    # ---------------------------------------------------------------- outputs

    def slice_filename(self, index: int) -> str:
        """Zero-padded so the files sort in time order in any file browser."""
        return f"slice{index:05d}.json"

    def export_graphs(self, directory: Path) -> list[Path]:
        """Write one JSON graph per time slice, plus a ``manifest.json``.

        Note the ``edges="edges"`` keyword: NetworkX 3.7 renamed the old
        ``edges="links"`` spelling used by the reference project.
        """
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        bar = make_bar(len(self.graph_list), "Saving slices", unit="slice",
                       disable=not self.show_progress)
        written: list[Path] = []
        try:
            for index, graph in enumerate(self.graph_list):
                path = directory / self.slice_filename(index)
                with path.open("w", encoding="utf-8") as handle:
                    json.dump(nx.node_link_data(graph, edges="edges"), handle, indent=2)
                written.append(path)
                bar.update(1)
        finally:
            bar.close()

        self.write_manifest(directory, written)
        self.logger.info("Wrote %d slices to %s", len(written), directory)
        return written

    def write_manifest(self, directory: Path, written: list[Path] | None = None) -> Path:
        """Describe the run next to the slices, so the dump is self-contained."""
        directory = Path(directory)
        scenario = self.scenario_config
        constellation = self.constellation_config

        manifest = {
            "scenario": {
                "name": scenario.name,
                "start": scenario.start,
                "stop": scenario.stop,
                "step_seconds": scenario.step_seconds,
            },
            "constellation": {
                "walker": constellation.describe(),
                "type": "delta" if constellation.is_delta else "star",
                "total_sats": constellation.total_sats,
                "num_planes": constellation.num_planes,
                "phasing": constellation.phasing,
                "sats_per_plane": constellation.num_sat_per_plane,
                "semi_major_axis_km": constellation.semi_major_axis_km,
                "inclination_deg": constellation.inclination_deg,
                "eccentricity": constellation.eccentricity,
                "phase_step_deg": constellation.phase_step_deg,
                "inter_plane_offset": constellation.resolved_inter_plane_offset,
            },
            "network": self.link_statistics(),
            "edge_attributes": {
                "range": "line-of-sight slant range, km",
                "kind": "intra | inter",
            },
            "slices": [path.name for path in (written or [])],
        }

        path = directory / "manifest.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        return path

    def link_statistics(self) -> dict:
        """Cheap summary of the built network, useful as a smoke test."""
        slices = len(self.graph_list)
        edge_counts = [graph.number_of_edges() for graph in self.graph_list]
        node_counts = [graph.number_of_nodes() for graph in self.graph_list]
        return {
            "satellites": len(self.satellites),
            "planes": self.constellation_config.num_planes,
            "isl_pairs": len(self.links),
            "slices": slices,
            "nodes_per_slice": node_counts[0] if node_counts else 0,
            "edges_per_slice_min": min(edge_counts) if edge_counts else 0,
            "edges_per_slice_max": max(edge_counts) if edge_counts else 0,
        }

    # --------------------------------------------------------- visualization

    def show_links(self, line_width: int = 2) -> int:
        """Draw the computed ISLs in the 2D/3D windows, on their access intervals.

        Each access object carries its own graphics; ``LineVisible`` is what puts
        the line on screen. As with the orbit system, ``Inherit`` must be cleared
        first or STK rejects the write.

        Returns the number of links drawn (0 if graphics are unavailable).
        """
        if not self.graphics_available:
            self.logger.warning(
                "Cannot draw links: %s mode has no Graphics.",
                "STK Engine" if self.use_engine else "current",
            )
            return 0

        if not self.access_list:
            self.logger.warning(
                "No access objects retained -- build with keep_access=True to draw links."
            )
            return 0

        bar = make_bar(len(self.access_list), "Drawing ISL links", unit="link",
                       disable=not self.show_progress)
        drawn = 0
        # Batch the graphics updates: without this STK repaints after every
        # property write and the loop is ~4x slower.
        self.stk_root.BeginUpdate()
        try:
            for link, access in self.access_list:
                graphics = access.Graphics
                graphics.Inherit = False
                graphics.LineVisible = True
                graphics.LineWidth = line_width
                drawn += 1
                bar.set_postfix_str(link.kind, refresh=False)
                bar.update(1)
        finally:
            self.stk_root.EndUpdate()
            bar.close()

        self.logger.info("Drew %d ISL links at width %d", drawn, line_width)
        return drawn

    def close(self, shutdown: bool = True) -> None:
        """Close the scenario and shut STK down -- unless ``shutdown`` is False.

        ``ShutDown()`` is the wrapper's own lifecycle call on both the Desktop and
        Engine application objects -- it closes the current scenario for you.
        Calling the raw COM ``Terminate()``/``Quit()`` instead tends to leave the
        Python wrappers pointing at freed COM objects (access violation at exit).

        With ``shutdown=False`` we only drop our references. STK Desktop was
        started with ``userControl=True``, which hands the application back to
        the user when the Python connection ends, so the window stays up with the
        scenario loaded.
        """
        if self.stk is None:
            return

        if shutdown:
            try:
                self.stk.ShutDown()
            except Exception:  # pragma: no cover - best-effort teardown
                self.logger.exception("STK shutdown failed")
            self.logger.info("STK closed")
        else:
            self.logger.info("Leaving STK open for inspection")

        self.stk = None
        self.stk_root = None
        self.scenario = None


__all__ = ["STKManager", "DEFAULT_ORBIT_SYSTEM"]
