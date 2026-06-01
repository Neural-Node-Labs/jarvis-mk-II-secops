/** PhaseItem.tsx — v1.0.0 */
import React from 'react';
import type { PhaseState, PhaseStatusEnum } from '../types';
import { PHASE_COLORS } from '../data/phases';
import styles from '../styles/Dashboard.module.css';

interface Props {
  phase: PhaseState;
  isRunning: boolean;
}

const PhaseItem: React.FC<Props> = ({ phase, isRunning }) => {
  const status: PhaseStatusEnum = phase.status;
  const colors = PHASE_COLORS[status];

  let badgeText = '';
  if (phase.customStatus === 'blocked') badgeText = 'WAITING';
  else if (phase.customStatus === 'fail') badgeText = 'FAIL';
  else if (status === 'running') badgeText = 'RUNNING';
  else if (status === 'done') badgeText = 'DONE';

  const rowClass = [
    styles.phaseRow,
    status === 'running' ? styles.phaseRowRunning : '',
    status === 'done' ? styles.phaseRowDone : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={rowClass}>
      <span
        className={`${styles.phaseDot} ${isRunning ? styles.phaseDotActive : ''}`}
        style={{ background: colors.dot }}
      />
      <span className={styles.phaseIcon} style={{ color: colors.text }} aria-hidden="true">
        ●
      </span>
      <span className={styles.phaseLabel} style={{ color: colors.text }}>
        {phase.definition.label}
      </span>
      {badgeText && (
        <span className={styles.phaseBadge} style={{ cssText: colors.badge }}>
          {badgeText}
        </span>
      )}
    </div>
  );
};

export default React.memo(PhaseItem);
