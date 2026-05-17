"""Application logging with rotation and structured output."""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(data_dir: str, level: int = logging.INFO) -> logging.Logger:
    """Configure and return the application logger.

    Logs to both console (stderr) and a rotating file in data_dir.
    """
    logger = logging.getLogger("panupdate")
    logger.setLevel(level)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler with rotation (5 MB, 3 backups)
    log_path = os.path.join(data_dir, "panupdate.log")
    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    """Get the panupdate logger (must have called setup_logger first)."""
    return logging.getLogger("panupdate")
