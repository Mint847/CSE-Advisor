// The four one-year quant figures shown as table cells on CANDIDATES and All stocks (same maths as the QNT tab);
// '–' until the figures exist (the next candidate run computes them for the liquid stocks, tracked stocks have them at once).
import type { QuantCols } from '../api'

export default function QuantCells({ q, cls }: { q?: QuantCols | null; cls: string }) {
  return (
    <>
      <td className={cls} title="Volatility over the last year, annualised">{q?.vol == null ? '–' : `${(q.vol * 100).toFixed(0)}%`}</td>
      <td className={cls} title="Return above T-bills per unit of volatility, last year">{q?.sharpe == null ? '–' : q.sharpe.toFixed(2)}</td>
      <td className={cls} title="Beta to the ASPI, last year">{q?.beta == null ? '–' : q.beta.toFixed(2)}</td>
      <td className={cls} title="Biggest fall from a high to a later low in the last year">{q?.mdd == null ? '–' : `${(q.mdd * 100).toFixed(0)}%`}</td>
    </>
  )
}
