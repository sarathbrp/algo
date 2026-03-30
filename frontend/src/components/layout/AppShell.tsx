import { Outlet } from 'react-router-dom'
import { Header } from './Header'

function AmbientShapes() {
  return (
    <>
      <div aria-hidden style={{
        position: 'fixed',
        top: 72,
        right: -90,
        width: 280,
        height: 280,
        borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(255,107,61,0.16) 0%, transparent 68%)',
        filter: 'blur(16px)',
        pointerEvents: 'none',
        zIndex: 0,
      }} />
      <div aria-hidden style={{
        position: 'fixed',
        bottom: 40,
        left: -80,
        width: 240,
        height: 240,
        borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(124,108,255,0.16) 0%, transparent 68%)',
        filter: 'blur(20px)',
        pointerEvents: 'none',
        zIndex: 0,
      }} />
    </>
  )
}

export function AppShell() {
  return (
    <>
      <AmbientShapes />
      <div style={{ position: 'relative', zIndex: 1, minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
        <Header />
        <main style={{ flex: 1, padding: '0 20px 28px', display: 'flex', flexDirection: 'column', maxWidth: 1540, width: '100%', margin: '0 auto' }}>
          <Outlet />
        </main>
        <footer style={{
          padding: '14px 20px 18px',
          borderTop: '1px solid rgba(255,255,255,0.08)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          maxWidth: 1540,
          width: '100%',
          margin: '0 auto',
        }}>
          <div style={{ display: 'flex', gap: 24 }}>
            {[
              ['BROKER', 'ALPACA · PAPER'],
              ['UNIVERSE', '124 SYMBOLS'],
              ['INTERVAL', '10 MIN'],
              ['STRATEGY', 'TREND-FOLLOWING'],
            ].map(([label, value]) => (
              <div key={label} style={{ display: 'flex', gap: 7, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)', letterSpacing: '0.04em' }}>
                <span>{label}</span>
                <strong style={{ color: 'var(--text-dim)', fontWeight: 500 }}>{value}</strong>
              </div>
            ))}
          </div>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)', letterSpacing: '0.04em' }}>
            ALGOSPHERE · v2.1.0 · BUILD 20260328
          </span>
        </footer>
      </div>
    </>
  )
}
