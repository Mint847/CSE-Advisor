// QLAB: Quant Lab. Tools built on Goldman Sachs' open-source gs-quant (the offline parts) and the app's own stored prices:
// a strategy backtester, event studies, a correlation explorer, a basket builder, and the map of what became of gs-quant.
// Measurements only; nothing here changes an engine's verdict.
import { useSearchParams } from 'react-router-dom'
import LabBacktest from '../components/LabBacktest'
import LabBaskets from '../components/LabBaskets'
import LabCorrelation from '../components/LabCorrelation'
import LabCoverage from '../components/LabCoverage'
import LabEvents from '../components/LabEvents'

const TABS = [
  { id: 'backtest', label: 'Strategy backtest', C: LabBacktest },
  { id: 'events', label: 'Event studies', C: LabEvents },
  { id: 'corr', label: 'Correlations', C: LabCorrelation },
  { id: 'baskets', label: 'Baskets', C: LabBaskets },
  { id: 'about', label: 'gs-quant & glossary', C: LabCoverage },
] as const

export default function QuantLab() {
  const [sp, setSp] = useSearchParams()
  const tab = TABS.find(t => t.id === sp.get('tab')) ?? TABS[0]
  const Body = tab.C
  return (
    <div className="mx-auto max-w-[1700px] space-y-2 px-3 py-3">
      <h1 className="text-[14px] font-semibold uppercase tracking-wider text-amber">QLAB · Quant Lab <span className="font-normal normal-case text-muted">{tab.label.toLowerCase()}</span></h1>
      <div className="flex flex-wrap gap-1 border-b border-line pb-1" role="tablist" aria-label="Quant Lab tools">
        {TABS.map(t => (
          <button key={t.id} role="tab" aria-selected={t.id === tab.id} className={`px-3 py-1 text-[12px] uppercase tracking-wide ${t.id === tab.id ? 'bg-amber text-black' : 'text-muted hover:text-fg'}`}
                  onClick={() => setSp(t.id === 'backtest' ? {} : { tab: t.id }, { replace: true })}>{t.label}</button>
        ))}
      </div>
      <Body />
    </div>
  )
}
