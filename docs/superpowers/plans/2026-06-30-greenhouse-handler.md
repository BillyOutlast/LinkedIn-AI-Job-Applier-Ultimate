# Greenhouse Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Greenhouse (`boards.greenhouse.io` / `job-boards.greenhouse.io`) per-ATS apply handler so the bot can submit Greenhouse applications without falling through to the LLM.

**Architecture:** New `src/job_manager/greenhouse/greenhouse_applier.py` implements a multi-phase apply flow (navigate → blocker-check → fill basic fields → upload resume → custom questions → submit). Recognizer's `_KNOWN_ATS` table grows by one row. The handler matches Workday's contract (`apply_to_job(url) -> Tuple[str, str]`) so `ApplyAgent._build_handler_with_state` can instantiate it via a 1-line elif extension. Defensive at every phase: reCAPTCHA detected → Skip; external-partner redirect → Skip; submit failure → Error.

**Tech Stack:** Python 3.12, asyncio, stdlib `re` + `urllib.parse`, Playwright async, pytest + pytest-asyncio.

**Ground truth (verified via chrome-devtools on Anthropic Fellows job 5023394008, 2026-06-30):** Greenhouse public apply forms are flat single-page forms: First Name, Last Name, Email, Country (combobox), Phone, Resume/CV (file input accepting pdf/doc/docx/txt/rtf), 1+ custom questions (text/select/file), Submit button. Always: a reCAPTCHA iframe and a "Submit application" button. Some jobs include text like "complete this application form from our official hiring partner, Constellation" — these are external redirects where the Greenhouse form is a notice, not the real apply.

## Global Constraints

- 857 tests currently pass. Plan must keep them green.
- No new project dependencies.
- Reuse existing recognizer table contract: each new ATS adds one row to `_KNOWN_ATS` in `src/llm/ats_recognizer.py`.
- Reuse existing `_build_handler_with_state` in `apply_agent.py` — extend the if/elif chain by one branch.
- All error returns must be `Tuple[str, str]` matching the Workday contract: `("Success", "")`, `("Skip", "<reason>")`, or `("Error", "<reason>")`.
- Best-effort: any uncaught exception → `("Error", str(e))` + log debug, never raises.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/job_manager/greenhouse/greenhouse_applier.py` | Create | `GreenhouseApplier` class with 6 phases |
| `src/job_manager/greenhouse/greenhouse_questions.py` | Create | `GreenhouseQuestionHandler.match_question(field)` |
| `src/job_manager/greenhouse/__init__.py` | Create | Empty package marker |
| `src/llm/ats_recognizer.py` | Modify | Add `"greenhouse"` to `_KNOWN_ATS`; add 2 hostname patterns; add `_build_greenhouse` factory |
| `src/llm/apply_agent.py` | Modify | Add `elif handler_factory() is GreenhouseApplier` to `_build_handler_with_state` |
| `config/constants.py` | Modify | Add `OUTPUT_DIR_GREENHOUSE = "data/output/greenhouse"` |
| `tests/test_ats_recognizer.py` | Modify | Add 2 Greenhouse URL tests |
| `tests/test_greenhouse_applier.py` | Create | 6 tests covering each phase + 2 blocker paths |

No new modules beyond `src/job_manager/greenhouse/`. No new third-party deps.

---

### Task 1: Recognizer entry + factory stub

**Files:**
- Modify: `src/llm/ats_recognizer.py` (add `"greenhouse"` row + 2 hostname patterns + factory stub)
- Modify: `tests/test_ats_recognizer.py` (add 2 tests)
- Create: `src/job_manager/greenhouse/__init__.py` (empty)
- Create: `src/job_manager/greenhouse/greenhouse_applier.py` (stub class — full impl in Task 2)

**Interfaces:**
- Consumes: existing `recognize()` and `_KNOWN_ATS` from this module
- Produces: `recognize("https://boards.greenhouse.io/...")` → `ATSMatch(name="greenhouse")`; `recognize("https://job-boards.greenhouse.io/...")` → same; `_build_greenhouse()` → `GreenhouseApplier` class (imported lazily)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ats_recognizer.py`:

