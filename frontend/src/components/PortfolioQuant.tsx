// PORT risk and performance (gs-quant maths, offline parts): the equity curve against the ASPI with the full figure set,
// the risk of what is held now (share of risk, correlations, VaR in rupees, stress tests) and "what if you had held your
// lists" baskets. Measurements only: nothing here changes a verdict.
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type ListBasket, type PortfolioQuant as PQ, type QPoint } from '../api'
import { MonthlyTable, TimeChart, pctf, type TSeries } from './QuantCharts'
import { Explain, FigureGroups, lkr, show } from './QuantFigures'
import { ErrorBox, Section, Spinner } from './ui'

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()
const C = { mine: '#e6e6e6', aspi: '#2f8fd6', hold: '#b8890a' }        // the validated compare palette (dataviz check)
const NO_LINES: { value: number; label: string }[] = []
const fmtIdx = (v: number) => v.toFixed(1)
const fmtPct = (v: number) => pctf(v, 1, false)
const fmtLkr = (v: number) => Math.round(v).toLocaleString()

/** Correlation colour: blue for moving together, magenta for opposite, mixed into the panel (numbers are always written). */
const corrFill = (v: number) => `color-mix(in srgb, ${v >= 0 ? C.aspi : '#d1497a'} ${Math.round(Math.min(Math.abs(v), 1) * 55)}%, var(--panel-3))`

function QStat({ k, value, sub, tone }: { k: string; value: ReactNode; sub?: ReactNode; tone?: 'bear' | 'warn' }) {
  return (
    <div className="min-w-0 border border-line bg-panel px-2.5 py-1.5">
      <div className="label !normal-case"><Explain k={k} /></div>
      <div className={`text-[15px] font-semibold num ${tone === 'bear' ? 'text-bear' : tone === 'warn' ? 'text-amber' : 'text-fg'}`}>{value}</div>
      {sub && <div className="text-[11px] text-muted">{sub}</div>}
    </div>
  )
}

const line = (label: string, color: string, points: QPoint[] = [], more: Partial<TSeries> = {}): TSeries => ({ label, color, points, ...more })

