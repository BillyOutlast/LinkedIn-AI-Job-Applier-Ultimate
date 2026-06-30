import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.discovery.ats_discoverer import (
    capture_fingerprint,
    record_encounter,
    summarize,
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
        {
            "ts": "2026-06-30T12:00:00Z",
            "host": "jobs.tjx.com",
            "url": "https://jobs.tjx.com/a",
            "signature": "phenom",
            "outcome": "skipped-known-ats",
        },
        {
            "ts": "2026-06-30T12:00:05Z",
            "host": "jobs.tjx.com",
            "url": "https://jobs.tjx.com/b",
            "signature": "phenom",
            "outcome": "skipped-known-ats",
        },
        {
            "ts": "2026-06-30T12:00:10Z",
            "host": "uhaul.wd1.myworkdayjobs.com",
            "url": "https://uhaul.wd1.myworkdayjobs.com/x",
            "signature": "myworkdayjobs",
            "outcome": "routed-to-workday",
        },
        {
            "ts": "2026-06-30T12:00:15Z",
            "host": "boards.greenhouse.io",
            "url": "https://boards.greenhouse.io/y",
            "signature": "",
            "outcome": "sent-to-browser-use",
        },
    ]
    jsonl.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    md = summarize(jsonl)
    assert "jobs.tjx.com" in md
    assert "uhaul.wd1.myworkdayjobs.com" in md
    assert "boards.greenhouse.io" in md
    assert "3" in md  # 3 hosts


def test_summarize_includes_capture_paths(tmp_path):
    jsonl = tmp_path / "encountered.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "ts": "2026-06-30T12:00:00Z",
                "host": "boards.greenhouse.io",
                "url": "https://boards.greenhouse.io/x",
                "signature": "",
                "outcome": "sent-to-browser-use",
                "fingerprint_path": "captures/boards.greenhouse.io_20260630T120000.json",
            }
        )
        + "\n"
    )

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
    """On page.goto failure, result is exactly the capture-failed dict shape."""
    page = MagicMock()
    page.goto = MagicMock(side_effect=RuntimeError("boom"))
    fp = capture_fingerprint(page, "https://example.invalid/apply")
    assert fp == {"fingerprint_status": "capture-failed", "error": "boom"}


def test_record_encounter_with_page_writes_capture(tmp_path, monkeypatch):
    """Locks in the duplicate-line design: lines[0] has no fingerprint_path, lines[1] does."""
    import src.discovery.ats_discoverer as mod

    log = tmp_path / "encountered.jsonl"
    log.touch()
    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    monkeypatch.setattr(mod, "_DISCOVERY_LOG_PATH", log)

    monkeypatch.setattr(
        mod,
        "capture_fingerprint",
        lambda page, url: {"title": "Apply Now", "headings": ["Apply Now"]},
    )

    def fake_capture_to_disk(url, page, cdir):
        p = cdir / "fake.json"
        p.write_text('{"title": "Apply Now"}')
        return p

    monkeypatch.setattr(mod, "_capture_to_disk", fake_capture_to_disk)

    mod.record_encounter_with_page(
        MagicMock(), "https://boards.greenhouse.io/apply", "sent-to-browser-use", ""
    )
    with open(log) as f:
        lines = [l for l in f.readlines() if l.strip()]
    # The first line is the bare encounter entry (no fingerprint_path).
    # The second line is the capture correlation entry (has fingerprint_path).
    assert len(lines) == 2, f"expected exactly 2 lines, got {len(lines)}: {lines!r}"
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert "fingerprint_path" not in first
    assert "fingerprint_path" in second


