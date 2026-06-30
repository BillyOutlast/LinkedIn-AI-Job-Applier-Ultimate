"""WorkdayApplier must dispatch through phases and return ApplyAgent-shaped tuples."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

    (result, reason), error = await deps.apply_to_job("https://uhaul.myworkdayjobs.com/x/apply")

    assert result == "Success"
    assert reason == "shot.png"
    assert error is None


@pytest.mark.asyncio
async def test_phase_failure_returns_error_tuple(deps: WorkdayApplier) -> None:
    deps._navigate_to_apply = AsyncMock()  # type: ignore[method-assign]
    deps._fill_account_if_needed = AsyncMock()  # type: ignore[method-assign]
    deps._fill_my_experience = AsyncMock(return_value=False)  # type: ignore[method-assign]

    (result, reason), error = await deps.apply_to_job("https://uhaul.myworkdayjobs.com/x/apply")

    assert result == "Error"
    assert "my_experience" in reason
    assert error is None


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


@pytest.mark.asyncio
async def test_fill_voluntary_disclosures_defaults_to_prefer_not_to_answer(
    deps: WorkdayApplier,
) -> None:
    deps._click_save_and_continue = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._select_prefer_not_to_answer = AsyncMock(return_value=True)  # type: ignore[method-assign]
    # Mock find_elements_safely to return 2 fake group elements
    with patch(
        "src.job_manager.workday.workday_applier.find_elements_safely",
        new=AsyncMock(return_value=[MagicMock(), MagicMock()]),
    ):
        ok = await deps._fill_voluntary_disclosures()

    assert ok is True
    assert deps._select_prefer_not_to_answer.await_count == 2
    deps._click_save_and_continue.assert_awaited_once()


@pytest.mark.asyncio
async def test_answer_custom_questions_uses_handler(deps: WorkdayApplier) -> None:
    deps._scrape_questions = AsyncMock(
        return_value=[{"text": "Why?", "type": "textarea", "required": True}]
    )  # type: ignore[method-assign]
    deps._apply_answers = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._click_save_and_continue = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps.question_handler.answer = MagicMock(return_value={"Why?": "Because."})

    ok = await deps._answer_custom_questions()

    assert ok is True
    deps.question_handler.answer.assert_called_once()
    deps._apply_answers.assert_awaited_once_with({"Why?": "Because."})
    deps._click_save_and_continue.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_and_submit_submits(deps: WorkdayApplier) -> None:
    deps._wait_for_review_page = AsyncMock()  # type: ignore[method-assign]
    deps._assert_no_required_errors = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._click_submit = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._capture_success_screenshot = AsyncMock(
        return_value="data/output/screenshots/uhaul_x.png"
    )  # type: ignore[method-assign]
    deps.page.wait_for_selector = AsyncMock()  # type: ignore[method-assign]

    result, reason = await deps._review_and_submit()

    assert result == "Success"
    assert reason == "data/output/screenshots/uhaul_x.png"
