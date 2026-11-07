// Custom Lightweight Charts primitive that draws detected patterns, S/R zones,
// liquidity pools, volume profile and the trade-plan buy zone straight onto the chart.
// It renders any pattern purely from its generic geometry (lines / zones / anchors),
// so new backend detectors need no frontend changes.
import type {
  IChartApiBase, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, Logical,
  PrimitiveHoveredItem, SeriesAttachedParameter, SeriesType, Time,
} from 'lightweight-charts'
import type { Pattern, Plan, Pool, VolumeProfile, Zone } from '../api'
import { SMC_ORDER, smcZoneStyle, type SmcZoneStyle } from './smcStyle'

type Target = Parameters<IPrimitivePaneRenderer['draw']>[0]
type Ctx = CanvasRenderingContext2D

export interface Palette {
  bull: string; bear: string; neutral: string; support: string; resistance: string; plan: string; planAnly: string; planSmc: string
  warn: string; text: string; muted: string; bg: string; accent: string; blue: string; fib: string; pocket: string
  ema21: string; ema50: string; ema200: string; rsi: string; macd: string; macdSignal: string; grid: string
}

export function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement)
  const v = (n: string, fb = '#888888') => cs.getPropertyValue(n).trim() || fb
  return {
    bull: v('--bull'), bear: v('--bear'), neutral: v('--neutral'), support: v('--support'), resistance: v('--resistance'),
    plan: v('--plan'), planAnly: v('--plan-anly'), planSmc: v('--plan-smc'), warn: v('--warn'), text: v('--text'), muted: v('--muted'), bg: v('--bg', '#000000'),
    accent: v('--amber'), blue: v('--blue', '#5aa9ff'), fib: v('--fib'), pocket: v('--fib'),
    ema21: v('--ema21'), ema50: v('--ema50'), ema200: v('--ema200'), rsi: v('--rsi'), macd: v('--macd'), macdSignal: v('--macd-signal'),
    grid: v('--chart-grid', '#141414'),
  }
}

export const CHART_FONT = '"IBM Plex Mono", ui-monospace, Consolas, monospace'

/** A trade plan drawn on the chart, tagged with the engine that produced it. */
export interface DrawnPlan { plan: Plan; tag: 'C' | 'L' | 'S'; color: string }
const PLAN_DASH: Record<DrawnPlan['tag'], number[]> = { C: [], L: [5, 3], S: [10, 4] }

/** Maps bar labels to logical bar indexes, snapping to the bar on/before and projecting future times.
 *  Daily labels are YYYY-MM-DD (projected on weekdays); intraday labels are 'YYYY-MM-DD HH:MM' in Colombo
 *  time (projected in session bar steps, skipping nights and weekends). Both sort as plain strings. */
export class TimeMapper {
  private times: string[]
  private index = new Map<string, number>()
  private step: number
  constructor(times: string[]) {
    this.times = times
    times.forEach((t, i) => this.index.set(t, i))
    this.step = barStep(times)
  }
  toLogical(t: string): number | null {
    const n = this.times.length
    if (!n) return null
    const hit = this.index.get(t)
    if (hit !== undefined) return hit
    if (t < this.times[0]) return 0
    const last = this.times[n - 1]
    if (t > last) return n - 1 + (last.length > 10 ? sessionBarsBetween(last, t, this.step) : weekdaysBetween(last, t))
    let lo = 0, hi = n - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (this.times[mid] <= t) lo = mid; else hi = mid - 1
    }
    return lo
  }
}

// CSE session (Colombo): 09:30-14:30
export const SESSION_OPEN = 9 * 60 + 30, SESSION_CLOSE = 14 * 60 + 30
const toMin = (t: string) => +t.slice(11, 13) * 60 + +t.slice(14, 16)

/** Bar size (minutes) of an intraday series: the smallest gap between bars of the same day. */
function barStep(times: string[]): number {
  if (!times.length || times[0].length <= 10) return 0
  let best = Infinity
  for (let i = Math.max(1, times.length - 300); i < times.length; i++) {
    const a = times[i - 1], b = times[i]
    if (a.slice(0, 10) === b.slice(0, 10)) best = Math.min(best, toMin(b) - toMin(a))
  }
  return Number.isFinite(best) && best > 0 ? best : 240
}

