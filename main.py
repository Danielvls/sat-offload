# -*- coding: utf-8 -*-
"""Build a Walker-Delta constellation and its ISL network in STK.

The *visual* entry point -- it wants the STK GUI so the per-plane colours are
actually visible. To write slice data, use ``save_slices.py``.

With ``visualization.enabled`` (the default) the scenario is left loaded in STK
so you can orbit it, scrub the timeline and watch the ISL lines come and go;
``visualization.links`` draws those lines in the first place.

Everything comes from ``config.yaml``; the only flag is ``--config``::

    ./.venv/Scripts/python.exe main.py
    ./.venv/Scripts/python.exe main.py --config walker_1584.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.pipeline import build_network
from src.settings import DEFAULT_CONFIG_PATH, ConfigError, load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a Walker-Delta constellation and ISL network in STK.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="YAML run configuration")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = load_settings(args.config)
    except ConfigError as error:
        print(f"config error: {error}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.run.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("main")
    logger.info("Config %s: %s", settings.source, settings.describe())

    visualization = settings.visualization
    manager = None
    try:
        manager = build_network(
            settings.scenario,
            settings.constellation,
            use_engine=settings.run.use_engine,
            visible=settings.run.visible,
            colors=settings.run.colors,
            with_lla=settings.run.with_lla,
            progress=settings.run.progress,
            keep_access=visualization.draw_links,
            logger=logger,
        )
        logger.info("Network built: %s", manager.link_statistics())

        if visualization.draw_links:
            manager.show_links(visualization.link_width)

        if visualization.enabled:
            logger.info(
                "Scenario %r stays loaded in STK -- close it there when you are done "
                "(or set visualization.enabled: false in %s).",
                settings.scenario.name, settings.source,
            )
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130
    except Exception:
        logger.exception("Failed to build the network")
        return 1
    finally:
        if manager is not None:
            # Keep STK alive when the config asks for it; the scenario stays on
            # screen because StartApplication used userControl=True.
            manager.close(shutdown=not visualization.enabled)

    return 0


if __name__ == "__main__":
    sys.exit(main())
