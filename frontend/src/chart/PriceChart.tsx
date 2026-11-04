import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, createSeriesMarkers,
  type IChartApi, type IPriceLine, type ISeriesApi, type ISeriesMarkersPluginApi, type Logical, type SeriesMarker, type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { Analysis, Candles, ChartStyle, Pattern, Quote } from '../api'
import { engines, fmt } from '../api'
import { CHART_FONT, PatternOverlay, SESSION_CLOSE, SESSION_OPEN, TimeMapper, readPalette, type DrawnPlan, type Palette } from './overlay'

/** Chart time for a bar label. Daily 'YYYY-MM-DD' stays a business-day string; intraday 'YYYY-MM-DD HH:MM'
 *  (Colombo wall time) becomes a UTC timestamp of that same wall time, so the axis reads Colombo time. */
export function ct(t: string): Time {
  if (t.length <= 10) return t as Time
  return (Date.UTC(+t.slice(0, 4), +t.slice(5, 7) - 1, +t.slice(8, 10), +t.slice(11, 13), +t.slice(14, 16)) / 1000) as UTCTimestamp
}
/** Inverse of ct(): the bar label for a chart time. */
export function barLabel(t: Time): string {
  if (typeof t === 'number') return new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ')
  if (typeof t === 'string') return t
  return `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`
}
const TF_MIN: Record<string, number> = { '1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240 }

export interface Layers {
  sr: boolean; vp: boolean; pools: boolean; plan: boolean
  ema: boolean; bb: boolean; volume: boolean; rsi: boolean; macd: boolean
  fib: boolean; tl: boolean; swing: boolean; zones: boolean; events: boolean
  sellz: boolean; avp: boolean; apat: boolean
  ob: boolean; obkey: boolean; fvg: boolean; pd: boolean; bos: boolean; sliq: boolean
  sess: boolean; zigzag: boolean
}
export const DEFAULT_LAYERS: Layers = { sr: true, vp: true, pools: false, plan: true, ema: true, bb: false, volume: true, rsi: true, macd: false,
  fib: true, tl: true, swing: false, zones: true, events: true, sellz: false, avp: false, apat: true,
  ob: false, obkey: true, fvg: false, pd: false, bos: false, sliq: false, sess: false, zigzag: false }

/** CSE opening-hour and closing-hour windows for each session in an intraday series (1m / 5m / 15m only). */
export function sessionWindows(times: string[], tf: string): { t1: string; t2: string; label: string }[] {
  if (tf !== '1m' && tf !== '5m' && tf !== '15m') return []
  const byDay = new Map<string, string[]>()
  for (const t of times) {
    const d = t.slice(0, 10)
    if (!byDay.has(d)) byDay.set(d, [])
    byDay.get(d)!.push(t)
  }
  const out: { t1: string; t2: string; label: string }[] = []
  const m = (t: string) => +t.slice(11, 13) * 60 + +t.slice(14, 16)
  for (const day of byDay.values()) {
    const open = day.filter(t => m(t) < SESSION_OPEN + 60), close = day.filter(t => m(t) >= SESSION_CLOSE - 60)
    if (open.length) out.push({ t1: open[0], t2: open[open.length - 1], label: 'OPEN HR' })
    if (close.length) out.push({ t1: close[0], t2: close[close.length - 1], label: 'CLOSE HR' })
  }
  return out
}

/** Which SMC layer switch controls an SMC drawing. */
export function smcLayer(p: Pattern): keyof Layers {
  return p.type.endsWith('order_block') ? 'ob' : p.type.endsWith('fvg') ? 'fvg' : p.type === 'premium_discount' ? 'pd'
    : p.type === 'smc_structure' ? 'bos' : 'sliq'
}
/** Is an SMC drawing on? Its layer, or (for the nearest block above / below price) the key-blocks layer; a per-drawing switch wins. */
export function smcOn(p: Pattern, layers: Layers, overrides: Record<string, boolean> = {}): boolean {
  return overrides[p.id] ?? (layers[smcLayer(p)] || (!!p.context.key_block && layers.obkey))
}
export function smcPatterns(all: Pattern[], layers: Layers, overrides: Record<string, boolean> = {}): Pattern[] {
  return all.filter(p => p.engine === 'smc' && smcOn(p, layers, overrides))
}

/** Which layer switch controls a level drawing by default. */
export function levelLayer(p: Pattern): keyof Layers | null {
  return p.type === 'fibonacci' ? 'fib' : p.type.startsWith('trendline') ? 'tl' : p.type === 'swing_structure' ? 'swing' : null
}
/** Level-type drawings (Analyst engine): on by their layer, unless switched individually. */
export function levelPatterns(all: Pattern[], layers: Layers, overrides: Record<string, boolean> = {}): Pattern[] {
  return all.filter(p => {
    if (p.category !== 'level') return false
    const k = levelLayer(p)
    return overrides[p.id] ?? (k ? layers[k] : false)
  })
}

/** Zig-zag swing points: price has to move `mult` x ATR(14) against the running extreme to turn. Uses the closes for the
 *  line chart (so the zig-zag sits on the line) and the highs / lows for candles. The last point is the leg in progress. */
export function zigzagPoints(bars: Candles['bars'], onClose: boolean, mult = 2): { time: string; value: number }[] {
  const n = bars.length
  if (n < 20) return []
  const hi = bars.map(b => (onClose ? b.close : b.high)), lo = bars.map(b => (onClose ? b.close : b.low))
  const tr = bars.map((b, i) => { const pc = i ? bars[i - 1].close : b.close; return Math.max(b.high - b.low, Math.abs(b.high - pc), Math.abs(b.low - pc)) })
  let sum = 0
  const atr = tr.map((t, i) => { sum += t - (i >= 14 ? tr[i - 14] : 0); return sum / Math.min(i + 1, 14) })
  const out: { time: string; value: number }[] = []
  let dir = 0, h = 0, l = 0
  for (let i = 1; i < n; i++) {
    if (hi[i] > hi[h]) h = i
    if (lo[i] < lo[l]) l = i
    const th = mult * atr[i]
    if (dir === 0) {
      if (hi[h] - lo[i] >= th && h < i) { out.push({ time: bars[h].time, value: hi[h] }); dir = -1; l = i }
      else if (hi[i] - lo[l] >= th && l < i) { out.push({ time: bars[l].time, value: lo[l] }); dir = 1; h = i }
    } else if (dir === 1) {
      if (hi[h] - lo[i] >= th) { out.push({ time: bars[h].time, value: hi[h] }); dir = -1; l = i }
    } else if (hi[i] - lo[l] >= th) { out.push({ time: bars[l].time, value: lo[l] }); dir = 1; h = i }
  }
  if (dir === 1) out.push({ time: bars[h].time, value: hi[h] })
  else if (dir === -1) out.push({ time: bars[l].time, value: lo[l] })
  return out
}

interface Props {
  candles: Candles
  analysis: Analysis | null
  visible: Pattern[]
  layers: Layers
  highlightId: string | null
  onSelect: (id: string | null) => void
  quote: Quote | null
  focus: { id: string; nonce: number } | null
  planEngine?: 'original' | 'analyst' | 'smc' | 'both'
  levelOverrides?: Record<string, boolean>
  chartStyle?: ChartStyle
}

const NO_OVERRIDES: Record<string, boolean> = {}

const alpha = (hex: string, a: number) => {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}

export default function PriceChart({ candles, analysis, visible, layers, highlightId, onSelect, quote, focus, planEngine = 'both', levelOverrides = NO_OVERRIDES, chartStyle = 'candles' }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const s = useRef<Record<string, ISeriesApi<any> | null>>({})
  const overlay = useRef<PatternOverlay | null>(null)
  const rsiOverlay = useRef<PatternOverlay | null>(null)
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const priceLines = useRef<IPriceLine[]>([])
  const viewKey = useRef('')
  const [palette, setPalette] = useState<Palette>(() => readPalette())
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null)
  const [legend, setLegend] = useState<{ time: string; o: number; h: number; l: number; c: number; est: boolean } | null>(null)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  const times = useMemo(() => candles.bars.map(b => b.time), [candles])
  const mapper = useMemo(() => new TimeMapper(times), [times])
  const barByTime = useMemo(() => new Map(candles.bars.map(b => [b.time, b])), [candles])
  const patternById = useMemo(() => new Map((analysis?.patterns ?? []).map(p => [p.id, p])), [analysis])

  // ---- create chart once
  useEffect(() => {
    const el = box.current!
    const pal = readPalette()
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: pal.bg }, textColor: pal.muted, fontSize: 11,
        fontFamily: CHART_FONT, panes: { separatorColor: '#2a2a2a' }, attributionLogo: false },
      grid: { vertLines: { color: pal.grid, style: LineStyle.Dotted }, horzLines: { color: pal.grid, style: LineStyle.Dotted } },
      crosshair: { mode: CrosshairMode.Normal,
        vertLine: { color: alpha(pal.accent, 0.5), labelBackgroundColor: pal.accent },
        horzLine: { color: alpha(pal.accent, 0.5), labelBackgroundColor: pal.accent } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.22 } },
      timeScale: { borderVisible: false, rightOffset: 12, barSpacing: 7, minBarSpacing: 2 },
    })
    chartRef.current = chart
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: pal.bull, downColor: pal.bear, borderVisible: false, wickUpColor: pal.bull, wickDownColor: pal.bear,
      priceLineVisible: true, lastValueVisible: true,
    })
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false })
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    const line = (color: string, width = 1, style: LineStyle = LineStyle.Solid) =>
      chart.addSeries(LineSeries, { color, lineWidth: width as 1, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    s.current = {
      candle, volume,
      close: chart.addSeries(LineSeries, { color: pal.blue, lineWidth: 2, priceLineVisible: true, lastValueVisible: true, crosshairMarkerVisible: true, visible: false }),
      zigzag: chart.addSeries(LineSeries, { color: pal.accent, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false }),
      ema21: line(pal.ema21), ema50: line(pal.ema50), ema200: line(pal.ema200, 2),
      bb_up: line(alpha(pal.muted, 0.7), 1, LineStyle.Dashed), bb_mid: line(alpha(pal.muted, 0.5), 1, LineStyle.Dotted),
      bb_lo: line(alpha(pal.muted, 0.7), 1, LineStyle.Dashed),
    }
    const ov = new PatternOverlay({ pane: 'main', patterns: [], highlightId: null, hoverId: null, zones: null, pools: null, profile: null, plans: [], buyZones: null }, pal, new TimeMapper([]))
    candle.attachPrimitive(ov)
    overlay.current = ov
    markers.current = createSeriesMarkers(candle, [])

    chart.subscribeCrosshairMove(param => {
      const id = typeof param.hoveredObjectId === 'string' ? param.hoveredObjectId : null
      setHover(id && param.point ? { id, x: param.point.x, y: param.point.y } : null)
      overlay.current?.set({ hoverId: id })
      rsiOverlay.current?.set({ hoverId: id })
      const t = param.time != null ? barLabel(param.time) : undefined
      if (t) {
        const d = param.seriesData.get(candle) as { open: number; high: number; low: number; close: number } | undefined
        if (d) setLegend({ time: t, o: d.open, h: d.high, l: d.low, c: d.close, est: false })
      } else setLegend(null)
    })
    chart.subscribeClick(param => {
      const id = typeof param.hoveredObjectId === 'string' ? param.hoveredObjectId : null
      onSelectRef.current(id)
    })
    // the theme is dark-only; re-read the tokens once styles have settled
    const t = window.setTimeout(() => setPalette(readPalette()), 300)
    return () => { window.clearTimeout(t); chart.remove(); chartRef.current = null }
  }, [])

  // ---- theme changes
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.applyOptions({ layout: { background: { type: ColorType.Solid, color: palette.bg }, textColor: palette.muted },
      grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } } })
    s.current.close?.applyOptions({ color: palette.blue })
    s.current.zigzag?.applyOptions({ color: palette.accent })
    overlay.current?.set({}, undefined, palette)
    rsiOverlay.current?.set({}, undefined, palette)
  }, [palette])

  // ---- data
  useEffect(() => {
    const cs = s.current
    if (!cs.candle) return
    const intraday = candles.bars.length > 0 && candles.bars[0].time.length > 10
    chartRef.current?.applyOptions({ timeScale: { timeVisible: intraday, secondsVisible: false } })
    cs.candle.setData(candles.bars.map(b => ({
      time: ct(b.time), open: b.open, high: b.high, low: b.low, close: b.close,
      ...(b.est && chartStyle === 'candles' ? { color: alpha(b.close >= b.open ? palette.bull : palette.bear, 0.55) } : {}),
    })))
    cs.close?.setData(candles.bars.map(b => ({ time: ct(b.time), value: b.close })))
    cs.volume!.setData(candles.bars.map(b => ({ time: ct(b.time), value: b.volume, color: alpha(b.close >= b.open ? palette.bull : palette.bear, 0.35) })))
    for (const k of ['ema21', 'ema50', 'ema200', 'bb_up', 'bb_mid', 'bb_lo'])
      cs[k]?.setData((candles.indicators[k] ?? []).map(p => ({ time: ct(p.time), value: p.value })))
    overlay.current?.set({}, mapper)
    rsiOverlay.current?.set({}, mapper)
    // reset the view only for a new stock / timeframe, not when the same series refreshes during the session
    const key = `${candles.symbol}|${candles.timeframe}|${candles.adjusted}`
    if (viewKey.current !== key) {
      viewKey.current = key
      const n = candles.bars.length
      chartRef.current?.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 150) as Logical, to: (n + 8) as Logical })
    }
  }, [candles, mapper, palette, chartStyle])

  // ---- candles or a close-price line. The candle series stays (it carries the drawings, markers and plan lines):
  // in line mode it is drawn transparent and left out of the price-scale range
  useEffect(() => {
    const cs = s.current
    if (!cs.candle) return
    const line = chartStyle === 'line'
    const clear = 'rgba(0,0,0,0)'
    cs.candle.applyOptions({
      upColor: line ? clear : palette.bull, downColor: line ? clear : palette.bear, wickUpColor: line ? clear : palette.bull, wickDownColor: line ? clear : palette.bear,
      priceLineVisible: !line, lastValueVisible: !line,
      autoscaleInfoProvider: line ? () => null : (base: () => unknown) => base() as never,
    })
    cs.close?.applyOptions({ visible: line })
  }, [chartStyle, palette])

  // ---- zig-zag swings: the line chart's closes, or the candles' highs and lows
  useEffect(() => {
    const z = s.current.zigzag
    if (!z) return
    z.applyOptions({ visible: layers.zigzag })
    z.setData(layers.zigzag ? zigzagPoints(candles.bars, chartStyle === 'line').map(p => ({ time: ct(p.time), value: p.value })) : [])
  }, [layers.zigzag, candles, chartStyle])

  // ---- indicator layers & panes
  useEffect(() => {
    const chart = chartRef.current, cs = s.current
    if (!chart) return
    for (const k of ['ema21', 'ema50', 'ema200']) cs[k]?.applyOptions({ visible: layers.ema })
    for (const k of ['bb_up', 'bb_mid', 'bb_lo']) cs[k]?.applyOptions({ visible: layers.bb })
    cs.volume?.applyOptions({ visible: layers.volume })

    const pal = palette
    const removeSeries = (k: string) => { if (cs[k]) { chart.removeSeries(cs[k]!); cs[k] = null } }
    // RSI pane
    if (layers.rsi && !cs.rsi) {
      const rsi = chart.addSeries(LineSeries, { color: pal.rsi, lineWidth: 1, priceLineVisible: false, lastValueVisible: true }, 1)
      rsi.createPriceLine({ price: 70, color: alpha(pal.bear, 0.6), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '' })
      rsi.createPriceLine({ price: 30, color: alpha(pal.bull, 0.6), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '' })
      const ro = new PatternOverlay({ pane: 'rsi', patterns: [], highlightId: null, hoverId: null, zones: null, pools: null, profile: null, plans: [] }, pal, mapper)
      rsi.attachPrimitive(ro)
      rsiOverlay.current = ro
      cs.rsi = rsi
    } else if (!layers.rsi) { removeSeries('rsi'); rsiOverlay.current = null }
    // MACD pane
    if (layers.macd && !cs.macd) {
      const pane = layers.rsi ? 2 : 1
      cs.macd_hist = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane)
      cs.macd = chart.addSeries(LineSeries, { color: pal.macd, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, pane)
      cs.macd_signal = chart.addSeries(LineSeries, { color: pal.macdSignal, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, pane)
    } else if (!layers.macd) { removeSeries('macd'); removeSeries('macd_signal'); removeSeries('macd_hist') }
    const ind = candles.indicators
    cs.rsi?.setData((ind.rsi ?? []).map(p => ({ time: ct(p.time), value: p.value })))
    cs.macd?.setData((ind.macd ?? []).map(p => ({ time: ct(p.time), value: p.value })))
    cs.macd_signal?.setData((ind.macd_signal ?? []).map(p => ({ time: ct(p.time), value: p.value })))
    cs.macd_hist?.setData((ind.macd_hist ?? []).map(p => ({ time: ct(p.time), value: p.value, color: alpha(p.value >= 0 ? pal.bull : pal.bear, 0.5) })))
    const panes = chart.panes()
    for (let i = 1; i < panes.length; i++) panes[i].setHeight(110)
  }, [layers.ema, layers.bb, layers.volume, layers.rsi, layers.macd, candles, mapper, palette])

  // ---- overlays, markers, plan price lines
  useEffect(() => {
    const lv = analysis?.levels
    const raw = candles.adjusted === false
    const eng = analysis ? engines(analysis) : null
    const close = eng?.orig.close ?? 0
    // with raw (unadjusted) prices the drawings, computed on adjusted prices, would not line up
    const plans: DrawnPlan[] = []
    if (!raw && layers.plan && eng) {
      const worth = (r: typeof eng.orig | null) => !!r?.plan && (r.plan.actionable || ['BUY', 'HOLD', 'TRIM'].includes(r.verdict))
      const on = (e: 'original' | 'analyst' | 'smc') => planEngine === 'both' || planEngine === e
      if (on('original') && worth(eng.orig)) plans.push({ plan: eng.orig.plan!, tag: 'O', color: palette.plan })
      if (on('analyst') && worth(eng.anly)) plans.push({ plan: eng.anly!.plan!, tag: 'A', color: palette.planAnly })
      if (on('smc') && worth(eng.smc)) plans.push({ plan: eng.smc!.plan!, tag: 'S', color: palette.planSmc })
    }
    const conf = lv?.confluence
    const zoneOn = (k: string) => levelOverrides[k] ?? layers.zones
    const buyZones = raw || !conf ? null : [
      ...(conf.zone1 && zoneOn('zone1') ? [{ label: 'Buy zone 1', low: conf.zone1.low, high: conf.zone1.high, sources: conf.zone1.sources, kind: 'buy' as const }] : []),
      ...(conf.zone2 && zoneOn('zone2') ? [{ label: 'Buy zone 2', low: conf.zone2.low, high: conf.zone2.high, sources: conf.zone2.sources, kind: 'buy' as const }] : []),
      ...conf.sell_zones.slice(0, 3).flatMap((z, i) => (levelOverrides[`sell${i}`] ?? layers.sellz)
        ? [{ label: `Sell zone ${i + 1}`, low: z.low, high: z.high, sources: z.sources, kind: 'sell' as const }] : []),
    ]
    const drawn = raw ? [] : [...new Map([...visible.filter(p => p.category !== 'level'), ...levelPatterns(analysis?.patterns ?? [], layers, levelOverrides),
      ...smcPatterns(analysis?.patterns ?? [], layers, levelOverrides),
      ...visible.filter(p => p.category === 'level' && p.id === highlightId)].map(p => [p.id, p])).values()]
    overlay.current?.set({
      patterns: drawn, highlightId,
      // draw only the 6 zones nearest to price (the full list is in the level ladder)
      zones: !raw && layers.sr && lv ? [...lv.zones].sort((x, y) => Math.abs(x.price - close) - Math.abs(y.price - close)).slice(0, 6) : null,
      pools: !raw && layers.pools ? lv?.pools ?? null : null,
      profile: !raw && layers.vp ? lv?.volume_profile ?? null : null,
      anchored: !raw && (levelOverrides.avp ?? layers.avp) ? lv?.anchored_profile ?? null : null,
      plans, buyZones,
      sessions: !raw && (levelOverrides.sess ?? layers.sess) ? sessionWindows(times, candles.timeframe) : null,
    })
    rsiOverlay.current?.set({ patterns: drawn.filter(p => p.lines.some(l => l.pane === 'rsi')), highlightId })

    // markers (snapped to existing bar times)
    const ms: SeriesMarker<Time>[] = []
    if (layers.events) for (const ev of candles.events ?? []) {
      const idx = mapper.toLogical(ev.time)
      if (idx == null || idx >= times.length) continue
      ms.push({ time: ct(times[idx]), position: 'aboveBar', shape: 'square', color: palette.accent, text: ev.text + (ev.applied ? ' (adj.)' : '') })
    }
    for (const p of drawn) {
      for (const m of p.markers) {
        const idx = mapper.toLogical(m.time)
        if (idx == null || idx >= times.length) continue
        const col = p.direction === 'bullish' ? palette.bull : p.direction === 'bearish' ? palette.bear : palette.neutral
        ms.push({ time: ct(times[idx]), position: m.position, shape: m.shape, color: col, text: m.text, size: p.id === highlightId ? 1.6 : 1, id: p.id })
      }
    }
    ms.sort((a, b) => (typeof a.time === 'number' && typeof b.time === 'number' ? a.time - b.time : String(a.time).localeCompare(String(b.time))))
    markers.current?.setMarkers(ms)

    // plan price lines with axis labels; "O:" = Original, "A:" = Analyst, "S:" = SMC engine
    const candle = s.current.candle
    if (candle) {
      priceLines.current.forEach(pl => candle.removePriceLine(pl))
      priceLines.current = []
      const add = (price: number, color: string, title: string, style = LineStyle.Solid, width = 1) =>
        priceLines.current.push(candle.createPriceLine({ price, color, title, lineStyle: style, lineWidth: width as 1, axisLabelVisible: true }))
      for (const { plan, tag, color } of plans) {
        const style = tag === 'A' ? LineStyle.Dashed : tag === 'S' ? LineStyle.LargeDashed : LineStyle.Solid
        if (plan.trigger) add(plan.trigger, palette.warn, `${tag}: Trigger`, LineStyle.Dashed, 2)
        add(plan.ideal, color, `${tag}: Limit`, style, 2)
        if (plans.length === 1) add(plan.max_chase, palette.warn, `${tag}: Max chase`, LineStyle.Dotted)
        add(plan.stop, palette.bear, `${tag}: Stop ${plan.stop_net_pct.toFixed(1)}%`, style, 2)
        plan.targets.slice(0, plans.length > 1 ? 2 : 3).forEach(t => add(t.price, color, `${tag}: ${t.label} +${t.net_pct.toFixed(1)}%`, LineStyle.Dotted))
      }
      const rv = eng?.orig.position_review ?? eng?.anly?.position_review ?? eng?.smc?.position_review
      if (rv) {
        priceLines.current.push(candle.createPriceLine({ price: rv.entry, color: palette.accent, title: 'Entry', lineStyle: LineStyle.Solid, lineWidth: 1, axisLabelVisible: true }))
        if (rv.suggested_stop) priceLines.current.push(candle.createPriceLine({ price: rv.suggested_stop, color: palette.bear, title: 'Trail stop', lineStyle: LineStyle.Solid, lineWidth: 2, axisLabelVisible: true }))
      }
    }
  }, [visible, highlightId, layers, analysis, mapper, times, palette, candles, planEngine, levelOverrides])

  // ---- live quote updates today's candle (daily) or the current intraday bucket
  const liveBar = useRef<{ time: string; open: number; high: number; low: number; close: number } | null>(null)
  useEffect(() => { liveBar.current = null }, [candles])
  useEffect(() => {
    if (!quote || !candles.bars.length) return
    const last = candles.bars[candles.bars.length - 1]
    if (candles.timeframe === '1D') {
      if (quote.time < last.time) return
      const bar = { time: quote.time, open: quote.open, high: quote.high, low: quote.low, close: quote.close }
      s.current.candle?.update(bar)
      s.current.close?.update({ time: quote.time, value: quote.close })
      if (quote.volume != null) s.current.volume?.update({ time: quote.time, value: quote.volume, color: alpha(quote.close >= quote.open ? palette.bull : palette.bear, 0.35) })
      return
    }
    const step = TF_MIN[candles.timeframe]
    if (!step || !quote.last_trade) return
    // the session bucket the last trade falls in (buckets start at the 09:30 open)
    const mins = +quote.last_trade.slice(0, 2) * 60 + +quote.last_trade.slice(3, 5)
    const rel = Math.max(0, Math.min(SESSION_CLOSE - SESSION_OPEN - 1, mins - SESSION_OPEN))
    const start = SESSION_OPEN + Math.floor(rel / step) * step
    const t = `${quote.time} ${String(Math.floor(start / 60)).padStart(2, '0')}:${String(start % 60).padStart(2, '0')}`
    if (t < last.time) return
    const base = liveBar.current?.time === t ? liveBar.current : t === last.time ? { ...last } : { time: t, open: quote.close, high: quote.close, low: quote.close, close: quote.close }
    const bar = { time: t, open: base.open, high: Math.max(base.high, quote.close), low: Math.min(base.low, quote.close), close: quote.close }
    liveBar.current = bar
    s.current.candle?.update({ ...bar, time: ct(t) })
    s.current.close?.update({ time: ct(t), value: bar.close })
  }, [quote, candles, palette])

  // ---- zoom to a selected pattern
  useEffect(() => {
    if (!focus) return
    const p = patternById.get(focus.id)
    if (!p) return
    const a = mapper.toLogical(p.start), b = mapper.toLogical(p.end)
    if (a == null || b == null) return
    const pad = Math.max(10, (b - a) * 0.4)
    chartRef.current?.timeScale().setVisibleLogicalRange({ from: (a - pad) as Logical, to: (b + pad + 6) as Logical })
  }, [focus, mapper, patternById])

  const hp = hover ? patternById.get(hover.id) : null
  const shown = legend ?? (candles.bars.length ? (() => { const b = candles.bars[candles.bars.length - 1]; return { time: b.time, o: b.open, h: b.high, l: b.low, c: b.close, est: b.est } })() : null)
  const est = shown ? barByTime.get(shown.time)?.est : false

  return (
    <div className="relative h-full w-full">
      <div ref={box} className="absolute inset-0" />
      {shown && (
        <div className="pointer-events-none absolute left-2 top-1.5 z-10 flex flex-wrap gap-x-3 text-[11px] text-muted num">
          <span>{shown.time}</span>
          <span>O <b className="text-fg">{fmt(shown.o)}</b>{est ? '*' : ''}</span>
          <span>H <b className="text-fg">{fmt(shown.h)}</b></span>
          <span>L <b className="text-fg">{fmt(shown.l)}</b></span>
          <span>C <b className="text-fg">{fmt(shown.c)}</b></span>
          {est && <span title="CSE history doesn't include opening prices; open ≈ previous close">* open estimated</span>}
        </div>
      )}
      {hp && hover && (
        <div className="pointer-events-none absolute z-20 w-72 border border-amber/60 bg-black p-2.5 text-xs shadow-xl"
             style={{ left: Math.min(hover.x + 14, (box.current?.clientWidth ?? 600) - 300), top: Math.max(4, hover.y - 10) }}>
          <div className="mb-1 flex items-center justify-between gap-2">
            <b className={hp.direction === 'bullish' ? 'text-bull' : hp.direction === 'bearish' ? 'text-bear' : 'text-neutral'}>{hp.name}</b>
            <span className="chip bg-panel-2 text-muted">{hp.status} · {hp.priority.toFixed(0)}</span>
          </div>
          <div className="mb-1 text-muted num">
            {Object.entries(hp.levels).map(([k, v]) => <span key={k} className="mr-2">{k}: <b className="text-fg">{fmt(v)}</b></span>)}
          </div>
          <div className="leading-snug text-fg/90">{hp.explanation.length > 260 ? hp.explanation.slice(0, 260) + '…' : hp.explanation}</div>
          <div className="mt-1 text-[10px] text-muted">Click to select</div>
        </div>
      )}
    </div>
  )
}