/** Number of session bars from intraday label a to b (b later), skipping nights and weekends. */
function sessionBarsBetween(a: string, b: string, step: number): number {
  if (!step) return 0
  if (b.length <= 10) b = b + ' 09:30'
  const perDay = Math.ceil((SESSION_CLOSE - SESSION_OPEN) / step)
  const slot = (t: string) => Math.max(0, Math.min(perDay - 1, Math.floor((toMin(t) - SESSION_OPEN) / step)))
  const da = a.slice(0, 10), db = b.slice(0, 10)
  if (da === db) return Math.max(0, slot(b) - slot(a))
  const days = weekdaysBetween(da, db)
  return (perDay - 1 - slot(a)) + (days - 1) * perDay + slot(b) + 1
}

function weekdaysBetween(a: string, b: string): number {
  const d = new Date(a + 'T00:00:00Z'), end = new Date(b + 'T00:00:00Z')
  let n = 0
  while (d < end) {
    d.setUTCDate(d.getUTCDate() + 1)
    const wd = d.getUTCDay()
    if (wd !== 0 && wd !== 6) n++
  }
  return n
}

export interface OverlayState {
  pane: 'main' | 'rsi'
  patterns: Pattern[]
  highlightId: string | null
  hoverId: string | null
  zones: Zone[] | null
  pools: Pool[] | null
  profile: VolumeProfile | null
  plans: DrawnPlan[]
  buyZones?: { label: string; low: number; high: number; sources?: string[]; kind?: 'buy' | 'sell' }[] | null
  anchored?: (VolumeProfile & { anchor?: string }) | null
  /** CSE session windows (opening / closing hour) shaded on intraday charts */
  sessions?: { t1: string; t2: string; label: string }[] | null
}

interface Hit { id: string; kind: 'seg' | 'rect' | 'pt'; a: number; b: number; c: number; d: number }
/** An SMC zone label, placed by the bottom pass and drawn by the top pass (above the candles). */
interface SmcLabel { text: string; short: string; x: number; y: number; color: string; st: SmcZoneStyle; top: number; bottom: number }

const alpha = (color: string, a: number) => {
  if (color.startsWith('#') && color.length === 7) {
    const n = parseInt(color.slice(1), 16)
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
  }
  return color
}

