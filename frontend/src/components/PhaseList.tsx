/**
 * PhaseList.tsx — v1.0.0
 * 10-phase pipeline renderer
 */

import React from 'react';
import type { PhaseState } from '../types';
import PhaseItem from './PhaseItem';
import styles from '../styles/Dashboard.module.css';

interface Props {
  phases: PhaseState[];
  currentPhase: number;
}

const PhaseList: React.FC<Props> = ({ phases, currentPhase }) => (
  <div className={styles.phaseList}>
    {phases.map((phase) => (
      <PhaseItem
        key={phase.definition.id}
        phase={phase}
        isRunning={phase.definition.id === currentPhase && phase.status === 'running'}
      />
    ))}
  </div>
);

export default React.memo(PhaseList);
