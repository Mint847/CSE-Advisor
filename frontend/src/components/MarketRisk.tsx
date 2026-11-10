// MKT: how jumpy the ASPI is now against its own past, how far below its high, and how closely it follows Wall Street and the rupee
// (gs-quant maths). Measurements only; nothing here changes an engine's verdict.
import { useEffect, useMemo, useState } from 'react'
import { api, type MarketRisk as MR } from '../api'
import { TimeChart, pctf, type TSeries } from './QuantCharts'
import { Section, Spinner } from './ui'

const NO_LINES: { value: number; label: string }[] = []
const fmtPct = (v: number) => pctf(v, 1, false)
const fmt2 = (v: number) => v.toFixed(2)
const REGIME: Record<string, { icon: string; cls: string; text: string }> = {
  calm: { icon: '◔', cls: 'text-bull', text: 'calmer than most of the past' },
  normal: { icon: '◑', cls: 'text-fg', text: 'about as jumpy as usual' },
  stormy: { icon: '●', cls: 'text-bear', text: 'jumpier than almost all of the past' },
}

export default function MarketRisk() {
  const [d, setD] = useState<MR | null>(null)
  useEffect(() => { api.marketRisk().then(setD).catch(() => setD({ available: false, reason: 'The risk figures could not be worked out.' })) }, [])
  const v = d?.volatility, dd = d?.drawdown, sp = d?.correlation?.spx
  const vol = useMemo<TSeries[] | null>(() => v ? [{ label: 'Volatility (3 months)', color: '#ffa028', points: v.points }] : null, [v])
  const down = useMemo<TSeries[] | null>(() => dd ? [{ label: 'Below its high', color: '#ff433d', points: dd.points, area: true }] : null, [dd])
  const cor = useMemo<TSeries[] | null>(() => sp ? [{ label: `vs ${sp.label}`, color: '#2f8fd6', points: sp.points }] : null, [sp])
  const volLines = useMemo(() => v ? [{ value: v.median, label: 'typical' }] : NO_LINES, [v])
  if (!d) return <Section title="ASPI risk"><Spinner label="Working out the risk figures…" /></Section>
  if (!d.available || !v || !dd || !vol || !down) return <Section title="ASPI risk"><p className="text-sm text-muted">{d.reason}</p></Section>
  const r = REGIME[v.regime]
  return (
    <Section title={<>ASPI risk <span className="font-normal normal-case text-muted">· as of {d.as_of} · gs-quant maths</span></>}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 [&>*]:min-w-0">
        <div className="border border-line bg-panel px-2.5 py-1.5"><div className="label">Volatility, {v.window} sessions</div><div className="text-[15px] font-semibold num text-fg">{pctf(v.now, 1, false)}</div><div className="text-[11px] text-muted">typical {pctf(v.median, 1, false)}</div></div>
        <div className="border border-line bg-panel px-2.5 py-1.5"><div className="label">Market mood</div><div className={`text-[15px] font-semibold uppercase ${r.cls}`}><span aria-hidden="true">{r.icon} </span>{v.regime}</div>
          <div className="text-[11px] text-muted" title="Higher than this share of all past 3-month volatility readings">{r.text} ({Math.round(v.percentile)}th percentile)</div></div>
        <div className="border border-line bg-panel px-2.5 py-1.5"><div className="label">Below its high</div><div className={`text-[15px] font-semibold num ${dd.now < -0.1 ? 'text-bear' : 'text-fg'}`}>{pctf(dd.now, 1, false)}</div><div className="text-[11px] text-muted">worst fall in a year {pctf(dd.max_1y, 1, false)}</div></div>
        <div className="border border-line bg-panel px-2.5 py-1.5"><div className="label">Follows Wall Street</div><div className="text-[15px] font-semibold num text-fg">{sp ? sp.now.toFixed(2) : '–'}</div><div className="text-[11px] text-muted">{sp ? `correlation, 120 sessions (all history ${sp.all.toFixed(2)})` : 'no S&P 500 history'}</div></div>
      </div>
      <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[12px]" aria-label="In plain words">{d.summary!.map(x => <li key={x}>{x}</li>)}</ul>
      <div className="mt-2 grid gap-2 lg:grid-cols-3 [&>*]:min-w-0">
        <div className="card p-2"><TimeChart title="Volatility, rolling 3 months" series={vol} refLines={volLines} fmt={fmtPct} /></div>
        <div className="card p-2"><TimeChart title="Below its previous high" series={down} refLines={NO_LINES} fmt={fmtPct} /></div>
        {cor && <div className="card p-2"><TimeChart title="Correlation with the S&P 500 (evening before), rolling 120 sessions" series={cor} refLines={NO_LINES} fmt={fmt2} /></div>}
      </div>
    </Section>
  )
}