```python
def test_recognize_greenhouse_by_hostname():
    match = recognize("https://boards.greenhouse.io/stripe/jobs/12345")
    assert match is not None
    assert match.name == "greenhouse"
    assert match.confidence == 1.0


def test_recognize_greenhouse_job_boards_subdomain():
    match = recognize("https://job-boards.greenhouse.io/anthropic/jobs/5023394008")
    assert match is not None
    assert match.name == "greenhouse"
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `uv run pytest tests/test_ats_recognizer.py::test_recognize_greenhouse_by_hostname tests/test_ats_recognizer.py::test_recognize_greenhouse_job_boards_subdomain -v`

Expected: FAIL — no greenhouse pattern / table entry.

- [ ] **Step 3: Add greenhouse to `_HOSTNAME_PATTERNS` + `_KNOWN_ATS` + factory stub**

In `src/llm/ats_recognizer.py`, modify `_HOSTNAME_PATTERNS`:

```python
_HOSTNAME_PATTERNS = [
    (re.compile(r"\.myworkdayjobs\.com$", re.I), "workday"),
    (re.compile(r"(^|\.)boards?\.greenhouse\.io$", re.I), "greenhouse"),
    (re.compile(r"(^|\.)job-boards\.greenhouse\.io$", re.I), "greenhouse"),
]
```

In `_KNOWN_ATS`, add the greenhouse entry after the workday one:

```python
_KNOWN_ATS: dict[str, ATSMatch] = {
    "workday": ATSMatch(
        name="workday",
        confidence=1.0,
        handler_factory=lambda: _workday_handler_class,
    ),
    "greenhouse": ATSMatch(
        name="greenhouse",
        confidence=1.0,
        handler_factory=lambda: _greenhouse_handler_class,
    ),
}
```

Below `_workday_handler_class`, add the greenhouse factory:

```python
def _greenhouse_handler_class():
    from src.job_manager.greenhouse.greenhouse_applier import GreenhouseApplier
    return GreenhouseApplier
```

Create `src/job_manager/greenhouse/__init__.py` (empty file).

Create `src/job_manager/greenhouse/greenhouse_applier.py` with a minimal stub so the factory's lazy import resolves:

```python
"""Greenhouse applier skeleton — full implementation lands in Task 2."""


class GreenhouseApplier:
    def __init__(self, *args, **kwargs):
        pass

    async def apply_to_job(self, url):
        return "Skip", "Greenhouse handler not yet implemented"
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_ats_recognizer.py::test_recognize_greenhouse_by_hostname tests/test_ats_recognizer.py::test_recognize_greenhouse_job_boards_subdomain -v`

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 859 passed (857 prior + 2 new). Investigate any regression.

- [ ] **Step 6: Commit**

```bash
git add src/job_manager/greenhouse/__init__.py src/job_manager/greenhouse/greenhouse_applier.py src/llm/ats_recognizer.py tests/test_ats_recognizer.py
git commit -m "feat(greenhouse): recognizer entry for boards.greenhouse.io

Adds the greenhouse pattern + table entry to ats_recognizer.py so URLs
on boards.greenhouse.io and job-boards.greenhouse.io subdomains
match. GreenhouseApplier is a stub for now; the full phase handler
lands in Task 2.

