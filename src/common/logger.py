"""Logging setup for the workflow."""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "hollywood_clips"


def get_logger(name: str = "workflow") -> logging.Logger:
    """Return a configured logger instance."""
    logger = logging.getLogger(f"{_LOGGER_NAME}.{name}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.propagate = False
    return logger
