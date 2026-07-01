"""Per-question matcher for Greenhouse custom questions.

Mirrors the WorkdayQuestionHandler pattern but tailored for Greenhouse's
question shapes (typically yes/no, dropdown, free-text).
"""

from __future__ import annotations

from typing import Any, Optional


class GreenhouseQuestionHandler:
    def __init__(self, cache_path: Any = None, llm_answerer: Any = None):
        self.cache_path = cache_path
        self.llm_answerer = llm_answerer
        self._cache: dict[str, str] = {}

    def match_question(self, label: str) -> Optional[str]:
        """Return a canned answer for a Greenhouse question by label, or None."""
        norm = label.strip().lower()
        if norm in self._cache:
            return self._cache[norm]
        if "authorized" in norm or "eligible" in norm or "sponsorship" in norm:
            answer = "Yes"
        elif "relocate" in norm or "willing to" in norm:
            answer = "Yes"
        elif "experience" in norm or "years of" in norm:
            answer = "5+"
        elif "salary" in norm or "compensation" in norm:
            answer = ""
        else:
            answer = ""
        self._cache[norm] = answer
        return answer
