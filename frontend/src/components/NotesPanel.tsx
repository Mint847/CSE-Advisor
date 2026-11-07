import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ticker, type NoteRow } from '../api'
import { when } from './Inbox'
import { Spinner } from './ui'

const parseTags = (s: string) => s.split(/[,\s]+/).map(t => t.trim()).filter(Boolean)

function NoteItem({ n, onChange, onTag, showSymbol }: { n: NoteRow; onChange: () => void; onTag?: (t: string) => void; showSymbol: boolean }) {
  const [edit, setEdit] = useState(false)
  const [body, setBody] = useState(n.body)
  const [tags, setTags] = useState(n.tags.join(', '))
  const [err, setErr] = useState<string | null>(null)
  const save = async () => {
    setErr(null)
    try { await api.editNote(n.id, { body, tags: parseTags(tags) }); setEdit(false); onChange() } catch (e) { setErr(String((e as Error).message)) }
  }
  return (
    <article className={`border-t border-line/60 px-2.5 py-2 text-[12px] ${n.pinned ? 'bg-amber/5' : ''}`}>
      <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted">
        {n.pinned && <span className="text-amber" title="Pinned">★</span>}
        {showSymbol && (n.symbol ? <Link to={`/stock/${n.symbol}?tab=note`} className="font-semibold text-amber">{ticker(n.symbol)}</Link> : <span>general</span>)}
        <span>{when(n.updated_at)}</span>
        {n.position_id != null && <span className="chip bg-panel-3 text-muted">position</span>}
        {n.tags.map(t => <button key={t} className="chip bg-panel-3 text-yellow hover:bg-amber hover:text-black" onClick={() => onTag?.(t)} title={`Show notes tagged ${t}`}>#{t}</button>)}
        <span className="ml-auto flex gap-2">
          <button className="hover:text-amber" onClick={async () => { await api.editNote(n.id, { pinned: !n.pinned }); onChange() }}>{n.pinned ? 'unpin' : 'pin'}</button>
          <button className="hover:text-amber" onClick={() => { setBody(n.body); setTags(n.tags.join(', ')); setEdit(e => !e) }}>{edit ? 'cancel' : 'edit'}</button>
          <button className="hover:text-bear" onClick={async () => { if (confirm('Delete this note?')) { await api.deleteNote(n.id); onChange() } }}>delete</button>
        </span>
      </div>
      {edit ? (
        <div className="space-y-1.5">
          <textarea className="input w-full" rows={4} value={body} onChange={e => setBody(e.target.value)} aria-label="Note text" />
          <input className="input w-full" value={tags} onChange={e => setTags(e.target.value)} placeholder="tags, separated by commas" aria-label="Tags" />
          <div className="flex items-center gap-2"><button className="btn btn-primary" onClick={save}>Save</button>{err && <span className="text-bear">{err}</span>}</div>
        </div>
      ) : <p className="whitespace-pre-wrap break-words text-fg">{n.body}</p>}
    </article>
  )
}

/** Notes for one stock (symbol given) or all of them. Search and tag filtering happen here, on what was loaded. */
export default function NotesPanel({ symbol, positionId, symbols, q = '', tag = '', onTag, onLoaded, limit }: {
  symbol?: string; positionId?: number; symbols?: string[]; q?: string; tag?: string
  onTag?: (t: string) => void; onLoaded?: (rows: NoteRow[]) => void; limit?: number
}) {
  const [rows, setRows] = useState<NoteRow[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [body, setBody] = useState('')
  const [tags, setTags] = useState('')
  const [sym, setSym] = useState(symbol ?? '')
  const [pinned, setPinned] = useState(false)
  const [busy, setBusy] = useState(false)

  const loaded = useRef(onLoaded)                       // callers may pass a new function every render: don't refetch for that
  useEffect(() => { loaded.current = onLoaded })
  const load = useCallback(() => {
    api.notes({ symbol, position_id: positionId, limit }).then(r => { setRows(r); loaded.current?.(r) }).catch(e => setErr(String(e.message)))
  }, [symbol, positionId, limit])
  useEffect(() => { load() }, [load])

  const add = async () => {
    setErr(null); setBusy(true)
    try {
      await api.addNote({ body, symbol: symbol ?? (sym || null), position_id: positionId ?? null, tags: parseTags(tags), pinned })
      setBody(''); setTags(''); setPinned(false); load()
    } catch (e) { setErr(String((e as Error).message)) } finally { setBusy(false) }
  }
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase(), t = tag.trim().toLowerCase()
    return (rows ?? []).filter(n => (!t || n.tags.includes(t)) && (!s || n.body.toLowerCase().includes(s) || (n.symbol ?? '').toLowerCase().includes(s) || n.tags.some(x => x.includes(s))))
  }, [rows, q, tag])

  return (
    <div>
      <form className="space-y-1.5 border-b border-line p-2.5" onSubmit={e => { e.preventDefault(); add() }}>
        <textarea className="input w-full" rows={3} value={body} onChange={e => setBody(e.target.value)} aria-label="New note"
                  placeholder={symbol ? `A note on ${ticker(symbol)}: why you like it, what would change your mind…` : 'A note: an idea, a lesson, a plan…'}
                  onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && body.trim()) { e.preventDefault(); add() } }} />
        <div className="flex flex-wrap items-center gap-2">
          {!symbol && positionId == null && (
            <select className="input" value={sym} onChange={e => setSym(e.target.value)} aria-label="Stock">
              <option value="">General (no stock)</option>{symbols?.map(s => <option key={s} value={s}>{ticker(s)}</option>)}
            </select>
          )}
          <input className="input min-w-[10rem] flex-1" value={tags} onChange={e => setTags(e.target.value)} placeholder="tags, e.g. plan, lesson, review" aria-label="Tags" />
          <label className="flex items-center gap-1 text-[12px]"><input type="checkbox" checked={pinned} onChange={e => setPinned(e.target.checked)} /> pin</label>
          <button className="btn btn-primary" disabled={busy || !body.trim()}>Save note</button>
        </div>
        {err && <p className="text-[12px] text-bear" role="alert">{err}</p>}
        <p className="text-[10px] text-muted">Ctrl+Enter saves. When you close a position, the review you write is saved here too, tagged <b>review</b>.</p>
      </form>
      {!rows ? <Spinner /> : shown.length === 0
        ? <p className="p-4 text-[12px] text-muted">{rows.length ? 'No notes match.' : symbol ? `No notes on ${ticker(symbol)} yet.` : 'No notes yet.'}</p>
        : shown.map(n => <NoteItem key={n.id} n={n} onChange={load} onTag={onTag} showSymbol={!symbol} />)}
    </div>
  )
}
