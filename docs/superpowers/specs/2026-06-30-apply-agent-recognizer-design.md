# ApplyAgent Site Recognition + Routing

**Date:** 2026-06-30
**Status:** Design — pending user approval
**Author:** brainstorming session
**Scope:** Sub-project 2 of N — improves ApplyAgent reliability before more per-ATS handlers are added

## Goal

Reduce token burn and stuck-agent risk on the browser-use LLM apply path by recognizing known ATS destinations and routing them to dedicated handlers before the LLM takes over. Initial entry: Workday. Future per-ATS plans (sub-projects 3..N) add entries to the same table.

## Background

`ApplyAgent` (`src/llm/apply_agent.py`, 354 lines) is the fallback path for external apply URLs that don't match `myworkdayjobs.com` and aren't in `EXTERNAL_APPLY_SKIP_ATSES`. It spawns a `browser_use` LLM agent with `max_failures=3` + `step_timeout=60` (recently added), lets the LLM drive the apply flow, and reports the outcome.

The pattern works, but every external ATS the bot encounters burns LLM tokens + multi-step browser time, even for ATSes we'd automate better with a structured handler. Workday already has a full multi-phase handler (`src/job_manager/workday/workday_applier.py`) but ApplyAgent still goes through LLM for the URL detection step — Workday's own URL routing is in the LinkedIn/Indeed dispatchers, but the moment `run_discovery` uncovers a new ATS, every apply attempt on that ATS hits the LLM.

Adding per-ATS recognition up-front (before the LLM starts) lets us:
- Short-circuit the LLM for known ATSes (Workday today, future ATSes as handlers are added).
- Build the recognition table incrementally — each new per-ATS spec adds one entry.
- Keep the LLM path as the catch-all fallback.

## Architecture

A new `ATSRecognizer` helper sits in front of `ApplyAgent.apply_to_job`. On entry, the recognizer captures a quick fingerprint (network hostnames + ats-marker-classes only — no DOM walk, ~2s vs ~10s for full capture) and matches it against a known-ATS table. If matched, the recognizer instantiates the handler class and delegates the apply. If not matched (or fingerprint/handler failure), the existing LLM flow runs unchanged.

A config flag `APPLY_AGENT_RECOGNIZE = True` (default on) gates the routing step. Setting it to `False` disables routing without code changes — useful when debugging the LLM path or when a handler misbehaves.

## Components

### `src/llm/ats_recognizer.py` (new, ~120 lines)

```python
"""Recognize known ATS destinations and route to dedicated handlers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from config.logger_config import logger


@dataclass(frozen=True)
class ATSMatch:
    name: str                    # "workday", "greenhouse", ...
    confidence: float            # 0.0 - 1.0
    handler_factory: Callable    # () -> Handler instance


_KNOWN_ATS: dict[str, ATSMatch] = {
    "workday": ATSMatch(
        name="workday",
        confidence=1.0,
        handler_factory=lambda: _build_workday(),
    ),
    # Future per-ATS plans add entries here. Each new entry is one
    # line in this table — the rest of the dispatcher routes for free.
}


_HOSTNAME_PATTERNS = [
    (re.compile(r"\.myworkdayjobs\.com$", re.I), "workday"),
    # Future entries:
    # (re.compile(r"(^|\.)boards?\.greenhouse\.io$", re.I), "greenhouse"),
]

_MARKER_PATTERNS = [
    (re.compile(r"jobs?[-_]?easy[-_]?apply", re.I), "workday"),
    # Future markers for other ATSes added here.
]


def _build_workday():
    """Construct WorkdayApplier — see wiring section for full deps."""
    from src.job_manager.workday.workday_applier import WorkdayApplier
    # The handler is instantiated lazily by apply_agent with the
    # correct page + resume + question_handler + authenticator.
    return WorkdayApplier  # caller instantiates with args


def _fingerprint(url: str) -> dict:
    """Lightweight fingerprint: just URL hostname + URL tokens."""
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

Note: the lightweight fingerprint deliberately skips the network-hostname capture + DOM walk that `capture_fingerprint` does. URL + hostname + URL-tokens are enough to disambiguate known ATSes; the heavy fingerprint stays in `src/discovery/ats_discoverer.py` where it's needed.

### `src/llm/apply_agent.py` (modify)

Add to the top of `apply_agent.py`:

```python
from config.app_config import APPLY_AGENT_RECOGNIZE
from src.llm.ats_recognizer import recognize
```

Modify the entry of `apply_to_job`. The exact line numbers depend on the file's current state — implementer should read first. Add this block at the top of the method body (after the existing docstring + state-validation code):

```python
        if APPLY_AGENT_RECOGNIZE:
            match = recognize(link)
            if match is not None:
                try:
                    handler_factory = match.handler_factory
                    handler = _build_handler_with_state(handler_factory, self, link)
                    result = await handler.apply_to_job(link)
                    if result and result[0] == "Success":
                        return result
                    logger.info(f"Handler {match.name} returned {result}, falling through to LLM")
                except Exception as e:
                    logger.warning(f"Handler {match.name} raised; falling through to LLM: {e}")
