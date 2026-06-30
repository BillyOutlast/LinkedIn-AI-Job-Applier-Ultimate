# Workday External-Apply Support

**Date:** 2026-06-29
**Status:** Approved — pending implementation plan
**Branch:** release

## Problem

LinkedIn job applications frequently redirect to external ATS providers. When the redirect lands on a Workday URL (`*.myworkdayjobs.com`), the current bot falls through to `src/llm/apply_agent.py`, which uses the `browser_use` LLM-driven browser library. Recent run logs show this path fails repeatedly:

```
WARNING:browser_use.BrowserSession:⚠️ Page readiness timeout (8.0s)
WARNING:browser_use.BrowserSession:⚠️ Page readiness timeout (3.0s) ×10
WARNING:browser_use.tools.service:⚠️ Element index 1772 not available
                                  - page may have changed. ×10
```

The LLM agent stalls on Workday's heavy JS application flow and loses track of DOM element indices.

## Goal

Add a deterministic Playwright-based `WorkdayApplier` that the bot dispatches to when an external apply URL matches `myworkdayjobs.com`. Replace the failing `browser_use` fallback for Workday URLs only. Non-Workday external URLs (Greenhouse, Lever, etc.) continue to use the existing `ApplyAgent`.

## Non-goals

- Workday as a standalone `JOB_SITE` (no job-search or aggregation; Workday hosts thousands of independent company boards)
- Greenhouse / Lever / iCIMS / Taleo specializations (existing ApplyAgent remains the fallback for those)
- Replacing `browser_use` for non-Workday external sites

## Decisions

| | Choice |
|---|---|
| Scope | External-apply handler dispatched from LinkedIn/Indeed flows |
| Flow depth | Full multi-step (account → My Experience → disclosures → questions → review → submit) |
| Account flow | Auto-create Workday account per company subdomain, persist session |
| Profile data | Reuse `data/output/structured_resume.yaml` + `RESUME_DIR/<name>.pdf` |
| Behavior | Workday URL → dedicated handler; other external URLs → existing `ApplyAgent` |

## Architecture

### New subpackage

`src/job_manager/workday/` mirrors the `indeed/` subpackage shape:

```
src/job_manager/workday/
  __init__.py                  # WorkdayApplier class export
  workday_authenticator.py     # per-subdomain account create / sign-in
  workday_applier.py           # main apply flow orchestrator
  workday_selectors.py         # CSS/XPath constants for Workday widgets
  workday_questions.py         # custom question handler (GPTAnswerer)
```

### Dispatch wiring

Two-line change in each of two files:

`src/job_manager/linkedin/job_manager_linkedin.py` around line 387:

```python
if TEST_MODE:
    apply_result = "Skip", "Test mode"
elif "myworkdayjobs.com" in apply_url:
    apply_result = await workday_applier.apply_to_job(apply_url, job=job)
else:
    apply_result = await self.llm_agent_component.apply_to_job(apply_url)
```

Same swap in `src/job_manager/indeed/job_manager_indeed.py` around line 300.

### Job lifecycle

1. URL detected → route to `WorkdayApplier`
2. `WorkdayAuthenticator.ensure_session(subdomain)` — read per-tenant storage state; if missing, auto-create account + persist
3. Phase loop: My Experience (form-fill from resume.yaml) → Voluntary Disclosures (auto-fill or "Prefer not to answer") → Custom Questions (LLM via `workday_questions.py`) → Review (read-only scan) → Submit
4. Return `(result, reason)` tuple matching existing ApplyAgent contract: `("Success", path)` / `("Error", "...")` / `("Skip", "...")`

## Components

### `workday_selectors.py`

Pure constants, no logic. CSS selectors keyed by Workday widget `data-automation-id`:

```python
ACCOUNT_CREATE_EMAIL_INPUT = "input[data-automation-id='email']"
ACCOUNT_CREATE_PASSWORD_INPUT = "input[data-automation-id='password']"
ACCOUNT_CREATE_SUBMIT = "button[data-automation-id='createAccountSubmitButton']"
MY_EXP_WORK_HISTORY_ADD = "button[data-automation-id='add-work-experience']"
# ... ~30-40 selectors covering: account, my-experience (work/edu/skills),
# disclosures, custom questions, review, submit
```

### `workday_authenticator.py`

`WorkdayAuthenticator` class:

- `async ensure_session(subdomain: str, email: str, storage_state_path: Path) -> bool`
- Reads existing browser storage state for subdomain; if session valid, return True
- Else: navigate to `https://{subdomain}/account/create`, fill email + auto-generated password (or `.env` `WORKDAY_DEFAULT_PASSWORD` if set), submit, save storage state
- On failure: log + return False (caller skips + reports "Could not create account")

### `workday_applier.py`

