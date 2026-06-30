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
from src.job_manager.workday.workday_selectors import (
    APPLY_FLOW_CONTAINER,
    EDUCATION_ADD_BUTTON,
    EDUCATION_DEGREE,
    EDUCATION_END_DATE,
    EDUCATION_SCHOOL,
    EDUCATION_START_DATE,
    RESUME_UPLOAD_INPUT,
    SAVE_AND_CONTINUE,
    SKILLS_INPUT,
    WORK_HISTORY_ADD_BUTTON,
    WORK_HISTORY_DESCRIPTION,
    WORK_HISTORY_EMPLOYER,
    WORK_HISTORY_END_DATE,
    WORK_HISTORY_START_DATE,
    WORK_HISTORY_TITLE,
)
from src.utils.browser_utils import (
    debug_capture,
    find_element_safely,
    safe_click,
    safe_fill,
)


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
        await self.page.goto(url, wait_until="domcontentloaded")
        await find_element_safely(self.page, APPLY_FLOW_CONTAINER, "css")

    async def _fill_account_if_needed(self, tenant: str) -> bool:
        raise NotImplementedError

    async def _fill_my_experience(self) -> bool:
        if not await self._upload_resume():
            return False
        if not await self._add_work_history():
            return False
        if not await self._add_education():
            return False
        if not await self._add_skills():
            return False
        return await self._click_save_and_continue()

    async def _upload_resume(self) -> bool:
        if not self.resume_pdf_path.exists():
            return False
        try:
            await self.page.set_input_files(RESUME_UPLOAD_INPUT, str(self.resume_pdf_path))
            return True
        except Exception as e:
            logger.error(f"Resume upload failed: {e}")
            return False

    async def _add_work_history(self) -> bool:
        for entry in self.resume_structured.get("work_experience", []) or []:
            if not await safe_click(self.page, WORK_HISTORY_ADD_BUTTON):
                return False
            await safe_fill(self.page, WORK_HISTORY_EMPLOYER, str(entry.get("employer", "")))
            await safe_fill(self.page, WORK_HISTORY_TITLE, str(entry.get("title", "")))
            await safe_fill(self.page, WORK_HISTORY_START_DATE, str(entry.get("start_date", "")))
            await safe_fill(self.page, WORK_HISTORY_END_DATE, str(entry.get("end_date", "")))
            await safe_fill(self.page, WORK_HISTORY_DESCRIPTION, str(entry.get("description", "")))
        return True

    async def _add_education(self) -> bool:
        for entry in self.resume_structured.get("education", []) or []:
            if not await safe_click(self.page, EDUCATION_ADD_BUTTON):
                return False
            await safe_fill(self.page, EDUCATION_SCHOOL, str(entry.get("school", "")))
            await safe_fill(self.page, EDUCATION_DEGREE, str(entry.get("degree", "")))
            await safe_fill(self.page, EDUCATION_START_DATE, str(entry.get("start_date", "")))
            await safe_fill(self.page, EDUCATION_END_DATE, str(entry.get("end_date", "")))
        return True

    async def _add_skills(self) -> bool:
        skills = self.resume_structured.get("skills") or []
        if not skills:
            return True
        text = ", ".join(str(s) for s in skills)
        return await safe_fill(self.page, SKILLS_INPUT, text)

    async def _click_save_and_continue(self) -> bool:
        return await safe_click(self.page, SAVE_AND_CONTINUE)

    async def _fill_voluntary_disclosures(self) -> bool:
        raise NotImplementedError

    async def _answer_custom_questions(self) -> bool:
        raise NotImplementedError

    async def _review_and_submit(self) -> tuple[str, str]:
        raise NotImplementedError
