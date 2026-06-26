# Job Applier Full Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three core issues preventing job applications from being submitted: company dedup blocking everything, form filler not handling dropdowns/radios, and SIGINT killing the browser mid-run.

**Architecture:** Three focused changes across 6 files. Company dedup refactor separates "attempted" from "successfully applied". Form handler expands from text-only to all LinkedIn field types. Graceful shutdown uses a state machine to finish the current job before exiting.

**Tech Stack:** Python 3.12, Playwright async API, Pydantic models

## Global Constraints

- Python 3.12+
- Playwright async API (not sync)
- All config in `config/app_config.py`
- Logging via `config.logger_config.logger`
- Run with `uv run python`

---

### Task 1: Add Config Values

**Files:**
- Modify: `config/app_config.py`

**Interfaces:**
- Produces: `RESET_SEEN_COMPANIES` (bool), `FORM_ERROR_MAX_RETRIES` (int)

- [ ] **Step 1: Add RESET_SEEN_COMPANIES**

In `config/app_config.py`, after the `READY_MADE_PHOTO_PATH` block (around line 93), add:

```python
"""
If True, clears skipped.yaml and failed.yaml on startup (keeps success.yaml).
Useful after refining search criteria to re-attempt previously skipped/failed companies.
"""
RESET_SEEN_COMPANIES = False
```

- [ ] **Step 2: Add FORM_ERROR_MAX_RETRIES**

In `config/app_config.py`, after the `TEMPERATURE` block (around line 174), add:

```python
"""Max retry attempts for form submission errors in Easy Apply"""
FORM_ERROR_MAX_RETRIES = 5
```

- [ ] **Step 3: Commit**

```bash
git add config/app_config.py
git commit -m "feat: add RESET_SEEN_COMPANIES and FORM_ERROR_MAX_RETRIES config"
```

---

### Task 2: Refactor Company Dedup Logic

**Files:**
- Modify: `src/job_manager/job_manager.py:337-356` (`_job_is_already_seen`)
- Modify: `src/job_manager/job_manager.py:52-70` (`set_parameters` — add RESET_SEEN_COMPANIES logic)

**Interfaces:**
- Consumes: `RESET_SEEN_COMPANIES` from `config/app_config.py`
- Produces: Modified `_job_is_already_seen` that only checks `success_companies` for company-wide skip

- [ ] **Step 1: Add import for RESET_SEEN_COMPANIES**

In `src/job_manager/job_manager.py` line 10, change:

```python
from config.app_config import COLLECT_INFO_MODE, JOB_SITE, MAX_APPLIES_NUM, TEST_MODE
```

to:

```python
from config.app_config import COLLECT_INFO_MODE, JOB_SITE, MAX_APPLIES_NUM, RESET_SEEN_COMPANIES, TEST_MODE
```

- [ ] **Step 2: Add RESET_SEEN_COMPANIES logic in set_parameters**

In `src/job_manager/job_manager.py`, after line 61 (`self.failed_companies = self._load_companies_from_yaml("failed.yaml")`), add:

```python
        if RESET_SEEN_COMPANIES:
            logger.info("RESET_SEEN_COMPANIES is enabled — clearing skipped and failed company lists")
            self.skipped_companies = {}
            self.failed_companies = {}
```

- [ ] **Step 3: Refactor _job_is_already_seen**

Replace the entire `_job_is_already_seen` method (lines 337-356) with:

```python
    def _job_is_already_seen(self, job: Job) -> Tuple[bool, str]:
        """Check if we have already applied to this vacancy"""
        company_name = job.company_name
        job_title = job.job_title
        if COLLECT_INFO_MODE is True:
            my_companies = self.interesting_jobs
            for job_info in my_companies:
                if company_name == job_info.company_name and job_title == job_info.job_title:
                    logger.warning("The vacancy has already been encountered, skipping")
                    return True, "The vacancy has already been encountered"
        else:
            # Company-wide skip: ONLY check success_companies
            # (skipped/failed companies are not blockers — those jobs were never applied to)
            if self.apply_once_at_company:
                is_seen, reason = self._match_seen_jobs(job, self.success_companies)
                if is_seen:
                    return True, reason

            # Exact job-title match: check ALL three lists
            for companies in (
                self.success_companies,
                self.skipped_companies,
                self.failed_companies,
            ):
                for comp in companies:
                    if sanitize_text(company_name) == sanitize_text(comp):
                        for job_info in companies[comp]:
                            if job_title == job_info["job_title"]:
                                logger.warning("The vacancy has already been encountered, skipping")
                                return True, "The vacancy has already been encountered"
        return False, ""
```

