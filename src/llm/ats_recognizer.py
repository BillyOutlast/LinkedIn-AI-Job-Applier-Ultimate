"""Recognize known ATS destinations and route to dedicated handlers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from config.logger_config import logger


@dataclass(frozen=True)
class ATSMatch:
    name: str  # "workday", "greenhouse", ...
    confidence: float  # 0.0 - 1.0
    handler_factory: Callable  # () -> Handler class (caller instantiates with deps)


_KNOWN_ATS: dict[str, ATSMatch] = {
    "workday": ATSMatch(
        name="workday",
        confidence=1.0,
        handler_factory=lambda: _workday_handler_class,
    ),
    "greenhouse": ATSMatch(
        name="greenhouse",
        confidence=1.0,
        handler_factory=lambda: _greenhouse_handler_class,
    ),
    # Future per-ATS plans add entries here. Each new entry is one
    # line in this table — the rest of the dispatcher routes for free.
}


# Imported lazily inside _workday_handler_class to avoid a module-load cycle
def _workday_handler_class():
    from src.job_manager.workday.workday_applier import WorkdayApplier

    return WorkdayApplier


def _greenhouse_handler_class():
    from src.job_manager.greenhouse.greenhouse_applier import GreenhouseApplier

    return GreenhouseApplier


_HOSTNAME_PATTERNS = [
    (re.compile(r"\.myworkdayjobs\.com$", re.I), "workday"),
    (re.compile(r"(^|\.)boards?\.greenhouse\.io$", re.I), "greenhouse"),
    (re.compile(r"(^|\.)job-boards\.greenhouse\.io$", re.I), "greenhouse"),
]


def _fingerprint(url: str) -> dict:
    """Lightweight fingerprint: URL hostname. <2s, no DOM walk."""
    host = urlparse(url).netloc.lower()
    return {
        "url": url,
        "host": host,
        "hostnames": {host},
    }


def recognize(url: str) -> Optional[ATSMatch]:
    """Best-effort ATS recognition. Returns None on no match or any failure."""
    try:
        fp = _fingerprint(url)
        for pattern, name in _HOSTNAME_PATTERNS:
            if pattern.search(fp["host"]):
                if name in _KNOWN_ATS:
                    return _KNOWN_ATS[name]
        return None
    except Exception as e:
        logger.debug(f"recognize() failed (non-fatal): {e}")
        return None
