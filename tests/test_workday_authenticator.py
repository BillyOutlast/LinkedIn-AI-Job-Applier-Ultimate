"""WorkdayAuthenticator must reuse cached sessions and persist new ones."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.job_manager.workday.workday_authenticator import WorkdayAuthenticator


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    return tmp_path / "browser_session"


@pytest.mark.asyncio
async def test_existing_session_returns_true_without_browser(session_dir: Path) -> None:
    session_dir.mkdir(parents=True)
    state_file = session_dir / "workday_uhaul.json"
    state_file.write_text("{}", encoding="utf-8")

    auth = WorkdayAuthenticator(
        page=MagicMock(),
        session_dir=session_dir,
        storage_writer=AsyncMock(),
    )

    ok = await auth.ensure_session("uhaul", "me@example.com")

    assert ok is True


@pytest.mark.asyncio
async def test_missing_session_calls_create(session_dir: Path) -> None:
    page = AsyncMock()
    storage_writer = AsyncMock(return_value=True)
    auth = WorkdayAuthenticator(
        page=page,
        session_dir=session_dir,
        storage_writer=storage_writer,
    )

    with (
        patch(
            "src.job_manager.workday.workday_authenticator.safe_fill",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "src.job_manager.workday.workday_authenticator.safe_click",
            new=AsyncMock(return_value=True),
        ),
    ):
        ok = await auth.ensure_session("uhaul", "me@example.com")

    assert ok is True
    storage_writer.assert_awaited_once()
    page.goto.assert_called()
