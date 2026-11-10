// Quant Lab results: what was tested, a plain-English reading, the curve against buy-and-hold and the ASPI, the figures, the
// two-halves check, the reshuffle ("is it luck?") test, and every trade. Measurements only: nothing here changes a verdict.
import { useMemo, useState } from 'react'
import type { LabResult as R } from '../api'
import { GLOSSARY } from '../quant/glossary'
import { TimeChart, pctf, type TSeries } from './QuantCharts'
import ExportCsv from './ExportCsv'
import { Explain, FigureGroups, lkr, show, type Kind } from './QuantFigures'
import type { CsvColumn } from '../export/csv'

const C = { strategy: '#e6e6e6', hold: '#b8890a', aspi: '#2f8fd6', cash: '#d1497a' }      // the validated compare palette
const NO_LINES: { value: number; label: string }[] = []
const fmtIdx = (v: number) => v.toFixed(0)
const fmtPct = (v: number) => pctf(v, 1, false)
const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()

const ROWS: [string, Kind, boolean][] = [   // figure, format, higher is better
  ['total_return', 'pct', true], ['cagr', 'pct', true], ['volatility', 'pct', false], ['max_drawdown', 'pct', true], ['sharpe', 'ratio', true], ['sortino', 'ratio', true], ['calmar', 'ratio', true],
]

function Compare({ r }: { r: R }) {
  const cols: [string, Record<string, unknown>][] = [['Your strategy', r.figures], [`Buy & hold (${r.meta.hold_stocks} stocks)`, r.hold_figures], ['ASPI', r.aspi_figures]]
  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="bb-table num text-[12px]" aria-label="Strategy against buy and hold and the ASPI">
        <thead><tr><th />{cols.map(([n]) => <th key={n} className="text-right">{n}</th>)}</tr></thead>
        <tbody>{ROWS.map(([k, kind, up]) => {
          const vals = cols.map(([, f]) => f[k] as number | null | undefined)
          const best = up ? Math.max(...vals.filter((v): v is number => typeof v === 'number')) : null
          return (
            <tr key={k}><td><Explain k={k} /></td>
              {vals.map((v, i) => <td key={i} className={`text-right ${v != null && v === best ? 'font-semibold text-amber' : ''}`}>{show(v, kind)}</td>)}</tr>
          )
        })}</tbody>
      </table>
      <p className="px-1 pt-1 text-[10px] text-muted">The best of the three in each row is highlighted (for volatility, lower is steadier). Buy &amp; hold = equal amounts in the same stocks, bought on day one with fees, never sold.</p>
    </div>
  )
}

