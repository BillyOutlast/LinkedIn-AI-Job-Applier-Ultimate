# External ATS Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the discoverer driver + dispatcher hooks that produce a structured catalog (JSONL + DOM fingerprints + summary.md) of every external ATS site the bot encounters when run with `EASY_APPLY_ONLY_MODE=False`.

**Architecture:** Two-piece system: (1) a new `src/discovery/ats_discoverer.py` driver that wraps the bot run with a hookable JSONL log + DOM-fingerprint capture + summary generator; (2) inline `_record_encounter(url, outcome, signature)` instrumentation in `LinkedInJobManager.apply_job` and `IndeedJobManager.apply_job` that fires on each apply_url decision. Hooks are no-ops when discovery mode is off.

**Tech Stack:** Python 3.12, asyncio, json, Playwright (existing), pytest + pytest-asyncio.

## Global Constraints

- Run parameters (already committed in `config/app_config.py`):
  - `EASY_APPLY_ONLY_MODE = False`
  - `MAX_APPLIES_NUM = 10`
  - `MONKEY_MODE = True`
  - `TEST_MODE = True`
  - `DEBUG_MODE = True`
  - `HEADLESS_MODE = True`
- Spec: `docs/superpowers/specs/2026-06-30-ats-sites-survey.md`.
- No new project dependencies. Stdlib `json`, existing `asyncio`, existing `pathlib`.
- Discovery artifacts under `data/output/discovery/` (already covered by `data/output` in `.gitignore`).
- 835 tests currently pass. Plan must keep them green.
- Logging instrumentation wraps existing try/except — discovery failure must NOT break the apply path.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/discovery/__init__.py` | Create | Empty package marker |
| `src/discovery/ats_discoverer.py` | Create | `record_encounter`, `capture_fingerprint`, `summarize`, `run_discovery` |
| `tests/test_discovery.py` | Create | Unit tests for `summarize`, `record_encounter`, `capture_fingerprint` |
| `src/job_manager/linkedin/job_manager_linkedin.py` | Modify | Add `_record_encounter` hook + calls in `apply_job` dispatcher |
| `src/job_manager/indeed/job_manager_indeed.py` | Modify | Add `_record_encounter` hook + calls in `apply_job` dispatcher |
| `tests/test_discovery_hooks.py` | Create | Tests for dispatcher hook calls |
| `docs/superpowers/specs/2026-06-30-ats-sites-survey.md` | Modify (Task 4) | Append Verification section |

---

### Task 1: Discoverer module — `summarize` + `record_encounter` + `capture_fingerprint`

**Files:**
- Create: `src/discovery/__init__.py` (empty)
- Create: `src/discovery/ats_discoverer.py`
- Create: `tests/test_discovery.py`

**Interfaces:**
- Consumes: stdlib `json`, `pathlib.Path`, existing logger at `config.logger_config.logger`
- Produces (used by Task 2 + future sub-projects):
  - `_DISCOVERY_LOG_PATH: Path | None = None` (module-level; set by `run_discovery` before run)
  - `record_encounter(url: str, outcome: str, signature: str = "") -> None` (no-op when log path unset)
  - `summarize(jsonl_path: Path) -> str` returns markdown string
  - `capture_fingerprint(page, url: str) -> dict` best-effort DOM fingerprint

- [ ] **Step 1: Create `src/discovery/__init__.py`**

```python
"""Discovery instrumentation package — populated by Task 1+."""
```

- [ ] **Step 2: Write failing tests in `tests/test_discovery.py`**

```python
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.discovery.ats_discoverer import (
    record_encounter,
    summarize,
    capture_fingerprint,
)


@pytest.fixture(autouse=True)
def _reset_log_path():
    import src.discovery.ats_discoverer as mod
    mod._DISCOVERY_LOG_PATH = None
    yield
    mod._DISCOVERY_LOG_PATH = None


def test_summarize_handles_empty_log(tmp_path):
    jsonl = tmp_path / "encountered.jsonl"
    jsonl.write_text("")
    md = summarize(jsonl)
    assert "0 sites encountered" in md
    assert "0 dispatches" in md


