# Easy Apply Button Missing — Debug Capture Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `LinkedInEasyApplier._find_easy_apply_button` cannot find or click the Easy Apply button, capture the page state to `data/debug/` so the next failure produces ground truth instead of a silent skip.

**Architecture:** Single additive call to the existing `debug_capture` helper on the False return path of `_find_easy_apply_button`. No signature change, no behavior change, no new dependency. This is a diagnostic step; the actual selector fix follows after re-running the bot and inspecting the captured HTML.

**Tech Stack:** Python 3.12, Playwright async, `debug_capture` helper (already in `src/utils/browser_utils.py`), pytest with `pytest-asyncio`.

## Global Constraints

- `DEBUG_MODE = True` in `config/app_config.py` — `debug_capture` writes artifacts to `data/debug/`.
- `debug_capture` is a no-op when `DEBUG_MODE=False`; existing call sites rely on this.
- 830 tests currently pass (`uv run pytest tests/`). Plan must keep this green.
- No changes outside `_find_easy_apply_button` and one new test method.
- Spec file `docs/superpowers/specs/2026-06-30-easy-apply-button-capture-design.md` was skipped per user direction; the design lives in this plan header.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/job_manager/linkedin/easy_applier_linkedin.py` | Modify (line 283) | Add `debug_capture` call on False return path of `_find_easy_apply_button` |
| `tests/test_easy_applier_unit.py` | Modify (append) | New test verifying `debug_capture` is called when the button isn't found |

No new files. No new modules. No new dependencies.

---

### Task 1: Capture page state on Easy Apply button miss

**Files:**
- Modify: `src/job_manager/linkedin/easy_applier_linkedin.py:279-283` (the False return block of `_find_easy_apply_button`)
- Modify: `tests/test_easy_applier_unit.py` (append new test method to `TestEasyApplyButtonDetection` class)

**Interfaces:**
- Consumes: existing `debug_capture(page: Page, label: str)` import at the top of `easy_applier_linkedin.py`; existing `easy_applier` fixture in `tests/test_easy_applier_unit.py`
- Produces: When `_find_easy_apply_button` exhausts both attempts, `debug_capture(self.page, "easy_apply_button_missing")` runs before `return False`

- [ ] **Step 1: Write the failing test**

Append this method to the `TestEasyApplyButtonDetection` class in `tests/test_easy_applier_unit.py` (right after `test_find_easy_apply_button_clicks_and_returns_true`, before the next class):

```python
    @pytest.mark.asyncio
    async def test_find_easy_apply_button_captures_debug_when_not_found(self, easy_applier):
        """When the button is not found, debug_capture must run before returning False."""
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )

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
            mock_find.return_value = []  # no buttons at all

            result = await easy_applier._find_easy_apply_button(job)

            assert result is False
            mock_capture.assert_awaited_once()
            # Second positional arg of the await is the label
            label_arg = mock_capture.await_args.args[1] if len(mock_capture.await_args.args) > 1 else mock_capture.await_args.kwargs.get("label")
            assert label_arg == "easy_apply_button_missing"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestEasyApplyButtonDetection::test_find_easy_apply_button_captures_debug_when_not_found -v`

Expected: FAIL with `AssertionError: assert False` on the `result is False` line OR `mock_capture.assert_awaited_once()` failing because `debug_capture` was never called. (Both are acceptable failure modes — the point is the test catches the missing capture.)

- [ ] **Step 3: Add the debug_capture call**

Edit `src/job_manager/linkedin/easy_applier_linkedin.py`, replacing the final block of `_find_easy_apply_button` (lines 279-283):

The exact `old_string` to match (preserve whitespace, 4-space indent inside the method):

```
        page_url = self.page.url
        logger.warning(
            f"No clickable 'Easy Apply' button found after 2 attempts. page url: {page_url}"
        )
        return False
```

The exact `new_string`:

```
        page_url = self.page.url
        logger.warning(
            f"No clickable 'Easy Apply' button found after 2 attempts. page url: {page_url}"
        )
        # ponytail: capture before returning False so DEBUG_MODE produces ground truth
        # for the next selector change. The capture is a no-op when DEBUG_MODE=False.
        await debug_capture(self.page, "easy_apply_button_missing")
        return False
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_easy_applier_unit.py::TestEasyApplyButtonDetection::test_find_easy_apply_button_captures_debug_when_not_found -v`

Expected: PASS, 1 test passed.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 831 passed (one new test added, 830 existing). If the count is off, investigate before continuing.

- [ ] **Step 6: Commit**

```bash
git add src/job_manager/linkedin/easy_applier_linkedin.py tests/test_easy_applier_unit.py
git commit -m "fix(linkedin): capture page state when Easy Apply button is not found

DEBUG_MODE=True was on but the False branch of _find_easy_apply_button
returned silently, leaving no ground truth for selector fixes. Adding
debug_capture on the miss path produces a screenshot + HTML in
data/debug/ for the next failure. No-op when DEBUG_MODE=False."
```

---

## Self-Review

1. **Spec coverage** — design has two requirements: (a) capture on False, (b) no behavior change. Task 1 Step 3 implements (a); Steps 4-5 verify (b) via the new test and the existing 830-test suite.
2. **Placeholders** — none. Every step has exact code, exact commands, exact expected output.
3. **Type/signature consistency** — `debug_capture(page, label)` matches the import in `easy_applier_linkedin.py` and the mock target in `tests/test_easy_applier_linkedin.py:250` (`patch("src.job_manager.linkedin.easy_applier_linkedin.debug_capture")`). The test asserts on positional arg index 1, matching the existing call pattern at line 187 (`await debug_capture(self.page, "job_easy_apply_error")`).
4. **No test regressions** — only adding one test method to an existing class; no other test files touched.

No issues found. Plan ready for execution.
