# Taleo Handler — Per-ATS Apply

**Date:** 2026-06-30
**Status:** Design — approved
**Author:** brainstorming session
**Scope:** Sub-project 4 of N (after Workday → Greenhouse)

## Goal

Add a per-ATS apply handler for Taleo (Oracle Taleo / Oracle Recruiting Cloud) so the bot can apply to Taleo-hosted jobs without falling through to the LLM. Removes `"taleo"` from `EXTERNAL_APPLY_SKIP_ATSES`. Handles both public forms and login-required forms (when credentials configured).

## Background

`EXTERNAL_APPLY_SKIP_ATSES` currently lists `taleo` as one of three ATSes the bot cannot reliably automate via browser-use LLM. The LLM gets stuck in login flows (per the existing comment in `src/job_manager/linkedin/job_manager_linkedin.py:62-69`). The Greenhouse sub-project established the per-ATS handler pattern (recognizer entry + handler class + apply_agent elif extension + tests). This sub-project extends the same pattern to Taleo with an added authentication phase.

## Architecture

Same recognizer table contract as Greenhouse. `_KNOWN_ATS` gains a `"taleo"` row. The handler extends the standard apply phases with an authentication phase that fires only when a login wall is detected. Auth is one phase inside `TaleoApplier` — not a separate module — because: (a) it's only used by Taleo today, (b) refactoring for size is YAGNI, (c) future ATSes with similar login requirements can each have their own auth phase without premature abstraction.

## Components

### `src/job_manager/taleo/taleo_applier.py` (new, ~200 lines)

```python
"""Taleo per-ATS apply handler."""
from __future__ import annotations
from typing import Any, Optional, Tuple

from config.logger_config import logger


class TaleoApplier:
    """Apply to a Taleo-hosted job (Oracle Taleo or Oracle Recruiting Cloud)."""

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
                    return "Skip", "Login required but no taleo_username/taleo_password configured"
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
        """Detect login wall: password input, 'Sign In' button, or login.taleo.net redirect."""
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
                f"xpath=//label[contains(normalize-space(.), \"{label_text}\")]/following::input[1]"
            ).first.fill(value)
        except Exception as e:
            logger.debug(f"fill '{label_text}' failed (non-fatal): {e}")

    async def _upload_resume(self) -> None:
        if not self.resume_pdf_path:
            return
        try:
            await self.page.locator("input[type='file']").first.set_input_files(str(self.resume_pdf_path))
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
            if label.strip().lower() in {"first name", "last name", "email", "phone", "resume/cv", "resume"}:
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
```

### `src/job_manager/taleo/taleo_authenticator.py` (new, ~50 lines)

```python
"""Taleo login flow."""
from __future__ import annotations
from typing import Any


class TaleoAuthenticator:
    def __init__(self, username: str = None, password: str = None):
        self.username = username
        self.password = password

    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    async def authenticate(self, page: Any) -> None:
        if not self.has_credentials():
            raise RuntimeError("No credentials configured")
        await page.locator(
            "input[type='text']:first-of-type, input[name='username'], input#username"
        ).first.fill(self.username)
        await page.locator("input[type='password']").first.fill(self.password)
        await page.locator(
            "button:has-text('Sign In'), button:has-text('Log In'), input[type='submit']"
        ).first.click()
        await page.wait_for_load_state("networkidle", timeout=10000)
```

### `src/job_manager/taleo/taleo_questions.py` (new, ~40 lines)

Mirrors `GreenhouseQuestionHandler` heuristic patterns: authorized/sponsorship → Yes, experience → "5+", salary → "". Returns None for unmatched (handler skips).

### `src/llm/ats_recognizer.py` (modify)

Add hostname pattern + marker pattern + factory + table entry:

```python
_HOSTNAME_PATTERNS = [
    (re.compile(r"\.myworkdayjobs\.com$", re.I), "workday"),
    (re.compile(r"(^|\.)boards?\.greenhouse\.io$", re.I), "greenhouse"),
    (re.compile(r"(^|\.)job-boards\.greenhouse\.io$", re.I), "greenhouse"),
    (re.compile(r"(^|\.)taleo\.net$", re.I), "taleo"),
    (re.compile(r"(^|\.)taleo\.com$", re.I), "taleo"),
]

_MARKER_PATTERNS = [
    (re.compile(r"hcmUI/CandidateExperience", re.I), "taleo"),  # Oracle Cloud
]
```

