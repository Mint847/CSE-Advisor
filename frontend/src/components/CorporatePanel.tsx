import { useEffect, useState } from 'react'
import { api, engines, fmt, type Analysis, type CorpAction } from '../api'
import { ErrorBox, Section, Spinner } from './ui'

/** Rights / bonus / split records used to adjust the price history, plus CSV import of longer history. */
export default function CorporatePanel({ symbol, a, onChanged }: { symbol: string; a: Analysis; onChanged: () => void }) {
  const [acts, setActs] = useState<CorpAction[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [edit, setEdit] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ kind: 'rights', ex_date: '', ratio_new: '', ratio_old: '1', price: '', listing_date: '' })
  const [csv, setCsv] = useState<{ text: string; name: string } | null>(null)
  const [adjusted, setAdjusted] = useState(false)
  const [importMsg, setImportMsg] = useState<string | null>(null)
  const load = () => api.corporateActions(symbol).then(setActs).catch(e => setErr(String(e.message ?? e)))
  useEffect(() => { setActs(null); load() }, [symbol]) // eslint-disable-line react-hooks/exhaustive-deps

  const corp = engines(a).anly?.corporate
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null)
    try { await fn(); await load(); onChanged() } catch (e) { setErr(String((e as Error).message)) } finally { setBusy(false) }
  }
  const num = (s: string) => (s.trim() === '' ? null : Number(s.replace(/,/g, '')))

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title="Corporate actions (price adjustment)">
        <p className="mb-3 text-sm text-muted">
          cse.lk price history is not adjusted for rights issues, bonus issues or splits. The app back-adjusts every bar before
          the ex-date (as TradingView does), so trend lines, Fibonacci levels and patterns are not broken by the XR gap.
          Records are built from CSE announcements; the rights subscription price is read from the listing notice, or you can type it here.
        </p>
        {err && <ErrorBox msg={err} />}
        {!acts ? <Spinner /> : acts.length === 0 ? <p className="text-sm text-muted">No rights, bonus or split events found in the announcements.</p> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted"><th className="py-1">Event</th><th>Ex-date</th><th>Ratio</th><th>Price</th><th>Factor</th><th /></tr></thead>
            <tbody>
              {acts.map(x => (
                <tr key={x.id} className="border-t border-line/60 align-top">
                  <td className="py-1.5 font-medium capitalize">{x.kind}{x.manual && <span className="ml-1 text-[10px] text-muted">(edited)</span>}</td>
                  <td className="num">{x.ex_date ?? '–'}</td>
                  <td className="num">{x.ratio_new != null && x.ratio_old != null ? `${x.ratio_new}:${x.ratio_old}` : '–'}</td>
                  <td className="num">
                    {x.kind === 'rights' ? (
                      <span className="inline-flex items-center gap-1">
                        <input className="input w-20 !py-0.5 text-xs" aria-label="Subscription price" placeholder="Rs"
                               value={edit[x.id] ?? (x.price ?? '').toString()} onChange={e => setEdit({ ...edit, [x.id]: e.target.value })} />
                        {edit[x.id] !== undefined && <button className="btn !px-1.5 !py-0.5 text-xs" disabled={busy}
                          onClick={() => run(() => api.saveCorporateAction(symbol, { ...x, price: num(edit[x.id]) }, x.id))}>save</button>}
                      </span>
                    ) : '–'}
                  </td>
                  <td className="num text-xs">
                    {x.applied ? <span className="text-bull">×{x.factor?.toFixed(4)}</span> : <span className="text-warn">{x.reason ?? 'not applied'}</span>}
                    {x.terp != null && <div className="text-muted">TERP {fmt(x.terp, 3)} (cum {fmt(x.cum_price)})</div>}
                  </td>
                  <td className="text-right">
                    <button className="text-xs text-muted hover:text-bear" disabled={busy}
                            onClick={() => run(() => api.saveCorporateAction(symbol, { ...x, ignore: !x.ignore }, x.id))}>{x.ignore ? 'use' : 'ignore'}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {corp && corp.notes.length > 0 && (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">{corp.notes.map((t, i) => <li key={i}>{t}</li>)}</ul>
        )}
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-amber">Add an event manually</summary>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <label className="text-xs">Kind<select className="input mt-0.5 w-full" value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })}>
              <option value="rights">Rights issue</option><option value="bonus">Bonus / scrip</option><option value="split">Split / subdivision</option></select></label>
            <label className="text-xs">Ex-date (XR/XD)<input type="date" className="input mt-0.5 w-full" value={form.ex_date} onChange={e => setForm({ ...form, ex_date: e.target.value })} /></label>
            <label className="text-xs">New shares<input className="input mt-0.5 w-full" value={form.ratio_new} onChange={e => setForm({ ...form, ratio_new: e.target.value })} placeholder="e.g. 2" /></label>
            <label className="text-xs">for every<input className="input mt-0.5 w-full" value={form.ratio_old} onChange={e => setForm({ ...form, ratio_old: e.target.value })} placeholder="e.g. 1" /></label>
            {form.kind === 'rights' && <label className="text-xs">Price (Rs)<input className="input mt-0.5 w-full" value={form.price} onChange={e => setForm({ ...form, price: e.target.value })} /></label>}
            {form.kind === 'rights' && <label className="text-xs">New shares listed<input type="date" className="input mt-0.5 w-full" value={form.listing_date} onChange={e => setForm({ ...form, listing_date: e.target.value })} /></label>}
          </div>
          <button className="btn btn-primary mt-2" disabled={busy || !form.ex_date || !form.ratio_new}
                  onClick={() => run(() => api.saveCorporateAction(symbol, {
                    kind: form.kind, ex_date: form.ex_date, ratio_new: num(form.ratio_new), ratio_old: num(form.ratio_old),
                    price: num(form.price), listing_date: form.listing_date || null,
                  }))}>Add &amp; re-analyse</button>
        </details>
      </Section>

      <Section title="Import longer price history (CSV)">
        <p className="mb-2 text-sm text-muted">
          cse.lk only provides about one year of daily bars. Multi-year Fibonacci anchors and broad patterns (like a head &amp; shoulders over several years)
          need more. Import a CSV with columns <code>date, open, high, low, close, volume</code>. Existing cse.lk days are never overwritten.
        </p>
        <input type="file" accept=".csv,text/csv" aria-label="CSV file"
               onChange={async e => { const f = e.target.files?.[0]; if (f) setCsv({ text: await f.text(), name: f.name }) }} />
        <label className="mt-2 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={adjusted} onChange={e => setAdjusted(e.target.checked)} />
          These prices are already adjusted for rights / splits (e.g. exported from TradingView)
        </label>
        <button className="btn btn-primary mt-2" disabled={!csv || busy}
                onClick={() => run(async () => {
                  const r = await api.importHistory(symbol, csv!.text, adjusted)
                  setImportMsg(`Imported ${r.added} new days from ${csv!.name} (${r.rows} rows, earliest ${r.first}).`)
                })}>Import &amp; re-analyse</button>
        {importMsg && <p className="mt-2 text-sm text-bull">{importMsg}</p>}
      </Section>
    </div>
  )
}
