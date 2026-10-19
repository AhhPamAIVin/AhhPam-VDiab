# mcp/

MCP (Model Context Protocol) servers and tool definitions exposed to external MCP clients.

No servers are implemented yet — add them here as `mcp/<server_name>/server.py`, each registering
its own tools. This is separate from the in-process LangGraph tools in `models/agents/tools/`, which
are only callable by the agent graph itself.