export class PatternOverlay implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null
  private series: ISeriesApi<SeriesType, Time> | null = null
  private requestUpdate: (() => void) | null = null
  private hits: Hit[] = []
  private smcLabels: SmcLabel[] = []
  private views: IPrimitivePaneView[]
  state: OverlayState
  palette: Palette
  mapper: TimeMapper

  constructor(state: OverlayState, palette: Palette, mapper: TimeMapper) {
    this.state = state
    this.palette = palette
    this.mapper = mapper
    const self = this
    this.views = [
      { zOrder: () => 'bottom', renderer: () => ({ draw: (t: Target) => self.drawBottom(t) }) },
      { zOrder: () => 'top', renderer: () => ({ draw: (t: Target) => self.drawTop(t) }) },
    ]
  }

  attached(p: SeriesAttachedParameter<Time>) {
    this.chart = p.chart
    this.series = p.series
    this.requestUpdate = p.requestUpdate
  }
  detached() { this.chart = null; this.series = null; this.requestUpdate = null }
  paneViews() { return this.views }
  updateAllViews() { /* geometry is computed at draw time */ }

  set(partial: Partial<OverlayState>, mapper?: TimeMapper, palette?: Palette) {
    this.state = { ...this.state, ...partial }
    if (mapper) this.mapper = mapper
    if (palette) this.palette = palette
    this.requestUpdate?.()
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    let best: { id: string; dist: number; prio: number } | null = null
    for (const h of this.hits) {
      let dist = Infinity, prio = 1
      if (h.kind === 'seg') dist = segDist(x, y, h.a, h.b, h.c, h.d)
      else if (h.kind === 'pt') { dist = Math.hypot(x - h.a, y - h.b) - 3; prio = 2 }
      else if (x >= h.a && x <= h.c && y >= h.b && y <= h.d) { dist = 0; prio = 0 }
      if (dist <= 6 && (!best || prio > best.prio || (prio === best.prio && dist < best.dist))) best = { id: h.id, dist, prio }
    }
    return best ? { externalId: best.id, zOrder: 'top', cursorStyle: 'pointer', distance: best.dist, hitTestPriority: best.prio } : null
  }

  // ------------------------------------------------------------------ coordinates
  private x(t: string): number | null {
    const l = this.mapper.toLogical(t)
    if (l == null || !this.chart) return null
    return this.chart.timeScale().logicalToCoordinate(l as Logical)
  }
  private y(p: number): number | null { return this.series ? this.series.priceToCoordinate(p) : null }
  private halfBar(): number {
    const sp = this.chart?.timeScale().options().barSpacing ?? 6
    return Math.max(2, sp / 2)
  }
  private color(p: Pattern) {
    return p.direction === 'bullish' ? this.palette.bull : p.direction === 'bearish' ? this.palette.bear : this.palette.neutral
  }
  private dim(p: Pattern) {
    const h = this.state.highlightId ?? this.state.hoverId
    return h != null && h !== p.id
  }

  // ------------------------------------------------------------------ background layer
  private drawBottom(target: Target) {
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      if (this.state.pane !== 'main') return
      const W = mediaSize.width
      const pal = this.palette
      // CSE session windows (the kill-zone adaptation): faint full-height bands, shown only
      if (this.state.sessions?.length) {
        const hb = this.halfBar()
        let lastLabelX = -Infinity
        for (const w of this.state.sessions) {
          const x1 = this.x(w.t1), x2 = this.x(w.t2)
          if (x1 == null || x2 == null || x2 + hb < 0 || x1 - hb > W) continue
          ctx.fillStyle = alpha(pal.accent, 0.055)
          ctx.fillRect(x1 - hb, 0, x2 - x1 + 2 * hb, mediaSize.height)
          if (x1 - lastLabelX > 90 && x1 > 0) {
            ctx.font = `10px ${CHART_FONT}`
            ctx.fillStyle = alpha(pal.accent, 0.55)
            ctx.textAlign = 'left'
            ctx.fillText(w.label, x1 - hb + 2, mediaSize.height - 4)
            lastLabelX = x1
          }
        }
      }
      // S/R zones (full-width bands)
      for (const z of this.state.zones ?? []) {
        const y1 = this.y(z.high), y2 = this.y(z.low)
        if (y1 == null || y2 == null) continue
        const col = z.kind === 'support' ? pal.support : pal.resistance
        const h = Math.max(2, y2 - y1)
        ctx.fillStyle = alpha(col, 0.07 + 0.025 * Math.min(z.touches, 6))
        ctx.fillRect(0, y1, W, h)
        ctx.strokeStyle = alpha(col, 0.45)
        ctx.setLineDash([4, 4]); ctx.lineWidth = 1
        ctx.beginPath(); ctx.moveTo(0, y1); ctx.lineTo(W, y1); ctx.moveTo(0, y1 + h); ctx.lineTo(W, y1 + h); ctx.stroke()
        ctx.setLineDash([])
        label(ctx, `${z.kind === 'support' ? 'S' : 'R'} ${z.low.toFixed(2)}–${z.high.toFixed(2)} ×${z.touches}`, 6, y1 + h / 2, col, pal.bg, 'left')
      }
      // trade-plan buy zones, one strip per engine (C = Classic, L = Levels, S = SMC)
      const np = this.state.plans.length
      this.state.plans.forEach((dp, i) => {
        const plan = dp.plan
        const y1 = this.y(plan.buy_zone.high), y2 = this.y(plan.buy_zone.low)
        if (y1 == null || y2 == null) return
        const x0 = (W * i) / np, x1 = (W * (i + 1)) / np
        ctx.fillStyle = alpha(dp.color, 0.12)
        ctx.fillRect(x0, y1, x1 - x0, Math.max(3, y2 - y1))
        ctx.strokeStyle = alpha(dp.color, 0.6); ctx.lineWidth = 1
        ctx.setLineDash(PLAN_DASH[dp.tag]); ctx.strokeRect(x0, y1, x1 - x0, Math.max(3, y2 - y1)); ctx.setLineDash([])
        label(ctx, `${dp.tag}: ${plan.entry_label ? plan.entry_label.toUpperCase() + ' ' : ''}BUY ZONE${plan.trigger ? ' (after trigger)' : ''}`,
              x0 + (x1 - x0) * 0.3, y1 - 8, dp.color, pal.bg, 'left')
      })
      // volume profile on the right edge
      const vp = this.state.profile
      if (vp && vp.bins.length) {
        const maxShare = Math.max(...vp.bins.map(b => b.share)) || 1
        const width = Math.min(160, W * 0.16)
        for (const b of vp.bins) {
          const y1 = this.y(b.high), y2 = this.y(b.low)
          if (y1 == null || y2 == null) continue
          const w = (b.share / maxShare) * width
          ctx.fillStyle = b.in_value_area ? alpha(pal.accent, 0.28) : alpha(pal.muted, 0.16)
          ctx.fillRect(W - w, y1 + 0.5, w, Math.max(1, y2 - y1 - 1))
        }
        if (vp.poc != null) {
          const yp = this.y(vp.poc)
          if (yp != null) {
            ctx.strokeStyle = alpha(pal.accent, 0.9); ctx.lineWidth = 1.5
            ctx.beginPath(); ctx.moveTo(W - width, yp); ctx.lineTo(W, yp); ctx.stroke()
            label(ctx, `POC ${vp.poc.toFixed(2)}`, W - width - 4, yp, pal.accent, pal.bg, 'right')
          }
        }
      }
      // analyst-style buy zones (grey boxes) and sell zones (resistance tint), on the right, labelled
      for (const bz of this.state.buyZones ?? []) {
        const y1 = this.y(bz.high), y2 = this.y(bz.low)
        if (y1 == null || y2 == null) continue
        const x0 = W * 0.62, h = Math.max(4, y2 - y1)
        const col = bz.kind === 'sell' ? pal.resistance : pal.muted
        ctx.fillStyle = alpha(col, bz.kind === 'sell' ? 0.16 : 0.22)
        ctx.fillRect(x0, y1, W - x0, h)
        ctx.strokeStyle = alpha(col, 0.7); ctx.lineWidth = 1
        ctx.strokeRect(x0, y1, W - x0, h)
        label(ctx, `${bz.label} ${bz.low.toFixed(2)}–${bz.high.toFixed(2)}`, x0 + 4, y1 + h / 2, bz.kind === 'sell' ? pal.resistance : pal.text, pal.bg, 'left', 10, true)
      }
      // anchored volume profile: POC and value area from the start of the dominant leg
      const ap = this.state.anchored
      if (ap && ap.poc != null) {
        const x0 = ap.anchor ? this.x(ap.anchor) ?? 0 : 0
        const rows: [number | null, string, number[]][] = [[ap.poc, 'aPOC', []], [ap.va_high, 'VAH', [2, 3]], [ap.va_low, 'VAL', [2, 3]]]
        for (const [price, name, dash] of rows) {
          if (price == null) continue
          const y = this.y(price)
          if (y == null) continue
          ctx.strokeStyle = alpha(pal.accent, name === 'aPOC' ? 0.9 : 0.6); ctx.lineWidth = name === 'aPOC' ? 1.5 : 1
          ctx.setLineDash(dash); ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(W, y); ctx.stroke(); ctx.setLineDash([])
          label(ctx, `${name} ${price.toFixed(2)}`, x0 + 4, y - 8, pal.accent, pal.bg, 'left', 10)
        }
      }
      // pattern zones (candle boxes, squeeze areas, Fibonacci golden pocket); SMC zones are collected and painted
      // after, biggest first, each type in its own texture
      const hb = this.halfBar()
      const smc: { p: Pattern; st: SmcZoneStyle; x1: number; xe: number; y1: number; h: number; low: number; high: number }[] = []
      for (const p of this.state.patterns) {
        for (const z of p.zones) {
          const x1 = this.x(z.t1), x2 = this.x(z.t2), y1 = this.y(z.high), y2 = this.y(z.low)
          if (x1 == null || x2 == null || y1 == null || y2 == null) continue
          if (z.role === 'golden_pocket') {
            ctx.fillStyle = alpha(pal.pocket, this.dim(p) ? 0.04 : 0.10)
            ctx.fillRect(x1, y1, Math.max(0, Math.min(W, x2) - x1), Math.max(1, y2 - y1))
            continue
          }
          const col = this.color(p)
          const st = smcZoneStyle(z.role, p.context, p.direction)
          if (st) {
            smc.push({ p, st, x1, xe: Math.min(W, x2), y1, h: Math.max(2, y2 - y1), low: z.low, high: z.high })
            continue
          }
          const a = this.dim(p) ? 0.04 : z.role === 'candle_box' ? 0.10 : 0.12
          ctx.fillStyle = alpha(col, a)
          ctx.fillRect(x1 - hb, y1 - 2, x2 - x1 + 2 * hb, y2 - y1 + 4)
          ctx.strokeStyle = alpha(col, this.dim(p) ? 0.2 : 0.7)
          ctx.lineWidth = p.id === this.state.highlightId ? 2 : 1
          ctx.strokeRect(x1 - hb, y1 - 2, x2 - x1 + 2 * hb, y2 - y1 + 4)
        }
      }
      const H = mediaSize.height
      const labels: SmcLabel[] = []
      smc.sort((a, b) => SMC_ORDER[a.st.kind] - SMC_ORDER[b.st.kind] || b.h * (b.xe - b.x1) - a.h * (a.xe - a.x1))
      for (const s of smc) {
        const { p, st, x1, xe, y1, h } = s
        const w = Math.max(0, xe - x1)
        if (w <= 0) continue
        const c = st.color === 'accent' ? pal.accent : this.color(p)
        const dim = this.dim(p)
        const k = dim ? 0.25 : st.strength
        const line = dim ? 0.2 : st.strength === 1 ? 0.85 : 0.55
        ctx.lineWidth = p.id === this.state.highlightId ? 2 : 1
        if (st.fill) { ctx.fillStyle = alpha(c, st.fill * k); ctx.fillRect(x1, y1, w, h) }
        if (st.hatch) hatch(ctx, x1, y1, w, h, alpha(c, 0.4 * k), 6)
        ctx.strokeStyle = alpha(c, line)
        if (st.border === 'solid') {
          ctx.setLineDash(st.dash); ctx.strokeRect(x1 + 0.5, y1 + 0.5, w - 1, h - 1)
        } else if (st.border === 'double') {
          ctx.setLineDash(st.dash); ctx.strokeRect(x1 + 0.5, y1 + 0.5, w - 1, h - 1)
          ctx.setLineDash([])
          if (w > 8 && h > 8) { ctx.strokeStyle = alpha(c, line * 0.7); ctx.strokeRect(x1 + 3.5, y1 + 3.5, w - 7, h - 7) }
        } else {
          ctx.setLineDash(st.dash)
          ctx.beginPath(); ctx.moveTo(x1, y1 + 0.5); ctx.lineTo(xe, y1 + 0.5); ctx.moveTo(x1, y1 + h - 0.5); ctx.lineTo(xe, y1 + h - 0.5); ctx.stroke()
        }
        ctx.setLineDash([])
        if (st.edgeBar && x1 >= 0) { ctx.fillStyle = alpha(c, dim ? 0.3 : 0.9); ctx.fillRect(x1, y1, 3, h) }
        // label at the right end of the visible box, vertically centred on its visible part
        const top = Math.max(0, y1), bottom = Math.min(H, y1 + h)
        if (!dim && bottom - top > 0 && xe > 40) {
          // narrow charts (phones): no price range, or the labels would cover the latest candles
          const range = W < 640 ? '' : ` ${s.low.toFixed(2)}–${s.high.toFixed(2)}`
          labels.push({ text: `${st.tag}${range}${st.statusText ? ' · ' + st.statusText : ''}`, short: st.tag, x: xe - 4, y: (top + bottom) / 2,
                        color: c, st, top, bottom })
        }
      }
      this.smcLabels = labels
    })
  }

  // ------------------------------------------------------------------ foreground layer
  private drawTop(target: Target) {
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const hits: Hit[] = []
      const pal = this.palette
      const W = mediaSize.width
      const hb = this.halfBar()
      if (this.state.pane === 'main') {
        // liquidity pools
        for (const pl of this.state.pools ?? []) {
          const y = this.y(pl.price)
          if (y == null) continue
          const x0 = pl.time ? this.x(pl.time) ?? 0 : 0
          ctx.strokeStyle = alpha(pl.kind === 'round_number' ? pal.muted : pal.warn, 0.7)
          ctx.setLineDash([1, 3]); ctx.lineWidth = 1
          ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(W, y); ctx.stroke(); ctx.setLineDash([])
          const txt = pl.kind === 'round_number' ? `${pl.price}` : pl.kind === 'buy_stops_above' ? 'liq ▲' : 'liq ▼'
          label(ctx, txt, W - 4, y - 7, pl.kind === 'round_number' ? pal.muted : pal.warn, pal.bg, 'right', 10)
        }
      }
      // pattern lines, anchors and target labels (highlighted pattern last so it sits on top)
      const ordered = [...this.state.patterns].sort((a, b) =>
        Number(a.id === this.state.highlightId || a.id === this.state.hoverId) - Number(b.id === this.state.highlightId || b.id === this.state.hoverId))
      for (const p of ordered) {
        const col = this.color(p)
        const dim = this.dim(p)
        const strong = p.id === this.state.highlightId || p.id === this.state.hoverId
        const forming = p.status === 'forming'
        const lines = p.lines.filter(l => (l.pane ?? 'main') === this.state.pane)
        for (const ln of lines) {
          const x1 = this.x(ln.from.time), y1 = this.y(ln.from.price), x2 = this.x(ln.to.time), y2 = this.y(ln.to.price)
          if (x1 == null || y1 == null || x2 == null || y2 == null) continue
          const isFib = ln.role === 'fib'
          const smcLine = ln.role === 'bos' || ln.role === 'eq' || ln.role === 'range' || ln.role === 'liquidity'
          if (smcLine) {
            const lc = ln.role === 'bos' ? (ln.dir === 'up' ? pal.bull : pal.bear)
              : ln.role === 'eq' ? pal.accent : ln.role === 'liquidity' ? pal.warn : pal.muted
            ctx.strokeStyle = alpha(lc, dim ? 0.2 : 0.85)
            ctx.lineWidth = strong ? 2 : ln.role === 'eq' ? 1.5 : 1
            ctx.setLineDash(ln.role === 'bos' ? [5, 3] : ln.role === 'eq' ? [6, 3] : ln.role === 'liquidity' ? [1, 3] : [])
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.setLineDash([])
            hits.push({ id: p.id, kind: 'seg', a: x1, b: y1, c: x2, d: y2 })
            if (!dim && ln.label) {
              if (ln.role === 'bos') label(ctx, ln.label, (x1 + x2) / 2, y1 - 8, lc, pal.bg, 'center', 10)
              else label(ctx, ln.label, Math.min(x2, W - 8), y1 - 8, lc, pal.bg, 'right', 10)
            }
            continue
          }
          const roleCol = ln.role === 'invalidation' ? pal.bear : ln.role === 'target_projection' ? pal.plan : isFib ? pal.fib : ln.role === 'trendline' ? pal.text : col
          ctx.strokeStyle = alpha(roleCol, dim ? 0.22 : isFib ? 0.75 : forming ? 0.75 : 0.95)
          ctx.lineWidth = strong ? 2.6 : isFib ? 1 : ln.role === 'outline' ? 1.2 : 1.7
          ctx.setLineDash(isFib ? (ln.style === 'dashed' ? [3, 3] : []) : ln.style === 'dashed' || (forming && ln.role !== 'trendline') ? [6, 4] : ln.style === 'dotted' ? [2, 3] : [])
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
          ctx.setLineDash([])
          hits.push({ id: p.id, kind: 'seg', a: x1, b: y1, c: x2, d: y2 })
          if (dim) continue
          if (ln.role === 'target_projection') {
            arrowHead(ctx, x1, y1, x2, y2, roleCol)
            label(ctx, `Target ${ln.to.price.toFixed(2)}`, x2 + 4, y2, roleCol, pal.bg, 'left')
          } else if (ln.role === 'invalidation') {
            label(ctx, `Invalid ${ln.to.price.toFixed(2)}`, x2 + 4, y2, pal.bear, pal.bg, 'left', 10)
          } else if (ln.role === 'neckline' || ln.role === 'swept_level') {
            label(ctx, ln.role === 'neckline' ? 'Neckline' : 'Swept', x2 + 4, y2, col, pal.bg, 'left', 10)
          } else if (ln.label && (isFib || ln.role === 'trendline')) {
            const lx = Math.min(Math.max(x1, x2), W - 8)
            label(ctx, ln.label, lx, isFib ? y2 : y2 - 8, isFib ? pal.fib : pal.text, pal.bg, 'right', 10)
          }
        }
        if (this.state.pane !== 'main') continue
        // anchors
        for (const a of p.anchors) {
          const x = this.x(a.time), y = this.y(a.price)
          if (x == null || y == null) continue
          if (p.category === 'candlestick') continue // markers already label candles
          if (p.type === 'fibonacci' && !strong) continue
          ctx.fillStyle = alpha(col, dim ? 0.25 : 1)
          ctx.beginPath(); ctx.arc(x, y, strong ? 4 : 3, 0, Math.PI * 2); ctx.fill()
          hits.push({ id: p.id, kind: 'pt', a: x, b: y, c: 0, d: 0 })
          const below = a.label != null && (a.label.endsWith('L') || a.label.startsWith('B'))
          if (a.label && !dim) label(ctx, a.label, x, y + (below ? 12 : -12), p.type === 'swing_structure' ? pal.text : col, pal.bg, 'center', 10)
        }
        // pattern title near its start
        if (p.lines.length && !dim && p.category !== 'level' && p.category !== 'smc' && (strong || p.priority_tier === 'high')) {
          const first = p.lines[0]
          const x = this.x(first.from.time), y = this.y(Math.max(first.from.price, first.to.price))
          if (x != null && y != null) label(ctx, `${p.name}${forming ? ' (forming)' : p.status === 'failed' ? ' (failed)' : ''}`, x, y - 16, col, pal.bg, 'left', 11, true)
        }
        // candle zones are hit-testable too
        for (const z of p.zones) {
          const x1 = this.x(z.t1), x2 = this.x(z.t2), y1 = this.y(z.high), y2 = this.y(z.low)
          if (x1 != null && x2 != null && y1 != null && y2 != null) hits.push({ id: p.id, kind: 'rect', a: x1 - hb, b: y1 - 2, c: x2 + hb, d: y2 + 2 })
        }
      }
      if (this.state.pane === 'main') this.drawSmcLabels(ctx, mediaSize.height)
      this.hits = hits
    })
  }

  /** SMC zone labels at the right end of each box, as chips in the zone's badge style. A label that would overlap one
   *  already drawn moves a line down or up; if there is no room it shrinks to its tag (OB ↓, FVG ↑ ...). */
  private drawSmcLabels(ctx: Ctx, H: number) {
    const size = 10, hgt = size + 6
    const placed: { l: number; r: number; t: number; b: number }[] = []
    for (const lb of this.smcLabels) {
      for (const text of [lb.text, lb.short]) {
        const w = chipWidth(ctx, text, lb.st.badge, size)
        const l = lb.x - w, r = lb.x
        let at: number | null = null
        for (const dy of [0, 13, -13, 26, -26]) {
          const y = Math.max(hgt / 2, Math.min(H - hgt / 2, lb.y + dy))
          if (!placed.some(p => l < p.r && r > p.l && y - hgt / 2 < p.b && y + hgt / 2 > p.t)) { at = y; break }
        }
        if (at != null) {
          chip(ctx, text, lb.x, at, lb.color, this.palette.bg, lb.st.badge, size)
          placed.push({ l, r, t: at - hgt / 2, b: at + hgt / 2 })
          break
        }
      }
    }
  }
}

