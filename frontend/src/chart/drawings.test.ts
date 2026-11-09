import assert from 'node:assert/strict'
import { test } from 'node:test'
import { hitTest, segDist, shifted, snapPrice, tidy, type Drawing, type Placed } from './drawGeom.ts'

test('distance to a segment', () => {
  assert.equal(segDist({ x: 5, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 }), 3)
  assert.equal(segDist({ x: 15, y: 0 }, { x: 0, y: 0 }, { x: 10, y: 0 }), 5)          // past the end: to the end point
  assert.equal(segDist({ x: 3, y: 4 }, { x: 0, y: 0 }, { x: 0, y: 0 }), 5)            // a zero-length segment
})

test('the magnet snaps to the nearest OHLC within reach, else keeps the pointer price', () => {
  const bar = { open: 10, high: 12, low: 9, close: 11 }
  const toY = (p: number) => 200 - p * 10                                               // 10 px per unit
  assert.equal(snapPrice(11.95, bar, toY, toY(11.95)), 12)
  assert.equal(snapPrice(10.4, bar, toY, toY(10.4)), 10)                                // 4 px from the open, 6 from the close
  assert.equal(snapPrice(10.5, bar, toY, toY(10.5), 4), 10.5)                           // a tight magnet: nothing close enough
  assert.equal(snapPrice(10.5, undefined, toY, 0), 10.5)
})

test('prices are rounded for their size', () => {
  assert.equal(tidy(21.534781), 21.53)
  assert.equal(tidy(1136000.4), 1136000.4)
  assert.equal(tidy(0.123456), 0.1235)
  assert.equal(tidy(5.12345), 5.123)
})

const D = (id: string, kind: Drawing['kind']): Drawing => ({ id, kind, p1: { time: 't', price: 1 }, color: '#fff' })

test('hit testing: handles first, then bodies, newest on top', () => {
  const placed: Placed[] = [
    { d: D('h', 'hline'), a: { x: 0, y: 50 }, b: null, w: 0 },
    { d: D('t', 'trend'), a: { x: 10, y: 10 }, b: { x: 110, y: 110 }, w: 0 },
    { d: D('r', 'rect'), a: { x: 200, y: 20 }, b: { x: 260, y: 80 }, w: 0 },
    { d: D('x', 'text'), a: { x: 300, y: 100 }, b: null, w: 40 },
  ]
  assert.deepEqual(hitTest(placed, { x: 400, y: 52 }), { id: 'h', part: 'body' })     // anywhere along a horizontal line
  assert.deepEqual(hitTest(placed, { x: 60, y: 62 }), { id: 't', part: 'body' })
  assert.deepEqual(hitTest(placed, { x: 109, y: 111 }), { id: 't', part: 'p2' })
  assert.deepEqual(hitTest(placed, { x: 11, y: 9 }), { id: 't', part: 'p1' })
  assert.deepEqual(hitTest(placed, { x: 230, y: 50 }), { id: 'r', part: 'body' })      // inside a rectangle
  assert.deepEqual(hitTest(placed, { x: 320, y: 95 }), { id: 'x', part: 'body' })
  assert.equal(hitTest(placed, { x: 160, y: 300 }), null)
})

test('moving a drawing shifts both points by bars and price', () => {
  const labels = ['a', 'b', 'c', 'd', 'e']
  const d: Drawing = { id: '1', kind: 'trend', p1: { time: 'a', price: 10 }, p2: { time: 'c', price: 12 }, color: '#fff' }
  const m = shifted(d, 2, 0.5, t => labels.indexOf(t), i => labels[i] ?? null)
  assert.deepEqual([m.p1, m.p2], [{ time: 'c', price: 10.5 }, { time: 'e', price: 12.5 }])
  const off = shifted(d, 9, 0, t => labels.indexOf(t), i => labels[i] ?? null)          // past the last bar: the time stays
  assert.equal(off.p2!.time, 'c')
})
