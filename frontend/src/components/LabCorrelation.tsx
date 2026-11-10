// Quant Lab › Correlations: how closely CSE stocks, the ASPI, sectors, world markets and the rupee move together, and how that link
// changed over time (click a cell for the rolling chart). Series that close after the CSE are matched to the session they can affect.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type CorrOptions, type CorrResult } from '../api'
import { TimeChart, type TSeries } from './QuantCharts'
import { Explain } from './QuantFigures'
import { ErrorBox, Spinner } from './ui'

const BLUE = '#2f8fd6', MAGENTA = '#d1497a'
const fill = (v: number) => `color-mix(in srgb, ${v >= 0 ? BLUE : MAGENTA} ${Math.round(Math.min(Math.abs(v), 1) * 55)}%, var(--panel-3))`
const NO_LINES = [{ value: 0, label: '0' }]
const fmt2 = (v: number) => v.toFixed(2)

export default function LabCorrelation() {
  const [opt, setOpt] = useState<CorrOptions | null>(null)
  const [ids, setIds] = useState<string[]>([])
  const [win, setWin] = useState('3y')
  const [freq, setFreq] = useState<'daily' | 'weekly'>('daily')
  const [pair, setPair] = useState<[number, number] | null>(null)
  const [res, setRes] = useState<CorrResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.labCorrOptions().then(o => {
      setOpt(o)
      const start = [...o.indices.filter(i => ['^ASPI', '^SPX', '^USDLKR'].includes(i.id)).map(i => i.id), ...o.cse.slice(0, 3).map(c => c.id)]
      setIds(start)
    }).catch(e => setErr(String(e.message ?? e)))
  }, [])

  const run = useCallback(async (p: [number, number] | null) => {
    if (ids.length < 2) return
    setBusy(true); setErr(null)
    try { setRes(await api.labCorr({ ids, window: win, freq, ...(p ? { pair: p } : {}) })) } catch (e) { setErr(String((e as Error).message ?? e)); setRes(null) } finally { setBusy(false) }
  }, [ids, win, freq])
  useEffect(() => { if (opt && ids.length >= 2) { setPair(null); run(null) } }, [opt, win, freq]) // eslint-disable-line react-hooks/exhaustive-deps

  const roll = useMemo<TSeries[] | null>(() => res?.rolling ? [{ label: `${res.rolling.a} vs ${res.rolling.b}`, color: '#ffa028', points: res.rolling.points }] : null, [res])
  const tog = (id: string) => setIds(s => (s.includes(id) ? s.filter(x => x !== id) : s.length < (opt?.max ?? 12) ? [...s, id] : s))
  if (!opt) return err ? <ErrorBox msg={err} /> : <Spinner label="Loading…" />
  const groups: [string, { id: string; label: string }[]][] = [['Indices and world series', opt.indices], ['My stocks', opt.cse], ['World stocks', opt.world], ['Sector indices', opt.sectors]]

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted"><Explain k="correlation">Correlation</Explain> says how closely two things move together, from −1 (opposite) through 0 (unrelated) to +1 (in step). Choose up to {opt.max} series.
        Two stocks with a high correlation add little protection to each other; a low one means they diversify.</p>
      <section className="card space-y-2 p-3" aria-label="Choose series">
        {groups.filter(([, l]) => l.length > 0).map(([title, list]) => (
          <div key={title} className="flex flex-wrap items-center gap-1"><span className="label mr-1 w-full sm:w-40">{title}</span>
            {list.map(o => <button key={o.id} type="button" aria-pressed={ids.includes(o.id)} onClick={() => tog(o.id)} title={o.id}
              className={`border px-2 py-0.5 text-[11px] ${ids.includes(o.id) ? 'border-amber bg-amber/20 text-fg' : 'border-line text-muted hover:text-fg'}`}>{o.label}</button>)}
          </div>
        ))}
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <span className="flex" role="radiogroup" aria-label="Window">{['1y', '3y', '5y', 'all'].map(w => <button key={w} role="radio" aria-checked={win === w} className="btn !px-2 !py-0.5" onClick={() => setWin(w)}>{w === 'all' ? 'All' : w.toUpperCase()}</button>)}</span>
          <span className="flex" role="radiogroup" aria-label="Return frequency">{([['daily', 'Daily moves'], ['weekly', 'Weekly moves']] as const).map(([k, l]) => <button key={k} role="radio" aria-checked={freq === k} className="btn !px-2 !py-0.5" onClick={() => setFreq(k)}>{l}</button>)}</span>
          <button type="button" className="btn btn-primary" disabled={busy || ids.length < 2} onClick={() => { setPair(null); run(null) }}>{busy ? 'Working…' : 'Update'}</button>
          <span className="text-muted">{ids.length} chosen</span>
        </div>
      </section>
      {err && <ErrorBox msg={err} />}
      {res && (
        <div className={`space-y-3 ${busy ? 'opacity-60' : ''}`}>
          <div className="flex flex-wrap gap-1.5 text-[11px]"><span className="chip bg-panel-3 !h-auto !whitespace-normal !normal-case !font-normal text-muted">{res.start} → {res.end} · {res.freq}</span>
            {res.missing.length > 0 && <span className="chip border border-warn/60 !h-auto !whitespace-normal !normal-case text-warn">No stored prices for {res.missing.join(', ')}</span>}</div>
          {res.notes.length > 0 && <ul className="list-disc space-y-0.5 pl-5 text-[12px] text-warn">{res.notes.map(n => <li key={n}>{n}</li>)}</ul>}
          <div className="grid gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] [&>*]:min-w-0">
            <section className="card"><div className="panel-title"><h3>Correlation matrix <span className="font-normal normal-case text-muted">· click a cell for the rolling chart</span></h3></div>
              <div className="scroll-thin overflow-x-auto p-2">
                <table className="num text-[11px]" aria-label="Correlation matrix">
                  <thead><tr><th />{res.labels.map((l, j) => <th key={j} className="px-1.5 pb-1 text-right font-normal text-muted">{l}{res.thin[j] ? <span className="text-warn" title="often did not trade"> *</span> : ''}</th>)}</tr></thead>
                  <tbody>{res.matrix.map((row, i) => (
                    <tr key={i}><th className="pr-2 text-left font-normal text-amber">{res.labels[i]}{res.thin[i] ? <span className="text-warn" title="often did not trade"> *</span> : ''}</th>
                      {row.map((v, j) => i === j ? <td key={j} className="border border-[var(--bg)] px-1.5 py-1 text-right text-muted">–</td> : (
                        <td key={j} className={`border border-[var(--bg)] p-0 text-right ${pair && ((pair[0] === i && pair[1] === j) || (pair[0] === j && pair[1] === i)) ? 'outline outline-1 outline-amber' : ''}`}
                            style={{ background: v == null ? 'transparent' : fill(v) }} title={v == null ? 'fewer than 30 shared days' : `${res.labels[i]} and ${res.labels[j]}: ${v.toFixed(2)} over ${res.n[i][j]} ${res.freq === 'daily' ? 'days' : 'weeks'}${res.significant[i][j] ? '' : ' (could be chance)'}`}>
                          <button type="button" className={`block w-full px-1.5 py-1 text-right ${v != null && !res.significant[i][j] ? 'opacity-50' : ''}`} disabled={v == null}
                                  onClick={() => { const p: [number, number] = [Math.min(i, j), Math.max(i, j)]; setPair(p); run(p) }}>{v == null ? '·' : v.toFixed(2)}</button></td>))}
                    </tr>
                  ))}</tbody>
                </table>
                <p className="mt-1 text-[10px] text-muted">Blue = move together, magenta = move against each other; the number is always written. Faded cells could be chance. * often did not trade: its daily figures are pulled toward zero, so try weekly moves.</p>
              </div></section>
            <section className="card"><div className="panel-title"><h3>Strongest links</h3></div>
              <ul className="p-2 text-[12px]">{res.pairs.map(p => (
                <li key={`${p.a}-${p.b}`} className="flex items-baseline gap-2 border-t border-line/60 py-1 first:border-t-0"><span className="min-w-0 flex-1 truncate">{p.a} · {p.b}</span>
                  <b className={`num ${p.r >= 0 ? 'text-fg' : 'text-bear'}`}>{p.r.toFixed(2)}</b><span className="text-[10px] text-muted num">{p.n} {res.freq === 'daily' ? 'days' : 'wks'}</span></li>
              ))}</ul></section>
          </div>
          {roll && res.rolling && (
            <div className="card p-2"><TimeChart title={`${res.rolling.a} vs ${res.rolling.b}: correlation over the last ${res.rolling.window} ${res.freq === 'daily' ? 'sessions' : 'weeks'}`} series={roll} refLines={NO_LINES} fmt={fmt2} height={200} />
              <p className="px-1 text-[10px] text-muted">A link that moves a lot over time is unreliable: do not assume today’s figure will hold.</p></div>
          )}
        </div>
      )}
    </div>
  )
}
