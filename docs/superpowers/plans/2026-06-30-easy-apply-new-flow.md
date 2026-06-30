# Easy Apply — Adapt to LinkedIn's New In-Page Apply Flow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bot work with LinkedIn's new Easy Apply flow (verified live on `https://www.linkedin.com/jobs/view/4425305553/` via chrome-devtools on 2026-06-30): clicking Easy Apply now navigates to a full-page multi-step apply form (`/jobs/view/{id}/apply/?applicantTrackingSystemName=...`) on linkedin.com — there is no modal.

**Background:** Debug capture + chrome-devtools DOM inspection on 2026-06-30 showed:
- The Easy Apply button is `<button aria-label="Easy Apply to this job">` (not `<a>`).
- The bot's xpath `'//a[contains(., "Apply")]'` does NOT match this button — it matches "· Easy Apply" anchor links in the similar-jobs sidebar, leading to mis-clicks.
- After clicking, the URL changes to `/jobs/view/{id}/apply/?...&applicantTrackingSystemName=Infinite+Brassring`. The body renders "Apply to Harbor Freight Tools · 1/15 pages · Contact info" with 20+ form fields (no `<form>` tag, no semantic fieldsets).
- All form fields have hashed CSS class names; no `name`, no `aria-label`, no stable `id`. Inputs use `«r6»`-style React ids.
- No modal elements: `[role="dialog"]` count = 0, `.artdeco-modal` count = 0. The bot's `wait_for_selector(".jobs-easy-apply-modal__content")` will never match.

**Architecture:** Four sequential surgical changes to `src/job_manager/linkedin/easy_applier_linkedin.py`:

1. **Task 1**: Fix button selector. Match `<button aria-label="Easy Apply to this job">` instead of `<a>` (with case-insensitive fallback).
2. **Task 2**: Detect URL transition after click. Wait for `/apply/` to appear in `self.page.url` before proceeding.
3. **Task 3**: Refactor `_fill_up` to operate on `self.page` when no modal is detected. Detect the apply-page state via the H2 "Apply to" heading.
4. **Task 4**: Update form-group detection. Replace class-based selectors (`.fb-dash-form-element`, `.jobs-easy-apply-form-section__group`) with a structural xpath that walks label + input pairs.

**Tech Stack:** Python 3.12, Playwright async, pytest + pytest-asyncio.

## Global Constraints

- `DEBUG_MODE = True` — `debug_capture` writes to `data/debug/`.
- 833 tests currently pass. Plan must keep them green.
- No changes outside `src/job_manager/linkedin/easy_applier_linkedin.py` and `tests/test_easy_applier_unit.py`.
- Tasks are sequential — each builds on the previous. Do NOT parallelize.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/job_manager/linkedin/easy_applier_linkedin.py` | Modify (Tasks 1-4) | Button selector, URL detection, modal→page refactor, form-group detection |
| `tests/test_easy_applier_unit.py` | Modify (Tasks 1-4) | New tests for each layer |

No new files. No new modules. No new dependencies.

---

### Task 1: Fix Easy Apply button selector

**Files:**
- Modify: `src/job_manager/linkedin/easy_applier_linkedin.py:254-256` (the `easy_apply_selectors` list)
- Modify: `tests/test_easy_applier_unit.py` (the existing click-success test needs a `bounding_box` mock added so the visibility gate from the prior plan lets the click through)

**Interfaces:**
- Consumes: existing `find_elements_safely`, existing `easy_applier` fixture
- Produces: `_find_easy_apply_button` matches `<button aria-label="Easy Apply to this job">` first, with case-insensitive + text-content fallbacks

- [ ] **Step 1: Add `bounding_box` mock to existing click-success test**

In `test_find_easy_apply_button_clicks_and_returns_true` (around line 57 in `tests/test_easy_applier_unit.py`), after `button.first = AsyncMock()`, add:

```python
        button.first.bounding_box = AsyncMock(
            return_value={"x": 100, "y": 200, "width": 120, "height": 32}
        )
```

The mock is required because the prior visibility-gate plan calls `bounding_box()` on `button.first` before clicking.

- [ ] **Step 2: Replace the selector list**

In `src/job_manager/linkedin/easy_applier_linkedin.py`, replace lines 254-256:

```python
            easy_apply_selectors = [
                '//a[contains(., "Apply")]',
            ]
