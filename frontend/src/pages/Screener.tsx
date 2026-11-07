import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fmt, ticker, type ScreenerData, type ScreenerRow } from '../api'
import { useSession } from '../auth/session'
import { useSort } from '../components/sort'
import { ErrorBox, Spinner } from '../components/ui'

type Filter = 'rule' | 'two' | 'all'
const pct = (x: number | null | undefined, nd = 1) => x == null ? '–' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(nd)}%`
const tone = (x: number | null | undefined) => x == null ? 'text-muted' : x >= 0 ? 'text-bull' : 'text-bear'

const GET: Record<string, (r: ScreenerRow) => number | string | null> = {
  ticker: r => ticker(r.symbol), last: r => r.close, chg: r => r.chg1d, r20: r => r.ret20, r60: r => r.ret60, r120: r => r.ret120,
  vs: r => r.rs120, d52: r => r.dist52, rank: r => r.rs_rank, turn: r => r.turn,
}

function Check({ ok, label }: { ok: boolean; label: string }) {
  return <span className={`inline-block w-[1.2em] text-center ${ok ? 'text-bull' : 'text-bear'}`} title={`${label}: ${ok ? 'met' : 'not met'}`} aria-label={`${label} ${ok ? 'met' : 'not met'}`}>{ok ? '●' : '○'}</span>
}

export default function Screener() {
  const nav = useNavigate()
  const admin = useSession().profile?.admin ?? false
  const [data, setData] = useState<ScreenerData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('rule')
  const [adding, setAdding] = useState<string | null>(null)
  const load = useCallback(() => api.screener().then(setData).catch(e => setErr(String(e.message ?? e))), [])
  useEffect(() => { load() }, [load])
  const running = data?.progress.running ?? false
  useEffect(() => {
    if (!running) return
    const t = window.setInterval(load, 3000)
    return () => window.clearInterval(t)
  }, [running, load])

  const run = async (full: boolean) => {
    setErr(null)
    try { await api.runScreener(full); await load() } catch (e) { setErr(String((e as Error).message)) }
  }
  const add = async (r: ScreenerRow) => {
    setAdding(r.symbol); setErr(null)
    try { await api.addStock(r.symbol); await load() } catch (e) { setErr(String((e as Error).message)) } finally { setAdding(null) }
  }

  const picked = useMemo(() => {
    const all = data?.run?.rows ?? []
    return filter === 'rule' ? all.filter(r => r.qualifies) : filter === 'two' ? all.filter(r => !r.qualifies && r.hits === 2) : all
  }, [data, filter])
  const { rows, th, sort, reset } = useSort(picked, GET)

  const meta = data?.run?.meta
  const prog = data?.progress

  return (
    <div className="mx-auto max-w-[1700px] px-3 py-3">
      <section className="card">
        <div className="panel-title flex-wrap">
          <h1 className="flex-1">SCR · Candidate ranking <span className="font-normal normal-case text-muted">liquid CSE stocks, ranked after the close</span></h1>
          {admin && <>
            <button className="btn !py-0.5" disabled={running} onClick={() => run(false)}>{running ? 'Running…' : 'Re-run now'}</button>
            <button className="btn !py-0.5" disabled={running} onClick={() => run(true)} title="Re-check liquidity of every ordinary share on the CSE (about 15 minutes)">Re-check full list</button>
          </>}
        </div>
        {err && <div className="p-2"><ErrorBox msg={err} /></div>}
        {running && prog && (
          <div className="border-b border-line p-3 text-[12px]" role="status">
            <span className="text-amber">RUNNING</span> {prog.phase}: {prog.done} of {prog.total} stocks read from TradingView (about 2 seconds each).
            <div className="mt-1 h-1.5 w-full bg-panel-3"><div className="h-1.5 bg-amber" style={{ width: `${prog.total ? (100 * prog.done) / prog.total : 0}%` }} /></div>
          </div>
        )}
        <div className="grid gap-3 p-3 text-[12px] lg:grid-cols-2">
          <div className="border border-line bg-panel p-2.5">
            <div className="label mb-1">The rule</div>
            <p>A stock meets it when it is in an <b>{meta?.rule ?? 'uptrend, within 10% of its 52-week high, and in the top 40% by 120-day return among liquid stocks'}</b>.</p>
          </div>
          <div className="border border-amber/50 bg-amber/5 p-2.5">
            <div className="label mb-1 text-amber">Tested, not proven</div>
            <p>{meta?.tested ?? 'Tested on 14 years of CSE history; it was chosen after looking at the data.'} The forward-test journal (JRN) keeps checking it on new data. A ranking is not a recommendation to buy.</p>
          </div>
        </div>
        {!data ? <Spinner /> : !data.run ? (
          <p className="p-6 text-center text-muted">{running ? 'The first run reads every ordinary CSE share once (about 15 minutes). This page fills in when it finishes.' : data.enabled ? 'No ranking yet. Press Re-run now.' : 'The screener is switched off in settings.yaml.'}</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-y border-line px-3 py-1.5 text-[12px]">
              <span>DATA TO <b className="num text-fg">{data.run.date}</b></span>
              <span><b className="num text-fg">{meta?.liquid}</b> liquid stocks (median daily turnover of at least LKR {((meta?.min_median_turnover_lkr ?? 0) / 1e6).toFixed(1)}M)</span>
              <span><b className="num text-bull">{meta?.qualifying}</b> meet the rule</span>
              {meta?.breadth_above50 != null && <span><b className="num text-fg">{(meta.breadth_above50 * 100).toFixed(0)}%</b> above their 50-day average</span>}
              {meta?.aspi_ret120 != null && <span>ASPI 120d <b className={`num ${tone(meta.aspi_ret120)}`}>{pct(meta.aspi_ret120)}</b></span>}
              <span className="ml-auto flex gap-1" role="radiogroup" aria-label="Show">
                {([['rule', 'Meets the rule'], ['two', '2 of 3'], ['all', 'All liquid']] as [Filter, string][]).map(([k, l]) =>
                  <button key={k} role="radio" aria-checked={filter === k} className="btn !py-0.5" aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>)}
              </span>
            </div>
            {rows.length === 0 ? (
              <p className="p-6 text-center text-muted">{filter === 'rule' ? 'No liquid stock meets all three conditions today. That happens in weak markets and is not an error.' : 'Nothing in this group.'}</p>
            ) : (
              <div className="scroll-thin overflow-x-auto">
                <table className="bb-table">
                  <thead>
                    <tr>
                      <th className="w-8"><button className="hover:text-yellow" onClick={reset} title="Back to the ranking order" aria-label="Sort by rank">#{sort ? ' ↺' : ''}</button></th>
                      {th('ticker', 'Ticker', '', true)}<th className="hidden lg:table-cell">Name</th>
                      {th('last', 'Last', 'text-right')}{th('chg', 'Chg%', 'text-right')}
                      {th('r20', '20D', 'hidden text-right md:table-cell')}{th('r60', '60D', 'hidden text-right md:table-cell')}{th('r120', '120D', 'text-right')}
                      {th('vs', 'vs ASPI', 'hidden text-right md:table-cell')}{th('d52', 'From 52W high', 'text-right')}
                      {th('rank', 'RS rank', 'hidden text-right md:table-cell')}{th('turn', 'Turnover/day', 'hidden text-right lg:table-cell')}
                      <th title="1) uptrend  2) within 10% of the 52-week high  3) top 40% by 120-day return">Rule</th><th>Watchlist</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={r.symbol} className={r.on_watchlist ? 'row-link' : ''} onClick={() => r.on_watchlist && nav(`/stock/${r.symbol}`)} title={r.qualifies ? r.reason : 'Missing: ' + r.missing.join('; ')}>
                        <td className="text-muted num">{i + 1})</td>
                        <td className="font-semibold text-amber">{ticker(r.symbol)}</td>
                        <td className="hidden max-w-[220px] truncate text-muted lg:table-cell" title={r.name}>{r.name}</td>
                        <td className="text-right font-semibold num">{fmt(r.close)}</td>
                        <td className={`text-right num ${tone(r.chg1d)}`}>{pct(r.chg1d, 2)}</td>
                        <td className={`hidden text-right num md:table-cell ${tone(r.ret20)}`}>{pct(r.ret20, 0)}</td>
                        <td className={`hidden text-right num md:table-cell ${tone(r.ret60)}`}>{pct(r.ret60, 0)}</td>
                        <td className={`text-right num ${tone(r.ret120)}`}>{pct(r.ret120, 0)}</td>
                        <td className={`hidden text-right num md:table-cell ${tone(r.rs120)}`}>{r.rs120 == null ? '–' : `${r.rs120 >= 0 ? '+' : ''}${(r.rs120 * 100).toFixed(0)} pts`}</td>
                        <td className="text-right num">{r.dist52 == null ? '–' : `${(r.dist52 * 100).toFixed(1)}%`}</td>
                        <td className="hidden text-right num md:table-cell">{r.rs_rank == null ? '–' : `${(r.rs_rank * 100).toFixed(0)}th`}</td>
                        <td className="hidden text-right num lg:table-cell">{r.turn == null ? '–' : `${(r.turn / 1e6).toFixed(1)}M`}</td>
                        <td><Check ok={r.trendup} label="Uptrend" /><Check ok={(r.dist52 ?? -1) > -0.1} label="Within 10% of the 52-week high" /><Check ok={(r.rs_rank ?? 0) >= 0.6} label="Top 40% by 120-day return" /></td>
                        <td>{r.on_watchlist ? <span className="text-muted">on watchlist</span>
                          : <button className="btn !py-0 text-[11px]" disabled={adding === r.symbol} onClick={e => { e.stopPropagation(); add(r) }}>{adding === r.symbol ? 'Adding…' : '+ADD'}</button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="border-t border-line px-3 py-2 text-[11px] text-muted">
              Hover a row for its reasoning. Prices are TradingView's rights/split-adjusted daily bars; stocks that trade too little to enter and exit are left out, and a stock must have about 6 months of history to be ranked.
              {meta?.failed?.length ? ` ${meta.failed.length} listed shares returned no data.` : ''} Decision support only, not financial advice.
            </p>
          </>
        )}
      </section>
    </div>
  )
}
