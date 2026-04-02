import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useBotStatus } from '@/hooks/useBotStatus'
import { useRegime } from '@/hooks/useRegime'
import { useThemeStore, resolveTheme } from '@/store/themeStore'
import { useAuthStore } from '@/store/authStore'
import { UserSwitcher } from './UserSwitcher'

const STATUS_LABELS: Record<string, string> = {
  running: 'RUNNING',
  paused: 'PAUSED',
  stopped: 'STOPPED',
}

const STATUS_COLORS: Record<string, string> = {
  running: 'var(--green)',
  paused:  'var(--amber)',
  stopped: 'var(--red)',
}

const REGIME_COLORS: Record<string, string> = {
  bullish: 'var(--green)',
  neutral: 'var(--amber)',
  bearish: 'var(--red)',
}

function useEstClock() {
  const [time, setTime] = useState('')
  useEffect(() => {
    const tick = () => {
      const opts: Intl.DateTimeFormatOptions = {
        timeZone: 'America/New_York',
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }
      setTime(new Date().toLocaleTimeString('en-US', opts) + ' EST')
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return time
}

export function Header() {
  const { status, mode } = useBotStatus()
  const { regime } = useRegime()
  const { mode: themeMode, setMode: setThemeMode } = useThemeStore()
  const { userId, role, paper, logout } = useAuthStore()
  const navigate = useNavigate()
  const clock = useEstClock()
  const statusColor = STATUS_COLORS[status]

  const resolvedTheme = resolveTheme(themeMode)
  const mutedText = resolvedTheme === 'light' ? '#51607b' : 'var(--text-muted)'
  const navText = resolvedTheme === 'light' ? '#33415c' : 'var(--text-muted)'
  const headerSurface = resolvedTheme === 'light'
    ? 'linear-gradient(135deg, rgba(255,255,255,0.96), rgba(255,246,239,0.96))'
    : 'linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02))'

  function handleLogout() {
    logout()
    navigate('/login')
  }

  function cycleTheme() {
    if (themeMode === 'system') setThemeMode('dark')
    else if (themeMode === 'dark') setThemeMode('light')
    else setThemeMode('system')
  }

  const themeIcon = themeMode === 'system' ? '⊙' : resolvedTheme === 'dark' ? '◐' : '○'
  const themeLabel = themeMode === 'system' ? 'SYS' : themeMode === 'dark' ? 'DARK' : 'LITE'

  return (
    <header style={{
      position: 'relative',
      display: 'flex',
      alignItems: 'center',
      padding: '18px 20px 12px',
      gap: 0,
      zIndex: 10,
      maxWidth: 1540,
      width: '100%',
      margin: '0 auto',
    }}>
      <div style={{
        position: 'absolute',
        inset: '8px 20px 0',
        borderRadius: 28,
        background: headerSurface,
        border: resolvedTheme === 'light' ? '1px solid rgba(31,44,82,0.12)' : '1px solid rgba(255,255,255,0.08)',
        backdropFilter: resolvedTheme === 'light' ? 'none' : 'blur(18px)',
        boxShadow: 'var(--surface-shadow)',
        zIndex: -1,
      }} />
      <div style={{
        position: 'absolute',
        bottom: 0,
        left: 20,
        width: 260,
        height: 3,
        borderRadius: 999,
        background: 'linear-gradient(90deg, var(--amber) 0%, var(--violet) 100%)',
      }} />

      <div style={{
        fontFamily: 'var(--font-display)',
        fontSize: 24,
        letterSpacing: '-0.04em',
        color: 'var(--amber)',
        marginRight: 24,
        textShadow: '0 0 22px rgba(255,107,61,0.22)',
        lineHeight: 1,
        flexShrink: 0,
      }}>
        Algo<span style={{ color: 'var(--text-primary)' }}>Sphere</span>
      </div>

      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 7,
        fontFamily: 'var(--font-mono)',
        fontSize: 10,
        fontWeight: 500,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        padding: '7px 12px',
        border: `1px solid ${statusColor}33`,
        borderRadius: 999,
        background: `${statusColor}18`,
        color: statusColor,
        flexShrink: 0,
      }}>
        <span style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: statusColor,
          flexShrink: 0,
          animation: status === 'running' ? 'pulse-dot 2s ease-in-out infinite' : 'none',
        }} />
        {mode.toUpperCase()} · {STATUS_LABELS[status]}
      </div>

      <div style={{ width: 1, height: 26, background: 'rgba(255,255,255,0.08)', margin: '0 18px', flexShrink: 0 }} />

      <nav style={{ display: 'flex', gap: 6, marginRight: 18 }}>
        {[
          { to: '/dashboard', label: 'DASHBOARD' },
          { to: '/trades',    label: 'TRADES' },
          { to: '/rules',     label: 'RULES' },
          { to: '/settings',  label: 'SETTINGS' },
        ].map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            style={({ isActive }) => ({
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              padding: '7px 12px',
              color: isActive ? 'var(--text-primary)' : navText,
              background: isActive
                ? (resolvedTheme === 'light' ? 'rgba(23,32,51,0.08)' : 'rgba(255,255,255,0.08)')
                : 'transparent',
              border: `1px solid ${isActive
                ? (resolvedTheme === 'light' ? 'rgba(31,44,82,0.12)' : 'rgba(255,255,255,0.12)')
                : 'transparent'}`,
              borderRadius: 999,
              textDecoration: 'none',
              transition: 'all 0.15s',
            })}
          >
            {label}
          </NavLink>
        ))}
      </nav>

      <div style={{ width: 1, height: 26, background: 'rgba(255,255,255,0.08)', margin: '0 18px', flexShrink: 0 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.16em', textTransform: 'uppercase', color: mutedText }}>
          REGIME
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 10,
          fontWeight: 500,
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
          color: REGIME_COLORS[regime?.label ?? 'neutral'],
        }}>
          {(regime?.label ?? 'neutral').toUpperCase()}
        </span>
      </div>

      {role === 'admin' && (
        <>
          <div style={{ width: 1, height: 26, background: 'rgba(255,255,255,0.08)', margin: '0 12px', flexShrink: 0 }} />
          <UserSwitcher />
        </>
      )}

      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 14, flexShrink: 0 }}>

        <button
          onClick={cycleTheme}
          title={`Theme: ${themeMode} — click to cycle`}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            padding: '7px 10px',
            border: '1px solid rgba(255,255,255,0.12)',
            background: 'rgba(255,255,255,0.04)',
            borderRadius: 999,
            color: mutedText,
            cursor: 'pointer',
            transition: 'all 0.15s',
            flexShrink: 0,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = 'var(--amber)'
            e.currentTarget.style.color = 'var(--amber)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = resolvedTheme === 'light' ? 'rgba(31,44,82,0.12)' : 'var(--border)'
            e.currentTarget.style.color = mutedText
          }}
        >
          <span style={{ fontSize: 11 }}>{themeIcon}</span>
          {themeLabel}
        </button>

        {userId && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.05em',
              textTransform: 'uppercase', color: 'var(--text-dim)',
              padding: '7px 10px', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 999,
              background: 'rgba(255,255,255,0.05)',
            }}>
              {role === 'admin' ? '★ ' : ''}{userId}{paper ? ' · PAPER' : ' · LIVE'}
            </div>
            <button
              onClick={handleLogout}
              title="Sign out"
              style={{
                fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.08em',
                textTransform: 'uppercase', padding: '7px 10px',
                border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.04)', borderRadius: 999,
                color: mutedText, cursor: 'pointer',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--red)'; e.currentTarget.style.color = 'var(--red)' }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = resolvedTheme === 'light' ? 'rgba(31,44,82,0.12)' : 'var(--border)'; e.currentTarget.style.color = mutedText }}
            >
              OUT
            </button>
          </div>
        )}

        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-dim)', letterSpacing: '0.06em', minWidth: 88, textAlign: 'right' }}>
          {clock}
        </div>
      </div>
    </header>
  )
}
