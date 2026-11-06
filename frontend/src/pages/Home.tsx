// The home page: tabs for your portfolio, your named watchlists (Watchlist 1, 2, 3 ...) and every listed stock.
// The tab is in the address (/?view=portfolio | all | <list id>) and remembered for next time.
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type ListsInfo, type WatchList } from '../api'
import RegimeBanner from '../components/RegimeBanner'
import AllStocks from './AllStocks'
import Watchlist, { type Scope } from './Watchlist'

const KEY = 'cse_home_view'
const stored = () => { try { return localStorage.getItem(KEY) } catch { return null } }

/** Rename / delete buttons in the title bar of a list. */
function ListActions({ list, onDone, canDelete }: { list: WatchList; onDone: (view?: string) => void; canDelete: boolean }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(list.name)
  const [err, setErr] = useState<string | null>(null)
  const save = async (e: FormEvent) => {
    e.preventDefault()
    try { await api.renameList(list.id, name); setEditing(false); setErr(null); onDone() } catch (x) { setErr((x as Error).message) }
  }
  if (editing) return (
    <form className="flex items-center gap-1" onSubmit={save}>
      <input className="input !py-0.5 w-40" value={name} maxLength={40} autoFocus onChange={e => { setName(e.target.value); setErr(null) }} aria-label="List name"
             onKeyDown={e => { if (e.key === 'Escape') { setEditing(false); setName(list.name); setErr(null) } }} />
      <button className="btn btn-primary !py-0.5">Save</button>
      <button type="button" className="btn !py-0.5" onClick={() => { setEditing(false); setName(list.name); setErr(null) }}>Cancel</button>
      {err && <span role="alert" className="text-[11px] text-bear">{err}</span>}
    </form>
  )
  return (
    <span className="flex items-center gap-1">
      <button className="btn !py-0.5" onClick={() => setEditing(true)}>Rename</button>
      {canDelete && <button className="btn !py-0.5 !text-bear" onClick={async () => {
        if (!confirm(`Delete the list "${list.name}"? Stocks that are only on this list stop being tracked.`)) return
        try { await api.deleteList(list.id); onDone('deleted') } catch (x) { setErr((x as Error).message) }
      }}>Delete list</button>}
      {err && <span role="alert" className="text-[11px] text-bear">{err}</span>}
    </span>
  )
}

export default function Home() {
  const [sp, setSp] = useSearchParams()
  const [info, setInfo] = useState<ListsInfo | null>(null)
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const loadLists = useCallback(() => api.lists().then(setInfo).catch(e => setErr(String(e.message ?? e))), [])
  useEffect(() => { loadLists() }, [loadLists])

  const lists = info?.lists ?? []
  const raw = sp.get('view') ?? stored()
  const fromUrl = sp.get('view')
  // opening /?view=all (the ALL command, a link) is remembered like clicking the tab
  useEffect(() => { if (fromUrl) { try { localStorage.setItem(KEY, fromUrl) } catch { /* storage blocked */ } } }, [fromUrl])
  const list = lists.find(l => String(l.id) === raw) ?? (raw === 'portfolio' || raw === 'all' ? undefined : lists[0])
  const view: 'portfolio' | 'all' | 'list' | null = raw === 'portfolio' ? 'portfolio' : raw === 'all' ? 'all' : list ? 'list' : null
  const key = view === 'list' ? String(list!.id) : view

  const go = (v: string) => {
    try { localStorage.setItem(KEY, v) } catch { /* storage blocked */ }
    setSp({ view: v }, { replace: true })
    setErr(null)
  }

  const create = async (e: FormEvent) => {
    e.preventDefault()
    try {
      const r = await api.createList(newName)
      setNewName(''); setAdding(false); await loadLists(); go(String(r.id))
    } catch (x) { setErr((x as Error).message) }
  }

  const tab = (k: string, label: string, count?: number) => (
    <button key={k} role="tab" aria-selected={key === k} className="tab" onClick={() => go(k)}>
      {label}{count != null && <span className="ml-1 text-muted">{count}</span>}
    </button>
  )

  const scope: Scope | null = view === 'portfolio' ? { kind: 'portfolio' } : view === 'list' ? { kind: 'list', id: list!.id, name: list!.name } : null

  return (
    <div className="mx-auto max-w-[1700px] px-3 py-3">
      <RegimeBanner />
      <div className="mb-2 flex flex-wrap items-center border-b border-line" role="tablist" aria-label="Portfolio, watchlists and all stocks">
        <div className="scroll-thin flex min-w-0 flex-1 items-center overflow-x-auto">
          {tab('portfolio', 'Portfolio', info?.portfolio)}
          {lists.map(l => tab(String(l.id), l.name, l.count))}
          {info && lists.length < info.max_lists && (
            <button className="tab" aria-label="New list" title="New list" onClick={() => { setAdding(a => !a); setErr(null) }}>+</button>
          )}
        </div>
        {tab('all', 'All stocks')}
      </div>
      {adding && (
        <form className="mb-2 flex flex-wrap items-center gap-1" onSubmit={create}>
          <input className="input !py-0.5 w-56" placeholder="New list name (e.g. Banks)" value={newName} maxLength={40} autoFocus
                 onChange={e => { setNewName(e.target.value); setErr(null) }} aria-label="New list name" />
          <button className="btn btn-primary !py-0.5" disabled={!newName.trim()}>Create list</button>
          <button type="button" className="btn !py-0.5" onClick={() => { setAdding(false); setErr(null) }}>Cancel</button>
        </form>
      )}
      {err && <p role="alert" className="mb-2 border border-bear/60 bg-bear/10 px-2 py-1 text-[12px] text-bear">{err}</p>}
      {view === 'all' ? <AllStocks onChanged={loadLists} />
        : scope ? <Watchlist key={key} scope={scope} onChanged={loadLists}
            actions={view === 'list' ? <ListActions key={list!.id + list!.name} list={list!} canDelete={lists.length > 1}
              onDone={v => { loadLists(); if (v === 'deleted') { try { localStorage.removeItem(KEY) } catch { /* */ } setSp({}, { replace: true }) } }} /> : undefined} />
        : null}
    </div>
  )
}
