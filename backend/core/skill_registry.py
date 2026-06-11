"""
Skill Registry — Discovers, registers, and dispatches all agent skills.
No Pydantic. SkillResult is a plain dataclass.

version: 2.1.0
changelog:
  1.0.0 - Initial registry with dynamic skill loading
  2.0.0 - No Pydantic. SkillResult dataclass. JarvisMKII skill registration.
  2.1.0 - Added swarm and sentinel skill registration.
"""
import asyncio
import logging
import traceback
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("skill_registry")


@dataclass
class SkillResult:
    success: bool = True
    output: Any = None
    error: Optional[str] = None
    requires_confirm: bool = False
    confirm_prompt: str = ""

    def to_dict(self) -> dict:
        return {"success": self.success, "output": self.output, "error": self.error, "requires_confirm": self.requires_confirm, "confirm_prompt": self.confirm_prompt}

    @staticmethod
    def ok(output: Any = None) -> "SkillResult":
        return SkillResult(success=True, output=output)

    @staticmethod
    def fail(error: str) -> "SkillResult":
        return SkillResult(success=False, error=error)

    @staticmethod
    def confirm(prompt: str, output: Any = None) -> "SkillResult":
        return SkillResult(success=True, output=output, requires_confirm=True, confirm_prompt=prompt)


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Any] = {}
        self._loaded: bool = False

    def register(self, name: str, handler) -> None:
        self._skills[name] = handler
        logger.info("[skill_registered] name=%s", name)

    def list_skills(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._skills.keys())

    def _ensure_loaded(self):
        if not self._loaded:
            self._loaded = True
            self._load_builtin_skills()

    def _load_builtin_skills(self):
        loaders = [
            ("filesystem", self._load_filesystem),
            ("os_execution", self._load_os_execution),
            ("file_streamer", self._load_file_streamer),
            ("folder_reader", self._load_folder_reader),
            ("memory_manager", self._load_memory_manager),
            ("cbd_architect", self._load_cbd_architect),
            ("jarvis_mkii", self._load_jarvis_mkii),
            ("swarm", self._load_swarm),
            ("sentinel", self._load_sentinel),
        ]
        for name, loader in loaders:
            try:
                handler = loader()
                if handler is not None:
                    self.register(name, handler)
            except Exception as exc:
                logger.warning("[skill_load_failed] name=%s err=%s", name, exc)

    def _load_filesystem(self):
        from skills.filesystem import FilesystemSkill
        return FilesystemSkill()

    def _load_os_execution(self):
        from skills.os_execution import OsExecutionSkill
        return OsExecutionSkill()

    def _load_file_streamer(self):
        from skills.file_streamer import FileStreamerSkill
        return FileStreamerSkill()

    def _load_folder_reader(self):
        from skills.folder_reader import FolderReaderSkill
        return FolderReaderSkill()

    def _load_memory_manager(self):
        from skills.memory_manager_skill import MemoryManagerSkill
        return MemoryManagerSkill()

    def _load_cbd_architect(self):
        from skills.cbd_architect import CbdArchitectSkill
        return CbdArchitectSkill()

    def _load_jarvis_mkii(self):
        from jarvis_mkii_skill import JarvisMKIISkill
        return JarvisMKIISkill()

    def _load_swarm(self):
        from skills.swarm_skill import SwarmSkill
        return SwarmSkill()

    def _load_sentinel(self):
        from skills.sentinel_skill import SentinelSkill
        return SentinelSkill()

    async def execute(self, skill_name: str, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        self._ensure_loaded()
        handler = self._skills.get(skill_name)
        if handler is None:
            known = ", ".join(self.list_skills())
            return SkillResult.fail(f"SKILL_NOT_FOUND: '{skill_name}'. Available: {known}")
        try:
            if hasattr(handler, "execute"):
                result = await handler.execute(action, params, confirmed)
            elif callable(handler):
                result = await handler(action, params, confirmed)
            else:
                return SkillResult.fail(f"SKILL_INVALID_HANDLER: '{skill_name}'")
            if not isinstance(result, SkillResult):
                result = SkillResult.ok(result)
            return result
        except Exception as exc:
            logger.error("[skill_dispatch_error] skill=%s action=%s err=%s\n%s", skill_name, action, exc, traceback.format_exc())
            return SkillResult.fail(f"SKILL_EXEC_ERROR: {type(exc).__name__}: {exc}")