/** '/' diagonal hatch inside a rectangle (fair value gaps: a texture, so they read as gaps even in grayscale). */
function hatch(ctx: Ctx, x: number, y: number, w: number, h: number, color: string, gap: number) {
  ctx.save()
  ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip()
  ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash([])
  ctx.beginPath()
  for (let s = -h; s < w; s += gap) { ctx.moveTo(x + s, y + h); ctx.lineTo(x + s + h, y) }
  ctx.stroke()
  ctx.restore()
}

function chipWidth(ctx: Ctx, text: string, badge: SmcZoneStyle['badge'], size: number) {
  ctx.font = `600 ${size}px ${CHART_FONT}`
  return ctx.measureText(text).width + 6 + (badge === 'hatch' ? 12 : 0)
}

/** A label chip, right-aligned at x: filled (order block), outlined (weekly), with a hatch swatch (FVG), dotted (OTE). */
function chip(ctx: Ctx, text: string, xRight: number, y: number, color: string, bg: string, badge: SmcZoneStyle['badge'], size = 10) {
  const w = chipWidth(ctx, text, badge, size), h = size + 6, left = xRight - w, top = y - h / 2
  if (badge === 'filled') {
    ctx.fillStyle = alpha(color, 0.92); ctx.fillRect(left, top, w, h)
  } else {
    ctx.fillStyle = alpha(bg.startsWith('#') ? bg : '#000000', 0.88); ctx.fillRect(left, top, w, h)
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(badge === 'dotted' ? [1, 2] : [])
    ctx.strokeRect(left + 0.5, top + 0.5, w - 1, h - 1); ctx.setLineDash([])
    if (badge === 'hatch') hatch(ctx, left + 3, top + 3, 9, h - 6, color, 3)
  }
  ctx.fillStyle = badge === 'filled' ? '#000000' : color
  ctx.textBaseline = 'middle'
  ctx.fillText(text, left + 3 + (badge === 'hatch' ? 12 : 0), y + 0.5)
}

