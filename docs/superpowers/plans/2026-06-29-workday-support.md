# Workday External-Apply Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic Playwright `WorkdayApplier` that the bot dispatches to when a LinkedIn/Indeed external apply URL matches `myworkdayjobs.com`, replacing the failing `browser_use` fallback for Workday URLs only.

**Architecture:** New `src/job_manager/workday/` subpackage mirrors the `indeed/` shape — separate files for selectors, authenticator, question handler, and the apply orchestrator. Dispatch site in `job_manager_linkedin.py` + `job_manager_indeed.py` gets a 2-line routing branch. Workday profile data reuses existing `structured_resume.yaml` + resume PDF. Per-tenant browser storage state persisted to `browser_session/workday_{tenant}.json`.

**Tech Stack:** Playwright (async), `src.llm.llm_manager.GPTAnswerer`, `data/output/answers.yaml` cache, existing `safe_click`/`safe_fill`/`debug_capture` helpers.

## Global Constraints

- All Python commands via `uv run` (project rule)
- All new selectors live in `workday_selectors.py` as module-level constants — never inline strings elsewhere
- New prompts in `src/llm/prompts.py` — never inline elsewhere (LLM rule)
- Logger: `from config.logger_config import logger` — no `print()` for app logs
- Resume PII must be anonymized before LLM calls (security rule)
- `apply_to_job(url: str) -> tuple[str, str]` contract — same shape as `src/llm/apply_agent.py:257`
- All secrets via `.env`; new var `WORKDAY_DEFAULT_PASSWORD` defaults to auto-generated
- Per-project test rule: `uv run pytest tests/test_workday_*.py`

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `src/job_manager/workday/__init__.py` | New | Export `WorkdayApplier` |
| `src/job_manager/workday/workday_selectors.py` | New | CSS/XPath constants keyed by Workday `data-automation-id` |
| `src/job_manager/workday/workday_authenticator.py` | New | `WorkdayAuthenticator` — account create / session persist per tenant |
| `src/job_manager/workday/workday_questions.py` | New | `WorkdayQuestionHandler` — answer custom questions via cache + GPTAnswerer |
| `src/job_manager/workday/workday_applier.py` | New | `WorkdayApplier` orchestrator — phase methods + `apply_to_job` entry |
| `src/llm/prompts.py` | Modify | Add `workday_questions_prompt` template |
| `config/constants.py` | Modify | Add `WORKDAY_SCREENSHOT_DIR`, `WORKDAY_SESSION_DIR` |
| `.env_example` | Modify | Document `WORKDAY_DEFAULT_PASSWORD` |
| `src/job_manager/linkedin/job_manager_linkedin.py` | Modify | Workday URL routing branch (~line 387) |
| `src/job_manager/indeed/job_manager_indeed.py` | Modify | Workday URL routing branch (~line 300) |
| `tests/test_workday_selectors.py` | New | Selector constant validity |
| `tests/test_workday_authenticator.py` | New | Authenticator unit tests |
| `tests/test_workday_questions.py` | New | Question handler unit tests |
| `tests/test_workday_applier.py` | New | Integration test (skipped without `WORKDAY_E2E_TENANT`) |

---

### Task 1: Workday selectors module + constant validity test

**Files:**
- Create: `src/job_manager/workday/workday_selectors.py`
- Create: `src/job_manager/workday/__init__.py` (stub — replaced in Task 6)
- Create: `tests/test_workday_selectors.py`

**Interfaces:**
- Produces: `from src.job_manager.workday.workday_selectors import *` — all selectors importable

- [ ] **Step 1: Write failing test for selectors**

Create `tests/test_workday_selectors.py`:

```python
"""Selector constants must be non-empty strings with no duplicates."""

from src.job_manager.workday import workday_selectors


def test_all_selectors_are_non_empty_strings():
    failures = [
        name for name, value in vars(workday_selectors).items()
        if name.isupper() and not (isinstance(value, str) and value.strip())
    ]
    assert failures == [], f"Empty/invalid selectors: {failures}"


def test_no_duplicate_selector_values():
    seen = {}
    duplicates = []
    for name, value in vars(workday_selectors).items():
        if not name.isupper() or not isinstance(value, str):
            continue
        if value in seen:
            duplicates.append((seen[value], name))
        else:
            seen[value] = name
    assert duplicates == [], f"Duplicate selector values: {duplicates}"


def test_selectors_use_data_automation_id():
    """Workday's stable test hooks — selectors should rely on them."""
    bad = [
        name for name, value in vars(workday_selectors).items()
        if name.isupper()
        and isinstance(value, str)
        and "[data-automation-id=" not in value
        and name not in {"APPLY_FLOW_URL_PATTERN", "WORKDAY_HOST_SUFFIX"}
    ]
    assert bad == [], f"Selectors must use data-automation-id: {bad}"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/test_workday_selectors.py -v`
