import { useEffect, useState } from 'react'
import { api, type Regime } from '../api'

const STYLE: Record<string, string> = {
  risk_on: 'border-bull/60 text-bull', risk_off: 'border-bear/60 text-bear', mixed: 'border-amber/60 text-amber', unknown: 'border-line text-muted',
}

/** Market read from the ASPI vs its 100 / 200-day averages and breadth. Description only: no engine uses it. */
export default function RegimeBanner() {
  const [r, setR] = useState<Regime | null>(null)
  useEffect(() => { api.regime().then(setR).catch(() => {}) }, [])
  if (!r || r.state === 'unknown') return null
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 border border-line bg-panel px-3 py-1.5 text-[12px]" role="status" aria-label="Market regime">
      <span className={`border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${STYLE[r.state]}`}>{r.label}</span>
      <span className="text-fg">{r.facts.join(' · ')}</span>
      <span className="ml-auto text-[11px] text-muted" title={r.note}>description only, not a trading signal</span>
    </div>
  )
}
