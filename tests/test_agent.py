from atlas.agent import AtlasAgent
from atlas.venues.fixtures import fixture_books, fixture_markets


async def test_agent_exposes_proposal_provenance():
    markets = fixture_markets()
    run = await AtlasAgent(
        {"kalshi": markets["kalshi"], "polymarket_us": markets["polymarket_us"]},
        books=fixture_books(),
    ).run()

    assert run.state["proposal_source"] == "local_lexical_fallback"
    assert run.state["proposal_count"] == 1
