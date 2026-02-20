"""
utils.py — Shared utilities for AtmoTrack

Provides:
  - TTY-aware ANSI color constants (Fore, Style) replacing colorama
  - get_logger(): single point of logger configuration for all scripts
"""

import logging
import sys

# ---------------------------------------------------------------------------
# TTY detection — checked once at import time.
# Colors are suppressed automatically when stdout/stderr is not a terminal
# (e.g. SLURM batch jobs, log redirection).
# ---------------------------------------------------------------------------
_use_color: bool = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


class Fore:
    """ANSI foreground color codes (empty strings when not a TTY)."""

    GREEN = "\033[32m" if _use_color else ""
    YELLOW = "\033[33m" if _use_color else ""
    MAGENTA = "\033[35m" if _use_color else ""
    RED = "\033[31m" if _use_color else ""
    CYAN = "\033[36m" if _use_color else ""
    BLUE = "\033[34m" if _use_color else ""


class Style:
    """ANSI style codes (empty strings when not a TTY)."""

    BRIGHT = "\033[1m" if _use_color else ""
    RESET_ALL = "\033[0m" if _use_color else ""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_RESET = "\033[0m" if _use_color else ""


class _ColoredFormatter(logging.Formatter):
    """Formatter that appends ANSI reset after every record so that inline
    color codes in log messages (Fore.GREEN etc.) do not bleed into the next
    line.  No-op when not connected to a TTY."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return msg + _RESET if _use_color else msg


_FMT = "%(asctime)s %(levelname)-8s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def get_logger(
    name: str = "atmotrack",
    level: int = logging.INFO,
    log_file: str | None = None,
) -> logging.Logger:
    """Return (and configure, on first call) the named logger.

    Parameters
    ----------
    name:     Logger name — use the same name across a process to share state.
    level:    Minimum log level (e.g. logging.DEBUG, logging.INFO).
    log_file: Optional path for a plain-text (no ANSI) file handler.

    Example
    -------
    >>> from utils import get_logger
    >>> logger = get_logger("atmotrack", level=logging.DEBUG, log_file="out.log")
    >>> logger.info("Starting")
    """
    logger = logging.getLogger(name)
    if logger.handlers:  # already configured — idempotent
        return logger

    logger.setLevel(level)

    # Console handler — colored when TTY
    sh = logging.StreamHandler()
    sh.setFormatter(_ColoredFormatter(_FMT, datefmt=_DATEFMT))
    logger.addHandler(sh)

    # Optional file handler — always plain text
    if log_file is not None:
        fh = logging.FileHandler(log_file, mode="w")
        fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
        logger.addHandler(fh)

    return logger
