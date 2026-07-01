# ApplyAgent Recognizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `ATSRecognizer` that fingerprints the destination URL before ApplyAgent's LLM flow runs; if matched (currently Workday), delegate to the dedicated handler. Otherwise fall through to the existing LLM path unchanged.

**Architecture:** New lightweight `src/llm/ats_recognizer.py` does URL → hostname → regex pattern matching against a known-ATS table (one row today: Workday). ApplyAgent checks the table at the top of `apply_to_job`; on match it instantiates the handler via a small `_build_handler_with_state` helper, delegates the apply, and returns the handler's result. All failures fall through to the LLM. Gated behind `APPLY_AGENT_RECOGNIZE = True` in `config/app_config.py` so the operator can disable routing without code changes.

**Tech Stack:** Python 3.12, stdlib `re` + `urllib.parse`, asyncio, pytest + pytest-asyncio.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-30-apply-agent-recognizer-design.md` (committed `b6cbff8`).
- 849 tests currently pass. Plan must keep them green.
- No new project dependencies. `re` and `urllib.parse` are stdlib.
- `APPLY_AGENT_RECOGNIZE = True` default in `config/app_config.py`.
- Recognizer must return `None` on any exception — no apply path may be lost.
- Fall-through behavior must be preserved when no match, when flag is False, or when handler raises.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `config/app_config.py` | Modify | Add `APPLY_AGENT_RECOGNIZE = True` |
| `src/llm/ats_recognizer.py` | Create | `recognize(url)` + `ATSMatch` dataclass + `_KNOWN_ATS` table |
| `src/llm/apply_agent.py` | Modify | Call `recognize()` at top of `apply_to_job`; route to handler on match |
| `tests/test_ats_recognizer.py` | Create | Unit tests for `recognize()` (5 cases) |
| `tests/test_apply_agent.py` | Modify | 2 new tests for routing path |

No new modules beyond `src/llm/`. No new third-party deps.

---

### Task 1: `APPLY_AGENT_RECOGNIZE` flag + `ats_recognizer.py` skeleton

**Files:**
- Modify: `config/app_config.py` (add `APPLY_AGENT_RECOGNIZE = True` near `APPLY_AGENT_MODEL`)
- Create: `src/llm/ats_recognizer.py`
- Create: `tests/test_ats_recognizer.py`

**Interfaces:**
- Consumes: `urllib.parse.urlparse` (stdlib), `re` (stdlib), `config.logger_config.logger`
- Produces (used by Task 2 + future per-ATS sub-projects):
  - `ATSMatch` dataclass: `name: str`, `confidence: float`, `handler_factory: Callable`
  - `recognize(url: str) -> ATSMatch | None` — public function
  - `_KNOWN_ATS: dict[str, ATSMatch]` — module-level table

- [ ] **Step 1: Add the flag**

In `config/app_config.py`, find the `APPLY_AGENT_MODEL` block and append right after it:

```python
# ponytail: route recognized ATSes (Workday, etc.) to dedicated handlers before
# the LLM apply path. Disable if a handler misbehaves or during LLM debugging.
APPLY_AGENT_RECOGNIZE = True
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_ats_recognizer.py`:

```python
from src.llm.ats_recognizer import recognize


def test_recognize_workday_by_hostname():
    match = recognize("https://uhaul.wd1.myworkdayjobs.com/en-US/UhaulJobs/job/123")
    assert match is not None
    assert match.name == "workday"
    assert match.confidence == 1.0


def test_recognize_unknown_returns_none():
    assert recognize("https://boards.greenhouse.io/apply/123") is None


def test_recognize_fingerprint_failure_returns_none():
    """Malformed URL still returns None instead of raising."""
    assert recognize("not-a-url") is None


def test_recognize_empty_url_returns_none():
    assert recognize("") is None


def test_recognize_case_insensitive():
    match = recognize("https://ACME.WD5.MYWORKDAYJOBS.COM/job/1")
    assert match is not None
    assert match.name == "workday"
