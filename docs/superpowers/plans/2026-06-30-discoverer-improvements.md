# Discoverer Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the discoverer easier to enable (`DISCOVERY = True` config flag toggles auto-init) and its fingerprints more useful for ATS identification (capture network hostnames the destination page calls).

**Architecture:** Two independent improvements to `src/discovery/ats_discoverer.py` and its surroundings. (1) Add a `DISCOVERY` flag in `config/app_config.py`; the discoverer module reads it on import and auto-initializes its log path so the regular bot picks up discovery mode without a separate operator invocation. (2) Extend `capture_fingerprint` to attach a Playwright `page.on("request", ...)` listener and aggregate unique hostnames into `network_hostnames` — a stronger ATS-identification signal than DOM shape.

**Tech Stack:** Python 3.12, asyncio, stdlib `urllib.parse`, Playwright async, pytest + pytest-asyncio.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-30-discoverer-improvements-design.md` (committed `990cbba`).
- 845 tests currently pass. Plan must keep them green.
- No new project dependencies. `urlparse` already imported in `ats_discoverer.py`.
- `_DISCOVERY_LOG_PATH is None` early-return is the no-op default for every record function; do not weaken it.
- `DISCOVERY = False` is the default in `config/app_config.py`. Operator must flip it to True to enable.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `config/app_config.py` | Modify | Add `DISCOVERY = False` constant + docstring |
| `src/discovery/ats_discoverer.py` | Modify | Add `_auto_init_discovery()`; extend `_capture_async` with network listener |
| `tests/test_discovery.py` | Modify | Add 3 tests (auto-init flag on/off, network hostnames capture) |

No new modules. No new files.

---

### Task 1: `DISCOVERY` config flag + auto-init

**Files:**
- Modify: `config/app_config.py` (add `DISCOVERY = False` constant near `EASY_APPLY_ONLY_MODE`)
- Modify: `src/discovery/ats_discoverer.py` (add `_auto_init_discovery()` function + module-level call)
- Modify: `tests/test_discovery.py` (add 2 tests)

**Interfaces:**
- Consumes: `config.app_config.DISCOVERY` (boolean, default False)
- Produces: `_auto_init_discovery()` function — defensive: any failure (import error, mkdir error) leaves `_DISCOVERY_LOG_PATH = None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discovery.py`:

```python
def test_discovery_auto_init_when_flag_enabled(tmp_path, monkeypatch):
    """When DISCOVERY=True and log path unset, _auto_init sets the path."""
    import src.discovery.ats_discoverer as mod
    monkeypatch.setattr(mod, "_DISCOVERY_LOG_PATH", None)
    monkeypatch.setattr("config.app_config.DISCOVERY", True, raising=False)
    mod._auto_init_discovery()
    assert mod._DISCOVERY_LOG_PATH is not None
    assert mod._DISCOVERY_LOG_PATH.name == "encountered.jsonl"


def test_discovery_no_auto_init_when_flag_disabled(tmp_path, monkeypatch):
    import src.discovery.ats_discoverer as mod
    monkeypatch.setattr(mod, "_DISCOVERY_LOG_PATH", None)
    monkeypatch.setattr("config.app_config.DISCOVERY", False, raising=False)
    mod._auto_init_discovery()
    assert mod._DISCOVERY_LOG_PATH is None
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `uv run pytest tests/test_discovery.py::test_discovery_auto_init_when_flag_enabled tests/test_discovery.py::test_discovery_no_auto_init_when_flag_disabled -v`

Expected: FAIL — `_auto_init_discovery` does not exist yet.

- [ ] **Step 3: Add `DISCOVERY` to `config/app_config.py`**

In `config/app_config.py`, find the `EASY_APPLY_ONLY_MODE` block and append right after it:

```python
# ponytail: ATS discovery. Set to True to log every apply_url encounter
# (host + URL + outcome) to data/output/discovery/sessions/<id>/encountered.jsonl
# and capture DOM fingerprints for non-Workday destinations. Adds
# capture_fingerprint() page loads in headless Chromium per non-Workday
# apply URL — expect +5-15s per such job. Set False for normal runs.
DISCOVERY = False
```

- [ ] **Step 4: Add `_auto_init_discovery()` to `ats_discoverer.py`**

In `src/discovery/ats_discoverer.py`, add this function at the bottom of the module (after all other definitions):

```python
def _auto_init_discovery() -> None:
    """If DISCOVERY flag is True, set up _DISCOVERY_LOG_PATH on import.

    Defensive: any failure (config missing, disk full, permission denied)
    leaves the module in its no-op state so the apply path is unaffected.
    """
    global _DISCOVERY_LOG_PATH
    if _DISCOVERY_LOG_PATH is not None:
        return
    try:
        from config.app_config import DISCOVERY
    except Exception:
        return
    if not DISCOVERY:
        return
    try:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        session_dir = Path("data/output/discovery/sessions") / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "captures").mkdir(exist_ok=True)
        log_path = session_dir / "encountered.jsonl"
        log_path.touch()
        _DISCOVERY_LOG_PATH = log_path
    except Exception as e:
        logger.debug(f"Auto-init discovery failed (non-fatal): {e}")


_auto_init_discovery()
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_discovery.py::test_discovery_auto_init_when_flag_enabled tests/test_discovery.py::test_discovery_no_auto_init_when_flag_disabled -v`

