// QNT tab: risk and return figures for one stock, computed with Goldman Sachs' gs-quant maths (offline parts only).
// Measurements only: nothing here changes an engine's verdict.
import { useEffect, useMemo, useState } from 'react'
import { api, type QuantReport } from '../api'
import { GLOSSARY } from '../quant/glossary'
import { Histogram, MonthlyTable, SeasonalBars, TimeChart, pctf, type TSeries } from './QuantCharts'
import { FigureGroups } from './QuantFigures'
import { ErrorBox, Spinner } from './ui'

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()

export default function QuantTab({ symbol, world = false }: { symbol: string; world?: boolean }) {
  const [win, setWin] = useState('3y')
  const [basis, setBasis] = useState<'total' | 'price'>('total')
  const [bench, setBench] = useState<string | undefined>(undefined)
  const [d, setD] = useState<QuantReport | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => { setBench(undefined) }, [symbol])
  useEffect(() => {
    let live = true
    setLoading(true); setErr(null)
    api.quant(symbol, world, win, basis, bench).then(x => { if (live) setD(x) }).catch(e => { if (live) setErr(String(e.message ?? e)) }).finally(() => live && setLoading(false))
    return () => { live = false }
  }, [symbol, world, win, basis, bench])

  const colors = useMemo(() => ({ amber: css('--amber') || '#ffa028', bear: css('--bear') || '#ff433d', blue: css('--blue') || '#5aa9ff', ink: '#e6e6e6' }), [])
  const charts = useMemo(() => {
    if (!d) return null
    const c = d.charts
    const s = (label: string, color: string, points = [] as typeof c.drawdown, more: Partial<TSeries> = {}): TSeries[] => [{ label, color, points, ...more }]
    return {
      dd: s('Below the high', colors.bear, c.drawdown, { area: true }),
      vol: c.rolling_vol ? s('Volatility (3 months)', colors.amber, c.rolling_vol) : null,
      beta: c.rolling_beta ? s('Beta (6 months)', colors.amber, c.rolling_beta) : null,
      corr: c.rolling_corr ? s('Correlation (6 months)', colors.amber, c.rolling_corr) : null,
      z: c.zscore ? s('z-score', colors.amber, c.zscore) : null,
      dec: c.decomposition.available ? [{ label: 'Price', color: colors.ink, points: c.decomposition.price! }, { label: 'Long-run trend', color: colors.amber, points: c.decomposition.trend!, dashed: true }] : null,
    }
  }, [d, colors])
  const zLines = useMemo(() => [{ value: 2, label: '+2' }, { value: 0, label: 'avg' }, { value: -2, label: '-2' }], [])
  const noLines = useMemo(() => [], [])
  const fmtPct = useMemo(() => (v: number) => pctf(v, 1, false), [])
  const fmtRatio = useMemo(() => (v: number) => v.toFixed(2), [])
  const fmtPrice = useMemo(() => (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 }), [])

  if (err) return <ErrorBox msg={err} />
  if (!d || !charts) return <Spinner label="Working out the figures…" />
  const m = d.meta, f = d.figures, r = d.relative
  const reg = d.charts.regression

  return (
    <div className={`space-y-3 ${loading ? 'opacity-60' : ''}`}>
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="flex" role="radiogroup" aria-label="Window">
          {(['1y', '3y', '5y', 'all'] as const).map(w => <button key={w} role="radio" aria-checked={win === w} className="btn !px-2 !py-0.5" onClick={() => setWin(w)}>{w === 'all' ? 'All' : w.toUpperCase()}</button>)}
        </span>
        {!world && (
          <span className="flex" role="radiogroup" aria-label="Return basis">
            {([['total', 'Total return'], ['price', 'Price only']] as const).map(([k, l]) => <button key={k} role="radio" aria-checked={basis === k} className="btn !px-2 !py-0.5" onClick={() => setBasis(k)}>{l}</button>)}
          </span>
        )}
        <label className="flex items-center gap-1"><span className="label">vs</span>
          <select className="input !py-0.5 text-[11px]" value={bench ?? m.bench.id} onChange={e => setBench(e.target.value)} aria-label="Benchmark">
            {m.benchmarks.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
        </label>
        <span className="text-muted">{m.start} → {m.end} · {f.sessions as number} sessions</span>
      </div>

      <div className="flex flex-wrap gap-1.5 text-[11px]">
        <span className="chip bg-panel-3 !h-auto !whitespace-normal !normal-case !font-normal text-muted" title={m.rate.source}>Risk-free: <b className="ml-1 text-fg">{m.rate.rate_pct.toFixed(2)}%</b>{m.rate.as_of ? ` (${m.rate.as_of})` : ''}{m.rate.stale ? ' · stale' : ''}</span>
        <span className="chip bg-panel-3 !normal-case !font-normal text-muted" title={GLOSSARY.sessions_per_year.what}>{m.sessions_per_year} sessions a year</span>
        {m.kind === 'cse' && <span className={'chip bg-panel-3 !h-auto !whitespace-normal !normal-case !font-normal text-muted'}
          title="Cash dividends recorded by the app are added back on the ex-dividend date">
          {m.dividends.applies ? (m.dividends.from ? <>Dividends added back: <b className="mx-1 text-fg">{m.dividends.count}</b> in this window (records start {m.dividends.from}; earlier is price only){m.dividends.left_out ? `; ${m.dividends.left_out} left out: a share split or bonus issue since then, with no as-traded price to convert the amount` : ''}{m.dividends.approx ? '; amounts before a rights issue are approximate' : ''}</> : 'No dividends recorded: price only') : 'Price only (dividends left out)'}</span>}
        {m.thin.thin && <span className="chip border border-warn/60 !h-auto !whitespace-normal !normal-case text-warn">Thinly traded: no trade on {Math.round((m.thin.share_no_trade ?? 0) * 100)}% of sessions. Use the weekly beta; risk is understated</span>}
      </div>

      <section className="card px-3 py-2 text-[12px]" aria-label="In plain words">
        <div className="label mb-1">In plain words</div>
        <ul className="list-disc space-y-0.5 pl-5">{d.summary.map(x => <li key={x}>{x}</li>)}</ul>
      </section>

      <FigureGroups f={f} r={r} benchLabel={m.bench.label} />

      <div className="grid gap-3 lg:grid-cols-2 [&>*]:min-w-0">
        <section className="card p-2"><TimeChart title="Below its previous high (drawdown)" series={charts.dd} refLines={noLines} fmt={fmtPct} /></section>
        {charts.vol && <section className="card p-2"><TimeChart title="Volatility, rolling 3 months" series={charts.vol} refLines={noLines} fmt={fmtPct} /></section>}
        {charts.beta && <section className="card p-2"><TimeChart title={`Beta to ${m.bench.label}, rolling 6 months`} series={charts.beta} refLines={noLines} fmt={fmtRatio} /></section>}
        {charts.corr && <section className="card p-2"><TimeChart title={`Correlation with ${m.bench.label}, rolling 6 months`} series={charts.corr} refLines={noLines} fmt={fmtRatio} /></section>}
        {charts.z && <section className="card p-2"><TimeChart title="Price vs its 1-year average (z-score)" series={charts.z} refLines={zLines} fmt={fmtRatio} /></section>}
        <section className="card p-2"><Histogram h={d.charts.histogram} /></section>
        <section className="card p-2"><SeasonalBars s={d.charts.monthly.seasonal} />
          <p className="px-1 text-[10px] text-muted">A month's average over few years is mostly chance: look for t above 2 and many years before trusting a pattern.</p></section>
        <section className="card p-2">
          {charts.dec ? <TimeChart title="Price and its long-run trend (yearly pattern removed)" series={charts.dec} refLines={noLines} fmt={fmtPrice} />
            : <p className="p-2 text-[12px] text-muted">Trend decomposition: {d.charts.decomposition.reason}.</p>}
        </section>
      </div>

      <section className="card">
        <div className="panel-title"><h3>Monthly returns</h3></div>
        <div className="p-2"><MonthlyTable m={d.charts.monthly} /></div>
      </section>

      {reg && (
        <section className="card">
          <div className="panel-title"><h3>{GLOSSARY.regression.name}</h3></div>
          <div className="p-2 text-[12px]">
            {!reg.available ? <p className="text-muted">{reg.reason}.</p> : (
              <>
                <div className="scroll-thin overflow-x-auto"><table className="bb-table num"><thead><tr><th>Factor</th><th className="text-right">Coefficient</th><th className="text-right">t</th><th>Reading</th></tr></thead>
                  <tbody>{reg.rows!.map(x => {
                    const sig = Math.abs(x.t) >= 2, alpha = x.factor.startsWith('Alpha')
                    return <tr key={x.factor}><td>{x.factor}</td><td className="text-right">{alpha ? pctf(x.coefficient, 1) : x.coefficient.toFixed(2)}</td>
                      <td className={`text-right ${sig ? 'text-fg' : 'text-muted'}`}>{x.t.toFixed(1)}</td>
                      <td className="text-[11px] text-muted">{!sig ? 'probably chance' : alpha ? (x.coefficient > 0 ? 'beat what the factors explain' : 'lagged what the factors explain') : `${x.coefficient > 0 ? 'moves with' : 'moves against'} it`}</td></tr>
                  })}</tbody></table></div>
                <p className="mt-1 text-[11px] text-muted">{reg.frequency} returns, {reg.observations} observations; the factors together explain <b className="text-fg">{pctf(reg.r_squared, 0, false)}</b> of the stock's moves. {GLOSSARY.regression.read}</p>
              </>
            )}
          </div>
        </section>
      )}

      <p className="text-[11px] text-muted">
        Computed with {m.gs_quant}, on the app's own prices (adjusted for rights issues and splits). Annualised with the CSE's real session count; ratios use the
        CBSL 3-month Treasury-bill rate. These describe the past, not the future, and they never change an engine's verdict. Not financial advice.
      </p>
    </div>
  )
}