```

Where `_build_handler_with_state(handler_factory, agent_self, link)` is a small private helper in `apply_agent.py` that wires the handler's dependencies from `agent_self`'s state (page, resume, question_handler, authenticator):

```python
def _build_handler_with_state(handler_factory, agent_self, link):
    """Build a handler instance with deps wired from ApplyAgent state."""
    from src.job_manager.workday.workday_applier import WorkdayApplier
    from src.job_manager.workday.workday_authenticator import WorkdayAuthenticator
    from src.job_manager.workday.workday_questions import WorkdayQuestionHandler
    from config.constants import WORKDAY_SESSION_DIR, RESUME_DIR, OUTPUT_DIR_WORKDAY

    if handler_factory is WorkdayApplier:
        authenticator = WorkdayAuthenticator(
            page=agent_self.page,
            session_dir=Path(WORKDAY_SESSION_DIR),
            storage_writer=None,  # bot's session save is already wired
        )
        question_handler = WorkdayQuestionHandler(
            cache_path=Path(OUTPUT_DIR_WORKDAY) / "answers.yaml",
            llm_answerer=agent_self.llm_answerer,
        )
        resume_structured = agent_self.resume_structured or {}
        resume_pdf_path = Path(RESUME_DIR) / "default.pdf"
        return WorkdayApplier(
            page=agent_self.page,
            resume_structured=resume_structured,
            resume_pdf_path=resume_pdf_path,
            question_handler=question_handler,
            authenticator=authenticator,
        )
    raise ValueError(f"Unknown handler factory: {handler_factory}")
