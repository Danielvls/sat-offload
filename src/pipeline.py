# -*- coding: utf-8 -*-
"""The build pipeline, shared by every CLI entry point.

Kept separate from the entry points so ``main.py`` (visual) and
``save_slices.py`` (data production) run exactly the same steps and cannot
drift apart.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.config import ConstellationConfig, ScenarioConfig
from src.stk.stk_manager import STKManager


def build_network(
    scenario: ScenarioConfig,
    constellation: ConstellationConfig,
    *,
    use_engine: bool = False,
    visible: bool = True,
    colors: bool = True,
    with_lla: bool = False,
    progress: bool = True,
    keep_access: bool = False,
    logger: Optional[logging.Logger] = None,
) -> STKManager:
    """Launch STK, build the constellation and its ISL network.

    Returns a **live** manager: the caller owns it and must call ``close()``.
    If any step fails the manager is closed before the exception propagates, so
    no STK process is left behind.
    """
    log = logger or logging.getLogger(__name__)
    manager = STKManager(
        scenario, constellation,
        use_engine=use_engine, visible=visible, progress=progress,
        keep_access=keep_access, logger=log,
    )

    try:
        manager.launch()
        manager.create_scenario()
        manager.create_constellation()

        if colors:
            manager.apply_plane_colors()

        manager.build_links()

        if with_lla:
            manager.add_satellite_lat_lon()
    except BaseException:
        manager.close()
        raise

    return manager
