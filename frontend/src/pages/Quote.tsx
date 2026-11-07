import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmt, fmtBig, ticker, type ListsInfo, type QuoteCard } from '../api'
import { KeyStatsStrip } from '../components/KeyStats'
import { ErrorBox, Spinner } from '../components/ui'

/** Any listed stock, tracked or not. Not tracked = no price history or analysis yet: it is a read-only card with an Add button. */
export default function Quote() {
  const { symbol = '' } = useParams()
  const go = useNavigate()
  const [q, setQ] = useState<QuoteCard | null>(null)
  const [lists, setLists] = useState<ListsInfo | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    setQ(null); setErr(null)
    api.quoteCard(symbol).then(setQ).catch(e => setErr(String(e.message ?? e)))
    api.lists().then(setLists).catch(() => {})
  }, [symbol])
  // a stock that is already on a list (or held) has the full page
  useEffect(() => { if (q?.tracked && (q.lists.length > 0 || q.held)) go(`/stock/${symbol}`, { replace: true }) }, [q, symbol, go])

  const add = async (listId: number) => {
    setBusy(true); setErr(null)
    try { await api.addStock(symbol, listId); go(`/stock/${symbol}`) } catch (e) { setErr(String((e as Error).message)); setBusy(false) }
  }
  const r = q?.quote
  return (
    <div className="mx-auto max-w-[1100px] px-3 py-3">
      {err && <ErrorBox msg={err} />}
      {!q && !err && <Spinner />}
      {q && r && (
        <section className="card">
          <header className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2">
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
              <h1 className="text-[15px] font-semibold tracking-wide text-amber">{ticker(symbol)}</h1>
              <span className="truncate text-[12px] text-muted">{r.name}</span>
              <span className="chip bg-panel-3 text-muted">QUOTE · not followed</span>
            </div>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 num">
              <b className="text-[20px] text-fg">{fmt(r.price)}</b>
              {r.change_pct != null && <span className={`text-[13px] ${r.change_pct >= 0 ? 'text-bull' : 'text-bear'}`}>{r.change_pct >= 0 ? '▲ +' : '▼ '}{r.change_pct.toFixed(2)}%</span>}
              <span className="text-[11px] text-muted">VOL <span className="text-fg">{fmtBig(r.volume)}</span> · TURNOVER <span className="text-fg">{fmtBig(r.turnover)}</span>{r.traded ? ` · last traded ${r.traded}` : ''}</span>
            </div>
            <div className="ml-auto flex items-center gap-2">
              {busy ? <span className="text-[12px] text-muted">Adding: fetching a year of history and analysing it…</span> : (
                <select className="input !py-0.5" value="" onChange={e => { if (e.target.value) add(Number(e.target.value)) }} aria-label={`Add ${ticker(symbol)} to a list`}>
                  <option value="">+ Add to a list to analyse it…</option>{lists?.lists.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
              )}
            </div>
          </header>
          <KeyStatsStrip k={q.stats} />
          <div className="space-y-1 p-3 text-[12px] text-muted">
            <p>This stock is listed on the CSE but isn’t on any of your lists, so it has no stored price history, charts or engine analysis. Add it to a list and the app fetches a year of history, then analyses it every evening like the rest.</p>
            <p>The figures above are the CSE’s own summary, read now. <Link className="text-amber hover:underline" to="/?view=all">All stocks</Link> · <Link className="text-amber hover:underline" to="/heatmap">Heatmap</Link></p>
          </div>
        </section>
      )}
    </div>
  )
}
