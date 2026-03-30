import { useBotStatus } from '@/hooks/useBotStatus'
import { usePortfolio } from '@/hooks/usePortfolio'
import { usePositions } from '@/hooks/usePositions'

function currency(value: number) {
  return `$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`
}

function pctColor(pct: number) {
  return pct >= 0 ? 'var(--green)' : 'var(--red)'
}

export function DashboardHero() {
  const { status, mode, workerStatus, heartbeatLabel, workerSummary, isConfigured } = useBotStatus()
  const { stats, regime } = usePortfolio()
  const { positions, updatedAt } = usePositions()

  const lastUpdated = updatedAt
    ? new Date(updatedAt).toLocaleTimeString('en-US', {
        timeZone: 'America/New_York',
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : null

  const headline = !isConfigured
    ? 'Connect your broker and turn on automation.'
    : positions.length === 0
      ? 'No open positions right now.'
      : `${positions.length} live position${positions.length === 1 ? '' : 's'} currently open.`

  const subline = !isConfigured
    ? 'Finish setup in one place, then this dashboard will start showing live account activity.'
    : workerSummary

  return (
    <section style={{
      position: 'relative',
      display: 'grid',
      gridTemplateColumns: 'minmax(0, 1.5fr) minmax(320px, 0.95fr)',
      gap: 14,
      marginTop: 12,
      marginBottom: 4,
    }}>
      <div style={{
        position: 'relative',
        overflow: 'hidden',
        borderRadius: 30,
        padding: '20px 22px',
        background: 'linear-gradient(135deg, rgba(255,107,61,0.28) 0%, rgba(124,108,255,0.26) 52%, rgba(93,183,255,0.24) 100%)',
        border: '1px solid rgba(255,255,255,0.18)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.08)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start' }}>
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(255,200,120,0.9)', marginBottom: 8, textShadow: '0 0 12px rgba(255,180,80,0.3)' }}>
              Live Account Snapshot
            </div>
            <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 34, lineHeight: 1.03, letterSpacing: '-0.05em', maxWidth: 620, color: '#fff', textShadow: '0 2px 8px rgba(0,0,0,0.3)' }}>
              {headline}
            </h1>
            <p style={{ marginTop: 10, maxWidth: 640, fontFamily: 'var(--font-ui)', fontSize: 14, lineHeight: 1.45, color: 'rgba(255,255,255,0.88)' }}>
              {subline}
            </p>
          </div>

          <div style={{
            alignSelf: 'stretch',
            minWidth: 140,
            borderRadius: 22,
            padding: '14px 16px',
            background: 'rgba(7,11,20,0.45)',
            border: '1px solid rgba(255,255,255,0.15)',
            backdropFilter: 'blur(14px)',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.06)',
          }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.75)', marginBottom: 10 }}>
              Bot Status
            </div>
            <div style={{
              fontFamily: 'var(--font-display)',
              fontSize: 24,
              fontWeight: 700,
              color: status === 'running' ? '#34d399' : status === 'paused' ? '#fbbf24' : '#f87171',
              textShadow: status === 'running' ? '0 0 16px rgba(52,211,153,0.4)' : status === 'paused' ? '0 0 16px rgba(251,191,36,0.4)' : '0 0 16px rgba(248,113,113,0.4)',
            }}>
              {status.toUpperCase()}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'rgba(255,255,255,0.78)', marginTop: 8, lineHeight: 1.5 }}>
              {mode.toUpperCase()} mode
              <br />
              Worker: {workerStatus.toUpperCase()}
              <br />
              Heartbeat: {heartbeatLabel}
            </div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 10, marginTop: 16 }}>
          {(() => {
            const dayColor = stats.dayPnl >= 0 ? '#34d399' : '#f87171'
            const moodColor = regime.label === 'bullish' ? '#34d399' : regime.label === 'bearish' ? '#f87171' : '#fbbf24'
            const cards: [string, string, string, string][] = [
              ['Equity', currency(stats.equity), 'Account value', '#fff'],
              ['Day move', `${stats.dayPnl >= 0 ? '+' : '-'}${currency(stats.dayPnl)}`, `${stats.dayPnlPct >= 0 ? '+' : ''}${stats.dayPnlPct.toFixed(2)}% today`, dayColor],
              ['Open positions', String(positions.length), positions.length === 0 ? 'Waiting for the next entry' : 'Live exposure on the book', '#93c5fd'],
              ['Market mood', regime.label.toUpperCase(), `SPY ${regime.spy >= 0 ? '+' : ''}${(regime.spy * 100).toFixed(1)}% · QQQ ${regime.qqq >= 0 ? '+' : ''}${(regime.qqq * 100).toFixed(1)}%`, moodColor],
            ]
            return cards.map(([label, value, hint, color], index) => (
              <div key={label} style={{
                padding: '12px 14px',
                borderRadius: 22,
                background: index === 0 ? 'rgba(255,255,255,0.16)' : 'rgba(7,11,20,0.38)',
                border: '1px solid rgba(255,255,255,0.14)',
                backdropFilter: 'blur(10px)',
                boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.05)',
              }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.72)' }}>
                  {label}
                </div>
                <div style={{ marginTop: 8, fontFamily: 'var(--font-display)', fontSize: 24, lineHeight: 1, letterSpacing: '-0.05em', color, textShadow: `0 0 14px ${color}33` }}>
                  {value}
                </div>
                <div style={{ marginTop: 6, fontFamily: 'var(--font-ui)', fontSize: 12, color: 'rgba(255,255,255,0.82)', lineHeight: 1.35 }}>
                  {hint}
                </div>
              </div>
            ))
          })()}
        </div>
      </div>

      {/* Live Tickers — open positions with price + return */}
      <div style={{
        borderRadius: 30,
        padding: '18px',
        background: 'var(--bg-panel)',
        border: '1px solid var(--border)',
        boxShadow: 'var(--surface-shadow)',
        backdropFilter: 'blur(18px)',
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
        overflow: 'hidden',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
            Live Tickers
          </div>
          {lastUpdated && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)' }}>
              {lastUpdated} ET
            </div>
          )}
        </div>

        {positions.length === 0 ? (
          <div style={{ fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--text-dim)', padding: '12px 0' }}>
            No open positions. Tickers will appear when the bot opens trades.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1 }}>
            {positions.map((pos) => {
              const returnPct = pos.returnPct
              const arrow = returnPct >= 0 ? '\u25B2' : '\u25BC'
              return (
                <div key={pos.symbol} style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr auto auto',
                  alignItems: 'center',
                  gap: 10,
                  padding: '10px 12px',
                  borderRadius: 16,
                  background: 'var(--bg-panel-alt)',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}>
                  <div>
                    <div style={{ fontFamily: 'var(--font-display)', fontSize: 16, letterSpacing: '-0.03em' }}>
                      {pos.symbol}
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)', marginTop: 2 }}>
                      {pos.side.toUpperCase()} · {pos.shares} shares
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontFamily: 'var(--font-display)', fontSize: 18, letterSpacing: '-0.04em' }}>
                      ${pos.currentPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)', marginTop: 2 }}>
                      entry ${pos.entryPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                  </div>
                  <div style={{
                    minWidth: 72,
                    textAlign: 'right',
                    padding: '6px 10px',
                    borderRadius: 12,
                    background: returnPct >= 0 ? 'rgba(52,211,153,0.12)' : 'rgba(248,113,113,0.12)',
                    border: `1px solid ${returnPct >= 0 ? 'rgba(52,211,153,0.2)' : 'rgba(248,113,113,0.2)'}`,
                  }}>
                    <div style={{ fontFamily: 'var(--font-display)', fontSize: 14, color: pctColor(returnPct), letterSpacing: '-0.02em' }}>
                      {arrow} {Math.abs(returnPct).toFixed(2)}%
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: pctColor(pos.unrealizedPnl), marginTop: 2 }}>
                      {pos.unrealizedPnl >= 0 ? '+' : '-'}${Math.abs(pos.unrealizedPnl).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}
