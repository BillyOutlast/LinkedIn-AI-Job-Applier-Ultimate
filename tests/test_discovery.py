import json
from pathlib import Path
from unittest.mock import MagicMock

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
    """Mocked page returns a dict; failure path returns capture-failed."""
    fp = capture_fingerprint(MagicMock(), "https://example.invalid/apply")
    assert isinstance(fp, dict)
    assert "title" in fp or "fingerprint_status" in fp