- [ ] **Step 4: Commit**

```bash
git add src/job_manager/job_manager.py
git commit -m "feat: refactor company dedup to only block on successful applications"
```

---

### Task 3: Expand Form Element Handler

**Files:**
- Modify: `src/job_manager/linkedin/easy_applier_linkedin.py:360-387` (`_check_and_fix_errors`)
- Modify: `src/job_manager/linkedin/easy_applier_linkedin.py:1916-1940` (`_fill_textbox_question_errors`)
- Add methods after `_fill_textbox_question_errors` (before the `if __name__` block at line 1943)

**Interfaces:**
- Consumes: `FORM_ERROR_MAX_RETRIES` from `config/app_config.py`, `find_elements_safely` from `src.utils.browser_utils`
- Produces: `_fill_form_errors` method that handles select, radio, checkbox, text, file field types

- [ ] **Step 1: Add import for FORM_ERROR_MAX_RETRIES**

In `src/job_manager/linkedin/easy_applier_linkedin.py`, find the imports section. Add `FORM_ERROR_MAX_RETRIES` to the existing config import. The file imports from `config.app_config` — find the line and add `FORM_ERROR_MAX_RETRIES` to that import.

- [ ] **Step 2: Update retry limit in _check_and_fix_errors**

In `src/job_manager/linkedin/easy_applier_linkedin.py` line 367, change:

```python
        while attempt < 3:
```

to:

```python
        while attempt < FORM_ERROR_MAX_RETRIES:
```

- [ ] **Step 3: Replace _fill_textbox_question_errors call with _fill_form_errors**

In `src/job_manager/linkedin/easy_applier_linkedin.py` line 371, change:

```python
                await self._fill_textbox_question_errors()
```

to:

```python
                await self._fill_form_errors()
```

- [ ] **Step 4: Add _fill_form_errors method**

After the `_fill_textbox_question_errors` method (after line 1940), before the `if __name__` block, add:

