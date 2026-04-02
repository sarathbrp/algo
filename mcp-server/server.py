"""AlgoSphere MCP Server.

Exposes the AlgoSphere trading platform to AI assistants via the
Model Context Protocol (MCP). Provides resources (read-only data),
tools (actions), and prompts (workflows).

Usage:
  PYTHONPATH=/path/to/algo ALGOSPHERE_USER_ID=your_user_id python mcp-server/server.py
"""
from __future__ import annotations

import sys
import os
import json
from typing import Any

# Ensure the project root is in the path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    GetPromptResult,
    PromptMessage,
)

from auth import get_user_id, get_session, verify_user
import resources as res
import tools
from prompts import PROMPTS

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = Server("algosphere")
USER_ID = get_user_id()


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

RESOURCE_MAP = {
    "algosphere://portfolio": ("Portfolio", "Current equity, cash, buying power, and day P&L", res.get_portfolio),
    "algosphere://positions": ("Open Positions", "All open positions with live prices and unrealized P&L", res.get_positions),
    "algosphere://watchlist": ("Watchlist", "Watchlist symbols with live quotes", res.get_watchlist),
    "algosphere://rules": ("Active Rules", "All active trading rules with conditions and actions", res.get_rules),
    "algosphere://pipeline": ("Rules Pipeline", "Current signal status — which rules are firing, watching, or holding", res.get_pipeline),
    "algosphere://trades": ("Recent Trades", "Completed trade history with P&L", res.get_trades),
    "algosphere://worker-status": ("Worker Status", "Bot state, worker health, and market regime", res.get_worker_status),
}


