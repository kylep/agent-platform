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
                                        "mcp__platform__get_quota_usage",
                                        "mcp__platform__artifacts",
                                        "mcp__platform__image_gen",
                                        "mcp__platform__quota_ok"]
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


def test_the_artifact_grants_ride_the_participant_rung():
    """docs/design/23: `artifacts` is default-granted and `image_gen` is the
    artist's, and both reach `/api/artifacts/*` as the agent itself — the
    `relay` rung, never `annotator`, for the reason the quota grant gives."""
    from agentplatform.agentspec import (PLATFORM_MCP_RELAY_TOOLS, TOOL_ARTIFACTS,
                                         TOOL_HELP, TOOL_IMAGE_GEN,
                                         platform_token_role)
    assert TOOL_ARTIFACTS == "mcp__platform__artifacts"
    assert TOOL_IMAGE_GEN == "mcp__platform__image_gen"
    for tool in (TOOL_ARTIFACTS, TOOL_IMAGE_GEN):
        assert tool in PLATFORM_MCP_RELAY_TOOLS
        assert platform_token_role([tool]) == "relay"
    names = {t["name"]: t.get("display_name") for t in TOOL_HELP}
    assert names[TOOL_ARTIFACTS] == "Artifacts"
    assert names[TOOL_IMAGE_GEN] == "Image generation"


def test_the_quota_gate_rides_the_participant_rung_and_is_explained():
    """docs/design/24: `quota_ok` reaches `/api/quota/ok` as the agent itself —
    the `relay` rung, never `annotator`, for the reason `get_quota_usage`
    gives — and, unlike it, is NOT default-granted: the engineer and the QA
    hold it because an admin decided they should. Grantable means explained:
    the help entry is what the lockstep test in test_help pins."""
    from agentplatform.agentspec import (PLATFORM_MCP_RELAY_TOOLS, TOOL_HELP,
                                         TOOL_QUOTA_OK, platform_token_role)
    assert TOOL_QUOTA_OK == "mcp__platform__quota_ok"
    assert TOOL_QUOTA_OK in PLATFORM_MCP_RELAY_TOOLS
    assert TOOL_QUOTA_OK in AVAILABLE_TOOLS
    assert platform_token_role([TOOL_QUOTA_OK]) == "relay"
    entry = next(t for t in TOOL_HELP if t["name"] == TOOL_QUOTA_OK)
    assert entry["display_name"] == "Quota gate"
    assert "NOT granted by default" in entry["description"]


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


def test_the_playwright_grant_is_a_dev_only_harness_tool():
    """docs/design/25: `PlaywrightMCP` is a harness GRANT name — what a `role:
    dev` agent declares to get the runner-started Playwright MCP server — and
    not a Claude tool name. It sits in CLAUDE_TOOLS so a row may hold it, and
    its help entry is marked `dev_only`: for any other run the runner drops
    it the way it drops the sensitive set, so declaring it does nothing. It
    is NOT sensitive — the sensitive set is the runner's always-denied list,
    which `test_help` pins and this grant must not join."""
    from agentplatform.agentspec import CLAUDE_TOOLS, TOOL_HELP, TOOL_PLAYWRIGHT_MCP
    assert TOOL_PLAYWRIGHT_MCP == "PlaywrightMCP"
    assert TOOL_PLAYWRIGHT_MCP in CLAUDE_TOOLS
    assert TOOL_PLAYWRIGHT_MCP in AVAILABLE_TOOLS
    entry = next(t for t in TOOL_HELP if t["name"] == TOOL_PLAYWRIGHT_MCP)
    assert entry["kind"] == "claude" and entry["dev_only"] is True
    assert not entry.get("sensitive")
    assert "role: dev" in entry["description"]
    # What is true of the boundary (docs/design/25 review): the browser's
    # resolver, not the MCP server's origin flags, is what keeps it home.
    assert "resolves only the platform's web host" in entry["description"]
    assert "NOTFOUND" in entry["description"] and "advisory" in entry["description"]
    assert [t["name"] for t in TOOL_HELP if t.get("dev_only")] == [TOOL_PLAYWRIGHT_MCP]
