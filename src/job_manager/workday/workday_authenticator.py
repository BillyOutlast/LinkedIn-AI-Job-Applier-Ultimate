"""Per-tenant Workday account creation and session persistence."""

import os
import secrets
from pathlib import Path
from typing import Awaitable, Callable

from playwright.async_api import Page

from config.logger_config import logger
from src.job_manager.workday.workday_selectors import (
    ACCOUNT_CREATE_EMAIL_INPUT,
    ACCOUNT_CREATE_PASSWORD_INPUT,
    ACCOUNT_CREATE_SUBMIT,
)
from src.utils.browser_utils import safe_click, safe_fill

StorageWriter = Callable[[Path], Awaitable[bool]]


class WorkdayAuthenticator:
    def __init__(
        self,
        page: Page,
        session_dir: Path,
        storage_writer: StorageWriter,
    ) -> None:
        self.page = page
        self.session_dir = session_dir
        self.storage_writer = storage_writer

    def _session_path(self, tenant: str) -> Path:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        return self.session_dir / f"workday_{tenant}.json"

    def _password(self) -> str:
        env_pw = os.getenv("WORKDAY_DEFAULT_PASSWORD", "").strip()
        return env_pw or secrets.token_urlsafe(24)

    async def ensure_session(self, tenant: str, email: str, apply_url: str = "") -> bool:
        """Return True if a usable session exists or was created for tenant.

        Args:
            tenant: Workday tenant ID (from URL path, e.g., "UhaulJobs").
            email: Account email.
            apply_url: The apply URL the user reached. Used to derive the
                correct account-creation URL (real Workday URLs vary by
                tenant subdomain and wd-region, e.g. wd1 vs wd5).
        """
        path = self._session_path(tenant)
        if path.exists():
            logger.info(f"Workday session exists for tenant={tenant}")
            return True

        from src.job_manager.workday.workday_applier import WorkdayApplier

        logger.info(f"Creating Workday account for tenant={tenant}")
        url = (
            WorkdayApplier._account_creation_url(apply_url)
            if apply_url
            else f"https://{tenant}.myworkdayjobs.com/en-US/{tenant}/account/create"
        )
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            if not await safe_fill(self.page, ACCOUNT_CREATE_EMAIL_INPUT, email):
                return False
            password = self._password()
            if not await safe_fill(self.page, ACCOUNT_CREATE_PASSWORD_INPUT, password):
                return False
            if not await safe_click(self.page, ACCOUNT_CREATE_SUBMIT):
                return False
            return await self.storage_writer(path)
        except Exception as e:
            logger.error(f"Workday account creation failed for tenant={tenant}: {e}")
            return False