export default function PortfolioQuant() {
  const [d, setD] = useState<PQ | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api.portfolioQuant().then(setD).catch(e => setErr(String(e.message ?? e))) }, [])
  const bear = useMemo(() => css('--bear') || '#ff433d', [])
  const amber = useMemo(() => css('--amber') || '#ffa028', [])
  const ch = d?.curve.charts
  const series = useMemo(() => ch ? {
    vs: [line('Your holdings', C.mine, ch.portfolio), line('ASPI', C.aspi, ch.aspi)],
    dd: [line('Below your high', bear, ch.drawdown, { area: true })],
    pnl: [line('Profit after fees', amber, ch.pnl)],
  } : null, [ch, bear, amber])

  if (err) return <ErrorBox msg={err} />
  if (!d) return <Section title="Performance and risk · gs-quant"><Spinner label="Working out your performance and risk…" /></Section>
  const c = d.curve, r = d.risk
  return (
    <>
      <Section title={<>Performance vs the ASPI <span className="font-normal normal-case text-muted">· time-weighted, after fees, dividends included</span></>}>
        {!c.available || !series ? <p className="text-sm text-muted">{c.reason}</p> : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5 text-[11px]">
              <span className="chip bg-panel-3 !normal-case !font-normal text-muted">since {c.start}</span>
              <span className="chip bg-panel-3 !normal-case !font-normal text-muted">put in <b className="ml-1 text-fg">{lkr(c.money_in ?? 0)}</b></span>
              <span className="chip bg-panel-3 !normal-case !font-normal text-muted">taken out <b className="ml-1 text-fg">{lkr(c.money_out ?? 0)}</b></span>
              <span className="chip bg-panel-3 !normal-case !font-normal text-muted" title="Dividend per share x shares you held before each XD date (recorded dividends only)">dividends (est.) <b className="ml-1 text-fg">{lkr(c.dividends ?? 0)}</b></span>
              <span className="chip bg-panel-3 !h-auto !whitespace-normal !normal-case !font-normal text-muted" title={d.rate.source}>Risk-free: <b className="ml-1 text-fg">{d.rate.rate_pct.toFixed(2)}%</b>{d.rate.as_of ? ` (${d.rate.as_of})` : ''}</span>
              {!!c.entry_estimated?.length && <span className="chip border border-line !h-auto !whitespace-normal !normal-case !font-normal text-muted">
                Bought before Nov 2025 ({c.entry_estimated.join(', ')}): counted from the close of the day you bought</span>}
              {!!c.skipped?.length && <span className="chip border border-warn/60 !h-auto !whitespace-normal !normal-case text-warn">No prices for {c.skipped.join(', ')}: left out</span>}
            </div>
            <div className="grid gap-3 lg:grid-cols-2 [&>*]:min-w-0">
              <div className="card p-2 lg:col-span-2"><TimeChart title="Your return vs the ASPI (start = 100)" series={series.vs} refLines={NO_LINES} fmt={fmtIdx} height={220} />
                <p className="px-1 text-[10px] text-muted"><Explain k="twr">How this is worked out</Explain></p></div>
              <div className="card p-2"><TimeChart title="Below your previous high (drawdown)" series={series.dd} refLines={NO_LINES} fmt={fmtPct} /></div>
              <div className="card p-2"><TimeChart title="Profit after fees, LKR (realised + unrealised + dividends)" series={series.pnl} refLines={NO_LINES} fmt={fmtLkr} /></div>
            </div>
            <FigureGroups f={c.figures ?? {}} r={c.relative ?? {}} benchLabel="ASPI" skip={['Habits and today']} />
            {ch && <div className="card"><div className="panel-title"><h3>Your monthly returns</h3></div><div className="p-2"><MonthlyTable m={ch.monthly} /></div></div>}
          </div>
        )}
      </Section>

      <Section title={<>Risk of what you hold now <span className="font-normal normal-case text-muted">· from the last year of daily prices</span></>}>
        {!r.available ? <p className="text-sm text-muted">{r.reason}</p> : <RiskNow r={r} />}
      </Section>

      <Baskets />
      <p className="px-1 text-[11px] text-muted">Computed with {d.gs_quant}. Annualised with the CSE's {d.sessions_per_year} sessions a year. These describe the past, not the future, and never change an engine's verdict. Not financial advice.</p>
    </>
  )
}