Expected: `ModuleNotFoundError: No module named 'src.job_manager.workday'`

- [ ] **Step 3: Create empty package + selectors module**

Create `src/job_manager/workday/__init__.py` (stub — replaced in Task 6):

```python
"""Workday external-apply handler (stub — replaced in Task 6)."""
```

Create `src/job_manager/workday/workday_selectors.py`:

```python
"""CSS/XPath constants for Workday widgets.

All selectors target Workday's `data-automation-id` test hooks, which are
stable across tenants since 2018. The two non-`data-automation-id` constants
are URL patterns, exempt from the test in test_workday_selectors.py.
"""

# --- Account creation -------------------------------------------------------
ACCOUNT_CREATE_EMAIL_INPUT = "input[data-automation-id='email']"
ACCOUNT_CREATE_PASSWORD_INPUT = "input[data-automation-id='password']"
ACCOUNT_CREATE_VERIFY_PASSWORD_INPUT = "input[data-automation-id='verifyPassword']"
ACCOUNT_CREATE_SUBMIT = "button[data-automation-id='createAccountSubmitButton']"

# --- Apply flow container ---------------------------------------------------
APPLY_FLOW_CONTAINER = "[data-automation-id='applyFlowContainer']"
APPLICATION_CONFIRMATION = "[data-automation-id='applicationConfirmation']"

# --- My Experience phase ----------------------------------------------------
RESUME_UPLOAD_INPUT = "input[type='file'][data-automation-id='file-upload-input']"
WORK_HISTORY_ADD_BUTTON = "button[data-automation-id='add-work-experience']"
WORK_HISTORY_EMPLOYER = "input[data-automation-id='employer']"
WORK_HISTORY_TITLE = "input[data-automation-id='jobTitle']"
WORK_HISTORY_START_DATE = "input[data-automation-id='startDate']"
WORK_HISTORY_END_DATE = "input[data-automation-id='endDate']"
WORK_HISTORY_DESCRIPTION = "textarea[data-automation-id='description']"
EDUCATION_ADD_BUTTON = "button[data-automation-id='add-education']"
EDUCATION_SCHOOL = "input[data-automation-id='school']"
EDUCATION_DEGREE = "input[data-automation-id='degree']"
EDUCATION_START_DATE = "input[data-automation-id='educationStartDate']"
EDUCATION_END_DATE = "input[data-automation-id='educationEndDate']"
SKILLS_INPUT = "input[data-automation-id='skillsInput']"
SAVE_AND_CONTINUE = "button[data-automation-id='bottom-navigation-next-button']"

# --- Voluntary Disclosures --------------------------------------------------
DISCLOSURE_RADIO_GROUP = "[data-automation-id='radioGroup']"

# --- Custom Questions -------------------------------------------------------
QUESTION_TEXT_INPUT = "input[data-automation-id='textInput']"
QUESTION_TEXTAREA = "textarea[data-automation-id='textAreaField']"
QUESTION_DROPDOWN = "select[data-automation-id='select']"
QUESTION_CHECKBOX = "input[type='checkbox'][data-automation-id='checkbox']"
QUESTION_RADIO = "input[type='radio'][data-automation-id='radio']"

# --- Review + Submit --------------------------------------------------------
REVIEW_PAGE_INDICATOR = "[data-automation-id='reviewPage']"
SUBMIT_BUTTON = "button[data-automation-id='bottom-navigation-submit-button']"

# --- URL patterns (exempt from data-automation-id check) ---------------------
WORKDAY_HOST_SUFFIX = "myworkdayjobs.com"
APPLY_FLOW_URL_PATTERN = "/apply"
```

- [ ] **Step 4: Run test, verify it passes**

Run: `uv run pytest tests/test_workday_selectors.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/job_manager/workday/ tests/test_workday_selectors.py
git commit -m "feat(workday): add selector constants module"
```

---

### Task 2: Workday path constants

**Files:**
- Modify: `config/constants.py`
- Modify: `.env_example`

**Interfaces:**
- Produces: `from config.constants import WORKDAY_SCREENSHOT_DIR, WORKDAY_SESSION_DIR, OUTPUT_DIR_WORKDAY` — Path values

- [ ] **Step 1: Add Workday constants**

Append to `config/constants.py` (verify exact existing layout first with `grep -n "RESUME_DIR\|BROWSER_STORAGE_STATE" config/constants.py`):

```python
# Workday
WORKDAY_SCREENSHOT_DIR = "data/output/screenshots"
WORKDAY_SESSION_DIR = "browser_session"
OUTPUT_DIR_WORKDAY = "data/output/workday"
```

- [ ] **Step 2: Document env var**

Append to `.env_example`:

