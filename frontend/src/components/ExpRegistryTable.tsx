/** ExpRegistryTable.tsx — v1.0.0 */
import React from 'react';
import type { ExpEntry } from '../types';
import styles from '../styles/Dashboard.module.css';

interface Props { entries: ExpEntry[] }

const ExpRegistryTable: React.FC<Props> = ({ entries }) => (
  <div className={styles.expSection}>
    <div className={styles.expSectionHeader}>
      <span className={styles.panelHeaderIcon} aria-hidden="true">📖</span>
      Experience register (EXP index)
    </div>
    {entries.length === 0 ? (
      <div className={styles.expEmpty}>No experience entries yet</div>
    ) : (
      <table className={styles.expTable}>
        <thead>
          <tr>
            <th>EXP-ID</th>
            <th>Category</th>
            <th>Title</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td className={styles.expId}>{e.id}</td>
              <td className={styles.expCategory}>{e.category}</td>
              <td className={styles.expTitle}>{e.title}</td>
              <td>
                <span
                  className={`${styles.expStatusBadge} ${
                    e.status === 'CONFIRMED' ? styles.expStatusConfirmed : styles.expStatusDraft
                  }`}
                >
                  {e.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    )}
  </div>
);

export default React.memo(ExpRegistryTable);