function RiskNow({ r }: { r: PQ['risk'] }) {
  const rows = r.rows ?? [], cm = r.correlation!, stress = r.stress ?? []
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <QStat k="portfolio_volatility" value={pctf(r.volatility, 1, false)} sub="a year" />
        <QStat k="portfolio_beta" value={r.beta == null ? '–' : r.beta.toFixed(2)} sub="vs the ASPI" />
        <QStat k="var_lkr" value={lkr(r.var95_lkr ?? 0)} sub={`${pctf(r.var95_pct, 1)} of ${lkr(r.total_value ?? 0)} in a day`} tone="bear" />
        <QStat k="cvar_lkr" value={lkr(r.cvar95_lkr ?? 0)} sub={`${pctf(r.cvar95_pct, 1)} in a day`} tone="bear" />
        <QStat k="diversification_ratio" value={r.diversification_ratio == null ? '–' : r.diversification_ratio.toFixed(2)} sub="1.0 = none" />
        <div className="min-w-0 border border-line bg-panel px-2.5 py-1.5"><div className="label">Measured over</div><div className="text-[15px] font-semibold num text-fg">{r.sessions} sessions</div><div className="text-[11px] text-muted">{r.from} → {r.to}</div></div>
      </div>
      {r.any_thin && <p className="text-[12px] text-warn">Some holdings often don't trade: their volatility and this VaR understate the real risk.</p>}

      <div className="grid gap-3 xl:grid-cols-2 [&>*]:min-w-0">
        <div className="card">
          <div className="panel-title"><h3><Explain k="risk_share">Where the risk comes from</Explain></h3></div>
          <div className="scroll-thin overflow-x-auto p-2">
            <table className="bb-table num text-[12px]" aria-label="Share of the risk by holding">
              <thead><tr><th>Stock</th><th className="text-right">Value</th><th className="text-right">Volatility</th><th className="min-w-[160px]">Share of money vs share of risk</th></tr></thead>
              <tbody>{rows.map(x => (
                <tr key={x.symbol}>
                  <td className="text-amber">{x.label}{x.thin && <span className="ml-1 text-[10px] text-warn" title={`no trade on ${Math.round((x.share_no_trade ?? 0) * 100)}% of sessions`}>thin</span>}</td>
                  <td className="text-right">{Math.round(x.value).toLocaleString()}</td>
                  <td className="text-right">{pctf(x.volatility, 0, false)}</td>
                  <td>
                    <div className="flex items-center gap-1.5"><span className="h-1.5 bg-muted/70" style={{ width: `${Math.max(2, x.weight * 120)}px` }} /><span className="text-muted">{pctf(x.weight, 0, false)} money</span></div>
                    <div className="flex items-center gap-1.5"><span className="h-1.5 bg-amber" style={{ width: `${Math.max(2, x.risk_share * 120)}px` }} /><span className="text-fg">{pctf(x.risk_share, 0, false)} risk</span></div>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="panel-title"><h3><Explain k="correlation">How they move together</Explain></h3></div>
          <div className="scroll-thin overflow-x-auto p-2">
            {cm.labels.length < 2 ? <p className="text-[12px] text-muted">Needs two or more holdings.</p> : (
              <table className="num text-[11px]" aria-label="Correlation between holdings">
                <thead><tr><th />{cm.labels.map(l => <th key={l} className="px-1.5 text-right font-normal text-muted">{l}</th>)}</tr></thead>
                <tbody>{cm.matrix.map((row, i) => (
                  <tr key={cm.labels[i]}><th className="pr-2 text-left font-normal text-amber">{cm.labels[i]}</th>
                    {row.map((v, j) => <td key={j} className="border border-[var(--bg)] px-1.5 py-1 text-right" style={{ background: i === j ? 'transparent' : corrFill(v) }}
                      title={i === j ? '' : `${cm.labels[i]} and ${cm.labels[j]}: ${v.toFixed(2)}`}>{i === j ? <span className="text-muted">–</span> : v.toFixed(2)}</td>)}
                  </tr>
                ))}</tbody>
              </table>
            )}
            <p className="mt-1 text-[10px] text-muted">Daily returns over the same year. Near 1: they rise and fall together, so holding both spreads little risk.</p>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="panel-title"><h3><Explain k="stress">If the ASPI's worst falls happened again</Explain></h3></div>
        <div className="scroll-thin overflow-x-auto p-2">
          <table className="bb-table num text-[12px]" aria-label="Stress tests">
            <thead><tr><th>Over</th><th>Dates</th><th className="text-right">ASPI</th><th className="text-right">Your stocks then</th><th className="text-right">By beta now</th><th>Note</th></tr></thead>
            <tbody>{stress.map(x => {
              const apart = Math.abs(x.portfolio_pct - x.beta_pct) > 0.1
              return (
                <tr key={`${x.horizon}-${x.from}`}>
                  <td>{x.horizon}</td><td className="text-muted">{x.from} → {x.to}</td>
                  <td className="text-right">{pctf(x.aspi, 1)}</td>
                  <td className="text-right"><b className={x.portfolio_pct < 0 ? 'text-bear' : 'text-fg'}>{pctf(x.portfolio_pct, 1)}</b> <span className="text-muted">{lkr(x.portfolio_lkr)}</span></td>
                  <td className="text-right">{pctf(x.beta_pct, 1)} <span className="text-muted">{lkr(x.beta_lkr)}</span></td>
                  <td className="text-[11px] text-muted">{x.estimated.length ? `${x.estimated.join(', ')}: no prices then, beta used. ` : ''}{apart ? 'The two readings differ a lot.' : ''}</td>
                </tr>
              )
            })}</tbody>
          </table>
          <p className="mt-1 text-[10px] text-muted">Each holding's own move between those dates, applied to what you hold today; and what today's beta to the ASPI suggests. A week or a month here counts trading sessions, so market closures stretch the dates.</p>
        </div>
      </div>
    </div>
  )
}

function Baskets() {
  const [win, setWin] = useState('3y')
  const [d, setD] = useState<ListBasket[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    let live = true
    setLoading(true); setErr(null)
    api.portfolioBaskets(win).then(x => { if (live) setD(x) }).catch(e => { if (live) setErr(String(e.message ?? e)) }).finally(() => live && setLoading(false))
    return () => { live = false }
  }, [win])
  return (
    <Section title={<><Explain k="basket">What if you'd held your lists</Explain></>}
             right={<span className="flex" role="radiogroup" aria-label="Basket window">{(['1y', '3y', '5y'] as const).map(w =>
               <button key={w} role="radio" aria-checked={win === w} className="btn !px-2 !py-0.5 text-[11px]" onClick={() => setWin(w)}>{w.toUpperCase()}</button>)}</span>}>
      {err ? <ErrorBox msg={err} /> : !d ? <Spinner label="Building the baskets…" /> : d.length === 0 ? <p className="text-sm text-muted">No lists yet.</p> : (
        <div className={`grid gap-3 xl:grid-cols-2 [&>*]:min-w-0 ${loading ? 'opacity-60' : ''}`}>{d.map(b => <BasketCard key={b.id} b={b} />)}</div>
      )}
    </Section>
  )
}

const BASKET_ROWS: [string, string][] = [['total_return', 'pct'], ['cagr', 'pct'], ['volatility', 'pct'], ['max_drawdown', 'pct'], ['sharpe', 'ratio']]
const BASKET_NAMES: Record<string, string> = { total_return: 'Total return', cagr: 'Return a year', volatility: 'Volatility', max_drawdown: 'Worst fall', sharpe: 'Sharpe' }

function BasketCard({ b }: { b: ListBasket }) {
  const s = useMemo(() => b.charts ? [line('Rebalanced monthly', C.mine, b.charts.basket), line('Bought once and held', C.hold, b.charts.hold, { dashed: true }), line('ASPI', C.aspi, b.charts.aspi)] : null, [b])
  if (!b.available || !s) return <div className="card p-3 text-[12px]"><b className="text-amber">{b.name}</b> <span className="text-muted">· {b.reason}</span></div>
  const cols: [string, Record<string, unknown> | undefined][] = [['Rebalanced', b.figures], ['Held', b.hold_figures], ['ASPI', b.aspi_figures]]
  const [lo, hi] = b.rebalance_day_range ?? [null, null]
  return (
    <div className="card">
      <div className="panel-title"><h3>{b.name} <span className="font-normal normal-case text-muted">· {b.symbols?.length} stocks, {b.start} → {b.end}</span></h3></div>
      <div className="space-y-2 p-2">
        <TimeChart title="Start = 100, after fees" series={s} refLines={NO_LINES} fmt={fmtIdx} />
        <div className="scroll-thin overflow-x-auto">
          <table className="bb-table num text-[12px]" aria-label={`${b.name} basket figures`}>
            <thead><tr><th />{cols.map(([n]) => <th key={n} className="text-right">{n}</th>)}</tr></thead>
            <tbody>{BASKET_ROWS.map(([k, kind]) => (
              <tr key={k}><td>{BASKET_NAMES[k]}</td>{cols.map(([n, f]) => {
                const v = f?.[k] as number | null | undefined
                return <td key={n} className="text-right">{v == null ? <span className="text-[10px] text-muted">{(f?.missing as Record<string, string> | undefined)?.[k] ? 'not enough history' : '–'}</span> : show(v, kind as 'pct' | 'ratio')}</td>
              })}</tr>
            ))}</tbody>
          </table>
        </div>
        <p className="text-[11px] text-muted">
          {lo != null && hi != null && <>Rebalancing on a different day of the month would have ended between <b className="text-fg">{pctf(lo, 0)}</b> and <b className="text-fg">{pctf(hi, 0)}</b>. </>}
          {b.relative?.beta != null && <>Beta to the ASPI {(b.relative.beta as number).toFixed(2)}, correlation {(b.relative.correlation as number).toFixed(2)}. </>}
          {!!b.too_new?.length && <>Left out (listed after the window starts): {b.too_new.join(', ')}. </>}
          {!!b.missing?.length && <>No prices: {b.missing.join(', ')}. </>}
          {b.limited_by && <>Starts when {b.limited_by} listed. </>}
        </p>
      </div>
    </div>
  )
}