Expected: PASS, 2 tests pass.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 847 passed (845 prior + 2 new). Investigate any regression.

- [ ] **Step 7: Commit**

```bash
git add config/app_config.py src/discovery/ats_discoverer.py tests/test_discovery.py
git commit -m "feat(discovery): DISCOVERY config flag + auto-init on import

Add DISCOVERY = False to config/app_config.py. The ats_discoverer
module reads it on import; when True, auto-creates
data/output/discovery/sessions/<id>/encountered.jsonl and sets
_DISCOVERY_LOG_PATH so dispatcher hooks fire without the operator
needing to run a separate discoverer entry point. Defensive — any
failure leaves the module in its no-op state."
```

---

### Task 2: Network hostname capture in `capture_fingerprint`

**Files:**
- Modify: `src/discovery/ats_discoverer.py` (extend `_capture_async` to attach a `page.on("request", ...)` listener and aggregate hostnames)
- Modify: `tests/test_discovery.py` (add 1 test)

**Interfaces:**
- Consumes: existing `capture_fingerprint(page, url)`; Playwright's `page.on("request", handler)` event API
- Produces: fingerprint dict gains `network_hostnames: list[str]` — sorted unique lowercased hostnames the destination page called

- [ ] **Step 1: Write the failing test**

Append to `tests/test_discovery.py`:

```python
def test_capture_fingerprint_includes_network_hostnames():
    """Mocked page emits request events; fingerprint dict includes network_hostnames."""
    page = MagicMock()
    listeners = []
    def fake_on(event, handler):
        if event == "request":
            listeners.append(handler)
    page.on = fake_on
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="Apply Now")
    page.evaluate = AsyncMock(side_effect=[
        [],   # headings
        [],   # inputs
        [],   # buttons
        [],   # classes
        0,    # iframe_count
    ])

    import asyncio
    async def run_capture():
        captured = capture_fingerprint(page, "https://example.com/apply")
        for url in ["https://api.greenhouse.io/v1/x", "https://boards.greenhouse.io/y"]:
            for h in listeners:
                req = MagicMock(); req.url = url
                h(req)
        return captured

    fp = asyncio.run(run_capture())
    assert "network_hostnames" in fp
    assert "api.greenhouse.io" in fp["network_hostnames"]
    assert "boards.greenhouse.io" in fp["network_hostnames"]
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_discovery.py::test_capture_fingerprint_includes_network_hostnames -v`

Expected: FAIL — current `_capture_async` doesn't return `network_hostnames`.

- [ ] **Step 3: Extend `_capture_async` in `ats_discoverer.py`**

In `src/discovery/ats_discoverer.py`, find `_capture_async`. Replace its current body with:

```python
async def _capture_async(page: Any, url: str) -> dict:
    hostnames: set[str] = set()
    try:
        page.on("request", lambda req: hostnames.add(urlparse(req.url).netloc.lower()))
    except Exception:
        pass
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
        "network_hostnames": sorted(hostnames),
    }
```

`urlparse` is already imported at module top.

- [ ] **Step 4: Run test, expect PASS**

Run: `uv run pytest tests/test_discovery.py::test_capture_fingerprint_includes_network_hostnames -v`

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -q --no-header -x`

Expected: 848 passed (847 + 1 new). Investigate any regression.

- [ ] **Step 6: Commit**

```bash
git add src/discovery/ats_discoverer.py tests/test_discovery.py
git commit -m "feat(discovery): capture network hostnames in fingerprints

Attach a Playwright page.on(\"request\", ...) listener at capture
start; aggregate unique lowercased hostnames into the fingerprint's
network_hostnames list. Hostnames are a stronger ATS-identification
signal than DOM shape alone (e.g. boards-api.greenhouse.io vs
myworkdayjobs.com/wday). Best-effort: registration failure → empty
list; per-request URL parse failure → host omitted."
```

---

## Self-Review

1. **Spec coverage:**
   - `DISCOVERY` flag in `config/app_config.py` → Task 1 Step 3
   - `_auto_init_discovery()` defensive auto-init → Task 1 Step 4
   - Network hostname capture in `_capture_async` → Task 2 Step 3
   - Three tests (auto-init on, auto-init off, network hostnames) → Tasks 1 + 2 steps 1
   - All error handling cases (DISCOVERY import fail, dir create fail, page.on fail, malformed URL) → covered by existing try/except wrappers in the spec'd code
2. **Placeholders:** none. Every code block is complete.
3. **Type consistency:**
   - `_auto_init_discovery() -> None` matches in spec, test mock, and call site
   - `_DISCOVERY_LOG_PATH` is `Path | None` everywhere
   - `DISCOVERY` is `bool` (default `False`)
   - `network_hostnames: list[str]` returned in the fingerprint dict

No issues found. Plan ready.