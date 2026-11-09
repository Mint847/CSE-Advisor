import { useEffect, useRef, useState } from 'react'
import { ALL_ENGINES, api, type CatalogEntry, type ChartStyle, type EngineMask, type Pattern } from './api'
import { DEFAULT_LAYERS, type Layers, type ScaleMode } from './chart/PriceChart'
import type { Drawing } from './chart/drawings'

export type Mode = 'top5' | 'high' | 'all' | 'none'
export type PlanEngine = 'original' | 'analyst' | 'smc' | 'both'   // 'both' = every engine (kept for saved prefs)
export interface StockPrefs {
  mode: Mode                               // which Classic-engine patterns are drawn by default
  categories: string[]
  directions: string[]
  statuses: string[]
  maxAge: number | null                    // only patterns that ended within N bars
  overrides: Record<string, boolean>       // per-pattern show/hide (Original patterns)
  levelOverrides: Record<string, boolean>  // per-drawing show/hide (Analyst levels and SMC drawings: pattern ids, 'zone1', 'zone2', 'sell0'…)
  layers: Layers
  planEngine: PlanEngine
  preset: string | null
  chartStyle: ChartStyle                   // candlesticks or a close-price line
  engines: EngineMask                      // whose drawings are shown: Classic, Levels, SMC
  scale?: ScaleMode                        // price scale: normal, logarithmic or % change
  compare?: string[]                       // up to 4 series drawn as % change beside the stock (daily chart)
  v?: number                               // preference version: 2 = candlestick patterns off by default
}
export const CATEGORIES = ['chart_pattern', 'liquidity', 'divergence', 'candlestick']
/** Candlestick patterns tested at no edge over 14 years (pooled excess +0.2 pts, t < 1), so they start switched off. */
export const DEFAULT_CATEGORIES = ['chart_pattern', 'liquidity', 'divergence']
const PREFS_VERSION = 2
export const CATEGORY_LABEL: Record<string, string> = { chart_pattern: 'Chart', liquidity: 'Liquidity', divergence: 'Divergence', candlestick: 'Candles' }

export const DEFAULT_PREFS: StockPrefs = {
  mode: 'top5', categories: DEFAULT_CATEGORIES, directions: ['bullish', 'bearish', 'neutral'],
  statuses: ['forming', 'confirmed', 'failed'], maxAge: 90, overrides: {}, levelOverrides: {}, layers: DEFAULT_LAYERS,
  planEngine: 'both', preset: 'both', chartStyle: 'candles', engines: ALL_ENGINES, scale: 'normal', compare: [], v: PREFS_VERSION,
}

const OFF = Object.fromEntries(Object.keys(DEFAULT_LAYERS).map(k => [k, false])) as unknown as Layers
const ON = Object.fromEntries(Object.keys(DEFAULT_LAYERS).map(k => [k, true])) as unknown as Layers