```python
    async def _fill_form_errors(self) -> bool:
        """Find form fields with validation errors and fill them based on field type (async).
        Handles text, select/dropdown, radio, checkbox, and file fields.
        Falls back to _fill_textbox_question_errors for text-specific LLM answering.
        """
        logger.debug("Searching for form errors across all field types")

        form_container_selectors = [
            "xpath=.//*[contains(@class, 'fb-dash-form-element')]",
            "div[data-test-form-element]",
            "xpath=.//*[contains(@class, 'jobs-easy-apply-form-section__group')]",
        ]

        form_containers = []
        for selector in form_container_selectors:
            try:
                form_containers = await self.page.locator(selector).all()
                if form_containers:
                    logger.debug(f"Found {len(form_containers)} form containers with selector: {selector}")
                    break
            except Exception:
                continue

        if not form_containers:
            logger.debug("No form containers found")
            return False

        filled_any = False
        for section in form_containers:
            # Check for error in this section
            error_text = ""
            try:
                error_selectors = [
                    ".artdeco-inline-feedback--error .artdeco-inline-feedback__message",
                    ".artdeco-inline-feedback--error",
                    "[role='alert'][data-test-form-element-error-messages]",
                ]
                for sel in error_selectors:
                    loc = section.locator(sel)
                    cand_data = await loc.evaluate_all(
                        "els => els.map((e, i) => ({i, visible: e.offsetParent !== null, text: e.textContent?.trim() || ''}))"
                    )
                    match = next((d for d in cand_data if d["visible"] and d["text"]), None)
                    if match:
                        error_text = match["text"]
                        break
            except Exception:
                continue

            if not error_text:
                continue

            logger.info(f"Found form error in section: {error_text}")

            # Find the field in this section
            field = await self._find_field_in_section(section)
            if not field:
                logger.debug("No fillable field found in errored section, skipping")
                continue

            field_type = await self._detect_field_type(field)
            logger.debug(f"Detected field type: {field_type}")

            try:
                if field_type == "select":
                    await self._fill_dropdown(field)
                    filled_any = True
                elif field_type == "radio":
                    await self._fill_radio(field)
                    filled_any = True
                elif field_type == "checkbox":
                    await self._fill_checkbox(field)
                    filled_any = True
                elif field_type in ("text", "textarea"):
                    # Use existing LLM-based text filler
                    await self._fill_textbox_error(section, field, error_text)
                    filled_any = True
                elif field_type == "file":
                    logger.warning("File upload error detected but cannot auto-fill")
                else:
                    logger.warning(f"Unknown field type '{field_type}', skipping")
            except Exception as e:
                logger.warning(f"Failed to fill {field_type} field: {e}")

        # Also try the existing textbox-specific handler for any remaining text errors
        if not filled_any:
            filled_any = await self._fill_textbox_question_errors()

        return filled_any

    async def _find_field_in_section(self, section: Any) -> Any:
        """Find the first fillable field in a form section (async)"""
        field_selectors = [
            "select",
            "input[type='radio']",
            "input[type='checkbox']",
            "input[type='text']",
            "input[type='number']",
            "textarea",
            "[role='listbox']",
            ".artdeco-text-input--input",
        ]
        for selector in field_selectors:
            try:
                loc = section.locator(selector)
                count = await loc.count()
                if count > 0:
                    # Return first visible one
                    for i in range(count):
                        el = loc.nth(i)
                        if await el.is_visible():
                            return el
            except Exception:
                continue
        return None

    async def _detect_field_type(self, field: Any) -> str:
        """Detect the type of a form field (async)"""
        try:
            tag = await field.evaluate("el => el.tagName.toLowerCase()")
            input_type = await field.evaluate("el => el.type || ''")

            if tag == "select":
                return "select"
            if await field.evaluate("el => el.getAttribute('role') === 'listbox'"):
                return "select"
            if tag == "input" and input_type == "radio":
                return "radio"
            if tag == "input" and input_type == "checkbox":
                return "checkbox"
            if tag == "input" and input_type == "file":
                return "file"
            if tag == "textarea":
                return "textarea"
            if tag == "input" and input_type in ("text", "number", ""):
                return "text"
        except Exception as e:
            logger.debug(f"Error detecting field type: {e}")
        return "unknown"

    async def _fill_dropdown(self, field: Any) -> None:
        """Fill a dropdown/select field with a smart default (async)"""
        try:
            options = await field.evaluate("""
                el => Array.from(el.options || el.querySelectorAll('option'))
                    .map(o => ({value: o.value, text: o.textContent.trim(), disabled: o.disabled}))
            """)
            valid_options = [o for o in options if o["value"] and not o["disabled"] and o["text"]]

            if not valid_options:
                logger.warning("No valid options found in dropdown")
                return

            selected = None

            # 1. Yes/No dropdowns → pick the positive option
            for opt in valid_options:
                if opt["text"].lower() in ("yes", "yes, i do", "i agree", "true"):
                    selected = opt
                    break

            # 2. Required field with few options → pick first non-empty
            if not selected and len(valid_options) <= 5:
                selected = valid_options[0]

            # 3. Fallback → first option
            if not selected:
                selected = valid_options[0]

            logger.info(f"Selected dropdown value: {selected['text']}")
            await field.select_option(value=selected["value"])
        except Exception as e:
            logger.warning(f"Failed to fill dropdown: {e}")

    async def _fill_radio(self, field: Any) -> None:
        """Select the first available radio button in the group (async)"""
        try:
            group_name = await field.evaluate("el => el.name")
            if not group_name:
                return

            radios = await find_elements_safely(
                self.page,
                f"input[type='radio'][name='{group_name}']",
                "css",
            )

            for radio in radios:
                if not await radio.is_disabled():
                    await radio.click(timeout=1000)
                    label = await radio.evaluate(
                        "el => el.labels?.[0]?.textContent?.trim() || el.value"
                    )
                    logger.info(f"Selected radio option: {label}")
                    return
        except Exception as e:
            logger.warning(f"Failed to fill radio field: {e}")

    async def _fill_checkbox(self, field: Any) -> None:
        """Check required consent checkboxes (async)"""
        try:
            is_checked = await field.is_checked()
            if is_checked:
                return

            is_required = await field.evaluate("""
                el => el.required || el.getAttribute('aria-required') === 'true'
                    || !!el.closest('.artdeco-form-item--bordered')
            """)

            if is_required:
                await field.check(timeout=1000)
                label = await field.evaluate(
                    "el => el.labels?.[0]?.textContent?.trim() || 'unknown'"
                )
                logger.info(f"Checked required checkbox: {label}")
        except Exception as e:
            logger.warning(f"Failed to fill checkbox: {e}")

    async def _fill_textbox_error(self, section: Any, field: Any, error_text: str) -> None:
        """Fill a textbox field that has a validation error using LLM (async)"""
        try:
            # Extract question text from label
            question_text = ""
            label_selectors = [
                "label",
                ".fb-dash-form-element__label",
                ".artdeco-text-input--label",
            ]
            for selector in label_selectors:
                labels = await find_elements_safely(section, selector, "css selector")
                if labels:
                    question_text = (await labels[0].text_content() or "").lower().strip()
                    question_text = self._deduplicate_question_text(question_text)
                    break

            if not question_text:
                alt = await field.get_attribute("aria-label") or await field.get_attribute("placeholder")
                if alt:
                    question_text = alt.strip()

            logger.info(f"Answering textbox question with error: {question_text}. Error: {error_text}")
            answer = self.gpt_answerer.answer_question_textual_wide_range_with_error(
                question_text,
                error_text,
                await field.get_attribute("value"),
                self.previous_question_texts[:-1],
            )
            if self._is_no_info_answer(answer):
                raise NoInfoException(
                    f"Can't fix error: {error_text}. No info found for question: {question_text}"
                )
            answer = self.resume_anonymizer.deanonymize_text(answer)
            await field.fill(answer)
            await self._process_autocomplete_suggestions(field)
            self._save_questions(
                Question(question_type="text", question=question_text, answer=answer)
            )
        except NoInfoException:
            raise
        except Exception as e:
            logger.warning(f"Failed to fill textbox error: {e}")
```

