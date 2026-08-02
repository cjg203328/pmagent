from __future__ import annotations

import asyncio

from artpm_agent.agent import ArtPMAgent


class CancelledMarketClient:
    @staticmethod
    async def list_market_skills(*, force: bool = False):
        del force
        raise asyncio.CancelledError


def _agent() -> ArtPMAgent:
    agent = object.__new__(ArtPMAgent)
    agent.mcp_client = CancelledMarketClient()
    agent.market_skills = []
    agent._market_skills_loaded = False
    return agent


def test_background_market_prefetch_treats_shutdown_cancellation_as_normal():
    agent = _agent()

    agent._prefetch_market_skills()

    assert agent.market_skills == []
    assert agent._market_skills_loaded is False
    assert agent._market_skills_prefetch_error == "cancelled"


def test_synchronous_market_refresh_treats_cancellation_as_normal():
    agent = _agent()

    assert agent.list_market_skills(force=True) == []
    assert agent._market_skills_loaded is False
