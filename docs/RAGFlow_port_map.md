  RAGFlow Port Map

  ┌────────────────────────────────────────────────────────────────┐
  │ Docker Container → Host Machine                                │
  ├────────────────────────────────────────────────────────────────┤
  │                                                                │
  │  Port 80   →  8080     Web UI (nginx)                          │
  │                        Access: http://192.168.25.249:8080      │
  │                        You see this in browser                 │
  │                                                                │
  │  Port 9380 →  9380     RAGFlow HTTP API                        │
  │                        Access: http://127.0.0.1:9380/api/v1/   │
  │                        REST API for datasets, retrieval, etc.  │
  │                        This is the MAIN API backend            │
  │                                                                │
  │  Port 9381 →  9381     Admin Server                            │
  │                        Access: http://127.0.0.1:9381           │
  │                        System administration interface         │
  │                                                                │
  │  Port 9382 →  9382     MCP Server (when enabled)               │
  │                        Access: http://127.0.0.1:9382/mcp       │
  │                        For AI tools (Claude Code, etc.)        │
  │                                                                │
  └────────────────────────────────────────────────────────────────┘

  Why MCP uses --mcp-base-url=http://127.0.0.1:9380?

  When the MCP server runs (either manually or in Docker), it needs to call the RAGFlow API to:
  - List datasets
  - Retrieve documents
  - Query knowledge bases

  So the flow is:

  Claude Code (client)
      ↓ HTTP
  MCP Server (port 9382/9383)
      ↓ HTTP calls to http://127.0.0.1:9380/api/v1/...
  RAGFlow API (port 9380)
      ↓
  Database/Elasticsearch/etc.

  Summary:
  - Port 8080: Web UI (what you see in browser)
  - Port 9380: RAGFlow REST API (backend that MCP server calls)
  - Port 9381: Admin panel
  - Port 9382: MCP server (bridge between AI tools and RAGFlow API)