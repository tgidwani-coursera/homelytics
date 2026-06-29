"""Shared scraper utilities: logging, rate limiting, retry, and parsing helpers.

These are deliberately defensive. The Rajasthan RERA portal renders much of its
content via JS and its markup is not guaranteed stable, so parsing helpers here
return ``None`` rather than raising when a value is missing, and the navigation
helper retries with exponential backoff.
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import date, datetime
from logging.handlers import RotatingFileHandler

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("homelytics")


def configure_logging(log_file: str = "scraper.log", level: int = logging.INFO) -> None:
    """Structured logs to both console and a rotating file."""
    root = logging.getLogger("homelytics")
    if root.handlers:  # already configured
        return
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def rate_limit() -> None:
    """Sleep a random 2–3s (configurable) between requests to be polite."""
    lo = float(os.getenv("RATE_LIMIT_MIN", "2.0"))
    hi = float(os.getenv("RATE_LIMIT_MAX", "3.0"))
    delay = random.uniform(lo, hi)
    logger.debug("Rate-limit sleep %.2fs", delay)
    time.sleep(delay)


# Retry decorator: 3 attempts, exponential backoff (2s, 4s, 8s capped at 30s).
with_retries = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


# --------------------------------------------------------------------------- #
# Parsing helpers — all tolerant of missing / messy input.
# --------------------------------------------------------------------------- #

def clean(text: object | None) -> str | None:
    """Collapse whitespace; return None for empty/placeholder values.

    Accepts any type (JSON values may be numbers/bools); non-strings are
    coerced to ``str`` first.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    value = re.sub(r"\s+", " ", text).strip()
    if not value or value in {"-", "--", "N/A", "NA", "NIL"}:
        return None
    return value


def to_int(value: object | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    cleaned = clean(value)
    if cleaned is None:
        return None
    match = re.search(r"-?\d[\d,]*", cleaned)
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def to_float(value: object | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = clean(value)
    if cleaned is None:
        return None
    match = re.search(r"-?\d[\d,]*\.?\d*", cleaned)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


_DATE_FORMATS = (
    "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y",
    "%d.%m.%Y", "%m/%d/%Y", "%d-%B-%Y",
)


def to_date(text: str | None) -> date | None:
    cleaned = clean(text)
    if cleaned is None:
        return None
    # Strip a trailing time component if present (handles "2026-04-28T08:35:18").
    cleaned = re.split(r"[ T]", cleaned)[0] if re.search(r"\d{2}:\d{2}", cleaned) else cleaned
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    logger.debug("Unparseable date: %r", text)
    return None
