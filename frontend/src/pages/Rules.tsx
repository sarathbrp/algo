import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '@/lib/api'
import type {
  RuleOut, RuleCatalog, RuleTree, RuleGroup, RuleCondition, RuleAction,
  IndicatorMeta,
} from '@/lib/api'
import { useAuthStore } from '@/store/authStore'
import { Panel } from '@/components/layout/Panel'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CATEGORY_COLORS: Record<string, string> = {
  price:      'var(--amber)',
  trend:      '#6b8aff',
  momentum:   '#c77dff',
  volatility: '#ff5c8a',
  volume:     '#4ecdc4',
}

const CATEGORY_LABELS: Record<string, string> = {
  price:      'PRICE',
  trend:      'TREND',
  momentum:   'MOMENTUM',
  volatility: 'VOLATILITY',
  volume:     'VOLUME',
}

function indicatorLabel(ind: IndicatorMeta, params: Record<string, any>): string {
  const period = params.period ?? ind.params.find(p => p.name === 'period')?.default
  const field = params.field ?? ind.params.find(p => p.name === 'field')?.default
  if (ind.id === 'price') return `Price(${field ?? 'close'})`
  if (period) return `${ind.label}(${period})`
  return ind.label
}

function valueLabel(val: any, catalog: RuleCatalog | null): string {
  if (val === null || val === undefined) return '?'
  if (typeof val === 'number') return String(val)
  if (Array.isArray(val)) return `${val[0]} and ${val[1]}`
  if (typeof val === 'object' && val.indicator) {
    const meta = catalog?.indicators.find(i => i.id === val.indicator)
    return meta ? indicatorLabel(meta, val.params ?? {}) : val.indicator
  }
  return String(val)
}

function generateExplanation(
  ruleType: 'entry' | 'exit',
  groups: RuleGroup[],
  actions: RuleAction[],
  catalog: RuleCatalog | null,
): { summary: string; details: string[] } {
  if (!catalog || groups.length === 0) return { summary: '', details: [] }

  const condParts: string[] = []
  const details: string[] = []

  for (const group of groups) {
    const parts: string[] = []
    for (const cond of group.conditions) {
      const indMeta = catalog.indicators.find(i => i.id === cond.indicator)
      const compMeta = catalog.comparators.find(c => c.id === cond.comparator)
      if (!indMeta || !compMeta) continue
      const indStr = indicatorLabel(indMeta, cond.params)
      const valStr = valueLabel(cond.value, catalog)
      parts.push(`${indStr} ${compMeta.label} ${valStr}`)

      if (cond.indicator === 'rsi') {
        const v = typeof cond.value === 'number' ? cond.value : null
        if (v !== null) {
          if (cond.comparator === 'is_below' && v <= 35)
            details.push(`RSI below ${v} means the stock may be oversold — a potential buying opportunity`)
          else if (cond.comparator === 'is_above' && v >= 65)
            details.push(`RSI above ${v} means the stock may be overbought — momentum could reverse`)
          else details.push(`RSI measures momentum on a 0-100 scale`)
        }
      } else if (cond.indicator === 'ema' || cond.indicator === 'sma') {
        const label = cond.indicator.toUpperCase()
        if (cond.comparator.includes('cross') && typeof cond.value === 'object')
          details.push(`${label} crossover detects when short-term momentum shifts direction`)
        else details.push(`${label} smooths price data to reveal the underlying trend`)
      } else if (cond.indicator === 'atr' || cond.indicator === 'atr_pct') {
        details.push(`ATR measures volatility — higher values mean bigger price swings`)
      } else if (cond.indicator === 'vwap') {
        details.push(`VWAP is the average price weighted by volume — institutional traders use it as fair value`)
      } else if (cond.indicator.includes('volume')) {
        details.push(`Volume confirms the strength of a price move`)
      }
    }
    condParts.push(parts.join(` ${group.logic} `))
  }

  const actionParts: string[] = []
  for (const act of actions) {
    const meta = catalog.actions.find(a => a.id === act.action)
    if (!meta) continue
    if (act.params.pct) {
      actionParts.push(`${meta.label} at ${act.params.pct}%`)
      if (act.action === 'set_stop_loss') details.push(`The ${act.params.pct}% stop loss caps your maximum loss per trade`)
      if (act.action === 'set_take_profit') details.push(`Locks in profits when the position gains ${act.params.pct}%`)
      if (act.action === 'set_trailing_stop') details.push(`The trailing stop follows price up and exits if it drops ${act.params.pct}% from peak`)
    } else {
      actionParts.push(meta.label)
    }
  }

  const verb = ruleType === 'entry' ? 'Buy' : 'Sell'
  const condStr = condParts.join(' AND ')
  const actStr = actionParts.length > 0 ? ` then ${actionParts.join(', ')}` : ''
  const summary = condStr ? `${verb} when ${condStr}${actStr}` : ''
  return { summary, details }
}

// ---------------------------------------------------------------------------
// Conflict detection
// ---------------------------------------------------------------------------

interface Conflict {
  ruleA: string
  ruleB: string
  reason: string
}