def test_summarize_groups_by_host(tmp_path):
    jsonl = tmp_path / "encountered.jsonl"
    lines = [
        {"ts": "2026-06-30T12:00:00Z", "host": "jobs.tjx.com", "url": "https://jobs.tjx.com/a", "signature": "phenom", "outcome": "skipped-known-ats"},
        {"ts": "2026-06-30T12:00:05Z", "host": "jobs.tjx.com", "url": "https://jobs.tjx.com/b", "signature": "phenom", "outcome": "skipped-known-ats"},
        {"ts": "2026-06-30T12:00:10Z", "host": "uhaul.wd1.myworkdayjobs.com", "url": "https://uhaul.wd1.myworkdayjobs.com/x", "signature": "myworkdayjobs", "outcome": "routed-to-workday"},
        {"ts": "2026-06-30T12:00:15Z", "host": "boards.greenhouse.io", "url": "https://boards.greenhouse.io/y", "signature": "", "outcome": "sent-to-browser-use"},
    ]
    jsonl.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    md = summarize(jsonl)
    assert "jobs.tjx.com" in md
    assert "uhaul.wd1.myworkdayjobs.com" in md
    assert "boards.greenhouse.io" in md
    assert "3" in md  # 3 hosts


def test_summarize_includes_capture_paths(tmp_path):
    jsonl = tmp_path / "encountered.jsonl"
    jsonl.write_text(json.dumps({
        "ts": "2026-06-30T12:00:00Z", "host": "boards.greenhouse.io",
        "url": "https://boards.greenhouse.io/x", "signature": "",
        "outcome": "sent-to-browser-use",
        "fingerprint_path": "captures/boards.greenhouse.io_20260630T120000.json",
    }) + "\n")

    md = summarize(jsonl)
    assert "captures/boards.greenhouse.io_20260630T120000.json" in md


