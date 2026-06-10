"""
Skill Registry — Discovers, registers, and dispatches all agent skills.
No Pydantic. SkillResult is a plain dataclass.

version: 2.0.0
changelog:
  1.0.0 - Initial registry with dynamic skill loading
  2.0.0 - No Pydantic. SkillResult dataclass. JarvisMKII skill registration.
          list_skills() introspection. Safe fallback on unknown skill.
          async-native dispatch with unified error schema.
"""
import asyncio
import logging
import traceback
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("skill_registry")


# ── SkillResult — universal return type ───────────────────────────────────────

@dataclass
class SkillResult:
    """
    Unified result returned by every skill action.

    success:          True if the action completed without error
    output:           The action's result payload (any JSON-serialisable type)
    error:            Error message string (only set on failure)
    requires_confirm: True → agent must pause and ask user before proceeding
    confirm_prompt:   Human-readable description of what will happen on confirm
    """
    success:          bool         = True
    output:           Any          = None
    error:            Optional[str] = None
    requires_confirm: bool         = False
    confirm_prompt:   str          = ""

    def to_dict(self) -> dict:
        return {
            "success":          self.success,
            "output":           self.output,
            "error":            self.error,
            "requires_confirm": self.requires_confirm,
            "confirm_prompt":   self.confirm_prompt,
        }

    @staticmethod
    def ok(output: Any = None) -> "SkillResult":
        return SkillResult(success=True, output=output)

    @staticmethod
    def fail(error: str) -> "SkillResult":
        return SkillResult(success=False, error=error)

    @staticmethod
    def confirm(prompt: str, output: Any = None) -> "SkillResult":
        return SkillResult(
            success=True, output=output,
            requires_confirm=True, confirm_prompt=prompt,
        )


# ── SkillRegistry ──────────────────────────────────────────────────────────────

class SkillRegistry:
    """
    Central dispatcher for all agent skills.

    Skills are registered via register(name, handler).
    The handler is either:
      - An async callable: async def handler(action, params, confirmed) -> SkillResult
      - An object with: async def execute(action, params, confirmed) -> SkillResult

    Built-in skills are loaded lazily on first call to avoid circular imports.
    """

    def __init__(self):
        self._skills: dict[str, Any] = {}
        self._loaded: bool = False

    def register(self, name: str, handler) -> None:
        """Register a skill handler under a given name."""
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
        """Lazily load and register all built-in skills."""
        loaders = [
            ("filesystem",    self._load_filesystem),
            ("os_execution",  self._load_os_execution),
            ("file_streamer", self._load_file_streamer),
            ("folder_reader", self._load_folder_reader),
            ("memory_manager",self._load_memory_manager),
            ("cbd_architect", self._load_cbd_architect),
            ("jarvis_mkii",   self._load_jarvis_mkii),
        ]
        for name, loader in loaders:
            try:
                handler = loader()
                if handler is not None:
                    self.register(name, handler)
            except Exception as exc:
                logger.warning("[skill_load_failed] name=%s err=%s", name, exc)

    # ── Skill loaders ──────────────────────────────────────────────────────────

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

    # ── Dispatch ───────────────────────────────────────────────────────────────

    async def execute(
        self,
        skill_name: str,
        action:     str,
        params:     dict,
        confirmed:  bool = False,
    ) -> SkillResult:
        """
        Dispatch a skill action. Returns SkillResult always — never raises.

        Trace points:
          skill_dispatch_start, skill_dispatch_ok, skill_dispatch_fail,
          skill_not_found, skill_dispatch_error
        """
        self._ensure_loaded()

        logger.info("[skill_dispatch_start] skill=%s action=%s confirmed=%s", skill_name, action, confirmed)

        handler = self._skills.get(skill_name)
        if handler is None:
            known = ", ".join(self.list_skills())
            err   = f"SKILL_NOT_FOUND: '{skill_name}'. Available: {known}"
            logger.warning("[skill_not_found] %s", err)
            return SkillResult.fail(err)

        try:
            # Support both object-style and callable-style handlers
            if hasattr(handler, "execute"):
                result = await handler.execute(action, params, confirmed)
            elif callable(handler):
                result = await handler(action, params, confirmed)
            else:
                return SkillResult.fail(f"SKILL_INVALID_HANDLER: '{skill_name}' is not callable")

            # Ensure we always get a SkillResult back
            if not isinstance(result, SkillResult):
                result = SkillResult.ok(result)

            status = "ok" if result.success else "fail"
            logger.info("[skill_dispatch_%s] skill=%s action=%s", status, skill_name, action)
            return result

        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("[skill_dispatch_error] skill=%s action=%s err=%s\n%s", skill_name, action, exc, tb)
            return SkillResult.fail(f"SKILL_EXEC_ERROR: {type(exc).__name__}: {exc}")
