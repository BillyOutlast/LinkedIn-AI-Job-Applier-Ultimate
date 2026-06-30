# Easy Apply Modal — Visibility Gate + ARIA Selectors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Easy Apply modal failure on `https://www.linkedin.com/jobs/view/4425305553/` and similar jobs, by (1) refusing to click Easy Apply buttons that are hidden from render and (2) replacing LinkedIn's now-hashed class selectors with ARIA-based ones.

**Background:** Debug capture at `data/debug/20260630_130041_easy_apply_modal_missing.html` shows LinkedIn has moved to hashed CSS class names. The old `.jobs-easy-apply-modal__content` will never match again. The Easy Apply buttons exist in DOM with `offsetParent === null` (hidden from render). When Playwright clicks a hidden button, the click event fires but LinkedIn's handler is no-op on invisible elements → no modal opens → wait_for_selector times out → bot logs `Easy Apply dialog did not open` and skips.

**Architecture:** Two surgical changes to `src/job_manager/linkedin/easy_applier_linkedin.py`:
- Task 1: Before clicking each Easy Apply button, assert it is visually rendered (non-zero area in viewport). If hidden, capture debug + skip without clicking.
- Task 2: Replace class-based modal selectors in `_fill_up` with ARIA-attribute-based selectors (`[role="dialog"]`, `[aria-modal="true"]`).

**Tech Stack:** Python 3.12, Playwright async, `debug_capture` helper, pytest with `pytest-asyncio`.

## Global Constraints

- `DEBUG_MODE = True` in `config/app_config.py` — `debug_capture` writes artifacts to `data/debug/`.
- 831 tests currently pass. Plan must keep them green (or grow by the new tests added).
- No changes outside `src/job_manager/linkedin/easy_applier_linkedin.py` and `tests/test_easy_applier_unit.py`.
- Task 1 and Task 2 modify the same file — implement sequentially, not in parallel.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/job_manager/linkedin/easy_applier_linkedin.py` | Modify (Tasks 1+2) | Visibility gate in `_find_easy_apply_button`; ARIA modal selectors in `_fill_up` |
| `tests/test_easy_applier_unit.py` | Modify (Tasks 1+2) | New test for visibility gate; new test for ARIA modal selector |

No new files. No new modules. No new dependencies.

---

### Task 1: Visible-button gate in `_find_easy_apply_button`

**Files:**
- Modify: `src/job_manager/linkedin/easy_applier_linkedin.py:241-283` (the click loop of `_find_easy_apply_button`)
- Modify: `tests/test_easy_applier_unit.py` (append new test method to `TestEasyApplyButtonDetection` class)

**Interfaces:**
- Consumes: existing `debug_capture(page, label)` import; existing `easy_applier` fixture
- Produces: When an Easy Apply button is hidden (`bounding_box()` returns zero area), the method captures debug with label `easy_apply_button_hidden`, continues to next button or returns False on the second attempt without raising

- [ ] **Step 1: Write the failing test**

Append to `TestEasyApplyButtonDetection` (after `test_find_easy_apply_button_captures_debug_when_not_found`):

```python
    @pytest.mark.asyncio
    async def test_find_easy_apply_button_skips_hidden_button(self, easy_applier):
        """A hidden Easy Apply button must not be clicked; debug_capture must fire."""
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )
        hidden_button = AsyncMock()
        hidden_button.is_visible = AsyncMock(return_value=True)  # playwright-side visible
        hidden_button.is_enabled = AsyncMock(return_value=True)
        hidden_button.first = AsyncMock()
        # Simulate LinkedIn's hidden-from-render behavior:
        # is_visible returns true (in DOM) but bounding rect is zero (not rendered)
        hidden_button.first.bounding_box = AsyncMock(return_value={"x": 0, "y": 0, "width": 0, "height": 0})
        # Track whether click was attempted
        click_attempted = False
        async def maybe_click(*args, **kwargs):
            nonlocal click_attempted
            click_attempted = True
        hidden_button.first.click = AsyncMock(side_effect=maybe_click)
        with (
            patch.object(easy_applier, "check_for_premium_redirect", new_callable=AsyncMock),
            patch.object(
                easy_applier, "_check_easy_apply_limit", new_callable=AsyncMock
            ) as mock_limit,
            patch(f"{MODULE}.find_elements_safely", new_callable=AsyncMock) as mock_find,
            patch(f"{MODULE}.debug_capture", new_callable=AsyncMock) as mock_capture,
            patch(f"{MODULE}.async_pause", new_callable=AsyncMock),
        ):
            mock_limit.return_value = False
            mock_find.return_value = [hidden_button]

            result = await easy_applier._find_easy_apply_button(job)

            assert result is False
            assert click_attempted is False, "hidden button must NOT be clicked"
            hidden_labels = [
                call.args[1] if len(call.args) > 1 else call.kwargs.get("label")
                for call in mock_capture.await_args_list
            ]
            assert "easy_apply_button_hidden" in hidden_labels
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestEasyApplyButtonDetection::test_find_easy_apply_button_skips_hidden_button -v`

Expected: FAIL — either the button gets clicked (assertion on `click_attempted`) or the capture label is wrong.

- [ ] **Step 3: Add the visibility gate**

Edit `src/job_manager/linkedin/easy_applier_linkedin.py` lines 261-267 (the inner per-button loop) to add a visibility check before clicking. Replace this block:

```python
            for button in easy_apply_buttons:
                try:
                    if not (await button.is_visible() and await button.is_enabled()):
                        logger.debug("Apply button is not visible or enabled")
                        continue
                    await button.first.click(timeout=1000)
                    return True
                except Exception as e:
                    logger.debug(f"Failed to click easy apply button: {e}")
