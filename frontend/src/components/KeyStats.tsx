import { useEffect, useState } from 'react'
import { api, fmt, fmtBig, type KeyStats } from '../api'

function Cell({ label, children, title }: { label: string; children: React.ReactNode; title?: string }) {
  return <div className="min-w-0 border-l border-line px-2.5 py-1 first:border-l-0" title={title}><div className="label !text-[10px] text-muted">{label}</div><div className="num text-[12px] text-fg">{children}</div></div>
}

/** The CSE's own summary for a stock: ranges, market cap, foreign holding, volume; plus its sector. For a tracked stock or any listed one. */
export function KeyStatsStrip({ k }: { k: KeyStats }) {
  const range = (lo: number | null, hi: number | null) => lo == null || hi == null ? '–' : `${fmt(lo)} – ${fmt(hi)}`
  return (
    <div className="grid grid-cols-2 gap-y-px border-y border-line bg-panel sm:grid-cols-3 lg:grid-cols-6 [&>*]:min-w-0" aria-label="Key statistics">
      <Cell label="Day range">{range(k.day_low, k.day_high)}</Cell>
      <Cell label="52-week range" title="Low – high over the last 12 months">
        {range(k.lo_52w, k.hi_52w)}
        {k.pos_52w != null && <span className="mt-0.5 block h-1 w-full bg-panel-3" title={`${k.pos_52w}% of the way from the 52-week low to the high`}><span className="block h-1 bg-amber" style={{ width: `${Math.max(2, Math.min(100, k.pos_52w))}%` }} /></span>}
      </Cell>
      <Cell label="From 52-wk high" title="Distance below the 12-month high">{k.pct_from_high == null ? '–' : <span className={k.pct_from_high >= -5 ? 'text-bull' : k.pct_from_high < -25 ? 'text-bear' : ''}>{k.pct_from_high.toFixed(1)}%</span>}</Cell>
      <Cell label="Year to date">{range(k.lo_ytd, k.hi_ytd)}</Cell>
      <Cell label="All-time" title="Lowest and highest price since listing (as the CSE records it, not adjusted for rights or splits)">{range(k.lo_all, k.hi_all)}</Cell>
      <Cell label="Market cap">{k.market_cap == null ? '–' : `LKR ${fmtBig(k.market_cap)}`}{k.market_cap_pct != null && <span className="text-[10px] text-muted"> {k.market_cap_pct.toFixed(2)}% of market</span>}</Cell>
      <Cell label="Foreign holding">{k.foreign_pct == null ? '–' : `${k.foreign_pct.toFixed(1)}%`}</Cell>
      <Cell label="Volume YTD">{fmtBig(k.volume_ytd)}</Cell>
      <Cell label="Shares issued">{fmtBig(k.shares_issued)}</Cell>
      <Cell label="Sector">{k.sector ?? '–'}</Cell>
      <Cell label="Industry"><span className="block truncate" title={k.industry ?? ''}>{k.industry ?? '–'}</span></Cell>
      <Cell label="ISIN">{k.isin ?? '–'}</Cell>
    </div>
  )
}

/** Loads the stats for one stock (re-read when the symbol changes). */
export function StockStats({ symbol, price }: { symbol: string; price?: number | null }) {
  const [k, setK] = useState<KeyStats | null>(null)
  const p = price == null ? null : Math.round(price * 100) / 100
  useEffect(() => { setK(null); api.stats(symbol, p).then(setK).catch(() => {}) }, [symbol, p])
  return k ? <KeyStatsStrip k={k} /> : null
}
