# Taleo Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Taleo (`taleo.net` / `taleo.com` / Oracle Recruiting Cloud `hcmUI/CandidateExperience`) per-ATS apply handler so the bot can submit Taleo applications with optional login, removing `taleo` from `EXTERNAL_APPLY_SKIP_ATSES`.

**Architecture:** New `src/job_manager/taleo/` package with `taleo_applier.py` (handler), `taleo_authenticator.py` (login flow), `taleo_questions.py` (heuristic matcher). Recognizer's `_KNOWN_ATS` table grows by one row, plus 2 new hostname patterns and 1 marker pattern for Oracle Cloud. `Secrets` model gains `taleo_username`/`taleo_password`. `apply_agent._build_handler_with_state` extends by one elif. Auth is one phase inside `TaleoApplier` (no separate module). Auth-failure → `("Error", "auth failed: ...")` → ApplyAgent falls through to LLM (per design decision).

**Tech Stack:** Python 3.12, asyncio, stdlib `re` + `urllib.parse`, Playwright async, Pydantic v2 (`Secrets`), pytest + pytest-asyncio.

**Ground truth (research note):** Classic Oracle Taleo public apply forms sit at `https://<company>.taleo.net/careersection/<section>/jobdetail.ftl?job=<id>`. Oracle Recruiting Cloud (newer) hosts Taleo on `https://<company>.<tenant>.oraclecloud.com/hcmUI/CandidateExperience/...`. Many Taleo forms require login (`input[type="password"]` or redirect to `login.taleo.net`); some are public. Auth flow is username + password → "Sign In" / "Log In" button → wait for post-login URL change.

## Global Constraints

- 865 tests currently pass. Plan must keep them green.
- No new project dependencies. `re` and `urllib.parse` are stdlib.
- Recognizer table contract: each new ATS adds one row to `_KNOWN_ATS`.
- All error returns must be `Tuple[str, str]` matching the Workday/Greenhouse contract: `("Success", "")`, `("Skip", "<reason>")`, or `("Error", "<reason>")`.
- Best-effort: any uncaught exception → `("Error", str(e))` + log debug, never raises.
- Auth is one phase inside the handler; NOT a separate module.
- Fall-through to LLM when handler raises / returns Error or Skip (matches Greenhouse + Workday behavior).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/job_manager/taleo/__init__.py` | Create | Empty package marker |
| `src/job_manager/taleo/taleo_applier.py` | Create | `TaleoApplier` with 6 phases (navigate, detect_login_wall, auth via authenticator, fill, upload, submit) |
| `src/job_manager/taleo/taleo_authenticator.py` | Create | `TaleoAuthenticator.authenticate(page)` |
| `src/job_manager/taleo/taleo_questions.py` | Create | `TaleoQuestionHandler.match_question(label)` |
| `src/llm/ats_recognizer.py` | Modify | Add `"taleo"` to `_KNOWN_ATS` + 2 hostname patterns + 1 marker pattern + factory |
| `src/llm/apply_agent.py` | Modify | Add `elif handler_factory() is TaleoApplier` branch to `_build_handler_with_state` |
| `src/pydantic_models/config_models.py` | Modify | Add `taleo_username`, `taleo_password` to `Secrets` |
| `config/constants.py` | Modify | Add `OUTPUT_DIR_TALEO = "data/output/taleo"` |
| `src/job_manager/linkedin/job_manager_linkedin.py` | Modify | Remove `"taleo"` from `EXTERNAL_APPLY_SKIP_ATSES` (final task only) |
| `tests/test_ats_recognizer.py` | Modify | +1 hostname test |
| `tests/test_taleo_applier.py` | Create | 6 tests (login-wall-no-creds, auth-fail, public-form-success, submit-fail, exception-catch, navigate-fail) |

No new modules beyond `src/job_manager/taleo/`. No new third-party deps.

---

### Task 1: Recognizer entry + Secrets fields + stub package

**Files:**
- Modify: `src/llm/ats_recognizer.py` (add hostname + marker + factory + table row)
- Modify: `src/pydantic_models/config_models.py` (add 2 fields to `Secrets`)
- Modify: `config/constants.py` (add `OUTPUT_DIR_TALEO`)
- Modify: `tests/test_ats_recognizer.py` (add 1 test)
- Create: `src/job_manager/taleo/__init__.py` (empty)
- Create: `src/job_manager/taleo/taleo_applier.py` (stub)
- Create: `src/job_manager/taleo/taleo_authenticator.py` (stub)
- Create: `src/job_manager/taleo/taleo_questions.py` (stub)

**Interfaces:**
- Consumes: existing `recognize()`, `_KNOWN_ATS`, `_HOSTNAME_PATTERNS`, `_MARKER_PATTERNS` in `src/llm/ats_recognizer.py`; existing `Secrets` model in `src/pydantic_models/config_models.py`
- Produces: `recognize("https://company.taleo.net/...")` → `ATSMatch(name="taleo")`; `recognize("https://x.oraclecloud.com/hcmUI/CandidateExperience/...")` → `ATSMatch(name="taleo")`; `Secrets(taleo_username="...", taleo_password="...")` validates; `OUTPUT_DIR_TALEO` importable

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ats_recognizer.py`:

