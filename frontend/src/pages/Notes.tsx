import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ticker, type NoteRow } from '../api'
import NotesPanel from '../components/NotesPanel'

export default function Notes() {
  const [q, setQ] = useState('')
  const [tag, setTag] = useState('')
  const [syms, setSyms] = useState<string[]>([])
  const [all, setAll] = useState<NoteRow[]>([])
  const onLoaded = useCallback((r: NoteRow[]) => setAll(r), [])
  useEffect(() => {
    Promise.all([api.watchlist(), api.positions()]).then(([w, p]) => {
      setSyms([...new Set([...w.map(x => x.symbol), ...p.map(x => x.symbol)])].sort())
    }).catch(() => {})
  }, [])
  const tags = useMemo(() => {
    const c = new Map<string, number>()
    all.forEach(n => n.tags.forEach(t => c.set(t, (c.get(t) ?? 0) + 1)))
    return [...c.entries()].sort((a, b) => b[1] - a[1])
  }, [all])

  return (
    <div className="mx-auto max-w-[1100px] px-3 py-3">
      <h1 className="mb-2 text-[14px] font-semibold uppercase tracking-wider text-amber">NOTE · Notes <span className="text-[12px] font-normal normal-case text-muted">{all.length} saved</span></h1>
      <section className="card">
        <div className="panel-title">
          <input className="input flex-1 !py-0.5" type="search" value={q} onChange={e => setQ(e.target.value)} placeholder={`Search notes, stocks (${syms.slice(0, 2).map(ticker).join(', ') || 'JKH'}…) or tags`} aria-label="Search notes" />
        </div>
        {tags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-2.5 py-1.5 text-[11px]">
            <span className="label">Tags</span>
            <button className={`chip ${tag === '' ? 'bg-amber text-black' : 'bg-panel-3 text-muted'}`} onClick={() => setTag('')}>all</button>
            {tags.map(([t, n]) => <button key={t} className={`chip ${tag === t ? 'bg-amber text-black' : 'bg-panel-3 text-yellow'}`} onClick={() => setTag(tag === t ? '' : t)}>#{t} <span className="opacity-70">{n}</span></button>)}
          </div>
        )}
        <NotesPanel symbols={syms} q={q} tag={tag} onTag={t => setTag(tag === t ? '' : t)} onLoaded={onLoaded} limit={1000} />
      </section>
    </div>
  )
}
