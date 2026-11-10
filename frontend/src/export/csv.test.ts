import assert from 'node:assert/strict'
import { test } from 'node:test'
import { csvCell, csvFileName, toCsv } from './csv.ts'

test('plain values are written as they are; empty and non-finite become empty cells', () => {
  assert.equal(csvCell('JKH'), 'JKH')
  assert.equal(csvCell(12.5), '12.5')
  assert.equal(csvCell(-0.25), '-0.25')
  assert.equal(csvCell(true), 'true')
  assert.equal(csvCell(null), '')
  assert.equal(csvCell(undefined), '')
  assert.equal(csvCell(NaN), '')
  assert.equal(csvCell(Infinity), '')
})

test('commas, quotes and line breaks are quoted and the quotes doubled', () => {
  assert.equal(csvCell('John Keells, Holdings'), '"John Keells, Holdings"')
  assert.equal(csvCell('said "buy"'), '"said ""buy"""')
  assert.equal(csvCell('line one\nline two'), '"line one\nline two"')
})

test('a text cell that a spreadsheet would run as a formula gets an apostrophe; numbers and numeric text do not', () => {
  assert.equal(csvCell('=HYPERLINK("http://x")'), `"'=HYPERLINK(""http://x"")"`)
  assert.equal(csvCell('+94771234567x'), "'+94771234567x")
  assert.equal(csvCell('@SUM(A1)'), "'@SUM(A1)")
  assert.equal(csvCell('-cmd'), "'-cmd")
  assert.equal(csvCell('-5'), '-5')
  assert.equal(csvCell(-5), '-5')
})

test('a table becomes a header row and one line per row, CRLF separated', () => {
  const rows = [{ s: 'JKH', q: 500, n: 'first, buy' }, { s: 'COMB', q: 300, n: null }]
  const csv = toCsv([{ header: 'Stock', get: r => r.s }, { header: 'Shares', get: r => r.q }, { header: 'Notes', get: r => r.n }], rows)
  assert.equal(csv, 'Stock,Shares,Notes\r\nJKH,500,"first, buy"\r\nCOMB,300,\r\n')
})

test('the file name is a safe slug with the date', () => {
  assert.equal(csvFileName('Open positions!', new Date('2026-11-10T09:00:00Z')), 'cse-advisor-open-positions-2026-11-10.csv')
  assert.equal(csvFileName('???', new Date('2026-11-10T09:00:00Z')), 'cse-advisor-export-2026-11-10.csv')
})