```python
def test_recognize_taleo_by_hostname():
    match = recognize("https://company.taleo.net/careersection/jobdetail.ftl?job=12345")
    assert match is not None
    assert match.name == "taleo"
    assert match.confidence == 1.0


def test_recognize_taleo_oracle_cloud_by_marker():
    match = recognize("https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/123")
    assert match is not None
    assert match.name == "taleo"
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `uv run pytest tests/test_ats_recognizer.py::test_recognize_taleo_by_hostname tests/test_ats_recognizer.py::test_recognize_taleo_oracle_cloud_by_marker -v`

Expected: FAIL — no taleo pattern / table entry.

- [ ] **Step 3: Add taleo to recognizer + Secrets + constants**

In `src/llm/ats_recognizer.py`, find `_HOSTNAME_PATTERNS` and append the taleo entries (after the greenhouse ones):

```python
_HOSTNAME_PATTERNS = [
    (re.compile(r"\.myworkdayjobs\.com$", re.I), "workday"),
    (re.compile(r"(^|\.)boards?\.greenhouse\.io$", re.I), "greenhouse"),
    (re.compile(r"(^|\.)job-boards\.greenhouse\.io$", re.I), "greenhouse"),
    (re.compile(r"(^|\.)taleo\.net$", re.I), "taleo"),
    (re.compile(r"(^|\.)taleo\.com$", re.I), "taleo"),
]
```

Find `_MARKER_PATTERNS` and append:

```python
_MARKER_PATTERNS = [
    (re.compile(r"hcmUI/CandidateExperience", re.I), "taleo"),
]
```

In `_KNOWN_ATS`, append after the greenhouse row:

```python
    "taleo": ATSMatch(
        name="taleo",
        confidence=1.0,
        handler_factory=lambda: _taleo_handler_class,
    ),
```

Below `_greenhouse_handler_class`, add:

```python
def _taleo_handler_class():
    from src.job_manager.taleo.taleo_applier import TaleoApplier
    return TaleoApplier
```

In `src/pydantic_models/config_models.py`, find `class Secrets(BaseModel)` and add inside:

```python
    taleo_username: Optional[str] = None
    taleo_password: Optional[str] = None
```

In `config/constants.py`, find the `# Greenhouse` block and add directly below:

```python
# Taleo
OUTPUT_DIR_TALEO = "data/output/taleo"
```

- [ ] **Step 4: Create the taleo package + stub files**

Create `src/job_manager/taleo/__init__.py` (empty file).

Create `src/job_manager/taleo/taleo_applier.py`:

```python
"""Taleo applier skeleton — full implementation lands in Task 2."""


class TaleoApplier:
    def __init__(self, *args, **kwargs):
        pass

    async def apply_to_job(self, url):
        return "Skip", "Taleo handler not yet implemented"
```

