"""
Aurora shared logging configuration.

Provides a consistent logging setup across all Aurora modules.

Usage:
    from runtime.shared.logging_config import setup_logging

    logger = setup_logging(__name__)
    logger.info("Ready")
"""

import logging
import sys

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_configured = False


def setup_logging(
    name: str = None,
    level: int = logging.INFO,
    fmt: str = _LOG_FORMAT,
) -> logging.Logger:
    """
    Return a logger, ensuring the root handler is configured exactly once.

    Args:
        name:  Logger name (typically ``__name__``).
        level: Minimum log level.
        fmt:   Log format string.

    Returns:
        A configured :class:`logging.Logger`.
    """
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(fmt))
        root = logging.getLogger()
        root.setLevel(level)
        # Avoid duplicate handlers if basicConfig was already called
        if not root.handlers:
            root.addHandler(handler)
        _configured = True

    return logging.getLogger(name)