`_KNOWN_ATS` gains a `"taleo"` row with `handler_factory=lambda: _taleo_handler_class`.

### `src/llm/apply_agent.py` (modify)

Extend `_build_handler_with_state` with an `elif handler_factory() is TaleoApplier` branch:

```python
if handler_factory is not None and handler_factory() is TaleoApplier:
    from src.job_manager.taleo.taleo_applier import TaleoApplier
    from src.job_manager.taleo.taleo_authenticator import TaleoAuthenticator
    from src.job_manager.taleo.taleo_questions import TaleoQuestionHandler
    from config.constants import OUTPUT_DIR_TALEO
    from pathlib import Path

    secrets = getattr(agent_self, "secrets", None) or {}
    authenticator = TaleoAuthenticator(
        username=getattr(secrets, "taleo_username", None),
        password=getattr(secrets, "taleo_password", None),
    )
    question_handler = TaleoQuestionHandler(
        cache_path=Path(OUTPUT_DIR_TALEO) / "answers.yaml",
        llm_answerer=getattr(agent_self, "llm_answerer", None),
    )
    resume_structured = getattr(agent_self, "resume_structured", None) or {}
    resume_pdf_path = getattr(agent_self, "resume_pdf_path", None) or Path(RESUME_DIR) / "default.pdf"
    return TaleoApplier(
        page=agent_self.page,
        resume_structured=resume_structured,
        resume_pdf_path=resume_pdf_path,
        question_handler=question_handler,
        authenticator=authenticator,
    )
```

### `src/pydantic_models/config_models.py` (modify)

Add to `Secrets`:

```python
taleo_username: Optional[str] = None
taleo_password: Optional[str] = None
```

### `config/constants.py` (modify)

Add `OUTPUT_DIR_TALEO = "data/output/taleo"` near `OUTPUT_DIR_GREENHOUSE`.

### `src/job_manager/linkedin/job_manager_linkedin.py` (modify)

Remove `"taleo"` from `EXTERNAL_APPLY_SKIP_ATSES` tuple.

### Tests

#### `tests/test_ats_recognizer.py` (modify, +1 test)

```python
def test_recognize_taleo_by_hostname():
    match = recognize("https://company.taleo.net/careersection/jobdetail.ftl?job=12345")
    assert match is not None
    assert match.name == "taleo"
    assert match.confidence == 1.0
```

#### `tests/test_taleo_applier.py` (new, 6 tests)

```python
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
            "personal": {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com", "phone": "+15555550100"},
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
        patch.object(applier_no_creds, "_detect_login_wall", new_callable=AsyncMock, return_value=True),
    ):
        result = await applier_no_creds.apply_to_job("https://company.taleo.net/careersection/jobdetail.ftl?job=12345")
    assert result[0] == "Skip"
    assert "credentials" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_on_auth_failure(applier_with_creds):
    """Auth raises → Error with reason; dispatcher falls through to LLM."""
    with (
        patch.object(applier_with_creds, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(applier_with_creds, "_detect_login_wall", new_callable=AsyncMock, return_value=True),
    ):
        result = await applier_with_creds.apply_to_job("https://company.taleo.net/careersection/jobdetail.ftl?job=12345")
    assert result[0] == "Error"
    assert "auth" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_returns_success_on_public_form(applier_with_creds):
    """No login wall + submit succeeds → Success."""
    with (
        patch.object(applier_with_creds, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(applier_with_creds, "_detect_login_wall", new_callable=AsyncMock, return_value=False),
        patch.object(applier_with_creds, "_fill_basic_info", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_upload_resume", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_handle_custom_questions", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_submit", new_callable=AsyncMock, return_value=True),
    ):
        result = await applier_with_creds.apply_to_job("https://company.taleo.net/careersection/jobdetail.ftl?job=12345")
    assert result[0] == "Success"
    assert result[1] == ""


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_when_submit_fails(applier_with_creds):
    with (
        patch.object(applier_with_creds, "_navigate", new_callable=AsyncMock, return_value=True),
        patch.object(applier_with_creds, "_detect_login_wall", new_callable=AsyncMock, return_value=False),
        patch.object(applier_with_creds, "_fill_basic_info", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_upload_resume", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_handle_custom_questions", new_callable=AsyncMock),
        patch.object(applier_with_creds, "_submit", new_callable=AsyncMock, return_value=False),
    ):
        result = await applier_with_creds.apply_to_job("https://company.taleo.net/careersection/jobdetail.ftl?job=12345")
    assert result[0] == "Error"
    assert "submit" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_catches_unexpected_exception(applier_with_creds):
    """Any uncaught exception → Error tuple, never raise."""
    with patch.object(applier_with_creds, "_navigate", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        result = await applier_with_creds.apply_to_job("https://company.taleo.net/careersection/jobdetail.ftl?job=12345")
    assert result[0] == "Error"
    assert "boom" in result[1]


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_when_navigate_fails(applier_with_creds):
    with patch.object(applier_with_creds, "_navigate", new_callable=AsyncMock, return_value=False):
        result = await applier_with_creds.apply_to_job("https://company.taleo.net/careersection/jobdetail.ftl?job=12345")
    assert result[0] == "Error"
    assert "navigate" in result[1].lower()
```

