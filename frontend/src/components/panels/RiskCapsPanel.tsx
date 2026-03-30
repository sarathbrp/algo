import { Panel } from '@/components/layout/Panel'
import { useBotStatus } from '@/hooks/useBotStatus'
import { usePortfolio } from '@/hooks/usePortfolio'
import { usePositions } from '@/hooks/usePositions'

interface Gauge {
  label: string
  current: number | string
  max: number | string
  pct: number
  level: 'safe' | 'warn' | 'danger'
}

const LEVEL_COLORS: Record<string, string> = {
  safe:   'var(--green)',
  warn:   'var(--amber)',
  danger: 'var(--red)',
}

function RiskGauge({ label, current, max, pct, level }: Gauge) {
  const color = LEVEL_COLORS[level]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
          {label}
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 500, color }}>
          {current} / {max}
        </span>
      </div>
      <div style={{ height: 3, background: 'var(--border)', position: 'relative', overflow: 'hidden' }}>
        <div style={{
          position: 'absolute',
          top: 0, left: 0,
          height: '100%',
          width: `${pct}%`,
          background: color,
          transition: 'width 0.8s cubic-bezier(0.4,0,0.2,1)',
        }} />
      </div>
    </div>
  )
}

export function RiskCapsPanel() {
  const { status, maxPositions } = useBotStatus()
  const { positions } = usePositions()
  const { stats } = usePortfolio()
  const positionCount = positions.length
  const exposure = positions.reduce((sum, pos) => sum + Math.abs(pos.currentPrice * pos.shares), 0)
  const exposurePct = stats.equity > 0 ? (exposure / stats.equity) * 100 : 0
  const dailyLossPct = stats.equity > 0 && stats.dayPnl < 0 ? Math.abs(stats.dayPnl / stats.equity) * 100 : 0
  const gauges: Gauge[] = [
    {
      label: 'POSITION COUNT',
      current: positionCount,
      max: maxPositions ?? 'UNSET',
      pct: maxPositions && maxPositions > 0 ? Math.min(100, (positionCount / maxPositions) * 100) : 0,
      level: maxPositions && maxPositions > 0 && positionCount >= maxPositions ? 'danger' : positionCount > 0 ? 'warn' : 'safe',
    },
    {
      label: 'OPEN EXPOSURE',
      current: `$${exposure.toFixed(0)}`,
      max: `${exposurePct.toFixed(1)}% eq`,
      pct: Math.min(100, exposurePct),
      level: exposurePct >= 75 ? 'danger' : exposurePct >= 40 ? 'warn' : 'safe',
    },
    {
      label: 'DAY P&L',
      current: `${stats.dayPnl >= 0 ? '+' : '-'}$${Math.abs(stats.dayPnl).toFixed(0)}`,
      max: `${dailyLossPct.toFixed(2)}% loss`,
      pct: Math.min(100, dailyLossPct * 10),
      level: stats.dayPnl < 0 && dailyLossPct >= 2 ? 'danger' : stats.dayPnl < 0 ? 'warn' : 'safe',
    },
    {
      label: 'BOT STATE',
      current: status.toUpperCase(),
      max: 'ACCOUNT',
      pct: status === 'running' ? 100 : status === 'paused' ? 60 : 20,
      level: status === 'running' ? 'safe' : status === 'paused' ? 'warn' : 'danger',
    },
  ]

  return (
    <Panel title="Risk Snapshot" tag="Live" style={{ gridColumn: 1, gridRow: 2 }}>
      <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 11 }}>
        {gauges.map((g) => <RiskGauge key={g.label} {...g} />)}

        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '7px 11px',
          background: status === 'running' ? 'var(--green-dim)' : status === 'paused' ? 'var(--amber-dim)' : 'var(--red-dim)',
          border: `1px solid ${status === 'running' ? 'rgba(0,212,139,0.2)' : status === 'paused' ? 'rgba(255,179,0,0.2)' : 'rgba(255,59,92,0.2)'}`,
          marginTop: 2,
        }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: status === 'running' ? 'var(--green)' : status === 'paused' ? 'var(--amber)' : 'var(--red)', flexShrink: 0 }} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: status === 'running' ? 'var(--green)' : status === 'paused' ? 'var(--amber)' : 'var(--red)' }}>
            {status === 'running' ? 'AUTOMATION ACTIVE' : status === 'paused' ? 'NEW TRADES PAUSED' : 'AUTOMATION STOPPED'}
          </span>
        </div>
      </div>
    </Panel>
  )
}
