// EQS: your own screener over every listed stock. Pick conditions (price, size, sector, today's move, 6-month return, distance from the
// 52-week high, trend, one-year volatility, Sharpe, beta, worst fall...), see who passes, sort, save the screen, export it.
// Information only: a screen never changes an engine's verdict.
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, fmtBig, ticker, type EqsCatalog, type EqsField, type EqsFilter, type EqsResult, type EqsSpec } from '../api'
import ExportCsv from '../components/ExportCsv'
import { ErrorBox, Spinner } from '../components/ui'
import type { CsvColumn } from '../export/csv'

const SAVED = 'eqs_screens'
interface Row { field: string; op: string; a: string; b: string }                 // a / b: the typed value(s); a bool is 'true' / 'false'
interface Saved { name: string; spec: EqsSpec }
const OP_TEXT: Record<string, string> = { '>': 'is above', '>=': 'is at least', '<': 'is below', '<=': 'is at most', '=': 'equals', between: 'is between', is: 'is', in: 'is one of', contains: 'contains' }

const toRow = (f: EqsFilter, fields: Map<string, EqsField>): Row => {
  const k = fields.get(f.field)?.kind
  if (f.op === 'between' && Array.isArray(f.value)) return { field: f.field, op: f.op, a: String(f.value[0]), b: String(f.value[1]) }
  if (k === 'text' && Array.isArray(f.value)) return { field: f.field, op: f.op, a: f.value.join(', '), b: '' }
  return { field: f.field, op: f.op, a: String(f.value), b: '' }
}
const fromRow = (r: Row, fields: Map<string, EqsField>): EqsFilter => {
  const k = fields.get(r.field)?.kind
  if (k === 'num') return { field: r.field, op: r.op, value: r.op === 'between' ? [Number(r.a), Number(r.b)] : Number(r.a) }
  if (k === 'bool') return { field: r.field, op: 'is', value: r.a !== 'false' }
  return { field: r.field, op: r.op, value: r.op === 'in' ? r.a.split(',').map(x => x.trim()).filter(Boolean) : r.a }
}

function show(f: EqsField | undefined, v: number | string | boolean | null | undefined): string {
  if (v == null || !f) return '–'
  if (typeof v === 'boolean') return v ? '✓' : '✗'
  if (typeof v === 'string') return v
  if (f.id === 'price') return fmt(v)
  if (f.unit === '%') return `${f.id === 'dist52' || f.id === 'mdd' || f.id.startsWith('ret') || f.id === 'change_pct' || f.id === 'ext20' ? (v > 0 ? '+' : '') : ''}${v.toFixed(1)}%`
  if (f.unit === 'LKR' || f.unit === 'shares') return fmtBig(v) ?? '–'
  if (f.unit === 'pts') return `${v > 0 ? '+' : ''}${v.toFixed(0)} pts`
  if (f.unit === 'percentile') return `${Math.round(v)}th`
  if (f.unit === 'of 3') return String(v)
  return v.toFixed(2)
}

