/** DashboardHeader.tsx — v1.0.0 */
import React from 'react';
import styles from '../styles/Dashboard.module.css';

interface Props { isRunning: boolean; onRun: () => void; onReset: () => void; }

const DashboardHeader: React.FC<Props> = ({ isRunning, onRun, onReset }) => (
  <div className={styles.headerRow}>
    <div className={styles.headerMeta}>
      <div className={styles.headerLabel}>Self-Evolution Skill</div>
      <div className={styles.headerTitle}>CBD v2.2 — Evolution Orchestrator</div>
    </div>
    <div className={styles.headerActions}>
      <button className={styles.btn} onClick={onRun} disabled={isRunning}>
        ▶ Run Evolution
      </button>
      <button className={styles.btn} onClick={onReset}>
        ↻ Reset
      </button>
    </div>
  </div>
);

export default DashboardHeader;