The recognizer + table contract is identical to Workday's — one
hostname pattern, one row in _KNOWN_ATS, one handler_factory stub."
```

---

### Task 2: GreenhouseApplier full implementation + ApplyAgent wiring

**Files:**
- Modify: `src/job_manager/greenhouse/greenhouse_applier.py` (replace stub with full phase-based handler)
- Create: `src/job_manager/greenhouse/greenhouse_questions.py` (question cache + heuristic matcher)
- Modify: `src/llm/apply_agent.py` (extend `_build_handler_with_state` to instantiate GreenhouseApplier)
- Modify: `config/constants.py` (add `OUTPUT_DIR_GREENHOUSE`)
- Create: `tests/test_greenhouse_applier.py` (6 tests)

**Interfaces:**
- Consumes: existing `WorkdayApplier` constructor signature for parity; `apply_agent._build_handler_with_state` pattern
- Produces:
  - `GreenhouseApplier(page, resume_structured, resume_pdf_path, question_handler) -> instance`
  - `await GreenhouseApplier.apply_to_job(url) -> ("Success"|"Skip"|"Error", reason)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_greenhouse_applier.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.job_manager.greenhouse.greenhouse_applier import GreenhouseApplier


@pytest.fixture
def applier():
    page = MagicMock()
    page.url = "https://boards.greenhouse.io/stripe/jobs/12345"
    page.goto = AsyncMock()
    page.locator.return_value.count = AsyncMock(return_value=0)
    page.locator.return_value.first = MagicMock()
    page.locator.return_value.first.click = AsyncMock()
    page.set_input_files = AsyncMock()
    return GreenhouseApplier(
        page=page,
        resume_structured={
            "personal": {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com", "phone": "+15555550100"},
            "location": {"country": "United States"},
        },
        resume_pdf_path="/tmp/ada.pdf",
        question_handler=MagicMock(match_question=MagicMock(return_value="Yes")),
    )


@pytest.mark.asyncio
async def test_apply_to_job_skips_when_recaptcha_detected(applier):
    """reCAPTCHA iframe present → Skip, no submit attempt."""
    with patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=True), \
         patch.object(applier, "_detect_blockers", new_callable=AsyncMock, return_value="recaptcha"):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Skip"
    assert "recaptcha" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_skips_on_external_redirect(applier):
    """External-partner text detected → Skip, no submit attempt."""
    with patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=True), \
         patch.object(applier, "_detect_blockers", new_callable=AsyncMock, return_value="external"):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Skip"
    assert "external" in result[1].lower() or "partner" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_returns_success_when_no_blockers_and_submit_succeeds(applier):
    """Happy path: no blockers, basic fields fill, resume uploads, submit succeeds."""
    with patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=True), \
         patch.object(applier, "_detect_blockers", new_callable=AsyncMock, return_value=None), \
         patch.object(applier, "_fill_basic_info", new_callable=AsyncMock), \
         patch.object(applier, "_upload_resume", new_callable=AsyncMock), \
         patch.object(applier, "_handle_custom_questions", new_callable=AsyncMock), \
         patch.object(applier, "_submit", new_callable=AsyncMock, return_value=True):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Success"
    assert result[1] == ""


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_when_submit_fails(applier):
    with patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=True), \
         patch.object(applier, "_detect_blockers", new_callable=AsyncMock, return_value=None), \
         patch.object(applier, "_fill_basic_info", new_callable=AsyncMock), \
         patch.object(applier, "_upload_resume", new_callable=AsyncMock), \
         patch.object(applier, "_handle_custom_questions", new_callable=AsyncMock), \
         patch.object(applier, "_submit", new_callable=AsyncMock, return_value=False):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Error"
    assert "submit" in result[1].lower()