function detectConflicts(rules: RuleOut[], catalog: RuleCatalog | null): Conflict[] {
  if (!catalog) return []
  const conflicts: Conflict[] = []
  const active = rules.filter(r => r.is_active)

  // Check entry vs exit conflicts on same indicator + opposite direction
  const entryRules = active.filter(r => r.rule_type === 'entry')
  const exitRules = active.filter(r => r.rule_type === 'exit')

  for (const entry of entryRules) {
    for (const exit of exitRules) {
      if (entry.symbol !== exit.symbol) continue // different tickers can't conflict
      for (const eg of entry.rule_tree.groups) {
        for (const ec of eg.conditions) {
          for (const xg of exit.rule_tree.groups) {
            for (const xc of xg.conditions) {
              if (ec.indicator === xc.indicator) {
                const sameParams = JSON.stringify(ec.params) === JSON.stringify(xc.params)
                if (!sameParams) continue
                // Opposite comparators on same value
                if (
                  (ec.comparator === 'is_above' && xc.comparator === 'is_below' &&
                   typeof ec.value === 'number' && typeof xc.value === 'number' &&
                   ec.value <= xc.value) ||
                  (ec.comparator === 'is_below' && xc.comparator === 'is_above' &&
                   typeof ec.value === 'number' && typeof xc.value === 'number' &&
                   ec.value >= xc.value)
                ) {
                  const ind = catalog.indicators.find(i => i.id === ec.indicator)
                  const label = ind ? indicatorLabel(ind, ec.params) : ec.indicator
                  conflicts.push({
                    ruleA: entry.name,
                    ruleB: exit.name,
                    reason: `Both rules use ${label} with overlapping thresholds — entry at ${ec.value} and exit at ${xc.value} could trigger simultaneously`,
                  })
                }
              }
            }
          }
        }
      }
    }
  }

  // Check duplicate entry rules (same indicator + same comparator)
  for (let i = 0; i < entryRules.length; i++) {
    for (let j = i + 1; j < entryRules.length; j++) {
      const a = entryRules[i], b = entryRules[j]
      if (a.symbol !== b.symbol) continue // different tickers can't create duplicates
      for (const ag of a.rule_tree.groups) {
        for (const ac of ag.conditions) {
          for (const bg of b.rule_tree.groups) {
            for (const bc of bg.conditions) {
              if (ac.indicator === bc.indicator && ac.comparator === bc.comparator &&
                  JSON.stringify(ac.params) === JSON.stringify(bc.params)) {
                const ind = catalog.indicators.find(i => i.id === ac.indicator)
                const label = ind ? indicatorLabel(ind, ac.params) : ac.indicator
                conflicts.push({
                  ruleA: a.name,
                  ruleB: b.name,
                  reason: `Both entry rules check ${label} with "${ac.comparator}" — they may fire at the same time, creating duplicate positions`,
                })
              }
            }
          }
        }
      }
    }
  }

  return conflicts
}

// ---------------------------------------------------------------------------
// Shared input styles
// ---------------------------------------------------------------------------

const inputBase: React.CSSProperties = {
  fontFamily: 'var(--font-mono)', fontSize: 11,
  background: 'var(--bg-panel-alt)', border: '1px solid var(--border)',
  borderRadius: 8, padding: '5px 8px', color: 'var(--text-primary)',
  textAlign: 'center', outline: 'none',
}

const selectBase: React.CSSProperties = {
  ...inputBase,
  textAlign: 'left', cursor: 'pointer',
}

// ---------------------------------------------------------------------------
// Toolbox Item
// ---------------------------------------------------------------------------

function ToolboxItem({ label, color, description, onAdd }: {
  label: string; color: string; description: string; onAdd: () => void
}) {
  return (
    <button
      onClick={onAdd}
      title={description}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '9px 14px', width: '100%',
        background: 'transparent', border: '1px solid transparent',
        borderRadius: 12, cursor: 'pointer', transition: 'all 0.2s',
        textAlign: 'left', position: 'relative',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.background = `${color}18`
        e.currentTarget.style.borderColor = `${color}40`
        e.currentTarget.style.transform = 'translateX(6px)'
        e.currentTarget.style.boxShadow = `0 0 20px ${color}15`
      }}
      onMouseLeave={e => {
        e.currentTarget.style.background = 'transparent'
        e.currentTarget.style.borderColor = 'transparent'
        e.currentTarget.style.transform = 'translateX(0)'
        e.currentTarget.style.boxShadow = 'none'
      }}
    >
      <span style={{
        width: 10, height: 10, borderRadius: '50%',
        background: color, flexShrink: 0,
        boxShadow: `0 0 10px ${color}60`,
      }} />
      <div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600,
          color: 'var(--text-primary)', letterSpacing: '0.03em',
        }}>
          {label}
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)',
          letterSpacing: '0.02em', marginTop: 1, lineHeight: 1.3,
          maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis',
          display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
        }}>
          {description}
        </div>
      </div>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Condition Row
// ---------------------------------------------------------------------------