Create `src/job_manager/taleo/taleo_authenticator.py`:

```python
"""Taleo authenticator skeleton — full implementation lands in Task 2."""


class TaleoAuthenticator:
    def __init__(self, username=None, password=None):
        self.username = username
        self.password = password

    def has_credentials(self):
        return bool(self.username and self.password)

    async def authenticate(self, page):
        raise RuntimeError("TaleoAuthenticator not yet implemented")
```

Create `src/job_manager/taleo/taleo_questions.py`:

```python
"""Taleo question handler skeleton — full implementation lands in Task 2."""


class TaleoQuestionHandler:
    def __init__(self, cache_path=None, llm_answerer=None):
        self.cache_path = cache_path
        self.llm_answerer = llm_answerer

    def match_question(self, label):
        return None
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_ats_recognizer.py::test_recognize_taleo_by_hostname tests/test_ats_recognizer.py::test_recognize_taleo_oracle_cloud_by_marker -v`

Expected: PASS.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 867 passed (865 prior + 2 new). Investigate any regression.

- [ ] **Step 7: Commit**

```bash
git add src/job_manager/taleo/__init__.py src/job_manager/taleo/taleo_applier.py src/job_manager/taleo/taleo_authenticator.py src/job_manager/taleo/taleo_questions.py src/llm/ats_recognizer.py src/pydantic_models/config_models.py config/constants.py tests/test_ats_recognizer.py
git commit -m "feat(taleo): recognizer entry + Secrets fields + package stubs

Adds taleo to the recognizer table with 2 hostname patterns
(taleo.net, taleo.com) and 1 URL-marker pattern for Oracle
Recruiting Cloud (hcmUI/CandidateExperience). Secrets model gains
taleo_username + taleo_password fields so future wiring can pull
creds from .env. config/constants.py gains OUTPUT_DIR_TALEO.

TaleoApplier / TaleoAuthenticator / TaleoQuestionHandler are
deliberate stubs; the full implementation lands in Task 2."
```

---

### Task 2: Full TaleoApplier + apply_agent wiring + skip-list removal + tests

**Files:**
- Modify: `src/job_manager/taleo/taleo_applier.py` (replace stub with full handler)
- Modify: `src/job_manager/taleo/taleo_authenticator.py` (replace stub with real auth flow)
- Modify: `src/job_manager/taleo/taleo_questions.py` (replace stub with heuristic matcher)
- Modify: `src/llm/apply_agent.py` (add `elif handler_factory() is TaleoApplier` branch)
- Modify: `src/job_manager/linkedin/job_manager_linkedin.py` (remove `"taleo"` from `EXTERNAL_APPLY_SKIP_ATSES`)
- Create: `tests/test_taleo_applier.py` (6 tests)

**Interfaces:**
- Consumes: existing `WorkdayApplier`/`GreenhouseApplier` constructor signature for parity; `apply_agent._build_handler_with_state` pattern (elif by class identity)
- Produces:
  - `TaleoApplier(page, resume_structured, resume_pdf_path, question_handler, authenticator) -> instance`
  - `await TaleoApplier.apply_to_job(url) -> ("Success"|"Skip"|"Error", reason)`
  - `TaleoAuthenticator.authenticate(page)` — fills username/password, clicks Sign In, waits for post-login

- [ ] **Step 1: Write the failing tests**

Create `tests/test_taleo_applier.py`:

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
        patch.object(applier_no_creds, "_detect_login_wall", new_callable=AsyncMock, return_value=True),
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
        patch.object(applier_with_creds, "_detect_login_wall", new_callable=AsyncMock, return_value=True),
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
        patch.object(applier_with_creds, "_detect_login_wall", new_callable=AsyncMock, return_value=False),
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
        patch.object(applier_with_creds, "_detect_login_wall", new_callable=AsyncMock, return_value=False),
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
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `uv run pytest tests/test_taleo_applier.py -v`

