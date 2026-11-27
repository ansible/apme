# APME MCP bundle

Bundle contains APME MCP server configuration and `apme-scan-review` Skill.

Install `apme-mcp` from APME, start local rootless APME stack, then register
`mcp.json` with MCP host. Register `skills/apme-scan-review/SKILL.md` with host
Skill discovery. The Skill defines scan workflow and safety boundaries; MCP
provides Gateway-backed tools.

Default Gateway endpoint is `http://127.0.0.1:8080`. Override
`APME_GATEWAY_URL` in `mcp.json` only for a supported local Gateway endpoint.
Current MCP adapter accepts loopback Gateway URLs only.
