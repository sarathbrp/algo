import { useMemo } from 'react'

const WIDTH = 320
const HEIGHT = 48

function buildPath(points: number[]) {
  if (points.length < 2) return { line: '', fill: '' }
  const step = WIDTH / (points.length - 1)
  const coords = points.map((y, i) => `${i * step},${y}`)
  const line = `M${coords.join(' L')}`
  const lastX = (points.length - 1) * step
  const fill = `${line} L${lastX},${HEIGHT} L0,${HEIGHT} Z`
  return { line, fill, lastX, lastY: points[points.length - 1] }
}

function normalizePoints(points: number[]): number[] {
  if (points.length <= 1) return [HEIGHT / 2, HEIGHT / 2]
  const min = Math.min(...points)
  const max = Math.max(...points)
  if (min === max) return points.map(() => HEIGHT / 2)
  const range = max - min
  return points.map((point) => {
    const pct = (point - min) / range
    return HEIGHT - 4 - pct * (HEIGHT - 8)
  })
}

export function Sparkline({ points }: { points: number[] }) {
  const normalized = useMemo(() => normalizePoints(points), [points])
  const { line, fill, lastX = WIDTH, lastY = HEIGHT / 2 } = buildPath(normalized)

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="none"
      style={{ width: '100%', height: HEIGHT, overflow: 'visible' }}
      aria-label="Equity sparkline"
    >
      <defs>
        <linearGradient id="spark-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#FFB300" stopOpacity={0.25} />
          <stop offset="100%" stopColor="#FFB300" stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={fill} fill="url(#spark-grad)" stroke="none" />
      <path
        d={line}
        fill="none"
        stroke="#FFB300"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
      <circle cx={lastX} cy={lastY} r={3} fill="#FFB300" opacity={0.9}>
        <animate attributeName="r" values="3;5;3" dur="2s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.9;0.4;0.9" dur="2s" repeatCount="indefinite" />
      </circle>
    </svg>
  )
}
