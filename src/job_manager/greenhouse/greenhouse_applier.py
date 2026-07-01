"""Greenhouse per-ATS apply handler."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from config.logger_config import logger


class GreenhouseApplier:
    """Apply to a Greenhouse-hosted job.

    Public contract: `await apply_to_job(url) -> Tuple[str, str]` matching
    the WorkdayApplier shape. The recognizer routes here for URLs matching
    boards.greenhouse.io or job-boards.greenhouse.io.

    Phases: navigate → detect_blockers (reCAPTCHA / external partner) →
    fill_basic_info → upload_resume → handle_custom_questions → submit.
    Each phase is best-effort; failures degrade gracefully.
    """

    def __init__(
        self,
        page: Any,
        resume_structured: dict,
        resume_pdf_path: Any,  # Path-like
        question_handler: Any,
    ) -> None:
        self.page = page
        self.resume_structured = resume_structured or {}
        self.resume_pdf_path = resume_pdf_path
        self.question_handler = question_handler
        # Capture pre-submit URL to detect post-submit redirect.
        try:
            self._initial_url = str(self.page.url)
        except Exception:
            self._initial_url = ""

    async def apply_to_job(self, url: str) -> Tuple[str, str]:
        """Entry point. Returns (outcome, reason). Never raises."""
        try:
            if not await self._navigate(url):
                return "Error", f"Could not navigate to {url}"
            blocker = await self._detect_blockers()
            if blocker == "recaptcha":
                return "Skip", "reCAPTCHA challenge present; cannot solve automatically"
            if blocker == "external":
                return (
                    "Skip",
                    "External application partner; Greenhouse form is a notice",
                )
            await self._fill_basic_info()
            await self._upload_resume()
            await self._handle_custom_questions()
            submitted = await self._submit()
            if not submitted:
                return "Error", "Submit failed or post-submit indicator not found"
            return "Success", ""
        except Exception as e:
            logger.debug(f"GreenhouseApplier.apply_to_job failed (non-fatal): {e}")
            return "Error", str(e)

    async def _navigate(self, url: str) -> bool:
        try:
            await self.page.goto(url, timeout=15000)
            count = await self.page.locator("form#job-application-form").count()
            return count > 0
        except Exception as e:
            logger.debug(f"_navigate failed: {e}")
            return False

    async def _detect_blockers(self) -> Optional[str]:
        """Return 'recaptcha', 'external', or None."""
        try:
            recaptcha_count = await self.page.locator(
                "iframe[title*='reCAPTCHA' i], iframe[src*='recaptcha' i]"
            ).count()
        except Exception:
            recaptcha_count = 0
        if recaptcha_count > 0:
            return "recaptcha"
        try:
            body = (await self.page.locator("body").inner_text()) or ""
        except Exception:
            body = ""
        body_lower = body.lower()
        if (
            "official hiring partner" in body_lower
            or "do not need to submit this greenhouse application" in body_lower
        ):
            return "external"
        return None

    async def _fill_basic_info(self) -> None:
        personal = self.resume_structured.get("personal", {})
        location = self.resume_structured.get("location", {})
        await self._fill_field_by_label("First Name", personal.get("first_name", ""))
        await self._fill_field_by_label("Last Name", personal.get("last_name", ""))
        await self._fill_field_by_label("Email", personal.get("email", ""))
        await self._fill_field_by_label("Phone", personal.get("phone", ""))
        country = location.get("country")
        if country:
            await self._select_country_by_label(country)

    async def _fill_field_by_label(self, label_text: str, value: str) -> None:
        if not value:
            return
        try:
            await self.page.locator(
                f'xpath=//label[contains(normalize-space(.), "{label_text}")]/following::input[1]'
            ).first.fill(value)
        except Exception as e:
            logger.debug(f"fill '{label_text}' failed (non-fatal): {e}")

    async def _select_country_by_label(self, country: str) -> None:
        try:
            await self.page.locator(
                'xpath=//label[contains(normalize-space(.), "Country")]/following::select[1]'
            ).first.select_option(label=country)
        except Exception as e:
            logger.debug(f"select country '{country}' failed (non-fatal): {e}")

    async def _upload_resume(self) -> None:
        if not self.resume_pdf_path:
            return
        try:
            await self.page.locator("input[type='file']").first.set_input_files(
                str(self.resume_pdf_path)
            )
        except Exception as e:
            logger.debug(f"resume upload failed (non-fatal): {e}")

    async def _handle_custom_questions(self) -> None:
        try:
            fields = await self.page.locator("div.field").all()
        except Exception:
            return
        for field in fields:
            try:
                label = await field.locator("label").first.inner_text()
            except Exception:
                continue
            if label.strip().lower() in {
                "first name",
                "last name",
                "email",
                "phone",
                "country",
                "resume/cv",
                "resume",
            }:
                continue
            answer = self.question_handler.match_question(label) if self.question_handler else None
            if answer is None:
                continue
            try:
                if await field.locator("textarea").count() > 0:
                    await field.locator("textarea").first.fill(str(answer))
                elif await field.locator("input").count() > 0:
                    await field.locator("input").first.fill(str(answer))
                elif await field.locator("select").count() > 0:
                    await field.locator("select").first.select_option(label=str(answer))
            except Exception as e:
                logger.debug(f"custom question '{label}' fill failed (non-fatal): {e}")

    async def _submit(self) -> bool:
        try:
            await self.page.locator("button:has-text('Submit application')").first.click()
        except Exception as e:
            logger.debug(f"submit click failed: {e}")
            return False
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        try:
            body = (await self.page.locator("body").inner_text()) or ""
        except Exception:
            body = ""
        body_lower = body.lower()
        return (
            "application submitted" in body_lower
            or "thanks for applying" in body_lower
            or (self.page.url != self._initial_url)
        )