export default function Eqs() {
  const [cat, setCat] = useState<EqsCatalog | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [sort, setSort] = useState<EqsSpec['sort']>({ field: 'turnover', dir: 'desc' })
  const [limit, setLimit] = useState(100)
  const [res, setRes] = useState<EqsResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<Saved[]>([])
  const [name, setName] = useState('')
  const fields = useMemo(() => new Map((cat?.fields ?? []).map(f => [f.id, f])), [cat])

  useEffect(() => {
    api.eqsCatalog().then(c => {
      setCat(c)
      const p = c.presets[0]
      const m = new Map(c.fields.map(f => [f.id, f]))
      setRows((p.spec.filters ?? []).map(f => toRow(f, m))); if (p.spec.sort) setSort(p.spec.sort)
    }).catch(e => setErr(String(e.message ?? e)))
    api.getPref<Saved[]>(SAVED).then(v => setSaved(Array.isArray(v) ? v : [])).catch(() => {})
  }, [])

  const spec = (s = sort): EqsSpec => ({ filters: rows.map(r => fromRow(r, fields)), sort: s, limit })
  const run = async (s = sort) => {
    const blank = rows.find(r => fields.get(r.field)?.kind === 'num' && (r.a.trim() === '' || (r.op === 'between' && r.b.trim() === '')))
    if (blank) { setErr(`Enter a number for “${fields.get(blank.field)!.label}”${blank.op === 'between' ? ' (both ends of the range)' : ''}.`); return }
    setBusy(true); setErr(null)
    try { setRes(await api.eqsRun(spec(s))) } catch (e) { setErr(String((e as Error).message ?? e)); setRes(null) } finally { setBusy(false) }
  }
  const load = (s: Partial<EqsSpec>) => {
    setRows((s.filters ?? []).map(f => toRow(f, fields))); if (s.sort) setSort(s.sort); if (s.limit) setLimit(s.limit); setRes(null); setErr(null)
  }
  const persist = (next: Saved[]) => { setSaved(next); api.putPref(SAVED, next).catch(() => {}) }
  const upd = (i: number, r: Partial<Row>) => setRows(rs => rs.map((x, k) => (k === i ? { ...x, ...r } : x)))

  const csvCols = useMemo<CsvColumn<EqsResult['rows'][number]>[]>(() => !res ? [] : [
    { header: 'Ticker', get: r => ticker(r.symbol) }, { header: 'Name', get: r => r.name }, { header: 'Sector', get: r => r.sector },
    ...res.columns.map(c => ({ header: `${fields.get(c)?.label ?? c}${fields.get(c)?.unit && fields.get(c)?.kind === 'num' ? ` (${fields.get(c)!.unit})` : ''}`, get: (r: EqsResult['rows'][number]) => r.values[c] as string | number | boolean | null })),
  ], [res, fields])

  if (!cat) return <div className="mx-auto max-w-[1700px] px-3 py-3">{err ? <ErrorBox msg={err} /> : <Spinner label="Loading the screener…" />}</div>
  const groups = [...new Set(cat.fields.map(f => f.group))]
  const sel = 'input !py-0.5 text-[12px]'

  return (
    <div className="mx-auto max-w-[1700px] space-y-2 px-3 py-3">
      <h1 className="text-[14px] font-semibold uppercase tracking-wider text-amber">EQS · Your screener</h1>
      <p className="text-sm text-muted">Pick the conditions a stock must meet, from today’s market and from the figures the app works out. Every listed stock is checked.
        The trend and strength figures exist for the liquid stocks in the evening ranking, and the one-year risk figures for those and the stocks you track: others are left out of a condition on them, and counted below. A screen is a list to look at, not a signal.</p>
      {err && <ErrorBox msg={err} />}

      <section className="card space-y-2 p-3" aria-label="Build your screen">
        <div className="flex flex-wrap items-center gap-1.5"><span className="label">Start from</span>
          {cat.presets.map(p => <button key={p.id} type="button" className="btn !py-0.5 text-[11px]" title={p.blurb} onClick={() => load(p.spec)}>{p.name}</button>)}
          <button type="button" className="btn !py-0.5 text-[11px]" onClick={() => { setRows([]); setRes(null) }}>clear</button>
        </div>
        {saved.length > 0 && <div className="flex flex-wrap items-center gap-1.5 text-[11px]"><span className="label">Your saved screens</span>
          {saved.map(s => <span key={s.name} className="flex items-center border border-line"><button type="button" className="px-2 py-0.5 hover:text-amber" onClick={() => load(s.spec)}>{s.name}</button>
            <button type="button" className="px-1.5 text-muted hover:text-bear" aria-label={`Delete ${s.name}`} onClick={() => persist(saved.filter(x => x.name !== s.name))}>✕</button></span>)}</div>}

        <div className="space-y-1.5" role="group" aria-label="Conditions">
          {rows.length === 0 && <p className="text-[12px] text-muted">No conditions: every listed stock passes. Add one below.</p>}
          {rows.map((r, i) => {
            const f = fields.get(r.field)!
            return (
              <div key={i} className="flex flex-wrap items-center gap-2 border border-line bg-panel-2 px-2 py-1.5 text-[12px]">
                <span className="w-8 text-amber">{i === 0 ? 'IF' : 'AND'}</span>
                <select className={sel} aria-label="Field" value={r.field} onChange={e => { const nf = fields.get(e.target.value)!; upd(i, { field: nf.id, op: nf.ops[0], a: nf.kind === 'bool' ? 'true' : '', b: '' }) }}>
                  {groups.map(g => <optgroup key={g} label={g}>{cat.fields.filter(x => x.group === g).map(x => <option key={x.id} value={x.id}>{x.label}{x.unit && x.kind === 'num' ? ` (${x.unit})` : ''}</option>)}</optgroup>)}
                </select>
                {f.kind === 'bool' ? (
                  <select className={sel} aria-label="Yes or no" value={r.a} onChange={e => upd(i, { a: e.target.value })}><option value="true">is yes</option><option value="false">is no</option></select>
                ) : (
                  <>
                    <select className={sel} aria-label="Comparison" value={r.op} onChange={e => upd(i, { op: e.target.value })}>{f.ops.map(o => <option key={o} value={o}>{OP_TEXT[o] ?? o}</option>)}</select>
                    {f.kind === 'num'
                      ? <><input className={`${sel} num !w-24`} type="number" step="any" aria-label="Value" value={r.a} onChange={e => upd(i, { a: e.target.value })} />
                          {r.op === 'between' && <><span className="text-muted">and</span><input className={`${sel} num !w-24`} type="number" step="any" aria-label="Upper value" value={r.b} onChange={e => upd(i, { b: e.target.value })} /></>}</>
                      : <input className={`${sel} !w-48`} aria-label="Value" placeholder={r.op === 'in' ? 'Banks, Telecom' : 'text'} value={r.a} onChange={e => upd(i, { a: e.target.value })} />}
                  </>
                )}
                <span className="min-w-0 flex-1 truncate text-[11px] text-muted" title={f.help}>{f.help}</span>
                <button type="button" className="text-[11px] text-muted hover:text-bear" onClick={() => setRows(rs => rs.filter((_, k) => k !== i))}>remove</button>
              </div>
            )
          })}
          <button type="button" className="btn !py-0.5 text-[11px]" disabled={rows.length >= cat.max_filters} onClick={() => setRows(rs => [...rs, { field: 'turnover', op: '>=', a: '', b: '' }])}>+ add a condition</button>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <label className="flex items-center gap-1"><span className="label">Sort by</span>
            <select className={sel} aria-label="Sort by" value={sort.field} onChange={e => setSort({ ...sort, field: e.target.value })}>{cat.fields.filter(f => f.kind !== 'bool').map(f => <option key={f.id} value={f.id}>{f.label}</option>)}</select></label>
          <select className={sel} aria-label="Order" value={sort.dir} onChange={e => setSort({ ...sort, dir: e.target.value as 'asc' | 'desc' })}><option value="desc">highest first</option><option value="asc">lowest first</option></select>
          <label className="flex items-center gap-1"><span className="label">Show</span>
            <select className={sel} aria-label="How many to show" value={limit} onChange={e => setLimit(Number(e.target.value))}>{[25, 50, 100, 200, cat.max_rows].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => run()}>{busy ? 'Screening…' : 'Run the screen'}</button>
          <input className="input !py-1 text-[12px]" placeholder="Name to save as" aria-label="Name to save this screen as" maxLength={60} value={name} onChange={e => setName(e.target.value)} />
          <button type="button" className="btn text-[12px]" disabled={!name.trim() || saved.length >= 30} onClick={() => { persist([...saved.filter(s => s.name !== name.trim()), { name: name.trim(), spec: spec() }]); setName('') }}>Save</button>
        </div>
      </section>

      {res && (
        <section className={`card ${busy ? 'opacity-60' : ''}`} aria-label="Results">
          <div className="panel-title flex-wrap gap-2"><h3 className="flex-1">{res.matched} of {res.total} stocks pass{res.shown < res.matched ? ` · showing ${res.shown}` : ''}</h3>
            <ExportCsv name="screen" columns={csvCols} rows={res.rows} /></div>
          {res.notes.length > 0 && <ul className="list-disc space-y-0.5 px-6 pt-2 text-[11px] text-muted">{res.notes.map(n => <li key={n}>{n}</li>)}</ul>}
          <div className="scroll-thin overflow-x-auto p-2">
            {res.rows.length === 0 ? <p className="p-3 text-center text-sm text-muted">No stock passes every condition. Loosen one.</p> : (
              <table className="bb-table num text-[12px]" aria-label="Stocks that pass">
                <thead><tr><th>Ticker</th><th className="hidden md:table-cell">Name</th><th className="hidden lg:table-cell">Sector</th>
                  {res.columns.map(c => (
                    <th key={c} className="text-right" aria-sort={res.spec.sort.field === c ? (res.spec.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                      <button className="uppercase hover:text-yellow" title={fields.get(c)?.help} disabled={fields.get(c)?.kind === 'bool'}
                              onClick={() => { const s = { field: c, dir: sort.field === c && sort.dir === 'desc' ? 'asc' as const : 'desc' as const }; setSort(s); run(s) }}>
                        {fields.get(c)?.label ?? c}{res.spec.sort.field === c ? (res.spec.sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}</button></th>))}</tr></thead>
                <tbody>{res.rows.map(r => (
                  <tr key={r.symbol}>
                    <td><Link className="font-semibold text-amber" to={r.mine ? `/stock/${r.symbol}` : `/quote/${r.symbol}`}>{ticker(r.symbol)}</Link>{r.held && <span className="ml-1 text-[10px] text-muted" title="you hold it">held</span>}</td>
                    <td className="hidden max-w-[240px] truncate text-muted md:table-cell" title={r.name ?? undefined}>{r.name}</td><td className="hidden max-w-[140px] truncate text-muted lg:table-cell">{r.sector ?? '–'}</td>
                    {res.columns.map(c => { const v = r.values[c]; return <td key={c} className={`text-right ${typeof v === 'number' && (c === 'change_pct' || c.startsWith('ret') || c === 'rs120') ? (v >= 0 ? 'text-bull' : 'text-bear') : ''}`}>{show(fields.get(c), v)}</td> })}
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
