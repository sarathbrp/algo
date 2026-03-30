import { usePositions } from '@/hooks/usePositions'
import { Panel } from '@/components/layout/Panel'
import type { Position } from '@/types'

function SideBadge({ side }: { side: Position['side'] }) {
  const isLong = side === 'long'
  return (
    <span style={{
      display: 'inline-block',
      fontFamily: 'var(--font-mono)',
      fontSize: 9,
      letterSpacing: '0.14em',
      padding: '2px 6px',
      border: `1px solid ${isLong ? 'rgba(0,212,139,0.35)' : 'rgba(255,59,92,0.35)'}`,
      background: isLong ? 'var(--green-dim)' : 'var(--red-dim)',
      color: isLong ? 'var(--green)' : 'var(--red)',
      textTransform: 'uppercase',
    }}>
      {side}
    </span>
  )
}

const TH: React.CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: 9,
  fontWeight: 500,
  letterSpacing: '0.2em',
  textTransform: 'uppercase',
  color: 'var(--text-muted)',
  textAlign: 'right',
  padding: '9px 14px',
  borderBottom: '1px solid var(--border)',
  background: 'var(--bg-panel)',
  whiteSpace: 'nowrap',
}

const TD: React.CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: 12,
  padding: '10px 14px',
  color: 'var(--text-primary)',
  whiteSpace: 'nowrap',
  textAlign: 'right',
  borderBottom: '1px solid var(--border)',
}

export function PositionsPanel() {
  const { positions, isLoading } = usePositions()
  const exposure = positions.reduce((sum, pos) => sum + Math.abs(pos.currentPrice * pos.shares), 0)
  const largest = positions.reduce<Position | null>((current, pos) => {
    if (current === null) return pos
    return Math.abs(pos.currentPrice * pos.shares) > Math.abs(current.currentPrice * current.shares) ? pos : current
  }, null)
  const totalUnrealized = positions.reduce((sum, pos) => sum + pos.unrealizedPnl, 0)

  return (
    <Panel
      title="Open Positions"
      tag={positions.length === 0 ? 'Waiting' : `${positions.length} live`}
      accented
      style={{ gridColumn: 2, gridRow: 1, display: 'flex', flexDirection: 'column', minHeight: 300 }}
    >
      <div style={{ flex: 1, overflowX: 'auto' }}>
        {!isLoading && positions.length === 0 ? (
          <div style={{ height: '100%', minHeight: 240, display: 'grid', placeItems: 'center', padding: 20 }}>
            <div style={{ maxWidth: 520, textAlign: 'center' }}>
              <div style={{
                width: 66,
                height: 66,
                margin: '0 auto 14px',
                borderRadius: 22,
                background: 'linear-gradient(135deg, var(--amber-dim), var(--violet-dim))',
                border: '1px solid rgba(255,255,255,0.12)',
                display: 'grid',
                placeItems: 'center',
                fontFamily: 'var(--font-display)',
                fontSize: 22,
                color: 'var(--amber)',
              }}>
                0
              </div>
              <div style={{ fontFamily: 'var(--font-display)', fontSize: 28, letterSpacing: '-0.05em', lineHeight: 1.05 }}>
                No open positions right now.
              </div>
              <div style={{ marginTop: 8, fontFamily: 'var(--font-ui)', fontSize: 14, color: 'var(--text-dim)', lineHeight: 1.5 }}>
                That does not mean the bot is broken. It usually means the strategy is waiting for a valid setup or the worker has not placed the first trade yet.
              </div>
              <div style={{ marginTop: 14, display: 'grid', gap: 8 }}>
                {[
                  'If automation is running, the bot is still scanning for entries.',
                  'If you just connected the account, wait for the first worker heartbeat.',
                  'Your next trade will appear here once a position opens.',
                ].map((item) => (
                  <div key={item} style={{ padding: '10px 12px', borderRadius: 16, background: 'var(--bg-panel-alt)', border: '1px solid var(--border)', fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--text-dim)' }}>
                    {item}
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['Symbol', 'Side', 'Shares', 'Entry', 'Last Buy', 'Current', 'Unrealized', 'Return', 'Stop', 'Partial Exit'].map((h, i) => (
                  <th key={h} style={{ ...TH, textAlign: i === 0 ? 'left' : 'right' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => (
                <tr
                  key={pos.symbol}
                  style={{ cursor: 'default', transition: 'background 0.15s' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--amber-dim)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <td style={{ ...TD, textAlign: 'left', color: 'var(--amber)', fontWeight: 500, letterSpacing: '0.05em' }}>
                    {pos.symbol}
                  </td>
                  <td style={TD}><SideBadge side={pos.side} /></td>
                  <td style={{ ...TD, color: 'var(--text-dim)' }}>{pos.shares}</td>
                  <td style={{ ...TD, color: 'var(--text-dim)' }}>${pos.entryPrice.toFixed(2)}</td>
                  <td style={{ ...TD, color: 'var(--text-dim)' }}>
                    {pos.lastBuyPrice !== null ? `$${pos.lastBuyPrice.toFixed(2)}` : 'N/A'}
                  </td>
                  <td style={TD}>${pos.currentPrice.toFixed(2)}</td>
                  <td style={{ ...TD, color: pos.unrealizedPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {pos.unrealizedPnl >= 0 ? '+' : '-'}${Math.abs(pos.unrealizedPnl).toFixed(2)}
                  </td>
                  <td style={{ ...TD, color: pos.returnPct >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {pos.returnPct >= 0 ? '+' : ''}{pos.returnPct.toFixed(2)}%
                  </td>
                  <td style={{ ...TD, color: 'var(--text-dim)' }}>
                    {pos.stopPct !== null ? `${pos.stopPct.toFixed(2)}%` : 'N/A'}
                  </td>
                  <td style={{ ...TD, color: 'var(--text-dim)' }}>
                    {pos.partialTaken ? 'Yes' : 'No'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', display: 'flex', gap: 18 }}>
        {[
          ['Exposure', `$${exposure.toFixed(2)}`, 'var(--amber)'],
          ['Largest', largest ? `${largest.symbol} · $${Math.abs(largest.currentPrice * largest.shares).toFixed(2)}` : 'No position yet', 'var(--text-dim)'],
          ['Unrealized', `${totalUnrealized >= 0 ? '+' : '-'}$${Math.abs(totalUnrealized).toFixed(2)}`, totalUnrealized >= 0 ? 'var(--green)' : 'var(--red)'],
        ].map(([label, value, color]) => (
          <div key={label} style={{ display: 'flex', gap: 7, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)', letterSpacing: '0.03em' }}>
            <span>{label}</span>
            <strong style={{ color, fontWeight: 500 }}>{value}</strong>
          </div>
        ))}
      </div>
    </Panel>
  )
}
