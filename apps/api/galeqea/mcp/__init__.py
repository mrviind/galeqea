"""QE tool pack for the agent.

**This package does not own a tool registry, and that is deliberate.**

QE Agent already has one canonical registry in ``galeqea.ai.tools``. It is what
the agent calls, what ``galeqea.mcp_server`` exposes to external MCP hosts
(Claude Code, Cursor, VS Code), what validates arguments against each schema, and
what routes every state-changing call through the human approval gate.

A second registry beside it would fork all four of those. Tools would appear in
the chat but not over MCP, or carry annotations the gate never sees, and the two
would drift the first time someone added a tool to whichever one they happened to
be looking at. So the tools here *register into* the canonical registry, and gain
schema validation, the approval gate, MCP exposure and both provider adapters by
doing so — none of which is reimplemented here.

Importing this package is what installs the pack.
"""

from . import (
    agile,  # noqa: F401  (import registers the ceremony tools)
    qe_tools,  # noqa: F401  (import registers the tools)
)

__all__ = ["qe_tools", "agile"]
