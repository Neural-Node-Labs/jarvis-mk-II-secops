"""orchestrator.py — v1.0.0 — Async state machine iterating phases 0-9"""

import asyncio, logging
from typing import AsyncGenerator
from .session import EvolutionSession, SessionStatus, PhaseStatus
from .phase_runners import run_phase, PhaseResult

logger = logging.getLogger("evolution_orchestrator")

class EvolutionOrchestrator:
    """Drives an EvolutionSession through all 10 phases asynchronously."""

    def __init__(self, session: EvolutionSession, registry):
        self.session = session
        self.registry = registry
        self._abort = asyncio.Event()
        self._approval = asyncio.Event()

    def abort(self) -> None:
        self._abort.set()

    def approve(self) -> None:
        self._approval.set()

    async def run(self) -> AsyncGenerator[dict, None]:
        session = self.session
        session.status = SessionStatus.RUNNING
        current = 0

        while current <= 9:
            if self._abort.is_set():
                session.add_log("[ORCHESTRATOR] ABORT received — halting evolution", "warn")
                session.status = SessionStatus.ABORTED
                session.verdict = "fail"
                yield session.to_dict()
                return

            session.current_phase = current
            logger.info(f"Starting phase {current}")

            try:
                result: PhaseResult = await run_phase(current, session, self.registry)
            except Exception as e:
                session.add_log(f"[ORCHESTRATOR] Phase {current} crashed: {str(e)}", "error")
                yield session.to_dict()
                return

            if not result.success:
                session.add_log(f"[ORCHESTRATOR] Phase {current} FAILED: {result.error}", "error")
                session.verdict = "fail"
                yield session.to_dict()
                return

            yield session.to_dict()

            if result.next_phase is None:
                if current == 4:
                    logger.info("Awaiting blueprint approval…")
                    yield session.to_dict()
                    while not self._approval.is_set() and not self._abort.is_set():
                        await asyncio.sleep(0.1)
                    if self._abort.is_set():
                        session.add_log("[ORCHESTRATOR] ABORT received during approval", "warn")
                        session.status = SessionStatus.ABORTED
                        session.verdict = "fail"
                        yield session.to_dict()
                        return
                    session.approved = True
                    session.add_log("[PHASE-4] BLUEPRINT_APPROVED  by=human", "success")
                    session.status = SessionStatus.APPROVED
                    session.set_phase_status(4, PhaseStatus.DONE)
                    yield session.to_dict()
                    current = 5
                    continue
                elif current == 9:
                    session.status = SessionStatus.COMPLETED
                    yield session.to_dict()
                    return
                else:
                    session.add_log(f"[ORCHESTRATOR] Unexpected halt at phase {current}", "error")
                    yield session.to_dict()
                    return

            current = result.next_phase

        session.status = SessionStatus.COMPLETED
        yield session.to_dict()