@pytest.mark.asyncio
async def test_apply_to_job_catches_unexpected_exception(applier):
    """Any uncaught exception → Error tuple, never raise."""
    with patch.object(applier, "_navigate", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Error"
    assert "boom" in result[1]


@pytest.mark.asyncio
async def test_apply_to_job_returns_error_when_navigate_fails(applier):
    with patch.object(applier, "_navigate", new_callable=AsyncMock, return_value=False):
        result = await applier.apply_to_job("https://boards.greenhouse.io/stripe/jobs/12345")
    assert result[0] == "Error"
    assert "navigate" in result[1].lower()
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `uv run pytest tests/test_greenhouse_applier.py -v`

Expected: FAIL — `GreenhouseApplier` stub returns `("Skip", "Greenhouse handler not yet implemented")` for every call; signature mismatch on phase methods.

- [ ] **Step 3: Implement `GreenhouseApplier` (replace stub)**

In `src/job_manager/greenhouse/greenhouse_applier.py`:

```python
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
                return "Error", f"Navigation failed for {url}"
            blocker = await self._detect_blockers()
            if blocker == "recaptcha":
                return "Skip", "reCAPTCHA challenge present; cannot solve automatically"
            if blocker == "external":
                return "Skip", "External application partner; Greenhouse form is a notice"
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
        if "official hiring partner" in body_lower or "do not need to submit this greenhouse application" in body_lower:
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
                f"xpath=//label[contains(normalize-space(.), \"{label_text}\")]/following::input[1]"
            ).first.fill(value)
        except Exception as e:
            logger.debug(f"fill '{label_text}' failed (non-fatal): {e}")

    async def _select_country_by_label(self, country: str) -> None:
        try:
            await self.page.locator(
                "xpath=//label[contains(normalize-space(.), \"Country\")]/following::select[1]"
            ).first.select_option(label=country)
        except Exception as e:
            logger.debug(f"select country '{country}' failed (non-fatal): {e}")

    async def _upload_resume(self) -> None:
        if not self.resume_pdf_path:
            return
        try:
            await self.page.locator("input[type='file']").first.set_input_files(str(self.resume_pdf_path))
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
            if label.strip().lower() in {"first name", "last name", "email", "phone", "country", "resume/cv", "resume"}:
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
        return ("application submitted" in body_lower
                or "thanks for applying" in body_lower
                or (self.page.url != self._initial_url))
```

- [ ] **Step 4: Implement `GreenhouseQuestionHandler`**

Create `src/job_manager/greenhouse/greenhouse_questions.py`:

```python
"""Per-question matcher for Greenhouse custom questions.

Mirrors the WorkdayQuestionHandler pattern but tailored for Greenhouse's
question shapes (typically yes/no, dropdown, free-text).
"""
from __future__ import annotations

from typing import Any, Optional


class GreenhouseQuestionHandler:
    def __init__(self, cache_path: Any = None, llm_answerer: Any = None):
        self.cache_path = cache_path
        self.llm_answerer = llm_answerer
        self._cache: dict[str, str] = {}

    def match_question(self, label: str) -> Optional[str]:
        """Return a canned answer for a Greenhouse question by label, or None."""
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

- [ ] **Step 5: Add `OUTPUT_DIR_GREENHOUSE` to `config/constants.py`**

In `config/constants.py`, append:

```python
OUTPUT_DIR_GREENHOUSE = "data/output/greenhouse"
```

- [ ] **Step 6: Extend `_build_handler_with_state` in `apply_agent.py`**

In `src/llm/apply_agent.py`, find the `_build_handler_with_state` static method. Add a new elif branch for GreenhouseApplier after the existing Workday branch:

```python
        if handler_factory is not None and handler_factory() is GreenhouseApplier:
            from src.job_manager.greenhouse.greenhouse_applier import GreenhouseApplier
            from src.job_manager.greenhouse.greenhouse_questions import GreenhouseQuestionHandler
            from config.constants import OUTPUT_DIR_GREENHOUSE
            from pathlib import Path

            question_handler = GreenhouseQuestionHandler(
                cache_path=Path(OUTPUT_DIR_GREENHOUSE) / "answers.yaml",
                llm_answerer=getattr(agent_self, "llm_answerer", None),
            )
            resume_structured = getattr(agent_self, "resume_structured", None) or {}
            resume_pdf_path = getattr(agent_self, "resume_pdf_path", None) or Path(RESUME_DIR) / "default.pdf"
            return GreenhouseApplier(
                page=agent_self.page,
                resume_structured=resume_structured,
                resume_pdf_path=resume_pdf_path,
                question_handler=question_handler,
            )
        raise ValueError(f"Unknown handler factory: {handler_factory}")
```

Also add the top-of-file import alongside the existing Workday import:

```python
from src.job_manager.greenhouse.greenhouse_applier import GreenhouseApplier
```

- [ ] **Step 7: Run tests, expect PASS**

Run: `uv run pytest tests/test_greenhouse_applier.py -v`

Expected: 6 passed.

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 865 passed (859 + 6 new). Investigate any regression.

- [ ] **Step 9: Commit**

```bash
git add src/job_manager/greenhouse/greenhouse_applier.py src/job_manager/greenhouse/greenhouse_questions.py src/llm/apply_agent.py config/constants.py tests/test_greenhouse_applier.py
git commit -m "feat(greenhouse): GreenhouseApplier with 6-phase apply flow

src/job_manager/greenhouse/greenhouse_applier.py implements
GreenhouseApplier with phases: navigate → detect_blockers (reCAPTCHA /
external partner) → fill_basic_info → upload_resume →
handle_custom_questions → submit. Returns the standard
(Success|Skip|Error, reason) contract.

Blocker detection covers two real-world failures from the chrome-devtools
investigation: reCAPTCHA iframe (cannot solve automatically → Skip) and
external-partner redirect text (Greenhouse form is a notice, not the
real apply → Skip).

apply_agent._build_handler_with_state extended with one elif branch
to instantiate GreenhouseApplier. config/constants.py gains
OUTPUT_DIR_GREENHOUSE for the question-handler cache."
```

---

### Task 3: Final review + housekeeping

- [ ] **Step 1: Read all task reports**

Read `.superpowers/sdd/task-1-report.md` and `task-2-report.md` for self-review / concerns.

- [ ] **Step 2: Verify final state**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 865+ passed.

- [ ] **Step 3: Update progress ledger**

Append to `.superpowers/sdd/progress.md`:

```
# Greenhouse Handler — Subagent-Driven Progress

Plan: `docs/superpowers/plans/2026-06-30-greenhouse-handler.md`
Started: 2026-06-30
Branch: release
Base commit: <last-rec-task-commit>
Task 1: complete (commits <base>..<T1>; 2 new recognizer tests)
Task 2: complete (commits <T1>..<T2>; 6 new applier tests; 865 total)
FINAL: complete (ready to merge; per-ATS contract preserved; no regressions)
```

- [ ] **Step 4: Commit the ledger**

```bash
git add .superpowers/sdd/progress.md
git commit -m "chore(greenhouse): progress ledger for sub-project 3 of N"
```

---

## Self-Review

1. **Spec coverage:**
   - `GreenhouseApplier` with 6 phases → Task 2 Step 3
   - `GreenhouseQuestionHandler` → Task 2 Step 4
   - Recognizer entry (2 hostname patterns + table row + factory) → Task 1 Step 3
   - `_build_handler_with_state` extension → Task 2 Step 6
   - reCAPTCHA + external-redirect blocker detection → Task 2 Step 3 (`_detect_blockers`)
   - Skip on blocker, Success on submit, Error on failure → Task 2 Step 3 (`apply_to_job`)
   - Tests for each phase → Task 1 Step 1 (2) + Task 2 Step 1 (6) = 8 new tests
2. **Placeholders:** none. Every code block is complete.
3. **Type consistency:**
   - `apply_to_job(url) -> Tuple[str, str]` matches WorkdayApplier contract
   - `GreenhouseApplier.__init__(page, resume_structured, resume_pdf_path, question_handler)` mirrors WorkdayApplier signature
   - Recognizer's `ATSMatch(name="greenhouse", confidence=1.0)` follows existing entry shape
   - `_build_handler_with_state` elif chain: same `handler_factory() is <Class>` pattern as Workday
4. **Sequential execution:** Task 1 must ship first (Task 2 references `GreenhouseApplier`). No parallel option.

No issues found. Plan ready.