- [ ] **Step 5: Commit**

```bash
git add src/job_manager/linkedin/easy_applier_linkedin.py
git commit -m "feat: expand form handler to support dropdowns, radios, checkboxes"
```

---

### Task 4: Add Graceful Shutdown State Machine

**Files:**
- Modify: `src/utils/runtime_control.py:31-34` (`request_shutdown` method)
- Add: `ShutdownState` enum and `get_shutdown_state` function

**Interfaces:**
- Produces: `ShutdownState` enum, `get_shutdown_state()` function
- Consumed by: `job_manager_linkedin.py` (Task 5)

- [ ] **Step 1: Add ShutdownState enum**

In `src/utils/runtime_control.py`, after the imports (after line 8), add:

```python
from enum import Enum


class ShutdownState(Enum):
    """State machine for graceful shutdown"""
    RUNNING = "running"
    DRAINING = "draining"    # Current job finishing, no new jobs started
    CLEANUP = "cleanup"      # Browser cleanup in progress
    DONE = "done"


_shutdown_state = ShutdownState.RUNNING


def get_shutdown_state() -> ShutdownState:
    """Get the current shutdown state"""
    return _shutdown_state
```

- [ ] **Step 2: Update request_shutdown to use state machine**

Replace the `request_shutdown` method in `RuntimeController` (lines 31-34) with:

```python
    def request_shutdown(self, source: str) -> None:
        global _shutdown_state
        if _shutdown_state == ShutdownState.RUNNING:
            _shutdown_state = ShutdownState.DRAINING
            logger.warning(f"Shutdown requested via {source} — finishing current job before exit")
        elif not self.shutdown_requested.is_set():
            logger.warning(f"Shutdown requested via {source}. Finishing current cleanup.")
        self.shutdown_requested.set()
```

- [ ] **Step 3: Commit**

```bash
git add src/utils/runtime_control.py
git commit -m "feat: add ShutdownState enum for graceful shutdown"
```

---

### Task 5: Wire Shutdown State into Job Loop

**Files:**
- Modify: `src/job_manager/linkedin/job_manager_linkedin.py:225-259` (job loop in `start_applying`)
- Modify: `src/job_manager/linkedin/job_manager_linkedin.py:394-412` (`apply_job` finally block)

**Interfaces:**
- Consumes: `get_shutdown_state`, `ShutdownState` from `src.utils.runtime_control`

- [ ] **Step 1: Add import**

In `src/job_manager/linkedin/job_manager_linkedin.py`, after the existing imports from `src.dashboard.runtime` (line 21), add:

```python
from src.utils.runtime_control import ShutdownState, get_shutdown_state
```

- [ ] **Step 2: Add shutdown check in job loop**

In `src/job_manager/linkedin/job_manager_linkedin.py`, inside the `for vacancy in vacancies:` loop (around line 225), add a shutdown check at the start of the loop body. After line 228 (`await self.pause_checker()`), add:

