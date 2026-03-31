import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import type { TradeOut } from '@/lib/api'
import { useAuthStore } from '@/store/authStore'

const EXIT_REASON_LABELS: Record<string, string> = {
  stop_loss:          'Stop Loss',
  trailing_stop:      'Trailing Stop',
  partial_take_profit:'Partial TP',
  time_bars:          'Time Exit',
  kill_switch:        'Kill Switch',
  news_sentiment:     'News Sell',
  manual:             'Manual',
}

function fmt(val: number | null, decimals = 2, prefix = '') {
  if (val === null || val === undefined) return '—'
  return `${prefix}${val.toFixed(decimals)}`
}

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + '  ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
}

function pnlColor(pnl: number | null) {
  if (pnl === null) return 'var(--text-muted)'
  return pnl >= 0 ? 'var(--green, #00c864)' : 'var(--red, #ff3b5c)'
}

export function Trades() {
  const userId = useAuthStore((s) => s.userId)
  const [trades, setTrades] = useState<TradeOut[]>([])
  const [limit, setLimit] = useState(50)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!userId) return
    setLoading(true)
    api.trades(userId, limit)
      .then(setTrades)
      .catch(() => setError('Failed to load trades.'))
      .finally(() => setLoading(false))
  }, [userId, limit])

  const totalPnl = trades.reduce((sum, t) => sum + (t.pnl ?? 0), 0)
  const winners = trades.filter(t => (t.pnl ?? 0) > 0).length
  const losers  = trades.filter(t => (t.pnl ?? 0) < 0).length
  const winRate = trades.length > 0 ? (winners / trades.length * 100).toFixed(0) : '—'

  return (
    <div style={{ padding: '24px 20px', maxWidth: 1100, margin: '0 auto' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: 20, letterSpacing: '0.12em', color: 'var(--amber)' }}>
            TRADE HISTORY
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.18em', color: 'var(--text-muted)', marginTop: 3, textTransform: 'uppercase' }}>
            Closed positions · entry / exit · P&L
          </div>
        </div>
        <select
          value={limit}
          onChange={e => setLimit(Number(e.target.value))}
          style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.08em',
            background: 'var(--bg-panel-alt)', border: '1px solid var(--border)',
            color: 'var(--text-muted)', padding: '5px 10px', cursor: 'pointer',
          }}
        >
          {[25, 50, 100, 250, 500].map(n => (
            <option key={n} value={n}>Last {n}</option>
          ))}
        </select>
      </div>

      {/* Summary bar */}
      {!loading && trades.length > 0 && (
        <div style={{
          display: 'flex', gap: 24, marginBottom: 20,
          padding: '12px 16px', background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
        }}>
          {[
            { label: 'TRADES', value: trades.length },
            { label: 'WIN RATE', value: `${winRate}%` },
            { label: 'WINNERS', value: winners, color: 'var(--green, #00c864)' },
            { label: 'LOSERS',  value: losers,  color: 'var(--red, #ff3b5c)' },
            { label: 'TOTAL P&L', value: `$${totalPnl.toFixed(2)}`, color: pnlColor(totalPnl) },
          ].map(({ label, value, color }) => (
            <div key={label}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.18em', color: 'var(--text-muted)', marginBottom: 3, textTransform: 'uppercase' }}>{label}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: color ?? 'var(--text-primary)', letterSpacing: '0.06em' }}>{value}</div>
            </div>
          ))}
        </div>
      )}

      {/* States */}
      {loading && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', letterSpacing: '0.1em', padding: 40, textAlign: 'center' }}>
          LOADING...
        </div>
      )}
      {error && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--red, #ff3b5c)', padding: '10px 14px', border: '1px solid rgba(255,59,92,0.25)', background: 'rgba(255,59,92,0.05)' }}>
          {error}
        </div>
      )}
      {!loading && !error && trades.length === 0 && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', letterSpacing: '0.1em', padding: 40, textAlign: 'center' }}>
          NO CLOSED TRADES YET
        </div>
      )}

      {/* Table */}
      {!loading && trades.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['SYMBOL', 'SIDE', 'QTY', 'ENTRY $', 'EXIT $', 'P&L $', 'P&L %', 'EXIT REASON', 'MODE', 'ENTERED', 'EXITED'].map(h => (
                  <th key={h} style={{
                    padding: '8px 10px', textAlign: 'left', fontSize: 8,
                    letterSpacing: '0.18em', color: 'var(--text-muted)',
                    fontWeight: 'normal', whiteSpace: 'nowrap',
                  }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr
                  key={t.id}
                  style={{
                    borderBottom: '1px solid rgba(255,255,255,0.04)',
                    background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)',
                  }}
                >
                  <td style={{ padding: '9px 10px', color: 'var(--amber)', letterSpacing: '0.06em', fontWeight: 'bold' }}>
                    {t.symbol}
                  </td>
                  <td style={{ padding: '9px 10px', color: t.side === 'long' ? 'var(--green, #00c864)' : 'var(--red, #ff3b5c)', textTransform: 'uppercase', fontSize: 10 }}>
                    {t.side}
                  </td>
                  <td style={{ padding: '9px 10px', color: 'var(--text-primary)' }}>
                    {t.qty}
                  </td>
                  <td style={{ padding: '9px 10px', color: 'var(--text-primary)' }}>
                    {fmt(t.entry_price, 2, '$')}
                  </td>
                  <td style={{ padding: '9px 10px', color: 'var(--text-primary)' }}>
                    {fmt(t.exit_price, 2, '$')}
                  </td>
                  <td style={{ padding: '9px 10px', color: pnlColor(t.pnl), fontWeight: 'bold' }}>
                    {t.pnl !== null ? `${t.pnl >= 0 ? '+' : ''}$${t.pnl.toFixed(2)}` : '—'}
                  </td>
                  <td style={{ padding: '9px 10px', color: pnlColor(t.pnl_pct) }}>
                    {t.pnl_pct !== null ? `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%` : '—'}
                  </td>
                  <td style={{ padding: '9px 10px', color: 'var(--text-muted)', fontSize: 9, letterSpacing: '0.06em' }}>
                    {t.exit_reason ? (EXIT_REASON_LABELS[t.exit_reason] ?? t.exit_reason) : '—'}
                  </td>
                  <td style={{ padding: '9px 10px', color: 'var(--text-muted)', fontSize: 9, textTransform: 'uppercase' }}>
                    {t.mode ?? '—'}
                  </td>
                  <td style={{ padding: '9px 10px', color: 'var(--text-dim)', fontSize: 9, whiteSpace: 'nowrap' }}>
                    {fmtDate(t.entered_at)}
                  </td>
                  <td style={{ padding: '9px 10px', color: 'var(--text-dim)', fontSize: 9, whiteSpace: 'nowrap' }}>
                    {fmtDate(t.exited_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
