"""Minimal logging setup — one line per request, no scenario payloads."""

from __future__ import annotations

import logging

from app.core.config import get_settings

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    logging.basicConfig(
        level=getattr(logging, get_settings().log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
        datefmt="%H:%M:%S",
    )
    _CONFIGURED = True