@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(uri=uri, name=name, description=desc, mimeType="text/plain")
        for uri, (name, desc, _) in RESOURCE_MAP.items()
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    entry = RESOURCE_MAP.get(str(uri))
    if not entry:
        return f"Unknown resource: {uri}"
    _, _, fn = entry
    session = get_session()
    try:
        return fn(session, USER_ID)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    Tool(
        name="create_rule",
        description="Create a trading rule for a ticker. Specify conditions (indicators + comparators) and actions (buy, stop loss, etc.).",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                "name": {"type": "string", "description": "Rule name"},
                "rule_type": {"type": "string", "enum": ["entry", "exit"], "description": "Entry (buy) or exit (sell) rule"},
                "conditions": {
                    "type": "array",
                    "description": "List of conditions. Each: {indicator, params, comparator, value}",
                    "items": {"type": "object"},
                },
                "actions": {
                    "type": "array",
                    "description": "List of actions. Each: {action, params}. Actions: enter_long, enter_short, exit_position, set_stop_loss, set_take_profit, set_trailing_stop, limit_entry_at, exit_at_price",
                    "items": {"type": "object"},
                },
                "logic": {"type": "string", "enum": ["AND", "OR"], "default": "AND"},
                "expires_at": {"type": "string", "description": "ISO 8601 expiration date (optional)"},
            },
            "required": ["symbol", "name", "rule_type", "conditions", "actions"],
        },
    ),
    Tool(
        name="apply_strategy",
        description="Apply a pre-built strategy template to a ticker. Templates: core_trend_following, pullback_entry, momentum_breakout",
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Template ID: core_trend_following, pullback_entry, or momentum_breakout"},
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "qty": {"type": "integer", "default": 1, "description": "Shares per trade"},
            },
            "required": ["template_id", "symbol"],
        },
    ),
    Tool(
        name="evaluate_rule",
        description="Test if a rule would fire right now for a symbol. Returns condition-by-condition breakdown with indicator values.",
        inputSchema={
            "type": "object",
            "properties": {
                "rule_id": {"type": "integer", "description": "ID of a saved rule to evaluate"},
                "symbol": {"type": "string", "description": "Ticker symbol (required if using inline rule_tree)"},
                "rule_tree": {"type": "object", "description": "Inline rule tree JSON (alternative to rule_id)"},
            },
        },
    ),
    Tool(
        name="backtest",
        description="Run a historical backtest simulation with entry and exit rules. Returns trades, P&L, win rate, and drawdown.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker to backtest"},
                "entry_rule_id": {"type": "integer", "description": "Saved entry rule ID"},
                "exit_rule_id": {"type": "integer", "description": "Saved exit rule ID"},
                "entry_rule_tree": {"type": "object", "description": "Inline entry rule tree"},
                "exit_rule_tree": {"type": "object", "description": "Inline exit rule tree"},
                "start_date": {"type": "string", "description": "Start date (ISO 8601)"},
                "end_date": {"type": "string", "description": "End date (ISO 8601)"},
                "initial_capital": {"type": "number", "default": 100000},
                "position_size_pct": {"type": "number", "default": 10},
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="sell_position",
        description="Close an open position. Cancels existing orders first, then sells at market or limit price.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker to sell"},
                "order_type": {"type": "string", "enum": ["market", "limit"], "default": "market"},
                "limit_price": {"type": "number", "description": "Minimum price (for limit orders)"},
                "qty": {"type": "integer", "description": "Shares to sell (omit for entire position)"},
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="place_order",
        description="Place a bracket order: limit entry with optional stop-loss and take-profit. Good for buying at support levels.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "side": {"type": "string", "enum": ["buy", "sell"], "default": "buy"},
                "qty": {"type": "integer", "description": "Number of shares"},
                "entry_price": {"type": "number", "description": "Limit entry price"},
                "stop_loss_pct": {"type": "number", "description": "Stop loss percentage"},
                "take_profit_pct": {"type": "number", "description": "Take profit percentage"},
            },
            "required": ["symbol", "qty"],
        },
    ),
    Tool(
        name="toggle_rule",
        description="Enable or disable a trading rule.",
        inputSchema={
            "type": "object",
            "properties": {
                "rule_id": {"type": "integer", "description": "Rule ID"},
                "active": {"type": "boolean", "description": "True to enable, False to disable"},
            },
            "required": ["rule_id", "active"],
        },
    ),
    Tool(
        name="delete_rule",
        description="Permanently delete a trading rule.",
        inputSchema={
            "type": "object",
            "properties": {
                "rule_id": {"type": "integer", "description": "Rule ID to delete"},
            },
            "required": ["rule_id"],
        },
    ),
    Tool(
        name="set_bot_state",
        description="Control the trading bot: running (trades actively), paused (manages positions only), or stopped (observation only).",
        inputSchema={
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["running", "paused", "stopped"]},
            },
            "required": ["state"],
        },
    ),
    Tool(
        name="scan_watchlist",
        description="Scan all watchlist symbols for technical setups. Returns SMA trend, RSI, ATR%, and gap for each ticker.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]

TOOL_FN_MAP = {
    "create_rule": tools.create_rule,
    "apply_strategy": tools.apply_strategy,
    "evaluate_rule": tools.evaluate_rule_tool,
    "backtest": tools.backtest_tool,
    "sell_position": tools.sell_position,
    "place_order": tools.place_order,
    "toggle_rule": tools.toggle_rule,
    "delete_rule": tools.delete_rule,
    "set_bot_state": tools.set_bot_state,
    "scan_watchlist": tools.scan_watchlist,
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOL_DEFINITIONS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    fn = TOOL_FN_MAP.get(name)
    if not fn:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    session = get_session()
    try:
        result = fn(session, USER_ID, **arguments)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@server.list_prompts()
async def list_prompts():
    from mcp.types import Prompt, PromptArgument
    return [
        Prompt(
            name=p["name"],
            description=p["description"],
            arguments=[
                PromptArgument(name=a["name"], description=a["description"], required=a.get("required", False))
                for a in p.get("arguments", [])
            ],
        )
        for p in PROMPTS.values()
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    prompt = PROMPTS.get(name)
    if not prompt:
        return GetPromptResult(
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=f"Unknown prompt: {name}"))],
        )
    template = prompt["template"]
    if arguments:
        template = template.format(**arguments)
    return GetPromptResult(
        messages=[PromptMessage(role="user", content=TextContent(type="text", text=template))],
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    # Verify user exists
    session = get_session()
    try:
        if not verify_user(session, USER_ID):
            print(f"Error: User '{USER_ID}' not found in database.", file=sys.stderr)
            sys.exit(1)
        print(f"AlgoSphere MCP Server starting for user {USER_ID}", file=sys.stderr)
    finally:
        session.close()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