function ConditionRow({ condition, catalog, onChange, onRemove }: {
  condition: RuleCondition; catalog: RuleCatalog
  onChange: (c: RuleCondition) => void; onRemove: () => void
}) {
  const ind = catalog.indicators.find(i => i.id === condition.indicator)
  const color = ind ? CATEGORY_COLORS[ind.category] ?? 'var(--text-muted)' : 'var(--text-muted)'

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px',
      background: 'linear-gradient(135deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01))',
      border: '1px solid var(--border)',
      borderLeft: `3px solid ${color}`,
      borderRadius: 14, flexWrap: 'wrap',
      backdropFilter: 'blur(8px)',
    }}>
      {/* Indicator chip */}
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700,
        color: '#fff', padding: '5px 12px',
        background: `linear-gradient(135deg, ${color}, ${color}cc)`,
        borderRadius: 10, textShadow: '0 1px 4px rgba(0,0,0,0.3)',
        boxShadow: `0 2px 12px ${color}40`,
      }}>
        {ind ? indicatorLabel(ind, condition.params) : condition.indicator}
      </span>

      {/* Indicator params */}
      {ind?.params.map(p => (
        <span key={p.name}>
          {p.type === 'number' && (
            <input type="number" value={condition.params[p.name] ?? p.default ?? ''}
              min={p.min} max={p.max}
              onChange={e => onChange({ ...condition, params: { ...condition.params, [p.name]: Number(e.target.value) } })}
              style={{ ...inputBase, width: 56 }}
            />
          )}
          {p.type === 'select' && (
            <select value={condition.params[p.name] ?? p.default ?? ''}
              onChange={e => onChange({ ...condition, params: { ...condition.params, [p.name]: e.target.value } })}
              style={{ ...selectBase, fontSize: 10 }}
            >
              {p.options?.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          )}
        </span>
      ))}

      {/* Comparator */}
      <select value={condition.comparator}
        onChange={e => onChange({ ...condition, comparator: e.target.value })}
        style={{ ...selectBase, color: 'var(--amber)', fontWeight: 600, fontSize: 11 }}
      >
        {catalog.comparators.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select>

      {/* Value */}
      {condition.comparator === 'between' ? (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="number" value={Array.isArray(condition.value) ? condition.value[0] : ''}
            onChange={e => { const cur = Array.isArray(condition.value) ? condition.value : [0, 100]; onChange({ ...condition, value: [Number(e.target.value), cur[1]] }) }}
            style={{ ...inputBase, width: 64 }}
          />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--violet)', fontWeight: 700, letterSpacing: '0.1em' }}>AND</span>
          <input type="number" value={Array.isArray(condition.value) ? condition.value[1] : ''}
            onChange={e => { const cur = Array.isArray(condition.value) ? condition.value : [0, 100]; onChange({ ...condition, value: [cur[0], Number(e.target.value)] }) }}
            style={{ ...inputBase, width: 64 }}
          />
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <select
            value={typeof condition.value === 'object' && condition.value?.indicator ? 'indicator' : 'number'}
            onChange={e => {
              if (e.target.value === 'indicator') onChange({ ...condition, value: { indicator: 'sma', params: { period: 50 } } })
              else onChange({ ...condition, value: 0 })
            }}
            style={{ ...selectBase, fontSize: 9, padding: '4px 6px', color: 'var(--text-muted)' }}
          >
            <option value="number">#</option>
            <option value="indicator">IND</option>
          </select>
          {typeof condition.value === 'object' && condition.value?.indicator ? (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <select value={condition.value.indicator}
                onChange={e => onChange({ ...condition, value: { ...condition.value, indicator: e.target.value } })}
                style={{ ...selectBase, fontSize: 10 }}
              >
                {catalog.indicators.map(i => <option key={i.id} value={i.id}>{i.label}</option>)}
              </select>
              {catalog.indicators.find(i => i.id === condition.value.indicator)?.params
                .filter(p => p.name === 'period')
                .map(p => (
                  <input key={p.name} type="number" value={condition.value.params?.[p.name] ?? p.default ?? ''}
                    min={p.min} max={p.max}
                    onChange={e => onChange({ ...condition, value: { ...condition.value, params: { ...condition.value.params, [p.name]: Number(e.target.value) } } })}
                    style={{ ...inputBase, width: 56 }}
                  />
                ))}
            </div>
          ) : (
            <input type="number" step="any" value={typeof condition.value === 'number' ? condition.value : ''}
              onChange={e => onChange({ ...condition, value: Number(e.target.value) })}
              style={{ ...inputBase, width: 76 }}
            />
          )}
        </div>
      )}

      {/* Remove */}
      <button onClick={onRemove}
        style={{ marginLeft: 'auto', width: 26, height: 26, borderRadius: 8, background: 'rgba(255,77,109,0.08)', border: '1px solid rgba(255,77,109,0.2)', color: 'var(--red)', fontFamily: 'var(--font-mono)', fontSize: 13, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', lineHeight: 1, transition: 'all 0.15s' }}
        onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,77,109,0.2)'; e.currentTarget.style.borderColor = 'var(--red)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'rgba(255,77,109,0.08)'; e.currentTarget.style.borderColor = 'rgba(255,77,109,0.2)' }}
      >x</button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Action Row
// ---------------------------------------------------------------------------

function ActionRow({ action, catalog, ruleType, onChange, onRemove }: {
  action: RuleAction; catalog: RuleCatalog; ruleType: 'entry' | 'exit'
  onChange: (a: RuleAction) => void; onRemove: () => void
}) {
  const meta = catalog.actions.find(a => a.id === action.action)
  const available = catalog.actions.filter(a => a.for_rule_type === ruleType || a.for_rule_type === 'both')

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px',
      background: 'linear-gradient(135deg, var(--green-dim), transparent)',
      border: '1px solid var(--border)', borderLeft: '3px solid var(--green)',
      borderRadius: 14,
    }}>
      <select value={action.action}
        onChange={e => {
          const newMeta = catalog.actions.find(a => a.id === e.target.value)
          const newParams: Record<string, any> = {}; newMeta?.params.forEach(p => { newParams[p.name] = p.default })
          onChange({ action: e.target.value, params: newParams })
        }}
        style={{ ...selectBase, fontWeight: 700, fontSize: 12, color: 'var(--green)', background: 'var(--green-dim)', borderColor: 'rgba(93,255,182,0.25)', padding: '6px 12px' }}
      >
        {available.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}
      </select>

      {meta?.params.map(p => (
        <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <input type="number" step="0.1" value={action.params[p.name] ?? p.default ?? ''}
            min={p.min} max={p.max}
            onChange={e => onChange({ ...action, params: { ...action.params, [p.name]: Number(e.target.value) } })}
            style={{ ...inputBase, width: 68 }}
          />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>%</span>
        </div>
      ))}

      <button onClick={onRemove}
        style={{ marginLeft: 'auto', width: 26, height: 26, borderRadius: 8, background: 'rgba(255,77,109,0.08)', border: '1px solid rgba(255,77,109,0.2)', color: 'var(--red)', fontFamily: 'var(--font-mono)', fontSize: 13, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', lineHeight: 1, transition: 'all 0.15s' }}
        onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,77,109,0.2)'; e.currentTarget.style.borderColor = 'var(--red)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'rgba(255,77,109,0.08)'; e.currentTarget.style.borderColor = 'rgba(255,77,109,0.2)' }}
      >x</button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Rule Card
// ---------------------------------------------------------------------------

