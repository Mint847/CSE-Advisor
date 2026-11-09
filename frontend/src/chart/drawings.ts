// Your own drawings on the chart: horizontal lines, trendlines, rectangles and text notes. They are kept per stock
// (UserPref `draw:<symbol>`) with each point as a bar label + price, so the same drawing lands on the right bar in
// every timeframe (a daily point snaps to that day's first intraday bar and vice versa).
import type {
  IChartApiBase, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, Logical,
  PrimitiveHoveredItem, SeriesAttachedParameter, SeriesType, Time,
} from 'lightweight-charts'
import { CHART_FONT, TimeMapper } from './overlay'

import { hitTest, type Drawing, type DrawPoint, type Hit, type Placed, type Px } from './drawGeom'
export * from './drawGeom'

type Target = Parameters<IPrimitivePaneRenderer['draw']>[0]

// ---------------------------------------------------------------- the chart primitive
interface State { drawings: Drawing[]; draft: Drawing | null; selected: string | null; hover: string | null }

export class DrawingsOverlay implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null
  private series: ISeriesApi<SeriesType, Time> | null = null
  private requestUpdate: (() => void) | null = null
  private views: IPrimitivePaneView[]
  placed: Placed[] = []
  state: State = { drawings: [], draft: null, selected: null, hover: null }
  mapper: TimeMapper

  constructor(mapper: TimeMapper) {
    this.mapper = mapper
    const renderer: IPrimitivePaneRenderer = { draw: (t: Target) => this.draw(t) }
    this.views = [{ renderer: () => renderer, zOrder: () => 'top' }]
  }
  attached(p: SeriesAttachedParameter<Time>) { this.chart = p.chart; this.series = p.series; this.requestUpdate = p.requestUpdate }
  detached() { this.chart = null; this.series = null; this.requestUpdate = null }
  paneViews() { return this.views }
  set(partial: Partial<State>, mapper?: TimeMapper) {
    this.state = { ...this.state, ...partial }
    if (mapper) this.mapper = mapper
    this.requestUpdate?.()
  }
  /** Pixel position of a point (null when it can't be placed, e.g. no bars yet). */
  toPx(p: DrawPoint): Px | null {
    const i = this.mapper.toLogical(p.time)
    if (i == null || !this.chart || !this.series) return null
    const x = this.chart.timeScale().logicalToCoordinate(i as Logical)
    const y = this.series.priceToCoordinate(p.price)
    return x == null || y == null ? null : { x, y }
  }
  hit(x: number, y: number): Hit | null { return hitTest(this.placed, { x, y }) }
  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    const h = this.hit(x, y)
    return h ? { externalId: `draw:${h.id}`, zOrder: 'top', cursorStyle: h.part === 'body' ? 'move' : 'grab' } : null
  }

  private draw(target: Target) {
    const list = [...this.state.drawings, ...(this.state.draft ? [this.state.draft] : [])]
    const placed: Placed[] = []
    target.useMediaCoordinateSpace(({ context: c, mediaSize }) => {
      c.save()
      c.font = `11px ${CHART_FONT}`
      for (const d of list) {
        const a = this.toPx(d.p1)
        if (!a) continue
        const b = d.p2 ? this.toPx(d.p2) : null
        const sel = d.id === this.state.selected, hov = d.id === this.state.hover
        const w = d.kind === 'text' ? c.measureText(d.text ?? '').width : 0
        placed.push({ d, a, b, w })
        c.strokeStyle = d.color
        c.fillStyle = d.color
        c.lineWidth = sel || hov ? 2 : 1.25
        c.setLineDash(d === this.state.draft ? [4, 3] : [])
        if (d.kind === 'hline') {
          c.beginPath(); c.moveTo(0, a.y); c.lineTo(mediaSize.width, a.y); c.stroke()
          const label = String(d.p1.price)
          const lw = c.measureText(label).width + 8
          c.fillRect(mediaSize.width - lw - 2, a.y - 8, lw, 16)
          c.fillStyle = '#000'
          c.fillText(label, mediaSize.width - lw + 2, a.y + 4)
        } else if (d.kind === 'trend' && b) {
          c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke()
        } else if (d.kind === 'rect' && b) {
          c.globalAlpha = 0.12
          c.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y))
          c.globalAlpha = 1
          c.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y))
        } else if (d.kind === 'text') {
          c.globalAlpha = 0.85
          c.fillStyle = '#000'
          c.fillRect(a.x - 3, a.y - 14, w + 6, 17)
          c.globalAlpha = 1
          c.fillStyle = d.color
          c.fillText(d.text ?? '', a.x, a.y)
          if (sel) c.strokeRect(a.x - 3, a.y - 14, w + 6, 17)
        }
        if (sel) {                                   // handles to drag the end points
          c.setLineDash([])
          for (const p of d.kind === 'hline' || d.kind === 'text' ? [] : [a, b]) {
            if (!p) continue
            c.fillStyle = '#000'; c.fillRect(p.x - 4, p.y - 4, 8, 8)
            c.strokeStyle = d.color; c.strokeRect(p.x - 4, p.y - 4, 8, 8)
          }
        }
      }
      c.restore()
    })
    this.placed = placed.filter(p => p.d !== this.state.draft)
  }
}
