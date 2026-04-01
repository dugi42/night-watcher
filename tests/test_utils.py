from __future__ import annotations

import logging

from src import utils


def test_setup_logging_returns_project_logger() -> None:
    logger = utils.setup_logging()

    assert isinstance(logger, logging.Logger)
    assert logger.name == "night_watcher"
