/** EvolutionLog.tsx — v1.0.0 */
import React, { useEffect, useRef } from 'react';
import type { LogEntry } from '../types';
import styles from '../styles/Dashboard.module.css';

interface Props { entries: LogEntry[] }

const LOG_COLORS: Record<string, string> = {
  info: 'var(--color-text-secondary)',
  success: 'var(--color-text-success)',
  warn: 'var(--color-text-warning)',
  error: 'var(--color-text-danger)',
  muted: 'var(--color-text-tertiary)',
};

const EvolutionLog: React.FC<Props> = ({ entries }) => {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries]);

  return (
    <div className={styles.evoLog} ref={scrollRef}>
      {entries.length === 0 ? (
        <span className={styles.logEmpty}>Waiting for evolution to start…</span>
      ) : (
        entries.map((entry) => (
          <div
            key={entry.id}
            className={styles.logEntry}
            style={{ color: LOG_COLORS[entry.type] || LOG_COLORS.info }}
          >
            [{entry.timestamp}] {entry.message}
          </div>
        ))
      )}
    </div>
  );
};

export default EvolutionLog;
