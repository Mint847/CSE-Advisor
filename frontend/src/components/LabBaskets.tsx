// Quant Lab › Baskets: build your own index of CSE stocks (equal, inverse-volatility or your own weights; monthly, weekly or never
// rebalanced) and compare it with the ASPI, after CSE fees and with dividends added back.
import { useEffect, useMemo, useState } from 'react'
import { api, type BasketResult, type LabCatalog } from '../api'
import { TimeChart, pctf, type TSeries } from './QuantCharts'
import { Explain, show, type Kind } from './QuantFigures'
import { ErrorBox, Spinner } from './ui'

const C = { basket: '#e6e6e6', hold: '#b8890a', aspi: '#2f8fd6' }
const NO_LINES: { value: number; label: string }[] = []
const fmtIdx = (v: number) => v.toFixed(1)
const ROWS: [string, string, Kind][] = [['total_return', 'Total return', 'pct'], ['cagr', 'Return a year', 'pct'], ['volatility', 'Volatility', 'pct'], ['max_drawdown', 'Worst fall', 'pct'], ['sharpe', 'Sharpe ratio', 'ratio']]

export default function LabBaskets() {
  const [cat, setCat] = useState<LabCatalog | null>(null)
  const [chosen, setChosen] = useState<string[]>([])
  const [mode, setMode] = useState<'equal' | 'inverse_vol' | 'custom'>('equal')
  const [custom, setCustom] = useState<Record<string, number>>({})
  const [rebalance, setRebalance] = useState<'monthly' | 'weekly' | 'none'>('monthly')
  const [win, setWin] = useState('3y')
  const [out, setOut] = useState<BasketResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.labCatalog().then(c => { setCat(c); setChosen(c.stocks.slice(0, 5).map(s => s.symbol)) }).catch(e => setErr(String(e.message ?? e))) }, [])

  const run = async () => {
    setBusy(true); setErr(null)
    try { setOut(await api.labBasket({ symbols: chosen, weights: mode === 'custom' ? Object.fromEntries(chosen.map(s => [s, custom[s] ?? 0])) : mode, rebalance, window: win })) }
    catch (e) { setErr(String((e as Error).message ?? e)); setOut(null) } finally { setBusy(false) }
  }
  const total = chosen.reduce((a, s) => a + (custom[s] ?? 0), 0)
  const series = useMemo<TSeries[] | null>(() => out?.charts ? [{ label: 'Your basket', color: C.basket, points: out.charts.basket }, { label: out.rebalance === 'none' ? '' : 'Bought once and held', color: C.hold, points: out.charts.hold, dashed: true },
    { label: 'ASPI', color: C.aspi, points: out.charts.aspi }].filter(s => s.label) : null, [out])
  if (!cat) return err ? <ErrorBox msg={err} /> : <Spinner label="Loading…" />
  const tog = (s: string) => setChosen(c => (c.includes(s) ? c.filter(x => x !== s) : c.length < cat.max_symbols ? [...c, s] : c))

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">Build your own index from your stocks and see how it would have done against the ASPI. The CSE’s fees are charged on the first purchase and on every rebalance, and dividends are added back.
        The stocks are ones you track today, so results flatter them. <Explain k="basket">How the basket works</Explain></p>
      <section className="card space-y-2 p-3" aria-label="Build the basket">
        <div className="flex flex-wrap items-center gap-1"><span className="label mr-1">Stocks</span><span className="mr-2 text-[11px] text-muted">{chosen.length} chosen, at least 2</span>
          {cat.stocks.map(s => <button key={s.symbol} type="button" aria-pressed={chosen.includes(s.symbol)} onClick={() => tog(s.symbol)} title={s.name ?? s.label}
            className={`border px-2 py-0.5 text-[11px] ${chosen.includes(s.symbol) ? 'border-amber bg-amber/20 text-fg' : 'border-line text-muted hover:text-fg'}`}>{s.label}</button>)}</div>
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <span className="label">Weights</span>
          <span className="flex" role="radiogroup" aria-label="Weights">{([['equal', 'Equal'], ['inverse_vol', 'Steadier stocks count more'], ['custom', 'My own %']] as const).map(([k, l]) => <button key={k} role="radio" aria-checked={mode === k} className="btn !px-2 !py-0.5" onClick={() => setMode(k)}>{l}</button>)}</span>
          <span className="label ml-2">Rebalance</span>
          <span className="flex" role="radiogroup" aria-label="Rebalance">{([['monthly', 'Monthly'], ['weekly', 'Weekly'], ['none', 'Never']] as const).map(([k, l]) => <button key={k} role="radio" aria-checked={rebalance === k} className="btn !px-2 !py-0.5" onClick={() => setRebalance(k)}>{l}</button>)}</span>
          <span className="label ml-2">Period</span>
          <span className="flex" role="radiogroup" aria-label="Period">{['1y', '3y', '5y'].map(w => <button key={w} role="radio" aria-checked={win === w} className="btn !px-2 !py-0.5" onClick={() => setWin(w)}>{w.toUpperCase()}</button>)}</span>
        </div>
        {mode === 'custom' && (
          <div className="flex flex-wrap items-center gap-2 text-[12px]" aria-label="Your weights">
            {chosen.map(s => <label key={s} className="flex items-center gap-1"><span className="text-amber">{s.split('.')[0]}</span>
              <input className="input num !w-16 !py-0.5 text-[12px]" type="number" min={0} max={100} step={1} value={custom[s] ?? 0} aria-label={`Weight of ${s.split('.')[0]} in percent`} onChange={e => setCustom({ ...custom, [s]: Number(e.target.value) })} />%</label>)}
            <span className={`num ${total === 100 ? 'text-bull' : 'text-muted'}`}>total {total}% {total !== 100 && total > 0 ? '(scaled to 100%)' : ''}</span>
            <button type="button" className="btn !py-0 text-[11px]" onClick={() => setCustom(Object.fromEntries(chosen.map(s => [s, Math.round(100 / chosen.length)])))}>equal</button>
          </div>
        )}
        <button type="button" className="btn btn-primary" disabled={busy || chosen.length < 2 || (mode === 'custom' && total <= 0)} onClick={run}>{busy ? 'Working…' : 'Build the basket'}</button>
      </section>
      {err && <ErrorBox msg={err} />}
      {out && !out.available && <p className="text-sm text-muted">{out.reason}</p>}
      {out?.available && series && out.figures && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-1.5 text-[11px]"><span className="chip bg-panel-3 !normal-case !font-normal text-muted">{out.start} → {out.end}</span>
            <span className="chip bg-panel-3 !normal-case !font-normal text-muted">{out.symbols?.length} stocks · {out.rebalance === 'none' ? 'never rebalanced' : `rebalanced ${out.rebalance}`}</span>
            {out.weights_note && <span className="chip border border-warn/60 !h-auto !whitespace-normal !normal-case text-warn">{out.weights_note}</span>}</div>
          <div className="card p-2"><TimeChart title="Start = 100, after fees" series={series} refLines={NO_LINES} fmt={fmtIdx} height={240} /></div>
          <div className="grid gap-3 lg:grid-cols-2 [&>*]:min-w-0">
            <section className="card"><div className="panel-title"><h3>Figures</h3></div><div className="scroll-thin overflow-x-auto p-2">
              <table className="bb-table num text-[12px]" aria-label="Basket figures">
                <thead><tr><th /><th className="text-right">Basket</th>{out.rebalance !== 'none' && <th className="text-right">Held</th>}<th className="text-right">ASPI</th></tr></thead>
                <tbody>{ROWS.map(([k, name, kind]) => (
                  <tr key={k}><td>{name}</td><td className="text-right">{show(out.figures![k], kind)}</td>{out.rebalance !== 'none' && <td className="text-right">{show(out.hold_figures?.[k], kind)}</td>}<td className="text-right">{show(out.aspi_figures?.[k], kind)}</td></tr>
                ))}</tbody>
              </table>
              <p className="mt-1 text-[11px] text-muted">
                {out.rebalance_day_range && <>Rebalancing on a different day of the month would have ended between <b className="text-fg">{pctf(out.rebalance_day_range[0], 0)}</b> and <b className="text-fg">{pctf(out.rebalance_day_range[1], 0)}</b>. </>}
                {out.relative?.beta != null && <>Beta to the ASPI {(out.relative.beta as number).toFixed(2)}, correlation {(out.relative.correlation as number).toFixed(2)}. </>}
                {!!out.too_new?.length && <>Left out (listed after the period starts): {out.too_new.join(', ')}. </>}{!!out.missing?.length && <>No prices: {out.missing.join(', ')}. </>}
                {out.limited_by && <>Starts when {out.limited_by} listed.</>}
              </p></div></section>
            <section className="card"><div className="panel-title"><h3>Weights at the start</h3></div><div className="p-2">
              <ul className="text-[12px]">{out.weights?.map(w => (
                <li key={w.symbol} className="flex items-center gap-2 border-t border-line/60 py-1 first:border-t-0"><span className="w-14 text-amber">{w.symbol}</span>
                  <span className="h-1.5 bg-amber" style={{ width: `${Math.max(2, w.weight * 220)}px` }} /><b className="num text-fg">{pctf(w.weight, 1, false)}</b></li>
              ))}</ul></div></section>
          </div>
        </div>
      )}
    </div>
  )
}
