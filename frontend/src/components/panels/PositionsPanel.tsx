import { useState } from 'react'
import { usePositions } from '@/hooks/usePositions'
import { Panel } from '@/components/layout/Panel'
import { api } from '@/lib/api'
import { useAuthStore } from '@/store/authStore'
import { useQueryClient } from '@tanstack/react-query'
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

function isMarketOpen(): boolean {
  const now = new Date()
  const est = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const day = est.getDay()
  const h = est.getHours()
  const m = est.getMinutes()
  const mins = h * 60 + m
  // Mon-Fri, 9:30am - 4:00pm ET
  return day >= 1 && day <= 5 && mins >= 570 && mins < 960
}

export function PositionsPanel() {
  const { positions, isLoading } = usePositions()
  const userId = useAuthStore(s => s.userId)
  const queryClient = useQueryClient()
  const [sellPanel, setSellPanel] = useState<string | null>(null)
  const [sellType, setSellType] = useState<'market' | 'limit'>('market')
  const [limitPrice, setLimitPrice] = useState('')
  const [sellQty, setSellQty] = useState('')
  const [selling, setSelling] = useState(false)
  const [sellResult, setSellResult] = useState<{ symbol: string; message: string; ok: boolean } | null>(null)
  const marketOpen = isMarketOpen()

  const [soldSymbols, setSoldSymbols] = useState<Set<string>>(new Set())

  function openSellPanel(symbol: string, currentPrice: number, shares: number) {
    setSellPanel(symbol)
    setSellType('market')
    setLimitPrice(currentPrice.toFixed(2))
    setSellQty(String(shares))
    setSellResult(null)
  }

  async function executeSell() {
    if (!userId || !sellPanel) return
    setSelling(true)
    try {
      const opts: { qty?: number; order_type?: 'market' | 'limit'; limit_price?: number; time_in_force?: string } = {
        order_type: sellType,
      }
      if (sellQty) opts.qty = parseInt(sellQty)
      if (sellType === 'limit') {
        const price = parseFloat(limitPrice)
        if (!price || price <= 0) { setSelling(false); return }
        opts.limit_price = price
        opts.time_in_force = 'gtc'
      }

      const result = await api.sellPosition(userId, sellPanel, opts)

      setSellResult({ symbol: sellPanel, message: result.message, ok: true })
      setSoldSymbols(prev => new Set(prev).add(sellPanel!))
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      queryClient.invalidateQueries({ queryKey: ['portfolio'] })
      setTimeout(() => { setSellPanel(null); setSellResult(null) }, 4000)
    } catch (e: any) {
      setSellResult({ symbol: sellPanel, message: `Failed: ${e?.response?.data?.detail || e?.message || 'unknown error'}`, ok: false })
    } finally {
      setSelling(false)
    }
  }
  // Filter out positions user explicitly sold in this session
  const visiblePositions = positions.filter(p => !soldSymbols.has(p.symbol))

  const exposure = visiblePositions.reduce((sum, pos) => sum + Math.abs(pos.currentPrice * pos.shares), 0)
  const largest = visiblePositions.reduce<Position | null>((current, pos) => {
    if (current === null) return pos
    return Math.abs(pos.currentPrice * pos.shares) > Math.abs(current.currentPrice * current.shares) ? pos : current
  }, null)
  const totalUnrealized = visiblePositions.reduce((sum, pos) => sum + pos.unrealizedPnl, 0)

  return (
    <Panel
      title="Open Positions"
      tag={visiblePositions.length === 0 ? 'Waiting' : `${visiblePositions.length} live`}
      accented
      style={{ gridColumn: 2, gridRow: 1, display: 'flex', flexDirection: 'column', minHeight: 300 }}
    >
      <div style={{ flex: 1, overflowX: 'auto' }}>
        {!isLoading && visiblePositions.length === 0 ? (
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
                {['Symbol', 'Side', 'Shares', 'Entry', 'Current', 'Unrealized', 'Return', 'Stop', ''].map((h, i) => (
                  <th key={h} style={{ ...TH, textAlign: i === 0 ? 'left' : 'right' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visiblePositions.map((pos) => (
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
                  <td style={TD}>${pos.currentPrice.toFixed(2)}</td>
                  <td style={{ ...TD, color: pos.unrealizedPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {pos.unrealizedPnl >= 0 ? '+' : '-'}${Math.abs(pos.unrealizedPnl).toFixed(2)}
                  </td>
                  <td style={{ ...TD, color: pos.returnPct >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {pos.returnPct >= 0 ? '+' : ''}{pos.returnPct.toFixed(2)}%
                  </td>
                  <td style={{ ...TD, color: 'var(--text-dim)' }}>
                    {pos.stopPct !== null ? `${pos.stopPct.toFixed(2)}%` : '—'}
                  </td>
                  <td style={{ ...TD, textAlign: 'center', padding: '6px 8px' }}>
                    {soldSymbols.has(pos.symbol) ? (
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700, color: 'var(--green)', letterSpacing: '0.1em' }}>
                        SOLD
                      </span>
                    ) : pos.pendingSell ? (
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 700, letterSpacing: '0.1em',
                        padding: '4px 10px', borderRadius: 999,
                        background: 'var(--amber-dim)', color: 'var(--amber)',
                        border: '1px solid rgba(255,107,61,0.3)',
                      }}>
                        SELL PENDING
                      </span>
                    ) : (
                      <button
                        onClick={() => openSellPanel(pos.symbol, pos.currentPrice, pos.shares)}
                        style={{
                          fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
                          letterSpacing: '0.1em', padding: '5px 12px', borderRadius: 999,
                          background: 'var(--red-dim)',
                          color: 'var(--red)',
                          border: '1px solid rgba(255,77,109,0.3)',
                          cursor: 'pointer', transition: 'all 0.15s',
                        }}
                        onMouseEnter={e => { e.currentTarget.style.background = 'linear-gradient(135deg, var(--red), rgba(255,77,109,0.8))'; e.currentTarget.style.color = '#fff' }}
                        onMouseLeave={e => { e.currentTarget.style.background = 'var(--red-dim)'; e.currentTarget.style.color = 'var(--red)' }}
                      >
                        SELL
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Sell panel */}
      {sellPanel && !sellResult && (
        <div style={{
          padding: '14px 16px', borderTop: '1px solid var(--border)',
          background: 'linear-gradient(135deg, var(--red-dim), transparent)',
          animation: 'fade-in-down 0.2s ease',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color: 'var(--amber)' }}>
              {sellPanel}
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)' }}>
              Sell {sellQty} shares
            </span>
            {!marketOpen && (
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 700, letterSpacing: '0.1em',
                padding: '2px 7px', borderRadius: 999, background: 'var(--amber-dim)', color: 'var(--amber)',
              }}>
                MARKET CLOSED
              </span>
            )}
            <button onClick={() => setSellPanel(null)} style={{
              marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)',
              background: 'none', border: 'none', cursor: 'pointer',
            }}>CANCEL</button>
          </div>

          {/* Order type toggle */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            {(['market', 'limit'] as const).map(t => (
              <button key={t} onClick={() => setSellType(t)} style={{
                flex: 1, fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
                letterSpacing: '0.08em', padding: '8px 14px', borderRadius: 10, textTransform: 'uppercase',
                background: sellType === t ? (t === 'market' ? 'var(--red-dim)' : 'var(--amber-dim)') : 'rgba(255,255,255,0.03)',
                color: sellType === t ? (t === 'market' ? 'var(--red)' : 'var(--amber)') : 'var(--text-muted)',
                border: `1px solid ${sellType === t ? (t === 'market' ? 'rgba(255,77,109,0.3)' : 'rgba(255,107,61,0.3)') : 'var(--border)'}`,
                cursor: 'pointer', transition: 'all 0.15s',
              }}>
                {t === 'market' ? 'Market Sell' : 'Limit Sell'}
              </button>
            ))}
          </div>

          {/* Description */}
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)', marginBottom: 10, lineHeight: 1.5 }}>
            {sellType === 'market'
              ? (marketOpen
                ? 'Sells immediately at the current market price.'
                : 'Market is closed. Order will queue and execute at market open (9:30 AM ET) at the opening price — price may gap overnight.')
              : 'Sells only at your specified price or higher. If the price never reaches your limit, the order stays open.'}
          </div>

          {/* Limit price input */}
          {sellType === 'limit' && (
            <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)' }}>Minimum price $</span>
              <input type="number" step="0.01" value={limitPrice}
                onChange={e => setLimitPrice(e.target.value)}
                style={{
                  width: 100, fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700,
                  background: 'var(--bg-panel-alt)', border: '1px solid var(--border)', borderRadius: 8,
                  padding: '6px 10px', color: 'var(--amber)', textAlign: 'center', outline: 'none',
                }}
              />
            </div>
          )}

          {/* Confirm button */}
          <button
            onClick={executeSell}
            disabled={selling}
            style={{
              fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, letterSpacing: '0.06em',
              padding: '10px 24px', borderRadius: 999, width: '100%',
              background: selling ? 'var(--text-muted)' : 'linear-gradient(135deg, var(--red), rgba(255,77,109,0.8))',
              color: '#fff', border: 'none', cursor: selling ? 'wait' : 'pointer',
              boxShadow: selling ? 'none' : '0 2px 12px rgba(255,77,109,0.3)',
              opacity: selling ? 0.5 : 1,
            }}
          >
            {selling ? 'SELLING...' : sellType === 'market'
              ? (marketOpen ? `SELL ${sellPanel} AT MARKET` : `SELL ${sellPanel} AT OPEN`)
              : `SELL ${sellPanel} AT $${limitPrice}`}
          </button>
        </div>
      )}

      {/* Sell result notification */}
      {sellResult && (
        <div style={{
          padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 11,
          color: sellResult.ok ? 'var(--green)' : 'var(--red)',
          background: sellResult.ok ? 'var(--green-dim)' : 'var(--red-dim)',
          borderTop: '1px solid var(--border)',
          animation: 'fade-in-down 0.3s ease',
        }}>
          {sellResult.message}
        </div>
      )}

      <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', display: 'flex', gap: 18, alignItems: 'center' }}>
        {!marketOpen && visiblePositions.length > 0 && (
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 700, letterSpacing: '0.1em',
            padding: '3px 8px', borderRadius: 999,
            background: 'var(--amber-dim)', color: 'var(--amber)', border: '1px solid rgba(255,107,61,0.3)',
          }}>
            MARKET CLOSED
          </div>
        )}
        {[
          ['Exposure', `$${exposure.toFixed(2)}`, 'var(--amber)'],
          ['Largest', largest ? `${largest.symbol} · $${Math.abs(largest.currentPrice * largest.shares).toFixed(2)}` : '—', 'var(--text-dim)'],
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
