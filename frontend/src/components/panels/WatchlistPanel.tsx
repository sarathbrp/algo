import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useViewingUserId } from '@/store/sessionStore'
import { Panel } from '@/components/layout/Panel'

function formatTime(iso: string) {
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  return d.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function WatchlistPanel() {
  const userId = useViewingUserId()
  const queryClient = useQueryClient()
  const [newSymbol, setNewSymbol] = useState('')
  const [editing, setEditing] = useState(false)

  const { data: watchlistData } = useQuery({
    queryKey: ['watchlist', userId],
    queryFn: () => api.watchlist(userId!),
    enabled: !!userId,
    staleTime: 30_000,
  })

  const symbols = watchlistData?.symbols ?? []

  const { data: quotesData } = useQuery({
    queryKey: ['watchlist-quotes', userId, symbols],
    queryFn: () => api.quotes(userId!, symbols),
    enabled: !!userId && symbols.length > 0,
    staleTime: 10_000,
    refetchInterval: 15_000,
  })

  const quotes = quotesData?.quotes ?? []
  const quoteMap = Object.fromEntries(quotes.map((q) => [q.symbol, q]))

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
            flex: 1,
            padding: '6px 10px',
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
            background: 'var(--bg-panel-alt)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 10,
            color: 'var(--text-primary)',
            outline: 'none',
          }}
        />
        <button
          onClick={addSymbol}
          style={{
            padding: '6px 12px',
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            background: 'rgba(52,211,153,0.12)',
            border: '1px solid rgba(52,211,153,0.2)',
            borderRadius: 10,
            color: 'var(--green)',
            cursor: 'pointer',
          }}
        >
          ADD
        </button>
        <button
          onClick={() => setEditing(!editing)}
          style={{
            padding: '6px 10px',
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            background: editing ? 'rgba(248,113,113,0.12)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${editing ? 'rgba(248,113,113,0.2)' : 'rgba(255,255,255,0.08)'}`,
            borderRadius: 10,
            color: editing ? 'var(--red)' : 'var(--text-muted)',
            cursor: 'pointer',
          }}
        >
          {editing ? 'DONE' : 'EDIT'}
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
          return (
            <div
              key={sym}
              style={{
                padding: '7px 13px',
                borderBottom: i < symbols.length - 1 ? '1px solid var(--border)' : 'none',
                display: 'grid',
                gridTemplateColumns: editing ? 'auto 1fr auto auto' : '1fr auto auto',
                alignItems: 'center',
                gap: 8,
              }}
            >
              {editing && (
                <button
                  onClick={() => removeSymbol(sym)}
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: '50%',
                    border: '1px solid rgba(248,113,113,0.3)',
                    background: 'rgba(248,113,113,0.1)',
                    color: 'var(--red)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: 0,
                  }}
                >
                  ×
                </button>
              )}
              <div>
                <div style={{ fontFamily: 'var(--font-display)', fontSize: 14, letterSpacing: '-0.03em' }}>
                  {sym}
                </div>
                {q && (
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: 'var(--text-muted)', marginTop: 1 }}>
                    {formatTime(q.timestamp)} ET
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'right' }}>
                {q ? (
                  <div style={{ fontFamily: 'var(--font-display)', fontSize: 16, letterSpacing: '-0.04em' }}>
                    ${q.mid.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </div>
                ) : (
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                    --
                  </div>
                )}
              </div>
              {q && (
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 9,
                  color: 'var(--text-muted)',
                  textAlign: 'right',
                  minWidth: 55,
                }}>
                  <div>bid {q.bid.toFixed(2)}</div>
                  <div>ask {q.ask.toFixed(2)}</div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Feed status footer */}
      {quotesData && (
        <div style={{
          padding: '6px 13px',
          borderTop: '1px solid var(--border)',
          fontFamily: 'var(--font-mono)',
          fontSize: 9,
          color: 'var(--text-muted)',
          display: 'flex',
          justifyContent: 'space-between',
        }}>
          <span>Feed: {quotesData.feed_status ?? 'N/A'}</span>
          <span>{quotesData.feed_timestamp ? formatTime(quotesData.feed_timestamp) + ' ET' : ''}</span>
        </div>
      )}
    </Panel>
  )
}
