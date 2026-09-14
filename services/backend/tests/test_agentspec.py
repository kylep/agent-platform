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


def test_the_participant_grants_are_selectable_but_do_not_promote():
    """The relay, tickets and wiki tools must be grantable like any other and
    must stay OUT of the annotator-promoting list: holding one reaches
    /api/relay/*, /api/tickets/* or /api/wiki/* as the agent itself and nothing
    else."""
    from agentplatform.agentspec import (GRANTABLE_PLATFORM_TOOLS,
                                         PLATFORM_MCP_RELAY_TOOLS,
                                         PLATFORM_MCP_TOOLS)
    assert PLATFORM_MCP_RELAY_TOOLS == ["mcp__platform__relay",
                                        "mcp__platform__tickets",
                                        "mcp__platform__wiki",
                                        "mcp__platform__get_quota_usage"]
    for tool in PLATFORM_MCP_RELAY_TOOLS:
        assert tool in GRANTABLE_PLATFORM_TOOLS
        assert tool in AVAILABLE_TOOLS
        assert tool not in PLATFORM_MCP_TOOLS


def test_the_quota_grant_rides_the_participant_rung(monkeypatch):
    """docs/design/22: asking how much usage is left reaches /api/quota as the
    agent itself, so it earns the `relay` rung and must not earn `annotator` —
    the tool is default-granted, and promoting on a default grant would hand
    every agent on the platform the run/metrics surface."""
    from agentplatform.agentspec import (PLATFORM_MCP_RELAY_TOOLS, TOOL_QUOTA,
                                         platform_token_role)
    assert TOOL_QUOTA == "mcp__platform__get_quota_usage"
    assert TOOL_QUOTA in PLATFORM_MCP_RELAY_TOOLS
    assert TOOL_QUOTA in AVAILABLE_TOOLS
    assert platform_token_role([TOOL_QUOTA]) == "relay"
    assert platform_token_role(["mcp__platform__runs_read", TOOL_QUOTA]) == "annotator"


def test_the_quota_grant_survives_the_runners_allowed_tools_filter():
    """The runner writes a granted tool into the agent's frontmatter and parses
    it back out for `--allowedTools`, dropping anything that is not a bare tool
    name (services/runner/runner.py `_install_agent`) — a permission SPECIFIER
    would otherwise slip past the sensitive-set strip. A grant name that missed
    that shape would be granted everywhere and allowed nowhere."""
    import re

    from agentplatform.agentspec import GRANTABLE_PLATFORM_TOOLS
    for tool in GRANTABLE_PLATFORM_TOOLS:
        assert re.fullmatch(r"[A-Za-z0-9_]+", tool)