```python
                # Check if shutdown was requested — stop starting new jobs
                if get_shutdown_state() == ShutdownState.DRAINING:
                    logger.info("Shutdown requested, finishing current job then stopping")
                    result = "Shutdown"
                    break
```

- [ ] **Step 3: Add shutdown check in apply_job finally block**

In `src/job_manager/linkedin/job_manager_linkedin.py`, in the `apply_job` method's finally block (around line 394), modify the `bring_to_front` section. Find:

```python
            try:
                await self.page.bring_to_front()
            except Exception as e:
                if self._is_target_closed_error(e):
                    logger.warning("Browser was closed before returning to the search page")
                else:
                    raise
```

Replace with:

```python
            if get_shutdown_state() != ShutdownState.DRAINING:
                try:
                    await self.page.bring_to_front()
                except Exception as e:
                    if self._is_target_closed_error(e):
                        logger.warning("Browser was closed before returning to the search page")
                    else:
                        raise
```

- [ ] **Step 4: Handle "Shutdown" result in the outer loop**

In `src/job_manager/linkedin/job_manager_linkedin.py`, after the `if result == "Limit"` check (around line 258), add:

```python
            if result == "Limit" or result == "Error" or result == "Shutdown":
                break
```

Replace the existing line:

```python
            if result == "Limit" or result == "Error":
                break
```

- [ ] **Step 5: Commit**

```bash
git add src/job_manager/linkedin/job_manager_linkedin.py
git commit -m "feat: wire shutdown state into job loop for graceful exit"
```

---

### Task 6: Update main.py Cleanup Block

**Files:**
- Modify: `main.py:385-404` (finally block in `create_and_run_bot`)

**Interfaces:**
- Consumes: `ShutdownState`, `get_shutdown_state` from `src.utils.runtime_control`

- [ ] **Step 1: Update the finally block**

In `main.py`, find the `finally:` block in `create_and_run_bot` (around line 385). Replace the cleanup section with:

```python
    finally:
        # Cleanup browser resources
        from src.utils.runtime_control import ShutdownState
        import src.utils.runtime_control as rc

        logger.info("Cleaning up browser resources...")
        rc._shutdown_state = ShutdownState.CLEANUP
        try:
            if context is not None:
                await save_browser_session(context)
                await stop_tracing(context)
            # Close Playwright browser (browser is None when using persistent context)
            if browser is not None:
                await browser.close()
            elif context is not None:
                await context.close()
            logger.info("Playwright browser closed")
            emit_event("browser_closed", "Playwright browser closed")

        except Exception as e:
            logger.warning(f"Error during browser cleanup: {e}")
        finally:
            rc._shutdown_state = ShutdownState.DONE
            # Local runtime patch: release any pending shutdown handler waits.
            runtime_controller.finish_run()
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat: update cleanup block to use shutdown state machine"
```

---

### Task 7: Verify and Test

**Files:**
- None (verification only)

- [ ] **Step 1: Run existing tests**

```bash
uv run pytest tests/ -v
```

Expected: All existing tests pass (no regressions).

- [ ] **Step 2: Syntax check all modified files**

```bash
uv run python -c "import py_compile; py_compile.compile('config/app_config.py', doraise=True)"
uv run python -c "import py_compile; py_compile.compile('src/job_manager/job_manager.py', doraise=True)"
uv run python -c "import py_compile; py_compile.compile('src/job_manager/linkedin/easy_applier_linkedin.py', doraise=True)"
uv run python -c "import py_compile; py_compile.compile('src/utils/runtime_control.py', doraise=True)"
uv run python -c "import py_compile; py_compile.compile('src/job_manager/linkedin/job_manager_linkedin.py', doraise=True)"
uv run python -c "import py_compile; py_compile.compile('main.py', doraise=True)"
```

Expected: No syntax errors.

- [ ] **Step 3: Import check**

```bash
uv run python -c "from config.app_config import RESET_SEEN_COMPANIES, FORM_ERROR_MAX_RETRIES; print(f'RESET_SEEN_COMPANIES={RESET_SEEN_COMPANIES}, FORM_ERROR_MAX_RETRIES={FORM_ERROR_MAX_RETRIES}')"
```

Expected: `RESET_SEEN_COMPANIES=False, FORM_ERROR_MAX_RETRIES=5`

- [ ] **Step 4: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: address review feedback on job applier overhaul"
```
