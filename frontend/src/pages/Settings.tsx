import { useState, useEffect } from 'react'
import { api, updateCredentials } from '@/lib/api'
import { useAuthStore } from '@/store/authStore'

const RISK_PROFILES = ['conservative', 'balanced', 'aggressive']

export function Settings() {
  const userId = useAuthStore((s) => s.userId)

  const [alpacaKey, setAlpacaKey] = useState('')
  const [alpacaSecret, setAlpacaSecret] = useState('')
  const [paper, setPaper] = useState(true)
  const [riskProfile, setRiskProfile] = useState('balanced')
  const [hasCredentials, setHasCredentials] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!userId) { setLoading(false); return }
    api.accountSettings(userId).then((s) => {
      if (!s) return
      setPaper(s.paper)
      setRiskProfile(s.risk_profile)
      setHasCredentials(s.has_credentials)
    }).finally(() => setLoading(false))
  }, [userId])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!alpacaKey || !alpacaSecret) {
      setError('Both Alpaca API key and secret are required.')
      return
    }
    setSaving(true)
    setError('')
    setSuccess(false)
    try {
      await updateCredentials(userId!, {
        alpaca_key: alpacaKey,
        alpaca_secret: alpacaSecret,
        paper,
        risk_profile: riskProfile,
      })
      setAlpacaKey('')
      setAlpacaSecret('')
      setHasCredentials(true)
      setSuccess(true)
    } catch {
      setError('Failed to save settings. Please check your credentials.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div style={{ padding: 40, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', fontSize: 12, letterSpacing: '0.1em' }}>
        LOADING...
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 520, margin: '0 auto', padding: '32px 16px' }}>
      {/* Header */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: 20, letterSpacing: '0.12em', color: 'var(--amber)' }}>
          BROKER SETTINGS
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.18em', color: 'var(--text-muted)', marginTop: 4, textTransform: 'uppercase' }}>
          Alpaca API credentials · trading mode · risk profile
        </div>
      </div>

      {/* Credential status badge */}
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.1em',
        padding: '4px 10px', marginBottom: 24,
        border: `1px solid ${hasCredentials ? 'rgba(0,200,100,0.3)' : 'rgba(255,179,0,0.3)'}`,
        background: hasCredentials ? 'rgba(0,200,100,0.05)' : 'rgba(255,179,0,0.05)',
        color: hasCredentials ? 'var(--green, #00c864)' : 'var(--amber)',
      }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: hasCredentials ? '#00c864' : 'var(--amber)', display: 'inline-block' }} />
        {hasCredentials ? 'CREDENTIALS CONFIGURED' : 'NO CREDENTIALS SET'}
      </div>

      <form onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* Alpaca keys section */}
        <Section title="ALPACA API CREDENTIALS">
          <Field
            label="API KEY"
            type="text"
            value={alpacaKey}
            onChange={setAlpacaKey}
            placeholder={hasCredentials ? '••••••••  (enter to update)' : 'Enter your Alpaca API key'}
          />
          <Field
            label="SECRET KEY"
            type="password"
            value={alpacaSecret}
            onChange={setAlpacaSecret}
            placeholder={hasCredentials ? '••••••••  (enter to update)' : 'Enter your Alpaca secret key'}
          />
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-muted)', letterSpacing: '0.08em', lineHeight: 1.6 }}>
            Get your keys at alpaca.markets → Account → API Keys.
            Paper and live trading use separate key pairs.
          </div>
        </Section>

        {/* Trading mode */}
        <Section title="TRADING MODE">
          <div style={{ display: 'flex', gap: 10 }}>
            {[true, false].map((isPaper) => (
              <button
                key={String(isPaper)}
                type="button"
                onClick={() => setPaper(isPaper)}
                style={{
                  flex: 1, padding: '10px 0',
                  fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.12em',
                  border: `1px solid ${paper === isPaper ? 'rgba(255,179,0,0.5)' : 'var(--border)'}`,
                  background: paper === isPaper ? 'var(--amber-mid)' : 'var(--bg-panel-alt)',
                  color: paper === isPaper ? 'var(--amber)' : 'var(--text-muted)',
                  cursor: 'pointer', transition: 'all 0.15s',
                }}
              >
                {isPaper ? 'PAPER' : 'LIVE'}
              </button>
            ))}
          </div>
          {!paper && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--red, #ff3b5c)', letterSpacing: '0.08em', padding: '6px 10px', border: '1px solid rgba(255,59,92,0.25)', background: 'rgba(255,59,92,0.05)' }}>
              ⚠ LIVE mode uses real money. Make sure you are using live API keys.
            </div>
          )}
        </Section>

        {/* Risk profile */}
        <Section title="RISK PROFILE">
          <div style={{ display: 'flex', gap: 10 }}>
            {RISK_PROFILES.map((rp) => (
              <button
                key={rp}
                type="button"
                onClick={() => setRiskProfile(rp)}
                style={{
                  flex: 1, padding: '10px 0',
                  fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase',
                  border: `1px solid ${riskProfile === rp ? 'rgba(255,179,0,0.5)' : 'var(--border)'}`,
                  background: riskProfile === rp ? 'var(--amber-mid)' : 'var(--bg-panel-alt)',
                  color: riskProfile === rp ? 'var(--amber)' : 'var(--text-muted)',
                  cursor: 'pointer', transition: 'all 0.15s',
                }}
              >
                {rp}
              </button>
            ))}
          </div>
        </Section>

        {/* Feedback */}
        {error && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--red, #ff3b5c)', letterSpacing: '0.08em', padding: '8px 12px', border: '1px solid rgba(255,59,92,0.25)', background: 'rgba(255,59,92,0.05)' }}>
            {error}
          </div>
        )}
        {success && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: '#00c864', letterSpacing: '0.08em', padding: '8px 12px', border: '1px solid rgba(0,200,100,0.25)', background: 'rgba(0,200,100,0.05)' }}>
            SETTINGS SAVED — worker will reload credentials on next cycle.
          </div>
        )}

        <button
          type="submit"
          disabled={saving}
          style={{
            fontFamily: 'var(--font-display)', fontSize: 16, letterSpacing: '0.14em',
            padding: '12px', background: 'var(--amber-mid)',
            border: '1px solid rgba(255,179,0,0.4)', color: 'var(--amber)',
            cursor: saving ? 'not-allowed' : 'pointer', opacity: saving ? 0.6 : 1,
            transition: 'all 0.15s',
          }}
        >
          {saving ? 'SAVING...' : 'SAVE SETTINGS'}
        </button>
      </form>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.2em',
        textTransform: 'uppercase', color: 'var(--text-muted)',
        borderBottom: '1px solid var(--border)', paddingBottom: 6,
      }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function Field({ label, type, value, onChange, placeholder }: {
  label: string; type: string; value: string
  onChange: (v: string) => void; placeholder: string
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <label style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        style={{
          background: 'var(--bg-panel-alt)',
          border: '1px solid var(--border)',
          color: 'var(--text-primary)',
          fontFamily: 'var(--font-mono)',
          fontSize: 13,
          padding: '9px 12px',
          outline: 'none',
          width: '100%',
          letterSpacing: '0.04em',
          boxSizing: 'border-box',
        }}
      />
    </div>
  )
}
