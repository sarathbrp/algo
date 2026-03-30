import { useRef, useEffect, useState } from 'react'
import { usePortfolio } from '@/hooks/usePortfolio'
import { Sparkline } from '@/components/charts/Sparkline'
import { RegimeRadar } from '@/components/charts/RegimeRadar'
import { Panel } from '@/components/layout/Panel'

const REGIME_COLORS: Record<string, string> = {
  bullish: 'var(--green)',
  neutral: 'var(--amber)',
  bearish: 'var(--red)',
}

function fmt(n: number, decimals = 2) {
  return Math.abs(n).toFixed(decimals)
}

function fmtSign(n: number) {
  return n >= 0 ? '+' : '-'
}

export function PortfolioPanel() {
  const { stats, regime, equityHistory } = usePortfolio()
  const prevEquity = useRef(stats.equity)
  const [flash, setFlash] = useState(false)

  useEffect(() => {
    if (prevEquity.current !== stats.equity) {
      setFlash(true)
      const t = setTimeout(() => setFlash(false), 400)
      prevEquity.current = stats.equity
      return () => clearTimeout(t)
    }
  }, [stats.equity])

  const whole = Math.floor(stats.equity).toLocaleString('en-US')
  const cents = String(Math.round((stats.equity % 1) * 100)).padStart(2, '0')
  const hasHistory = equityHistory.length > 1
  const isEmpty = stats.equity === 0 && stats.totalTrades === 0 && stats.unrealized === 0

  return (
    <Panel title="Portfolio Pulse" tag={regime.label.toUpperCase()} accented style={{ gridColumn: 1, gridRow: 1, display: 'flex', flexDirection: 'column' }}>

      <div style={{ padding: '18px 18px 14px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 10 }}>
          Account equity
        </div>
        <div style={{
          fontFamily: 'var(--font-display)',
          fontSize: 50,
          lineHeight: 1,
          color: 'var(--amber)',
          textShadow: '0 0 26px rgba(255,107,61,0.22)',
          letterSpacing: '-0.05em',
          display: 'flex',
          alignItems: 'baseline',
          gap: 3,
          animation: flash ? 'num-flash 0.4s ease' : 'none',
        }}>
          <span style={{ fontSize: 26, color: 'var(--text-dim)' }}>$</span>
          <span>{whole}</span>
          <span style={{ fontSize: 30, opacity: 0.7 }}>.{cents}</span>
        </div>
        <div style={{ marginTop: 8, fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--text-dim)' }}>
          {isEmpty ? 'Waiting for your first synced account snapshot.' : 'Live balance based on the latest saved portfolio snapshot.'}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
        {[
          {
            label: 'Day P&L',
            value: `${fmtSign(stats.dayPnl)}$${fmt(stats.dayPnl)}`,
            sub: `${fmtSign(stats.dayPnlPct)}${fmt(stats.dayPnlPct)}%`,
            color: stats.dayPnl >= 0 ? 'var(--green)' : 'var(--red)',
          },
          {
            label: 'Unrealized',
            value: `${fmtSign(stats.unrealized)}$${fmt(stats.unrealized)}`,
            sub: 'live open positions',
            color: stats.unrealized >= 0 ? 'var(--green)' : 'var(--red)',
          },
          {
            label: 'Total Return',
            value: `${fmtSign(stats.totalReturn)}${fmt(stats.totalReturn, 1)}%`,
            sub: 'from earliest loaded snapshot',
            color: stats.totalReturn >= 0 ? 'var(--green)' : 'var(--red)',
          },
          {
            label: 'Win Rate',
            value: `${stats.winRate.toFixed(1)}%`,
            sub: `${stats.winCount} of ${stats.totalTrades} trades`,
            color: 'var(--text-primary)',
          },
        ].map(({ label, value, sub, color }, i) => (
          <div key={label} style={{
            padding: '12px 16px',
            borderRight: i % 2 === 0 ? '1px solid var(--border)' : 'none',
            borderBottom: i < 2 ? '1px solid var(--border)' : 'none',
          }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 4 }}>
              {label}
            </div>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 600, letterSpacing: '-0.04em', color }}>
              {value}
            </div>
            <div style={{ fontFamily: 'var(--font-ui)', fontSize: 11, color: 'var(--text-muted)', marginTop: 3, lineHeight: 1.35 }}>
              {sub}
            </div>
          </div>
        ))}
      </div>

      <div style={{ padding: '12px 16px 14px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 8 }}>
          Equity trend
        </div>
        <Sparkline points={hasHistory ? equityHistory : [stats.equity, stats.equity]} />
        <div style={{ marginTop: 8, fontFamily: 'var(--font-ui)', fontSize: 12, color: 'var(--text-dim)' }}>
          {hasHistory ? 'Trend is based on the saved equity history already loaded for this account.' : 'The chart will wake up once more portfolio snapshots are recorded.'}
        </div>
      </div>

      <div style={{ padding: '14px 16px 16px', display: 'flex', alignItems: 'center', gap: 12, justifyContent: 'space-between' }}>
        <RegimeRadar regime={regime} />
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
            Market mood
          </div>
            <div style={{ marginTop: 6, fontFamily: 'var(--font-display)', fontSize: 22, color: REGIME_COLORS[regime.label], letterSpacing: '-0.04em', textTransform: 'capitalize' }}>
              {regime.label}
            </div>
            <div style={{ marginTop: 4, fontFamily: 'var(--font-ui)', fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.4 }}>
              SPY {regime.spy >= 0 ? '+' : ''}{(regime.spy * 100).toFixed(1)}% · QQQ {regime.qqq >= 0 ? '+' : ''}{(regime.qqq * 100).toFixed(1)}% · VIX {regime.vix.toFixed(1)}
            </div>
          </div>
      </div>
    </Panel>
  )
}
