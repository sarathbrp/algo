export interface Position {
  symbol: string
  side: 'long' | 'short'
  shares: number
  entryPrice: number
  lastBuyPrice: number | null
  currentPrice: number
  unrealizedPnl: number
  returnPct: number
  stopPct: number | null
  partialTaken: boolean
}

export interface Trade {
  time: string
  symbol: string
  side: 'long' | 'short'
  shares: number
  entryPrice: number
  exitPrice: number
  pnl: number
  returnPct: number
  exitReason: 'profit-target' | 'stop-loss' | 'trail-stop' | 'time-exit'
  barsHeld: number
}

export interface GateLogEntry {
  id: string
  time: string
  type: string
  symbol: string
  message: string
}

export interface PortfolioStats {
  equity: number
  dayPnl: number
  dayPnlPct: number
  unrealized: number
  totalReturn: number
  winRate: number
  winCount: number
  totalTrades: number
}

export interface RegimeScores {
  label: 'bullish' | 'neutral' | 'bearish'
  spy: number
  qqq: number
  vix: number
}