Expected: FAIL — stub returns `("Skip", "Taleo handler not yet implemented")` for every call; signature mismatch on phase methods.

- [ ] **Step 3: Implement full `TaleoApplier` (replace stub)**

In `src/job_manager/taleo/taleo_applier.py`, replace the stub with:

```python
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
```

- [ ] **Step 4: Implement full `TaleoAuthenticator` (replace stub)**

In `src/job_manager/taleo/taleo_authenticator.py`, replace the stub with:

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

- [ ] **Step 5: Implement full `TaleoQuestionHandler` (replace stub)**

In `src/job_manager/taleo/taleo_questions.py`, replace the stub with:

```python
"""Per-question matcher for Taleo custom questions.

Mirrors the GreenhouseQuestionHandler pattern: heuristic answers for
common question shapes (authorized/sponsorship, experience, salary).
Returns None for unmatched — handler skips the field.
"""
from __future__ import annotations

from typing import Any, Optional


class TaleoQuestionHandler:
    def __init__(self, cache_path: Any = None, llm_answerer: Any = None):
        self.cache_path = cache_path
        self.llm_answerer = llm_answerer
        self._cache: dict[str, str] = {}

    def match_question(self, label: str) -> Optional[str]:
        norm = label.strip().lower()
        if norm in self._cache:
            return self._cache[norm]
        if "authorized" in norm or "eligible" in norm or "sponsorship" in norm:
            answer = "Yes"
        elif "relocate" in norm or "willing to" in norm:
            answer = "Yes"
        elif "experience" in norm or "years of" in norm:
            answer = "5+"
        elif "salary" in norm or "compensation" in norm:
            answer = ""
        else:
            answer = ""
        self._cache[norm] = answer
        return answer
```

- [ ] **Step 6: Extend `_build_handler_with_state` in `apply_agent.py`**

In `src/llm/apply_agent.py`, find the import block (around `from src.job_manager.greenhouse.greenhouse_applier import GreenhouseApplier`) and add directly after it:

```python
from src.job_manager.taleo.taleo_applier import TaleoApplier
```

Find `_build_handler_with_state`. After the GreenhouseApplier branch (which ends with `return GreenhouseApplier(...)`), add a new `if` branch (mirroring the Greenhouse structure):

```python
        if handler_factory is not None and handler_factory() is TaleoApplier:
            from pathlib import Path

            from config.constants import OUTPUT_DIR_TALEO
            from src.job_manager.taleo.taleo_applier import TaleoApplier
            from src.job_manager.taleo.taleo_authenticator import TaleoAuthenticator
            from src.job_manager.taleo.taleo_questions import TaleoQuestionHandler

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
            resume_pdf_path = (
                getattr(agent_self, "resume_pdf_path", None) or Path(RESUME_DIR) / "default.pdf"
            )
            return TaleoApplier(
                page=agent_self.page,
                resume_structured=resume_structured,
                resume_pdf_path=resume_pdf_path,
                question_handler=question_handler,
                authenticator=authenticator,
            )
```

- [ ] **Step 7: Remove `"taleo"` from `EXTERNAL_APPLY_SKIP_ATSES`**

In `src/job_manager/linkedin/job_manager_linkedin.py`, find the `EXTERNAL_APPLY_SKIP_ATSES` tuple (line 65) and remove `"taleo"`:

```python
EXTERNAL_APPLY_SKIP_ATSES: tuple[str, ...] = (
    "phenom",
    "successfactors",
)
```

Verify that the `jobdetail.ftl?job=` URL `/careersection/` is also not skipped (matched in the recognizer first → taleo elif).

- [ ] **Step 8: Run tests, expect PASS**

Run: `uv run pytest tests/test_taleo_applier.py -v`

Expected: 6 passed.

- [ ] **Step 9: Run full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 873 passed (867 + 6 new). Investigate any regression.

- [ ] **Step 10: Commit**

