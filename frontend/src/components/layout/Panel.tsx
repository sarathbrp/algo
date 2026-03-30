import type { CSSProperties, ReactNode } from 'react'

interface PanelProps {
  title: string
  tag?: string
  accented?: boolean
  children: ReactNode
  style?: CSSProperties
}

export function Panel({ title, tag, accented, children, style }: PanelProps) {
  return (
    <div style={{
      background: 'var(--bg-panel)',
      border: '1px solid var(--border)',
      borderRadius: 28,
      position: 'relative',
      overflow: 'hidden',
      animation: 'panel-enter 0.5s ease both',
      backdropFilter: 'blur(18px)',
      boxShadow: 'var(--surface-shadow)',
      ...style,
    }}>
      {accented && (
        <div aria-hidden style={{
          position: 'absolute',
          inset: 0,
          background: 'linear-gradient(135deg, var(--amber-dim) 0%, transparent 34%, var(--violet-dim) 100%)',
          zIndex: 1,
          pointerEvents: 'none',
        }} />
      )}

      <div aria-hidden style={{
        position: 'absolute',
        inset: 0,
        background: 'linear-gradient(180deg, rgba(255,255,255,0.08), transparent 24%)',
        pointerEvents: 'none',
      }} />

      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '14px 18px 12px',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        position: 'relative',
        zIndex: 1,
      }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
          {title}
        </span>
        {tag && (
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--text-primary)',
            background: 'rgba(255,255,255,0.08)',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: 999,
            padding: '4px 10px',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.08)',
          }}>
            {tag}
          </span>
        )}
      </div>

      <div style={{ position: 'relative', zIndex: 1 }}>
        {children}
      </div>
    </div>
  )
}
