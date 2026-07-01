"""Per-question matcher for Taleo custom questions.

Mirrors the GreenhouseQuestionHandler pattern: heuristic answers for
common question shapes (authorized/sponsorship, experience, salary).
Returns None for unmatched — handler skips the field.
"""

from __future__ import annotations

from typing import Any, Optional


class TaleoQuestionHandler:
    def __init__(self, cache_path: Any = None, llm_answerer: Any = None):
        self.cache_path = cache_path
        self.llm_answerer = llm_answerer
        self._cache: dict[str, str] = {}

    def match_question(self, label: str) -> Optional[str]:
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
