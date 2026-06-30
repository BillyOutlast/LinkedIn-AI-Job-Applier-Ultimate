"""WorkdayQuestionHandler must hit cache before LLM and skip on malformed LLM output."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.job_manager.workday.workday_questions import WorkdayQuestionHandler


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    return tmp_path / "answers.yaml"


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.answer_question = MagicMock(return_value='{"Q1": "answer1"}')
    return llm


def test_cache_hit_skips_llm(tmp_cache: Path, mock_llm: MagicMock) -> None:
    tmp_cache.parent.mkdir(parents=True, exist_ok=True)
    tmp_cache.write_text('"Q1": "cached answer1"\n', encoding="utf-8")

    handler = WorkdayQuestionHandler(cache_path=tmp_cache, llm_answerer=mock_llm)
    questions = [{"text": "Q1", "type": "text", "required": True}]

    answers = handler.answer(questions, context={"resume": "", "job": {}})

    assert answers == {"Q1": "cached answer1"}
    mock_llm.answer_question.assert_not_called()


def test_llm_only_path_parses_json(tmp_cache: Path, mock_llm: MagicMock) -> None:
    tmp_cache.parent.mkdir(parents=True, exist_ok=True)
    tmp_cache.write_text("{}\n", encoding="utf-8")
    handler = WorkdayQuestionHandler(cache_path=tmp_cache, llm_answerer=mock_llm)
    questions = [{"text": "Q1", "type": "text", "required": True}]

    answers = handler.answer(questions, context={"resume": "x", "job": {}})

    assert answers == {"Q1": "answer1"}
    mock_llm.answer_question.assert_called_once()


def test_malformed_llm_returns_empty_answers(tmp_cache: Path) -> None:
    tmp_cache.parent.mkdir(parents=True, exist_ok=True)
    tmp_cache.write_text("{}\n", encoding="utf-8")
    bad_llm = MagicMock()
    bad_llm.answer_question = MagicMock(return_value="not json at all")
    handler = WorkdayQuestionHandler(cache_path=tmp_cache, llm_answerer=bad_llm)
    questions = [{"text": "Q1", "type": "text", "required": True}]

    answers = handler.answer(questions, context={"resume": "", "job": {}})

    assert answers == {}