```

With:

```python
            # ponytail: LinkedIn changed Easy Apply from <a> to <button aria-label="Easy Apply to this job">.
            # The legacy <a> selector matched sidebar "· Easy Apply" similar-job links and caused
            # mis-clicks that navigated to unrelated jobs. ARIA-first, then case-insensitive fallback.
            easy_apply_selectors = [
                '//button[@aria-label="Easy Apply to this job"]',
                '//button[contains(translate(@aria-label, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "easy apply")]',
                '//button[contains(., "Easy Apply")]',
            ]
```

- [ ] **Step 3: Run the existing click-success test**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestEasyApplyButtonDetection::test_find_easy_apply_button_clicks_and_returns_true -v`

Expected: PASS.

- [ ] **Step 4: Run all button-detection tests**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestEasyApplyButtonDetection -v`

Expected: all 4 existing tests pass.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 833 passed.

- [ ] **Step 6: Commit**

```bash
git add src/job_manager/linkedin/easy_applier_linkedin.py tests/test_easy_applier_unit.py
git commit -m "fix(linkedin): select Easy Apply button by aria-label

LinkedIn changed the Easy Apply button from an <a> tag to a <button
aria-label=\"Easy Apply to this job\">. The old xpath matched similar-
job sidebar links (\"· Easy Apply\") and caused mis-clicks that navigated
to unrelated jobs. ARIA-first selector with case-insensitive fallback."
```

---

### Task 2: Detect URL transition to `/apply/` after click

**Files:**
- Modify: `src/job_manager/linkedin/easy_applier_linkedin.py:151-160` (the post-click block in `job_easy_apply`)
- Modify: `tests/test_easy_applier_unit.py` (new test)

- [ ] **Step 1: Write the failing test**

Add to `TestEasyApplyButtonDetection`:

```python
    @pytest.mark.asyncio
    async def test_job_easy_apply_waits_for_url_transition_after_click(self, easy_applier):
        """After Easy Apply click, wait for /apply/ URL before filling form."""
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )

        easy_applier.page.wait_for_url = AsyncMock()
        easy_applier.page.url = "https://www.linkedin.com/jobs/view/12345/apply/?foo=bar"

        with (
            patch.object(easy_applier, "check_for_premium_redirect", new_callable=AsyncMock, return_value=False),
            patch.object(easy_applier, "_check_easy_apply_limit", new_callable=AsyncMock, return_value=False),
            patch(f"{MODULE}.find_elements_safely", new_callable=AsyncMock) as mock_find,
            patch(f"{MODULE}.debug_capture", new_callable=AsyncMock),
            patch(f"{MODULE}.async_pause", new_callable=AsyncMock),
            patch.object(easy_applier, "_is_already_applied", new_callable=AsyncMock, return_value=False),
            patch.object(easy_applier, "_click_continue_applying_button", new_callable=AsyncMock),
            patch.object(easy_applier, "_fill_application_form", new_callable=AsyncMock),
        ):
            button = AsyncMock()
            button.is_visible = AsyncMock(return_value=True)
            button.is_enabled = AsyncMock(return_value=True)
            button.first = AsyncMock()
            button.first.bounding_box = AsyncMock(
                return_value={"x": 100, "y": 200, "width": 120, "height": 32}
            )
            button.first.click = AsyncMock()
            mock_find.return_value = [button]

            await easy_applier.job_easy_apply(job)

            easy_applier.page.wait_for_url.assert_awaited()
            url_pattern = easy_applier.page.wait_for_url.await_args.args[0]
            assert "apply" in url_pattern.lower(), (
                f"Expected /apply/ in wait_for_url pattern, got: {url_pattern}"
            )
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestEasyApplyButtonDetection::test_job_easy_apply_waits_for_url_transition_after_click -v`

Expected: FAIL — `wait_for_url` was never awaited.

- [ ] **Step 3: Add URL wait after click**

In `src/job_manager/linkedin/easy_applier_linkedin.py`, find the block:

```python
                logger.debug("'Easy Apply' button clicked successfully")
                await async_pause()
                # Click 'Continue Applying' button if it appears
                await self._click_continue_applying_button()
                await async_pause()
                # Check for premium redirect
                if not await self.check_for_premium_redirect(self.current_job):
                    break
                else:
                    logger.debug("Redirected to premium page, trying again")
