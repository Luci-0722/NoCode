"""Centralized logging configuration for nocode_agent."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_LOG_PATH = "nocode_agent/.state/nocode.log"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 3


def setup_logging(level: str | None = None, log_file: str | None = None) -> None:
    """Initialize the nocode_agent logging system.

    Log level is resolved from (in priority order):
      1. The *level* argument
      2. The ``NOCODE_LOG_LEVEL`` environment variable
      3. Default: ``INFO``

    Log file path is resolved from (in priority order):
      1. The *log_file* argument
      2. The ``NOCODE_LOG_FILE`` environment variable
      3. Default: ``nocode_agent/.state/nocode.log``

    Two handlers are attached:
      - ``StreamHandler`` → stderr (console output)
      - ``RotatingFileHandler`` → log file (10 MB, 3 backups)
    """
    resolved = (
        level
        or os.environ.get("NOCODE_LOG_LEVEL")
        or "INFO"
    ).upper()

    numeric = getattr(logging, resolved, logging.INFO)

    formatter = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATE_FORMAT)

    # stderr handler
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)

    # file handler (rotating, 10 MB × 3)
    resolved_path = log_file or os.environ.get("NOCODE_LOG_FILE") or _DEFAULT_LOG_PATH
    log_path = Path(resolved_path).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    pkg_logger = logging.getLogger("nocode_agent")
    pkg_logger.setLevel(numeric)
    # Remove any existing handlers to avoid duplicates on repeated calls
    pkg_logger.handlers.clear()
    pkg_logger.addHandler(stderr_handler)
    pkg_logger.addHandler(file_handler)
    # Prevent propagation to the root logger (avoids double output)
    pkg_logger.propagate = False
