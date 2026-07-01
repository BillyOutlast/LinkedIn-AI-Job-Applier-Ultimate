"""Taleo per-ATS apply handler."""

from __future__ import annotations

from typing import Any, Tuple

from config.logger_config import logger


class TaleoApplier:
    """Apply to a Taleo-hosted job (Oracle Taleo or Oracle Recruiting Cloud).

    Public contract: `await apply_to_job(url) -> Tuple[str, str]` matching
    the WorkdayApplier / GreenhouseApplier shape. The recognizer routes here
    for URLs matching taleo.net, taleo.com, or oraclecloud.com/hcmUI/CandidateExperience.

    Phases: navigate → detect_login_wall → authenticate (if wall) →
    fill_basic_info → upload_resume → handle_custom_questions → submit.
    """

    def __init__(
        self,
        page: Any,
        resume_structured: dict,
        resume_pdf_path: Any,
        question_handler: Any,
        authenticator: Any,
    ) -> None:
        self.page = page
        self.resume_structured = resume_structured or {}
        self.resume_pdf_path = resume_pdf_path
        self.question_handler = question_handler
        self.authenticator = authenticator
        try:
            self._initial_url = str(self.page.url)
        except Exception:
            self._initial_url = ""

    async def apply_to_job(self, url: str) -> Tuple[str, str]:
        """Entry point. Returns (outcome, reason). Never raises."""
        try:
            if not await self._navigate(url):
                return "Error", f"Could not navigate to {url}"
            if await self._detect_login_wall():
                if not self.authenticator or not self.authenticator.has_credentials():
                    return (
                        "Skip",
                        "Login required but no taleo credentials configured (taleo_username/taleo_password)",
                    )
                try:
                    await self.authenticator.authenticate(self.page)
                except Exception as e:
                    logger.debug(f"Taleo auth failed (non-fatal): {e}")
                    return "Error", f"auth failed: {e}"
            await self._fill_basic_info()
            await self._upload_resume()
            await self._handle_custom_questions()
            submitted = await self._submit()
            if not submitted:
                return "Error", "Submit failed or post-submit indicator not found"
            return "Success", ""
        except Exception as e:
            logger.debug(f"TaleoApplier.apply_to_job failed (non-fatal): {e}")
            return "Error", str(e)

    async def _navigate(self, url: str) -> bool:
        try:
            await self.page.goto(url, timeout=15000)
            return True
        except Exception as e:
            logger.debug(f"_navigate failed: {e}")
            return False

    async def _detect_login_wall(self) -> bool:
        """Detect login wall: password input or login.taleo.net redirect."""
        try:
            password_count = await self.page.locator("input[type='password']").count()
        except Exception:
            password_count = 0
        if password_count > 0:
            return True
        try:
            current_url = str(self.page.url).lower()
        except Exception:
            current_url = ""
        if "login.taleo.net" in current_url or "signin" in current_url:
            return True
        return False

    async def _fill_basic_info(self) -> None:
        personal = self.resume_structured.get("personal", {})
        await self._fill_field_by_label("First Name", personal.get("first_name", ""))
        await self._fill_field_by_label("Last Name", personal.get("last_name", ""))
        await self._fill_field_by_label("Email", personal.get("email", ""))
        await self._fill_field_by_label("Phone", personal.get("phone", ""))

    async def _fill_field_by_label(self, label_text: str, value: str) -> None:
        if not value:
            return
        try:
            await self.page.locator(
                f'xpath=//label[contains(normalize-space(.), "{label_text}")]/following::input[1]'
            ).first.fill(value)
        except Exception as e:
            logger.debug(f"fill '{label_text}' failed (non-fatal): {e}")

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
            fields = await self.page.locator("div.field, .question, .form-group").all()
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
            await self.page.locator(
                "button:has-text('Submit'), button:has-text('Apply'), input[type='submit']"
            ).first.click()
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
            or "thank you for your interest" in body_lower
            or (self.page.url != self._initial_url)
        )
