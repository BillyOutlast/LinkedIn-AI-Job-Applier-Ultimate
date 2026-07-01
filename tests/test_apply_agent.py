from unittest.mock import AsyncMock, MagicMock

import pytest

from src.llm.apply_agent import ApplyAgent


@pytest.fixture
def agent(monkeypatch):
    # Default LLM_MODEL_TYPE ("openai_compatible") demands llm_api_url;
    # override the bound module-level name so __init__ picks a model
    # the api_key="fake" fixture can satisfy.
    from src.llm import apply_agent as mod

    monkeypatch.setattr(mod, "LLM_MODEL_TYPE", "openai", raising=False)
    return ApplyAgent(api_key="fake", browser_storage_state="/tmp/none.json")


@pytest.mark.asyncio
async def test_apply_agent_routes_workday_url_to_handler(agent, monkeypatch):
    """Recognize-match: ApplyAgent delegates to handler and returns its result."""
    from src.job_manager.workday import workday_applier as wd_mod
    from src.llm import apply_agent as mod

    monkeypatch.setattr(mod, "APPLY_AGENT_RECOGNIZE", True, raising=False)
    fake_handler = AsyncMock()
    fake_handler.apply_to_job = AsyncMock(return_value=("Success", ""))
    # Provide the page attribute the helper reads before constructing
    # the handler — the bare ApplyAgent fixture doesn't carry one.
    agent.page = MagicMock()

    # Swap WorkdayApplier with a fake class whose __new__ returns our
    # AsyncMock handler — preserves the helper's `is WorkdayApplier` check.
    class _FakeWorkday:
        def __new__(cls, *args, **kwargs):
            return fake_handler

    monkeypatch.setattr(wd_mod, "WorkdayApplier", _FakeWorkday)
    fake_match = MagicMock(name="workday", handler_factory=lambda: wd_mod.WorkdayApplier)
    monkeypatch.setattr(mod, "recognize", lambda url: fake_match)
    # Stub the LLM Agent to assert it does NOT run on recognize-match
    monkeypatch.setattr(
        mod,
        "Agent",
        MagicMock(side_effect=AssertionError("LLM should not run on recognize-match")),
    )

    result = await agent.apply_to_job("https://uhaul.wd1.myworkdayjobs.com/job/1")

    assert result[0] == "Success"
    fake_handler.apply_to_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_agent_falls_through_to_llm_when_disabled(agent, monkeypatch):
    """APPLY_AGENT_RECOGNIZE=False → LLM flow runs (recognize not consulted)."""
    from src.llm import apply_agent as mod

    # APPLY_AGENT_RECOGNIZE is bound at import-time into apply_agent's
    # namespace; patch the bound name, not the config module attribute.
    monkeypatch.setattr(mod, "APPLY_AGENT_RECOGNIZE", False, raising=False)
    called = []
    monkeypatch.setattr(mod, "recognize", lambda url: called.append(url) or None)
    # Stub the LLM Agent to return a successful run.
    fake_agent = MagicMock(run=AsyncMock(return_value=None))
    fake_agent_cls = MagicMock(return_value=fake_agent)
    monkeypatch.setattr(mod, "Agent", fake_agent_cls)

    result = await agent.apply_to_job("https://uhaul.wd1.myworkdayjobs.com/job/1")

    assert called == [], "recognize() should not be called when flag is False"
    # LLM path was taken: the LLM Agent was constructed and run.
    fake_agent_cls.assert_called_once()
    fake_agent.run.assert_awaited_once()
    # apply_to_job wraps the LLM success in ("Success", "").
    assert result[0] == "Success"


@pytest.mark.asyncio
async def test_apply_agent_falls_through_to_llm_when_handler_raises(agent, monkeypatch):
    """Handler exception → LLM flow runs (spec: fall through on handler raise)."""
    from src.job_manager.workday import workday_applier as wd_mod
    from src.llm import apply_agent as mod

    monkeypatch.setattr(mod, "APPLY_AGENT_RECOGNIZE", True, raising=False)

    # Wire a fake Workday handler that raises when invoked.
    raising_handler = AsyncMock()
    raising_handler.apply_to_job = AsyncMock(side_effect=RuntimeError("handler boom"))
    agent.page = MagicMock()

    class _FakeWorkday:
        def __new__(cls, *args, **kwargs):
            return raising_handler

    monkeypatch.setattr(wd_mod, "WorkdayApplier", _FakeWorkday)
    fake_match = MagicMock(name="workday", handler_factory=lambda: wd_mod.WorkdayApplier)
    monkeypatch.setattr(mod, "recognize", lambda url: fake_match)

    # LLM should run with its normal shape ("Success", "") wrapping a successful run.
    fake_agent = MagicMock(run=AsyncMock(return_value=None))
    fake_agent_cls = MagicMock(return_value=fake_agent)
    monkeypatch.setattr(mod, "Agent", fake_agent_cls)

    result = await agent.apply_to_job("https://uhaul.wd1.myworkdayjobs.com/job/1")

    # Handler was invoked (and raised); LLM Agent then constructed and run.
    raising_handler.apply_to_job.assert_awaited_once()
    fake_agent_cls.assert_called_once()
    fake_agent.run.assert_awaited_once()
    # Result shape is the LLM-flow shape ("Success", "") — not the handler's exception.
    assert result == ("Success", "")