/** One-click display presets. Each returns a patch; per-item overrides are cleared. */
export const PRESETS: { id: string; label: string; desc: string; patch: () => Partial<StockPrefs> }[] = [
  { id: 'clean', label: 'Clean', desc: 'Trade plans and buy zones only',
    patch: () => ({ mode: 'none', planEngine: 'both', engines: ALL_ENGINES, layers: { ...OFF, plan: true, zones: true, volume: true } }) },
  { id: 'original', label: 'Classic', desc: 'Top 5 classic patterns, S/R, EMAs, Classic plan',
    patch: () => ({ mode: 'top5', planEngine: 'original', engines: { original: true, analyst: false, smc: false }, layers: { ...OFF, sr: true, vp: true, ema: true, plan: true, volume: true, rsi: true } }) },
  { id: 'analyst', label: 'Levels', desc: 'Fibonacci, buy/sell zones, trendlines, XR, swings, Levels entries',
    patch: () => ({ mode: 'none', planEngine: 'analyst', engines: { original: false, analyst: true, smc: false }, layers: { ...OFF, fib: true, zones: true, sellz: true, tl: true, events: true, swing: true, apat: true, avp: true, plan: true, ema: true, volume: true, rsi: true } }) },
  { id: 'smc', label: 'SMC', desc: 'Order blocks, fair value gaps, premium/discount, BOS/CHoCH, liquidity, SMC entries',
    patch: () => ({ mode: 'none', planEngine: 'smc', engines: { original: false, analyst: false, smc: true }, layers: { ...OFF, ob: true, fvg: true, pd: true, bos: true, sliq: true, plan: true, events: true, volume: true } }) },
  { id: 'both', label: 'All 3', desc: 'Default: all three engines side by side',
    patch: () => ({ mode: 'top5', planEngine: 'both', engines: ALL_ENGINES, layers: DEFAULT_LAYERS }) },
  { id: 'everything', label: 'Everything', desc: 'Every pattern and every layer',
    patch: () => ({ mode: 'all', planEngine: 'both', engines: ALL_ENGINES, maxAge: null, layers: ON }) },
  { id: 'none', label: 'None', desc: 'Bare candles and volume',
    patch: () => ({ mode: 'none', engines: ALL_ENGINES, layers: { ...OFF, volume: true } }) },
]

/** Per-stock display preferences, persisted in the backend (falls back to the global default). */
export function useStockPrefs(symbol: string) {
  const [prefs, setPrefs] = useState<StockPrefs>(DEFAULT_PREFS)
  const [loaded, setLoaded] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => {
    let alive = true
    setLoaded(false)
    Promise.all([api.getPref<StockPrefs>(`stock:${symbol}`), api.getPref<StockPrefs>('stock:default')])
      .then(([own, def]) => { if (alive) { setPrefs(merge(def, own)); setLoaded(true) } })
      .catch(() => alive && setLoaded(true))
    return () => { alive = false }
  }, [symbol])
  const update = (patch: Partial<StockPrefs>, alsoDefault = false) => {
    setPrefs(prev => {
      const next = { ...prev, ...patch }
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => {
        api.putPref(`stock:${symbol}`, next).catch(() => {})
        if (alsoDefault) api.putPref('stock:default', { ...next, overrides: {}, levelOverrides: {} }).catch(() => {})
      }, 400)
      return next
    })
  }
  const applyPreset = (id: string) => {
    const p = PRESETS.find(x => x.id === id)
    if (p) update({ ...p.patch(), overrides: {}, levelOverrides: {}, preset: id })
  }
  const saveAsDefault = () => api.putPref('stock:default', { ...prefs, overrides: {}, levelOverrides: {}, preset: 'mine' })
  const loadMyDefault = () => api.getPref<StockPrefs>('stock:default').then(d => d && update({ ...merge(d, null), overrides: {}, levelOverrides: {}, preset: 'mine' }))
  return { prefs, update, loaded, applyPreset, saveAsDefault, loadMyDefault }
}

function merge(def: StockPrefs | null, own: StockPrefs | null): StockPrefs {
  const m = mergeRaw(def, own)
  // preferences saved before version 2 had candlestick patterns on: switch them off once (the drawer can turn them back on)
  const ver = own?.v ?? def?.v ?? 1
  return ver < PREFS_VERSION ? { ...m, v: PREFS_VERSION, categories: m.categories.filter(c => c !== 'candlestick') } : m
}

function mergeRaw(def: StockPrefs | null, own: StockPrefs | null): StockPrefs {
  return { ...DEFAULT_PREFS, ...(def ?? {}), ...(own ?? {}),
    levelOverrides: { ...(own?.levelOverrides ?? {}) },
    engines: { ...ALL_ENGINES, ...(def?.engines ?? {}), ...(own?.engines ?? {}) },
    layers: { ...DEFAULT_LAYERS, ...(def?.layers ?? {}), ...(own?.layers ?? {}) } }
}

