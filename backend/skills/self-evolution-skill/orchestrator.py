"""
orchestrator.py — v1.0.0
CHANGELOG: Async state machine iterating phases 0-9 with abort/approval support
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional

from .session import EvolutionSession, SessionStatus, PhaseStatus
from .phase_runners import run_phase, PhaseResult

logger = logging.getLogger("evolution_orchestrator")


class EvolutionOrchestrator:
    """
    Drives an EvolutionSession through all 10 phases asynchronously.
    Yields state snapshots after each phase for WebSocket streaming.
    Supports pause at Phase 4 (approval gate) and external abort.
    """

    def __init__(self, session: EvolutionSession, registry):
        self.session = session
        self.registry = registry
        self._abort = asyncio.Event()
        self._approval = asyncio.Event()
        self._phase_done = asyncio.Event()

    def abort(self) -> None:
        """Signal abort from external caller (e.g., API route)."""
        self._abort.set()
        self._phase_done.set()  # unblock any wait

    def approve(self) -> None:
        """Signal blueprint approval from external caller."""
        self._approval.set()
        self._phase_done.set()

    async def run(self) -> AsyncGenerator[dict, None]:
        """
        Execute phases 0 through 9 sequentially.
        Yields session.to_dict() after each phase completes or state change.
        Halts at Phase 4 until approve() is called.
        """
        session = self.session
        try:
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

                # Run the phase
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

                # Emit state after phase completes
                yield session.to_dict()

                # Handle approval gate (Phase 4 returns next_phase=None)
                if result.next_phase is None:
                    if current == 4:
                        logger.info("Awaiting blueprint approval…")
                        session.add_log("[ORCHESTRATOR] Awaiting human approval — send approve to continue", "warn")
                        yield session.to_dict()

                        # Wait for approval or abort
                        while not self._approval.is_set() and not self._abort.is_set():
                            await asyncio.sleep(0.1)

                        if self._abort.is_set():
                            session.add_log("[ORCHESTRATOR] ABORT received during approval wait", "warn")
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
                        # Evolution complete
                        session.status = SessionStatus.COMPLETED
                        session.verdict = "pass"
                        logger.info("Evolution complete — all phases passed")
                        yield session.to_dict()
                        return

                    else:
                        # Unexpected halt
                        session.add_log(f"[ORCHESTRATOR] Unexpected halt at phase {current}", "error")
                        yield session.to_dict()
                        return

                current = result.next_phase

            # Fallback completion
            if current > 9:
                session.status = SessionStatus.COMPLETED
                yield session.to_dict()

        except Exception as e:
            logger.error(f"Orchestrator crashed: {e}", exc_info=True)
            session.add_log(f"[ORCHESTRATOR] CRASH: {str(e)}", "error")
            session.status = SessionStatus.FAILED
            session.verdict = "fail"
            yield session.to_dict()
