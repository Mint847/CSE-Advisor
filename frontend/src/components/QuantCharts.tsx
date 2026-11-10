// Charts for the QNT tab. Time series use lightweight-charts (crosshair + a live value legend on hover); the bar charts
// and tables are plain SVG / HTML with a hover tooltip. Colour rules (dataviz): one axis per chart; a legend whenever
// there are two series; red/green only for polarity (gain vs loss) and always with the number written beside it.
import { useEffect, useRef, useState } from 'react'
import { AreaSeries, ColorType, LineSeries, LineStyle, createChart, type IChartApi, type Time } from 'lightweight-charts'
import type { QPoint, QuantReport } from '../api'

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()
export const pctf = (v: number | null | undefined, nd = 1, sign = true) => v == null || !Number.isFinite(v) ? '–' : `${sign && v > 0 ? '+' : ''}${(v * 100).toFixed(nd)}%`
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** A diverging fill for a return: green up / red down, mixed into the panel; full strength at `full` (e.g. 10%). */
export const heatFill = (v: number | null | undefined, full = 0.1) => {
  if (v == null || !Number.isFinite(v)) return 'transparent'
  const k = Math.min(Math.abs(v) / full, 1) * 50
  return `color-mix(in srgb, var(${v >= 0 ? '--bull' : '--bear'}) ${k.toFixed(0)}%, var(--panel-3))`
}

export interface TSeries { label: string; color: string; points: QPoint[]; area?: boolean; dashed?: boolean }

/** A small time-series chart with a hover legend. `fmt` formats values (e.g. as %). */
export function TimeChart({ series, refLines = [], fmt, height = 180, title }: {
  series: TSeries[]; refLines?: { value: number; label: string }[]; fmt: (v: number) => string; height?: number; title: string
}) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const [hover, setHover] = useState<{ time: string; values: (number | null)[] } | null>(null)
  useEffect(() => {
    const el = box.current!
    const c = createChart(el, {
      autoSize: true, layout: { background: { type: ColorType.Solid, color: css('--bg') || '#000' }, textColor: css('--muted'), fontSize: 10,
        fontFamily: '"IBM Plex Mono", monospace', attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: css('--chart-grid') } },
      rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false },
      crosshair: { horzLine: { labelVisible: false } },
    })
    chart.current = c
    const made = series.map(s => {
      const opts = { color: s.color, lineWidth: 2 as const, priceLineVisible: false, lastValueVisible: false, lineStyle: s.dashed ? LineStyle.Dashed : LineStyle.Solid,
        priceFormat: { type: 'custom' as const, formatter: fmt, minMove: 0.0001 } }
      const se = s.area ? c.addSeries(AreaSeries, { ...opts, lineColor: s.color, topColor: 'rgba(0,0,0,0)', bottomColor: s.color + '55', invertFilledArea: true })
        : c.addSeries(LineSeries, opts)
      se.setData(s.points.map(p => ({ time: p.time as Time, value: p.value })))
      return se
    })
    if (made[0]) for (const r of refLines) made[0].createPriceLine({ price: r.value, color: css('--muted'), lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: true, title: r.label })
    c.timeScale().fitContent()
    c.subscribeCrosshairMove(p => {
      if (!p.time) { setHover(null); return }
      setHover({ time: String(p.time), values: made.map(se => { const d = p.seriesData.get(se) as { value?: number } | undefined; return d?.value ?? null }) })
    })
    return () => { c.remove(); chart.current = null }
  }, [series, refLines, fmt])
  const last = series.map(s => s.points.at(-1)?.value ?? null)
  const shown = hover ?? { time: series[0]?.points.at(-1)?.time ?? '', values: last }
  return (
    <figure className="min-w-0" aria-label={title}>
      <figcaption className="flex flex-wrap items-baseline gap-x-3 px-1 text-[11px]">
        <span className="label">{title}</span>
        <span className="text-muted num">{shown.time}</span>
        {series.map((s, i) => (
          <span key={s.label} className="flex items-center gap-1 num">
            <svg width="14" height="4" aria-hidden="true"><line x1="0" y1="2" x2="14" y2="2" stroke={s.color} strokeWidth="2" strokeDasharray={s.dashed ? '3 2' : undefined} /></svg>
            {series.length > 1 && <span className="text-muted">{s.label}</span>}
            <b className="text-fg">{shown.values[i] == null ? '–' : fmt(shown.values[i]!)}</b>
          </span>
        ))}
      </figcaption>
      <div ref={box} style={{ height }} />
    </figure>
  )
}