def test_discovery_auto_init_when_flag_enabled(tmp_path, monkeypatch):
    """When DISCOVERY=True and log path unset, _auto_init sets the path."""
    import src.discovery.ats_discoverer as mod

    # Redirect session discovery root to tmp_path so the test does not
    # touch the real `data/output/discovery/sessions` tree.
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    real_Path = mod.Path

    class _PathSandbox(real_Path):
        pass

    def _patched_Path(arg=""):
        # Both call sites pass either a relative string ("data/output/discovery/sessions")
        # or a sub-path built on the session_dir. Map the hard-coded root
        # to tmp_path/sessions and let everything else resolve naturally.
        s = str(arg)
        if s == "data/output/discovery/sessions":
            return real_Path(str(sessions_dir))
        return real_Path(arg)

    monkeypatch.setattr(mod, "Path", _patched_Path)
    monkeypatch.setattr(mod, "_DISCOVERY_LOG_PATH", None)
    monkeypatch.setattr("config.app_config.DISCOVERY", True, raising=False)
    mod._auto_init_discovery()
    assert mod._DISCOVERY_LOG_PATH is not None
    assert mod._DISCOVERY_LOG_PATH.name == "encountered.jsonl"
    # captures/ subdir must exist next to the log file (used by
    # record_encounter_with_page when a fingerprint is captured).
    assert (mod._DISCOVERY_LOG_PATH.parent / "captures").is_dir()


def test_discovery_no_auto_init_when_flag_disabled(tmp_path, monkeypatch):
    import src.discovery.ats_discoverer as mod

    monkeypatch.setattr(mod, "_DISCOVERY_LOG_PATH", None)
    monkeypatch.setattr("config.app_config.DISCOVERY", False, raising=False)
    mod._auto_init_discovery()
    assert mod._DISCOVERY_LOG_PATH is None


def test_capture_fingerprint_includes_network_hostnames():
    """Mocked page emits request events; fingerprint dict includes network_hostnames."""
    page = MagicMock()
    listeners = []

    def fake_on(event, handler):
        if event == "request":
            listeners.append(handler)

    def fake_off(event, handler):
        # Mirror `page.on` so cleanup is observable from the listener list.
        if event == "request":
            try:
                listeners.remove(handler)
            except ValueError:
                pass

    page.on = fake_on
    page.off = fake_off
    page.title = AsyncMock(return_value="Apply Now")
    page.evaluate = AsyncMock(
        side_effect=[
            [],  # headings
            [],  # inputs
            [],  # buttons
            [],  # classes
            0,  # iframe_count
        ]
    )

    request_urls = [
        "https://api.greenhouse.io/v1/x",
        "https://boards.greenhouse.io/y",
    ]

    async def fake_goto(url, timeout=None):
        # Fire registered request listeners synchronously, simulating
        # network activity during navigation. This is the point at which
        # `_capture_async` would observe them.
        for u in request_urls:
            for h in listeners:
                req = MagicMock()
                req.url = u
                h(req)

    page.goto = fake_goto

    fp = capture_fingerprint(page, "https://example.com/apply")
    assert "network_hostnames" in fp
    assert "api.greenhouse.io" in fp["network_hostnames"]
    assert "boards.greenhouse.io" in fp["network_hostnames"]


def test_capture_fingerprint_removes_request_listener():
    """After _capture_async returns, the page's 'request' listener is detached.

    The bot shares the same page object for the post-discovery Easy Apply
    flow, so a leaked listener would silently mutate a dead `hostnames`
    set on every later navigation. This locks the cleanup contract.
    """
    page = MagicMock()
    on_handlers: list = []
    off_calls: list = []

    def fake_on(event, handler):
        if event == "request":
            on_handlers.append(handler)

    def fake_off(event, handler):
        if event == "request":
            off_calls.append(handler)

    page.on = fake_on
    page.off = fake_off
    page.title = AsyncMock(return_value="Apply")
    page.evaluate = AsyncMock(side_effect=[[], [], [], [], 0])

    async def fake_goto(url, timeout=None):
        for h in on_handlers:
            h(MagicMock(url="https://x.example.com/"))

    page.goto = fake_goto

    capture_fingerprint(page, "https://example.com/apply")
    assert on_handlers, "expected request listener to be registered"
    assert off_calls, "expected page.off to be called for cleanup"
    assert (
        off_calls[0] is on_handlers[0]
    ), "page.off must receive the same handler passed to page.on"
