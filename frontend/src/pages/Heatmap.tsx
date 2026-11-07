import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fmt, fmtBig, ticker, type HeatStock, type Heatmap as HeatData } from '../api'
import { squarify2 } from '../chart/treemap'
import { ErrorBox, Spinner } from '../components/ui'

type SizeBy = 'market_cap' | 'turnover'
const SCALE = 3                       // a move of this many % is the full colour

/** Green for up, red for down, mixed into the dark panel: the strongest is 50% so white text stays readable on it. */
const heat = (p: number) => {
  const k = Math.min(Math.abs(p) / SCALE, 1) * 50
  return `color-mix(in srgb, var(${p >= 0 ? '--bull' : '--bear'}) ${k.toFixed(0)}%, var(--panel-3))`
}
const sgn = (x: number, nd = 2) => `${x >= 0 ? '+' : ''}${x.toFixed(nd)}%`

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setW(el.clientWidth))
    ro.observe(el); setW(el.clientWidth)
    return () => ro.disconnect()
  })
  return [ref, w] as const
}

const tip = (s: HeatStock) => `${s.name}\n${ticker(s.symbol)} · ${s.sector}${s.industry ? ' / ' + s.industry : ''}\nLast ${fmt(s.price)} (${sgn(s.change_pct)})\nMarket cap LKR ${fmtBig(s.market_cap)} · turnover ${fmtBig(s.turnover)}${s.mine ? '\nOn your lists' : ''}`