/** Daily returns histogram (SVG). Bars below the 5% VaR line are drawn in the loss colour. */
export function Histogram({ h }: { h: QuantReport['charts']['histogram'] }) {
  const [hi, setHi] = useState<number | null>(null)
  if (!h.bins.length) return <p className="text-[12px] text-muted">Not enough days for a histogram.</p>
  const W = 520, H = 160, max = Math.max(...h.bins.map(b => b.count)), bw = W / h.bins.length
  const lo = h.bins[0].from, hiX = h.bins.at(-1)!.to, x = (v: number) => ((v - lo) / (hiX - lo)) * W
  return (
    <figure className="min-w-0" aria-label="Distribution of daily returns">
      <figcaption className="flex flex-wrap gap-x-3 px-1 text-[11px]"><span className="label">How big the daily moves were</span>
        <span className="text-muted">{h.n} days · mean {pctf(h.mean, 2)} · 1 day in 20 below <b className="text-bear">{pctf(h.var95, 2)}</b></span>
        {hi != null && <span className="num text-fg">{pctf(h.bins[hi].from, 2)} to {pctf(h.bins[hi].to, 2)}: <b>{h.bins[hi].count}</b> days</span>}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H + 16}`} className="h-auto w-full" role="img" onMouseLeave={() => setHi(null)}>
        {h.bins.map((b, i) => {
          const bh = (b.count / max) * H
          return <rect key={i} x={i * bw + 1} y={H - bh} width={Math.max(1, bw - 2)} height={bh} rx={1}
                       fill={b.to <= (h.var95 ?? -1) ? 'var(--bear)' : 'var(--muted)'} opacity={hi == null || hi === i ? 0.9 : 0.45}
                       onMouseEnter={() => setHi(i)}><title>{`${pctf(b.from, 2)} to ${pctf(b.to, 2)}: ${b.count} days`}</title></rect>
        })}
        {h.var95 != null && <><line x1={x(h.var95)} x2={x(h.var95)} y1={0} y2={H} stroke="var(--bear)" strokeDasharray="3 2" />
          <text x={x(h.var95) + 3} y={10} fontSize="10" fill="var(--text)">VaR 95%</text></>}
        <line x1={x(0)} x2={x(0)} y1={0} y2={H} stroke="var(--text)" strokeOpacity="0.4" />
        <text x={0} y={H + 12} fontSize="10" fill="var(--muted)">{pctf(lo, 1)}</text>
        <text x={x(0) - 4} y={H + 12} fontSize="10" fill="var(--muted)">0</text>
        <text x={W} y={H + 12} fontSize="10" fill="var(--muted)" textAnchor="end">{pctf(hiX, 1)}</text>
      </svg>
    </figure>
  )
}

/** Calendar of monthly returns (rows = years) with each year's total; colour = gain / loss, with the number written. */
export function MonthlyTable({ m }: { m: QuantReport['charts']['monthly'] }) {
  if (!m.years.length) return null
  return (
    <div className="scroll-thin min-w-0 overflow-x-auto">
      <table className="bb-table num text-[11px]" aria-label="Monthly returns">
        <thead><tr><th>Year</th>{MONTHS.map(x => <th key={x} className="text-right">{x}</th>)}<th className="text-right">Year</th></tr></thead>
        <tbody>
          {m.years.slice().reverse().map(y => (
            <tr key={y.year}>
              <td className="text-amber">{y.year}{y.partial ? '*' : ''}</td>
              {MONTHS.map((_, i) => { const v = y.months[String(i + 1)]; return <td key={i} className="text-right" style={{ background: heatFill(v) }} title={v == null ? '' : `${MONTHS[i]} ${y.year}: ${pctf(v)}`}>{v == null ? '' : pctf(v, 1, false)}</td> })}
              <td className="text-right font-semibold" style={{ background: heatFill(y.year_return, 0.4) }}>{pctf(y.year_return, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="px-1 pt-1 text-[10px] text-muted">* part of the year only. Each cell is that month's change.</p>
    </div>
  )
}

/** Average return of each calendar month across the years in the window (bars), with how many years and how often up. */
export function SeasonalBars({ s }: { s: QuantReport['charts']['monthly']['seasonal'] }) {
  const [hi, setHi] = useState<number | null>(null)
  const vals = s.map(x => x.average ?? 0)
  const max = Math.max(0.01, ...vals.map(Math.abs))
  const W = 520, H = 140, mid = H / 2, bw = W / 12
  const cur = hi != null ? s[hi] : null
  return (
    <figure className="min-w-0" aria-label="Average return by calendar month">
      <figcaption className="flex flex-wrap gap-x-3 px-1 text-[11px]"><span className="label">Average by calendar month</span>
        {cur ? <span className="num text-fg">{MONTHS[cur.month - 1]}: <b>{pctf(cur.average, 2)}</b> · up in {cur.positive == null ? '–' : `${Math.round(cur.positive * 100)}%`} of {cur.years} years{cur.t != null ? ` · t ${cur.t}` : ''}</span>
          : <span className="text-muted">hover a month</span>}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H + 14}`} className="h-auto w-full" role="img" onMouseLeave={() => setHi(null)}>
        <line x1={0} x2={W} y1={mid} y2={mid} stroke="var(--text)" strokeOpacity="0.3" />
        {s.map((x, i) => {
          const v = x.average ?? 0, bh = (Math.abs(v) / max) * (mid - 6)
          return <g key={i} onMouseEnter={() => setHi(i)}>
            <rect x={i * bw} y={0} width={bw} height={H} fill="transparent" />
            <rect x={i * bw + 4} y={v >= 0 ? mid - bh : mid} width={bw - 8} height={Math.max(1, bh)} rx={2} fill={v >= 0 ? 'var(--bull)' : 'var(--bear)'} opacity={hi == null || hi === i ? 0.9 : 0.5}>
              <title>{`${MONTHS[i]}: ${pctf(x.average, 2)} average over ${x.years} years`}</title></rect>
            <text x={i * bw + bw / 2} y={H + 11} fontSize="10" textAnchor="middle" fill="var(--muted)">{MONTHS[i]}</text>
          </g>
        })}
      </svg>
    </figure>
  )
}
