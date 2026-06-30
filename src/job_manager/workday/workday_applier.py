"""Workday apply flow orchestrator.

Phases run in order; each returns True/False or a (result, reason) tuple.
Top-level apply_to_job mirrors ApplyAgent's contract so the dispatch site
can use it as a drop-in.
"""

import json
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Page

from config.app_config import HEADLESS_MODE
from config.constants import WORKDAY_SCREENSHOT_DIR
from config.logger_config import logger
from src.dashboard.runtime import emit_event
from src.job_manager.workday.workday_authenticator import WorkdayAuthenticator
from src.job_manager.workday.workday_questions import WorkdayQuestionHandler
from src.job_manager.workday.workday_selectors import (
    APPLICATION_CONFIRMATION,
    APPLY_FLOW_CONTAINER,
    DISCLOSURE_RADIO_GROUP,
    EDUCATION_ADD_BUTTON,
    EDUCATION_DEGREE,
    EDUCATION_END_DATE,
    EDUCATION_SCHOOL,
    EDUCATION_START_DATE,
    QUESTION_CHECKBOX,
    QUESTION_DROPDOWN,
    QUESTION_RADIO,
    QUESTION_TEXT_INPUT,
    QUESTION_TEXTAREA,
    RESUME_UPLOAD_INPUT,
    REVIEW_PAGE_INDICATOR,
    SAVE_AND_CONTINUE,
    SKILLS_INPUT,
    SUBMIT_BUTTON,
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
    find_elements_safely,
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
        return await self.authenticator.ensure_session(tenant, email=self._account_email(tenant))

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
        groups = await find_elements_safely(self.page, DISCLOSURE_RADIO_GROUP, "css")
        for group in groups:
            if not await self._select_prefer_not_to_answer(group):
                return False
        return await self._click_save_and_continue()

    async def _select_prefer_not_to_answer(self, group) -> bool:
        """Click the 'Prefer not to answer' option inside the given disclosure group."""
        try:
            locator = group.locator("label:has-text('Prefer not to answer') input[type='radio']")
            if await locator.count() == 0:
                return True
            await locator.first.click()
            return True
        except Exception as e:
            logger.warning(f"Disclosure select failed: {e}")
            return False

    async def _answer_custom_questions(self) -> bool:
        questions = await self._scrape_questions()
        if not questions:
            return await self._click_save_and_continue()
        answers = self.question_handler.answer(
            questions, context={"resume": str(self.resume_structured), "job": {}}
        )
        if not await self._apply_answers(answers):
            return False
        return await self._click_save_and_continue()

    async def _scrape_questions(self) -> list[dict]:
        """Scrape visible question labels and infer field type from nearest input."""
        try:
            return await self.page.evaluate("""() => {
                    const labels = Array.from(document.querySelectorAll('[data-automation-id="questionnaireLabel"], label'));
                    return labels.map(l => {
                        const text = (l.textContent || '').trim();
                        if (!text) return null;
                        const root = l.closest('div') || document;
                        const input = root.querySelector('input[type=text], textarea, select, input[type=radio], input[type=checkbox]');
                        let type = 'text';
                        if (input) {
                            if (input.tagName === 'TEXTAREA') type = 'textarea';
                            else if (input.tagName === 'SELECT') type = 'dropdown';
                            else if (input.type === 'radio') type = 'radio';
                            else if (input.type === 'checkbox') type = 'checkbox';
                        }
                        const required = !!(root.querySelector('[aria-required=true]') || root.querySelector('[required]'));
                        const options = input && input.tagName === 'SELECT'
                            ? Array.from(input.options).map(o => o.text)
                            : [];
                        return { text, type, required, options };
                    }).filter(Boolean);
                }""") or []
        except Exception as e:
            logger.warning(f"Question scrape failed: {e}")
            return []

    async def _apply_answers(self, answers: dict[str, str]) -> bool:
        for text, answer in answers.items():
            try:
                label = self.page.locator(f"label:has-text('{text}')").first
                root = label.locator("xpath=ancestor::div[1]")
                if await root.locator(QUESTION_TEXTAREA).count():
                    await root.locator(QUESTION_TEXTAREA).first.fill(answer)
                elif await root.locator(QUESTION_TEXT_INPUT).count():
                    await root.locator(QUESTION_TEXT_INPUT).first.fill(answer)
                elif await root.locator(QUESTION_DROPDOWN).count():
                    await root.locator(QUESTION_DROPDOWN).first.select_option(label=answer)
                elif await root.locator(QUESTION_RADIO).count():
                    await self.page.locator(
                        f"label:has-text('{answer}') >> input[type=radio]"
                    ).first.click()
                elif await root.locator(QUESTION_CHECKBOX).count():
                    await self.page.locator(
                        f"label:has-text('{answer}') >> input[type=checkbox]"
                    ).first.click()
            except Exception as e:
                logger.warning(f"Answer apply failed for '{text}': {e}")
        return True

    async def _review_and_submit(self) -> tuple[str, str]:
        try:
            await self._wait_for_review_page()
            if not await self._assert_no_required_errors():
                return ("Error", "Review page shows required-field errors")
            if not await self._click_submit():
                return ("Error", "Submit button click failed")
            await self.page.wait_for_selector(APPLICATION_CONFIRMATION, timeout=15000)
            shot = await self._capture_success_screenshot()
            return ("Success", shot)
        except Exception as e:
            logger.error(f"Review/submit failed: {e}", exc_info=True)
            await debug_capture(self.page, "workday_review_submit_fail")
            return ("Error", str(e))

    async def _wait_for_review_page(self) -> None:
        await self.page.wait_for_selector(REVIEW_PAGE_INDICATOR, timeout=10000)

    async def _assert_no_required_errors(self) -> bool:
        return (await self.page.locator("text=Required").count()) == 0

    async def _click_submit(self) -> bool:
        return await safe_click(self.page, SUBMIT_BUTTON)

    async def _capture_success_screenshot(self) -> str:
        out = Path(WORKDAY_SCREENSHOT_DIR)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"workday_{int(time.time())}.png"
        await self.page.screenshot(path=str(path))
        return str(path)


if __name__ == "__main__":
    """Smoke test against the Uhaul Workday job from the failure log."""
    import asyncio
    import traceback

    import dotenv
    from playwright.async_api import async_playwright

    from config.app_config import HEADLESS_MODE
    from config.constants import (
        BROWSER_STORAGE_STATE,
        OUTPUT_DIR_WORKDAY,
        RESUME_DIR,
        WORKDAY_SESSION_DIR,
    )
    from src.job_manager.workday.workday_authenticator import WorkdayAuthenticator
    from src.job_manager.workday.workday_questions import WorkdayQuestionHandler
    from src.pydantic_models.prompt_models import ResumeStructure
    from src.utils.browser_utils import create_playwright_browser, save_browser_session
    from src.utils.utils import load_yaml_file

    UHAUL_URL = (
        "https://uhaul.wd1.myworkdayjobs.com/en-US/UhaulJobs/job/"
        "Augusta-Maine/Customer-Service-Representative_R249007/apply?source=LinkedIn"
    )

    async def smoke() -> bool:
        secrets = dotenv.dotenv_values(".env")
        resume_structured = load_yaml_file(Path(RESUME_DIR) / "structured_resume.yaml")
        resume_structured = ResumeStructure(**resume_structured).model_dump()
        resume_pdf = next(Path(RESUME_DIR).glob("*.pdf"), None)

        browser, context, page = await create_playwright_browser(
            storage_state=BROWSER_STORAGE_STATE
        )
        authenticator = WorkdayAuthenticator(page, Path(WORKDAY_SESSION_DIR), save_browser_session)
        question_handler = WorkdayQuestionHandler(
            Path(OUTPUT_DIR_WORKDAY) / "answers.yaml", llm_answerer=None
        )
        applier = WorkdayApplier(
            page=page,
            resume_structured=resume_structured,
            resume_pdf_path=resume_pdf or Path("missing.pdf"),
            question_handler=question_handler,
            authenticator=authenticator,
            headless=HEADLESS_MODE,
        )
        try:
            result, reason = await applier.apply_to_job(UHAUL_URL)
            print(f"Result: {result} | Reason: {reason}")
            return result == "Success"
        finally:
            await context.close()
            await browser.close()

    success = asyncio.run(smoke())
    print("PASS" if success else "FAIL")
