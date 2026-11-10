// Quant Lab › Backtest: try a trading rule on the app's own stored prices (gs-quant engine, CSE fees, T-bill interest); does it beat holding, and could it be luck?
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type LabCatalog, type LabRun, type LabSaved, type LabSpec } from '../api'
import LabBuilder from './LabBuilder'
import LabResult from './LabResult'
import { ErrorBox, Spinner } from './ui'

const LAST_RUN = 'qlab:last-run'
const store = {
  get: () => { try { return sessionStorage.getItem(LAST_RUN) } catch { return null } },
  set: (v: string) => { try { sessionStorage.setItem(LAST_RUN, v) } catch { /* private window */ } },
}

function startSpec(cat: LabCatalog): LabSpec {
  const p = cat.presets.find(x => x.id === 'factor') ?? cat.presets[0]
  return { name: p.name, symbols: cat.stocks.slice(0, cat.max_symbols).map(s => s.symbol), years: cat.defaults.years, capital: cat.defaults.capital, basis: cat.defaults.basis,
    slippage_pct: cat.defaults.slippage_pct, max_positions: 5, entry: { type: 'monthly' }, ...p.spec, exit: { ...p.spec.exit } } as LabSpec
}

export default function LabBacktest() {
  const [cat, setCat] = useState<LabCatalog | null>(null)
  const [spec, setSpec] = useState<LabSpec | null>(null)
  const [saved, setSaved] = useState<LabSaved[]>([])
  const [lists, setLists] = useState<{ id: number; name: string }[]>([])
  const [run, setRun] = useState<LabRun | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    api.labCatalog().then(c => { setCat(c); setSpec(startSpec(c)) }).catch(e => setErr(String(e.message ?? e)))
    api.labSaved().then(setSaved).catch(() => {})
    api.lists().then(l => setLists(l.lists.map(x => ({ id: x.id, name: x.name })))).catch(() => {})
    const last = store.get()
    if (last) api.labStatus(last).then(r => { setRun(r); if (r.status === 'running') poll(r.id) }).catch(() => {})
    return () => { if (timer.current) window.clearTimeout(timer.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const poll = useCallback((id: string) => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(async () => {
      try {
        const r = await api.labStatus(id)
        setRun(r)
        if (r.status === 'running') poll(id)
      } catch (e) { setErr(String((e as Error).message ?? e)) }
    }, 1200)
  }, [])

  const go = async () => {
    if (!spec) return
    setErr(null)
    try {
      const r = await api.labRun(spec)
      store.set(r.id); setRun(r)
      if (r.status === 'running') poll(r.id); else if (r.status === 'done') api.labStatus(r.id).then(setRun).catch(() => {})
    } catch (e) { setErr(String((e as Error).message ?? e)) }
  }

  const fromList = async (id: number) => {
    if (!cat || !spec) return
    const have = new Set(cat.stocks.map(s => s.symbol))
    const syms = (await api.watchlist(id)).map(w => w.symbol).filter(s => have.has(s)).slice(0, cat.max_symbols)
    setSpec({ ...spec, symbols: syms, max_positions: Math.min(spec.max_positions, Math.max(1, syms.length)) })
  }
  const save = async (name: string) => { if (spec) { try { setSaved(await api.labSave(name, spec)); setSpec({ ...spec, name }) } catch (e) { setErr(String((e as Error).message ?? e)) } } }
  const running = run?.status === 'running'

  return (
    <div className="space-y-2">
      <p className="text-sm text-muted">Try a buying and selling rule on the CSE prices the app has stored, with real fees and slippage, and see what it would have done compared with simply holding the same stocks. A test shows the past: it is not a forecast.</p>
      {err && <ErrorBox msg={err} />}
      {!cat || !spec ? (!err && <Spinner label="Loading the lab…" />) : cat.stocks.length === 0 ? (
        <p className="text-sm text-muted">No stock has a year of stored prices yet. Add stocks to your watchlist and let the app collect their history first.</p>
      ) : (
        <LabBuilder cat={cat} spec={spec} onChange={setSpec} onRun={go} running={!!running} lists={lists} onList={fromList}
                    saved={saved} onSave={save} onDeleteSaved={async n => setSaved(await api.labDelete(n))} onLoad={s => setSpec({ ...s })} />
      )}
      {running && run && (
        <div className="card p-3" role="status" aria-live="polite">
          <div className="mb-1 flex items-center justify-between text-[12px]"><span>{run.stage}</span><span className="num text-muted">{Math.round(run.progress * 100)}%</span></div>
          <div className="h-2 w-full bg-panel-3" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(run.progress * 100)} aria-label="Test progress">
            <div className="h-2 bg-amber transition-[width]" style={{ width: `${Math.max(2, run.progress * 100)}%` }} /></div>
          <p className="mt-1 text-[11px] text-muted">The test runs in the background; you can leave this page and come back.</p>
        </div>
      )}
      {run?.status === 'error' && <ErrorBox msg={run.error ?? 'The test failed.'} />}
      {run?.status === 'done' && run.result && (
        <div className={run.cached ? '' : ''}>
          {run.cached && <p className="mb-1 text-[11px] text-muted">Same test as before on unchanged data: showing the earlier result.</p>}
          <LabResult r={run.result} />
        </div>
      )}
    </div>
  )
}
