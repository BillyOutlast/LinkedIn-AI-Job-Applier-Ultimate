"""WorkdayApplier must dispatch through phases and return ApplyAgent-shaped tuples."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.job_manager.workday.workday_applier import WorkdayApplier


@pytest.fixture
def deps() -> WorkdayApplier:
    return WorkdayApplier(
        page=MagicMock(),
        resume_structured={"work_experience": [], "education": []},
        resume_pdf_path=Path("dummy.pdf"),
        question_handler=MagicMock(answer=MagicMock(return_value={})),
        authenticator=MagicMock(ensure_session=AsyncMock(return_value=True)),
        headless=True,
    )


@pytest.mark.asyncio
async def test_apply_to_job_returns_success_tuple(deps: WorkdayApplier) -> None:
    deps._navigate_to_apply = AsyncMock()  # type: ignore[method-assign]
    deps._fill_account_if_needed = AsyncMock()  # type: ignore[method-assign]
    deps._fill_my_experience = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._fill_voluntary_disclosures = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._answer_custom_questions = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._review_and_submit = AsyncMock(return_value=("Success", "shot.png"))  # type: ignore[method-assign]

    result, reason = await deps.apply_to_job("https://uhaul.myworkdayjobs.com/x/apply")

    assert result == "Success"
    assert reason == "shot.png"


@pytest.mark.asyncio
async def test_phase_failure_returns_error_tuple(deps: WorkdayApplier) -> None:
    deps._navigate_to_apply = AsyncMock()  # type: ignore[method-assign]
    deps._fill_account_if_needed = AsyncMock()  # type: ignore[method-assign]
    deps._fill_my_experience = AsyncMock(return_value=False)  # type: ignore[method-assign]

    result, reason = await deps.apply_to_job("https://uhaul.myworkdayjobs.com/x/apply")

    assert result == "Error"
    assert "my_experience" in reason


@pytest.mark.asyncio
async def test_fill_my_experience_uploads_resume_and_saves(
    deps: WorkdayApplier,
) -> None:
    deps.resume_structured = {
        "work_experience": [
            {
                "employer": "Acme",
                "title": "Eng",
                "start_date": "2020-01",
                "end_date": "2023-01",
                "description": "Built things.",
            }
        ],
        "education": [{"school": "MIT", "degree": "BS", "start_date": "2016", "end_date": "2020"}],
        "skills": ["Python", "Playwright"],
    }
    deps._upload_resume = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._add_work_history = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._add_education = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._add_skills = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._click_save_and_continue = AsyncMock(return_value=True)  # type: ignore[method-assign]

    ok = await deps._fill_my_experience()

    assert ok is True
    deps._upload_resume.assert_awaited_once()
    deps._add_work_history.assert_awaited_once()
    deps._add_education.assert_awaited_once()
    deps._add_skills.assert_awaited_once()
    deps._click_save_and_continue.assert_awaited_once()
