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

# Outcomes that warrant a DOM fingerprint capture (vs. plain record_encounter)
CAPTURE_OUTCOMES: frozenset[str] = frozenset({"sent-to-browser-use", "skipped-known-ats", "error"})

_KNOWN_SIGNATURES = (
    "myworkdayjobs",
    "phenom",
    "taleo",
    "successfactors",
    "greenhouse",
    "lever",
    "icims",
    "workable",
    "bamboohr",
    "jobvite",
    "smartrecruiters",
    "brassring",
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
        capture_path.write_text(
            json.dumps({"fingerprint_status": "capture-failed", "error": str(e)})
        )
    return capture_path


def record_encounter_with_page(page: Any, url: str, outcome: str, signature: str = "") -> None:
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
            f.write(
                json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "host": _extract_host(url),
                        "url": url,
                        "outcome": outcome,
                        "signature": signature or _match_signature(url),
                        "fingerprint_path": str(
                            capture_path.relative_to(_DISCOVERY_LOG_PATH.parent.parent)
                        ),
                    }
                )
                + "\n"
            )
    except Exception as e:
        logger.debug(f"record_encounter_with_page capture failed (non-fatal): {e}")


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


async def run_discovery(
    search_config: dict | None = None,
    secrets: dict | None = None,
    resume_text: str = "",
    resume_structured: dict | None = None,
) -> int:
    """Operator entry point: run the bot under discovery mode.

    Sets `_DISCOVERY_LOG_PATH` so `_record_encounter` calls (from each
    dispatcher's apply_url branch) append to a per-session JSONL log.
    Writes `summary.md` next to that log on completion. Delegates the
    bot run to `main.create_and_run_bot`, whose existing dispatcher
    hooks do all the recording.

    Any args that are not supplied are read from the bot's normal
    ConfigValidator pipeline (search config from
    `config/search_config.yaml`, secrets from `.env`, resume from
    `RESUME_TEXT_FILE` / `RESUME_STRUCTURED_FILE`). This keeps the
    `python -m src.discovery.ats_discoverer` invocation zero-config
    for operators who already have a working `main.py` setup.
    """
    import asyncio
    from datetime import datetime, timezone
    from uuid import uuid4

    from config.constants import SEARCH_CONFIG_FILE
    from config.logger_config import logger
    from main import ConfigValidator, create_and_run_bot
    from src.utils.utils import load_yaml_file

    if search_config is None or secrets is None or resume_structured is None:
        validator = ConfigValidator()
        if search_config is None:
            search_config = validator.validate_search_config(SEARCH_CONFIG_FILE)
        if secrets is None:
            secrets = validator.validate_secrets()
        if not resume_text and not resume_structured:
            from config.constants import RESUME_STRUCTURED_FILE, RESUME_TEXT_FILE

            resume_text = validator.validate_resume_text(RESUME_TEXT_FILE)
            resume_structured = validator.validate_resume_structured(RESUME_STRUCTURED_FILE)

    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_") + uuid4().hex[:8]
    discovery_root = Path("data/output/discovery/sessions") / session_id
    discovery_root.mkdir(parents=True, exist_ok=True)
    log_path = discovery_root / "encountered.jsonl"
    log_path.touch()

    global _DISCOVERY_LOG_PATH
    _DISCOVERY_LOG_PATH = log_path
    logger.info(f"Discovery mode active; logging to {log_path}")

    try:
        await create_and_run_bot(
            search_config=search_config,
            secrets=secrets,
            resume_text=resume_text,
            resume_structured=resume_structured or {},
        )
    except Exception as e:
        logger.error(f"Bot run under discovery mode failed: {e}")
    finally:
        # Always write the summary, even on partial runs.
        summary_path = discovery_root / "summary.md"
        summary_path.write_text(summarize(log_path))
        logger.info(f"Discovery summary written to {summary_path}")
        _DISCOVERY_LOG_PATH = None

    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(run_discovery()))


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