```

With:

```python
            for button in easy_apply_buttons:
                try:
                    if not (await button.is_visible() and await button.is_enabled()):
                        logger.debug("Apply button is not visible or enabled")
                        continue
                    # ponytail: LinkedIn gates Easy Apply by hiding the button from render
                    # when it detects automation. is_visible() returns true (DOM-present)
                    # but bounding rect is zero (offsetParent === null). Clicking a hidden
                    # button fires the JS event but LinkedIn's handler is a no-op, so the
                    # modal never opens. Skip and capture.
                    bbox = await button.first.bounding_box()
                    if not bbox or bbox.get("width", 0) == 0 or bbox.get("height", 0) == 0:
                        logger.debug("Apply button hidden from render (zero bounding box), skipping")
                        await debug_capture(self.page, "easy_apply_button_hidden")
                        continue
                    await button.first.click(timeout=1000)
                    return True
                except Exception as e:
                    logger.debug(f"Failed to click easy apply button: {e}")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestEasyApplyButtonDetection::test_find_easy_apply_button_skips_hidden_button -v`

Expected: PASS, 1 test passed.

- [ ] **Step 5: Run the new test + the previously-added one (regression check)**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestEasyApplyButtonDetection -v`

Expected: both tests pass (the visibility-gate test + the button-missing capture test from prior plan).

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 832 passed (831 prior + 1 new). Investigate any regressions before continuing.

- [ ] **Step 7: Commit**

```bash
git add src/job_manager/linkedin/easy_applier_linkedin.py tests/test_easy_applier_unit.py
git commit -m "fix(linkedin): skip Easy Apply buttons hidden from render

LinkedIn hides the Easy Apply button at the DOM level when it detects
automation (offsetParent === null even though is_visible() returns true).
Playwright's click() fires the JS event on hidden elements, but
LinkedIn's handler is a no-op, leaving the modal closed and producing
the Easy Apply dialog did not open error. Detect via bounding box and
emit a debug capture so the bot skips instead of churning."
```

---

### Task 2: ARIA-based modal selectors in `_fill_up`

**Files:**
- Modify: `src/job_manager/linkedin/easy_applier_linkedin.py:455-460` (the `modal_selectors` list in `_fill_up`)
- Modify: `tests/test_easy_applier_unit.py` (append new test class `TestModalDetection`)

**Interfaces:**
- Consumes: existing `find_element_safely` import; existing `easy_applier` fixture
- Produces: `_fill_up` modal-finding tries `[role="dialog"]`, `[aria-modal="true"]`, `[aria-labelledby*="apply" i]` before falling back to legacy classes.

- [ ] **Step 1: Write the failing test**

Append a new `TestModalDetection` class to `tests/test_easy_applier_unit.py`:

