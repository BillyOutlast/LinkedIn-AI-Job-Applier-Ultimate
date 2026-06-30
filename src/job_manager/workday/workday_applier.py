"""Workday apply flow orchestrator.

Phases run in order; each returns True/False or a (result, reason) tuple.
Top-level apply_to_job mirrors ApplyAgent's contract so the dispatch site
can use it as a drop-in.
"""

from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Page

from config.app_config import HEADLESS_MODE
from config.logger_config import logger
from src.dashboard.runtime import emit_event
from src.job_manager.workday.workday_authenticator import WorkdayAuthenticator
from src.job_manager.workday.workday_questions import WorkdayQuestionHandler
from src.utils.browser_utils import debug_capture


class WorkdayApplier:
    def __init__(
        self,
        page: Page,
        resume_structured: dict,
        resume_pdf_path: Path,
        question_handler: WorkdayQuestionHandler,
        authenticator: WorkdayAuthenticator,
        headless: bool = HEADLESS_MODE,
    ) -> None:
        self.page = page
        self.resume_structured = resume_structured
        self.resume_pdf_path = resume_pdf_path
        self.question_handler = question_handler
        self.authenticator = authenticator
        self.headless = headless

    @staticmethod
    def _tenant_from_url(url: str) -> str:
        host = urlparse(url).hostname or ""
        return host.split(".")[0]

    def _account_email(self, tenant: str) -> str:
        import os

        per_tenant = os.getenv(f"WORKDAY_EMAIL_{tenant.upper()}", "").strip()
        if per_tenant:
            return per_tenant
        return os.getenv("linkedin_email") or os.getenv("indeed_email") or ""

    async def apply_to_job(self, url: str) -> tuple[str, str]:
        tenant = self._tenant_from_url(url)
        emit_event("workday_phase", f"Starting Workday apply tenant={tenant}", tenant=tenant)

        try:
            await self._navigate_to_apply(url)
            if not await self._fill_account_if_needed(tenant):
                return ("Error", f"Account creation failed for tenant={tenant}")
            for phase_name, phase_fn in (
                ("my_experience", self._fill_my_experience),
                ("voluntary_disclosures", self._fill_voluntary_disclosures),
                ("custom_questions", self._answer_custom_questions),
            ):
                ok = await phase_fn()
                if not ok:
                    return ("Error", f"Phase failed: {phase_name}")
            result, reason = await self._review_and_submit()
            emit_event("workday_submitted", f"Submitted tenant={tenant}", tenant=tenant)
            return (result, reason)
        except Exception as e:
            logger.error(f"Workday apply crashed tenant={tenant}: {e}", exc_info=True)
            await debug_capture(self.page, f"workday_{tenant}_crash")
            return ("Error", str(e))

    # --- Phase methods (implemented in Tasks 7-9) ----------------------------
    async def _navigate_to_apply(self, url: str) -> None:
        raise NotImplementedError

    async def _fill_account_if_needed(self, tenant: str) -> bool:
        raise NotImplementedError

    async def _fill_my_experience(self) -> bool:
        raise NotImplementedError

    async def _fill_voluntary_disclosures(self) -> bool:
        raise NotImplementedError

    async def _answer_custom_questions(self) -> bool:
        raise NotImplementedError

    async def _review_and_submit(self) -> tuple[str, str]:
        raise NotImplementedError
