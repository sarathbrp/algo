import { useGateLog } from '@/hooks/useGateLog'
import { Panel } from '@/components/layout/Panel'

const EVENT_STYLES: Record<string, { badge: string; color: string; bg: string }> = {
  buy:        { badge: 'BUY',    color: 'var(--green)',           bg: 'rgba(52,211,153,0.12)' },
  sell:       { badge: 'SELL',   color: 'var(--red)',             bg: 'rgba(248,113,113,0.12)' },
  heartbeat:  { badge: 'SYNC',   color: 'rgba(147,197,253,0.9)', bg: 'rgba(96,165,250,0.10)' },
  regime:     { badge: 'MOOD',   color: 'var(--amber)',           bg: 'rgba(251,191,36,0.10)' },
  rule_entry: { badge: 'ENTRY',  color: 'var(--green)',           bg: 'rgba(52,211,153,0.12)' },
  rule_exit:  { badge: 'EXIT',   color: 'var(--red)',             bg: 'rgba(248,113,113,0.12)' },
  entry:      { badge: 'INFO',   color: 'var(--text-muted)',      bg: 'rgba(255,255,255,0.04)' },
}

export function GateLogPanel() {
  const { entries } = useGateLog(20)

  return (
    <Panel title="WORKER LOGS" tag={`${entries.length} EVENTS`} style={{ gridColumn: 3, gridRow: 2, display: 'flex', flexDirection: 'column', minHeight: 160 }}>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {entries.length === 0 && (
          <div style={{ padding: '14px 13px', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)' }}>
            No recent worker activity.
          </div>
        )}
        {entries.map((entry, i) => {
          const style = EVENT_STYLES[entry.type] ?? EVENT_STYLES.entry
          return (
            <div
              key={entry.id}
              style={{
                padding: '6px 13px',
                borderBottom: i < entries.length - 1 ? '1px solid var(--border)' : 'none',
                display: 'flex',
                gap: 8,
                alignItems: 'baseline',
                animation: i === 0 ? 'fade-in-down 0.4s ease' : 'none',
              }}
            >
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)', flexShrink: 0 }}>
                {entry.time}
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 9,
                fontWeight: 600,
                flexShrink: 0,
                padding: '1px 5px',
                borderRadius: 4,
                color: style.color,
                background: style.bg,
                letterSpacing: '0.04em',
              }}>
                {style.badge}
              </span>
              {entry.symbol && (
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--amber)', fontWeight: 500, flexShrink: 0 }}>
                  {entry.symbol}
                </span>
              )}
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.4 }}>
                {entry.message}
              </span>
            </div>
          )
        })}
      </div>
    </Panel>
  )
}
