// Chart toolbar controls: compare lines, your drawings, saved layouts, PNG export.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type EngineMask, type ChartStyle, type Timeframe } from '../api'
import { COMPARE_COLORS, MAX_COMPARE, type ChartHandle, type Layers, type ScaleMode } from '../chart/PriceChart'
import { DRAW_COLORS, type DrawKind } from '../chart/drawings'
import type { PlanEngine } from '../prefs'

interface Option { id: string; label: string; name?: string }
interface Options { indices: Option[]; cse: Option[]; world: Option[] }

/** A small panel that opens below its button and closes on Escape or a click elsewhere. */
function Pop({ label, open, setOpen, children, wide = false }: { label: ReactNode; open: boolean; setOpen: (o: boolean) => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away); document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open, setOpen])
  return (
    <div className="relative" ref={ref}>
      <button className="btn btn-menu" aria-expanded={open} onClick={() => setOpen(!open)}>{label}</button>
      {open && <div className={`absolute left-0 top-full z-40 mt-1 max-h-[70vh] overflow-y-auto border border-amber/60 bg-panel p-2 text-[12px] shadow-xl ${wide ? 'w-[min(92vw,360px)]' : 'w-[min(92vw,260px)]'}`}>{children}</div>}
    </div>
  )
}

export function CompareMenu({ value, onChange, enabled }: { value: string[]; onChange: (v: string[]) => void; enabled: boolean }) {
  const [open, setOpen] = useState(false)
  const [opts, setOpts] = useState<Options | null>(null)
  const [q, setQ] = useState('')
  useEffect(() => { if (open && !opts) api.compareOptions().then(setOpts).catch(() => {}) }, [open, opts])
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter(x => x !== id) : [...value, id].slice(0, MAX_COMPARE))
  const group = (title: string, list: Option[]) => {
    const f = list.filter(o => !q || o.label.toLowerCase().includes(q.toLowerCase()) || (o.name ?? '').toLowerCase().includes(q.toLowerCase()))
    if (!f.length) return null
    return (
      <div className="mb-1.5">
        <div className="label mb-0.5">{title}</div>
        {f.map(o => {
          const on = value.includes(o.id), full = !on && value.length >= MAX_COMPARE
          return (
            <label key={o.id} className={`flex items-center gap-1.5 py-0.5 ${full ? 'opacity-40' : 'cursor-pointer hover:text-yellow'}`}>
              <input type="checkbox" checked={on} disabled={full} onChange={() => toggle(o.id)} />
              <b className="text-amber">{o.label}</b><span className="truncate text-muted">{o.name && o.name !== o.label ? o.name : ''}</span>
            </label>
          )
        })}
      </div>
    )
  }
  return (
    <Pop open={open} setOpen={setOpen} wide label={<><span className="key">7)</span>Compare{value.length ? ` (${value.length})` : ''}</>}>
      <p className="mb-1.5 text-[11px] text-muted">Up to {MAX_COMPARE} lines as % change from the first bar in view. {enabled ? '' : <b className="text-warn">Daily chart only: switch to 1D to see them.</b>}</p>
      <input className="input mb-1.5 w-full !py-0.5" value={q} onChange={e => setQ(e.target.value)} placeholder="Filter" aria-label="Filter what to compare" />
      {!opts ? <p className="text-muted">Loading…</p> : <>{group('Indices', opts.indices)}{group('My CSE stocks', opts.cse)}{group('World stocks', opts.world)}</>}
      {value.length > 0 && <button className="btn mt-1 w-full !py-0.5" onClick={() => onChange([])}>Clear the comparison</button>}
    </Pop>
  )
}

/** The compare lines' legend: identity by colour, dash and name (and the stock's own line). */
export function CompareLegend({ main, items, onRemove }: { main: string; items: { id: string; label: string; error?: boolean }[]; onRemove: (id: string) => void }) {
  const dash = ['', '4 3', '1 3']
  return (
    <span className="flex shrink-0 items-center gap-2 border-r border-line pr-2 text-[11px]" aria-label="Comparison legend">
      <span className="label">% vs</span>
      <span className="flex items-center gap-1"><svg width="18" height="6" aria-hidden="true"><line x1="0" y1="3" x2="18" y2="3" stroke="#e6e6e6" strokeWidth="2" /></svg><b className="text-fg">{main}</b></span>
      {items.map((c, i) => (
        <span key={c.id} className="flex items-center gap-1">
          <svg width="18" height="6" aria-hidden="true"><line x1="0" y1="3" x2="18" y2="3" stroke={COMPARE_COLORS[i]} strokeWidth="2" strokeDasharray={dash[i]} /></svg>
          <span className={c.error ? 'text-warn' : 'text-fg'}>{c.label}{c.error ? ' (no data)' : ''}</span>
          <button className="text-muted hover:text-bear" onClick={() => onRemove(c.id)} aria-label={`Stop comparing with ${c.label}`}>✕</button>
        </span>
      ))}
    </span>
  )
}

const TOOLS: { id: DrawKind | null; label: string; key: string; title: string }[] = [
  { id: null, label: '↖', key: 'Esc', title: 'Select or move a drawing' },
  { id: 'hline', label: '─ Line', key: 'H', title: 'Horizontal line at a price' },
  { id: 'trend', label: '╱ Trend', key: 'T', title: 'Trendline: two clicks' },
  { id: 'rect', label: '▭ Box', key: 'B', title: 'Rectangle: two corners' },
  { id: 'text', label: 'T Note', key: 'N', title: 'A text note on the chart' },
]

