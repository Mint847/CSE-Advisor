// Quant Lab builder: pick stocks, say when to buy and when to sell, set the money and costs. The conditions are a flat list
// joined by AND / OR (each can be negated); the backend also accepts deeper trees, which are shown read-only here.
import { useState } from 'react'
import type { LabCatalog, LabLeaf, LabRuleDef, LabRule, LabSpec } from '../api'
import { fromFlat, toFlat, type Flat, type Item } from '../quant/labRules'
import { Explain } from './QuantFigures'

const blank = (d: LabRuleDef): LabLeaf => ({ type: d.type, ...Object.fromEntries(d.params.map(p => [p.key, p.default])) })

function Conditions({ title, hint, value, onChange, defs, joiner }: {
  title: string; hint?: string; value: LabRule | null | undefined; onChange: (r: LabRule | null) => void; defs: LabRuleDef[]; joiner: 'AND/OR' | 'OR'
}) {
  const flat = toFlat(value)
  const [open, setOpen] = useState<number | null>(null)
  if (!flat) return <div className="text-[12px] text-muted">This strategy uses nested conditions, which this form can run but not edit. Pick a preset to start again.</div>
  const set = (f: Flat) => onChange(fromFlat(f))
  const byType = Object.fromEntries(defs.map(d => [d.type, d]))
  const upd = (i: number, it: Item) => set({ ...flat, items: flat.items.map((x, k) => (k === i ? it : x)) })
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label">{title}</span>
        {joiner === 'AND/OR' && flat.items.length > 1 && (
          <span className="flex" role="radiogroup" aria-label={`${title}: how conditions combine`}>
            {([['all', 'ALL of these'], ['any', 'ANY of these']] as const).map(([k, l]) => <button key={k} type="button" role="radio" aria-checked={flat.op === k} className="btn !px-2 !py-0.5 text-[11px]" onClick={() => set({ ...flat, op: k })}>{l}</button>)}
          </span>
        )}
        {hint && <span className="text-[11px] text-muted">{hint}</span>}
      </div>
      {flat.items.length === 0 && <p className="text-[12px] text-muted">None.</p>}
      <ul className="space-y-1.5">
        {flat.items.map((it, i) => {
          const d = byType[it.leaf.type]
          return (
            <li key={i} className="border border-line bg-panel-2 px-2 py-1.5">
              <div className="flex flex-wrap items-center gap-2 text-[12px]">
                {i > 0 && <b className="w-8 text-amber">{joiner === 'OR' ? 'OR' : flat.op === 'all' ? 'AND' : 'OR'}</b>}
                <label className="flex items-center gap-1 text-muted"><input type="checkbox" checked={it.not} onChange={e => upd(i, { ...it, not: e.target.checked })} aria-label="Not (the opposite)" /> NOT</label>
                <select className="input !py-0.5 text-[12px]" value={it.leaf.type} aria-label="Condition" onChange={e => upd(i, { ...it, leaf: blank(byType[e.target.value]) })}>
                  {defs.map(x => <option key={x.type} value={x.type}>{x.label}</option>)}
                </select>
                {d?.params.map(p => (
                  <label key={p.key} className="flex items-center gap-1 text-muted">
                    <span>{p.label}</span>
                    <input className="input num !w-16 !py-0.5 text-[12px]" type="number" min={p.min} max={p.max} step={p.step} value={String(it.leaf[p.key] ?? p.default)} aria-label={p.label}
                           onChange={e => upd(i, { ...it, leaf: { ...it.leaf, [p.key]: e.target.value === '' ? p.default : Number(e.target.value) } })} />
                  </label>
                ))}
                <button type="button" className="text-[10px] text-muted hover:text-amber" aria-expanded={open === i} aria-label="What does this condition mean?" onClick={() => setOpen(open === i ? null : i)}>ⓘ</button>
                <button type="button" className="ml-auto text-[11px] text-muted hover:text-bear" onClick={() => set({ ...flat, items: flat.items.filter((_, k) => k !== i) })}>remove</button>
              </div>
              {open === i && d && <p className="mt-1 bg-panel-3 px-2 py-1 text-[11px] leading-snug">{d.help}</p>}
            </li>
          )
        })}
      </ul>
      <button type="button" className="btn !py-0.5 text-[11px]" onClick={() => set({ ...flat, items: [...flat.items, { leaf: blank(defs[0]), not: false }] })}>+ add a condition</button>
    </div>
  )
}

