/**
 * useEvolutionEngine.ts — v2.0.0
 * CHANGELOG: Replaced simulation with real backend API calls
 */

import { useReducer, useCallback, useRef, useEffect } from 'react';
import type { EvolutionState, DashboardAction, PhaseStatusEnum, LogType } from '../types';
import { PHASES, createInitialPhases } from '../data/phases';

const API_BASE = '/api/evolution';
const WS_URL = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/evolution`;

function reducer(s: EvolutionState, a: DashboardAction): EvolutionState {
  switch (a.type) {
    case 'START_EVOLUTION':
      return { ...s, running: true, currentPhase: -1, paused: false, approved: false, aborted: false,
        workspaceId: '\u2014', filesCopied: '\u2014', testsPassed: '\u2014', expCount: '\u2014', verdict: null,
        phases: createInitialPhases(), gateStatus: 'hidden', logs: [] };
    case 'RESET_ALL':
      return { running: false, currentPhase: -1, paused: false, approved: false, aborted: false,
        workspaceId: '\u2014', filesCopied: '\u2014', testsPassed: '\u2014', expCount: '\u2014', verdict: null,
        phases: createInitialPhases(), gateStatus: 'hidden', logs: [], expEntries: [] };
    case 'SYNC_STATE':
      return { ...s, ...(a.payload as Partial<EvolutionState>) };
    default:
      return s;
  }
}

const initState = (): EvolutionState => ({
  running: false, currentPhase: -1, paused: false, approved: false, aborted: false,
  workspaceId: '\u2014', filesCopied: '\u2014', testsPassed: '\u2014', expCount: '\u2014', verdict: null,
  phases: createInitialPhases(), gateStatus: 'hidden', logs: [], expEntries: [],
});

function mapBackendState(bs: Record<string, any>): Partial<EvolutionState> {
  return {
    running: bs.running ?? false,
    currentPhase: bs.currentPhase ?? -1,
    paused: bs.paused ?? false,
    approved: bs.approved ?? false,
    aborted: bs.aborted ?? false,
    workspaceId: bs.workspaceId ?? '\u2014',
    filesCopied: bs.filesCopied ?? '\u2014',
    testsPassed: bs.testsPassed ?? '\u2014',
    expCount: bs.expCount ?? '\u2014',
    verdict: bs.verdict ?? null,
    phases: (bs.phases || []).map((p: any) => ({
      definition: PHASES.find(ph => ph.id === (p.id ?? p.phase_id)) ?? PHASES[0],
      status: p.status as PhaseStatusEnum,
      customStatus: p.customStatus ?? null,
    })),
    gateStatus: bs.gateStatus ?? 'hidden',
    logs: (bs.logs || []).map((l: any) => ({
      id: l.id ?? '', timestamp: l.timestamp ?? '', message: l.message ?? '', type: l.type as LogType,
    })),
    expEntries: (bs.expEntries || []).map((e: any) => ({
      id: e.id ?? '', category: e.category ?? '', title: e.title ?? '', status: e.status ?? 'DRAFT',
    })),
  };
}

export interface EvolutionEngineAPI {
  state: EvolutionState;
  dispatch: React.Dispatch<DashboardAction>;
  runEvolution: () => void;
  resetEvolution: () => void;
  approveBlueprint: () => void;
  reviseBlueprint: () => void;
  workspaceId: string;
}

export function useEvolutionEngine(): EvolutionEngineAPI {
  const [state, dispatch] = useReducer(reducer, undefined, initState);
  const wsIdRef = useRef<string>('');
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const connWS = useCallback((id: string) => {
    if (wsRef.current) wsRef.current.close();
    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ type: 'subscribe', workspace_id: id }));
      ws.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data);
          if (m.type === 'evolution_state' || m.type === 'evolution_complete')
            dispatch({ type: 'SYNC_STATE', payload: mapBackendState(m.data) });
        } catch {}
      };
    } catch {}
  }, []);

  const poll = useCallback((id: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API_BASE}/status?workspace_id=${id}`);
        if (r.ok) {
          const d = await r.json();
          if (d.state) dispatch({ type: 'SYNC_STATE', payload: mapBackendState(d.state) });
        }
      } catch {}
    }, 500);
  }, []);

  useEffect(() => () => {
    if (wsRef.current) wsRef.current.close();
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  const runEvolution = useCallback(async () => {
    dispatch({ type: 'START_EVOLUTION' });
    try {
      const r = await fetch(`${API_BASE}/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target_skill: '', task_description: '' }) });
      if (r.ok) {
        const d = await r.json();
        wsIdRef.current = d.workspace_id;
        connWS(d.workspace_id);
        poll(d.workspace_id);
      }
    } catch (e) { console.error(e); }
  }, [connWS, poll]);

  const resetEvolution = useCallback(async () => {
    try { await fetch(`${API_BASE}/reset`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workspace_id: wsIdRef.current }) }); } catch {}
    if (wsRef.current) wsRef.current.close();
    if (pollRef.current) clearInterval(pollRef.current);
    dispatch({ type: 'RESET_ALL' });
  }, []);

  const approveBlueprint = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/approve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workspace_id: wsIdRef.current }) });
      if (r.ok) { const d = await r.json(); if (d.state) dispatch({ type: 'SYNC_STATE', payload: mapBackendState(d.state) }); }
    } catch (e) { console.error(e); }
  }, []);

  const reviseBlueprint = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/revise`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workspace_id: wsIdRef.current }) });
      if (r.ok) { const d = await r.json(); if (d.state) dispatch({ type: 'SYNC_STATE', payload: mapBackendState(d.state) }); }
    } catch (e) { console.error(e); }
  }, []);

  return { state, dispatch, runEvolution, resetEvolution, approveBlueprint, reviseBlueprint, workspaceId: wsIdRef.current };
}
