from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.job_manager.linkedin.easy_applier_linkedin import LinkedInEasyApplier
from src.pydantic_models.job_models import Job

MODULE = "src.job_manager.linkedin.easy_applier_linkedin"


@pytest.fixture
def easy_applier():
    page = MagicMock()
    page.wait_for_selector = AsyncMock()
    page.reload = AsyncMock()
    page.locator.return_value.all = AsyncMock(return_value=[])

    with (
        patch(f"{MODULE}.get_ready_made_resume", return_value=None),
        patch.object(LinkedInEasyApplier, "_load_questions", return_value=[]),
    ):
        return LinkedInEasyApplier(
            page=page,
            gpt_answerer=MagicMock(),
            resume_anonymizer=MagicMock(),
            resume_generator_manager=MagicMock(),
            pause_checker=None,
            answers_file=Path("answers.yaml"),
            resume_dir=Path("resume"),
            cover_letter_dir=Path("cover_letters"),
            test_mode=True,
        )


class TestEasyApplyButtonDetection:
    @pytest.mark.asyncio
    async def test_find_easy_apply_button_returns_none_when_limit_reached(self, easy_applier):
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )

        with (
            patch.object(easy_applier, "check_for_premium_redirect", new_callable=AsyncMock),
            patch.object(
                easy_applier, "_check_easy_apply_limit", new_callable=AsyncMock
            ) as mock_limit,
        ):
            mock_limit.return_value = True

            result = await easy_applier._find_easy_apply_button(job)

            assert result is None

    @pytest.mark.asyncio
    async def test_find_easy_apply_button_clicks_and_returns_true(self, easy_applier):
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )
        button = AsyncMock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.first = AsyncMock()

        with (
            patch.object(easy_applier, "check_for_premium_redirect", new_callable=AsyncMock),
            patch.object(
                easy_applier, "_check_easy_apply_limit", new_callable=AsyncMock
            ) as mock_limit,
            patch(f"{MODULE}.find_elements_safely", new_callable=AsyncMock) as mock_find,
        ):
            mock_limit.return_value = False
            mock_find.return_value = [button]

            result = await easy_applier._find_easy_apply_button(job)

            assert result is True
            button.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_find_easy_apply_button_captures_debug_when_not_found(self, easy_applier):
        """When the button is not found, debug_capture must run before returning False."""
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )

        with (
            patch.object(easy_applier, "check_for_premium_redirect", new_callable=AsyncMock),
            patch.object(
                easy_applier, "_check_easy_apply_limit", new_callable=AsyncMock
            ) as mock_limit,
            patch(f"{MODULE}.find_elements_safely", new_callable=AsyncMock) as mock_find,
            patch(f"{MODULE}.debug_capture", new_callable=AsyncMock) as mock_capture,
            patch(f"{MODULE}.async_pause", new_callable=AsyncMock),
        ):
            mock_limit.return_value = False
            mock_find.return_value = []  # no buttons at all

            result = await easy_applier._find_easy_apply_button(job)

            assert result is False
            mock_capture.assert_awaited_once()
            # Second positional arg of the await is the label
            label_arg = (
                mock_capture.await_args.args[1]
                if len(mock_capture.await_args.args) > 1
                else mock_capture.await_args.kwargs.get("label")
            )
            assert label_arg == "easy_apply_button_missing"

    @pytest.mark.asyncio
    async def test_find_easy_apply_button_skips_hidden_button(self, easy_applier):
        """A hidden Easy Apply button must not be clicked; debug_capture must fire."""
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )
        hidden_button = AsyncMock()
        hidden_button.is_visible = AsyncMock(return_value=True)
        hidden_button.is_enabled = AsyncMock(return_value=True)
        hidden_button.first = AsyncMock()
        hidden_button.first.bounding_box = AsyncMock(
            return_value={"x": 0, "y": 0, "width": 0, "height": 0}
        )
        click_attempted = False

        async def maybe_click(*args, **kwargs):
            nonlocal click_attempted
            click_attempted = True

        hidden_button.first.click = AsyncMock(side_effect=maybe_click)
        with (
            patch.object(easy_applier, "check_for_premium_redirect", new_callable=AsyncMock),
            patch.object(
                easy_applier, "_check_easy_apply_limit", new_callable=AsyncMock
            ) as mock_limit,
            patch(f"{MODULE}.find_elements_safely", new_callable=AsyncMock) as mock_find,
            patch(f"{MODULE}.debug_capture", new_callable=AsyncMock) as mock_capture,
            patch(f"{MODULE}.async_pause", new_callable=AsyncMock),
        ):
            mock_limit.return_value = False
            mock_find.return_value = [hidden_button]

            result = await easy_applier._find_easy_apply_button(job)

            assert result is False
            assert click_attempted is False, "hidden button must NOT be clicked"
            hidden_labels = [
                call.args[1] if len(call.args) > 1 else call.kwargs.get("label")
                for call in mock_capture.await_args_list
            ]
            assert "easy_apply_button_hidden" in hidden_labels


class TestNextButtonDetection:
    @pytest.mark.asyncio
    async def test_find_next_or_submit_button_returns_matching_button(self, easy_applier):
        button = AsyncMock()

        with (
            patch(f"{MODULE}.find_elements_safely", new_callable=AsyncMock) as mock_find,
            patch(f"{MODULE}.get_clean_text", new_callable=AsyncMock) as mock_text,
        ):
            mock_find.return_value = [button]
            mock_text.return_value = "Review"

            next_button, button_text = await easy_applier._find_next_or_submit_button()

            assert next_button == button
            assert button_text == "review"
