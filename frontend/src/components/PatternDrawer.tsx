// Large, searchable chooser for everything drawn on the chart. Opened from the stock toolbar,
// the P key or the PAT command. Non-modal: the chart redraws live behind it.
import { useEffect, useMemo, useRef, useState } from 'react'
import { fmt, type Analysis, type Pattern } from '../api'
import { levelLayer, smcLayer, smcOn, type Layers } from '../chart/PriceChart'
import { smcKindOf, smcZoneStyle, type SmcKind } from '../chart/smcStyle'
import { SmcSwatch } from './SmcSwatch'
import { CATEGORIES, CATEGORY_LABEL, PRESETS, type Mode, type PlanEngine, type StockPrefs } from '../prefs'
import { EngineBadge, Switch } from './ui'

const DIR_ICON: Record<string, string> = { bullish: '▲', bearish: '▼', neutral: '◆' }
const dirCls = (d: string) => d === 'bullish' ? 'text-bull' : d === 'bearish' ? 'text-bear' : 'text-neutral'

export interface LevelItem { key: string; label: string; sub: string; on: boolean; patternId?: string; swatch?: { kind: SmcKind; dir: string; faded: boolean } }

/** The drawn shape of an SMC pattern, for its swatch (null for lines-only patterns such as BOS / sweeps). */
export function smcSwatchOf(p: Pattern): LevelItem['swatch'] {
  const kind = smcKindOf(p.type)
  if (!kind) return undefined
  const st = smcZoneStyle(p.zones[0]?.role ?? (kind === 'ote' ? 'ote' : kind === 'fvg' ? 'fvg' : 'order_block'), p.context, p.direction)
  return { kind, dir: p.direction, faded: (st?.strength ?? 1) < 1 }
}
export interface LevelGroup { title: string; layer: keyof Layers; items: LevelItem[] }

/** Analyst-engine drawings as switchable rows (also used by the active-drawings strip). */
export function analystGroups(a: Analysis, prefs: StockPrefs, analystPatterns: Pattern[], isAnalystVisible: (p: Pattern) => boolean): LevelGroup[] {
  const lv = a.levels, L = prefs.layers, ov = prefs.levelOverrides
  const on = (key: string, layer: keyof Layers) => ov[key] ?? L[layer]
  const lvlPats = a.patterns.filter(p => p.category === 'level')
  const groups: LevelGroup[] = []
  const fib = lv.fib
  const fibP = lvlPats.filter(p => p.type === 'fibonacci')
  if (fibP.length) groups.push({ title: 'Fibonacci', layer: 'fib', items: fibP.map(p => ({
    key: p.id, patternId: p.id, label: 'Fibonacci retracement', on: on(p.id, 'fib'),
    sub: fib ? `${fib.scale} ${fib.direction === 'up' ? 'rally' : 'decline'} ${fmt(fib.low.price)}→${fmt(fib.high.price)} (${fib.move_pct.toFixed(0)}%), now at ${fib.retracement_now.toFixed(2)}` : p.explanation.slice(0, 80),
  })) })
  const c = lv.confluence
  if (c && (c.zone1 || c.zone2)) groups.push({ title: 'Buy zones', layer: 'zones', items: [
    ...(c.zone1 ? [{ key: 'zone1', label: 'Buy zone 1', on: on('zone1', 'zones'), sub: `${fmt(c.zone1.low)}–${fmt(c.zone1.high)} · ${c.zone1.sources.slice(0, 3).join(' + ')}` }] : []),
    ...(c.zone2 ? [{ key: 'zone2', label: 'Buy zone 2 (if Zone 1 breaks)', on: on('zone2', 'zones'), sub: `${fmt(c.zone2.low)}–${fmt(c.zone2.high)} · ${c.zone2.sources.slice(0, 3).join(' + ')}` }] : []),
  ] })
  if (c?.sell_zones.length) groups.push({ title: 'Sell zones (take-profit levels)', layer: 'sellz', items: c.sell_zones.slice(0, 3).map((z, i) => ({
    key: `sell${i}`, label: `Sell zone ${i + 1}`, on: on(`sell${i}`, 'sellz'), sub: `${fmt(z.low)}–${fmt(z.high)} · ${z.sources.slice(0, 3).join(' + ')}`,
  })) })
  const tls = lvlPats.filter(p => p.type.startsWith('trendline'))
  if (tls.length) groups.push({ title: 'Trendlines', layer: 'tl', items: tls.map(p => ({
    key: p.id, patternId: p.id, label: p.name + (p.status === 'confirmed' ? ' (broken)' : ''), on: on(p.id, 'tl'),
    sub: `${p.start} → ${p.end}${p.levels.level ? ` · now ${fmt(p.levels.level)}` : ''}`,
  })) })
  const sw = lvlPats.filter(p => levelLayer(p) === 'swing')
  if (sw.length) groups.push({ title: 'Swing structure', layer: 'swing', items: sw.map(p => ({ key: p.id, patternId: p.id, label: 'Swing labels (HH / HL / LH / LL)', on: on(p.id, 'swing'), sub: `${p.anchors.length} swing points` })) })
  const ap = lv.anchored_profile
  if (ap?.poc != null) groups.push({ title: 'Anchored volume profile', layer: 'avp', items: [{
    key: 'avp', label: 'Anchored POC & value area', on: on('avp', 'avp'),
    sub: `POC ${fmt(ap.poc)} · VA ${fmt(ap.va_low)}–${fmt(ap.va_high)}${ap.anchor ? ` · from ${ap.anchor}` : ''}`,
  }] })
  if (analystPatterns.length) groups.push({ title: 'Levels-engine patterns', layer: 'apat', items: analystPatterns.map(p => ({
    key: p.id, patternId: p.id, label: `${DIR_ICON[p.direction]} ${p.name}`, on: isAnalystVisible(p), sub: `${p.status} · ${p.end} · priority ${p.priority.toFixed(0)}`,
  })) })
  return groups
}