`WorkdayApplier` class, async, the orchestrator:

- `__init__(resume_structured, resume_pdf_path, llm_answerer, headless)` — wires deps
- `async apply_to_job(url: str, job: Job) -> Tuple[Tuple[str, str], Optional[str]]` — entry point matching ApplyAgent contract
- Internal phase methods (private):
  - `_navigate_to_apply(url)` — wait for `data-automation-id='applyFlowContainer'`
  - `_fill_account_if_needed()` — delegates to `WorkdayAuthenticator`
  - `_fill_my_experience()` — work history, education, skills, resume upload
  - `_fill_voluntary_disclosures()` — radio/checkbox defaults
  - `_answer_custom_questions()` — delegates to `workday_questions.py`
  - `_review_and_submit()` — scan review page, click Submit, capture screenshot
- Each phase returns `bool` (success/continue). On failure → log + `await debug_capture()` + return `("Error", reason)`
- Emits `emit_event()` for dashboard live feed

### `workday_questions.py`

`WorkdayQuestionHandler` class:

- `async answer(questions: List[Dict], context: Dict) -> Dict[str, str]` — page text + question list in, answer map out
- Reuses `src.llm.llm_manager.GPTAnswerer` (matches Easy Apply pattern from `easy_applier_linkedin.py`)
- New prompt template added to `src/llm/prompts.py` — single function, no inline prompts
- Falls back to `data/output/answers.yaml` cache before LLM

### Public surface

```python
from src.job_manager.workday import WorkdayApplier
```

The only import the dispatch sites need.

## Data flow

### Trigger

LinkedIn/Indeed job is in `apply` loop. `_check_apply_button()` returns external URL.

### Routing

```python
apply_url = await self._check_apply_button()
if not apply_url:
    apply_result = await self.easy_apply(job)  # native easy apply
elif TEST_MODE:
    apply_result = ("Skip", "Test mode")
elif "myworkdayjobs.com" in apply_url:
    workday_applier = WorkdayApplier(
        resume_structured=self.resume_structured,
        resume_pdf_path=self.submitted_resume_path or default_resume_pdf(),
        llm_answerer=self.llm_answerer_component,
        headless=HEADLESS_MODE,
    )
    apply_result = await workday_applier.apply_to_job(apply_url, job=job)
else:
    apply_result = await self.llm_agent_component.apply_to_job(apply_url)
```

### Apply run

1. **Subdomain parse** — `tenant = url.split("//")[1].split(".")[0]` → `"uhaul"`
2. **Account** — `await self.auth.ensure_session(tenant, email, storage_state_path)`
   - Email source: `linkedin_email` from `.env` (existing field) for tenants reached via LinkedIn; `indeed_email` for Indeed; per-tenant override stored at `browser_session/workday_{tenant}.email`
   - Reads `browser_session/workday_{tenant}.json`
   - Else: navigate to `https://{tenant}/account/create`, fill email + password, submit, persist state to `browser_session/workday_{tenant}.json`
3. **Apply page** — `await self._navigate_to_apply(url)` — waits for `data-automation-id='applyFlowContainer'`
4. **My Experience phase**:
   - Resume upload: `input[type='file']` → `set_input_files` with PDF
   - Work history: click "Add Work Experience" → fill employer/title/start/end/description per `resume.work_experience` entry
   - Education: same pattern from `resume.education`
   - Skills: free-text input → comma-joined skill list extracted from resume text
   - Click "Save and Continue"
5. **Voluntary Disclosures phase**:
   - Scan for radio groups (gender, race, veteran, disability)
   - Default to "Prefer not to answer" or values from `resume.demographics` if user opted in
   - Click "Save and Continue"
6. **Custom Questions phase**:
   - Read all visible question labels
   - `WorkdayQuestionHandler.answer(questions, ctx={"resume": ..., "job": ...})`
   - Apply answers to text/dropdown/checkbox/radio fields
   - Click "Save and Continue"
7. **Review phase**:
   - Read-only page — no input
   - Sanity check: no required fields show "Required" error
   - Click "Submit"
8. **Confirmation** — wait for `data-automation-id='applicationConfirmation'` or similar
   - Screenshot → `data/output/screenshots/workday_{tenant}_{job_id}_success.png`
   - Return `("Success", screenshot_path)`

Each phase emits:

```python
emit_event("workday_phase", f"Phase complete: {name}", job_id=job.id, tenant=tenant)
```

## Error handling