export default function Heatmap() {
  const go = useNavigate()
  const [d, setD] = useState<HeatData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [size, setSize] = useState<SizeBy>('market_cap')
  const [onlyMine, setOnlyMine] = useState(false)
  const [box, width] = useWidth<HTMLDivElement>()
  useEffect(() => {
    const load = () => api.heatmap().then(x => { setD(x); setErr(null) }).catch(e => setErr(String(e.message ?? e)))
    load()
    const t = window.setInterval(load, 60000)
    return () => window.clearInterval(t)
  }, [])

  const stocks = useMemo(() => (d?.stocks ?? []).filter(s => (!onlyMine || s.mine) && (size === 'market_cap' || s.turnover > 0)), [d, onlyMine, size])
  const height = width < 640 ? 620 : Math.max(480, Math.min(720, Math.round(width * 0.46)))
  const groups = useMemo(() => {
    const by = new Map<string, HeatStock[]>()
    stocks.forEach(s => by.set(s.sector, [...(by.get(s.sector) ?? []), s]))
    return squarify2([...by.entries()].map(([key, items]) => ({ key, items: items.map(s => ({ item: s, value: s[size] })) })), { x: 0, y: 0, w: width, h: height }, 16, 1)
  }, [stocks, size, width, height])

  const sectors = useMemo(() => {
    const by = new Map<string, { n: number; cap: number; move: number; up: number; down: number }>()
    stocks.forEach(s => {
      const x = by.get(s.sector) ?? { n: 0, cap: 0, move: 0, up: 0, down: 0 }
      x.n++; x.cap += s.market_cap; x.move += s.market_cap * s.change_pct; x.up += s.change_pct > 0 ? 1 : 0; x.down += s.change_pct < 0 ? 1 : 0
      by.set(s.sector, x)
    })
    return [...by.entries()].map(([name, x]) => ({ name, ...x, pct: x.cap ? x.move / x.cap : 0 })).sort((a, b) => b.cap - a.cap)
  }, [stocks])
  const total = useMemo(() => {
    const cap = stocks.reduce((a, s) => a + s.market_cap, 0)
    return { pct: cap ? stocks.reduce((a, s) => a + s.market_cap * s.change_pct, 0) / cap : 0, up: stocks.filter(s => s.change_pct > 0).length, down: stocks.filter(s => s.change_pct < 0).length }
  }, [stocks])
  const pctOf = (key: string) => sectors.find(s => s.name === key)?.pct ?? 0

  return (
    <div className="mx-auto max-w-[1700px] px-3 py-3">
      <section className="card">
        <div className="panel-title flex-wrap">
          <h1 className="flex-1">HMAP · Market heatmap <span className="font-normal normal-case text-muted">size = {size === 'market_cap' ? 'market cap' : 'today’s turnover'}, colour = change on the day</span></h1>
          <span className="flex gap-1" role="radiogroup" aria-label="Size by">
            {([['market_cap', 'Market cap'], ['turnover', 'Turnover']] as [SizeBy, string][]).map(([k, l]) =>
              <button key={k} role="radio" aria-checked={size === k} className="btn !py-0.5" aria-pressed={size === k} onClick={() => setSize(k)}>{l}</button>)}
          </span>
          <label className="flex items-center gap-1 text-[11px] font-normal normal-case"><input type="checkbox" checked={onlyMine} onChange={e => setOnlyMine(e.target.checked)} /> my stocks only</label>
        </div>
        {err && <div className="p-2"><ErrorBox msg={err} /></div>}
        {!d ? (err ? null : <Spinner />) : (
          <>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-3 py-1.5 text-[12px]">
              <span>{onlyMine ? 'MY STOCKS' : 'MARKET'} <b className={`num ${total.pct >= 0 ? 'text-bull' : 'text-bear'}`}>{sgn(total.pct)}</b> <span className="text-muted">weighted by market cap</span></span>
              <span className="num"><b className="text-bull">{total.up}</b> up · <b className="text-bear">{total.down}</b> down · {stocks.length - total.up - total.down} unchanged</span>
              <span className="ml-auto flex items-center gap-1 text-[11px] text-muted" aria-label="Colour scale">
                <span className="num">−{SCALE}%</span>
                <span className="h-2.5 w-24" style={{ background: `linear-gradient(90deg, ${heat(-SCALE)}, ${heat(0)}, ${heat(SCALE)})` }} />
                <span className="num">+{SCALE}%</span>
              </span>
            </div>
            {stocks.length === 0 ? <p className="p-6 text-center text-muted">{onlyMine ? 'None of your stocks has traded yet.' : 'Nothing to show.'}</p> : (
              <div ref={box} className="relative w-full overflow-hidden bg-black" style={{ height }} role="group" aria-label="Heatmap of listed stocks by sector">
                {width > 0 && groups.map(g => (
                  <div key={g.key} className="absolute bg-panel-2 shadow-[inset_0_0_0_1px_#000]" style={{ left: g.rect.x, top: g.rect.y, width: g.rect.w, height: g.rect.h }} title={g.head ? undefined : `${g.key}: ${sgn(pctOf(g.key))}`}>
                    {g.head > 0 && <div className="truncate px-1 text-[10px] font-semibold uppercase leading-[16px] tracking-wide text-amber" title={`${g.key}: ${sgn(pctOf(g.key))}`}>
                      {g.key} <span className={pctOf(g.key) >= 0 ? 'text-bull' : 'text-bear'}>{g.rect.w > 90 ? sgn(pctOf(g.key), 1) : ''}</span>
                    </div>}
                    {g.tiles.map(t => {
                      const s = t.item, r = t.rect
                      const showName = r.w >= 40 && r.h >= 26, showPct = r.w >= 40 && r.h >= 38
                      return (
                        <button key={s.symbol} title={tip(s)} aria-label={`${ticker(s.symbol)} ${sgn(s.change_pct)}`}
                                className={`absolute overflow-hidden border border-black p-0.5 text-left leading-tight text-fg hover:z-10 hover:outline hover:outline-1 hover:outline-fg ${s.mine && !onlyMine ? 'outline outline-1 -outline-offset-2 outline-amber' : ''}`}
                                style={{ left: r.x - g.rect.x, top: r.y - g.rect.y, width: r.w, height: r.h, background: heat(s.change_pct), ...(r.w < 14 || r.h < 14 ? { padding: 0 } : {}), ...(r.w < 5 || r.h < 5 ? { borderWidth: 0 } : {}) }}
                                onClick={() => go(s.mine ? `/stock/${s.symbol}` : `/quote/${s.symbol}`)}>
                          {showName && <span className="block truncate text-[10px] font-semibold">{ticker(s.symbol)}</span>}
                          {showPct && <span className="block text-[10px] num">{sgn(s.change_pct, 1)}</span>}
                        </button>
                      )
                    })}
                  </div>
                ))}
              </div>
            )}
            <div className="grid gap-3 border-t border-line p-3 lg:grid-cols-2 [&>*]:min-w-0">
              <div>
                <div className="label mb-1">Sectors (TradingView classification)</div>
                <div className="scroll-thin overflow-x-auto">
                  <table className="bb-table">
                    <thead><tr><th>Sector</th><th className="text-right">Stocks</th><th className="text-right">Change</th><th className="text-right">Up / down</th><th className="hidden text-right sm:table-cell">Market cap</th></tr></thead>
                    <tbody>{[...sectors].sort((a, b) => b.pct - a.pct).map(x => (
                      <tr key={x.name}><td>{x.name}</td><td className="text-right num">{x.n}</td>
                        <td className={`text-right num ${x.pct >= 0 ? 'text-bull' : 'text-bear'}`}>{sgn(x.pct)}</td>
                        <td className="text-right num"><span className="text-bull">{x.up}</span> / <span className="text-bear">{x.down}</span></td>
                        <td className="hidden text-right num sm:table-cell">{fmtBig(x.cap)}</td></tr>))}</tbody>
                  </table>
                </div>
              </div>
              <div>
                <div className="label mb-1">S&amp;P/CSE sector indices {d.indices_date ? `· ${d.indices_date}` : ''}</div>
                {d.indices.length === 0 ? <p className="text-[12px] text-muted">No index data yet.</p> : (
                  <div className="flex flex-wrap gap-1.5">
                    {[...d.indices].sort((a, b) => (b.change_pct ?? 0) - (a.change_pct ?? 0)).map(x => (
                      <span key={x.code} className="chip border border-line" title={x.name} style={{ background: heat(x.change_pct ?? 0) }}>{x.code} <b className="num">{x.change_pct == null ? '–' : sgn(x.change_pct)}</b></span>
                    ))}
                  </div>
                )}
                <p className="mt-3 text-[11px] text-muted">Prices and market caps are from the CSE trade summary (refreshed each minute); sectors and industries come from TradingView{d.meta_age_days != null ? `, read ${d.meta_age_days} days ago` : ''}. Amber outline = on one of your lists. Click a tile to open it; a stock you don’t follow opens as a quote card. Stocks that haven’t traded today keep their last price and show no change.</p>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
