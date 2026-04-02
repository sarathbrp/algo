import { DashboardHero } from '@/components/panels/DashboardHero'
import { PortfolioPanel }  from '@/components/panels/PortfolioPanel'
import { PositionsPanel }  from '@/components/panels/PositionsPanel'
import { WatchlistPanel }  from '@/components/panels/WatchlistPanel'
import { RulesPipelinePanel } from '@/components/panels/RulesPipelinePanel'
import { TradeActivityPanel } from '@/components/panels/TradeActivityPanel'
import { GateLogPanel }    from '@/components/panels/GateLogPanel'

export function Dashboard() {
  return (
    <>
      <DashboardHero />
      <div className="dashboard-grid" style={{ gridTemplateColumns: 'minmax(300px, 0.92fr) minmax(0, 1.3fr) minmax(280px, 0.88fr)', gridTemplateRows: 'auto auto' }}>
        <PortfolioPanel />
        <PositionsPanel />
        <WatchlistPanel />
        <RulesPipelinePanel />
        <TradeActivityPanel />
        <GateLogPanel />
      </div>
    </>
  )
}