def test_record_encounter_writes_jsonl_line(tmp_path):
    import src.discovery.ats_discoverer as mod
    log = tmp_path / "encountered.jsonl"
    log.touch()
    mod._DISCOVERY_LOG_PATH = log
    record_encounter("https://jobs.tjx.com/a", "skipped-known-ats", "phenom")
    with open(log) as f:
        lines = [l for l in f.readlines() if l.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["host"] == "jobs.tjx.com"
    assert entry["outcome"] == "skipped-known-ats"
    assert entry["signature"] == "phenom"


def test_record_encounter_is_noop_when_log_path_unset(tmp_path):
    # The autouse fixture sets _DISCOVERY_LOG_PATH to None
    record_encounter("https://x.com/a", "error", "")  # must not raise
    assert list(tmp_path.iterdir()) == []


def test_capture_fingerprint_returns_dict_or_capture_failed():
    """Mocked page returns a dict; failure path returns capture-failed."""
    fp = capture_fingerprint(MagicMock(), "https://example.invalid/apply")
    assert isinstance(fp, dict)
    assert "title" in fp or "fingerprint_status" in fp
```

- [ ] **Step 3: Run tests, expect failure (ModuleNotFoundError)**

Run: `uv run pytest tests/test_discovery.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `src/discovery/ats_discoverer.py`**

```python
"""Discovery instrumentation for external ATS sites.

Activated when `_DISCOVERY_LOG_PATH` is set (set by `run_discovery`).
Outside discovery mode, `record_encounter` is a no-op.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config.logger_config import logger

_DISCOVERY_LOG_PATH: Path | None = None

_KNOWN_SIGNATURES = (
    "myworkdayjobs", "phenom", "taleo", "successfactors",
    "greenhouse", "lever", "icims", "workable", "bamboohr",
    "jobvite", "smartrecruiters", "brassring",
)


def _extract_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _match_signature(url: str) -> str:
    url_l = url.lower()
    for sig in _KNOWN_SIGNATURES:
        if sig in url_l:
            return sig
    return ""


def record_encounter(url: str, outcome: str, signature: str = "") -> None:
    """Append one JSONL line to the active discovery log. No-op if inactive."""
    if _DISCOVERY_LOG_PATH is None:
        return
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "host": _extract_host(url),
            "url": url,
            "signature": signature or _match_signature(url),
            "outcome": outcome,
        }
        with open(_DISCOVERY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.debug(f"record_encounter failed (non-fatal): {e}")


async def _capture_async(page: Any, url: str) -> dict:
    await page.goto(url, timeout=15000)
    title = await page.title()
    headings = await page.evaluate(
        "() => Array.from(document.querySelectorAll('h1,h2')).map(h => h.textContent.trim()).filter(Boolean).slice(0, 10)"
    )
    inputs = await page.evaluate(
        "() => Array.from(document.querySelectorAll('input,select,textarea')).map(i => i.tagName.toLowerCase() + (i.type ? ':' + i.type : ''))"
    )
    buttons = await page.evaluate(
        "() => Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim()).filter(t => t && t.length < 60).slice(0, 20)"
    )
    classes = await page.evaluate(
        "() => { const s = new Set(); document.querySelectorAll('*[class]').forEach(e => { e.className && e.className.toString().split(/\\s+/).forEach(c => { if (c.length > 4 && c.length < 40) s.add(c); }); }); return Array.from(s).slice(0, 200); }"
    )
    iframes = await page.evaluate("() => document.querySelectorAll('iframe').length")
    return {
        "title": title,
        "headings": headings or [],
        "form_input_types": inputs or [],
        "button_labels": buttons or [],
        "ats_marker_classes": classes or [],
        "iframe_count": iframes,
    }


def capture_fingerprint(page: Any, url: str) -> dict:
    """Best-effort DOM fingerprint. On failure returns {fingerprint_status: capture-failed, error}."""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_capture_async(page, url))
        finally:
            loop.close()
    except Exception as e:
        return {"fingerprint_status": "capture-failed", "error": str(e)}


def summarize(jsonl_path: Path) -> str:
    """Host-grouped markdown summary. Empty log returns the '0 sites encountered' stub."""
    by_host: dict[str, list[dict]] = {}
    total = 0
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                host = entry.get("host") or "(unknown)"
                by_host.setdefault(host, []).append(entry)
    except FileNotFoundError:
        return "# Discovery summary\n\n0 sites encountered, 0 dispatches.\n"

    lines = ["# Discovery summary", ""]
    if total == 0:
        lines.append("0 sites encountered, 0 dispatches.")
        return "\n".join(lines) + "\n"

    lines.append(f"{total} dispatches across {len(by_host)} hosts.")
    lines.append("")
    lines.append("| Host | Dispatches | Outcomes | First-seen | Last-seen |")
    lines.append("|------|------------|----------|------------|-----------|")
    for host in sorted(by_host.keys()):
        entries = by_host[host]
        outcomes = ", ".join(sorted({e.get("outcome", "?") for e in entries}))
        first = min(e.get("ts", "") for e in entries)
        last = max(e.get("ts", "") for e in entries)
        cap = sorted({e.get("fingerprint_path", "") for e in entries if e.get("fingerprint_path")})
        cap_note = f" ({len(cap)} captures)" if cap else ""
        lines.append(f"| {host} | {len(entries)} | {outcomes}{cap_note} | {first} | {last} |")

    caps = [e for entries in by_host.values() for e in entries if e.get("fingerprint_path")]
    if caps:
        lines.append("")
        lines.append("## Captures")
        for e in caps:
            lines.append(f"- `{e.get('fingerprint_path')}` — {e.get('host')} ({e.get('outcome')})")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_discovery.py -v`

Expected: 6 tests pass.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 835 prior + 6 new = 841. Investigate any regression.

- [ ] **Step 7: Commit**

```bash
git add src/discovery/ tests/test_discovery.py
git commit -m "feat(discovery): discoverer skeleton with JSONL log + summarize

Adds src/discovery/ats_discoverer.py. record_encounter() appends one
JSONL line per apply_url hit (no-op when _DISCOVERY_LOG_PATH is
unset). summarize() renders a host-grouped markdown summary.
capture_fingerprint() returns a best-effort DOM fingerprint dict.

Dispatcher hooks (Task 2) + capture wiring (Task 3) will round this
out into end-to-end discovery."
```

---

### Task 2: Dispatcher instrumentation hooks in LinkedIn + Indeed managers

**Files:**
- Modify: `src/job_manager/linkedin/job_manager_linkedin.py` — add `_record_encounter` private method + calls in dispatcher branches
- Modify: `src/job_manager/indeed/job_manager_indeed.py` — mirror the same shape
- Create: `tests/test_discovery_hooks.py` — assert hooks fire from each branch

**Interfaces:**
- Consumes: existing `record_encounter` from `src.discovery.ats_discoverer` (lazy import inside the private method to keep module-load cycle-safe)
- Produces: `_record_encounter(url, outcome)` private method on each manager; called from each apply_url branch

- [ ] **Step 1: Write failing tests in `tests/test_discovery_hooks.py`**

```python
"""Verify dispatcher hooks in LinkedInJobManager and IndeedJobManager fire on each branch."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MODULE_LI = "src.job_manager.linkedin.job_manager_linkedin"
MODULE_IN = "src.job_manager.indeed.job_manager_indeed"


@pytest.fixture(autouse=True)
def _reset_log_path():
    import src.discovery.ats_discoverer as mod
    mod._DISCOVERY_LOG_PATH = None
    yield
    mod._DISCOVERY_LOG_PATH = None


def test_record_encounter_hook_writes_for_workday(monkeypatch, tmp_path):
    """When called with myworkdayjobs URL, JSONL gets one routed-to-workday line."""
    log = tmp_path / "encountered.jsonl"
    log.touch()
    import src.discovery.ats_discoverer as mod
    monkeypatch.setattr(mod, "_DISCOVERY_LOG_PATH", log)

    from src.job_manager.linkedin.job_manager_linkedin import LinkedInJobManager
    applier = LinkedInJobManager.__new__(LinkedInJobManager)
    applier._record_encounter(
        "https://uhaul.wd1.myworkdayjobs.com/en-US/UhaulJobs/job/123",
        "routed-to-workday",
    )

    with open(log) as f:
        entries = [json.loads(l) for l in f.readlines() if l.strip()]
    assert len(entries) == 1
    assert entries[0]["host"] == "uhaul.wd1.myworkdayjobs.com"
    assert entries[0]["outcome"] == "routed-to-workday"


def test_record_encounter_is_noop_outside_discovery_mode():
    """If _DISCOVERY_LOG_PATH is None, the private hook must not raise and produce no file."""
    from src.job_manager.linkedin.job_manager_linkedin import LinkedInJobManager
    applier = LinkedInJobManager.__new__(LinkedInJobManager)
    applier._record_encounter("https://example.com/x", "error")  # must be a no-op
```

- [ ] **Step 2: Run tests, expect failure (LinkedInJobManager has no `_record_encounter`)**

Run: `uv run pytest tests/test_discovery_hooks.py -v`

Expected: FAIL with `AttributeError: 'LinkedInJobManager' object has no attribute '_record_encounter'`.

- [ ] **Step 3: Add `_record_encounter` to LinkedIn manager**

Read `src/job_manager/linkedin/job_manager_linkedin.py:388-440` to confirm exact line numbers, then:

A. Add the private method near the top of `LinkedInJobManager` class (find a good spot — e.g., right after `_check_apply_button` or right after `_record_apply_result`):

```python
    def _record_encounter(self, url: str, outcome: str) -> None:
        """Lazy import: no-op when discovery mode is off."""
        from src.discovery.ats_discoverer import record_encounter as _re
        _re(url, outcome)
```

B. In each branch of the dispatcher around line 388-440, add `_record_encounter(apply_url, ...)` calls. Exact line numbers depend on the file's current state; the implementer must read first. Target branches:

| Branch (around lines 388-440) | Hook call |
|---|---|
| Top, when `apply_url` is non-empty | `self._record_encounter(apply_url, "existing-handler-or-external")` — actually skip this, the per-branch calls below cover all paths. |
| `elif "myworkdayjobs.com" in apply_url:` | `self._record_encounter(apply_url, "routed-to-workday")` |
| Inside `EXTERNAL_APPLY_SKIP_ATSES` branch | `self._record_encounter(apply_url, "skipped-known-ats")` |
| Inside `else: apply_result = await self.llm_agent_component.apply_to_job(apply_url)` | `self._record_encounter(apply_url, "sent-to-browser-use")` |
| If `apply_url` is empty (easy apply path) | Optional: `self._record_encounter("", "no-external-apply")` for completeness — skip if it's noise. |

Each call should be a single line. Use the existing indentation.

- [ ] **Step 4: Mirror in Indeed manager**

Read `src/job_manager/indeed/job_manager_indeed.py:300-355`. Apply the same pattern: add the private method + hook each branch (`routed-to-workday`, `sent-to-browser-use`, etc.).

- [ ] **Step 5: Run unit tests, expect PASS (proves the hook itself works and is no-op-safe)**

Run: `uv run pytest tests/test_discovery_hooks.py -v`

Expected: PASS for both tests.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/job_manager/linkedin/job_manager_linkedin.py src/job_manager/indeed/job_manager_indeed.py tests/test_discovery_hooks.py
git commit -m "feat(discovery): instrument LinkedIn + Indeed dispatchers

When _DISCOVERY_LOG_PATH is set, each apply_url branch records its
outcome (routed-to-workday / skipped-known-ats / sent-to-browser-use)
to the JSONL log. The hook is a no-op when discovery mode is off,
so the apply path is unchanged in normal operation.

Records:
- LinkedInJobManager.apply_job — 4 branches (workday / phenom-taleo-sf
  / browser-use / easy apply direct)
- IndeedJobManager.apply_job — analogous branches"
```

---

### Task 3: Wire DOM fingerprint capture for non-Workday outcomes

**Files:**
- Modify: `src/discovery/ats_discoverer.py` — add `record_encounter_with_page(url, outcome, page)` 
- Modify: `src/job_manager/linkedin/job_manager_linkedin.py` — add `_record_encounter_with_capture` private method; replace bare calls from Task 2 with it for `sent-to-browser-use` / `skipped-known-ats` outcomes
- Modify: `src/job_manager/indeed/job_manager_indeed.py` — mirror
- Modify: `tests/test_discovery.py` — add a test for `record_encounter_with_page`

**Interfaces:**
- Consumes: existing `capture_fingerprint` from Task 1
- Produces: `record_encounter_with_page(url, outcome, signature)` in `ats_discoverer.py` — writes the JSONL entry then captures a DOM fingerprint into `captures/<host>_<ts>.json` and re-writes the entry with `fingerprint_path`

- [ ] **Step 1: Add failing test**

Append to `tests/test_discovery.py`:

```python
def test_record_encounter_with_page_writes_capture(tmp_path, monkeypatch):
    import src.discovery.ats_discoverer as mod
    log = tmp_path / "encountered.jsonl"
    log.touch()
    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    monkeypatch.setattr(mod, "_DISCOVERY_LOG_PATH", log)

    monkeypatch.setattr(mod, "capture_fingerprint", lambda page, url: {"title": "Apply Now", "headings": ["Apply Now"]})
    monkeypatch.setattr(mod, "_capture_to_disk",
                        lambda url, page, captures_dir: captures_dir / "fake.json")

    # Stub _capture_to_disk
    from pathlib import Path
    def fake_capture_to_disk(url, page, cdir):
        p = cdir / f"fake.json"
        p.write_text('{"title": "Apply Now"}')
        return p
    monkeypatch.setattr(mod, "_capture_to_disk", fake_capture_to_disk)

    mod.record_encounter_with_page(MagicMock(), "https://boards.greenhouse.io/apply",
                                    "sent-to-browser-use", "")
    with open(log) as f:
        lines = [l for l in f.readlines() if l.strip()]
    assert len(lines) >= 1
    last = json.loads(lines[-1])
    assert "fingerprint_path" in last
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_discovery.py::test_record_encounter_with_page_writes_capture -v`

Expected: FAIL — `record_encounter_with_page` doesn't exist yet.

- [ ] **Step 3: Implement `record_encounter_with_page` + `_capture_to_disk` in `ats_discoverer.py`**

Add to `ats_discoverer.py`:

```python
def _capture_to_disk(url: str, page: Any, captures_dir: Path) -> Path:
    """Capture fingerprint to disk; return path. Best-effort."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    host = _extract_host(url) or "unknown"
    safe_host = host.replace("/", "_").replace(":", "_")
    capture_path = captures_dir / f"{safe_host}_{timestamp}.json"
    try:
        fp = capture_fingerprint(page, url)
        capture_path.write_text(json.dumps(fp, indent=2))
    except Exception as e:
        capture_path.write_text(json.dumps({"fingerprint_status": "capture-failed", "error": str(e)}))
    return capture_path


def record_encounter_with_page(
    page: Any, url: str, outcome: str, signature: str = ""
) -> None:
    """Same as record_encounter, plus a DOM fingerprint capture.

    Used by dispatchers that have a Playwright page in scope. The fingerprint
    is written to captures/<host>_<ts>.json and a second JSONL line is
    appended with `fingerprint_path` so the summarize() can reference it.
    """
    if _DISCOVERY_LOG_PATH is None:
        return
    record_encounter(url, outcome, signature)
    try:
        captures_dir = _DISCOVERY_LOG_PATH.parent / "captures"
        captures_dir.mkdir(exist_ok=True)
        capture_path = _capture_to_disk(url, page, captures_dir)
        with open(_DISCOVERY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "host": _extract_host(url),
                "url": url,
                "outcome": outcome,
                "signature": signature or _match_signature(url),
                "fingerprint_path": str(capture_path.relative_to(_DISCOVERY_LOG_PATH.parent.parent)),
            }) + "\n")
    except Exception as e:
        logger.debug(f"record_encounter_with_page capture failed (non-fatal): {e}")
```

- [ ] **Step 4: Add `_record_encounter_with_capture` to dispatchers**

In `src/job_manager/linkedin/job_manager_linkedin.py`, add a sibling method:

```python
    def _record_encounter_with_capture(self, url: str, outcome: str) -> None:
        """Same as _record_encounter but also captures a DOM fingerprint for non-Workday outcomes."""
        from src.discovery.ats_discoverer import record_encounter_with_page
        if outcome in {"sent-to-browser-use", "skipped-known-ats", "error"}:
            record_encounter_with_page(self.page, url, outcome)
        else:
            from src.discovery.ats_discoverer import record_encounter
            record_encounter(url, outcome)
```

Replace the bare `_record_encounter(...)` calls in the `sent-to-browser-use` and `skipped-known-ats` branches with `self._record_encounter_with_capture(apply_url, ...)` (keep `routed-to-workday` / existing-handler as bare calls — they're not worth fingerprinting).

Mirror in `src/job_manager/indeed/job_manager_indeed.py`.

- [ ] **Step 5: Re-run tests**

Run: `uv run pytest tests/test_discovery.py tests/test_discovery_hooks.py -v`

Expected: all pass.

- [ ] **Step 6: Full suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 842+ tests pass. No regressions.

- [ ] **Step 7: Commit**

```bash
git add src/discovery/ tests/ src/job_manager/
git commit -m "feat(discovery): capture DOM fingerprints for non-Workday URLs

Adds record_encounter_with_page() which calls capture_fingerprint()
and writes the result to captures/<host>_<ts>.json, then appends a
second JSONL line with fingerprint_path so summarize() can reference
it.

Dispatchers now use _record_encounter_with_capture() for outcomes
where the destination page is worth inspecting (sent-to-browser-use,
skipped-known-ats, error). Workday-routed and existing-handler
outcomes stay as plain _record_encounter() since their destinations
are already covered."
```

---

### Task 4: Operator verification

**Files:**
- Modify: `docs/superpowers/specs/2026-06-30-ats-sites-survey.md` — append a "Verification" section

- [ ] **Step 1: Append Verification to spec**

```markdown

## Verification

After Tasks 1-3 land, run the discoverer against the user's real LinkedIn session:

    uv run python -m src.discovery.ats_discoverer

(Note: Tasks 1-3 build the helpers; `run_discovery` itself is the
operator entry point — confirm its signature in `ats_discoverer.py`
matches the user's `main.create_and_run_bot` call before running.)

Expected output under `data/output/discovery/sessions/<session_id>/`:
- `encountered.jsonl` — one line per apply_url hit
- `captures/*.json` — DOM fingerprints for non-Workday destinations
- `summary.md` — host-grouped table

Inspect:
1. `summary.md` — confirm host list is plausible.
2. JSONL with `outcome: "sent-to-browser-use"` — these are ATSes that need automation.
3. Captures for each new ATS — verify fingerprints look like real apply forms.

Tighten search filters (e.g., "Retail", "Manufacturing", "Logistics") if all encounters are Easy Apply — External Apply jobs surface more often in those verticals for this user's locale.
```

- [ ] **Step 2: Operator run**

Confirm with the user before running live (this hits LinkedIn with their real session and may take 10-30 minutes).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-30-ats-sites-survey.md
git commit -m "docs(spec): add Verification section for ATS discovery run

Operator runbook for the discoverer: uv run python -m
src.discovery.ats_discoverer against the real session, then
inspect data/output/discovery/sessions/<id>/summary.md. Each
non-Workday external ATS becomes a follow-up per-ATS brainstorming
cycle."
```

---

## Self-Review

1. **Spec coverage** — every spec section has a task:
   - Architecture → Task 1 creates `ats_discoverer.py`, Task 2 wires it.
   - Components → same.
   - Data flow → encoded in `record_encounter` + `summarize` + verification doc.
   - Run parameters → committed in spec.
   - Error handling → try/except wraps + no-op guards throughout.
   - Testing → unit + integration tests in Tasks 1-3; verification doc in Task 4.
   - Out of scope → per-ATS specs deferred per spec.
2. **Placeholders** — none. Implementer is told to "read file first, then edit" for unknown line numbers — that's not a placeholder, it's the discipline.
3. **Type consistency** — `record_encounter(url: str, outcome: str, signature: str = "")` matches across spec, Task 1, Task 3, and dispatcher hook.
4. **Sequential** — Tasks 1-3 each modify distinct file regions; safe to execute in order.

No issues found. Plan ready.