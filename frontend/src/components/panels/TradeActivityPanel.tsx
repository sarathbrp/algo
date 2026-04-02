import { useTrades } from '@/hooks/useTrades'
import { useRulesPipeline } from '@/hooks/useRulesPipeline'
import { Panel } from '@/components/layout/Panel'
import type { Trade } from '@/types'
import type { SymbolPipeline } from '@/hooks/useRulesPipeline'

// ---------------------------------------------------------------------------
// Activity item types
// ---------------------------------------------------------------------------

interface ActivityItem {
  id: string
  time: string
  type: 'signal' | 'buy' | 'sell' | 'sell_pending' | 'stop' | 'trail' | 'profit' | 'pending'
  symbol: string
  message: string
  detail?: string
  pnl?: number
  color: string
  badgeLabel: string
  badgeBg: string
}

const BADGE_STYLES: Record<string, { label: string; color: string; bg: string }> = {
  signal:       { label: 'SIGNAL',       color: 'var(--green)',  bg: 'rgba(93,255,182,0.12)' },
  buy:          { label: 'BUY',          color: 'var(--green)',  bg: 'rgba(93,255,182,0.12)' },
  sell:         { label: 'SELL',         color: 'var(--red)',    bg: 'rgba(255,77,109,0.12)' },
  sell_pending: { label: 'SELL QUEUED',  color: 'var(--amber)',  bg: 'var(--amber-dim)' },
  stop:         { label: 'STOP',         color: '#f87171',       bg: 'rgba(248,113,113,0.12)' },
  trail:        { label: 'TRAIL',        color: '#93c5fd',       bg: 'rgba(147,197,253,0.10)' },
  profit:       { label: 'PROFIT',       color: '#34d399',       bg: 'rgba(52,211,153,0.12)' },
  pending:      { label: 'WATCHING',     color: 'var(--violet)', bg: 'var(--violet-dim)' },
}

function exitReasonToType(reason: string): ActivityItem['type'] {
  if (reason === 'stop-loss' || reason === 'stop_loss') return 'stop'
  if (reason === 'trail-stop' || reason === 'trail_stop' || reason === 'trailing_stop') return 'trail'
  if (reason === 'profit-target' || reason === 'take_profit' || reason === 'partial_take_profit') return 'profit'
  if (reason === 'manual_pending') return 'sell_pending'
  return 'sell'
}

// ---------------------------------------------------------------------------
// Build activity items from trades + pipeline
// ---------------------------------------------------------------------------