```

The exact attribute names on `agent_self` depend on ApplyAgent's current `__init__` — implementer should read the file first.

### `config/app_config.py` (modify)

Add near the existing `APPLY_AGENT_MODEL` flag:

```python
# ponytail: route recognized ATSes (Workday, etc.) to dedicated handlers before
# the LLM apply path. Disable if a handler misbehaves or during LLM debugging.
APPLY_AGENT_RECOGNIZE = True
```

### `tests/test_ats_recognizer.py` (new)

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

### `tests/test_apply_agent.py` (extend existing file)

Add 2 tests:

```python
@pytest.mark.asyncio
async def test_apply_agent_routes_workday_url_to_handler(monkeypatch):
    """Recognize-match: ApplyAgent delegates to handler and returns its result."""
    monkeypatch.setattr("config.app_config.APPLY_AGENT_RECOGNIZE", True, raising=False)
    agent = ApplyAgent(api_key="fake", browser_storage_state="/tmp/none.json")
    fake_handler = AsyncMock()
    fake_handler.apply_to_job = AsyncMock(return_value=("Success", ""))
    monkeypatch.setattr("src.llm.ats_recognizer.recognize", lambda url: MagicMock(name="workday", handler_factory=lambda: fake_handler.__class__))
    # Stub the LLM agent so we'd notice if it ran
    from src.llm import apply_agent as mod
    monkeypatch.setattr(mod, "Agent", MagicMock(return_value=MagicMock(run=AsyncMock(side_effect=AssertionError("LLM should not run on recognize-match")))))
    result = await agent.apply_to_job("https://uhaul.wd1.myworkdayjobs.com/job/1")
    assert result[0] == "Success"
    fake_handler.apply_to_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_agent_falls_through_to_llm_when_disabled(monkeypatch):
    """APPLY_AGENT_RECOGNIZE=False → LLM flow runs (recognize not consulted)."""
    monkeypatch.setattr("config.app_config.APPLY_AGENT_RECOGNIZE", False, raising=False)
    agent = ApplyAgent(api_key="fake", browser_storage_state="/tmp/none.json")
    called = []
    monkeypatch.setattr("src.llm.ats_recognizer.recognize", lambda url: called.append(url) or None)
    # Stub LLM Agent to return a known shape
    fake_result = ("Submitted", "fake")
    from src.llm import apply_agent as mod
    monkeypatch.setattr(mod, "Agent", MagicMock(return_value=MagicMock(run=AsyncMock(return_value=fake_result))))
    result = await agent.apply_to_job("https://uhaul.wd1.myworkdayjobs.com/job/1")
    assert called == [], "recognize() should not be called when flag is False"
```

## Data flow

```
1. Dispatcher hits apply_url → ApplyAgent.apply_to_job(url).
2. APPLY_AGENT_RECOGNIZE check.
3. recognizer.recognize(url) → ATSMatch(name="workday", confidence=1.0) or None.
4. Match + flag:
   a. Build handler (factory wires deps from ApplyAgent.__init__'s state).
   b. handler.apply_to_job(url) → result.
   c. If result[0] == "Success": return.
   d. Otherwise: log, fall through.
5. No match OR disabled:
   - existing LLM flow runs (current behavior preserved).
6. Result returned to dispatcher.
```

## Error handling

- `recognize()` returns `None` on any exception (URL parse fail, regex fail, dict miss) — fall through to LLM, no apply lost.
- Handler raises → caught, logged, fall through to LLM attempt.
- Handler returns non-Success → logged, fall through to LLM attempt.
- ApplyAgent state missing dependencies the handler factory needs → factory raises; caught, fall through.

## Testing

Unit tests in `tests/test_ats_recognizer.py` (new):
- `test_recognize_workday_by_hostname`
- `test_recognize_unknown_returns_none`
- `test_recognize_fingerprint_failure_returns_none`
- `test_recognize_empty_url_returns_none`
- `test_recognize_case_insensitive`

Unit tests in `tests/test_apply_agent.py` (extended):
- `test_apply_agent_routes_workday_url_to_handler`
- `test_apply_agent_falls_through_to_llm_when_disabled`

Existing 849 tests must keep passing. Target after this spec: 854+ tests.

## Out of scope

- Adding more entries to `_KNOWN_ATS` (each is its own per-ATS sub-project).
- Auto-populating `EXTERNAL_APPLY_SKIP_ATSES` from repeated handler failures.
- LLM cost ceiling or stuck detection (separate brainstorming cycles).
- Network-hostname capture in the recognizer (deliberately skipped — URL+host is enough for known ATSes).

## Decomposition note

Sub-project 2 of N. Each future per-ATS brainstorming cycle (sub-projects 3..N) will:
1. Add a per-ATS handler in `src/job_manager/<ats>/` (Workday-style phase automation).
2. Add one entry to `_KNOWN_ATS` (the table-row contract).
3. Add 1-2 tests for the recognize-match.

This spec only establishes the recognizer infrastructure + Workday entry. Subsequent ATS work is incremental and low-cost.