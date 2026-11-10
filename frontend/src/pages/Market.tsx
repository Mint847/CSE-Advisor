import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ColorType, HistogramSeries, LineSeries, createChart } from 'lightweight-charts'
import { api, fmt, ticker, type Headline, type MarketData, type Mover } from '../api'
import MarketRisk from '../components/MarketRisk'
import WorldPanel from '../components/WorldPanel'
import { Section, Spinner, Stat } from '../components/ui'

const css = (k: string) => getComputedStyle(document.documentElement).getPropertyValue(k).trim()
const m1 = (v: number | null | undefined) => (v == null ? '–' : `${(v / 1e6).toLocaleString(undefined, { maximumFractionDigits: 1 })}M`)
const mn = (v: number | null | undefined) => (v == null ? '–' : `${v >= 0 ? '+' : '−'}${(Math.abs(v) / 1e6).toLocaleString(undefined, { maximumFractionDigits: 1 })}M`)

function baseChart(el: HTMLDivElement) {
  return createChart(el, {
    autoSize: true, layout: { background: { type: ColorType.Solid, color: css('--bg') || '#000000' }, textColor: css('--muted'), fontFamily: '"IBM Plex Mono", monospace', attributionLogo: false },
    grid: { vertLines: { visible: false }, horzLines: { color: css('--chart-grid') } },
    rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false },
  })
}

