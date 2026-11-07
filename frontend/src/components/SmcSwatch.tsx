// A tiny sample of how an SMC zone is drawn on the chart (same shapes as overlay.ts, from smcStyle.ts), for the
// "on chart" chips, the pattern drawer and the key under the chart.
import type { CSSProperties } from 'react'
import { SMC_KEY, type SmcKind } from '../chart/smcStyle'

const colorOf = (kind: SmcKind, dir?: string) =>
  kind === 'ote' ? 'var(--amber)' : dir === 'bearish' ? 'var(--bear)' : dir === 'bullish' ? 'var(--bull)' : 'var(--neutral)'

export function SmcSwatch({ kind, dir, faded = false }: { kind: SmcKind; dir?: string; faded?: boolean }) {
  const c = colorOf(kind, dir)
  const tint = (pct: number) => `color-mix(in srgb, ${c} ${faded ? pct / 2 : pct}%, transparent)`
  const base: CSSProperties = { display: 'inline-block', width: 16, height: 10, flex: 'none', boxSizing: 'border-box', opacity: faded ? 0.7 : 1 }
  const style: CSSProperties =
    kind === 'ob' ? { ...base, background: tint(35), border: `1px solid ${c}`, borderLeft: `3px solid ${c}` }
    : kind === 'wob' ? { ...base, background: tint(18), border: `3px double ${c}` }
    : kind === 'fvg' ? { ...base, background: `repeating-linear-gradient(135deg, ${tint(80)} 0 1px, transparent 1px 3.5px)`,
                         borderTop: `1px dashed ${c}`, borderBottom: `1px dashed ${c}` }
    : { ...base, borderTop: `1px dotted ${c}`, borderBottom: `1px dotted ${c}` }
  return <span aria-hidden="true" style={style} />
}

/** One line under the chart: what each SMC shape means. */
export function SmcKey() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-2 py-1 text-[11px] text-muted" aria-label="SMC drawing key">
      <span className="label">SMC key</span>
      {SMC_KEY.map(k => (
        <span key={k.kind} className="inline-flex items-center gap-1"><SmcSwatch kind={k.kind} dir={k.kind === 'ote' ? undefined : 'bullish'} />{k.text}</span>
      ))}
      <span>green bullish · red bearish · fainter = already tested or partly filled · labels at the right end of each zone</span>
    </div>
  )
}
