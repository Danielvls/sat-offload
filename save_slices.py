# -*- coding: utf-8 -*-
"""Write the time-slice dataset: one JSON network snapshot per sample step.

The *data production* entry point. It runs the same pipeline as ``main.py``
(via :func:`src.pipeline.build_network`), then writes each slice to its own file
and drops a ``manifest.json`` next to them describing the run.

Everything comes from ``config.yaml``; the only flag is ``--config``::

    ./.venv/Scripts/python.exe save_slices.py

Set ``run.mode: engine`` in the config for a headless run, or leave it on
``desktop`` to watch the per-plane colours while it works.

Output layout::

    data/slices/
        manifest.json      run description: scenario, constellation, counts
        slice00000.json    t = start
        slice00001.json    t = start + step
        ...

Each slice is NetworkX node-link JSON:

* ``graph["time"]`` -- the slice timestamp, ISO 8601;
* every node is a satellite, with ``plane`` and ``index`` (and ``lat``/``lon``
  when ``run.with_lla`` is on). Satellites with no visible ISL stay as
  degree-0 nodes;
* every edge is an ISL observed on that slice, with ``range`` (km) and ``kind``
  (``intra`` / ``inter``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.pipeline import build_network
from src.settings import DEFAULT_CONFIG_PATH, ConfigError, load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Save one JSON network snapshot per time slice.",
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
    logger = logging.getLogger("save_slices")
    logger.info("Config %s: %s", settings.source, settings.describe())

    out = settings.output.directory

    # Refuse to silently mix two runs in one directory.
    if out.exists() and not settings.output.overwrite:
        stale = sorted(out.glob("slice*.json"))
        if stale:
            logger.error(
                "%s already holds %d slice(s); set output.overwrite: true in %s to replace them",
                out, len(stale), settings.source,
            )
            return 2

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
            logger=logger,
        )

        stats = manager.link_statistics()
        written = manager.export_graphs(out)

        with (out / "manifest.json").open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)

        edges = stats["edges_per_slice_min"]
        if stats["edges_per_slice_max"] != edges:
            edges = f"{edges}-{stats['edges_per_slice_max']}"

        logger.info("Saved %d slices to %s", len(written), out)
        logger.info("  %s", manifest["constellation"]["walker"])
        logger.info("  %d satellites, %d ISL pairs, %s edges per slice",
                    stats["satellites"], stats["isl_pairs"], edges)
        logger.info("  manifest: %s", out / "manifest.json")
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130
    except Exception:
        logger.exception("Failed to save slices")
        return 1
    finally:
        if manager is not None:
            manager.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