```python
class TestModalDetection:
    @pytest.mark.asyncio
    async def test_fill_up_attempts_aria_selectors_before_legacy(self, easy_applier):
        """ARIA selectors must be tried before hashed-class fallbacks."""
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )

        modal = AsyncMock()
        modal.locator.return_value.all = AsyncMock(return_value=[])

        with (
            patch(f"{MODULE}.find_element_safely", new_callable=AsyncMock) as mock_find,
            patch.object(
                easy_applier, "_click_continue_applying_button", new_callable=AsyncMock
            ),
            patch.object(
                easy_applier, "_is_already_applied",
                new_callable=AsyncMock, return_value=False,
            ),
            patch(f"{MODULE}.async_pause", new_callable=AsyncMock),
        ):
            # First 3 selectors miss, [role="dialog"] is the 4th and matches.
            # Subsequent find calls (for form elements) get a usable mock too.
            mock_find.side_effect = [None, None, None, modal, modal, modal, modal]

            # Call _fill_up directly — it returns None normally
            await easy_applier._fill_up(job)

            attempted_selectors = [call.args[0] for call in mock_find.await_args_list]
            aria_selectors = [
                s for s in attempted_selectors
                if '[role=' in s or 'aria-modal' in s or 'aria-labelledby' in s
            ]
            assert aria_selectors, (
                f"Expected at least one ARIA-based selector; got: {attempted_selectors}"
            )
            # ARIA selectors must come before the legacy .jobs-easy-apply-modal__content
            aria_idx = next(
                (i for i, s in enumerate(attempted_selectors)
                 if '[role=' in s or 'aria-modal' in s or 'aria-labelledby' in s),
                None,
            )
            legacy_idx = next(
                (i for i, s in enumerate(attempted_selectors)
                 if 'jobs-easy-apply-modal' in s),
                None,
            )
            if legacy_idx is not None:
                assert aria_idx is not None and aria_idx < legacy_idx, (
                    f"ARIA selectors must come before legacy class; "
                    f"aria_idx={aria_idx}, legacy_idx={legacy_idx}, all={attempted_selectors}"
                )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestModalDetection::test_fill_up_attempts_aria_selectors_before_legacy -v`

Expected: FAIL — current modal_selectors has no ARIA selectors.

- [ ] **Step 3: Replace the class-based modal selectors with ARIA-based ones**

Edit `src/job_manager/linkedin/easy_applier_linkedin.py` lines 456-460 (the `modal_selectors` list inside `_fill_up`). Replace this block:

```python
            # Try multiple selectors to find the modal content
            modal_selectors = [
                ".jobs-easy-apply-modal__content",  # CSS selector
                ".artdeco-modal__content",  # Fallback CSS
                "//*[contains(@class, 'jobs-easy-apply-modal__content')]",  # XPath
            ]
```

With:

```python
            # ponytail: LinkedIn moved to hashed CSS class names in 2026, so
            # .jobs-easy-apply-modal__content rarely matches anymore. ARIA
            # semantics ([role=dialog], [aria-modal=true]) are stable across
            # builds. ARIA first; legacy classes last as a safety net.
            modal_selectors = [
                '[role="dialog"]',                                # ARIA dialog role
                '[aria-modal="true"]',                            # Explicit ARIA modal flag
                '[aria-labelledby*="apply" i]',                   # Labelled with apply-related text
                ".jobs-easy-apply-modal__content",                # Legacy class (rarely matches now)
                ".artdeco-modal__content",                        # Legacy generic modal class
                "//*[contains(@class, 'jobs-easy-apply-modal__content')]",  # Legacy XPath
            ]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestModalDetection::test_fill_up_attempts_aria_selectors_before_legacy -v`

Expected: PASS, 1 test passed.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 833 passed (831 + 1 Task 1 + 1 Task 2 = 833). Investigate any regression.

- [ ] **Step 7: Commit**

```bash
git add src/job_manager/linkedin/easy_applier_linkedin.py tests/test_easy_applier_unit.py
git commit -m "fix(linkedin): find Easy Apply modal via ARIA, not hashed classes

LinkedIn now emits hashed CSS class names (e.g. _4c6a6fe1 _1ef6ccae
...), so legacy selectors like .jobs-easy-apply-modal__content rarely
match. ARIA semantics ([role=dialog], [aria-modal=true]) are stable
across LinkedIn's build changes. Add ARIA-first selectors; keep legacy
classes as last-resort fallbacks."
```

---

## Self-Review

1. **Spec coverage** — three goal items, two tasks (the third is the manual headed-mode verification, handled outside this plan). Task 1 = visibility gate; Task 2 = ARIA selectors.
2. **Placeholders** — none.
3. **Type/signature consistency** — `bounding_box()` returns `dict | None` per Playwright; both branches handled. `find_element_safely` signature unchanged.
4. **Same-file edits** — Tasks 1 and 2 modify `easy_applier_linkedin.py` at different lines (261-267 vs 456-460). Serial execution avoids edit conflicts; each commit is independently reviewable.
5. **Test ordering** — the side_effect list `[None, None, None, modal, modal, modal, modal]` reflects: 3 ARIA/legacy selector failures, then `[role="dialog"]` matches at index 3 (since it's the 4th in the list), then the form-element lookups also match the same modal.

No issues found. Plan ready.
