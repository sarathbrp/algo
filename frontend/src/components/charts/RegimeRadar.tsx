import type { RegimeScores } from '@/types'

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

export function RegimeRadar({ regime }: { regime: RegimeScores }) {
  // spy/qqq scores are (close - MA) / MA, typically -0.15 to +0.15; scale by 10x for radar
  const spyY = 40 - clamp(regime.spy * 10, -1, 1) * 18
  const qqqX = 40 + clamp(regime.qqq * 10, -1, 1) * 18
  // vix is the raw level (e.g. 25); divide by 40 to normalize
  const vixY = 58 - clamp(regime.vix / 40, 0, 1.5) * 18

  return (
    <svg
      width={80}
      height={80}
      viewBox="0 0 80 80"
      aria-label="Market regime radar"
    >
      <defs>
        <radialGradient id="radar-grad" cx="50%" cy="50%">
          <stop offset="0%" stopColor="#FFB300" stopOpacity={0.08} />
          <stop offset="100%" stopColor="#FFB300" stopOpacity={0} />
        </radialGradient>
      </defs>

      {/* Rings */}
      {[36, 24, 12].map((r) => (
        <circle key={r} cx={40} cy={40} r={r} fill="none" stroke="#FFB30020" strokeWidth={0.5} />
      ))}

      {/* Crosshairs */}
      <line x1={4}  y1={40} x2={76} y2={40} stroke="#FFB30015" strokeWidth={0.5} />
      <line x1={40} y1={4}  x2={40} y2={76} stroke="#FFB30015" strokeWidth={0.5} />

      {/* Sweep */}
      <g style={{ transformOrigin: '40px 40px', animation: 'radar-rotate 4s linear infinite' }}>
        <path d="M40,40 L40,4 A36,36 0 0,1 76,40 Z" fill="url(#radar-grad)" />
        <line x1={40} y1={40} x2={40} y2={4} stroke="#FFB300" strokeWidth={0.8} opacity={0.5} />
      </g>

      {/* Blips */}
      <circle cx={26} cy={spyY} r={2.5} fill="#00D48B" style={{ animation: 'blip-fade 4s ease-in-out 0.5s infinite' }} />
      <circle cx={qqqX} cy={24} r={1.8} fill="#00D48B" style={{ animation: 'blip-fade 4s ease-in-out 1.2s infinite' }} />
      <circle cx={56} cy={vixY} r={2.0} fill="#FFB300" style={{ animation: 'blip-fade 4s ease-in-out 2.1s infinite' }} />
    </svg>
  )
}