export function DrawBar({ tool, setTool, color, setColor, selected, onDelete, onUndo, canUndo, onClear, count, onRecolor }: {
  tool: DrawKind | null; setTool: (t: DrawKind | null) => void; color: string; setColor: (c: string) => void; selected: string | null
  onDelete: () => void; onUndo: () => void; canUndo: boolean; onClear: () => void; count: number; onRecolor: (c: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center" role="group" aria-label="Drawing tools">
      <span className="btn btn-menu !pr-1.5"><span className="key">8)</span>Draw</span>
      {TOOLS.map(t => <button key={t.label} className="btn !border-l-0 !px-2" aria-pressed={tool === t.id} title={`${t.title} (${t.key})`} onClick={() => setTool(t.id)}>{t.label}</button>)}
      <span className="flex items-center gap-0.5 border border-l-0 border-line px-1.5 py-[3px]" role="radiogroup" aria-label="Drawing colour">
        {DRAW_COLORS.map(c => <button key={c} role="radio" aria-checked={color === c} aria-label={`Colour ${c}`} title={selected ? 'Colour for new drawings and the selected one' : 'Colour for new drawings'}
                                        className={`h-3.5 w-3.5 ${color === c ? 'outline outline-1 outline-offset-1 outline-fg' : ''}`} style={{ background: c }}
                                        onClick={() => { setColor(c); if (selected) onRecolor(c) }} />)}
      </span>
      <button className="btn !border-l-0 !px-2" disabled={!canUndo} onClick={onUndo} title="Undo (Ctrl+Z)">Undo</button>
      <button className="btn !border-l-0 !px-2" disabled={!selected} onClick={onDelete} title="Delete the selected drawing (Delete)">Delete</button>
      <button className="btn !border-l-0 !px-2" disabled={!count} onClick={() => { if (confirm(`Remove all ${count} drawings on this stock?`)) onClear() }}>Clear</button>
    </div>
  )
}

export interface Layout { layers: Layers; engines: EngineMask; chartStyle: ChartStyle; planEngine: PlanEngine; scale: ScaleMode; tf: Timeframe; compare: string[] }
const LAYOUTS_KEY = 'chart_layouts'

/** Named chart set-ups (layers, engines, style, scale, timeframe, comparison), saved for your account. */
export function LayoutsMenu({ current, apply }: { current: () => Layout; apply: (l: Layout) => void }) {
  const [open, setOpen] = useState(false)
  const [all, setAll] = useState<Record<string, Layout>>({})
  const [name, setName] = useState('')
  useEffect(() => { if (open) api.getPref<Record<string, Layout>>(LAYOUTS_KEY).then(v => setAll(v ?? {})).catch(() => {}) }, [open])
  const save = (next: Record<string, Layout>) => { setAll(next); api.putPref(LAYOUTS_KEY, next).catch(() => {}) }
  const names = Object.keys(all).sort()
  return (
    <Pop open={open} setOpen={setOpen} label={<><span className="key">9)</span>Layouts</>}>
      {names.length === 0 ? <p className="mb-1.5 text-muted">No saved layouts yet.</p> : (
        <ul className="mb-1.5">
          {names.map(n => (
            <li key={n} className="flex items-center gap-1 py-0.5">
              <button className="min-w-0 flex-1 truncate text-left text-amber hover:underline" onClick={() => { apply(all[n]); setOpen(false) }} title={`${all[n].tf} · ${all[n].chartStyle} · ${all[n].scale} scale`}>{n}</button>
              <span className="text-[10px] text-muted">{all[n].tf}</span>
              <button className="text-muted hover:text-bear" onClick={() => { const c = { ...all }; delete c[n]; save(c) }} aria-label={`Delete layout ${n}`}>✕</button>
            </li>
          ))}
        </ul>
      )}
      <form className="flex gap-1" onSubmit={e => { e.preventDefault(); const n = name.trim().slice(0, 40); if (n) { save({ ...all, [n]: current() }); setName('') } }}>
        <input className="input min-w-0 flex-1 !py-0.5" value={name} onChange={e => setName(e.target.value)} placeholder="Save this view as…" aria-label="Layout name" />
        <button className="btn btn-primary !py-0" disabled={!name.trim()}>Save</button>
      </form>
      <p className="mt-1.5 text-[11px] text-muted">A layout keeps the layers, engines, chart style, scale, timeframe and comparison, and applies to any stock.</p>
    </Pop>
  )
}

/** The chart as a PNG, with a title line (stock, timeframe, date) above it. */
export function exportPng(handle: ChartHandle | null, title: string, file: string): boolean {
  const shot = handle?.screenshot()
  if (!shot) return false
  const head = 26
  const c = document.createElement('canvas')
  c.width = shot.width; c.height = shot.height + head
  const g = c.getContext('2d')
  if (!g) return false
  g.fillStyle = '#000'; g.fillRect(0, 0, c.width, c.height)
  g.drawImage(shot, 0, head)
  g.fillStyle = '#ffa028'; g.font = '600 13px "IBM Plex Mono", ui-monospace, Consolas, monospace'
  g.fillText(title, 8, 17)
  g.fillStyle = '#9a9a9a'; g.font = '11px "IBM Plex Mono", ui-monospace, Consolas, monospace'
  const tag = 'CSE Advisor · decision support, not advice'
  g.fillText(tag, c.width - g.measureText(tag).width - 8, 17)
  const a = document.createElement('a')
  a.href = c.toDataURL('image/png'); a.download = file
  document.body.appendChild(a); a.click(); a.remove()
  return true
}
