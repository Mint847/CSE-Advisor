// Quant Lab › gs-quant & glossary: what became of every part of Goldman Sachs' gs-quant (so nothing was left out silently), and a plain-English
// glossary of every figure the quant pages show.
import { useEffect, useMemo, useState } from 'react'
import { api, type CoverageEntry } from '../api'
import { GLOSSARY } from '../quant/glossary'
import { ErrorBox, Spinner } from './ui'

const STATUS: Record<CoverageEntry['status'], { label: string; icon: string; text: string; cls: string }> = {
  used: { label: 'Used', icon: '✓', text: 'The module’s own code runs in this app.', cls: 'text-bull' },
  recreated: { label: 'Re-created', icon: '≈', text: 'Needs Goldman’s data, so the same method is computed here on our own data.', cls: 'text-amber' },
  not_possible: { label: 'Not possible', icon: '✗', text: 'Only runs on Goldman’s servers, or needs data that may not be given to the public.', cls: 'text-bear' },
  not_applicable: { label: 'Not applicable', icon: '–', text: 'Works offline but has nothing to do with CSE stocks.', cls: 'text-muted' },
  plumbing: { label: 'Plumbing', icon: '⚙', text: 'Internal machinery (sessions, JSON, generated types): nothing to show.', cls: 'text-muted' },
}
const ORDER: CoverageEntry['status'][] = ['used', 'recreated', 'not_possible', 'not_applicable', 'plumbing']

export default function LabCoverage() {
  const [data, setData] = useState<{ version: string; marquee: string; entries: CoverageEntry[] } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [status, setStatus] = useState<CoverageEntry['status'] | 'all'>('all')
  const [q, setQ] = useState('')
  const [g, setG] = useState('')
  useEffect(() => { api.labCoverage().then(setData).catch(e => setErr(String(e.message ?? e))) }, [])
  const counts = useMemo(() => { const c: Record<string, number> = {}; for (const e of data?.entries ?? []) c[e.status] = (c[e.status] ?? 0) + 1; return c }, [data])
  const rows = useMemo(() => (data?.entries ?? []).filter(e => (status === 'all' || e.status === status)
    && (!q.trim() || `${e.module} ${e.what} ${e.where ?? ''} ${e.why ?? ''}`.toLowerCase().includes(q.trim().toLowerCase()))), [data, status, q])
  const gloss = useMemo(() => Object.values(GLOSSARY).filter(x => !g.trim() || `${x.name} ${x.what} ${x.read}`.toLowerCase().includes(g.trim().toLowerCase())), [g])
  if (err) return <ErrorBox msg={err} />
  if (!data) return <Spinner label="Loading…" />

  return (
    <div className="space-y-4">
      <section className="space-y-2" aria-label="gs-quant coverage">
        <h3 className="text-[13px] font-semibold text-amber">What became of every part of gs-quant {data.version}</h3>
        <p className="text-sm text-muted">gs-quant is Goldman Sachs’ open-source toolkit. It ships <b className="text-fg">no market data</b>: its data and pricing come from Goldman’s Marquee service, which {data.marquee}.
          So only its offline maths and its backtest engine could be brought in, working on this app’s own prices. This table lists every module and what happened to it.</p>
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <button type="button" aria-pressed={status === 'all'} className={`btn !py-0.5 ${status === 'all' ? '' : 'opacity-70'}`} onClick={() => setStatus('all')}>All ({data.entries.length})</button>
          {ORDER.map(s => <button key={s} type="button" aria-pressed={status === s} title={STATUS[s].text} className={`btn !py-0.5 ${status === s ? '' : 'opacity-70'}`} onClick={() => setStatus(s)}>
            <span className={STATUS[s].cls} aria-hidden="true">{STATUS[s].icon}</span> {STATUS[s].label} ({counts[s] ?? 0})</button>)}
          <input className="input !py-0.5 text-[12px]" placeholder="Search modules…" aria-label="Search modules" value={q} onChange={e => setQ(e.target.value)} />
        </div>
        <div className="scroll-thin max-h-[520px] overflow-auto border border-line">
          <table className="bb-table text-[12px] [&_td]:whitespace-normal" aria-label="gs-quant modules">
            <thead><tr><th>Module</th><th>Status</th><th>What it is</th><th>In this app</th><th>Notes</th></tr></thead>
            <tbody>{rows.map(e => (
              <tr key={e.module}><td className="whitespace-nowrap font-mono text-amber">{e.module}</td>
                <td className="whitespace-nowrap"><span className={STATUS[e.status].cls} aria-hidden="true">{STATUS[e.status].icon}</span> {STATUS[e.status].label}</td>
                <td>{e.what}</td><td className="text-muted">{e.where ? `${e.where}${e.stage ? ` (${e.stage})` : ''}` : '–'}</td><td className="text-[11px] text-muted">{e.why ?? ''}</td></tr>
            ))}{rows.length === 0 && <tr><td colSpan={5} className="p-4 text-center text-muted">Nothing matches.</td></tr>}</tbody>
          </table>
        </div>
      </section>

      <section className="space-y-2" aria-label="Glossary">
        <h3 className="text-[13px] font-semibold text-amber">Glossary: every figure in plain English</h3>
        <input className="input !py-0.5 text-[12px]" placeholder="Search the glossary…" aria-label="Search the glossary" value={g} onChange={e => setG(e.target.value)} />
        <dl className="grid gap-2 md:grid-cols-2 [&>*]:min-w-0">
          {gloss.map(x => (
            <div key={x.name} className="card px-3 py-2 text-[12px]"><dt className="font-semibold text-fg">{x.name}</dt>
              <dd className="mt-0.5 text-muted">{x.what}</dd><dd className="mt-0.5"><b className="text-amber">How to read it:</b> {x.read}</dd>{x.caveat && <dd className="mt-0.5 text-warn"><b>Careful:</b> {x.caveat}</dd>}</div>
          ))}
        </dl>
      </section>
    </div>
  )
}