function buildActivityItems(trades: Trade[], pipeline: SymbolPipeline[]): ActivityItem[] {
  const items: ActivityItem[] = []

  // Pipeline signals (upcoming actions)
  for (const p of pipeline) {
    if (p.status === 'ready_to_buy') {
      const entry = p.entry_rules.find(r => r.fired)
      items.push({
        id: `signal-buy-${p.symbol}`,
        time: 'NOW',
        type: 'signal',
        symbol: p.symbol,
        message: `Entry signal active`,
        detail: entry?.actions_summary || 'Buy conditions met',
        color: 'var(--green)',
        badgeLabel: BADGE_STYLES.signal.label,
        badgeBg: BADGE_STYLES.signal.bg,
      })
    } else if (p.status === 'ready_to_sell') {
      const exit = p.exit_rules.find(r => r.fired)
      items.push({
        id: `signal-sell-${p.symbol}`,
        time: 'NOW',
        type: 'sell',
        symbol: p.symbol,
        message: `Exit signal active`,
        detail: exit?.actions_summary || 'Sell conditions met',
        color: 'var(--red)',
        badgeLabel: BADGE_STYLES.sell.label,
        badgeBg: BADGE_STYLES.sell.bg,
      })
    } else if (p.status === 'watching') {
      const entry = p.entry_rules[0]
      if (entry) {
        items.push({
          id: `watch-${p.symbol}`,
          time: '',
          type: 'pending',
          symbol: p.symbol,
          message: `${entry.conditions_met}/${entry.conditions_total} conditions met`,
          detail: entry.actions_summary,
          color: 'var(--violet)',
          badgeLabel: BADGE_STYLES.pending.label,
          badgeBg: BADGE_STYLES.pending.bg,
        })
      }
    } else if (p.status === 'in_position') {
      const exit = p.exit_rules[0]
      items.push({
        id: `hold-${p.symbol}`,
        time: '',
        type: 'pending',
        symbol: p.symbol,
        message: exit ? `${exit.conditions_met}/${exit.conditions_total} exit conditions` : 'Holding',
        detail: p.status_detail,
        color: 'var(--amber)',
        badgeLabel: 'HOLDING',
        badgeBg: 'var(--amber-dim)',
      })
    }
  }

  // Closed trades (today's activity)
  for (const t of trades) {
    const type = exitReasonToType(t.exitReason)
    const badge = BADGE_STYLES[type] || BADGE_STYLES.sell
    items.push({
      id: `trade-${t.time}-${t.symbol}`,
      time: t.time,
      type,
      symbol: t.symbol,
      message: `${t.side === 'long' ? 'Bought' : 'Shorted'} ${t.shares} @ $${t.entryPrice.toFixed(2)} → $${t.exitPrice.toFixed(2)}`,
      pnl: t.pnl,
      color: badge.color,
      badgeLabel: badge.label,
      badgeBg: badge.bg,
    })
  }

  return items
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TradeActivityPanel() {
  const { trades } = useTrades()
  const { pipeline, totalRules } = useRulesPipeline()

  const items = buildActivityItems(trades, pipeline)
  const pipelineItems = items.filter(i => i.type === 'signal' || i.type === 'pending' || i.id.startsWith('hold-'))
  const tradeItems = items.filter(i => i.type !== 'signal' && i.type !== 'pending' && !i.id.startsWith('hold-'))

  const wins = trades.filter(t => t.pnl > 0).length
  const losses = trades.filter(t => t.pnl < 0).length
  const tagParts: string[] = []
  if (totalRules > 0) tagParts.push(`${pipeline.length} TICKERS`)
  if (trades.length > 0) tagParts.push(`${wins}W ${losses}L`)
  const tagText = tagParts.length > 0 ? tagParts.join(' · ') : 'NO ACTIVITY'

  return (
    <Panel
      title="TRADE ACTIVITY"
      tag={tagText}
      accented
      style={{ gridColumn: 2, gridRow: 2, minHeight: 160 }}
    >
      <div style={{ overflowY: 'auto', maxHeight: 360 }}>

        {/* Pipeline section */}
        {pipelineItems.length > 0 && (
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)' }}>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.18em',
              color: 'var(--violet)', marginBottom: 8, fontWeight: 700,
            }}>
              PIPELINE
            </div>
            {pipelineItems.map(item => (
              <div key={item.id} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0',
                borderBottom: '1px solid rgba(255,255,255,0.03)',
              }}>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 700,
                  letterSpacing: '0.08em', padding: '2px 6px', borderRadius: 5,
                  color: item.color, background: item.badgeBg, whiteSpace: 'nowrap',
                }}>
                  {item.badgeLabel}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, color: 'var(--amber)', flexShrink: 0 }}>
                  {item.symbol}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {item.message}
                </span>
                {item.time && (
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
                    color: item.type === 'signal' ? 'var(--green)' : 'var(--red)',
                    animation: 'pulse-dot 2s ease-in-out infinite',
                  }}>
                    {item.time}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Closed trades section */}
        <div style={{ padding: '10px 14px' }}>
          {pipelineItems.length > 0 && tradeItems.length > 0 && (
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.18em',
              color: 'var(--text-muted)', marginBottom: 8, fontWeight: 700,
            }}>
              COMPLETED
            </div>
          )}
          {items.length === 0 && (
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)',
              padding: '18px 0', textAlign: 'center', lineHeight: 1.6,
            }}>
              No trade activity yet. Create rules to start monitoring tickers.
            </div>
          )}
          {tradeItems.map(item => (
            <div key={item.id} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0',
              borderBottom: '1px solid rgba(255,255,255,0.03)',
            }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)', flexShrink: 0, minWidth: 55 }}>
                {item.time}
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 700,
                letterSpacing: '0.08em', padding: '2px 6px', borderRadius: 5,
                color: item.color, background: item.badgeBg, whiteSpace: 'nowrap',
              }}>
                {item.badgeLabel}
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, color: 'var(--amber)', flexShrink: 0 }}>
                {item.symbol}
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {item.message}
              </span>
              {item.pnl !== undefined && (
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
                  color: item.pnl >= 0 ? 'var(--green)' : 'var(--red)',
                  flexShrink: 0,
                }}>
                  {item.pnl >= 0 ? '+' : ''}{item.pnl.toFixed(2)}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </Panel>
  )
}