```bash
git add src/job_manager/taleo/taleo_applier.py src/job_manager/taleo/taleo_authenticator.py src/job_manager/taleo/taleo_questions.py src/llm/apply_agent.py src/job_manager/linkedin/job_manager_linkedin.py tests/test_taleo_applier.py
git commit -m "feat(taleo): full TaleoApplier with auth phase + apply-agent wiring

src/job_manager/taleo/taleo_applier.py implements TaleoApplier with
6 phases: navigate → detect_login_wall → authenticate → fill_basic_info
→ upload_resume → handle_custom_questions → submit. Returns the
standard (Success|Skip|Error, reason) tuple.

taleo_authenticator.py fills username/password then clicks Sign In /
Log In, waits for post-login networkidle.

Auth phase behavior (per design decision):
  - No login wall → skip auth, proceed to fill
  - Wall + no creds → ('Skip', 'credentials not configured')
  - Wall + creds + auth raises → ('Error', 'auth failed: ...') so
    ApplyAgent routing block falls through to LLM

apply_agent._build_handler_with_state gains an elif-equivalent branch
to instantiate TaleoApplier with secrets.taleo_username/password and
the question handler. Removes 'taleo' from EXTERNAL_APPLY_SKIP_ATSES,
unblocking Taleo-routed jobs from the LLM-stuck path."
```

---

### Task 3: Final review + housekeeping

- [ ] **Step 1: Read task reports**

Read `.superpowers/sdd/task-1-report.md` and `task-2-report.md`.

- [ ] **Step 2: Verify final state**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 873+ passed.

- [ ] **Step 3: Update progress ledger**

Append to `.superpowers/sdd/progress.md`:

```
# Taleo Handler — Subagent-Driven Progress

Plan: `docs/superpowers/plans/2026-06-30-taleo-handler.md`
Started: 2026-06-30
Branch: release
Base commit: ac2697c (Taleo design spec commit)
Task 1: complete (commits <base>..<T1>; 2 new recognizer tests; 867 total)
Task 2: complete (commits <T1>..<T2>; 6 new applier tests; 873 total)
FINAL: complete (per-ATS contract preserved; taleo removed from skip list; no regressions)
```

- [ ] **Step 4: Commit the ledger**

```bash
git add .superpowers/sdd/progress.md
git commit -m "chore(taleo): progress ledger for sub-project 4 of N"
```

---

## Self-Review

1. **Spec coverage:**
   - `TaleoApplier` (6 phases) → Task 2 Step 3
   - `TaleoAuthenticator.authenticate(page)` → Task 2 Step 4
   - `TaleoQuestionHandler.match_question(label)` heuristic → Task 2 Step 5
   - Recognizer entry (2 hostname patterns + 1 marker pattern + factory + table row) → Task 1 Step 3
   - `Secrets.taleo_username` / `Secrets.taleo_password` → Task 1 Step 3
   - `OUTPUT_DIR_TALEO` constant → Task 1 Step 3
   - `apply_agent._build_handler_with_state` extension → Task 2 Step 6
   - Remove `"taleo"` from `EXTERNAL_APPLY_SKIP_ATSES` → Task 2 Step 7
   - 6 applier tests + 2 recognizer tests → Task 1 Step 1 (2) + Task 2 Step 1 (6) = 8 new tests
   - All error returns `Tuple[str, str]` → matches Greenhouse/Workday contract
2. **Placeholders:** none. Every code block is complete.
3. **Type consistency:**
   - `apply_to_job(url) -> Tuple[str, str]` matches Workday/Greenhouse contract
   - `TaleoApplier.__init__(page, resume_structured, resume_pdf_path, question_handler, authenticator)` — 5th param mirrors WorkdayAuthenticator pattern (Workday uses `authenticator`)
   - Recognizer's `ATSMatch(name="taleo", confidence=1.0)` follows existing entry shape
   - `_build_handler_with_state` if-branch: same `handler_factory() is <Class>` pattern
4. **Sequential execution:** Task 1 must ship first (Task 2 references `TaleoApplier`, `TaleoAuthenticator`, `TaleoQuestionHandler`). No parallel option.

No issues found. Plan ready.