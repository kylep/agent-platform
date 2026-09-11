"""What is left of agentspec once definitions stopped being files: the slug
rule every agent name obeys, and the grantable-tool constants."""
import pytest

from agentplatform.agentspec import AVAILABLE_TOOLS, validate_agent_name


def test_validate_agent_name():
    assert validate_agent_name("my-agent-1") == "my-agent-1"
    for bad in ["", "-lead", "UPPER", "has space", "a/b", "x" * 64]:
        with pytest.raises(ValueError):
            validate_agent_name(bad)


def test_mcp_broker_tools_are_selectable():
    """The broker's tools must be grantable, or the write path 422s on the real
    config of the agents that already declare them (health-monitor,
    run-summarizer) — making those agents uneditable."""
    assert "mcp__platform__runs_read" in AVAILABLE_TOOLS
    assert "mcp__platform__query_app" in AVAILABLE_TOOLS


def test_the_relay_grant_is_selectable_but_does_not_promote():
    """Relay's tool must be grantable like any other and must stay OUT of the
    annotator-promoting list: holding it reaches /api/relay/* as the agent
    itself and nothing else."""
    from agentplatform.agentspec import (GRANTABLE_PLATFORM_TOOLS,
                                         PLATFORM_MCP_RELAY_TOOLS,
                                         PLATFORM_MCP_TOOLS)
    assert PLATFORM_MCP_RELAY_TOOLS == ["mcp__platform__relay"]
    assert "mcp__platform__relay" in GRANTABLE_PLATFORM_TOOLS
    assert "mcp__platform__relay" in AVAILABLE_TOOLS
    assert "mcp__platform__relay" not in PLATFORM_MCP_TOOLS
