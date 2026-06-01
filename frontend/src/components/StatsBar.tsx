/**
 * StatsBar.tsx — v1.0.0
 * 4 KPI cards: Workspace ID, Files copied, Tests passed, EXP entries
 */

import React from 'react';
import styles from '../styles/Dashboard.module.css';

interface Props {
  workspaceId: string;
  filesCopied: string;
  testsPassed: string;
  expCount: string;
}

const StatsBar: React.FC<Props> = ({ workspaceId, filesCopied, testsPassed, expCount }) => (
  <div className={styles.statsGrid}>
    <div className={styles.statCard}>
      <div className={styles.statLabel}>Workspace</div>
      <div className={styles.statValueMono}>{workspaceId}</div>
    </div>
    <div className={styles.statCard}>
      <div className={styles.statLabel}>Files copied</div>
      <div className={styles.statValue}>{filesCopied}</div>
    </div>
    <div className={styles.statCard}>
      <div className={styles.statLabel}>Tests passed</div>
      <div className={styles.statValue}>{testsPassed}</div>
    </div>
    <div className={styles.statCard}>
      <div className={styles.statLabel}>EXP entries</div>
      <div className={styles.statValue}>{expCount}</div>
    </div>
  </div>
);

export default StatsBar;
