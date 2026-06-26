# Job Applier Full Overhaul — Design Spec

**Date:** 2026-06-26
**Status:** Approved
**Scope:** Fix the three core issues preventing job applications from being submitted

---

## Problem Statement

The bot navigates to jobs, scrapes descriptions, generates resumes — but never actually submits applications. Three root causes:

1. **Company dedup blocks everything** — `_job_is_already_seen` checks all three company lists (success + skipped + failed). With `apply_once_at_company: true`, any previously seen company is skipped forever, even if no application was ever submitted.
2. **Form filler can't handle dropdowns/radios** — `_check_and_fix_errors` only calls `_fill_textbox_question_errors()`. LinkedIn Easy Apply forms have dropdowns, radio buttons, checkboxes, and consent fields that never get filled. The "Please make a selection" error comes from these unfilled elements.
3. **SIGINT kills browser mid-run** — Signal arrives during job processing, browser connection drops, all remaining jobs cascade into "Connection closed" errors.

---

## Section 1: Company Dedup Refactor

### Current behavior

`_job_is_already_seen` in `src/job_manager/job_manager.py:337` iterates through `success_companies`, `skipped_companies`, and `failed_companies`. When `apply_once_at_company` is true, `_match_seen_jobs` skips the entire company if it appears in ANY of these lists.

### Proposed change

Separate "attempted" from "successfully applied":

- **`success_companies`** → blocks re-application (you already applied there)
- **`skipped_companies`** → does NOT block (job was never applied to)
- **`failed_companies`** → does NOT block (application failed, worth retrying)

### New `_job_is_already_seen` logic

```python
def _job_is_already_seen(self, job: Job) -> Tuple[bool, str]:
    company_name = job.company_name
    job_title = job.job_title

    if COLLECT_INFO_MODE is True:
        # Existing collect-info logic unchanged
        my_companies = self.interesting_jobs
        for job_info in my_companies:
            if company_name == job_info.company_name and job_title == job_info.job_title:
                return True, "The vacancy has already been encountered"
    else:
        # Company-wide skip: ONLY check success_companies
        if self.apply_once_at_company:
            is_seen, reason = self._match_seen_jobs(job, self.success_companies)
            if is_seen:
                return True, reason

        # Exact job-title match: check ALL three lists
        for companies in (self.success_companies, self.skipped_companies, self.failed_companies):
            for comp in companies:
                if sanitize_text(company_name) == sanitize_text(comp):
                    for job_info in companies[comp]:
                        if job_title == job_info["job_title"]:
                            return True, "The vacancy has already been encountered"

    return False, ""
```

### `RESET_SEEN_COMPANIES` config

When `True`, clears `skipped.yaml` and `failed.yaml` on startup (keeps `success.yaml`). Useful after refining search criteria.

```python
# config/app_config.py
RESET_SEEN_COMPANIES = False
```

Applied in `BaseJobManager.__init__` or at startup in `main.py` before loading companies.

---

## Section 2: Form Element Handler

### Current behavior

`_check_and_fix_errors` in `src/job_manager/linkedin/easy_applier_linkedin.py:360` retries 3 times but only calls `_fill_textbox_question_errors()`, which handles text inputs only.

### Proposed change

Expand to detect and handle all LinkedIn form field types.

### New `_fill_form_errors` method

```python
async def _fill_form_errors(self):
    """Fill form fields that have validation errors (async)"""
    error_elements = await self._find_error_elements()
    for error_el in error_elements:
        field = await self._find_associated_field(error_el)
        if not field:
            continue
        field_type = await self._detect_field_type(field)

        match field_type:
            case "select" | "dropdown":
                await self._fill_dropdown(field)
            case "radio":
                await self._fill_radio(field)
            case "checkbox":
                await self._fill_checkbox(field)
            case "textarea" | "text":
                await self._fill_textbox(field)
            case "file":
                await self._handle_file_upload(field)
            case _:
                logger.warning(f"Unknown field type: {field_type}")
```

### Field type detection

```python
async def _detect_field_type(self, field) -> str:
    tag = await field.evaluate("el => el.tagName.toLowerCase()")
    input_type = await field.evaluate("el => el.type || ''")

    if tag == "select" or await field.evaluate("el => el.getAttribute('role') === 'listbox'"):
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
    return "unknown"
```

### Dropdown handling

```python
async def _fill_dropdown(self, field):
    """Fill a dropdown/select field with a smart default (async)"""
    options = await field.evaluate("""
        el => Array.from(el.options || el.querySelectorAll('option'))
            .map(o => ({value: o.value, text: o.textContent.trim(), disabled: o.disabled}))
    """)
    valid_options = [o for o in options if o["value"] and not o["disabled"] and o["text"]]

    if not valid_options:
        logger.warning("No valid options found in dropdown")
        return

    # Smart selection strategy
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
```

### Radio button handling

```python
async def _fill_radio(self, field):
    """Select the first available radio button in the group (async)"""
    group_name = await field.evaluate("el => el.name")
    if not group_name:
        return

    # Find all radio buttons in the same group
    radios = await find_elements_safely(
        self.page,
        f"input[type='radio'][name='{group_name}']",
        "css"
    )

    for radio in radios:
        if not await radio.is_disabled():
            await radio.click(timeout=1000)
            label = await radio.evaluate("el => el.labels?.[0]?.textContent?.trim() || el.value")
            logger.info(f"Selected radio option: {label}")
            return
```

### Checkbox/consent handling

