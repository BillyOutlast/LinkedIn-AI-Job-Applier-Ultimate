from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.job_manager.greenhouse.greenhouse_applier import GreenhouseApplier


@pytest.fixture
def applier():
    page = MagicMock()
    page.url = "https://boards.greenhouse.io/stripe/jobs/12345"
    page.goto = AsyncMock()
    page.locator.return_value.count = AsyncMock(return_value=0)
    page.locator.return_value.first = MagicMock()
    page.locator.return_value.first.click = AsyncMock()
    page.set_input_files = AsyncMock()
    return GreenhouseApplier(
        page=page,
        resume_structured={
            "personal": {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "phone": "+15555550100",
            },
            "location": {"country": "United States"},
        },
        resume_pdf_path="/tmp/ada.pdf",
        question_handler=MagicMock(match_question=MagicMock(return_value="Yes")),
    )


@pytest.mark.asyncio
async def test_apply_to_job_skips_when_recaptcha_detected(applier):
    """reCAPTCHA iframe present → Skip, no submit attempt."""
    with (
        patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(
            applier,
            "_detect_blockers",
            new_callable=AsyncMock,
            return_value="recaptcha",
        ),
    ):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Skip"
    assert "recaptcha" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_skips_on_external_redirect(applier):
    """External-partner text detected → Skip, no submit attempt."""
    with (
        patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(applier, "_detect_blockers", new_callable=AsyncMock, return_value="external"),
    ):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Skip"
    assert "external" in result[1].lower() or "partner" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_returns_success_when_no_blockers_and_submit_succeeds(
    applier,
):
    """Happy path: no blockers, basic fields fill, resume uploads, submit succeeds."""
    with (
        patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(applier, "_detect_blockers", new_callable=AsyncMock, return_value=None),
        patch.object(applier, "_fill_basic_info", new_callable=AsyncMock),
        patch.object(applier, "_upload_resume", new_callable=AsyncMock),
        patch.object(applier, "_handle_custom_questions", new_callable=AsyncMock),
        patch.object(applier, "_submit", new_callable=AsyncMock, return_value=True),
    ):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Success"
    assert result[1] == ""


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_when_submit_fails(applier):
    with (
        patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(applier, "_detect_blockers", new_callable=AsyncMock, return_value=None),
        patch.object(applier, "_fill_basic_info", new_callable=AsyncMock),
        patch.object(applier, "_upload_resume", new_callable=AsyncMock),
        patch.object(applier, "_handle_custom_questions", new_callable=AsyncMock),
        patch.object(applier, "_submit", new_callable=AsyncMock, return_value=False),
    ):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Error"
    assert "submit" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_catches_unexpected_exception(applier):
    """Any uncaught exception → Error tuple, never raise."""
    with patch.object(
        applier, "_navigate", new_callable=AsyncMock, side_effect=RuntimeError("boom")
    ):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Error"
    assert "boom" in result[1]


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_when_navigate_fails(applier):
    with patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=False):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Error"
    assert "navigate" in result[1].lower()