function LuckChart({ lk, actual }: { lk: NonNullable<R['luck']>; actual: number | null | undefined }) {
  const [hi, setHi] = useState<number | null>(null)
  const W = 520, H = 130
  const lo = Math.min(lk.bins[0].from, actual ?? Infinity), top = Math.max(lk.bins.at(-1)!.to, actual ?? -Infinity)
  const x = (v: number) => ((v - lo) / (top - lo || 1)) * W
  const max = Math.max(...lk.bins.map(b => b.count), 1)
  return (
    <figure className="min-w-0" aria-label="Where the real result sits among the reshuffled ones">
      <figcaption className="flex flex-wrap gap-x-3 px-1 text-[11px]"><span className="label">{lk.runs} reshuffled runs: total return</span>
        {hi != null ? <span className="num text-fg">{pctf(lk.bins[hi].from, 0)} to {pctf(lk.bins[hi].to, 0)}: <b>{lk.bins[hi].count}</b> runs</span>
          : <span className="text-muted">hover a bar · middle half of runs: {pctf(lk.total.p5, 0)} to {pctf(lk.total.p95, 0)} (5th to 95th percentile)</span>}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H + 18}`} className="h-auto w-full" role="img" onMouseLeave={() => setHi(null)}>
        {lk.bins.map((b, i) => {
          const bh = (b.count / max) * H
          return <rect key={i} x={x(b.from) + 1} y={H - bh} width={Math.max(1, x(b.to) - x(b.from) - 2)} height={bh} rx={1} fill="var(--muted)" opacity={hi == null || hi === i ? 0.85 : 0.45} onMouseEnter={() => setHi(i)}>
            <title>{`${pctf(b.from, 0)} to ${pctf(b.to, 0)}: ${b.count} runs`}</title></rect>
        })}
        {actual != null && <><line x1={x(actual)} x2={x(actual)} y1={0} y2={H} stroke="var(--amber)" strokeWidth="2" />
          <text x={Math.min(x(actual) + 4, W - 70)} y={10} fontSize="10" fill="var(--text)">real: {pctf(actual, 0)}</text></>}
        <text x={0} y={H + 12} fontSize="10" fill="var(--muted)">{pctf(lo, 0)}</text>
        <text x={W} y={H + 12} fontSize="10" fill="var(--muted)" textAnchor="end">{pctf(top, 0)}</text>
      </svg>
    </figure>
  )
}

type TradeRow = R['trades']['rows'][number]
const TRADES: CsvColumn<TradeRow>[] = [
  { header: 'Stock', get: t => t.stock }, { header: 'Bought', get: t => t.in }, { header: 'Sold', get: t => t.out }, { header: 'Sessions held', get: t => t.sessions }, { header: 'Shares', get: t => t.qty },
  { header: 'Return after costs %', get: t => t.ret * 100 }, { header: 'Profit (LKR)', get: t => t.pnl }, { header: 'Costs (LKR)', get: t => t.costs }, { header: 'Why it ended', get: t => t.why },
]

export default function LabResult({ r }: { r: R }) {
  const [all, setAll] = useState(false)
  const amber = useMemo(() => css('--amber') || '#ffa028', [])
  const bear = useMemo(() => css('--bear') || '#ff433d', [])
  const ch = r.charts
  const main = useMemo<TSeries[]>(() => [
    { label: 'Your strategy', color: C.strategy, points: ch.strategy }, { label: 'Buy & hold', color: C.hold, points: ch.hold },
    { label: 'ASPI', color: C.aspi, points: ch.aspi }, { label: 'Cash (T-bills)', color: C.cash, points: ch.cash, dashed: true }], [ch])
  const dd = useMemo<TSeries[]>(() => [{ label: 'Below its high', color: bear, points: ch.drawdown, area: true }], [ch, bear])
  const inv = useMemo<TSeries[]>(() => [{ label: 'Invested', color: amber, points: ch.invested }], [ch, amber])
  const st = r.stats, m = r.meta
  const stat = (k: string, v: string, sub?: string, tone?: string) => (
    <div key={k} className="min-w-0 border border-line bg-panel px-2.5 py-1.5"><div className="label !normal-case"><Explain k={k} /></div>
      <div className={`text-[15px] font-semibold num ${tone ?? 'text-fg'}`}>{v}</div>{sub && <div className="text-[11px] text-muted">{sub}</div>}</div>)
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5 text-[11px]">
        <span className="chip bg-panel-3 !normal-case !font-normal text-muted">{m.start} → {m.end} · {m.sessions} sessions</span>
        <span className="chip bg-panel-3 !normal-case !font-normal text-muted">fees <b className="ml-1 text-fg">{m.brokerage_pct}%</b> + slippage <b className="ml-1 text-fg">{m.slippage_pct}%</b> each side</span>
        <span className="chip bg-panel-3 !normal-case !font-normal text-muted" title={m.rate.source}>cash earns <b className="ml-1 text-fg">{m.rate.rate_pct.toFixed(2)}%</b>{m.rate.stale ? ' (stale)' : ''}</span>
        {r.parity.ok
          ? <span className="chip bg-panel-3 !h-auto !whitespace-normal !normal-case !font-normal text-muted" title={`${r.parity.sessions_compared} sessions and ${r.parity.engine_trades} trades compared; largest difference ${r.parity.max_rel_diff.toExponential(1)}`}>
              ✓ cross-checked with gs-quant’s engine ({r.parity.engine_trades} trades, identical fills)</span>
          : <span className="chip border border-warn/60 !h-auto !whitespace-normal !normal-case text-warn">The fast calculation and gs-quant’s engine disagree ({r.parity.fills_differing} fills, {r.parity.max_rel_diff.toExponential(1)}): treat the numbers with care</span>}
        <span className="chip bg-panel-3 !normal-case !font-normal text-muted">took {m.seconds}s</span>
      </div>

      <section className="card px-3 py-2 text-[12px]" aria-label="What was tested">
        <div className="label mb-1">What was tested</div>
        <p><b className="text-amber">Stocks:</b> {m.universe.join(', ')}</p>
        <p><b className="text-amber">Buy</b> (best-traded first, up to {r.spec.max_positions} at once, equal amounts) when {m.entry_text}.</p>
        <p><b className="text-amber">Sell</b> when {m.exit_text}.</p>
      </section>

      <section className="card px-3 py-2 text-[12px]" aria-label="In plain words">
        <div className="label mb-1">In plain words</div>
        <ul className="list-disc space-y-0.5 pl-5">{r.summary.map(x => <li key={x}>{x.startsWith('Bottom line:') ? <b className="text-fg">{x}</b> : x}</li>)}</ul>
      </section>

      <div className="card p-2"><TimeChart title="LKR 100 at the start" series={main} refLines={NO_LINES} fmt={fmtIdx} height={260} /></div>
      <section className="card"><div className="panel-title"><h3>Against buying and holding, and the market</h3></div><div className="p-2"><Compare r={r} /></div></section>

      <section className="card"><div className="panel-title"><h3>How the trading went</h3></div>
        <div className="grid grid-cols-2 gap-2 p-2 sm:grid-cols-3 lg:grid-cols-6 [&>*]:min-w-0">
          {stat('win_rate', st.win_rate == null ? '–' : pctf(st.win_rate, 0, false), `${st.trades} closed trades${st.open_at_end ? `, ${st.open_at_end} still open` : ''}`)}
          {stat('profit_factor', st.profit_factor == null ? '–' : st.profit_factor.toFixed(2), `avg win ${pctf(st.avg_win)}, avg loss ${pctf(st.avg_loss)}`)}
          {stat('exposure', st.exposure == null ? '–' : pctf(st.exposure, 0, false), st.avg_sessions == null ? undefined : `average hold ${st.avg_sessions.toFixed(0)} sessions`)}
          {stat('turnover', st.turnover_per_year == null ? '–' : `${st.turnover_per_year.toFixed(1)}×`, 'a year')}
          {stat('slippage', lkr(st.brokerage + st.slippage), `fees ${lkr(st.brokerage)} + slippage ${lkr(st.slippage)}`, 'text-bear')}
          <div className="min-w-0 border border-line bg-panel px-2.5 py-1.5"><div className="label">Interest on idle cash</div><div className="text-[15px] font-semibold num text-fg">{lkr(st.interest)}</div>
            <div className="text-[11px] text-muted">best {pctf(st.best)} · worst {pctf(st.worst)}</div></div>
        </div>
      </section>

      <div className="grid gap-3 lg:grid-cols-2 [&>*]:min-w-0">
        {r.halves.length === 2 && (
          <section className="card"><div className="panel-title"><h3>Did it work in both halves?</h3></div>
            <div className="scroll-thin overflow-x-auto p-2">
              <table className="bb-table num text-[12px]" aria-label="First half and second half">
                <thead><tr><th /><th>Dates</th><th className="text-right">Strategy</th><th className="text-right">Buy &amp; hold</th><th className="text-right">Sharpe</th><th className="text-right">Trades</th></tr></thead>
                <tbody>{r.halves.map(h => (
                  <tr key={h.name}><td>{h.name}</td><td className="text-muted">{h.from} → {h.to}</td>
                    <td className={`text-right ${h.total_return >= 0 ? 'text-bull' : 'text-bear'}`}>{pctf(h.total_return)}</td><td className="text-right">{pctf(h.hold_total_return)}</td>
                    <td className="text-right">{h.sharpe == null ? '–' : h.sharpe.toFixed(2)}</td><td className="text-right">{h.trades}</td></tr>
                ))}</tbody>
              </table>
              <p className="mt-1 text-[10px] text-muted">Each half is run again from scratch with the same starting money. A rule that only works in one half may owe its result to one good stretch.</p>
            </div>
          </section>
        )}
        {r.luck && (
          <section className="card"><div className="panel-title"><h3><Explain k="reshuffle">Is it just luck? The reshuffle test</Explain></h3></div>
            <div className="space-y-1 p-2">
              <LuckChart lk={r.luck} actual={r.figures.total_return as number | null} />
              <p className="px-1 text-[11px]">
                Beat <b className="text-fg">{r.luck.sharpe ? `${Math.round(r.luck.sharpe.beat * 100)}%` : '–'}</b> of the reshuffled runs on return for the risk
                and <b className="text-fg">{r.luck.total.beat == null ? '–' : `${Math.round(r.luck.total.beat * 100)}%`}</b> on total return
                ({r.luck.runs} runs). Typical reshuffled result: {pctf(r.luck.total.median, 0)}.
              </p>
            </div>
          </section>
        )}
        <div className="card p-2"><TimeChart title="Below its previous high (drawdown)" series={dd} refLines={NO_LINES} fmt={fmtPct} /></div>
        <div className="card p-2"><TimeChart title="Share of the account invested in stocks" series={inv} refLines={NO_LINES} fmt={fmtPct} /></div>
      </div>

      <section className="card">
        <div className="panel-title"><h3>All the figures</h3><button type="button" className="btn !py-0 text-[11px]" aria-expanded={all} onClick={() => setAll(a => !a)}>{all ? 'hide' : 'show'}</button></div>
        {all && <div className="p-2"><FigureGroups f={r.figures} r={r.relative} benchLabel="ASPI" skip={['Habits and today']} /></div>}
      </section>

      <section className="card">
        <div className="panel-title"><h3 className="flex-1">Every trade <span className="font-normal normal-case text-muted">· {r.trades.shown < r.trades.total ? `latest ${r.trades.shown} of ${r.trades.total}` : r.trades.total}</span></h3><ExportCsv name="backtest-trades" columns={TRADES} rows={r.trades.rows} /></div>
        <div className="scroll-thin max-h-[420px] overflow-auto p-2">
          <table className="bb-table num text-[12px]" aria-label="Trades">
            <thead><tr><th>Stock</th><th>Bought</th><th>Sold</th><th className="text-right">Sessions</th><th className="text-right">Shares</th><th className="text-right">Return after costs</th><th className="text-right">Profit LKR</th><th className="text-right">Costs LKR</th><th>Why it ended</th></tr></thead>
            <tbody>{r.trades.rows.map((t, i) => (
              <tr key={i}><td className="text-amber">{t.stock}</td><td>{t.in}</td><td>{t.out ?? <span className="text-muted">still open</span>}</td>
                <td className="text-right">{t.sessions ?? '–'}</td><td className="text-right">{t.qty.toLocaleString()}</td>
                <td className={`text-right ${t.ret >= 0 ? 'text-bull' : 'text-bear'}`}>{pctf(t.ret)}</td>
                <td className={`text-right ${t.pnl >= 0 ? 'text-bull' : 'text-bear'}`}>{Math.round(t.pnl).toLocaleString()}</td>
                <td className="text-right text-muted">{Math.round(t.costs).toLocaleString()}</td><td className="text-muted">{t.why}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </section>

      <p className="text-[11px] text-muted">
        Computed with {m.gs_quant}. {GLOSSARY.survivorship.what} Past results are not forecasts, and nothing here changes an engine’s verdict. Not financial advice.
      </p>
    </div>
  )
}
