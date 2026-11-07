import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ticker, type Feed, type FeedItem } from '../api'
import { when } from '../components/Inbox'
import { ErrorBox, Spinner } from '../components/ui'

const TONES: [string, string][] = [['all', 'Any tone'], ['positive', 'Positive'], ['negative', 'Negative'], ['neutral', 'Neutral']]
const DAYS: [number, string][] = [[1, 'Today'], [3, '3 days'], [7, '7 days'], [14, '14 days'], [30, '30 days'], [90, '90 days']]

function Dot({ tone }: { tone: number | null }) {
  const t = tone == null ? 'neutral' : tone > 0.15 ? 'positive' : tone < -0.15 ? 'negative' : 'neutral'
  return <span className={`inline-block w-3 text-center ${t === 'positive' ? 'text-bull' : t === 'negative' ? 'text-bear' : 'text-muted'}`} title={tone == null ? 'tone not rated' : `tone ${tone > 0 ? '+' : ''}${tone.toFixed(2)} (${t})`}
               aria-label={`tone: ${t}`}>{t === 'positive' ? '▲' : t === 'negative' ? '▼' : '●'}</span>
}

function Row({ it, names }: { it: FeedItem; names: Record<string, string> }) {
  const ann = it.type === 'announcement'
  return (
    <article className="flex gap-2 border-t border-line/60 px-2.5 py-1.5 text-[12px]">
      <div className="w-[72px] shrink-0 pt-0.5 text-[10px] text-muted">{it.at ? when(it.at.length <= 16 ? it.at + ':00' : it.at) : ''}</div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
          <span className={`chip ${ann ? 'bg-amber text-black' : 'bg-panel-3 text-muted'}`}>{ann ? 'CSE' : 'NEWS'}</span>
          <Dot tone={it.tone} />
          {it.symbols.map(s => <Link key={s} to={`/stock/${s}?tab=cn`} title={names[s]} className="font-semibold text-amber">{ticker(s)}</Link>)}
          {ann && it.importance != null && it.importance >= 3 && <span className="chip border border-warn/60 text-warn" title="Material announcement">important</span>}
          {it.category && <span className="text-[10px] uppercase tracking-wide text-muted">{it.category.replace(/_/g, ' ')}</span>}
        </div>
        <div className="mt-0.5">
          {it.link ? <a href={it.link} target="_blank" rel="noreferrer noopener" className="text-fg hover:text-yellow hover:underline">{it.title}</a>
            : it.symbols[0] ? <Link to={`/stock/${it.symbols[0]}?tab=cn`} className="text-fg hover:text-yellow hover:underline">{it.title}</Link> : <span className="text-fg">{it.title}</span>}
        </div>
        <div className="text-[10px] text-muted">{it.source}{it.detail ? ` · ${it.detail}` : ''}</div>
      </div>
    </article>
  )
}

export default function News() {
  const [scope, setScope] = useState('mine')
  const [kind, setKind] = useState('all')
  const [tone, setTone] = useState('all')
  const [days, setDays] = useState(14)
  const [q, setQ] = useState('')
  const [qd, setQd] = useState('')
  const [data, setData] = useState<Feed | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { const t = window.setTimeout(() => setQd(q), 300); return () => window.clearTimeout(t) }, [q])
  useEffect(() => {
    let live = true
    const load = () => api.feed({ scope, kind, tone, q: qd, days }).then(d => { if (live) { setData(d); setErr(null) } }).catch(e => { if (live) setErr(String(e.message ?? e)) })
    load()
    const t = window.setInterval(load, 120000)
    return () => { live = false; window.clearInterval(t) }
  }, [scope, kind, tone, qd, days])

  return (
    <div className="mx-auto max-w-[1100px] px-3 py-3">
      <section className="card">
        <div className="panel-title flex-wrap">
          <h1 className="flex-1">NEWS · Announcements &amp; headlines <span className="font-normal normal-case text-muted">{data ? `${data.count} found` : ''}</span></h1>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-2.5 py-1.5">
          <span className="flex gap-1" role="radiogroup" aria-label="Whose stocks">
            {([['mine', 'My stocks'], ['all', 'All tracked']] as [string, string][]).map(([k, l]) => <button key={k} role="radio" aria-checked={scope === k} aria-pressed={scope === k} className="btn !py-0 text-[11px]" onClick={() => setScope(k)}>{l}</button>)}
          </span>
          <span className="flex gap-1" role="radiogroup" aria-label="Type">
            {([['all', 'Both'], ['announcement', 'Announcements'], ['headline', 'Headlines']] as [string, string][]).map(([k, l]) => <button key={k} role="radio" aria-checked={kind === k} aria-pressed={kind === k} className="btn !py-0 text-[11px]" onClick={() => setKind(k)}>{l}</button>)}
          </span>
          <select className="input !py-0 text-[11px]" value={tone} onChange={e => setTone(e.target.value)} aria-label="Tone">{TONES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <select className="input !py-0 text-[11px]" value={days} onChange={e => setDays(Number(e.target.value))} aria-label="How far back">{DAYS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <input className="input !py-0.5 min-w-[10rem] flex-1" type="search" value={q} onChange={e => setQ(e.target.value)} placeholder="Search title, ticker, company or category" aria-label="Search the feed" />
        </div>
        {err && <div className="p-2"><ErrorBox msg={err} /></div>}
        {!data ? (err ? null : <Spinner />) : data.items.length === 0
          ? <p className="p-6 text-center text-[12px] text-muted">Nothing matches. {scope === 'mine' ? 'Try “All tracked”, or a longer period.' : 'Try a longer period or a different search.'}</p>
          : <div>{data.items.map(it => <Row key={it.id} it={it} names={data.names} />)}</div>}
        <p className="border-t border-line px-2.5 py-1.5 text-[11px] text-muted">Announcements are the CSE’s own filings for stocks on any list; headlines come from Sri Lankan papers, CBSL and Google News (titles and links only, the same story from several outlets appears once). “My stocks” also includes market-wide and macro headlines. Tone is a keyword (or Claude, if switched on) reading, shown for context only; it never changes an engine’s verdict.</p>
      </section>
    </div>
  )
}
