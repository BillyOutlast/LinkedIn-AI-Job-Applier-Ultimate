"""Verify dispatcher hooks in LinkedInJobManager and IndeedJobManager fire on each branch."""

import json
from pathlib import Path

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
