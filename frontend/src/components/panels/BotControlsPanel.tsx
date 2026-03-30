import { useBotStatus } from '@/hooks/useBotStatus'
import { Panel } from '@/components/layout/Panel'
import type { BotState } from '@/lib/api'

const BTN_CONFIG: Record<BotState, { label: string; color: string; detail: string }> = {
  running: {
    label: 'RUN TRADING',
    color: 'var(--green)',
    detail: 'Bot may open new trades and manage open positions.',
  },
  paused: {
    label: 'PAUSE NEW TRADES',
    color: 'var(--amber)',
    detail: 'Bot keeps syncing and managing open positions, but will not open new trades.',
  },
  stopped: {
    label: 'STOP AUTOMATION',
    color: 'var(--red)',
    detail: 'Bot becomes observation-only. No automated entries or exits are placed.',
  },
}

export function BotControlsPanel() {
  const {
    status,
    mode,
    setStatus,
    description,
    workerStatus,
    heartbeatLabel,
    workerSummary,
    strategySlug,
    riskProfile,
    maxPositions,
    isConfigured,
    mutationError,
    isLoading,
    isMutating,
  } = useBotStatus()

  return (
    <Panel title="Bot Controls" tag={status.toUpperCase()} accented style={{ gridColumn: 3, gridRow: 1, display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ padding: 14, background: 'var(--bg-panel-alt)', border: '1px solid var(--border)', borderRadius: 20 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>
            What the bot is doing
          </div>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: 24, color: !isConfigured ? 'var(--amber)' : status === 'running' ? 'var(--green)' : status === 'paused' ? 'var(--amber)' : 'var(--red)', letterSpacing: '-0.04em' }}>
            {!isConfigured ? 'SETUP REQUIRED' : status.toUpperCase()}
          </div>
          <div style={{ fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--text-dim)', marginTop: 6, lineHeight: 1.45 }}>
            {isLoading ? 'Loading bot state...' : !isConfigured ? 'Connect Alpaca in Settings before you can control automation.' : description}
          </div>
          <div style={{ marginTop: 6, fontFamily: 'var(--font-ui)', fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.4 }}>
            {workerSummary}
          </div>
          {mutationError && (
            <div style={{ marginTop: 8, fontFamily: 'var(--font-ui)', fontSize: 12, color: 'var(--red)', lineHeight: 1.4 }}>
              {mutationError}
            </div>
          )}
        </div>

        <div style={{ display: 'grid', gap: 10 }}>
          {(['running', 'paused', 'stopped'] as BotState[]).map((action) => {
            const { label, color, detail } = BTN_CONFIG[action]
            const isActive = status === action
            return (
              <button
                key={action}
                onClick={() => setStatus(action)}
                disabled={isMutating || isLoading || !isConfigured}
                style={{
                  width: '100%',
                  padding: '12px 14px',
                  fontFamily: 'var(--font-ui)',
                  borderRadius: 22,
                  border: `1px solid ${isActive ? color : 'rgba(255,255,255,0.08)'}`,
                  background: isActive ? `${color}18` : 'rgba(255,255,255,0.04)',
                  color: 'var(--text-primary)',
                  cursor: isMutating || isLoading ? 'wait' : !isConfigured ? 'not-allowed' : 'pointer',
                  transition: 'all 0.15s ease',
                  boxShadow: isActive ? `0 0 14px ${color}22` : 'none',
                  opacity: isMutating || isLoading ? 0.75 : !isConfigured ? 0.58 : 1,
                  textAlign: 'left',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{
                    width: 12,
                    height: 12,
                    borderRadius: '50%',
                    background: isActive ? color : 'transparent',
                    border: `2px solid ${isActive ? color : 'rgba(255,255,255,0.2)'}`,
                    boxShadow: isActive ? `0 0 12px ${color}66` : 'none',
                    flexShrink: 0,
                  }} />
                  <div style={{ fontFamily: 'var(--font-display)', fontSize: 18, letterSpacing: '-0.03em', color }}>{label}</div>
                </div>
                <div style={{ fontFamily: 'var(--font-ui)', fontSize: 12, color: 'var(--text-dim)', marginTop: 4, lineHeight: 1.4 }}>
                  {detail}
                </div>
              </button>
            )
          })}
        </div>

        <div style={{ padding: 14, background: 'var(--bg-panel-alt)', border: '1px solid var(--border)', borderRadius: 20 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 8 }}>
            Account setup
          </div>
          <div style={{ display: 'grid', gap: 5, fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--text-dim)' }}>
            <div>Mode: {mode.toUpperCase()}</div>
            <div>Strategy: {strategySlug.replace(/_/g, ' ')}</div>
            <div>Risk profile: {riskProfile}</div>
            <div>Max positions: {maxPositions ?? 'Not set yet'}</div>
          </div>
        </div>

        <div style={{ padding: 14, background: 'var(--bg-panel-alt)', border: '1px solid var(--border)', borderRadius: 20 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>
            Sync health
          </div>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: workerStatus === 'unknown' ? 'var(--amber)' : 'var(--text-primary)', letterSpacing: '-0.04em' }}>
            {workerStatus.toUpperCase()}
          </div>
          <div style={{ fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--text-dim)', marginTop: 6, lineHeight: 1.4 }}>
            Last heartbeat: {heartbeatLabel}
          </div>
        </div>
      </div>
    </Panel>
  )
}