const SMC_GROUPS: { layer: keyof Layers; title: string }[] = [
  { layer: 'obkey', title: 'Key order blocks (nearest below & above price)' },
  { layer: 'ob', title: 'All order blocks (daily & weekly)' }, { layer: 'fvg', title: 'Fair value gaps' },
  { layer: 'pd', title: 'Premium / discount (equilibrium)' }, { layer: 'bos', title: 'Market structure (BOS / CHoCH)' },
  { layer: 'sliq', title: 'Liquidity: sweeps, equal highs / lows' },
]

/** SMC-engine drawings as switchable rows, one group per SMC layer. */
export function smcGroups(a: Analysis, prefs: StockPrefs): LevelGroup[] {
  const ov = prefs.levelOverrides
  const smc = a.patterns.filter(p => p.engine === 'smc')
  const sub = (p: Pattern) => {
    const z = p.zones[0]
    if (p.type === 'premium_discount') return `${fmt(p.levels.low)} → ${fmt(p.levels.high)} · EQ ${fmt(p.levels.equilibrium)} · price at ${((Number(p.context.pos) || 0) * 100).toFixed(0)}%`
    if (p.type === 'smc_structure') return p.lines.slice(-3).map(l => `${l.label} ${fmt(l.from.price)}`).join(' · ')
    if (z) return `${fmt(z.low)}–${fmt(z.high)} · ${p.context.fresh ? 'fresh' : p.context.touches ? `tested ${p.context.touches}×` : p.context.partial ? 'partly filled' : p.status} · ${p.start}`
    return `${p.levels.level != null ? fmt(p.levels.level) + ' · ' : ''}${p.end}`
  }
  const inGroup = (p: Pattern, layer: keyof Layers) => layer === 'obkey' ? !!p.context.key_block : smcLayer(p) === layer
  const htf = (a.recommendation as { smc?: { smc?: { labels?: { ltf: string; htf: string } } } })?.smc?.smc?.labels
  const groups: LevelGroup[] = SMC_GROUPS.map(g => ({
    title: g.layer === 'ob' && htf ? `All order blocks (${htf.ltf} & ${htf.htf})` : g.title, layer: g.layer,
    items: smc.filter(p => inGroup(p, g.layer)).map(p => ({
      key: p.id, patternId: p.id, label: `${DIR_ICON[p.direction]} ${p.name}`, on: smcOn(p, prefs.layers, ov), sub: sub(p), swatch: smcSwatchOf(p),
    })) })).filter(g => g.items.length)
  if (a.tf === '1m' || a.tf === '5m' || a.tf === '15m') groups.push({ title: 'CSE session windows (kill-zone adaptation)', layer: 'sess', items: [{
    key: 'sess', label: 'Opening hour 09:30–10:30 and closing hour 13:30–14:30', on: ov.sess ?? prefs.layers.sess,
    sub: 'shaded only, not scored: the London / New York kill zones do not apply to Colombo' }] })
  return groups
}