function MoverTable({ title, rows, kind, wl, onAdd }: { title: string; rows: Mover[]; kind: 'chg' | 'active'; wl: Set<string>; onAdd: (s: string) => void }) {
  return (
    <div className="min-w-0">
      <div className="label px-1 pb-1">{title}</div>
      <table className="bb-table w-full">
        <thead><tr><th className="text-left">Ticker</th>{kind === 'chg' ? <><th>Price</th><th>Chg%</th></> : <><th>Trades</th><th>Turnover</th></>}</tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.symbol}>
              <td className="text-left">
                {wl.has(r.symbol) ? <Link to={`/stock/${r.symbol}`} className="text-amber hover:underline">{ticker(r.symbol)}</Link>
                  : <span className="text-fg">{ticker(r.symbol)} <button className="text-[10px] text-muted hover:text-yellow" onClick={() => onAdd(r.symbol)} title={`Add ${r.symbol} to the watchlist`}>+ADD</button></span>}
              </td>
              {kind === 'chg'
                ? <><td className="num">{fmt(r.price)}</td><td className={`num ${(r.change_pct ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>{r.change_pct != null ? `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}%` : '–'}</td></>
                : <><td className="num">{r.trades?.toLocaleString() ?? '–'}</td><td className="num">{r.turnover != null ? `${(r.turnover / 1e6).toFixed(1)}M` : '–'}</td></>}
            </tr>
          ))}
          {!rows.length && <tr><td colSpan={3} className="text-muted">no data yet</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

export default function Market() {
  const [m, setM] = useState<MarketData | null>(null)
  const [wl, setWl] = useState<Set<string>>(new Set())
  const [msg, setMsg] = useState<string | null>(null)
  const [hl, setHl] = useState<Headline[]>([])
  const box = useRef<HTMLDivElement>(null)
  const fbox = useRef<HTMLDivElement>(null)
  const load = () => api.market().then(setM).catch(() => {})
  useEffect(() => { api.headlines().then(setHl).catch(() => {}) }, [])
  useEffect(() => { load(); api.watchlist().then(w => setWl(new Set(w.map(x => x.symbol)))).catch(() => {}) }, [])
  useEffect(() => {
    if (!m || !box.current) return
    const chart = baseChart(box.current)
    const s = chart.addSeries(LineSeries, { color: css('--amber'), lineWidth: 2, priceLineVisible: false })
    s.setData(m.series.filter(r => r.aspi != null).map(r => ({ time: r.time, value: r.aspi! })))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [m])
  const flow = (m?.series ?? []).filter(r => r.foreign_buy != null && r.foreign_sell != null)
  useEffect(() => {
    if (!fbox.current || flow.length < 5) return
    const chart = baseChart(fbox.current)
    const s = chart.addSeries(HistogramSeries, { priceLineVisible: false, priceFormat: { type: 'custom', formatter: (v: number) => mn(v) } })
    s.setData(flow.map(r => { const v = r.foreign_buy! - r.foreign_sell!; return { time: r.time, value: v, color: v >= 0 ? css('--bull') : css('--bear') } }))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [m]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!m) return <Spinner />
  const last = [...m.series].reverse().find(r => r.aspi != null)
  const br = [...m.series].reverse().find(r => r.advancers != null)
  const val = [...m.series].reverse().find(r => r.per != null)
  const lf = flow[flow.length - 1]
  const net = lf ? lf.foreign_buy! - lf.foreign_sell! : null
  const add = async (sym: string) => {
    setMsg(`Adding ${sym}…`)
    try { await api.addStock(sym); setWl(new Set([...wl, sym])); setMsg(`${sym} added; intraday history loads in the background.`) }
    catch (e) { setMsg(String((e as Error).message)) }
  }
  return (
    <div className="mx-auto max-w-[1700px] space-y-2 px-3 py-3">
      <h1 className="text-[14px] font-semibold uppercase tracking-wider text-amber">MKT · Market</h1>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6 [&>*]:min-w-0">
        <Stat label="ASPI" value={fmt(last?.aspi, 0)} sub={last?.time} />
        <Stat label="Regime" value={m.regime.regime.replace('_', '-')} tone={m.regime.regime === 'risk_on' ? 'bull' : m.regime.regime === 'risk_off' ? 'bear' : undefined} sub={m.regime.score != null ? `${m.regime.score.toFixed(0)}/100` : ''} />
        <Stat label="Advancers / decliners" value={br ? `${br.advancers} / ${br.decliners}` : '–'} sub={br?.time} />
        <Stat label="Foreign net (LKR)" value={mn(net)} tone={net == null ? undefined : net >= 0 ? 'bull' : 'bear'} sub={lf ? `${lf.time} · ${lf.foreign_trades ?? '–'} trades` : 'records nightly'} />
        <Stat label="Market PER / PBV" value={val ? `${val.per ?? '–'} / ${val.pbv ?? '–'}` : '–'} sub={val ? `DY ${val.dy ?? '–'}% · ${val.time}` : ''} />
        <Stat label="Session" value={m.open ? 'open' : 'closed'} tone={m.open ? 'bull' : undefined} sub="from CSE market status" />
      </div>
      <Section title="All Share Price Index (ASPI)"><div ref={box} className="h-72" /></Section>
      <MarketRisk />

      <div className="grid gap-2 lg:grid-cols-2 [&>*]:min-w-0">
        <Section title="Foreign net flow (purchases − sales, LKR)">
          {flow.length >= 5 ? <div ref={fbox} className="h-48" />
            : <p className="text-[12px] text-muted">{flow.length ? `${flow.length} session${flow.length > 1 ? 's' : ''} recorded so far; the chart appears from 5.` : 'No foreign-flow data yet.'}</p>}
          <table className="bb-table mt-2 w-full">
            <thead><tr><th className="text-left">Date</th><th>Foreign buy</th><th>Foreign sell</th><th>Net</th></tr></thead>
            <tbody>{[...flow].reverse().slice(0, 8).map(r => {
              const v = r.foreign_buy! - r.foreign_sell!
              return <tr key={r.time}><td className="text-left num">{r.time}</td><td className="num">{m1(r.foreign_buy)}</td><td className="num">{m1(r.foreign_sell)}</td><td className={`num ${v >= 0 ? 'text-bull' : 'text-bear'}`}>{mn(v)}</td></tr>
            })}</tbody>
          </table>
          <p className="mt-1 text-[11px] text-muted">From CSE's daily market summary, which only returns the last two sessions, so the history builds up each evening. Shown only, not used in any score.</p>
        </Section>

        <Section title={`Sector indices${m.sectors?.date ? ` · ${m.sectors.date}` : ''}`}>
          <div className="max-h-[22rem] overflow-y-auto scroll-thin">
            <table className="bb-table w-full [&_td]:whitespace-normal">
              <thead><tr><th className="text-left">Sector</th><th>Index</th><th>Chg%</th><th>Turnover</th><th className="text-left">Your stocks</th></tr></thead>
              <tbody>{(m.sectors?.rows ?? []).map(r => (
                <tr key={r.code}>
                  <td className="text-left"><span className="text-amber">{r.code}</span> <span className="text-muted">{r.name}</span></td>
                  <td className="num">{fmt(r.value)}</td>
                  <td className={`num ${(r.change_pct ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>{r.change_pct != null ? `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}%` : '–'}</td>
                  <td className="num">{r.turnover != null ? `${(r.turnover / 1e6).toFixed(1)}M` : '–'}</td>
                  <td className="text-left">{r.watchlist.map(s => <Link key={s} to={`/stock/${s}`} className="mr-1 text-amber hover:underline">{ticker(s)}</Link>)}</td>
                </tr>
              ))}
              {!m.sectors?.rows.length && <tr><td colSpan={5} className="text-muted">Recorded each evening from CSE's sector list.</td></tr>}</tbody>
            </table>
          </div>
          <p className="mt-1 text-[11px] text-muted">S&amp;P/CSE sector indices. CSE doesn't say which sector a stock is in, so set it per stock in SET &lt;GO&gt; to see it here and on the stock page.</p>
        </Section>
      </div>

      <Section title={`Today's movers${m.movers?.updated ? ` · ${m.movers.updated.slice(11, 16)}` : ''}`}>
        {msg && <p className="mb-1 text-[12px] text-yellow">{msg}</p>}
        <div className="grid gap-3 sm:grid-cols-3 [&>*]:min-w-0">
          <MoverTable title="Top gainers" rows={m.movers?.gainers ?? []} kind="chg" wl={wl} onAdd={add} />
          <MoverTable title="Top losers" rows={m.movers?.losers ?? []} kind="chg" wl={wl} onAdd={add} />
          <MoverTable title="Most active (trades)" rows={m.movers?.active ?? []} kind="active" wl={wl} onAdd={add} />
        </div>
      </Section>

      <WorldPanel />

      <Section title="Business headlines (Sri Lankan papers and Google News)">
        {hl.length === 0 ? <p className="text-[12px] text-muted">No headlines fetched yet; they are fetched every few hours and each evening.</p> : (
          <ul className="max-h-[24rem] divide-y divide-line/60 overflow-y-auto scroll-thin">{hl.map(h => (
            <li key={h.link} className="flex flex-wrap items-start gap-x-2 py-1.5 text-[12px] sm:flex-nowrap">
              <span className="w-28 shrink-0 text-[11px] text-muted num">{h.published?.slice(0, 16).replace('T', ' ')}<div className="truncate">{h.source}</div></span>
              <span className="min-w-0 flex-1">
                <a className="text-fg hover:text-amber hover:underline" href={h.link} target="_blank" rel="noreferrer">{h.title}</a>
                {h.reason && <span className="block text-[11px] text-muted">{h.reason}</span>}
              </span>
              {(h.symbols ?? []).map(s => <Link key={s} to={`/stock/${s}?tab=cn`} className="chip shrink-0 border border-amber/60 text-amber">{ticker(s)}</Link>)}
            </li>
          ))}</ul>
        )}
        <p className="mt-1 text-[11px] text-muted">Titles and links only; read the article on the source site. A chip means the headline is about one of your stocks.</p>
      </Section>

      <Section title="What drives the market regime score">
        <ul className="list-disc space-y-1 pl-5 text-sm">{m.regime.factors.map(f => <li key={f}>{f}</li>)}</ul>
        <p className="mt-2 text-xs text-muted">Turnover and breadth build up from the day you start running the app (CSE history gives ASPI closes only). On 5M–4H charts the Classic engine uses the ASPI on the same timeframe instead. World markets (oil, US shares, USD/LKR) are shown above with their measured link to the ASPI; none is scored.</p>
      </Section>
    </div>
  )
}