const num = (v: string): number | null => (v === '' ? null : Number(v))

export default function LabBuilder({ cat, spec, onChange, onRun, running, lists, onList, saved, onSave, onDeleteSaved, onLoad }: {
  cat: LabCatalog; spec: LabSpec; onChange: (s: LabSpec) => void; onRun: () => void; running: boolean
  lists: { id: number; name: string }[]; onList: (id: number) => void
  saved: { name: string; spec: LabSpec }[]; onSave: (name: string) => void; onDeleteSaved: (name: string) => void; onLoad: (s: LabSpec) => void
}) {
  const chosen = new Set(spec.symbols ?? [])
  const toggle = (sym: string) => {
    const next = new Set(chosen)
    if (next.has(sym)) next.delete(sym); else if (next.size < cat.max_symbols) next.add(sym)
    onChange({ ...spec, symbols: [...next], max_positions: Math.min(spec.max_positions, Math.max(1, next.size)) })
  }
  const [saveName, setSaveName] = useState('')
  const ex = spec.exit
  return (
    <div className="space-y-3">
      <section className="card p-3" aria-label="Start from">
        <div className="flex flex-wrap items-center gap-2">
          <span className="label">Start from an idea</span>
          {cat.presets.map(p => <button key={p.id} type="button" className="btn !py-0.5 text-[11px]" title={p.blurb}
            onClick={() => onChange({ ...spec, ...p.spec, name: p.name, symbols: spec.symbols, exit: { ...p.spec.exit } } as LabSpec)}>{p.name}</button>)}
        </div>
        <p className="mt-1 text-[11px] text-muted">{cat.presets.find(p => p.name === spec.name)?.blurb ?? 'Change anything below, then run it.'}</p>
        {saved.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
            <span className="label">Your saved</span>
            {saved.map(s => (
              <span key={s.name} className="flex items-center border border-line">
                <button type="button" className="px-2 py-0.5 hover:text-amber" onClick={() => onLoad(s.spec)}>{s.name}</button>
                <button type="button" className="px-1.5 text-muted hover:text-bear" aria-label={`Delete ${s.name}`} onClick={() => onDeleteSaved(s.name)}>✕</button>
              </span>
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-3 lg:grid-cols-2 [&>*]:min-w-0">
        <section className="card p-3" aria-label="Stocks">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="label">1 · Which stocks</span>
            <span className="text-[11px] text-muted">{chosen.size} of at most {cat.max_symbols}</span>
            <button type="button" className="btn !py-0 text-[11px]" onClick={() => onChange({ ...spec, symbols: cat.stocks.slice(0, cat.max_symbols).map(s => s.symbol) })}>all</button>
            <button type="button" className="btn !py-0 text-[11px]" onClick={() => onChange({ ...spec, symbols: [], max_positions: 1 })}>none</button>
            {lists.length > 0 && (
              <select className="input !py-0.5 text-[11px]" aria-label="Use a list" value="" onChange={e => e.target.value && onList(Number(e.target.value))}>
                <option value="">use a list…</option>{lists.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            )}
          </div>
          <div className="flex flex-wrap gap-1" role="group" aria-label="Stocks to test">
            {cat.stocks.map(s => (
              <button key={s.symbol} type="button" aria-pressed={chosen.has(s.symbol)} title={`${s.name ?? s.label}: ${s.sessions} stored sessions`}
                      className={`border px-2 py-0.5 text-[11px] ${chosen.has(s.symbol) ? 'border-amber bg-amber/20 text-fg' : 'border-line text-muted hover:text-fg'}`} onClick={() => toggle(s.symbol)}>{s.label}</button>
            ))}
          </div>
          <p className="mt-1 text-[11px] text-muted">Only stocks the app holds at least a year of prices for can be tested (the ones you track). <Explain k="survivorship">Hindsight warning</Explain></p>
        </section>

        <section className="card space-y-2 p-3" aria-label="Money and costs">
          <span className="label">4 · Money, period and costs</span>
          <div className="grid grid-cols-2 gap-2 text-[12px] sm:grid-cols-3">
            <label className="block"><span className="label">Starting money (LKR)</span>
              <input className="input num w-full" type="number" min={100000} step={100000} value={spec.capital} onChange={e => onChange({ ...spec, capital: Number(e.target.value) })} /></label>
            <label className="block"><span className="label">Positions at once</span>
              <input className="input num w-full" type="number" min={1} max={Math.max(1, chosen.size)} value={spec.max_positions} onChange={e => onChange({ ...spec, max_positions: Number(e.target.value) })} /></label>
            <label className="block"><span className="label">Period</span>
              <select className="input w-full" value={String(spec.years)} onChange={e => onChange({ ...spec, years: e.target.value === 'all' ? 'all' : Number(e.target.value) })}>
                {[1, 2, 3, 5, 7, 10, 14].map(y => <option key={y} value={y}>last {y} year{y > 1 ? 's' : ''}</option>)}<option value="all">all stored history</option>
              </select></label>
            <label className="block"><span className="label">Return basis</span>
              <select className="input w-full" value={spec.basis} onChange={e => onChange({ ...spec, basis: e.target.value as 'total' | 'price' })}>
                <option value="total">Total (dividends added back)</option><option value="price">Price only</option>
              </select></label>
            <label className="block"><span className="label"><Explain k="slippage">Slippage</Explain> %</span>
              <input className="input num w-full" type="number" min={0} max={3} step={0.05} value={spec.slippage_pct} onChange={e => onChange({ ...spec, slippage_pct: Number(e.target.value) })} /></label>
            <div className="block"><span className="label">CSE fees each side</span><div className="num pt-1 text-fg">{cat.defaults.brokerage_pct}%</div></div>
          </div>
          <p className="text-[11px] text-muted">Orders are decided after the close and filled at the next open the stock actually traded. Idle cash earns the CBSL T-bill rate.</p>
        </section>

        <section className="card p-3 lg:col-span-1" aria-label="Buy rule">
          <Conditions title="2 · Buy when" value={spec.entry} onChange={r => onChange({ ...spec, entry: r ?? { type: 'monthly' } })} defs={cat.rules} joiner="AND/OR" />
          <p className="mt-1 text-[11px] text-muted">If more stocks qualify than there are free positions, the best-traded ones are bought first.</p>
        </section>

        <section className="card space-y-2 p-3" aria-label="Sell rule">
          <span className="label">3 · Sell when (any of these happens)</span>
          <div className="grid grid-cols-2 gap-2 text-[12px] sm:grid-cols-4">
            {([['hold_days', 'Held … sessions', 1, 2000, 1], ['stop_pct', 'Falls …% below the price paid', 0.5, 90, 0.5], ['trail_pct', 'Falls …% from its high since buying', 0.5, 90, 0.5], ['target_pct', 'Gains …%', 0.5, 1000, 0.5]] as const).map(([k, l, lo, hi, st]) => (
              <label key={k} className="block"><span className="label !normal-case">{l}</span>
                <input className="input num w-full" type="number" min={lo} max={hi} step={st} placeholder="off" value={ex[k] ?? ''} onChange={e => onChange({ ...spec, exit: { ...ex, [k]: num(e.target.value) } })} /></label>
            ))}
          </div>
          <Conditions title="…or when" hint="(optional)" value={ex.rule} onChange={r => onChange({ ...spec, exit: { ...ex, rule: r } })} defs={cat.rules} joiner="OR" />
          <p className="text-[11px] text-muted">Stops are checked on closing prices and sold at the next open, so a gap can sell below the stop. With nothing filled in, positions are kept to the end.</p>
        </section>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-primary" disabled={running || !chosen.size} onClick={onRun}>{running ? 'Running…' : 'Run the test'}</button>
        <input className="input !py-1 text-[12px]" placeholder="Name to save as" aria-label="Name to save this strategy as" maxLength={60} value={saveName} onChange={e => setSaveName(e.target.value)} />
        <button type="button" className="btn text-[12px]" disabled={!saveName.trim() || !chosen.size} onClick={() => { onSave(saveName.trim()); setSaveName('') }}>Save</button>
      </div>
    </div>
  )
}
