/** ApprovalGate.tsx — v1.0.0 */
import React from 'react';
import type { GateStatus } from '../types';
import styles from '../styles/Dashboard.module.css';

interface Props {
  gateStatus: GateStatus;
  onApprove: () => void;
  onRevise: () => void;
}

const ApprovalGate: React.FC<Props> = ({ gateStatus, onApprove, onRevise }) => {
  if (gateStatus === 'hidden' || gateStatus === 'approved') {
    const isApproved = gateStatus === 'approved';
    return (
      <div className={`${styles.gateBox} ${isApproved ? styles.gateBoxApproved : ''}`}>
        {isApproved ? '✓ Blueprint approved — proceeding to Phase 5' : 'Blueprint approval required after Phase 4'}
      </div>
    );
  }

  return (
    <>
      <div className={`${styles.gateBox} ${styles.gateBoxWaiting}`}>
        ⚠ Blueprint ready — review blueprint.md and blueprint.json, then approve to continue
      </div>
      <div className={styles.gateButtons}>
        <button className={`${styles.btn} ${styles.btnApprove}`} onClick={onApprove}>
          ✓ APPROVED — proceed
        </button>
        <button className={styles.btnRevise} onClick={onRevise}>
          ✎ REVISE
        </button>
      </div>
    </>
  );
};

export default ApprovalGate;