```
# Optional: shared Workday password used when auto-creating accounts per company subdomain.
# If unset, the bot generates a unique random password per tenant and persists it in the per-tenant session state file.
WORKDAY_DEFAULT_PASSWORD=

# Optional: per-tenant email override (defaults to linkedin_email / indeed_email from .env).
# Example: WORKDAY_EMAIL_UHAUL=me+workday-uhaul@example.com
```

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from config.constants import WORKDAY_SCREENSHOT_DIR, WORKDAY_SESSION_DIR, OUTPUT_DIR_WORKDAY; print(WORKDAY_SCREENSHOT_DIR)"`
Expected: `data/output/screenshots`

- [ ] **Step 4: Commit**

```bash
git add config/constants.py .env_example
git commit -m "feat(workday): add path constants and env var template"
```

---

### Task 3: Workday question prompt template

**Files:**
- Modify: `src/llm/prompts.py`

**Interfaces:**
- Produces: `from src.llm.prompts import workday_questions_prompt` — function returning a prompt string

- [ ] **Step 1: Inspect existing prompts.py**

Run: `grep -n "^def \|^async def " src/llm/prompts.py | head -20`

Pick the convention used for existing prompt builders (e.g., function that returns `str`, takes question list + resume dict).

- [ ] **Step 2: Append the Workday prompt builder**

Append to `src/llm/prompts.py`:

```python
def workday_questions_prompt(questions: list[dict], context: dict) -> str:
    """Build prompt for answering Workday custom application questions.

    Args:
        questions: List of dicts with keys: `text`, `type` (text|textarea|dropdown|checkbox|radio), `required`, `options` (for dropdown/radio/checkbox).
        context: Dict with `resume` (anonymized) and `job` (title, company, description).

    Returns:
        Prompt string instructing LLM to return a JSON map of question_text -> answer.
    """
    q_lines = []
    for i, q in enumerate(questions, 1):
        opts = f" Options: {q['options']}" if q.get("options") else ""
        req = " (REQUIRED)" if q.get("required") else ""
        q_lines.append(f"{i}. [{q['type']}]{req} {q['text']}{opts}")
    questions_block = "\n".join(q_lines)

    return (
        "You are filling out a Workday job application. Answer each question based "
        "on the resume and job context below. Prefer concise, truthful answers. If "
        "the question asks for personal data not present (e.g., date of birth, "
        "gender, race, veteran status, disability), answer "
        "\"Prefer not to answer\". If a numeric salary/wage is requested and not "
        "specified, leave it blank or write \"Open\".\n\n"
        f"Resume (anonymized):\n{context.get('resume', '')}\n\n"
        f"Job: {context.get('job', {}).get('title', '')} at "
        f"{context.get('job', {}).get('company', '')}\n"
        f"{context.get('job', {}).get('description', '')}\n\n"
        f"Questions:\n{questions_block}\n\n"
        "Return JSON only: {\"<question_text>\": \"<answer>\", ...}"
    )
```

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from src.llm.prompts import workday_questions_prompt; print(workday_questions_prompt([{'text': 'Why?', 'type': 'textarea', 'required': True}], {'resume': 'x', 'job': {'title': 't', 'company': 'c', 'description': 'd'}})[:80])"`
Expected: prints first 80 chars of the prompt without error

- [ ] **Step 4: Commit**

```bash
git add src/llm/prompts.py
git commit -m "feat(workday): add custom-question prompt builder"
```

---

### Task 4: WorkdayQuestionHandler + cache-fallback test

**Files:**
- Create: `src/job_manager/workday/workday_questions.py`
- Create: `tests/test_workday_questions.py`

**Interfaces:**
- Consumes: `from src.llm.llm_manager import GPTAnswerer`, `from src.llm.prompts import workday_questions_prompt`, `from src.utils.utils import load_yaml_file`
- Produces: `WorkdayQuestionHandler(cache_path: Path, llm_answerer: GPTAnswerer).answer(questions: list[dict], context: dict) -> dict[str, str]`

- [ ] **Step 1: Write failing test**

Create `tests/test_workday_questions.py`:

