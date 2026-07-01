from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.job_manager.taleo.taleo_applier import TaleoApplier
from src.job_manager.taleo.taleo_authenticator import TaleoAuthenticator


@pytest.fixture
def applier_with_creds():
    page = MagicMock()
    page.url = "https://company.taleo.net/careersection/jobdetail.ftl?job=12345"
    page.goto = AsyncMock()
    authenticator = TaleoAuthenticator(username="user@test.com", password="secret")
    return TaleoApplier(
        page=page,
        resume_structured={
            "personal": {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "phone": "+15555550100",
            },
        },
        resume_pdf_path="/tmp/ada.pdf",
        question_handler=MagicMock(match_question=MagicMock(return_value="Yes")),
        authenticator=authenticator,
    )


@pytest.fixture
def applier_no_creds():
    page = MagicMock()
    page.url = "https://company.taleo.net/careersection/jobdetail.ftl?job=12345"
    page.goto = AsyncMock()
    authenticator = TaleoAuthenticator()
    return TaleoApplier(
        page=page,
        resume_structured={"personal": {}},
        resume_pdf_path="/tmp/ada.pdf",
        question_handler=MagicMock(),
        authenticator=authenticator,
    )


@pytest.mark.asyncio
async def test_apply_to_job_skips_login_wall_without_credentials(applier_no_creds):
    """Login wall + no creds → Skip with clear reason."""
    with (
        patch.object(applier_no_creds, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(
            applier_no_creds,
            "_detect_login_wall",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await applier_no_creds.apply_to_job(
            "https://company.taleo.net/careersection/jobdetail.ftl?job=12345"
        )
    assert result[0] == "Skip"
    assert "credentials" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_on_auth_failure(applier_with_creds):
    """Auth raises → Error; dispatcher will fall through to LLM."""
    with (
        patch.object(applier_with_creds, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(
            applier_with_creds,
            "_detect_login_wall",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await applier_with_creds.apply_to_job(
            "https://company.taleo.net/careersection/jobdetail.ftl?job=12345"
        )
    assert result[0] == "Error"
    assert "auth" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_returns_success_on_public_form(applier_with_creds):
    """No login wall + submit succeeds → Success."""
    with (
        patch.object(applier_with_creds, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(
            applier_with_creds,
            "_detect_login_wall",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch.object(applier_with_creds, "_fill_basic_info", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_upload_resume", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_handle_custom_questions", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_submit", new_callable=AsyncMock, return_value=True),
    ):
        result = await applier_with_creds.apply_to_job(
            "https://company.taleo.net/careersection/jobdetail.ftl?job=12345"
        )
    assert result[0] == "Success"
    assert result[1] == ""


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_when_submit_fails(applier_with_creds):
    with (
        patch.object(applier_with_creds, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(
            applier_with_creds,
            "_detect_login_wall",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch.object(applier_with_creds, "_fill_basic_info", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_upload_resume", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_handle_custom_questions", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_submit", new_callable=AsyncMock, return_value=False),
    ):
        result = await applier_with_creds.apply_to_job(
            "https://company.taleo.net/careersection/jobdetail.ftl?job=12345"
        )
    assert result[0] == "Error"
    assert "submit" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_catches_unexpected_exception(applier_with_creds):
    """Any uncaught exception → Error tuple, never raise."""
    with patch.object(
        applier_with_creds,
        "_navigate",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        result = await applier_with_creds.apply_to_job(
            "https://company.taleo.net/careersection/jobdetail.ftl?job=12345"
        )
    assert result[0] == "Error"
    assert "boom" in result[1]


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_when_navigate_fails(applier_with_creds):
    with patch.object(applier_with_creds, "_navigate", new_callable=AsyncMock, return_value=False):
        result = await applier_with_creds.apply_to_job(
            "https://company.taleo.net/careersection/jobdetail.ftl?job=12345"
        )
    assert result[0] == "Error"
    assert "navigate" in result[1].lower()