function RuleCard({ rule, catalog, onEdit, onToggle, onDelete }: {
  rule: RuleOut; catalog: RuleCatalog
  onEdit: () => void; onToggle: () => void; onDelete: () => void
}) {
  const { summary } = generateExplanation(rule.rule_type as 'entry' | 'exit', rule.rule_tree.groups, rule.rule_tree.actions, catalog)
  const accent = rule.rule_type === 'entry' ? 'var(--green)' : 'var(--amber)'
  const accentDim = rule.rule_type === 'entry' ? 'var(--green-dim)' : 'var(--amber-dim)'

  return (
    <div style={{
      padding: '16px 20px', borderRadius: 20,
      background: rule.is_active ? `linear-gradient(135deg, ${accentDim}, transparent 60%)` : 'rgba(255,255,255,0.02)',
      border: `1px solid ${rule.is_active ? 'var(--border)' : 'rgba(255,255,255,0.05)'}`,
      opacity: rule.is_active ? 1 : 0.45,
      transition: 'all 0.25s', backdropFilter: 'blur(8px)',
      boxShadow: rule.is_active ? `0 4px 24px ${accent}10` : 'none',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.16em',
          textTransform: 'uppercase', padding: '4px 10px', borderRadius: 999,
          background: accentDim, color: accent,
          border: `1px solid ${accent}40`,
          boxShadow: `0 0 12px ${accent}20`,
        }}>
          {rule.rule_type}
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700,
          color: 'var(--amber)', letterSpacing: '0.06em',
          padding: '3px 8px', background: 'var(--amber-dim)', borderRadius: 6,
        }}>
          {rule.symbol}
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700,
          color: 'var(--text-primary)', letterSpacing: '0.01em',
        }}>
          {rule.name}
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <PillButton onClick={onToggle} active={rule.is_active} color={rule.is_active ? 'var(--green)' : 'var(--text-muted)'}>
            {rule.is_active ? 'ON' : 'OFF'}
          </PillButton>
          <PillButton onClick={onEdit}>EDIT</PillButton>
          <PillButton onClick={onDelete} color="var(--red)">DEL</PillButton>
        </div>
      </div>
      {summary && (
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 11,
          color: 'var(--text-dim)', lineHeight: 1.6, letterSpacing: '0.01em',
        }}>
          {summary}
        </div>
      )}
    </div>
  )
}

