"""Answer Workday custom application questions via cache + LLM."""

import json
from pathlib import Path
from typing import Any

from config.logger_config import logger
from src.llm.prompts import workday_questions_prompt
from src.utils.utils import append_yaml_file, load_yaml_file


class WorkdayQuestionHandler:
    def __init__(self, cache_path: Path, llm_answerer: Any) -> None:
        self.cache_path = cache_path
        self.llm_answerer = llm_answerer

    def answer(self, questions: list[dict], context: dict) -> dict[str, str]:
        """Return a map of question_text -> answer.

        Cache hits skip the LLM. Misses go to LLM; malformed JSON returns
        empty answers so the caller can decide whether to skip the question.
        """
        cache = load_yaml_file(self.cache_path) or {}
        answers: dict[str, str] = {}
        missing: list[dict] = []

        for q in questions:
            text = q.get("text", "").strip()
            if not text:
                continue
            cached = cache.get(text)
            if cached:
                answers[text] = str(cached)
            else:
                missing.append(q)

        if missing and self.llm_answerer is not None:
            try:
                prompt = workday_questions_prompt(missing, context)
                raw = self.llm_answerer.answer_question(prompt)
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    for q in missing:
                        text = q["text"]
                        if text in parsed:
                            answers[text] = str(parsed[text])
                            append_yaml_file(self.cache_path, {text: parsed[text]})
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning(f"Workday LLM answer parse failed: {e}")
            except Exception as e:
                logger.warning(f"Workday LLM call failed: {e}")

        return answers