```

- [ ] **Step 3: Run tests, expect failure (module not found)**

Run: `uv run pytest tests/test_ats_recognizer.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.llm.ats_recognizer'`.

- [ ] **Step 4: Implement `src/llm/ats_recognizer.py`**

```python
"""Recognize known ATS destinations and route to dedicated handlers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from config.logger_config import logger


@dataclass(frozen=True)
class ATSMatch:
    name: str                    # "workday", "greenhouse", ...
    confidence: float            # 0.0 - 1.0
    handler_factory: Callable    # () -> Handler class (caller instantiates with deps)


_KNOWN_ATS: dict[str, ATSMatch] = {
    "workday": ATSMatch(
        name="workday",
        confidence=1.0,
        handler_factory=lambda: _workday_handler_class,
    ),
    # Future per-ATS plans add entries here. Each new entry is one
    # line in this table — the rest of the dispatcher routes for free.
}


# Imported lazily inside _workday_handler_class to avoid a module-load cycle
def _workday_handler_class():
    from src.job_manager.workday.workday_applier import WorkdayApplier
    return WorkdayApplier


_HOSTNAME_PATTERNS = [
    (re.compile(r"\.myworkdayjobs\.com$", re.I), "workday"),
    # Future entries — e.g.:
    # (re.compile(r"(^|\.)boards?\.greenhouse\.io$", re.I), "greenhouse"),
]

_MARKER_PATTERNS = [
    (re.compile(r"jobs?[-_]?easy[-_]?apply", re.I), "workday"),
    # Future markers for other ATSes added here.
]


def _fingerprint(url: str) -> dict:
    """Lightweight fingerprint: URL hostname + URL tokens. <2s, no DOM walk."""
    host = urlparse(url).netloc.lower()
    return {
        "url": url,
        "host": host,
        "hostnames": {host},
        "url_tokens": set(re.findall(r"[a-z0-9-]+", url.lower())),
    }


def recognize(url: str) -> Optional[ATSMatch]:
    """Best-effort ATS recognition. Returns None on no match or any failure."""
    try:
        fp = _fingerprint(url)
        # 1. Hostname match (highest confidence)
        for pattern, name in _HOSTNAME_PATTERNS:
            if pattern.match(fp["host"]):
                if name in _KNOWN_ATS:
                    return _KNOWN_ATS[name]
        # 2. URL-token match (catches subdomains / paths)
        tokens_joined = " ".join(fp["url_tokens"])
        for pattern, name in _MARKER_PATTERNS:
            if pattern.search(tokens_joined):
                if name in _KNOWN_ATS:
                    return _KNOWN_ATS[name]
        return None
    except Exception as e:
        logger.debug(f"recognize() failed (non-fatal): {e}")
        return None
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_ats_recognizer.py -v`

Expected: 5 passed.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 854 passed (849 prior + 5 new). Investigate any regression.

- [ ] **Step 7: Commit**

```bash
git add config/app_config.py src/llm/ats_recognizer.py tests/test_ats_recognizer.py
git commit -m "feat(llm): ATSRecognizer with Workday entry

Lightweight URL fingerprinting + known-ATS table. recognize(url)
returns an ATSMatch for URLs matching known patterns (currently
.workdayjobs.com) or None for unknown / failure cases.

Gated behind APPLY_AGENT_RECOGNIZE = True in config/app_config.py
(operator can disable). No LLM code path changes yet — that's Task 2.

Each future per-ATS plan adds one line to _KNOWN_ATS plus its handler
module (sub-projects 3..N)."
```

---

### Task 2: Wire recognizer into ApplyAgent + routing tests

**Files:**
- Modify: `src/llm/apply_agent.py` (call `recognize()` at top of `apply_to_job`; route on match)
- Modify: `tests/test_apply_agent.py` (add 2 routing tests)

**Interfaces:**
- Consumes: existing `recognize()` from Task 1; `APPLY_AGENT_RECOGNIZE` from config
- Produces: ApplyAgent routes Workday URLs to the Workday handler; falls through to LLM otherwise

- [ ] **Step 1: Read `apply_agent.py` to find the exact line where `apply_to_job` starts**