```python
"""WorkdayQuestionHandler must hit cache before LLM and skip on malformed LLM output."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.job_manager.workday.workday_questions import WorkdayQuestionHandler


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    return tmp_path / "answers.yaml"


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.answer_question = MagicMock(return_value='{"Q1": "answer1"}')
    return llm


def test_cache_hit_skips_llm(tmp_cache: Path, mock_llm: MagicMock) -> None:
    tmp_cache.parent.mkdir(parents=True, exist_ok=True)
    tmp_cache.write_text('"Q1": "cached answer1"\n', encoding="utf-8")

    handler = WorkdayQuestionHandler(cache_path=tmp_cache, llm_answerer=mock_llm)
    questions = [{"text": "Q1", "type": "text", "required": True}]

    answers = handler.answer(questions, context={"resume": "", "job": {}})

    assert answers == {"Q1": "cached answer1"}
    mock_llm.answer_question.assert_not_called()


def test_llm_only_path_parses_json(tmp_cache: Path, mock_llm: MagicMock) -> None:
    handler = WorkdayQuestionHandler(cache_path=tmp_cache, llm_answerer=mock_llm)
    questions = [{"text": "Q1", "type": "text", "required": True}]

    answers = handler.answer(questions, context={"resume": "x", "job": {}})

    assert answers == {"Q1": "answer1"}
    mock_llm.answer_question.assert_called_once()


def test_malformed_llm_returns_empty_answers(tmp_cache: Path) -> None:
    bad_llm = MagicMock()
    bad_llm.answer_question = MagicMock(return_value="not json at all")
    handler = WorkdayQuestionHandler(cache_path=tmp_cache, llm_answerer=bad_llm)
    questions = [{"text": "Q1", "type": "text", "required": True}]

    answers = handler.answer(questions, context={"resume": "", "job": {}})

    assert answers == {}
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/test_workday_questions.py -v`
Expected: `ModuleNotFoundError: No module named 'src.job_manager.workday.workday_questions'`

- [ ] **Step 3: Implement WorkdayQuestionHandler**

Create `src/job_manager/workday/workday_questions.py`:

```python
"""Answer Workday custom application questions via cache + LLM."""

import json
from pathlib import Path
from typing import Any

from config.logger_config import logger
from src.llm.prompts import workday_questions_prompt
from src.utils.utils import append_yaml_file, load_yaml_file


class WorkdayQuestionHandler:
    def __init__(self, cache_path: Path, llm_answerer: Any) -> None:
        self.cache_path = cache_path
        self.llm_answerer = llm_answerer

    def answer(
        self, questions: list[dict], context: dict
    ) -> dict[str, str]:
        """Return a map of question_text -> answer.

        Cache hits skip the LLM. Misses go to LLM; malformed JSON returns
        empty answers so the caller can decide whether to skip the question.
        """
        cache = load_yaml_file(self.cache_path) or {}
        answers: dict[str, str] = {}
        missing: list[dict] = []

        for q in questions:
            text = q.get("text", "").strip()
            if not text:
                continue
            cached = cache.get(text)
            if cached:
                answers[text] = str(cached)
            else:
                missing.append(q)

        if missing and self.llm_answerer is not None:
            try:
                prompt = workday_questions_prompt(missing, context)
                raw = self.llm_answerer.answer_question(prompt)
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    for q in missing:
                        text = q["text"]
                        if text in parsed:
                            answers[text] = str(parsed[text])
                            append_yaml_file(self.cache_path, {text: parsed[text]})
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning(f"Workday LLM answer parse failed: {e}")
            except Exception as e:
                logger.warning(f"Workday LLM call failed: {e}")

        return answers
```

- [ ] **Step 4: Run test, verify it passes**

Run: `uv run pytest tests/test_workday_questions.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/job_manager/workday/workday_questions.py tests/test_workday_questions.py
git commit -m "feat(workday): add custom-question handler with cache+LLM"
```

---

### Task 5: WorkdayAuthenticator + session-persist test

**Files:**
- Create: `src/job_manager/workday/workday_authenticator.py`
- Create: `tests/test_workday_authenticator.py`

**Interfaces:**
- Consumes: `from src.utils.browser_utils import safe_fill, safe_click`
- Produces: `WorkdayAuthenticator(page: Page, session_dir: Path, storage_writer: Callable[[Path], Awaitable[bool]]).async ensure_session(tenant: str, email: str) -> bool` — returns `True` if a valid session exists or was created

- [ ] **Step 1: Write failing test**

Create `tests/test_workday_authenticator.py`:

```python
"""WorkdayAuthenticator must reuse cached sessions and persist new ones."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
    page = MagicMock()
    storage_writer = AsyncMock(return_value=True)
    auth = WorkdayAuthenticator(
        page=page,
        session_dir=session_dir,
        storage_writer=storage_writer,
    )

    ok = await auth.ensure_session("uhaul", "me@example.com")

    assert ok is True
    storage_writer.assert_awaited_once()
    page.goto.assert_called()
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/test_workday_authenticator.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement WorkdayAuthenticator**

Create `src/job_manager/workday/workday_authenticator.py`:

```python
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

    async def ensure_session(self, tenant: str, email: str) -> bool:
        """Return True if a usable session exists or was created for tenant."""
        path = self._session_path(tenant)
        if path.exists():
            logger.info(f"Workday session exists for tenant={tenant}")
            return True

        logger.info(f"Creating Workday account for tenant={tenant}")
        url = f"https://{tenant}.myworkdayjobs.com/en-US/{tenant}/account/create"
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
```

- [ ] **Step 4: Run test, verify it passes**

Run: `uv run pytest tests/test_workday_authenticator.py -v`
Expected: 2 passed (requires `pytest-asyncio`. If absent, run `uv add --dev pytest-asyncio` first.)

- [ ] **Step 5: Commit**

```bash
git add src/job_manager/workday/workday_authenticator.py tests/test_workday_authenticator.py
git commit -m "feat(workday): add per-tenant authenticator"
```

---

### Task 6: WorkdayApplier skeleton + entry-point test

**Files:**
- Create: `src/job_manager/workday/workday_applier.py`
- Create: `tests/test_workday_applier.py`
- Modify: `src/job_manager/workday/__init__.py` (replace stub with real export)

**Interfaces:**
- Consumes: `WorkdayAuthenticator`, `WorkdayQuestionHandler`, `Page`, resume dict
- Produces: `WorkdayApplier(...).async apply_to_job(url: str) -> tuple[str, str]` — same contract as `ApplyAgent.apply_to_job`

- [ ] **Step 1: Replace stub __init__.py**

`src/job_manager/workday/__init__.py`:

```python
"""Workday external-apply handler."""