- **Per-step retry:** Phase methods use `safe_click` / `safe_fill` from `src/utils/browser_utils.py`. These retry on stale-element errors and capture debug artifacts when `DEBUG_MODE=True`. No new retry logic.
- **Phase-level failure isolation:** A failing phase returns early `(False, reason)` instead of raising. Top of `apply_to_job` catches and returns `("Error", reason)`. One bad phase doesn't corrupt the session — cookies persist for retry.
- **Page-change tolerance:** Selectors are constants, keyed by Workday's stable `data-automation-id` test hooks (consistent across tenants since 2018). A missing selector raises `ElementNotFound`, caught, logged with selector name, returns `("Skip", f"Could not find {selector_name}")`. Outer `_handle_apply_result` already handles `Skip` reasons starting with `"Could not"`.
- **Account failure:** `WorkdayAuthenticator.ensure_session` returning `False` → immediate `("Error", f"Account creation failed for tenant={tenant}")`, no further phase attempts. Saves a debug screenshot of the account page (Workday may require CAPTCHA or phone verification).
- **Stale browser context:** Reuses `browser_session/browser_state.json` (shared across all sites per project rule). Workday per-tenant state stored separately as `browser_session/workday_{tenant}.json`.
- **LLM failure on custom questions:** Reuses `workday_questions.py` cache + GPTAnswerer fallback chain. Malformed LLM response → empty string for text fields, skip for required fields (matches Easy Apply convention).
- **Resume PDF missing:** If `submitted_resume_path` is None AND no default in `RESUME_DIR`, return `("Error", "No resume PDF available")` before phase 4.
- **TEST_MODE:** Returns `("Skip", "Test mode")` early at dispatch site.
- **MONKEY_MODE / COLLECT_INFO_MODE:** Same as native apply paths.
- **Dashboard events:** `workday_phase_complete`, `workday_phase_failed`, `workday_account_created`, `workday_submitted`, all with `tenant`, `job_id`, `phase`.

## Testing

### Unit tests

- `tests/test_workday_selectors.py` — assert all selectors are non-empty strings, no duplicates
- `tests/test_workday_authenticator.py` — mock Playwright page + storage state. Cover: existing session valid, missing session creates account, failure returns False, state persisted
- `tests/test_workday_questions.py` — mock GPTAnswerer. Cover: cache hit returns without LLM call, LLM-only path returns parsed answers, malformed LLM response falls back to skip

### Integration test

`tests/test_workday_applier.py`:

- One end-to-end test using `TEST_MODE` against a live Workday tenant — full phase traversal with mocked GPTAnswerer
- Reuses the same Playwright test fixtures pattern as `tests/test_authenticator.py`
- Skip if `WORKDAY_E2E_TENANT` env var unset (CI without credentials)

### Manual smoke test

`src/job_manager/workday/workday_applier.py` `__main__` block:

- Runs against the Uhaul job from the failure log with `HEADLESS_MODE=False`
- One runnable check per phase, fails loudly if a phase breaks

### Existing test coverage

Untouched — no edits to `tests/test_easy_applier_*.py`. Only new files.

### Acceptance criteria

1. Test against the Uhaul Workday URL from the failure log → reaches "Submit" without `browser_use` warnings
2. Re-running against the same tenant uses cached storage state, skips account creation
3. Non-Workday external URLs (Greenhouse, Lever) still route to existing `ApplyAgent` — verified by integration test that mocks apply URL routing

## Files added

| File | Purpose |
|---|---|
| `src/job_manager/workday/__init__.py` | `WorkdayApplier` export |
| `src/job_manager/workday/workday_authenticator.py` | Account create / session persist |
| `src/job_manager/workday/workday_applier.py` | Apply flow orchestrator |
| `src/job_manager/workday/workday_selectors.py` | CSS/XPath constants |
| `src/job_manager/workday/workday_questions.py` | Custom question handler |
| `tests/test_workday_selectors.py` | Selector constant validity |
| `tests/test_workday_authenticator.py` | Authenticator unit tests |
| `tests/test_workday_questions.py` | Question handler unit tests |
| `tests/test_workday_applier.py` | Integration test |

## Files modified

| File | Change |
|---|---|
| `src/job_manager/linkedin/job_manager_linkedin.py` | Add Workday URL routing branch (~line 387) |
| `src/job_manager/indeed/job_manager_indeed.py` | Add Workday URL routing branch (~line 300) |
| `src/llm/prompts.py` | Add `workday_questions_prompt` template |
| `data/output/screenshots/` (dir) | New screenshots per success |
| `browser_session/workday_{tenant}.json` (per tenant) | Per-tenant session state |

## Project rules respected

- `uv run pytest tests/test_workday_*.py` (testing rule)
- Mock LLM, Playwright, storage state in unit tests (testing rule)
- New prompt template in `src/llm/prompts.py`, no inline prompts elsewhere (LLM rule)
- Logger from `config.logger_config`, specific exceptions, contextual log fields (code-style rule)
- Resume anonymization before LLM calls (security rule)
- `.env` for any new secrets (`WORKDAY_DEFAULT_PASSWORD`); default to auto-generated if unset
