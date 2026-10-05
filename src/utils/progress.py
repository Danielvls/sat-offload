# -*- coding: utf-8 -*-
"""tqdm bars, styled to match ``sat-net``'s green 30-column bars."""

from __future__ import annotations

from typing import Iterable, Optional, TypeVar

from tqdm import tqdm

T = TypeVar("T")

#: Same look as the reference project's managers.
BAR_FORMAT = "{l_bar}{bar:30}{r_bar}"
COLOUR = "green"


def make_bar(
    total: Optional[int],
    desc: str,
    *,
    unit: str = "it",
    disable: bool = False,
    **kwargs,
) -> tqdm:
    """A tqdm bar with the project's standard appearance."""
    return tqdm(
        total=total,
        desc=desc,
        unit=unit,
        colour=COLOUR,
        bar_format=BAR_FORMAT,
        mininterval=1.0,
        disable=disable,
        **kwargs,
    )


def track(
    iterable: Iterable[T],
    desc: str,
    *,
    unit: str = "it",
    disable: bool = False,
    total: Optional[int] = None,
    **kwargs,
) -> Iterable[T]:
    """``tqdm(iterable)`` with the project's standard appearance."""
    return tqdm(
        iterable,
        desc=desc,
        unit=unit,
        colour=COLOUR,
        bar_format=BAR_FORMAT,
        mininterval=1.0,
        disable=disable,
        total=total,
        **kwargs,
    )