function LevelGroups({ groups, prefs, setLayer, setLevel, selected, onSelect }: {
  groups: LevelGroup[]; prefs: StockPrefs; setLayer: (k: keyof Layers, v: boolean, clear: string[]) => void
  setLevel: (key: string, v: boolean) => void; selected: string | null; onSelect: (id: string) => void
}) {
  return (
    <>
      {groups.map(g => (
        <div key={g.title} className="border-t border-[#141414]">
          <div className="flex items-center gap-2 px-3 py-1.5">
            <Switch on={prefs.layers[g.layer]} onChange={v => setLayer(g.layer, v, g.items.map(i => i.key))} label={`${g.title}: all`} />
            <span className="label flex-1">{g.title}</span>
            {g.items.length > 1 && <span className="text-[11px] text-muted">{g.items.filter(i => i.on).length}/{g.items.length} on</span>}
          </div>
          <ul>
            {g.items.map(it => (
              <li key={it.key} onClick={() => it.patternId && onSelect(it.patternId)}
                  className={`flex min-h-9 items-center gap-2 py-1.5 pl-8 pr-3 ${it.patternId ? 'cursor-pointer hover:bg-[#1d1405]' : ''} ${selected && selected === it.patternId ? 'bg-[#1d1405]' : ''}`}>
                <Switch on={it.on} onChange={v => setLevel(it.key, v)} label={`Show ${it.label}`} />
                {it.swatch && <SmcSwatch kind={it.swatch.kind} dir={it.swatch.dir} faded={it.swatch.faded} />}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-fg">{it.label}</span>
                  <span className="block truncate text-[11px] text-muted num" title={it.sub}>{it.sub}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  )
}

interface Props {
  a: Analysis
  prefs: StockPrefs
  update: (patch: Partial<StockPrefs>, alsoDefault?: boolean) => void
  applyPreset: (id: string) => void
  saveAsDefault: () => Promise<unknown>
  loadMyDefault: () => Promise<unknown>
  listed: Pattern[]
  analystPatterns: Pattern[]
  isVisible: (p: Pattern) => boolean
  isAnalystVisible: (p: Pattern) => boolean
  drawnCount: number
  selected: string | null
  onSelect: (id: string) => void
  onClose: () => void
}

function Group({ id, title, count, children, right, open, onToggle }: { id: string; title: string; count?: string; children: React.ReactNode; right?: React.ReactNode; open: boolean; onToggle: () => void }) {
  return (
    <section id={id} className="border-b border-line">
      <div className="panel-title sticky top-0 z-10">
        <button className="flex flex-1 items-center gap-2 text-left uppercase" aria-expanded={open} onClick={onToggle}>
          <span className="w-3 text-muted">{open ? '▾' : '▸'}</span>{title}{count && <span className="font-normal normal-case text-muted">{count}</span>}
        </button>{right}
      </div>
      {open && children}
    </section>
  )
}
const LIST_CAP = 12

export default function PatternDrawer(p: Props) {
  const { a, prefs, update } = p
  const [q, setQ] = useState('')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [saved, setSaved] = useState(false)
  const [openSec, setOpenSec] = useState({ orig: true, anly: true, smc: true, ind: true })
  const [showAll, setShowAll] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const jump = (id: 'orig' | 'anly' | 'smc' | 'ind') => {
    setOpenSec(s => ({ ...s, [id]: true }))
    window.setTimeout(() => scrollRef.current?.querySelector(`#sec-${id}`)?.scrollIntoView({ block: 'start' }), 0)
  }
  const searchRef = useRef<HTMLInputElement>(null)
  useEffect(() => { searchRef.current?.focus() }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); p.onClose() } }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [p])

  const needle = q.trim().toLowerCase()
  const match = (...xs: (string | undefined)[]) => !needle || xs.some(x => x?.toLowerCase().includes(needle))
  const listed = p.listed.filter(x => match(x.name, x.type, x.status))
  const groups = useMemo(() => analystGroups(a, prefs, p.analystPatterns, p.isAnalystVisible), [a, prefs, p.analystPatterns, p.isAnalystVisible])
    .map(g => ({ ...g, items: g.items.filter(i => match(i.label, i.sub, g.title)) })).filter(g => g.items.length)
  const sGroups = useMemo(() => smcGroups(a, prefs), [a, prefs])
    .map(g => ({ ...g, items: g.items.filter(i => match(i.label, i.sub, g.title, 'smc')) })).filter(g => g.items.length)
  const totalOrig = a.patterns.filter(x => x.category !== 'level' && (x.engine ?? 'original') === 'original').length

  const toggleIn = (key: 'categories' | 'directions' | 'statuses', v: string) => {
    const cur = prefs[key]
    update({ [key]: cur.includes(v) ? cur.filter(x => x !== v) : [...cur, v], preset: null } as Partial<StockPrefs>)
  }
  const setLayer = (k: keyof Layers, v: boolean, clearKeys: string[] = []) => {
    const lo = { ...prefs.levelOverrides }
    clearKeys.forEach(x => delete lo[x])
    update({ layers: { ...prefs.layers, [k]: v }, levelOverrides: lo, preset: null })
  }
  const setLevel = (key: string, v: boolean) => update({ levelOverrides: { ...prefs.levelOverrides, [key]: v }, preset: null })
  const setOverride = (id: string, v: boolean) => update({ overrides: { ...prefs.overrides, [id]: v }, preset: null })
  const bulk = (v: boolean) => update({ overrides: { ...prefs.overrides, ...Object.fromEntries(listed.map(x => [x.id, v])) }, preset: null })

  const ind: [keyof Layers, string][] = [['sr', 'S/R zones'], ['vp', 'Volume profile (120d)'], ['pools', 'Liquidity pools'], ['events', 'XR / split markers'],
    ['ema', 'EMA 21 / 50 / 200'], ['bb', 'Bollinger bands'], ['volume', 'Volume'], ['rsi', 'RSI pane'], ['macd', 'MACD pane'], ['zigzag', 'Zig-zag swings (price moved 2 × ATR)']]
  const indShown = ind.filter(([, l]) => match(l, 'indicator layer'))

  return (
    <aside role="dialog" aria-label="Patterns and layers" aria-modal="false"
           className="fixed bottom-0 right-0 top-0 z-40 flex w-full flex-col border-l border-amber/60 bg-black shadow-[-12px_0_30px_rgba(0,0,0,.7)] sm:w-[520px]">
      <div className="flex items-center gap-2 border-b border-amber/60 bg-amber px-3 py-1.5 text-black">
        <b className="flex-1 text-[13px] tracking-wider">PAT · PATTERNS & LAYERS</b>
        <span className="text-[11px] font-semibold num">{p.drawnCount} DRAWN</span>
        <button className="border border-black/40 px-2 text-[11px] font-semibold hover:bg-black hover:text-amber" onClick={p.onClose} aria-label="Close (Esc)">ESC ✕</button>
      </div>

      <div className="space-y-2 border-b border-line p-3">
        <input ref={searchRef} className="input w-full" placeholder="Search patterns, levels and layers… (e.g. wedge, fib, zone)" value={q}
               onChange={e => setQ(e.target.value)} aria-label="Search" />
        <div className="label">Presets</div>
        <div className="grid grid-cols-3 gap-1 sm:grid-cols-4">
          {PRESETS.map(x => (
            <button key={x.id} className="btn !justify-center !py-1" aria-pressed={prefs.preset === x.id} title={x.desc} onClick={() => p.applyPreset(x.id)}>{x.label}</button>
          ))}
          <button className="btn !justify-center !py-1" aria-pressed={prefs.preset === 'mine'} title="Your saved default view" onClick={() => p.loadMyDefault()}>My default</button>
          <button className="btn !justify-center !py-1 col-span-2 sm:col-span-1" onClick={async () => { await p.saveAsDefault(); setSaved(true); window.setTimeout(() => setSaved(false), 2000) }}
                  title="Use the current view as the default for every stock">{saved ? 'Saved ✓' : 'Save as default'}</button>
        </div>
        <p className="text-[11px] text-muted">{PRESETS.find(x => x.id === prefs.preset)?.desc ?? (prefs.preset === 'mine' ? 'Your saved default view' : 'Custom view')}</p>
        <div className="flex gap-1" role="navigation" aria-label="Jump to section">
          <span className="label self-center pr-1">Go to</span>
          <button className="btn !py-0.5" onClick={() => jump('orig')}>1) Classic</button>
          <button className="btn !py-0.5" onClick={() => jump('anly')}>2) Levels</button>
          <button className="btn !py-0.5" onClick={() => jump('smc')}>3) SMC</button>
          <button className="btn !py-0.5" onClick={() => jump('ind')}>4) Indicators</button>
        </div>
      </div>

      <div ref={scrollRef} className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <Group id="sec-orig" title="1) Classic patterns" count={`${listed.filter(p.isVisible).length} shown · ${listed.length} listed · ${totalOrig} found`}
               right={<EngineBadge e="original" />} open={openSec.orig} onToggle={() => setOpenSec(s => ({ ...s, orig: !s.orig }))}>
          <div className="space-y-2 p-3">
            <div className="flex flex-wrap items-center gap-1" role="radiogroup" aria-label="Which classic patterns are drawn">
              {(['top5', 'high', 'all', 'none'] as Mode[]).map(m => (
                <button key={m} role="radio" aria-checked={prefs.mode === m} className="btn !py-1"
                        onClick={() => update({ mode: m, overrides: {}, preset: null }, true)}>
                  {m === 'top5' ? 'Top 5' : m === 'high' ? 'High only' : m === 'all' ? 'All' : 'None'}
                </button>
              ))}
              <button className="btn ml-auto !py-1" aria-expanded={filtersOpen} onClick={() => setFiltersOpen(o => !o)}>Filters {filtersOpen ? '▴' : '▾'}</button>
            </div>
            {filtersOpen && (
              <div className="space-y-2 border border-line bg-panel p-2">
                <div><div className="label mb-1">Category</div><div className="flex flex-wrap gap-1">
                  {CATEGORIES.map(c => <button key={c} className="btn !py-0.5" aria-pressed={prefs.categories.includes(c)} onClick={() => toggleIn('categories', c)}>{CATEGORY_LABEL[c]}</button>)}
                </div>
                {!prefs.categories.includes('candlestick') && <p className="mt-1 text-[11px] text-muted">Candlestick patterns are off by default: over 14 years they showed no edge beyond the stock's trend (pooled +0.2 points, not significant). Switch Candles on to see them; the engines' scores are unchanged either way.</p>}
                </div>
                <div><div className="label mb-1">Direction</div><div className="flex flex-wrap gap-1">
                  {['bullish', 'bearish', 'neutral'].map(d => <button key={d} className="btn !py-0.5" aria-pressed={prefs.directions.includes(d)} onClick={() => toggleIn('directions', d)}>{DIR_ICON[d]} {d}</button>)}
                </div></div>
                <div><div className="label mb-1">Status</div><div className="flex flex-wrap gap-1">
                  {['forming', 'confirmed', 'failed', 'expired'].map(s => <button key={s} className="btn !py-0.5" aria-pressed={prefs.statuses.includes(s)} onClick={() => toggleIn('statuses', s)}>{s}</button>)}
                </div></div>
                <div className="flex items-center gap-2"><span className="label">Age</span>
                  <select className="input !py-0.5" value={prefs.maxAge ?? ''} aria-label="Recency" onChange={e => update({ maxAge: e.target.value ? Number(e.target.value) : null, preset: null }, true)}>
                    <option value="20">last 20 bars</option><option value="60">last 60 bars</option><option value="90">last 90 bars</option><option value="">any age</option>
                  </select>
                </div>
              </div>
            )}
            <div className="flex gap-1">
              <button className="btn !py-0.5" onClick={() => bulk(true)} disabled={!listed.length}>Show all listed</button>
              <button className="btn !py-0.5" onClick={() => bulk(false)} disabled={!listed.length}>Hide all listed</button>
            </div>
          </div>
          <ul>
            {listed.length === 0 && <li className="px-3 pb-3 text-muted">No classic patterns match.</li>}
            {(showAll || needle ? listed : listed.slice(0, LIST_CAP)).map((x, i) => {
              const vis = p.isVisible(x)
              return (
                <li key={x.id} onClick={() => p.onSelect(x.id)} title={x.explanation}
                    className={`flex min-h-9 cursor-pointer items-center gap-2 border-t border-[#141414] px-3 py-1.5 hover:bg-[#1d1405] ${p.selected === x.id ? 'bg-[#1d1405]' : ''}`}>
                  <Switch on={vis} onChange={v => setOverride(x.id, v)} label={`Show ${x.name} on chart`} />
                  <span className="w-6 text-right text-[11px] text-muted num">{i + 1})</span>
                  <span className={dirCls(x.direction)}>{DIR_ICON[x.direction]}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-fg">{x.name}</span>
                    <span className="block text-[11px] text-muted">{x.status} · {x.end}</span>
                  </span>
                  <span className="flex w-20 items-center gap-1.5" title="Priority 0–100: reliability × context × recency × status">
                    <span className="h-1.5 flex-1 bg-panel-3"><span className="block h-full" style={{ width: `${x.priority}%`, background: x.priority_tier === 'high' ? 'var(--bull)' : x.priority_tier === 'medium' ? 'var(--amber)' : 'var(--muted)' }} /></span>
                    <span className="w-6 text-right text-[11px] num">{x.priority.toFixed(0)}</span>
                  </span>
                </li>
              )
            })}
            {!needle && listed.length > LIST_CAP && (
              <li><button className="w-full border-t border-[#141414] px-3 py-1.5 text-left text-[11px] text-amber hover:bg-panel-2" onClick={() => setShowAll(s => !s)}>
                {showAll ? '▴ Show the top 12 only' : `▾ Show all ${listed.length} (lower priority)`}
              </button></li>
            )}
          </ul>
        </Group>

        <Group id="sec-anly" title="2) Levels (Fibonacci, zones, trendlines)" right={<EngineBadge e="analyst" />} open={openSec.anly} onToggle={() => setOpenSec(s => ({ ...s, anly: !s.anly }))}>
          {groups.length === 0 && <p className="p-3 text-muted">{needle ? 'Nothing matches.' : 'No levels-engine drawings for this stock yet (re-analyse).'}</p>}
          <LevelGroups groups={groups} prefs={prefs} setLayer={setLayer} setLevel={setLevel} selected={p.selected} onSelect={p.onSelect} />
        </Group>

        <Group id="sec-smc" title="3) SMC: order blocks & liquidity" right={<EngineBadge e="smc" />} open={openSec.smc} onToggle={() => setOpenSec(s => ({ ...s, smc: !s.smc }))}>
          {sGroups.length === 0 && <p className="p-3 text-muted">{needle ? 'Nothing matches.' : 'No SMC drawings for this stock yet (re-analyse).'}</p>}
          <LevelGroups groups={sGroups} prefs={prefs} setLayer={setLayer} setLevel={setLevel} selected={p.selected} onSelect={p.onSelect} />
        </Group>

        <Group id="sec-ind" title="4) Chart & indicators" open={openSec.ind} onToggle={() => setOpenSec(s => ({ ...s, ind: !s.ind }))}>
          <div className="space-y-2 p-3">
            {match('trade plan', 'plan') && (
              <div className="flex flex-wrap items-center gap-2">
                <Switch on={prefs.layers.plan} onChange={v => setLayer('plan', v)} label="Trade plan" />
                <span className="label">Trade plan</span>
                <div className="ml-auto flex gap-1" role="radiogroup" aria-label="Which engine's plan is drawn">
                  {(['original', 'analyst', 'smc', 'both'] as PlanEngine[]).map(e => (
                    <button key={e} role="radio" aria-checked={prefs.planEngine === e} className="btn !py-0.5" onClick={() => update({ planEngine: e, preset: null })}>
                      {e === 'original' ? 'Classic' : e === 'analyst' ? 'Levels' : e === 'smc' ? 'SMC' : 'All'}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
              {indShown.map(([k, label]) => (
                <label key={k} className="flex min-h-8 cursor-pointer items-center gap-2 border border-[#1a1a1a] px-2 hover:bg-panel-2">
                  <Switch on={prefs.layers[k]} onChange={v => setLayer(k, v)} label={label} />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </div>
        </Group>
      </div>

      <div className="flex items-center gap-2 border-t border-line bg-panel px-3 py-1.5 text-[11px]">
        <span className="flex-1 text-muted">Plans: Classic white solid (C:) · Levels pink dashed (L:) · SMC lavender long dashes (S:)</span>
        <button className="btn !py-0.5" onClick={() => p.applyPreset('both')}>Reset</button>
      </div>
    </aside>
  )
}