export function useCatalog() {
  const [catalog, setCatalog] = useState<Record<string, CatalogEntry>>({})
  const [overrides, setOverrides] = useState<Record<string, boolean>>({})
  useEffect(() => {
    api.catalog().then(c => setCatalog(c.patterns)).catch(() => {})
    api.getPref<Record<string, boolean>>('catalog').then(o => setOverrides(o ?? {})).catch(() => {})
  }, [])
  const enabled = (type: string) => overrides[type] ?? catalog[type]?.enabled ?? true
  const setEnabled = (type: string, on: boolean) => {
    const next = { ...overrides, [type]: on }
    setOverrides(next)
    api.putPref('catalog', next).catch(() => {})
  }
  return { catalog, enabled, setEnabled }
}

const isAnalyst = (p: Pattern) => p.engine === 'analyst'

/** Splits patterns into the Original list (priority-ranked, mode + filters) and Analyst-engine patterns
 *  (hidden divergences, long flags). Level drawings (Fibonacci, trendlines, swings) are handled by levelPatterns(). */
export function filterPatterns(all: Pattern[], prefs: StockPrefs, enabled: (t: string) => boolean) {
  const recent = (p: Pattern) => prefs.maxAge == null || (p.context.age_bars ?? 0) <= prefs.maxAge
  const listed = all
    .filter(p => p.category !== 'level' && !isAnalyst(p) && enabled(p.type) && prefs.categories.includes(p.category)
      && prefs.directions.includes(p.direction) && prefs.statuses.includes(p.status) && recent(p))
    .sort((a, b) => b.priority - a.priority)
  const base = prefs.mode === 'none' ? new Set<string>()
    : prefs.mode === 'top5' ? new Set(listed.slice(0, 5).map(p => p.id))
    : prefs.mode === 'high' ? new Set(listed.filter(p => p.priority_tier === 'high').map(p => p.id))
    : new Set(listed.map(p => p.id))
  const isVisible = (p: Pattern) => prefs.overrides[p.id] ?? base.has(p.id)
  // SMC drawings (category 'smc') are neither listed nor analyst patterns: smcPatterns() draws them by layer
  const analystPatterns = all.filter(p => p.category !== 'level' && isAnalyst(p) && enabled(p.type) && recent(p))
    .sort((a, b) => b.priority - a.priority)
  const isAnalystVisible = (p: Pattern) => prefs.levelOverrides[p.id] ?? prefs.layers.apat
  return { listed, analystPatterns, visible: [...listed.filter(isVisible), ...analystPatterns.filter(isAnalystVisible)], isVisible, isAnalystVisible }
}


/** Your drawings on one stock's chart, saved for your account (UserPref `draw:<symbol>`), with undo. */
export function useDrawings(symbol: string) {
  const [drawings, setDrawings] = useState<Drawing[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const list = useRef<Drawing[]>([])
  const past = useRef<Drawing[][]>([])
  const timer = useRef<number | undefined>(undefined)
  const key = `draw:${symbol}`
  useEffect(() => {
    let alive = true
    list.current = []; past.current = []
    setDrawings([]); setSelected(null)
    api.getPref<Drawing[]>(key).then(v => { if (alive && Array.isArray(v)) { list.current = v; setDrawings(v) } }).catch(() => {})
    return () => { alive = false }
  }, [key])
  const save = (next: Drawing[]) => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => { api.putPref(key, next).catch(() => {}) }, 300)
  }
  const commit = (next: Drawing[], select?: string | null) => {
    past.current = [...past.current.slice(-49), list.current]
    list.current = next
    setDrawings(next)
    if (select !== undefined) setSelected(select)
    save(next)
  }
  const undo = () => {
    const prev = past.current.pop()
    if (!prev) return
    list.current = prev
    setDrawings(prev)
    setSelected(null)
    save(prev)
  }
  const remove = (id: string) => commit(list.current.filter(d => d.id !== id), null)
  const recolor = (id: string, color: string) => commit(list.current.map(d => d.id === id ? { ...d, color } : d), id)
  return { drawings, selected, select: setSelected, commit, undo, remove, recolor, clear: () => commit([], null), canUndo: past.current.length > 0 }
}