function segDist(px: number, py: number, x1: number, y1: number, x2: number, y2: number) {
  const dx = x2 - x1, dy = y2 - y1
  const len = dx * dx + dy * dy
  const t = len ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len)) : 0
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
}

function arrowHead(ctx: Ctx, x1: number, y1: number, x2: number, y2: number, color: string) {
  const ang = Math.atan2(y2 - y1, x2 - x1), s = 7
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.moveTo(x2, y2)
  ctx.lineTo(x2 - s * Math.cos(ang - 0.45), y2 - s * Math.sin(ang - 0.45))
  ctx.lineTo(x2 - s * Math.cos(ang + 0.45), y2 - s * Math.sin(ang + 0.45))
  ctx.closePath(); ctx.fill()
}

function label(ctx: Ctx, text: string, x: number, y: number, color: string, bg: string,
               align: 'left' | 'right' | 'center' = 'left', size = 11, bold = false) {
  ctx.font = `${bold ? 600 : 500} ${size}px ${CHART_FONT}`
  const w = ctx.measureText(text).width
  const pad = 3
  const left = align === 'left' ? x : align === 'right' ? x - w - 2 * pad : x - w / 2 - pad
  ctx.fillStyle = alpha(bg.startsWith('#') ? bg : '#000000', 0.85)
  ctx.fillRect(left, y - size / 2 - 3, w + 2 * pad, size + 6)
  ctx.fillStyle = color
  ctx.textBaseline = 'middle'
  ctx.fillText(text, left + pad, y + 0.5)
}
