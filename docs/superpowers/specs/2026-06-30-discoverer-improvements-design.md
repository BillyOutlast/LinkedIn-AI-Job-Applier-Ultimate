# Discoverer Improvements — Config Flag + Network Capture

**Date:** 2026-06-30
**Status:** Design — pending user approval
**Author:** brainstorming session
**Scope:** Sub-project 1.5 of N (improves the discovery driver before first operator run)

## Goal

Make the discoverer easier to enable and the captured fingerprints more useful for downstream per-ATS planning. Two small changes:

1. **`DISCOVERY` config flag** so the operator toggles discovery mode in `config/app_config.py` instead of having to invoke the discoverer as a separate process.
2. **Network-hostname capture** in `capture_fingerprint` so the fingerprint identifies the ATS by the API endpoints it calls, not just DOM shape.

## Background

The ATS sites survey spec (`docs/superpowers/specs/2026-06-30-ats-sites-survey.md`) was implemented in commits `82d8a74..0740eb3`. The deliverable works but has two operator-experience and data-quality gaps:

1. **Manual entry point.** The discoverer's `__main__` block exists, but the operator still needs to remember to run `uv run python -m src.discovery.ats_discoverer` instead of the regular bot. A `DISCOVERY = True` flag in `config/app_config.py` lets the discoverer auto-initialize its log path on import, so running the regular bot (`uv run python main.py`) automatically records encounters.

2. **Weak ATS identification.** `capture_fingerprint` extracts title, headings, form input types, button labels, marker classes, iframe count — all DOM signals. But many ATSes share DOM patterns (Taleo, Workday, Greenhouse all use similar field structures). The `network_hostnames` field was a placeholder. Capturing the actual HTTP request hostnames (e.g., `boards-api.greenhouse.io`, `myworkdayjobs.com/wday`) is a stronger identity signal.

## Architecture

### Config flag wiring

A new module-level constant `DISCOVERY = False` in `config/app_config.py`. On import, `ats_discoverer.py` reads it; if True and `_DISCOVERY_LOG_PATH is None`, the module auto-creates a session directory and sets the log path. This way, importing the discoverer (which happens at module load in dispatcher hooks via lazy import) is sufficient — no separate operator invocation.

The bot's regular entry point (`uv run python main.py`) reads the same `DISCOVERY` flag, so toggling discovery mode is a config-only change. The discoverer's separate `__main__` invocation remains as an escape hatch for advanced use cases (e.g., specifying a non-default session dir).

### Network capture

Playwright's `page.on("request", handler)` event fires for every HTTP request the page makes. We attach a listener at capture start that pushes `(urlparse(req.url).netloc.lower(),)` into a `set()`. After capture completes, the set is converted to a sorted list and returned in the fingerprint as `network_hostnames`.

This adds ~10 lines to `_capture_async` and matches the existing best-effort error-handling pattern.

## Components

### `config/app_config.py` (modify)

Add a new constant below the existing `EASY_APPLY_ONLY_MODE` block:

```python
# ponytail: ATS discovery. Set to True to log every apply_url encounter
# (host + URL + outcome) to data/output/discovery/sessions/<id>/encountered.jsonl
# and capture DOM fingerprints for non-Workday destinations. Adds
# capture_fingerprint() page loads in headless Chromium per non-Workday
# apply URL — expect +5-15s per such job. Set False for normal runs.
DISCOVERY = False
```

### `src/discovery/ats_discoverer.py` (modify)

**Two changes.**

**1. Auto-init on import.** After the module's existing imports / constants, add:

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

Call once at module load (after the function definition). This is the function's only call site — placed at module bottom.

**2. Network-hostname capture in `_capture_async`.** Replace the current body with:

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

The `from urllib.parse import urlparse` already exists at module top (used by `_extract_host`).

### `tests/test_discovery.py` (modify)

Add three tests:

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


def test_capture_fingerprint_includes_network_hostnames():
    """Mocked page emits request events; fingerprint dict includes network_hostnames set."""
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

## Data flow

1. Operator edits `config/app_config.py` → `DISCOVERY = True` → saves → restarts bot.
2. Bot starts; `main.py` imports `config.app_config` (DISCOVERY=True).
3. Bot imports dispatchers, which lazily import `ats_discoverer`.
4. `ats_discoverer._auto_init_discovery()` runs at module load: creates `data/output/discovery/sessions/<session_id>/{encountered.jsonl, captures/}` and sets `_DISCOVERY_LOG_PATH`.
5. Bot runs LinkedIn jobs; dispatcher hits apply_url → `_record_encounter(apply_url, outcome)` writes JSONL.
6. For `sent-to-browser-use` / `skipped-known-ats`: `_record_encounter_with_capture` calls `capture_fingerprint` which opens the URL, listens for requests, captures hostnames, returns the enriched dict.
7. Fingerprint JSON (now including `network_hostnames`) written to `captures/<host>_<ts>.json`.
8. `summarize()` reads JSONL + fingerprint paths at run end → produces `summary.md`.
9. Operator inspects `summary.md` + the fingerprint JSONs → identifies which ATSes need automation → each becomes a per-ATS brainstorming cycle.

## Error handling

- `DISCOVERY` import failure (e.g., config.py syntax error): `_auto_init_discovery` swallows, leaves `_DISCOVERY_LOG_PATH = None`. Apply path unchanged.
- Auto-init directory creation failure (disk full, permissions): swallowed, log debug, `_DISCOVERY_LOG_PATH` stays None. Bot runs normally but discovery is silently off.
- `page.on("request", ...)` registration failure (e.g., Playwright API drift): caught, `network_hostnames` ends up empty. Other fingerprint fields still captured.
- Listener fire error (e.g., malformed request URL): `urlparse` raises → `hostnames.add` not called → host omitted. Non-fatal.

## Testing

Unit tests in `tests/test_discovery.py`:

- `test_discovery_auto_init_when_flag_enabled` — DISCOVERY=True → log path set
- `test_discovery_no_auto_init_when_flag_disabled` — DISCOVERY=False → log path stays None
- `test_capture_fingerprint_includes_network_hostnames` — emitted requests show up in fingerprint

Existing 11 tests must keep passing. Full suite target: 14+ tests in `test_discovery.py` + unchanged `test_discovery_hooks.py`. Run: `uv run pytest tests/test_discovery.py tests/test_discovery_hooks.py -v`.

## Out of scope

- Dashboard UI toggle for DISCOVERY
- Session cleanup policy (auto-prune sessions older than N days)
- Real-time `tail -f` support
- Network-hostname aggregation across multiple pages in the same multi-step apply flow (only captures hostnames from the initial `page.goto(url)`)
- Filtering noisy hostnames (CDNs, analytics) from the captured set — could add a denylist later but not in scope here

## Decomposition note

This is sub-project 1.5 of N — a small follow-up to the ATS sites survey spec. Single implementation plan; ~3 files modified; one commit.