```

Insert after the `async_pause()` line and before the premium-redirect check:

```python
                # ponytail: LinkedIn's Easy Apply now navigates to /jobs/view/{id}/apply/?
                # instead of opening a modal. Wait for that URL transition (10s) before
                # form-filling kicks in. Falls through if URL doesn't change (legacy modal).
                try:
                    await self.page.wait_for_url(
                        "**/jobs/view/*/apply/**", timeout=10000
                    )
                    logger.debug("Navigated to LinkedIn apply page")
                except Exception as e:
                    logger.debug(f"No URL transition to /apply/ (legacy modal?): {e}")
```

- [ ] **Step 4: Run test, expect PASS**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestEasyApplyButtonDetection::test_job_easy_apply_waits_for_url_transition_after_click -v`

Expected: PASS.

- [ ] **Step 5: Full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 834 passed.

- [ ] **Step 6: Commit**

```bash
git add src/job_manager/linkedin/easy_applier_linkedin.py tests/test_easy_applier_unit.py
git commit -m "fix(linkedin): wait for /apply/ URL after Easy Apply click

LinkedIn's new Easy Apply flow navigates to /jobs/view/{id}/apply/?
on click instead of opening a modal. Wait up to 10s for that URL
transition before form-filling kicks in. Falls through if URL doesn't
change (legacy modal still supported for unmigrated jobs)."
```

---

### Task 3: Refactor `_fill_up` to operate on `self.page`

**Files:**
- Modify: `src/job_manager/linkedin/easy_applier_linkedin.py:446-490` (modal-wait + modal-finding block; rename `modal_content` → `form_root`)
- Modify: `tests/test_easy_applier_unit.py` (update the modal-detection test from the prior plan so it accepts the apply-page branch)

- [ ] **Step 1: Update the modal-detection test to expect apply-page branch**

In `TestModalDetection::test_fill_up_attempts_aria_selectors_before_legacy`, change `mock_find.side_effect` from `[None, None, None, modal, modal, modal, modal, modal]` to `[None] * 6 + [modal] * 6`. The 6 leading Nones represent the modal + apply-page indicator lookups all failing until the structural selector eventually finds the form group. (This still verifies ARIA-first ordering; the test asserts ARIA selectors come before legacy.)

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestModalDetection -v`

Expected: FAIL — old code raises `NoInfoException` after the modal selectors all return None.

- [ ] **Step 3: Replace modal-finding block with modal-or-page branch**

In `src/job_manager/linkedin/easy_applier_linkedin.py`, replace the modal-finding block (lines 446-477):

```python
            # Try to wait for the modal to be visible
            try:
                # Wait up to 10 seconds for the modal to appear
                await self.page.wait_for_selector(
                    ".jobs-easy-apply-modal__content", state="visible", timeout=10000
                )
                logger.debug("Modal selector found via wait_for_selector")
            except Exception as e:
                logger.warning(f"wait_for_selector failed: {e}")

            # ponytail: LinkedIn moved to hashed CSS class names in 2026, so
            # .jobs-easy-apply-modal__content rarely matches anymore. ARIA
            # semantics ([role=dialog], [aria-modal=true]) are stable across
            # builds. ARIA first; legacy classes last as a safety net.
            modal_selectors = [
                '[role="dialog"]',
                '[aria-modal="true"]',
                '[aria-labelledby*="apply" i]',
                ".jobs-easy-apply-modal__content",
                ".artdeco-modal__content",
                "//*[contains(@class, 'jobs-easy-apply-modal__content')]",
            ]

            for selector in modal_selectors:
                selector_type = (
                    "css" if selector.startswith(".") or selector.startswith("[") else "xpath"
                )
                modal_content = await find_element_safely(self.page, selector, selector_type)
                if modal_content is not None:
                    logger.debug(f"Easy Apply modal content found with selector: {selector}")
                    break

            if modal_content is None:
                logger.error("Easy Apply modal content not found on the page with any selector")
                await debug_capture(self.page, "easy_apply_modal_missing")
                if await self._is_already_applied():
                    raise NoInfoException("Already applied to this job")
                raise NoInfoException("Easy Apply dialog did not open")