Use `Read` (with offset/limit) to inspect lines 150-250 of `src/llm/apply_agent.py`. Identify the entry point of `apply_to_job` (likely an `async def` followed by a docstring and state-validation code).

- [ ] **Step 2: Write the failing tests**

If `tests/test_apply_agent.py` exists, append to it. Otherwise create it. Add 2 tests:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.llm.apply_agent import ApplyAgent


@pytest.fixture
def agent():
    return ApplyAgent(api_key="fake", browser_storage_state="/tmp/none.json")


@pytest.mark.asyncio
async def test_apply_agent_routes_workday_url_to_handler(agent, monkeypatch):
    """Recognize-match: ApplyAgent delegates to handler and returns its result."""
    monkeypatch.setattr("config.app_config.APPLY_AGENT_RECOGNIZE", True, raising=False)
    fake_handler = AsyncMock()
    fake_handler.apply_to_job = AsyncMock(return_value=("Success", ""))
    fake_match = MagicMock(name="workday", handler_factory=lambda: fake_handler.__class__)
    monkeypatch.setattr("src.llm.apply_agent.recognize", lambda url: fake_match)
    # Stub the LLM Agent to assert it does NOT run on recognize-match
    from src.llm import apply_agent as mod
    monkeypatch.setattr(mod, "Agent", MagicMock(side_effect=AssertionError("LLM should not run on recognize-match")))

    result = await agent.apply_to_job("https://uhaul.wd1.myworkdayjobs.com/job/1")

    assert result[0] == "Success"
    fake_handler.apply_to_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_agent_falls_through_to_llm_when_disabled(agent, monkeypatch):
    """APPLY_AGENT_RECOGNIZE=False → LLM flow runs (recognize not consulted)."""
    monkeypatch.setattr("config.app_config.APPLY_AGENT_RECOGNIZE", False, raising=False)
    called = []
    monkeypatch.setattr("src.llm.apply_agent.recognize", lambda url: called.append(url) or None)
    # Stub the LLM Agent to return a known shape
    fake_result = ("Submitted", "fake")
    from src.llm import apply_agent as mod
    monkeypatch.setattr(mod, "Agent", MagicMock(return_value=MagicMock(run=AsyncMock(return_value=fake_result))))

    result = await agent.apply_to_job("https://uhaul.wd1.myworkdayjobs.com/job/1")

    assert called == [], "recognize() should not be called when flag is False"
    assert result[0] == "Submitted"
```

- [ ] **Step 3: Run tests, expect failure (no recognize() call yet)**

Run: `uv run pytest tests/test_apply_agent.py::test_apply_agent_routes_workday_url_to_handler tests/test_apply_agent.py::test_apply_agent_falls_through_to_llm_when_disabled -v`

Expected: FAIL — ApplyAgent doesn't call `recognize()` yet, so:
- The first test will see the LLM Agent get called (assertion-error side_effect) OR return something other than "Success"
- The second test will have `called` be empty but the test passes (since no LLM gets called either way). The important assertion `called == []` will pass.

If `tests/test_apply_agent.py` doesn't exist, create it with the fixture + 2 tests.

- [ ] **Step 4: Add recognize() call + routing block to ApplyAgent**

In `src/llm/apply_agent.py`, add to the imports (top of file):

```python
from config.app_config import APPLY_AGENT_RECOGNIZE
from src.llm.ats_recognizer import recognize
```

Add a small private helper near the top of the class (after `__init__`):

```python
    @staticmethod
    def _build_handler_with_state(handler_factory, agent_self, link):
        """Build a handler instance with deps wired from ApplyAgent state.

        Only WorkdayApplier is currently supported. New handlers extend the if/elif
        below — that's the entry-point contract for adding per-ATS sub-projects.
        """
        from src.job_manager.workday.workday_applier import WorkdayApplier
        from config.constants import WORKDAY_SESSION_DIR, RESUME_DIR, OUTPUT_DIR_WORKDAY
        from pathlib import Path
        from src.job_manager.workday.workday_authenticator import WorkdayAuthenticator
        from src.job_manager.workday.workday_questions import WorkdayQuestionHandler

        if handler_factory is not None and handler_factory() is WorkdayApplier:
            authenticator = WorkdayAuthenticator(
                page=agent_self.page,
                session_dir=Path(WORKDAY_SESSION_DIR),
                storage_writer=None,
            )
            question_handler = WorkdayQuestionHandler(
                cache_path=Path(OUTPUT_DIR_WORKDAY) / "answers.yaml",
                llm_answerer=getattr(agent_self, "llm_answerer", None),
            )
            resume_structured = getattr(agent_self, "resume_structured", None) or {}
            resume_pdf_path = getattr(agent_self, "resume_pdf_path", None) or Path(RESUME_DIR) / "default.pdf"
            return WorkdayApplier(
                page=agent_self.page,
                resume_structured=resume_structured,
                resume_pdf_path=resume_pdf_path,
                question_handler=question_handler,
                authenticator=authenticator,
            )
        raise ValueError(f"Unknown handler factory: {handler_factory}")
