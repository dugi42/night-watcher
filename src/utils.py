"""Logging configuration for Night Watcher.

Call :func:`setup_logging` once at application startup to configure the root
``night_watcher`` logger. Child loggers (e.g. ``night_watcher.detector``) are
created per module and inherit this configuration automatically.
"""

import logging


def setup_logging() -> logging.Logger:
    """Configure and return the root Night Watcher logger.

    Sets the log level to INFO and uses a timestamp-prefixed format. Safe to
    call multiple times — :func:`logging.basicConfig` is a no-op after the
    first call.

    Returns
    -------
    logging.Logger
        The ``"night_watcher"`` root logger.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    return logging.getLogger("night_watcher")
