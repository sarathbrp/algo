"""MCP Prompts — pre-built workflows for AI assistants."""
from __future__ import annotations

PROMPTS = {
    "market_scan": {
        "name": "market_scan",
        "description": "Scan the watchlist for the best trading setups right now",
        "arguments": [],
        "template": (
            "You are a trading analyst for AlgoSphere. Scan the user's watchlist using the "
            "scan_watchlist tool, then analyze the results.\n\n"
            "For each symbol, assess:\n"
            "1. Trend: Is price above or below the 20-day and 50-day SMAs?\n"
            "2. Momentum: Is RSI oversold (<30), overbought (>70), or neutral?\n"
            "3. Volatility: Is ATR% high (>3%) or low (<2%)?\n"
            "4. Gap: Did it gap significantly at open?\n\n"
            "Rank the symbols from strongest to weakest setup.\n"
            "For the top 2-3 candidates, suggest specific entry rules with stop-loss and take-profit.\n"
            "Use the create_rule tool to create rules for any that look actionable."
        ),
    },
    "portfolio_review": {
        "name": "portfolio_review",
        "description": "Review open positions, risk exposure, and P&L progress",
        "arguments": [],
        "template": (
            "You are a risk manager for AlgoSphere. Review the current portfolio state.\n\n"
            "1. Use the portfolio resource to get account equity and day P&L.\n"
            "2. Use the positions resource to see all open positions.\n"
            "3. Use the pipeline resource to check if any exit rules are close to firing.\n"
            "4. Use the trades resource to see recent trade history.\n\n"
            "Analyze:\n"
            "- Total exposure (sum of position values vs equity)\n"
            "- Concentration risk (is one position too large?)\n"
            "- P&L trajectory (winning or losing overall?)\n"
            "- Any positions that should be closed based on the rules pipeline\n\n"
            "Provide a clear recommendation: hold, tighten stops, or close specific positions."
        ),
    },
    "create_strategy": {
        "name": "create_strategy",
        "description": "Guided step-by-step rule creation for a ticker",
        "arguments": [
            {"name": "symbol", "description": "The ticker symbol to create a strategy for", "required": True},
        ],
        "template": (
            "Help the user create a complete trading strategy (entry + exit rules) for {symbol}.\n\n"
            "Steps:\n"
            "1. First, evaluate {symbol}'s current technical state using evaluate_rule with common indicators.\n"
            "2. Based on the analysis, suggest an appropriate strategy:\n"
            "   - If trending up: trend-following (SMA crossover + ATR filter)\n"
            "   - If oversold: buy-the-dip (RSI + support level)\n"
            "   - If breaking out: momentum (EMA cross + volume)\n"
            "3. Explain each condition in plain English before creating.\n"
            "4. Ask the user for position size (number of shares).\n"
            "5. Add gap protection (gap_pct between -5% and 5%).\n"
            "6. Create both entry and exit rules using create_rule.\n"
            "7. Run a backtest to validate the strategy.\n\n"
            "Always include stop-loss and take-profit in the entry rule actions."
        ),
    },
}