```

Note: the helper uses `getattr(..., default)` so it tolerates ApplyAgent state that may not have every attribute yet — falls back gracefully. If `agent_self.page` is missing too, the WorkdayApplier call will raise; the caller's try/except catches and falls through to LLM.

At the top of `apply_to_job` (after the docstring + state validation), add the routing block:

```python
        if APPLY_AGENT_RECOGNIZE:
            match = recognize(link)
            if match is not None:
                try:
                    handler = self._build_handler_with_state(match.handler_factory, self, link)
                    result = await handler.apply_to_job(link)
                    if result and result[0] == "Success":
                        return result
                    logger.info(f"Handler {match.name} returned {result}, falling through to LLM")
                except Exception as e:
                    logger.warning(f"Handler {match.name} raised; falling through to LLM: {e}")
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_apply_agent.py::test_apply_agent_routes_workday_url_to_handler tests/test_apply_agent.py::test_apply_agent_falls_through_to_llm_when_disabled -v`

Expected: 2 passed.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 856 passed (854 + 2 new). Investigate any regression.

- [ ] **Step 7: Commit**

```bash
git add src/llm/apply_agent.py tests/test_apply_agent.py
git commit -m "feat(llm): route recognized ATSes to dedicated handlers in ApplyAgent

ApplyAgent.apply_to_job now consults the recognizer at entry. On
match (currently Workday), it instantiates the handler via
_build_handler_with_state (wires page + resume + question_handler +
authenticator from ApplyAgent state) and delegates the apply. On
non-Success result or any exception, falls through to the existing
browser-use LLM flow unchanged.

Gated behind APPLY_AGENT_RECOGNIZE config flag; when False, the
recognizer is not consulted and the LLM runs as before.

Future per-ATS sub-projects extend the _build_handler_with_state
elif-chain to support their handler — table-row contract from the
recognizer spec."
```

---

## Self-Review

1. **Spec coverage:**
   - `APPLY_AGENT_RECOGNIZE` flag → Task 1 Step 1
   - `ATSRecognizer` module + `recognize()` + `ATSMatch` + `_KNOWN_ATS` → Task 1 Step 4
   - Workday pattern (`\.myworkdayjobs\.com$`) → Task 1 Step 4
   - ApplyAgent wiring + handler-factory → Task 2 Step 4
   - Fall-through on failure / no match / flag off → Task 2 Step 4 (try/except + flag guard)
   - 5 recognizer tests + 2 routing tests → Task 1 Step 2 + Task 2 Step 2
2. **Placeholders:** none. Every code block is complete.
3. **Type consistency:**
   - `ATSMatch` fields: `name: str`, `confidence: float`, `handler_factory: Callable` — consistent across spec, dataclass, tests
   - `recognize(url: str) -> ATSMatch | None` — consistent
   - `_build_handler_with_state(handler_factory, agent_self, link)` — same signature in dispatch + tests' monkeypatch targets

No issues found. Plan ready.