import { useRulesPipeline } from '@/hooks/useRulesPipeline'
import type { SymbolPipeline, RuleSignal } from '@/hooks/useRulesPipeline'
import { Panel } from '@/components/layout/Panel'
import { useNavigate } from 'react-router-dom'

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string; glow: string }> = {
  ready_to_buy:  { label: 'READY TO BUY',  color: 'var(--green)', bg: 'var(--green-dim)', glow: '0 0 12px rgba(93,255,182,0.3)' },
  ready_to_sell: { label: 'READY TO SELL', color: 'var(--red)',   bg: 'var(--red-dim)',   glow: '0 0 12px rgba(255,77,109,0.3)' },
  in_position:   { label: 'HOLDING',       color: 'var(--amber)', bg: 'var(--amber-dim)', glow: '0 0 12px rgba(255,107,61,0.2)' },
  watching:      { label: 'WATCHING',      color: 'var(--violet)', bg: 'var(--violet-dim)', glow: 'none' },
  no_signal:     { label: 'EXIT ONLY',     color: 'var(--text-muted)', bg: 'rgba(255,255,255,0.04)', glow: 'none' },
}

function ProgressBar({ met, total, color }: { met: number; total: number; color: string }) {
  const pct = total > 0 ? (met / total) * 100 : 0
  return (
    <div style={{ flex: 1, height: 4, background: 'rgba(255,255,255,0.06)', borderRadius: 99, overflow: 'hidden', minWidth: 40 }}>
      <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 99, transition: 'width 0.4s ease' }} />
    </div>
  )
}

function RuleRow({ signal }: { signal: RuleSignal }) {
  const color = signal.fired
    ? (signal.rule_type === 'entry' ? 'var(--green)' : 'var(--red)')
    : 'var(--text-muted)'

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
    }}>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 700,
        letterSpacing: '0.12em', textTransform: 'uppercase',
        color, minWidth: 36,
      }}>
        {signal.rule_type === 'entry' ? 'BUY' : 'SELL'}
      </span>
      <ProgressBar met={signal.conditions_met} total={signal.conditions_total} color={color} />
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)',
        minWidth: 30, textAlign: 'right',
      }}>
        {signal.conditions_met}/{signal.conditions_total}
      </span>
      {signal.fired && (
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 700,
          color: '#000', background: color, padding: '1px 5px', borderRadius: 4,
          animation: 'pulse-dot 2s ease-in-out infinite',
        }}>
          FIRE
        </span>
      )}
    </div>
  )
}

function SymbolCard({ item }: { item: SymbolPipeline }) {
  const cfg = STATUS_CONFIG[item.status] ?? STATUS_CONFIG.no_signal
  const allSignals = [...item.entry_rules, ...item.exit_rules]

  return (
    <div style={{
      padding: '12px 14px', borderRadius: 16,
      background: 'linear-gradient(135deg, rgba(255,255,255,0.03), transparent)',
      border: `1px solid var(--border)`,
      borderLeft: `3px solid ${cfg.color}`,
      transition: 'all 0.2s',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700,
          color: 'var(--amber)', letterSpacing: '0.04em',
        }}>
          {item.symbol}
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 700,
          letterSpacing: '0.12em', padding: '2px 7px', borderRadius: 999,
          color: cfg.color, background: cfg.bg,
          boxShadow: cfg.glow,
        }}>
          {cfg.label}
        </span>
      </div>

      {/* Signal rows */}
      {allSignals.map(s => (
        <RuleRow key={s.rule_id} signal={s} />
      ))}

      {/* Detail */}
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)',
        marginTop: 6, lineHeight: 1.4,
      }}>
        {item.status_detail}
      </div>
    </div>
  )
}

export function RulesPipelinePanel() {
  const { pipeline, totalRules, symbolsMonitored, isLoading } = useRulesPipeline()
  const navigate = useNavigate()

  // Sort: ready_to_buy and ready_to_sell first, then in_position, then watching
  const sortOrder: Record<string, number> = { ready_to_buy: 0, ready_to_sell: 1, in_position: 2, watching: 3, no_signal: 4 }
  const sorted = [...pipeline].sort((a, b) => (sortOrder[a.status] ?? 9) - (sortOrder[b.status] ?? 9))

  return (
    <Panel
      title="RULES PIPELINE"
      tag={totalRules > 0 ? `${symbolsMonitored} TICKERS` : 'NO RULES'}
      style={{ gridColumn: 1, gridRow: 2, display: 'flex', flexDirection: 'column', minHeight: 160 }}
    >
      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 14px' }}>
        {isLoading && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)', padding: 14, textAlign: 'center', letterSpacing: '0.1em' }}>
            LOADING...
          </div>
        )}

        {!isLoading && pipeline.length === 0 && (
          <div style={{ padding: '20px 10px', textAlign: 'center' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', marginBottom: 10, lineHeight: 1.6 }}>
              No active rules yet. Create rules to start monitoring tickers.
            </div>
            <button
              onClick={() => navigate('/rules')}
              style={{
                fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', fontWeight: 700,
                padding: '8px 18px', borderRadius: 999,
                background: 'linear-gradient(135deg, var(--amber), var(--amber-bright))',
                color: '#000', border: 'none', cursor: 'pointer',
                boxShadow: '0 2px 12px rgba(255,107,61,0.3)',
              }}
            >
              + CREATE RULE
            </button>
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {sorted.map(item => (
            <SymbolCard key={item.symbol} item={item} />
          ))}
        </div>
      </div>
    </Panel>
  )
}
