import { useTrades } from '@/hooks/useTrades'
import { Panel } from '@/components/layout/Panel'
import type { Trade } from '@/types'

const EXIT_STYLES: Record<Trade['exitReason'], { label: string; color: string; bg: string; border: string }> = {
  'profit-target': { label: 'PROFIT', color: '#34d399', bg: 'rgba(52,211,153,0.12)', border: 'rgba(52,211,153,0.3)' },
  'stop-loss':     { label: 'STOP',   color: '#f87171', bg: 'rgba(248,113,113,0.12)', border: 'rgba(248,113,113,0.3)' },
  'trail-stop':    { label: 'TRAIL',  color: '#93c5fd', bg: 'rgba(147,197,253,0.10)', border: 'rgba(147,197,253,0.3)' },
  'time-exit':     { label: 'TIME',   color: '#fbbf24', bg: 'rgba(251,191,36,0.10)', border: 'rgba(251,191,36,0.3)' },
}

function ExitReasonBadge({ reason }: { reason: Trade['exitReason'] }) {
  const s = EXIT_STYLES[reason]
  return (
    <span style={{
      fontFamily: 'var(--font-mono)',
      fontSize: 8,
      fontWeight: 600,
      letterSpacing: '0.08em',
      textTransform: 'uppercase',
      padding: '2px 8px',
      borderRadius: 6,
      border: `1px solid ${s.border}`,
      background: s.bg,
      color: s.color,
      whiteSpace: 'nowrap',
    }}>
      {s.label}
    </span>
  )
}

const TH: React.CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: 9,
  fontWeight: 600,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: 'rgba(255,255,255,0.5)',
  textAlign: 'right',
  padding: '10px 14px',
  borderBottom: '1px solid rgba(255,255,255,0.08)',
  background: 'transparent',
  whiteSpace: 'nowrap',
}

const TD: React.CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: 11,
  padding: '9px 14px',
  color: 'rgba(255,255,255,0.88)',
  whiteSpace: 'nowrap',
  textAlign: 'right',
  borderBottom: '1px solid rgba(255,255,255,0.05)',
}

export function TradesPanel() {
  const { trades } = useTrades()

  const wins = trades.filter((t) => t.pnl > 0).length
  const losses = trades.filter((t) => t.pnl < 0).length
  const tagText = trades.length === 0
    ? 'NO FILLS'
    : `${trades.length} FILLS · ${wins}W ${losses}L`

  return (
    <Panel
      title="RECENT TRADES"
      tag={tagText}
      accented
      style={{ gridColumn: 2, gridRow: 2, minHeight: 160 }}
    >
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {['TIME', 'SYMBOL', 'SIDE', 'SHARES', 'ENTRY', 'EXIT', 'P&L', 'RETURN', 'REASON', 'HELD'].map((h, i) => (
                <th key={h} style={{ ...TH, textAlign: i <= 1 ? 'left' : 'right' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 && (
              <tr>
                <td colSpan={10} style={{ ...TD, textAlign: 'left', color: 'rgba(255,255,255,0.4)', padding: '18px 14px', fontFamily: 'var(--font-ui)', fontSize: 13 }}>
                  No closed trades yet. Trades appear here when the bot exits a position.
                </td>
              </tr>
            )}
            {trades.map((t, idx) => {
              const isWin = t.pnl >= 0
              const rowBg = idx % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)'
              return (
                <tr
                  key={`${t.time}-${t.symbol}`}
                  style={{ cursor: 'default', transition: 'background 0.15s', background: rowBg }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.06)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = rowBg)}
                >
                  <td style={{ ...TD, textAlign: 'left', color: 'rgba(255,255,255,0.5)', fontSize: 10 }}>{t.time}</td>
                  <td style={{ ...TD, textAlign: 'left', fontFamily: 'var(--font-display)', fontSize: 13, fontWeight: 600, color: '#fff', letterSpacing: '-0.02em' }}>{t.symbol}</td>
                  <td style={TD}>
                    <span style={{
                      display: 'inline-block',
                      fontFamily: 'var(--font-mono)',
                      fontSize: 8,
                      fontWeight: 600,
                      letterSpacing: '0.1em',
                      padding: '2px 6px',
                      borderRadius: 5,
                      border: `1px solid ${t.side === 'long' ? 'rgba(52,211,153,0.3)' : 'rgba(248,113,113,0.3)'}`,
                      background: t.side === 'long' ? 'rgba(52,211,153,0.10)' : 'rgba(248,113,113,0.10)',
                      color: t.side === 'long' ? '#34d399' : '#f87171',
                      textTransform: 'uppercase',
                    }}>
                      {t.side}
                    </span>
                  </td>
                  <td style={{ ...TD, color: 'rgba(255,255,255,0.6)' }}>{t.shares}</td>
                  <td style={{ ...TD, color: 'rgba(255,255,255,0.55)' }}>${t.entryPrice.toFixed(2)}</td>
                  <td style={{ ...TD, color: 'rgba(255,255,255,0.85)' }}>${t.exitPrice.toFixed(2)}</td>
                  <td style={{
                    ...TD,
                    color: isWin ? '#34d399' : '#f87171',
                    fontWeight: 600,
                    textShadow: `0 0 10px ${isWin ? 'rgba(52,211,153,0.3)' : 'rgba(248,113,113,0.3)'}`,
                  }}>
                    {isWin ? '+' : '-'}${Math.abs(t.pnl).toFixed(2)}
                  </td>
                  <td style={{
                    ...TD,
                    color: isWin ? '#34d399' : '#f87171',
                    fontSize: 10,
                  }}>
                    {t.returnPct >= 0 ? '+' : ''}{t.returnPct.toFixed(2)}%
                  </td>
                  <td style={TD}><ExitReasonBadge reason={t.exitReason} /></td>
                  <td style={{ ...TD, color: 'rgba(255,255,255,0.45)', fontSize: 10 }}>{t.barsHeld}d</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