```python
async def _fill_checkbox(self, field):
    """Check required consent checkboxes (async)"""
    is_checked = await field.is_checked()
    if is_checked:
        return

    # Check if it's a required field
    is_required = await field.evaluate("""
        el => el.required || el.getAttribute('aria-required') === 'true'
            || !!el.closest('.artdeco-form-item--bordered')
    """)

    if is_required:
        await field.check(timeout=1000)
        label = await field.evaluate("el => el.labels?.[0]?.textContent?.trim() || 'unknown'")
        logger.info(f"Checked required checkbox: {label}")
```

### Associated field finder

```python
async def _find_associated_field(self, error_element):
    """Find the form field associated with an error element (async)"""
    container = await error_element.evaluate_handle("""
        el => el.closest('.artdeco-form-item')
            || el.closest('[data-test-id]')
            || el.parentElement?.parentElement
    """)
    if not container:
        return None

    selectors = ["select", "input:not([type='hidden'])", "textarea", "[role='listbox']"]
    for selector in selectors:
        field = await container.query_selector(selector)
        if field:
            return field
    return None
```

### Retry limit

Change from 3 → `FORM_ERROR_MAX_RETRIES` (default 5):

```python
# config/app_config.py
FORM_ERROR_MAX_RETRIES = 5
```

In `_check_and_fix_errors`:
```python
while attempt < FORM_ERROR_MAX_RETRIES:
```

---

## Section 3: Graceful Shutdown

### Current behavior

`request_shutdown()` in `src/utils/runtime_control.py` sets a flag, but the browser connection can die immediately from SIGINT, causing cascading "Connection closed" errors.

### Proposed change

State machine with three states: RUNNING → DRAINING → CLEANUP → DONE.

### ShutdownState enum

```python
# src/utils/runtime_control.py
from enum import Enum

class ShutdownState(Enum):
    RUNNING = "running"
    DRAINING = "draining"    # Current job finishing, no new jobs started
    CLEANUP = "cleanup"      # Browser cleanup in progress
    DONE = "done"

_shutdown_state = ShutdownState.RUNNING

def get_shutdown_state() -> ShutdownState:
    return _shutdown_state

def request_shutdown():
    global _shutdown_state
    if _shutdown_state == ShutdownState.RUNNING:
        _shutdown_state = ShutdownState.DRAINING
        logger.info("Shutdown requested — finishing current job before exit")
```

### Job loop check

In `job_manager_linkedin.py:start_applying`:

```python
for vacancy in vacancies:
    if get_shutdown_state() == ShutdownState.DRAINING:
        logger.info("Shutdown requested, finishing current job then stopping")
        break
    # ... existing job processing ...
```

### Apply job respects shutdown

In `job_manager_linkedin.py:apply_job` finally block:

```python
finally:
    # ... existing cleanup ...
    if get_shutdown_state() != ShutdownState.DRAINING:
        try:
            await self.page.bring_to_front()
        except Exception as e:
            if self._is_target_closed_error(e):
                logger.warning("Browser was closed before returning to the search page")
            else:
                raise
```

### Cleanup sequence

In `main.py:create_and_run_bot` finally block:

```python
finally:
    from src.utils.runtime_control import ShutdownState, get_shutdown_state

    logger.info("Cleaning up browser resources...")
    # Transition to CLEANUP state
    import src.utils.runtime_control as rc
    rc._shutdown_state = ShutdownState.CLEANUP

    try:
        if context is not None:
            await save_browser_session(context)
            await stop_tracing(context)
        if browser is not None:
            await browser.close()
        elif context is not None:
            await context.close()
        logger.info("Playwright browser closed")
    except Exception as e:
        logger.warning(f"Error during browser cleanup: {e}")
    finally:
        rc._shutdown_state = ShutdownState.DONE
        runtime_controller.finish_run()
```

---

## Section 4: Config & Integration

### New config values

```python
# config/app_config.py

# Reset skipped/failed company lists on startup (keeps success.yaml)
RESET_SEEN_COMPANIES = False

# Max retry attempts for form submission errors
FORM_ERROR_MAX_RETRIES = 5
```

### File change summary

| File | Change |
|------|--------|
| `src/job_manager/job_manager.py` | Refactor `_job_is_already_seen` to only check `success_companies` for company-wide skip; add `RESET_SEEN_COMPANIES` logic |
| `src/job_manager/linkedin/easy_applier_linkedin.py` | Expand `_fill_textbox_question_errors` → `_fill_form_errors` with element-type detection; increase retry limit |
| `src/utils/runtime_control.py` | Add `ShutdownState` enum, update signal handler to set DRAINING |
| `src/job_manager/linkedin/job_manager_linkedin.py` | Check shutdown state in job loop; skip `bring_to_front` during DRAINING |
| `config/app_config.py` | Add `RESET_SEEN_COMPANIES` and `FORM_ERROR_MAX_RETRIES` |
| `main.py` | Update cleanup block to respect shutdown state |

### Testing approach

1. Run with `TEST_MODE = True` — generates resumes and fills forms but discards before submit. Validates dedup + form handler without actually applying.
2. Run with `DEBUG_MODE = True` — captures screenshots on form errors for visual debugging.
3. Verify `success.yaml` entries have non-null `applied_at` timestamps after a real run.
4. Test SIGINT during a run — verify graceful shutdown without cascading errors.

---

## Out of Scope

- Resume generation changes
- LLM prompt modifications
- Dashboard UI changes
- Indeed platform (LinkedIn only)
