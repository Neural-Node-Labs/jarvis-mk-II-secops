/** EvolutionDashboard.tsx — v1.0.0 — Root orchestrator */
import React from 'react';
import { useEvolutionEngine } from '../hooks/useEvolutionEngine';
import DashboardHeader from './DashboardHeader';
import StatsBar from './StatsBar';
import PhaseList from './PhaseList';
import EvolutionLog from './EvolutionLog';
import ApprovalGate from './ApprovalGate';
import VerdictBanner from './VerdictBanner';
import ExpRegistryTable from './ExpRegistryTable';
import styles from '../styles/Dashboard.module.css';

const EvolutionDashboard: React.FC = () => {
  const { state, runEvolution, resetEvolution, approveBlueprint, reviseBlueprint } = useEvolutionEngine();

  return (
    <div className={styles.dashboardContainer}>
      <h2 className={styles.srOnly}>Self-Evolution Skill — CBD v2.2 Orchestration Dashboard</h2>
      <DashboardHeader isRunning={state.running} onRun={runEvolution} onReset={resetEvolution} />
      <StatsBar workspaceId={state.workspaceId} filesCopied={state.filesCopied} testsPassed={state.testsPassed} expCount={state.expCount} />
      <div className={styles.mainGrid}>
        <PhaseList phases={state.phases} currentPhase={state.currentPhase} />
        <div className={styles.rightColumn}>
          <div className={styles.panel}>
            <div className={styles.panelHeader}><span className={styles.panelHeaderIcon} aria-hidden="true">▸</span>Evolution log</div>
            <EvolutionLog entries={state.logs} />
          </div>
          <div className={styles.panel}>
            <div className={styles.panelHeader}><span className={styles.panelHeaderIcon} aria-hidden="true">🛡</span>Approval gate</div>
            <ApprovalGate gateStatus={state.gateStatus} onApprove={approveBlueprint} onRevise={reviseBlueprint} />
          </div>
          <VerdictBanner verdict={state.verdict} />
        </div>
      </div>
      <ExpRegistryTable entries={state.expEntries} />
    </div>
  );
};

export default EvolutionDashboard;
