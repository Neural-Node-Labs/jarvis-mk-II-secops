/**
 * data/phases.ts — v1.0.0
 * 10-phase CBD pipeline definitions, delays, color mappings
 */

import type { PhaseDefinition, PhaseStatusEnum, PhaseColorMap, PhaseState } from '../types';

export const PHASES: PhaseDefinition[] = [
  { id: 0, label: 'Experience lookup', icon: 'ti-search', description: 'Phase 0 — consult experienced/index.md' },
  { id: 1, label: 'Workspace bootstrap', icon: 'ti-folder-plus', description: 'Phase 1 — create /tmp/evo/ workspace' },
  { id: 2, label: 'Source discovery', icon: 'ti-files', description: 'Phase 2 — copy files + read versions' },
  { id: 3, label: 'Code analysis', icon: 'ti-code', description: 'Phase 3 — write analysis.md report' },
  { id: 4, label: 'Blueprint generation', icon: 'ti-layout', description: 'Phase 4 — blueprint.md + blueprint.json' },
  { id: 5, label: 'Implementation', icon: 'ti-hammer', description: 'Phase 5 — apply changes, version files' },
  { id: 6, label: 'Build validation', icon: 'ti-circle-check', description: 'Phase 6 — syntax, deps, smoke test' },
  { id: 7, label: 'UI & API testing', icon: 'ti-test-pipe', description: 'Phase 7 — generate + run test suite' },
  { id: 8, label: 'Deploy & verify', icon: 'ti-upload', description: 'Phase 8 — copy back + hash verify' },
  { id: 9, label: 'Restart & health', icon: 'ti-heart-rate-monitor', description: 'Phase 9 — restart + health check + EXP' },
];

export const PHASE_DELAYS: number[] = [600, 900, 1100, 1400, 1700, 0, 1200, 1400, 1100, 1300];

export const PHASE_COLORS: PhaseColorMap = {
  idle:    { dot: 'var(--color-background-tertiary,#cbd5e1)', badge: '', text: 'var(--color-text-tertiary,#94a3b8)' },
  running: { dot: 'var(--color-text-info,#3b82f6)', badge: 'background:var(--color-background-info,#dbeafe);color:var(--color-text-info,#1d4ed8)', text: 'var(--color-text-primary,#0f172a)' },
  done:    { dot: 'var(--color-text-success,#22c55e)', badge: 'background:var(--color-background-success,#dcfce7);color:var(--color-text-success,#15803d)', text: 'var(--color-text-primary,#0f172a)' },
  blocked: { dot: 'var(--color-text-warning,#f59e0b)', badge: 'background:var(--color-background-warning,#fef3c7);color:var(--color-text-warning,#b45309)', text: 'var(--color-text-primary,#0f172a)' },
  fail:    { dot: 'var(--color-text-danger,#ef4444)', badge: 'background:var(--color-background-danger,#fee2e2);color:var(--color-text-danger,#b91c1c)', text: 'var(--color-text-primary,#0f172a)' },
};

export function createInitialPhases(): PhaseState[] {
  return PHASES.map((def) => ({
    definition: def,
    status: 'idle' as PhaseStatusEnum,
    customStatus: null,
  }));
}