function PillButton({ onClick, children, color, active }: {
  onClick: () => void; children: React.ReactNode; color?: string; active?: boolean
}) {
  const c = color ?? 'var(--text-muted)'
  return (
    <button onClick={onClick} style={{
      fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em',
      padding: '5px 12px', borderRadius: 999,
      background: active ? `${c}18` : 'rgba(255,255,255,0.04)',
      border: `1px solid ${c}30`,
      color: c, cursor: 'pointer', transition: 'all 0.15s', fontWeight: 600,
    }}
      onMouseEnter={e => { e.currentTarget.style.background = `${c}25`; e.currentTarget.style.borderColor = c }}
      onMouseLeave={e => { e.currentTarget.style.background = active ? `${c}18` : 'rgba(255,255,255,0.04)'; e.currentTarget.style.borderColor = `${c}30` }}
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Main Rules Page
// ---------------------------------------------------------------------------

export function Rules() {
  const userId = useAuthStore(s => s.userId)
  const [catalog, setCatalog] = useState<RuleCatalog | null>(null)
  const [rules, setRules] = useState<RuleOut[]>([])
  const [templates, setTemplates] = useState<import('@/lib/api').StrategyTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Template apply state
  const [applyingTemplate, setApplyingTemplate] = useState<string | null>(null)
  const [templateSymbol, setTemplateSymbol] = useState('')
  const [templateQty, setTemplateQty] = useState(1)
  const [templateSymbolStatus, setTemplateSymbolStatus] = useState<{ valid: boolean; message: string } | null>(null)
  const [templateApplying, setTemplateApplying] = useState(false)
  const templateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Builder state
  const [editing, setEditing] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [ruleSymbol, setRuleSymbol] = useState('')
  const [symbolStatus, setSymbolStatus] = useState<{ valid: boolean; message: string; name?: string } | null>(null)
  const [symbolChecking, setSymbolChecking] = useState(false)
  const symbolTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [ruleName, setRuleName] = useState('')
  const [ruleType, setRuleType] = useState<'entry' | 'exit'>('entry')
  const [groups, setGroups] = useState<RuleGroup[]>([{ logic: 'AND', conditions: [] }])
  const [actions, setActions] = useState<RuleAction[]>([])
  const [saving, setSaving] = useState(false)

  const loadData = useCallback(async () => {
    if (!userId) return
    setLoading(true)
    try {
      const [cat, rls, tpls] = await Promise.all([api.ruleCatalog(userId), api.rules(userId), api.strategyTemplates(userId)])
      setCatalog(cat); setRules(rls); setTemplates(tpls)
    } catch { setError('Failed to load rules.') }
    finally { setLoading(false) }
  }, [userId])

  useEffect(() => { loadData() }, [loadData])

  function handleSymbolChange(val: string) {
    const sym = val.toUpperCase().replace(/[^A-Z]/g, '')
    setRuleSymbol(sym)
    setSymbolStatus(null)
    if (symbolTimerRef.current) clearTimeout(symbolTimerRef.current)
    if (!sym || sym.length < 1 || !userId) return
    setSymbolChecking(true)
    symbolTimerRef.current = setTimeout(async () => {
      try {
        const result = await api.validateSymbol(userId, sym)
        setSymbolStatus({ valid: result.valid, message: result.message, name: result.name ?? undefined })
      } catch {
        setSymbolStatus(null)
      } finally {
        setSymbolChecking(false)
      }
    }, 400)
  }

  function resetBuilder() {
    setEditing(false); setEditId(null); setRuleSymbol(''); setSymbolStatus(null); setRuleName(''); setRuleType('entry')
    setGroups([{ logic: 'AND', conditions: [] }]); setActions([])
  }

  function startNew() { resetBuilder(); setEditing(true) }

  function startEdit(rule: RuleOut) {
    setEditId(rule.id); setRuleSymbol(rule.symbol); setSymbolStatus({ valid: true, message: rule.symbol }); setRuleName(rule.name); setRuleType(rule.rule_type as 'entry' | 'exit')
    setGroups(rule.rule_tree.groups.length > 0 ? rule.rule_tree.groups : [{ logic: 'AND', conditions: [] }])
    setActions(rule.rule_tree.actions); setEditing(true)
  }

  async function handleSave() {
    if (!userId || !catalog) return
    setSaving(true)
    try {
      const sym = ruleSymbol.toUpperCase().trim()
      if (!sym) { setError('Please enter a ticker symbol.'); setSaving(false); return }
      const tree: RuleTree = { groups, actions }
      if (editId) await api.updateRule(userId, editId, { symbol: sym, name: ruleName, rule_type: ruleType, rule_tree: tree })
      else await api.createRule(userId, { symbol: sym, name: ruleName || `${sym} ${ruleType} rule`, rule_type: ruleType, rule_tree: tree })
      await loadData(); resetBuilder()
    } catch { setError('Failed to save rule.') }
    finally { setSaving(false) }
  }

  async function handleToggle(rule: RuleOut) {
    if (!userId) return
    try { await api.toggleRule(userId, rule.id, !rule.is_active); await loadData() }
    catch { setError('Failed to toggle rule.') }
  }

  async function handleDelete(rule: RuleOut) {
    if (!userId) return
    try { await api.deleteRule(userId, rule.id); await loadData() }
    catch { setError('Failed to delete rule.') }
  }

  function addIndicatorCondition(indicatorId: string) {
    if (!catalog) return
    const ind = catalog.indicators.find(i => i.id === indicatorId)
    if (!ind) return
    const params: Record<string, any> = {}
    ind.params.forEach(p => { params[p.name] = p.default })
    const newCond: RuleCondition = { indicator: indicatorId, params, comparator: 'is_above', value: 0 }
    setGroups(prev => {
      const updated = [...prev]
      if (updated.length === 0) updated.push({ logic: 'AND', conditions: [] })
      updated[updated.length - 1] = { ...updated[updated.length - 1], conditions: [...updated[updated.length - 1].conditions, newCond] }
      return updated
    })
  }

  function addAction(actionId: string) {
    if (!catalog) return
    const meta = catalog.actions.find(a => a.id === actionId)
    if (!meta) return
    const params: Record<string, any> = {}
    meta.params.forEach(p => { params[p.name] = p.default })
    setActions(prev => [...prev, { action: actionId, params }])
  }

  function updateCondition(gi: number, ci: number, cond: RuleCondition) {
    setGroups(prev => { const u = [...prev]; const g = { ...u[gi], conditions: [...u[gi].conditions] }; g.conditions[ci] = cond; u[gi] = g; return u })
  }
  function removeCondition(gi: number, ci: number) {
    setGroups(prev => { const u = [...prev]; u[gi] = { ...u[gi], conditions: u[gi].conditions.filter((_, i) => i !== ci) }; return u })
  }
  function updateAction(idx: number, act: RuleAction) { setActions(prev => prev.map((a, i) => i === idx ? act : a)) }
  function removeAction(idx: number) { setActions(prev => prev.filter((_, i) => i !== idx)) }

  const explanation = generateExplanation(ruleType, groups, actions, catalog)
  const conflicts = detectConflicts(rules, catalog)
  const entryRules = rules.filter(r => r.rule_type === 'entry')
  const exitRules = rules.filter(r => r.rule_type === 'exit')

  if (loading) {
    return (
      <div style={{ padding: '80px 20px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)', letterSpacing: '0.14em' }}>
        LOADING RULES ENGINE...
      </div>
    )
  }

  return (
    <div style={{ padding: '24px 0', maxWidth: 1440, margin: '0 auto' }}>

      {/* Hero */}
      <section style={{
        position: 'relative', overflow: 'hidden',
        borderRadius: 30, padding: '22px 26px', marginBottom: 20,
        background: 'linear-gradient(135deg, rgba(124,108,255,0.28) 0%, rgba(255,107,61,0.24) 52%, rgba(93,255,182,0.18) 100%)',
        border: '1px solid rgba(255,255,255,0.18)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.08)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(200,180,255,0.9)', marginBottom: 6, textShadow: '0 0 12px rgba(124,108,255,0.3)' }}>
              Rules Engine
            </div>
            <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 28, lineHeight: 1.1, letterSpacing: '-0.04em', color: '#fff', textShadow: '0 2px 8px rgba(0,0,0,0.3)', margin: 0 }}>
              Build your trading strategy visually.
            </h1>
            <p style={{ marginTop: 8, fontFamily: 'var(--font-ui)', fontSize: 13, lineHeight: 1.5, color: 'rgba(255,255,255,0.8)', maxWidth: 500 }}>
              Combine indicators into entry and exit rules. We'll explain what each rule does in plain English.
            </p>
          </div>
          {!editing && (
            <button onClick={startNew} style={{
              fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.06em', fontWeight: 700,
              padding: '12px 28px', borderRadius: 999,
              background: 'linear-gradient(135deg, var(--amber), var(--amber-bright))',
              color: '#000', border: 'none', cursor: 'pointer',
              boxShadow: '0 4px 20px rgba(255,107,61,0.4), inset 0 1px 0 rgba(255,255,255,0.3)',
              transition: 'all 0.2s',
            }}
              onMouseEnter={e => e.currentTarget.style.transform = 'translateY(-2px)'}
              onMouseLeave={e => e.currentTarget.style.transform = 'translateY(0)'}
            >
              + NEW RULE
            </button>
          )}
        </div>
      </section>

      {/* Error */}
      {error && (
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--red)',
          padding: '12px 18px', border: '1px solid var(--red-dim)', borderRadius: 14,
          background: 'var(--red-dim)', marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          {error}
          <button onClick={() => setError('')} style={{ color: 'var(--red)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 10 }}>DISMISS</button>
        </div>
      )}

      {/* Conflicts Warning */}
      {!editing && conflicts.length > 0 && (
        <div style={{
          padding: '14px 20px', marginBottom: 16, borderRadius: 18,
          background: 'linear-gradient(135deg, rgba(255,77,109,0.12), rgba(255,159,90,0.08))',
          border: '1px solid rgba(255,77,109,0.25)',
        }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em', color: 'var(--red)', marginBottom: 10, fontWeight: 700 }}>
            RULE CONFLICTS DETECTED
          </div>
          {conflicts.map((c, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 6, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.5 }}>
              <span style={{ color: 'var(--red)', flexShrink: 0, fontWeight: 700 }}>!</span>
              <span><strong style={{ color: 'var(--text-primary)' }}>{c.ruleA}</strong> vs <strong style={{ color: 'var(--text-primary)' }}>{c.ruleB}</strong> — {c.reason}</span>
            </div>
          ))}
        </div>
      )}

      {/* Builder */}
      {editing && catalog && (
        <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr 300px', gap: 16, marginBottom: 28 }}>

          {/* LEFT: Toolbox */}
          <Panel title="Toolbox" tag="DRAG" style={{ alignSelf: 'start' }}>
            <div style={{ padding: '12px 14px' }}>
              {Object.keys(CATEGORY_LABELS).map(cat => {
                const items = catalog.indicators.filter(i => i.category === cat)
                if (items.length === 0) return null
                return (
                  <div key={cat} style={{ marginBottom: 16 }}>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.22em', color: CATEGORY_COLORS[cat], marginBottom: 6, fontWeight: 700 }}>
                      {CATEGORY_LABELS[cat]}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                      {items.map(ind => (
                        <ToolboxItem key={ind.id} label={ind.label} color={CATEGORY_COLORS[ind.category]}
                          description={ind.description} onAdd={() => addIndicatorCondition(ind.id)} />
                      ))}
                    </div>
                  </div>
                )
              })}

              <div style={{ height: 1, background: 'var(--border)', margin: '16px 0' }} />

              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.22em', color: 'var(--green)', marginBottom: 8, fontWeight: 700 }}>
                ACTIONS
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {catalog.actions
                  .filter(a => a.for_rule_type === ruleType || a.for_rule_type === 'both')
                  .map(act => (
                    <ToolboxItem key={act.id} label={act.label} color="var(--green)"
                      description={act.description} onAdd={() => addAction(act.id)} />
                  ))}
              </div>
            </div>
          </Panel>

          {/* CENTER: Canvas */}
          <Panel title="Rule Builder" tag={ruleType.toUpperCase()} accented style={{ minHeight: 420 }}>
            <div style={{ padding: '16px 20px' }}>
              {/* Symbol + Name + type */}
              <div style={{ display: 'flex', gap: 12, marginBottom: 22 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <input type="text" placeholder="Ticker..." value={ruleSymbol}
                    onChange={e => handleSymbolChange(e.target.value)}
                    maxLength={6}
                    style={{
                      width: 100, fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 700,
                      background: 'transparent', border: 'none',
                      borderBottom: `2px solid ${
                        symbolStatus === null ? 'var(--border)'
                        : symbolStatus.valid ? 'var(--green)'
                        : 'var(--red)'
                      }`,
                      padding: '10px 4px', color: 'var(--amber)', letterSpacing: '0.06em', outline: 'none',
                      textAlign: 'center',
                    }}
                  />
                  {symbolChecking && (
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: 'var(--text-muted)', textAlign: 'center', letterSpacing: '0.1em' }}>
                      CHECKING...
                    </span>
                  )}
                  {!symbolChecking && symbolStatus && (
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: 8, textAlign: 'center', letterSpacing: '0.04em',
                      color: symbolStatus.valid ? 'var(--green)' : 'var(--red)',
                      maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {symbolStatus.valid ? (symbolStatus.name || symbolStatus.message) : symbolStatus.message}
                    </span>
                  )}
                </div>
                <input type="text" placeholder="Name your rule..." value={ruleName}
                  onChange={e => setRuleName(e.target.value)}
                  style={{
                    flex: 1, fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 600,
                    background: 'transparent', border: 'none', borderBottom: '2px solid var(--border)',
                    padding: '10px 4px', color: 'var(--text-primary)', letterSpacing: '0.01em', outline: 'none',
                  }}
                />
                <div style={{ display: 'flex', borderRadius: 14, overflow: 'hidden', border: '1px solid var(--border)' }}>
                  {(['entry', 'exit'] as const).map(t => (
                    <button key={t} onClick={() => setRuleType(t)} style={{
                      fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.08em', fontWeight: 700,
                      padding: '10px 18px', border: 'none', cursor: 'pointer', textTransform: 'uppercase',
                      background: ruleType === t ? (t === 'entry' ? 'var(--green-dim)' : 'var(--amber-dim)') : 'transparent',
                      color: ruleType === t ? (t === 'entry' ? 'var(--green)' : 'var(--amber)') : 'var(--text-muted)',
                      transition: 'all 0.15s',
                    }}>{t}</button>
                  ))}
                </div>
              </div>

              {/* Conditions */}
              <div style={{ marginBottom: 22 }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.2em', color: ruleType === 'entry' ? 'var(--green)' : 'var(--amber)', marginBottom: 12, fontWeight: 700 }}>
                  CONDITIONS {ruleType === 'entry' ? '— WHEN TO BUY' : '— WHEN TO SELL'}
                </div>

                {groups[0]?.conditions.length === 0 && (
                  <div style={{
                    padding: '36px 20px', textAlign: 'center',
                    border: '2px dashed rgba(255,255,255,0.1)', borderRadius: 18,
                    color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12,
                    background: 'linear-gradient(135deg, rgba(255,255,255,0.02), transparent)',
                  }}>
                    Click an indicator from the toolbox to add a condition
                  </div>
                )}

                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {groups.map((group, gi) =>
                    group.conditions.map((cond, ci) => (
                      <div key={`${gi}-${ci}`}>
                        {ci > 0 && (
                          <div style={{ textAlign: 'center', padding: '6px 0' }}>
                            <button onClick={() => {
                              setGroups(prev => { const u = [...prev]; u[gi] = { ...u[gi], logic: u[gi].logic === 'AND' ? 'OR' : 'AND' }; return u })
                            }} style={{
                              fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', fontWeight: 700,
                              padding: '3px 14px', borderRadius: 999,
                              background: 'var(--violet-dim)', border: '1px solid rgba(124,108,255,0.3)',
                              color: 'var(--violet)', cursor: 'pointer',
                              boxShadow: '0 0 12px rgba(124,108,255,0.15)',
                            }}>{group.logic}</button>
                          </div>
                        )}
                        <ConditionRow condition={cond} catalog={catalog}
                          onChange={c => updateCondition(gi, ci, c)} onRemove={() => removeCondition(gi, ci)} />
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* Actions */}
              <div style={{ marginBottom: 24 }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.2em', color: 'var(--green)', marginBottom: 12, fontWeight: 700 }}>
                  ACTIONS — WHAT TO DO
                </div>
                {actions.length === 0 && (
                  <div style={{
                    padding: '24px 20px', textAlign: 'center',
                    border: '2px dashed rgba(255,255,255,0.1)', borderRadius: 18,
                    color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12,
                    background: 'linear-gradient(135deg, rgba(255,255,255,0.02), transparent)',
                  }}>
                    Click an action from the toolbox
                  </div>
                )}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {actions.map((act, i) => (
                    <ActionRow key={i} action={act} catalog={catalog} ruleType={ruleType}
                      onChange={a => updateAction(i, a)} onRemove={() => removeAction(i)} />
                  ))}
                </div>
              </div>

              {/* Save / Cancel */}
              <div style={{ display: 'flex', gap: 10 }}>
                <button onClick={handleSave}
                  disabled={saving || !ruleSymbol.trim() || !symbolStatus?.valid || groups[0]?.conditions.length === 0}
                  style={{
                    fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.06em', fontWeight: 700,
                    padding: '12px 28px', borderRadius: 999, border: 'none',
                    background: (saving || !ruleSymbol.trim() || !symbolStatus?.valid || groups[0]?.conditions.length === 0)
                      ? 'var(--text-muted)' : 'linear-gradient(135deg, var(--amber), var(--amber-bright))',
                    color: '#000', cursor: (saving || !ruleSymbol.trim() || !symbolStatus?.valid || groups[0]?.conditions.length === 0) ? 'not-allowed' : 'pointer',
                    opacity: (saving || !ruleSymbol.trim() || !symbolStatus?.valid || groups[0]?.conditions.length === 0) ? 0.3 : 1,
                    boxShadow: (saving || !ruleSymbol.trim() || !symbolStatus?.valid || groups[0]?.conditions.length === 0) ? 'none' : '0 4px 20px rgba(255,107,61,0.35)',
                    transition: 'all 0.2s',
                  }}
                >
                  {saving ? 'SAVING...' : editId ? 'UPDATE RULE' : 'SAVE RULE'}
                </button>
                <button onClick={resetBuilder} style={{
                  fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.08em',
                  padding: '12px 22px', borderRadius: 999,
                  background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)',
                  color: 'var(--text-muted)', cursor: 'pointer', transition: 'all 0.15s',
                }}
                  onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--text-muted)'}
                  onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}
                >CANCEL</button>
              </div>
            </div>
          </Panel>

          {/* RIGHT: Explanation */}
          <Panel title="What Your Rule Does" tag="AI" style={{ alignSelf: 'start' }}>
            <div style={{ padding: '14px 18px' }}>
              {explanation.summary ? (
                <>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500,
                    color: 'var(--text-primary)', lineHeight: 1.7,
                    padding: '16px 18px', borderRadius: 16,
                    background: 'linear-gradient(135deg, var(--amber-dim), var(--violet-dim))',
                    border: '1px solid rgba(255,107,61,0.2)',
                    marginBottom: 18,
                    boxShadow: '0 4px 20px rgba(255,107,61,0.08)',
                  }}>
                    &ldquo;{explanation.summary}&rdquo;
                  </div>

                  {explanation.details.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.2em', color: 'var(--violet)', fontWeight: 700 }}>
                        WHAT THIS MEANS
                      </div>
                      {explanation.details.map((d, i) => (
                        <div key={i} style={{
                          display: 'flex', gap: 10, alignItems: 'flex-start',
                          fontFamily: 'var(--font-mono)', fontSize: 11,
                          color: 'var(--text-dim)', lineHeight: 1.6,
                        }}>
                          <span style={{ color: 'var(--violet)', flexShrink: 0, fontSize: 14, lineHeight: 1.2 }}>&#x2192;</span>
                          <span>{d}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 12,
                  color: 'var(--text-muted)', lineHeight: 1.7,
                  padding: '28px 14px', textAlign: 'center',
                }}>
                  Add conditions and actions to see a plain-English explanation of your trading rule.
                </div>
              )}
            </div>
          </Panel>
        </div>
      )}

      {/* Strategy Templates */}
      {!editing && templates.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.16em',
            color: 'var(--violet)', marginBottom: 14, fontWeight: 700, textTransform: 'uppercase',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--violet)', boxShadow: '0 0 10px rgba(124,108,255,0.4)' }} />
            PRE-BUILT STRATEGIES — Pick one, add a ticker, start trading
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${templates.length}, 1fr)`, gap: 14 }}>
            {templates.map(t => {
              const isApplying = applyingTemplate === t.id
              return (
                <div key={t.id} style={{
                  padding: '18px 20px', borderRadius: 22,
                  background: isApplying
                    ? 'linear-gradient(135deg, var(--violet-dim), rgba(124,108,255,0.08))'
                    : 'linear-gradient(135deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01))',
                  border: `1px solid ${isApplying ? 'rgba(124,108,255,0.4)' : 'var(--border)'}`,
                  backdropFilter: 'blur(8px)',
                  transition: 'all 0.2s',
                }}>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700,
                    color: 'var(--text-primary)', marginBottom: 8,
                  }}>
                    {t.name}
                  </div>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)',
                    lineHeight: 1.6, marginBottom: 14, minHeight: 60,
                  }}>
                    {t.description}
                  </div>

                  {!isApplying ? (
                    <button
                      onClick={() => { setApplyingTemplate(t.id); setTemplateSymbol(''); setTemplateQty(1); setTemplateSymbolStatus(null) }}
                      style={{
                        fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', fontWeight: 700,
                        padding: '8px 18px', borderRadius: 999, width: '100%',
                        background: 'linear-gradient(135deg, var(--violet), rgba(124,108,255,0.8))',
                        color: '#fff', border: 'none', cursor: 'pointer',
                        boxShadow: '0 2px 12px rgba(124,108,255,0.3)',
                      }}
                    >
                      USE THIS STRATEGY
                    </button>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      <div style={{ display: 'flex', gap: 8 }}>
                        <div style={{ flex: 1 }}>
                          <input type="text" placeholder="Ticker..." value={templateSymbol}
                            onChange={e => {
                              const sym = e.target.value.toUpperCase().replace(/[^A-Z]/g, '')
                              setTemplateSymbol(sym)
                              setTemplateSymbolStatus(null)
                              if (templateTimerRef.current) clearTimeout(templateTimerRef.current)
                              if (!sym || !userId) return
                              templateTimerRef.current = setTimeout(async () => {
                                try {
                                  const r = await api.validateSymbol(userId, sym)
                                  setTemplateSymbolStatus({ valid: r.valid, message: r.message })
                                } catch { /* ignore */ }
                              }, 400)
                            }}
                            maxLength={6}
                            style={{
                              width: '100%', fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700,
                              background: 'var(--bg-panel-alt)', border: `1px solid ${templateSymbolStatus?.valid ? 'var(--green)' : templateSymbolStatus ? 'var(--red)' : 'var(--border)'}`,
                              borderRadius: 10, padding: '8px 12px', color: 'var(--amber)', letterSpacing: '0.06em', outline: 'none',
                            }}
                          />
                          {templateSymbolStatus && (
                            <div style={{
                              fontFamily: 'var(--font-mono)', fontSize: 8, marginTop: 3,
                              color: templateSymbolStatus.valid ? 'var(--green)' : 'var(--red)',
                              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                            }}>
                              {templateSymbolStatus.message}
                            </div>
                          )}
                        </div>
                        <input type="number" placeholder="Qty" value={templateQty} min={1}
                          onChange={e => setTemplateQty(Math.max(1, Number(e.target.value)))}
                          style={{
                            width: 60, fontFamily: 'var(--font-mono)', fontSize: 13,
                            background: 'var(--bg-panel-alt)', border: '1px solid var(--border)',
                            borderRadius: 10, padding: '8px', color: 'var(--text-primary)', textAlign: 'center', outline: 'none',
                          }}
                        />
                      </div>
                      <div style={{ display: 'flex', gap: 8 }}>
                        <button
                          disabled={!templateSymbolStatus?.valid || templateApplying}
                          onClick={async () => {
                            if (!userId || !templateSymbolStatus?.valid) return
                            setTemplateApplying(true)
                            try {
                              await api.applyTemplate(userId, { template_id: t.id, symbol: templateSymbol, qty: templateQty })
                              await loadData()
                              setApplyingTemplate(null); setTemplateSymbol(''); setTemplateSymbolStatus(null)
                            } catch { setError('Failed to apply template.') }
                            finally { setTemplateApplying(false) }
                          }}
                          style={{
                            flex: 1, fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', fontWeight: 700,
                            padding: '8px 14px', borderRadius: 999,
                            background: (!templateSymbolStatus?.valid || templateApplying) ? 'var(--text-muted)' : 'linear-gradient(135deg, var(--green), rgba(93,255,182,0.8))',
                            color: '#000', border: 'none',
                            cursor: (!templateSymbolStatus?.valid || templateApplying) ? 'not-allowed' : 'pointer',
                            opacity: (!templateSymbolStatus?.valid || templateApplying) ? 0.3 : 1,
                          }}
                        >
                          {templateApplying ? 'APPLYING...' : 'APPLY'}
                        </button>
                        <button
                          onClick={() => setApplyingTemplate(null)}
                          style={{
                            fontFamily: 'var(--font-mono)', fontSize: 10, padding: '8px 14px', borderRadius: 999,
                            background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-muted)', cursor: 'pointer',
                          }}
                        >
                          CANCEL
                        </button>
                      </div>

                      {/* Explanation */}
                      <div style={{ marginTop: 4 }}>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.16em', color: 'var(--text-muted)', marginBottom: 6 }}>ENTRY CONDITIONS</div>
                        {t.explanation.entry.map((e, i) => (
                          <div key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', lineHeight: 1.5, display: 'flex', gap: 6, marginBottom: 3 }}>
                            <span style={{ color: 'var(--green)', flexShrink: 0 }}>&#x2192;</span>
                            <span>{e}</span>
                          </div>
                        ))}
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.16em', color: 'var(--text-muted)', marginTop: 8, marginBottom: 6 }}>EXIT CONDITIONS</div>
                        {t.explanation.exit.map((e, i) => (
                          <div key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', lineHeight: 1.5, display: 'flex', gap: 6, marginBottom: 3 }}>
                            <span style={{ color: 'var(--red)', flexShrink: 0 }}>&#x2192;</span>
                            <span>{e}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Saved Rules */}
      {!editing && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
          {/* Entry */}
          <Panel title="Entry Rules" tag={`${entryRules.length}`} style={{}}>
            <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {entryRules.length === 0 && (
                <div style={{
                  padding: '44px 20px', textAlign: 'center',
                  border: '2px dashed rgba(255,255,255,0.08)', borderRadius: 16,
                  fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)',
                  background: 'linear-gradient(135deg, rgba(93,255,182,0.03), transparent)',
                }}>
                  No entry rules yet — click "+ NEW RULE" to create one
                </div>
              )}
              {entryRules.map(r => (
                <RuleCard key={r.id} rule={r} catalog={catalog!}
                  onEdit={() => startEdit(r)} onToggle={() => handleToggle(r)} onDelete={() => handleDelete(r)} />
              ))}
            </div>
          </Panel>

          {/* Exit */}
          <Panel title="Exit Rules" tag={`${exitRules.length}`} style={{}}>
            <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {exitRules.length === 0 && (
                <div style={{
                  padding: '44px 20px', textAlign: 'center',
                  border: '2px dashed rgba(255,255,255,0.08)', borderRadius: 16,
                  fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)',
                  background: 'linear-gradient(135deg, rgba(255,107,61,0.03), transparent)',
                }}>
                  No exit rules yet — click "+ NEW RULE" to create one
                </div>
              )}
              {exitRules.map(r => (
                <RuleCard key={r.id} rule={r} catalog={catalog!}
                  onEdit={() => startEdit(r)} onToggle={() => handleToggle(r)} onDelete={() => handleDelete(r)} />
              ))}
            </div>
          </Panel>
        </div>
      )}
    </div>
  )
}