```

With:

```python
            # ponytail: LinkedIn's new Easy Apply navigates to /jobs/view/{id}/apply/
            # instead of opening a modal. Detect either:
            #   1) Legacy modal (some jobs still use it) — via ARIA role + legacy class.
            #   2) New in-page apply form — via H2 "Apply to <company>" heading.
            # Both branches set `form_root` to the element the form-filling code operates on.

            # 1) Try legacy modal first.
            modal_selectors = [
                '[role="dialog"]',
                '[aria-modal="true"]',
                '[aria-labelledby*="apply" i]',
                ".jobs-easy-apply-modal__content",
                ".artdeco-modal__content",
                "//*[contains(@class, 'jobs-easy-apply-modal__content')]",
            ]
            modal_content = None
            for selector in modal_selectors:
                selector_type = (
                    "css" if selector.startswith(".") or selector.startswith("[") else "xpath"
                )
                modal_content = await find_element_safely(self.page, selector, selector_type)
                if modal_content is not None:
                    logger.debug(f"Easy Apply modal found with selector: {selector}")
                    break

            form_root = modal_content  # may be None if no modal

            # 2) If no modal, check for new in-page apply form.
            if form_root is None:
                apply_heading_selectors = [
                    'xpath=//h2[contains(text(), "Apply to")]',
                    'xpath=//h1[contains(text(), "Apply to")]',
                    'xpath=//*[contains(text(), "/") and contains(text(), "pages") and not(self::script)]',
                ]
                apply_indicator = None
                for selector in apply_heading_selectors:
                    apply_indicator = await find_element_safely(self.page, selector, "xpath")
                    if apply_indicator is not None:
                        logger.debug(f"Apply page detected with selector: {selector}")
                        break
                if apply_indicator is None:
                    logger.error("Easy Apply form not found (no modal, no apply page)")
                    await debug_capture(self.page, "easy_apply_form_missing")
                    if await self._is_already_applied():
                        raise NoInfoException("Already applied to this job")
                    raise NoInfoException("Easy Apply dialog did not open")
                form_root = self.page

            logger.debug("Easy Apply form root resolved")
```

- [ ] **Step 4: Update form-elements finding to use `form_root`**

In `src/job_manager/linkedin/easy_applier_linkedin.py`, replace lines 484-491:

```python
            # Find all form elements using the correct selectors
            form_elements = await modal_content.locator(".fb-dash-form-element").all()
            logger.debug(f"Found {len(form_elements)} form elements")

            if not form_elements:
                # Fallback to the old selector if new one doesn't work
                form_elements = await modal_content.locator(
                    "xpath=.//*[contains(@class, 'jobs-easy-apply-form-section__group')]"
                ).all()
                logger.debug(f"Found {len(form_elements)} form elements with fallback selector")
```

With:

```python
            # Find all form elements using the correct selectors
            form_elements = await form_root.locator(".fb-dash-form-element").all()
            logger.debug(f"Found {len(form_elements)} form elements")

            if not form_elements:
                form_elements = await form_root.locator(
                    "xpath=.//*[contains(@class, 'jobs-easy-apply-form-section__group')]"
                ).all()
                logger.debug(f"Found {len(form_elements)} form elements with fallback selector")
```

- [ ] **Step 5: Run test, expect PASS**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestModalDetection -v`

Expected: PASS.

- [ ] **Step 6: Full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 834 passed (no new test added; existing one updated).

- [ ] **Step 7: Commit**

```bash
git add src/job_manager/linkedin/easy_applier_linkedin.py tests/test_easy_applier_unit.py
git commit -m "fix(linkedin): support new in-page Easy Apply form (no modal)

LinkedIn's Easy Apply now navigates to /jobs/view/{id}/apply/ instead
of opening a modal. The old .jobs-easy-apply-modal__content selector
will never match the new flow. Detect the apply page via H2 heading
'Apply to <company>' and operate on self.page directly. Keep modal
detection as a fallback for jobs that haven't migrated yet."
```

---

### Task 4: Form-group detection via structural selector

**Files:**
- Modify: `src/job_manager/linkedin/easy_applier_linkedin.py:484-491` (the form-elements finding block)
- Modify: `tests/test_easy_applier_unit.py` (new test in `TestModalDetection`)

- [ ] **Step 1: Write the failing test**

Append to `TestModalDetection`:

```python
    @pytest.mark.asyncio
    async def test_fill_up_tries_structural_form_group_selector(self, easy_applier):
        """After class selectors fail, a structural label+input selector must be tried."""
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )

        apply_indicator = AsyncMock()
        form_group = AsyncMock()
        apply_indicator.locator.return_value.all = AsyncMock(return_value=[])
        apply_indicator.locator.return_value.first = AsyncMock()
        apply_indicator.locator.return_value.first.click = AsyncMock()
        form_group.locator.return_value.all = AsyncMock(return_value=[])

        with (
            patch(f"{MODULE}.find_element_safely", new_callable=AsyncMock) as mock_find,
            patch.object(easy_applier, "_click_continue_applying_button", new_callable=AsyncMock),
            patch.object(easy_applier, "_is_already_applied", new_callable=AsyncMock, return_value=False),
            patch(f"{MODULE}.async_pause", new_callable=AsyncMock),
            patch.object(easy_applier.page, "locator") as mock_locator,
        ):
            mock_find.side_effect = [None] * 6 + [apply_indicator] + [form_group] * 20
            mock_locator.return_value.all = AsyncMock(return_value=[])
            mock_locator.return_value.first = AsyncMock()
            mock_locator.return_value.first.click = AsyncMock()

            await easy_applier._fill_up(job)

            attempted_xpaths = [
                c.args[0] for c in mock_locator.await_args_list
                if c.args and isinstance(c.args[0], str) and c.args[0].startswith("xpath=")
            ]
            assert any("label" in x and "input" in x for x in attempted_xpaths), (
                f"Expected a structural label+input selector; got: {attempted_xpaths}"
            )
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestModalDetection::test_fill_up_tries_structural_form_group_selector -v`

Expected: FAIL — current code only tries class-based selectors.

- [ ] **Step 3: Add structural fallback**

In `src/job_manager/linkedin/easy_applier_linkedin.py`, replace the form-elements finding block (currently lines 484-491 after Task 3):

```python
            # Find all form elements using the correct selectors
            form_elements = await form_root.locator(".fb-dash-form-element").all()
            logger.debug(f"Found {len(form_elements)} form elements")

            if not form_elements:
                form_elements = await form_root.locator(
                    "xpath=.//*[contains(@class, 'jobs-easy-apply-form-section__group')]"
                ).all()
                logger.debug(f"Found {len(form_elements)} form elements with fallback selector")
```

With:

```python
            # ponytail: LinkedIn's 2026 rebuild hashed all CSS class names, so legacy
            # .fb-dash-form-element / .jobs-easy-apply-form-section__group rarely match.
            # Try legacy classes first (some accounts/older jobs still have them), then
            # fall back to a structural selector: any container that holds both a <label>
            # and an <input>/<select>/<textarea> is a form group.
            form_elements = await form_root.locator(".fb-dash-form-element").all()
            logger.debug(f"Found {len(form_elements)} form elements via .fb-dash-form-element")

            if not form_elements:
                form_elements = await form_root.locator(
                    "xpath=.//*[contains(@class, 'jobs-easy-apply-form-section__group')]"
                ).all()
                logger.debug(f"Found {len(form_elements)} form elements via jobs-easy-apply-form-section__group")

            if not form_elements:
                form_elements = await form_root.locator(
                    "xpath=.//*[.//label and (.//input or .//select or .//textarea)]"
                ).all()
                logger.debug(f"Found {len(form_elements)} form elements via structural selector")
```

- [ ] **Step 4: Run test, expect PASS**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestModalDetection::test_fill_up_tries_structural_form_group_selector -v`

Expected: PASS.

- [ ] **Step 5: Full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 835 passed (834 + 1 new).

- [ ] **Step 6: Commit**

```bash
git add src/job_manager/linkedin/easy_applier_linkedin.py tests/test_easy_applier_unit.py
git commit -m "fix(linkedin): find form groups via structural selector

LinkedIn's 2026 rebuild hashed all CSS class names including
.fb-dash-form-element and .jobs-easy-apply-form-section__group, so
the existing form-group detection finds nothing on the new apply
page. Add a structural fallback: any element containing a <label>
AND an <input>/<select>/<textarea> is a form group. Works against
the hashed DOM regardless of class names."
```

---

## Self-Review

1. **Spec coverage** — 4 sub-changes for the architectural switch: button selector, URL transition detection, modal→page scope, structural form-group detection.
2. **Placeholders** — none. Each step has exact code, exact selectors, exact commands.
3. **Type/signature consistency** — `wait_for_url` is a Playwright async method taking a glob pattern; called correctly with `"**/jobs/view/*/apply/**"`. `find_element_safely` already imported. `form_root` replaces `modal_content` consistently in form-elements lookup.
4. **Sequential execution** — Tasks 1-4 modify the same file but at different sections; serial execution avoids edit conflicts. Each commit is independently reviewable.
5. **Legacy modal still supported** — Task 3's `modal_selectors` loop runs first; only if no modal is found does the apply-page detector run. Jobs that haven't migrated keep working.

No issues found. Plan ready for execution.