## Data flow

```
1. LinkedIn dispatcher hits apply_url → ApplyAgent.apply_to_job(url)
2. recognize(url) → ATSMatch(name="taleo")
3. ApplyAgent routing block → _build_handler_with_state(taleo factory, self, url)
4. New elif branch: build TaleoAuthenticator with secrets.taleo_username/password, build TaleoQuestionHandler, instantiate TaleoApplier
5. TaleoApplier.apply_to_job(url):
   a. _navigate(url) → page.goto
   b. _detect_login_wall() → bool
   c. If wall + no creds → return ("Skip", "credentials not configured")
   d. If wall + creds → authenticator.authenticate(page); on raise → return ("Error", "auth failed: <reason>")
   e. _fill_basic_info() — fill name/email/phone via xpath label-following
   f. _upload_resume() — set input[type=file]
   g. _handle_custom_questions() — heuristic match_question for each div.field
   h. _submit() — click Submit/Apply; wait for networkidle; check post-submit indicator
6. Return ("Success"|"Skip"|"Error", reason) to ApplyAgent
7. ApplyAgent: if Success → return; if Skip/Error → falls through to LLM browser-use
```

## Error handling

- **No login wall detected** → skip auth, proceed to fill phases (the common public-form case)
- **Login wall + no creds** → `("Skip", "Login required but no taleo_username/taleo_password configured")` — operator knows to add creds
- **Login wall + creds + login succeeds** → continue to fill phases
- **Login wall + creds + login raises** → caught in apply_to_job → `("Error", "auth failed: <reason>")` → ApplyAgent falls through to LLM (per design decision)
- **Fill phase fails** → caught per-field, logged debug, continue
- **Submit fails** → `("Error", "Submit failed or post-submit indicator not found")`
- **Unexpected exception** → caught by outermost try/except → `("Error", str(e))` + debug log, never raises

## Testing

- 1 new recognizer test (`test_recognize_taleo_by_hostname`)
- 6 new applier tests (login-wall-no-creds, auth-fail, public-form-success, submit-fail, exception-catch, navigate-fail)
- Existing 865 tests must keep passing. Target after this spec: 872 total.

## Decomposition note

Sub-project 4 of N. Same per-ATS pattern as Greenhouse. Future per-ATS sub-projects (5..N: SuccessFactors, Phenom, Lever, iCIMS, Workable, etc.) follow the same template: recognizer row + handler class + apply_agent elif extension + Secrets fields + skip-list removal.

## Out of scope (YAGNI)

- MFA / 2FA handling
- SSO redirect handling (Okta, Azure AD)
- reCAPTCHA solving (currently Skip-on-detect not implemented; can be added later if discovered in real Taleo forms)
- Multi-page Taleo apply flows (some forms span multiple pages — handler assumes single-page)
- File formats other than PDF for resume upload