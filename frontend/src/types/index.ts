/**
 * types/index.ts — v1.0.0
 * CHANGELOG: Initial type definitions for Evolution Dashboard React conversion
 * All interfaces matching the CBD Phase I blueprint IN/OUT schemas
 */

// ---- Phase Definition (static, from data/phases.ts) ----
export interface PhaseDefinition {
  id: number;
  label: string;
  icon: string;          // Tabler icon class name (e.g. "ti-search")
  description: string;   // Full description used in log messages
}

// ---- Phase Runtime Status ----
export type PhaseStatusEnum = 'idle' | 'running' | 'done' | 'blocked' | 'fail';

export interface PhaseState {
  definition: PhaseDefinition;
  status: PhaseStatusEnum;
  customStatus?: 'blocked' | 'fail' | null;  // override for special states like Phase 4 gate
}

// ---- Gate Status ----
export type GateStatus = 'hidden' | 'waiting' | 'approved' | 'revised';

// ---- Verdict ----
export type Verdict = 'pass' | 'fail' | null;

// ---- Log Entry ----
export type LogType = 'info' | 'success' | 'warn' | 'error' | 'muted';

export interface LogEntry {
  id: string;            // unique key (crypto.randomUUID or incremental)
  timestamp: string;     // ISO 8601 UTC
  message: string;
  type: LogType;
}

// ---- EXP Entry ----
export type ExpStatus = 'DRAFT' | 'CONFIRMED';

export interface ExpEntry {
  id: string;            // e.g., "EXP-0001"
  category: string;
  title: string;
  status: ExpStatus;
}

// ---- Evolution Engine State (master state for useReducer) ----
export interface EvolutionState {
  running: boolean;
  currentPhase: number;       // -1 when idle, 0-9 during run, 10 when complete
  paused: boolean;
  approved: boolean;
  aborted: boolean;
  workspaceId: string;
  filesCopied: string;
  testsPassed: string;
  expCount: string;
  verdict: Verdict;
  phases: PhaseState[];
  gateStatus: GateStatus;
  logs: LogEntry[];
  expEntries: ExpEntry[];
}

// ---- Reducer Actions ----
export type DashboardAction =
  | { type: 'START_EVOLUTION' }
  | { type: 'RESET_ALL' }
  | { type: 'SET_PHASE'; payload: number }
  | { type: 'APPROVE_BLUEPRINT' }
  | { type: 'REVISE_BLUEPRINT' }
  | { type: 'ADD_LOG'; payload: { message: string; type: LogType } }
  | { type: 'UPDATE_STATS'; payload: { filesCopied?: string; testsPassed?: string; expCount?: string; workspaceId?: string } }
  | { type: 'ADD_EXP_ENTRY'; payload: ExpEntry }
  | { type: 'SET_VERDICT'; payload: 'pass' | 'fail' }
  | { type: 'EVOLUTION_COMPLETE' }
  | { type: 'ABORT' };

// ---- Phase Colors Map (for visual rendering) ----
export interface PhaseColorInfo {
  dot: string;
  badge: string;
  text: string;
}

export type PhaseColorMap = Record<PhaseStatusEnum, PhaseColorInfo>;
