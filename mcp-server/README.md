# AlgoSphere MCP Server

MCP (Model Context Protocol) server that lets AI assistants interact with the AlgoSphere trading platform.

## What It Does

Any AI assistant (Claude Desktop, Claude Code, Cursor) can:
- **Read** portfolio, positions, watchlist, rules, pipeline status, trades
- **Act** on the account: create rules, apply strategies, sell positions, place orders, run backtests
- **Analyze** the market: scan the watchlist for setups, evaluate rules, review risk

## Setup

### Claude Code

Add to your project's `.claude/settings.json`:

```json
{
  "mcpServers": {
    "algosphere": {
      "command": "python3",
      "args": ["mcp-server/server.py"],
      "env": {
        "ALGOSPHERE_USER_ID": "YOUR_USER_ID",
        "PYTHONPATH": "."
      }
    }
  }
}
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "algosphere": {
      "command": "python3",
      "args": ["/full/path/to/algo/mcp-server/server.py"],
      "env": {
        "ALGOSPHERE_USER_ID": "YOUR_USER_ID",
        "PYTHONPATH": "/full/path/to/algo"
      }
    }
  }
}
```

### Find Your User ID

Your user ID is in the dashboard URL or you can get it from the API:
```bash
curl -s http://localhost:8000/auth/me -H "Authorization: Bearer YOUR_TOKEN" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])"
```

## Resources (read-only)

| Ask Claude... | Resource Used |
|--------------|-------------|
| "What's my portfolio?" | `algosphere://portfolio` |
| "Show my open positions" | `algosphere://positions` |
| "What's on my watchlist?" | `algosphere://watchlist` |
| "What rules do I have?" | `algosphere://rules` |
| "What's the pipeline status?" | `algosphere://pipeline` |
| "Show my recent trades" | `algosphere://trades` |
| "Is the bot running?" | `algosphere://worker-status` |

## Tools (actions)

| Ask Claude... | Tool Used |
|--------------|----------|
| "Create a rule for AAPL: buy when RSI < 30" | `create_rule` |
| "Apply trend following to NVDA with 50 shares" | `apply_strategy` |
| "Would my GLD rule fire right now?" | `evaluate_rule` |
| "Backtest my CLS rules over the last year" | `backtest` |
| "Sell my SQQQ position" | `sell_position` |
| "Buy 100 shares of SPY at $650 with 2% stop" | `place_order` |
| "Disable rule #5" | `toggle_rule` |
| "Delete rule #3" | `delete_rule` |
| "Stop the bot" | `set_bot_state` |
| "Scan my watchlist for setups" | `scan_watchlist` |

## Prompts (workflows)

| Prompt | What It Does |
|--------|-------------|
| `market_scan` | Scans watchlist, ranks setups, suggests rules |
| `portfolio_review` | Reviews positions, risk, and P&L |
| `create_strategy` | Step-by-step guided rule creation for a ticker |

## Requirements

- Python 3.10+
- `mcp` package: `pip install mcp`
- AlgoSphere running (Docker stack or local)
- Database accessible (TiDB or SQLite)