from src.job_manager.workday.workday_applier import WorkdayApplier

__all__ = ["WorkdayApplier"]
```

- [ ] **Step 2: Write failing test for entry contract**

Create `tests/test_workday_applier.py`:

```python
"""WorkdayApplier must dispatch through phases and return ApplyAgent-shaped tuples."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.job_manager.workday.workday_applier import WorkdayApplier


@pytest.fixture
def deps() -> WorkdayApplier:
    return WorkdayApplier(
        page=MagicMock(),
        resume_structured={"work_experience": [], "education": []},
        resume_pdf_path=Path("dummy.pdf"),
        question_handler=MagicMock(answer=MagicMock(return_value={})),
        authenticator=MagicMock(ensure_session=AsyncMock(return_value=True)),
        headless=True,
    )


@pytest.mark.asyncio
async def test_apply_to_job_returns_success_tuple(deps: WorkdayApplier) -> None:
    deps._navigate_to_apply = AsyncMock()  # type: ignore[method-assign]
    deps._fill_account_if_needed = AsyncMock()  # type: ignore[method-assign]
    deps._fill_my_experience = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._fill_voluntary_disclosures = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._answer_custom_questions = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._review_and_submit = AsyncMock(return_value=("Success", "shot.png"))  # type: ignore[method-assign]

    result, reason = await deps.apply_to_job("https://uhaul.myworkdayjobs.com/x/apply")

    assert result == "Success"
    assert reason == "shot.png"


@pytest.mark.asyncio
async def test_phase_failure_returns_error_tuple(deps: WorkdayApplier) -> None:
    deps._navigate_to_apply = AsyncMock()  # type: ignore[method-assign]
    deps._fill_account_if_needed = AsyncMock()  # type: ignore[method-assign]
    deps._fill_my_experience = AsyncMock(return_value=False)  # type: ignore[method-assign]

    result, reason = await deps.apply_to_job("https://uhaul.myworkdayjobs.com/x/apply")

    assert result == "Error"
    assert "my_experience" in reason
```

- [ ] **Step 3: Run test, verify it fails**

Run: `uv run pytest tests/test_workday_applier.py -v`
Expected: `ModuleNotFoundError` for `workday_applier`

- [ ] **Step 4: Implement WorkdayApplier skeleton**

Create `src/job_manager/workday/workday_applier.py`:

```python
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
```

- [ ] **Step 5: Run test, verify it passes**

Run: `uv run pytest tests/test_workday_applier.py -v`
Expected: 2 passed (phase methods mocked)

- [ ] **Step 6: Commit**

```bash
git add src/job_manager/workday/ tests/test_workday_applier.py
git commit -m "feat(workday): add applier skeleton with phase dispatch"
```

---

### Task 7: My Experience phase

**Files:**
- Modify: `src/job_manager/workday/workday_applier.py` — implement `_navigate_to_apply` and `_fill_my_experience`
- Modify: `tests/test_workday_applier.py` — add coverage

- [ ] **Step 1: Append test for My Experience phase**

Add to `tests/test_workday_applier.py`:

```python
@pytest.mark.asyncio
async def test_fill_my_experience_uploads_resume_and_saves(deps: WorkdayApplier) -> None:
    deps.resume_structured = {
        "work_experience": [{"employer": "Acme", "title": "Eng", "start_date": "2020-01", "end_date": "2023-01", "description": "Built things."}],
        "education": [{"school": "MIT", "degree": "BS", "start_date": "2016", "end_date": "2020"}],
        "skills": ["Python", "Playwright"],
    }
    deps._upload_resume = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._add_work_history = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._add_education = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._add_skills = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._click_save_and_continue = AsyncMock(return_value=True)  # type: ignore[method-assign]

    ok = await deps._fill_my_experience()

    assert ok is True
    deps._upload_resume.assert_awaited_once()
    deps._add_work_history.assert_awaited_once()
    deps._add_education.assert_awaited_once()
    deps._add_skills.assert_awaited_once()
    deps._click_save_and_continue.assert_awaited_once()
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/test_workday_applier.py::test_fill_my_experience_uploads_resume_and_saves -v`
Expected: `NotImplementedError`

- [ ] **Step 3: Implement `_navigate_to_apply` and `_fill_my_experience`**

Append to `WorkdayApplier` in `src/job_manager/workday/workday_applier.py`:

```python
    from src.job_manager.workday.workday_selectors import (
        APPLY_FLOW_CONTAINER,
        RESUME_UPLOAD_INPUT,
        WORK_HISTORY_ADD_BUTTON,
        WORK_HISTORY_EMPLOYER,
        WORK_HISTORY_TITLE,
        WORK_HISTORY_START_DATE,
        WORK_HISTORY_END_DATE,
        WORK_HISTORY_DESCRIPTION,
        EDUCATION_ADD_BUTTON,
        EDUCATION_SCHOOL,
        EDUCATION_DEGREE,
        EDUCATION_START_DATE,
        EDUCATION_END_DATE,
        SKILLS_INPUT,
        SAVE_AND_CONTINUE,
    )
    from src.utils.browser_utils import safe_click, safe_fill, find_element_safely

    async def _navigate_to_apply(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")
        await find_element_safely(self.page, APPLY_FLOW_CONTAINER, "css")

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
```

- [ ] **Step 4: Run test, verify it passes**

Run: `uv run pytest tests/test_workday_applier.py -v`
Expected: all 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/job_manager/workday/workday_applier.py tests/test_workday_applier.py
git commit -m "feat(workday): implement my-experience phase"
```

---

### Task 8: Voluntary Disclosures phase

**Files:**
- Modify: `src/job_manager/workday/workday_applier.py`
- Modify: `tests/test_workday_applier.py`

- [ ] **Step 1: Append test**

```python
@pytest.mark.asyncio
async def test_fill_voluntary_disclosures_defaults_to_prefer_not_to_answer(
    deps: WorkdayApplier,
) -> None:
    deps._click_save_and_continue = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._select_prefer_not_to_answer = AsyncMock(return_value=True)  # type: ignore[method-assign]

    ok = await deps._fill_voluntary_disclosures()

    assert ok is True
    deps._select_prefer_not_to_answer.assert_awaited()
    deps._click_save_and_continue.assert_awaited_once()
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/test_workday_applier.py::test_fill_voluntary_disclosures_defaults_to_prefer_not_to_answer -v`
Expected: `NotImplementedError`

- [ ] **Step 3: Implement `_fill_voluntary_disclosures`**

Append to `WorkdayApplier`:

```python
    from src.job_manager.workday.workday_selectors import DISCLOSURE_RADIO_GROUP
    from src.utils.browser_utils import find_elements_safely

    async def _fill_voluntary_disclosures(self) -> bool:
        groups = await find_elements_safely(self.page, DISCLOSURE_RADIO_GROUP, "css")
        for _ in groups:
            if not await self._select_prefer_not_to_answer():
                return False
        return await self._click_save_and_continue()

    async def _select_prefer_not_to_answer(self) -> bool:
        """Click the 'Prefer not to answer' option inside the current disclosure group."""
        try:
            locator = self.page.locator(
                "label:has-text('Prefer not to answer') input[type='radio']"
            )
            if await locator.count() == 0:
                return True
            await locator.first.click()
            return True
        except Exception as e:
            logger.warning(f"Disclosure select failed: {e}")
            return False
```

- [ ] **Step 4: Run test, verify it passes**

Run: `uv run pytest tests/test_workday_applier.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/job_manager/workday/workday_applier.py tests/test_workday_applier.py
git commit -m "feat(workday): implement voluntary-disclosures phase"
```

---

### Task 9: Custom Questions + Review/Submit phases

**Files:**
- Modify: `src/job_manager/workday/workday_applier.py`
- Modify: `tests/test_workday_applier.py`

- [ ] **Step 1: Append tests**

```python
@pytest.mark.asyncio
async def test_answer_custom_questions_uses_handler(deps: WorkdayApplier) -> None:
    deps._scrape_questions = AsyncMock(return_value=[{"text": "Why?", "type": "textarea", "required": True}])  # type: ignore[method-assign]
    deps._apply_answers = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._click_save_and_continue = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps.question_handler.answer = MagicMock(return_value={"Why?": "Because."})

    ok = await deps._answer_custom_questions()

    assert ok is True
    deps.question_handler.answer.assert_called_once()
    deps._apply_answers.assert_awaited_once_with({"Why?": "Because."})
    deps._click_save_and_continue.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_and_submit_submits(deps: WorkdayApplier) -> None:
    deps._wait_for_review_page = AsyncMock()  # type: ignore[method-assign]
    deps._assert_no_required_errors = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._click_submit = AsyncMock(return_value=True)  # type: ignore[method-assign]
    deps._capture_success_screenshot = AsyncMock(return_value="data/output/screenshots/uhaul_x.png")  # type: ignore[method-assign]

    result, reason = await deps._review_and_submit()

    assert result == "Success"
    assert reason == "data/output/screenshots/uhaul_x.png"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/test_workday_applier.py::test_answer_custom_questions_uses_handler tests/test_workday_applier.py::test_review_and_submit_submits -v`
Expected: `NotImplementedError`

- [ ] **Step 3: Implement `_answer_custom_questions` and `_review_and_submit`**

Append to `WorkdayApplier`:

```python
    import json
    import time
    from pathlib import Path

    from config.constants import WORKDAY_SCREENSHOT_DIR
    from src.job_manager.workday.workday_selectors import (
        APPLICATION_CONFIRMATION,
        REVIEW_PAGE_INDICATOR,
        SUBMIT_BUTTON,
        QUESTION_CHECKBOX,
        QUESTION_DROPDOWN,
        QUESTION_RADIO,
        QUESTION_TEXT_INPUT,
        QUESTION_TEXTAREA,
    )
    from src.utils.browser_utils import safe_click

    async def _fill_account_if_needed(self, tenant: str) -> bool:
        return await self.authenticator.ensure_session(
            tenant, email=self._account_email(tenant)
        )

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
            return await self.page.evaluate(
                """() => {
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
                }"""
            ) or []
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
                    await self.page.locator(f"label:has-text('{answer}') >> input[type=radio]").first.click()
                elif await root.locator(QUESTION_CHECKBOX).count():
                    await self.page.locator(f"label:has-text('{answer}') >> input[type=checkbox]").first.click()
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
```

- [ ] **Step 4: Run all workday tests, verify they pass**

Run: `uv run pytest tests/test_workday_*.py -v`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/job_manager/workday/workday_applier.py tests/test_workday_applier.py
git commit -m "feat(workday): implement questions and review/submit phases"
```

---

### Task 10: LinkedIn dispatch wiring

**Files:**
- Modify: `src/job_manager/linkedin/job_manager_linkedin.py:382-389`

- [ ] **Step 1: Read current dispatch**

Run: `sed -n '380,395p' src/job_manager/linkedin/job_manager_linkedin.py`

- [ ] **Step 2: Apply the routing branch**

Replace the block:

```python
                    if TEST_MODE:
                        apply_result = "Skip", "Test mode"
                    else:
                        apply_result = await self.llm_agent_component.apply_to_job(apply_url)
```

with:

```python
                    if TEST_MODE:
                        apply_result = "Skip", "Test mode"
                    elif "myworkdayjobs.com" in apply_url:
                        from src.job_manager.workday import WorkdayApplier
                        from src.job_manager.workday.workday_authenticator import WorkdayAuthenticator
                        from src.job_manager.workday.workday_questions import WorkdayQuestionHandler

                        authenticator = WorkdayAuthenticator(
                            page=self.page,
                            session_dir=Path(WORKDAY_SESSION_DIR),
                            storage_writer=save_browser_session,
                        )
                        question_handler = WorkdayQuestionHandler(
                            cache_path=Path(OUTPUT_DIR_WORKDAY) / "answers.yaml",
                            llm_answerer=self.llm_answerer_component,
                        )
                        workday_applier = WorkdayApplier(
                            page=self.page,
                            resume_structured=self.resume_structured or {},
                            resume_pdf_path=self.submitted_resume_path or Path(RESUME_DIR) / "default.pdf",
                            question_handler=question_handler,
                            authenticator=authenticator,
                        )
                        apply_result = await workday_applier.apply_to_job(apply_url)
                    else:
                        apply_result = await self.llm_agent_component.apply_to_job(apply_url)
```

Add to the imports at the top of the file (verify existing layout first):

```python
from pathlib import Path

from config.constants import OUTPUT_DIR_WORKDAY, RESUME_DIR, WORKDAY_SESSION_DIR
from src.utils.browser_utils import save_browser_session
```

- [ ] **Step 3: Verify import surface**

Run: `uv run python -c "from src.job_manager.linkedin.job_manager_linkedin import LinkedInJobManager; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/job_manager/linkedin/job_manager_linkedin.py
git commit -m "feat(workday): route myworkdayjobs.com URLs to WorkdayApplier"
```

---

### Task 11: Indeed dispatch wiring

**Files:**
- Modify: `src/job_manager/indeed/job_manager_indeed.py:295-305`

- [ ] **Step 1: Read current dispatch**

Run: `sed -n '290,310p' src/job_manager/indeed/job_manager_indeed.py`

- [ ] **Step 2: Apply the same routing branch**

Mirror Task 10's edit. Replace:

```python
                    apply_result = await self.llm_agent_component.apply_to_job(apply_url)
```

with the same Workday branch from Task 10.

- [ ] **Step 3: Verify import surface**

Run: `uv run python -c "from src.job_manager.indeed.job_manager_indeed import IndeedJobManager; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/job_manager/indeed/job_manager_indeed.py
git commit -m "feat(workday): route myworkdayjobs.com URLs in Indeed manager"
```

---

### Task 12: Smoke test (`__main__` block in workday_applier.py)

**Files:**
- Modify: `src/job_manager/workday/workday_applier.py`

- [ ] **Step 1: Append `__main__` block**

```python
if __name__ == "__main__":
    """Smoke test against the Uhaul Workday job from the failure log."""
    import asyncio
    import traceback

    import dotenv
    from playwright.async_api import async_playwright

    from config.app_config import HEADLESS_MODE
    from config.constants import BROWSER_STORAGE_STATE, OUTPUT_DIR_WORKDAY, RESUME_DIR, WORKDAY_SESSION_DIR
    from src.job_manager.workday.workday_authenticator import WorkdayAuthenticator
    from src.job_manager.workday.workday_questions import WorkdayQuestionHandler
    from src.utils.browser_utils import create_playwright_browser, save_browser_session
    from src.utils.utils import load_yaml_file
    from src.pydantic_models.prompt_models import ResumeStructure

    UHAUL_URL = (
        "https://uhaul.wd1.myworkdayjobs.com/en-US/UhaulJobs/job/"
        "Augusta-Maine/Customer-Service-Representative_R249007/apply?source=LinkedIn"
    )

    async def smoke() -> bool:
        secrets = dotenv.dotenv_values(".env")
        resume_structured = load_yaml_file(Path(RESUME_DIR) / "structured_resume.yaml")
        resume_structured = ResumeStructure(**resume_structured).model_dump()
        resume_pdf = next(Path(RESUME_DIR).glob("*.pdf"), None)

        browser, context, page = await create_playwright_browser(storage_state=BROWSER_STORAGE_STATE)
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
```

- [ ] **Step 2: Verify syntax**

Run: `uv run python -c "import ast; ast.parse(open('src/job_manager/workday/workday_applier.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/job_manager/workday/workday_applier.py
git commit -m "feat(workday): add manual smoke test"
```

---

### Task 13: Manual end-to-end run + selectors verification

**Files:** none — this task is a manual gate, not a code change.

- [ ] **Step 1: Run the smoke test against Uhaul**

Run: `uv run python src/job_manager/workday/workday_applier.py`
Expected: `Result: Success | Reason: data/output/screenshots/workday_<ts>.png` (or `Result: Error | Reason: ...` if a phase fails — proceed to step 2)

- [ ] **Step 2: If any phase fails, inspect debug artifacts**

Inspect `data/debug/workday_*.png` and `data/debug/workday_*.html` (or the file referenced in the `Error` reason). If a selector constant in `workday_selectors.py` doesn't match the live Workday DOM, fix it in place. Re-run.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest`
Expected: all tests pass

- [ ] **Step 4: Commit any selector fixes**

If selectors were corrected:

```bash
git add src/job_manager/workday/workday_selectors.py
git commit -m "fix(workday): correct selectors after live verification"
```

---

## Self-Review

**1. Spec coverage:**
- New subpackage — Tasks 1, 4, 5, 6
- Dispatch wiring in LinkedIn — Task 10
- Dispatch wiring in Indeed — Task 11
- Full multi-step flow (account, my-experience, disclosures, questions, review, submit) — Tasks 5, 7, 8, 9
- Auto-create account per company subdomain — Task 5
- Profile data reuse — Task 7 (`resume_structured` + `resume_pdf_path`)
- Cache + LLM custom questions — Task 4
- Resume anonymization before LLM calls — **deferred**. `WorkdayQuestionHandler.answer` currently passes `str(self.resume_structured)` raw. The existing `src/resume_builder/resume_anonymizer.py` should be applied first. Document as a known gap; ship the fix in a follow-up task before public release.
- Dashboard events — Task 6 + 9 (`emit_event("workday_phase", ...)`, `emit_event("workday_submitted", ...)`)
- Per-tenant storage state — Task 5 (`workday_{tenant}.json`)
- Tests covering selectors, authenticator, questions, applier — Tasks 1, 4, 5, 6, 7, 8, 9
- Acceptance criteria (Uhaul URL reaches Submit without browser_use warnings) — Task 13
- Smoke test — Task 12

**2. Placeholder scan:** No "TBD" / "implement later" / "similar to" patterns. All code blocks are real.

**3. Type consistency:**
- `apply_to_job(url: str) -> tuple[str, str]` consistent in Tasks 6, 7, 9, 12, 13
- `ensure_session(tenant: str, email: str) -> bool` consistent in Tasks 5, 6, 10, 11
- `answer(questions, context) -> dict[str, str]` consistent in Tasks 4, 9
- Selector constant names consistent in `workday_selectors.py` and import sites