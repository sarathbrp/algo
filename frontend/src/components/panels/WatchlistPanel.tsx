import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useViewingUserId } from '@/store/sessionStore'
import { Panel } from '@/components/layout/Panel'

export function WatchlistPanel() {
  const userId = useViewingUserId()
  const queryClient = useQueryClient()
  const [newSymbol, setNewSymbol] = useState('')

  const { data: watchlistData } = useQuery({
    queryKey: ['watchlist', userId],
    queryFn: () => api.watchlist(userId!),
    enabled: !!userId,
    staleTime: 30_000,
  })

  const symbols = watchlistData?.symbols ?? []

  const REFRESH_INTERVAL = 15_000

  const { data: quotesData, dataUpdatedAt } = useQuery({
    queryKey: ['watchlist-quotes', userId, symbols],
    queryFn: () => api.quotes(userId!, symbols),
    enabled: !!userId && symbols.length > 0,
    staleTime: 10_000,
    refetchInterval: REFRESH_INTERVAL,
  })

  const quotes = quotesData?.quotes ?? []
  const quoteMap = Object.fromEntries(quotes.map((q) => [q.symbol, q]))

  // Countdown timer
  const [countdown, setCountdown] = useState(REFRESH_INTERVAL / 1000)
  useEffect(() => {
    setCountdown(REFRESH_INTERVAL / 1000)
  }, [dataUpdatedAt])
  useEffect(() => {
    const id = setInterval(() => setCountdown(prev => Math.max(0, prev - 1)), 1000)
    return () => clearInterval(id)
  }, [])

  const updateMutation = useMutation({
    mutationFn: (newSymbols: string[]) => api.updateWatchlist(userId!, newSymbols),
    onSuccess: (data) => {
      queryClient.setQueryData(['watchlist', userId], data)
    },
  })

  const addSymbol = () => {
    const sym = newSymbol.trim().toUpperCase()
    if (!sym || symbols.includes(sym)) {
      setNewSymbol('')
      return
    }
    updateMutation.mutate([...symbols, sym])
    setNewSymbol('')
  }

  const removeSymbol = (sym: string) => {
    updateMutation.mutate(symbols.filter((s) => s !== sym))
  }

  return (
    <Panel title="WATCHLIST" tag={`${symbols.length} TICKERS`} style={{ gridColumn: 3, gridRow: 1, display: 'flex', flexDirection: 'column' }}>
      {/* Add ticker input */}
      <div style={{ padding: '10px 13px 6px', display: 'flex', gap: 6 }}>
        <input
          type="text"
          value={newSymbol}
          onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === 'Enter' && addSymbol()}
          placeholder="Add ticker..."
          style={{
            flex: 1, padding: '6px 10px', fontFamily: 'var(--font-mono)', fontSize: 11,
            background: 'var(--bg-panel-alt)', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 10, color: 'var(--text-primary)', outline: 'none',
          }}
        />
        <button
          onClick={addSymbol}
          style={{
            padding: '6px 12px', fontFamily: 'var(--font-mono)', fontSize: 10,
            background: 'rgba(52,211,153,0.12)', border: '1px solid rgba(52,211,153,0.2)',
            borderRadius: 10, color: 'var(--green)', cursor: 'pointer',
          }}
        >
          ADD
        </button>
      </div>

      {/* Ticker list */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {symbols.length === 0 && (
          <div style={{ padding: '14px 13px', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)' }}>
            Add tickers above to build your watchlist.
          </div>
        )}
        {symbols.map((sym, i) => {
          const q = quoteMap[sym]
          const changePct = q?.change_pct ?? null
          const changeColor = changePct === null ? 'var(--text-muted)'
            : changePct >= 0 ? 'var(--green)' : 'var(--red)'

          return (
            <div
              key={sym}
              style={{
                padding: '7px 13px',
                borderBottom: i < symbols.length - 1 ? '1px solid var(--border)' : 'none',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              {/* Symbol */}
              <div style={{ minWidth: 50 }}>
                <div style={{ fontFamily: 'var(--font-display)', fontSize: 14, letterSpacing: '-0.03em' }}>
                  {sym}
                </div>
              </div>

              {/* Price + Prev Close + Change */}
              {q ? (
                <>
                  <div style={{ flex: 1, textAlign: 'right' }}>
                    <div style={{
                      fontFamily: 'var(--font-display)', fontSize: 15, letterSpacing: '-0.04em',
                      opacity: q.stale ? 0.6 : 1,
                    }}>
                      ${q.mid.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                    {q.prev_close != null && (
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: 'var(--text-muted)', marginTop: 1 }}>
                        prev ${q.prev_close.toFixed(2)}
                      </div>
                    )}
                  </div>
                  <div style={{ minWidth: 75, textAlign: 'right' }}>
                    {changePct !== null && q.prev_close != null ? (
                      <div>
                        <div style={{
                          fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700,
                          color: changeColor,
                        }}>
                          {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
                        </div>
                        <div style={{
                          fontFamily: 'var(--font-mono)', fontSize: 9,
                          color: changeColor, marginTop: 1,
                        }}>
                          {(q.mid - q.prev_close) >= 0 ? '+' : ''}${(q.mid - q.prev_close).toFixed(2)}
                        </div>
                      </div>
                    ) : (
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)' }}>--</span>
                    )}
                  </div>
                </>
              ) : (
                <div style={{ flex: 1, textAlign: 'right' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)', fontStyle: 'italic' }}>
                    Not available on Alpaca
                  </span>
                </div>
              )}

              {/* Remove button */}
              <button
                onClick={() => removeSymbol(sym)}
                title={`Remove ${sym}`}
                style={{
                  width: 20, height: 20, borderRadius: 6, padding: 0,
                  border: '1px solid transparent',
                  background: 'transparent',
                  color: 'var(--text-muted)',
                  fontFamily: 'var(--font-mono)', fontSize: 13,
                  cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  transition: 'all 0.15s', flexShrink: 0,
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.background = 'var(--red-dim)'
                  e.currentTarget.style.color = 'var(--red)'
                  e.currentTarget.style.borderColor = 'rgba(255,77,109,0.3)'
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = 'transparent'
                  e.currentTarget.style.color = 'var(--text-muted)'
                  e.currentTarget.style.borderColor = 'transparent'
                }}
              >
                x
              </button>
            </div>
          )
        })}
      </div>

      {/* Footer with countdown */}
      <div style={{
        padding: '6px 13px', borderTop: '1px solid var(--border)',
        fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <span>Feed: {quotesData?.feed_status ?? (symbols.length > 0 ? 'loading' : 'idle')}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{
            width: 30, height: 3, borderRadius: 99, background: 'rgba(255,255,255,0.06)', overflow: 'hidden',
          }}>
            <div style={{
              height: '100%', borderRadius: 99,
              width: `${(countdown / (REFRESH_INTERVAL / 1000)) * 100}%`,
              background: countdown <= 3 ? 'var(--green)' : 'var(--text-muted)',
              transition: 'width 1s linear',
            }} />
          </div>
          <span style={{ minWidth: 20, textAlign: 'right' }}>{countdown}s</span>
        </div>
      </div>
    </Panel>
  )
}
