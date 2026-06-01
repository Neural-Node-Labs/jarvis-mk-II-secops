/** VerdictBanner.tsx — v1.0.0 */
import React from 'react';
import type { Verdict } from '../types';
import styles from '../styles/Dashboard.module.css';

interface Props { verdict: Verdict }

const VerdictBanner: React.FC<Props> = ({ verdict }) => {
  if (!verdict) return null;

  const isPass = verdict === 'pass';
  return (
    <div className={`${styles.verdictBox} ${isPass ? styles.verdictPass : styles.verdictFail}`}>
      <span className={styles.verdictIcon} aria-hidden="true">
        {isPass ? '✓' : '✗'}
      </span>
      {isPass
        ? 'Evolution complete — all phases passed. Service restarted and healthy.'
        : 'Evolution aborted — rollback executed. Check log for details.'}
    </div>
  );
};

export default VerdictBanner;
