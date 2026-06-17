"""
Skill Registry — Discovers, registers, and dispatches all agent skills.
No Pydantic. SkillResult is a plain dataclass.

version: 2.2.0
changelog:
  1.0.0 - Initial registry with dynamic skill loading
  2.0.0 - No Pydantic. SkillResult dataclass. JarvisMKII skill registration.
  2.1.0 - Added swarm and sentinel skill registration.
  2.2.0 - Added scheduler skill. Added dynamic skill discovery
          (_discover_dynamic_skills / register_module / reload) so newly
          evolved skills.*.py files become usable without a hand-written
          loader entry or a container restart.
"""
import asyncio
import importlib
import inspect
import logging
import pkgutil
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("skill_registry")

# Dotted module paths already wired up explicitly in _load_builtin_skills().
# Dynamic discovery skips these so a skill never gets registered twice under
# two different names (once statically, once via the generic name-derivation
# rule in _camel_to_snake()).
_STATIC_SKILL_MODULES = {
    "skills.filesystem",
    "skills.os_execution",
    "skills.file_streamer",
    "skills.folder_reader",
    "skills.memory_manager_skill",
    "skills.cbd_architect",
    "jarvis_mkii_skill",
    "skills.swarm_skill",
    "skills.sentinel_skill",
    "skills.scheduler_skill",
    "skills.selenium_test_skill",
    "skills.skill_creator_skill",
    "skills.unix_tools_skill",
}


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
            ("scheduler", self._load_scheduler),

            ("selenium_test_skill", self._load_selenium_test_skill),
            ("skill_creator", self._load_skill_creator),
            ("unix_tools_skill", self._load_unix_tools_skill),
        ]
        for name, loader in loaders:
            try:
                handler = loader()
                if handler is not None:
                    self.register(name, handler)
            except Exception as exc:
                logger.warning("[skill_load_failed] name=%s err=%s", name, exc)

        # ── Dynamic discovery ───────────────────────────────────────────────
        # Picks up any skill module dropped into skills/ that ISN'T in the
        # static loaders list above (e.g. a module written by the
        # Self-Evolution pipeline). This is what lets "evolve" add a brand
        # new capability instead of only being able to modify a skill that
        # was already hand-wired here. See _discover_dynamic_skills().
        try:
            added = self._discover_dynamic_skills()
            if added:
                logger.info("[skills_dynamically_discovered] names=%s", ", ".join(added))
        except Exception as exc:
            logger.warning("[skill_discovery_failed] err=%s", exc)

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

    def _load_scheduler(self):
        """Loads the scheduler skill — CRUD + due-task tracking for scheduled
        AI calls and process/python executions. Actual execution of due
        tasks is driven by the background loop in main.py (_scheduler_loop),
        which has access to the Agent sessions; this skill only owns the
        task/run storage so it stays a plain, dependency-free skill like
        the others in this registry."""
        from skills.scheduler_skill import SchedulerSkill
        return SchedulerSkill()

    def _load_selenium_test_skill(self):
        """Loads the selenium test orchestration and scaffolding skill."""
        # Using a unified folder/package runner matching your structural pattern
        from skills.selenium_test_skill import SeleniumTestSkill
        return SeleniumTestSkill()

    def _load_skill_creator(self):
        """Loads the skill creator package containing evaluators and analytical agents."""
        from skills.skill_creator_skill import SkillCreatorSkill
        return SkillCreatorSkill()

    def _load_unix_tools_skill(self):
        """Loads the specialized native unix shell utilities wrapper skill."""
        from skills.unix_tools_skill import UnixToolsSkill
        return UnixToolsSkill()

    # ── Dynamic skill discovery / hot registration ──────────────────────────
    # Why this exists: previously, the ONLY way a skill became usable was a
    # hand-written entry in the `loaders` list above + a container restart.
    # That meant the Self-Evolution pipeline (POST /api/evolve) could write a
    # brand-new skills/*.py file to disk, but it could never actually be
    # *used* — the registry had no way to find it, so "evolve" only ever
    # appeared to work for editing a skill that was already wired in here.
    # These methods close that gap: skills are discovered by convention
    # (any class ending in "Skill" with an `execute` method, defined in a
    # module under the skills/ package) and can be registered either at
    # startup automatically, or on-demand via reload() / register_module()
    # without restarting the process.

    @staticmethod
    def _camel_to_snake(name: str) -> str:
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
        return s2.lower()

    def _derive_skill_name(self, class_name: str) -> str:
        """'SchedulerSkill' -> 'scheduler'. 'JarvisMKIISkill' -> 'jarvis_mkii'."""
        snake = self._camel_to_snake(class_name)
        if snake.endswith("_skill"):
            snake = snake[: -len("_skill")]
        return snake or snake

    def _skill_classes_in_module(self, module) -> list[type]:
        """Classes DEFINED in this module (not imported) whose name ends in
        'Skill' and that look like a skill (duck-typed: has `execute`)."""
        found = []
        for attr_name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and obj.__module__ == module.__name__
                and attr_name.endswith("Skill")
                and hasattr(obj, "execute")
            ):
                found.append(obj)
        return found

    def _discover_dynamic_skills(self) -> list[str]:
        """Scan the `skills` package for modules not in _STATIC_SKILL_MODULES
        and register any skill classes found inside. Returns the list of
        newly-registered skill names. Safe to call repeatedly — already
        registered names are left untouched."""
        added: list[str] = []
        try:
            import skills as skills_pkg
        except ImportError:
            logger.debug("[skill_discovery] no 'skills' package on path — skipping")
            return added

        for modinfo in pkgutil.iter_modules(skills_pkg.__path__, prefix="skills."):
            dotted = modinfo.name
            if dotted in _STATIC_SKILL_MODULES:
                continue
            try:
                module = importlib.import_module(dotted)
                for cls in self._skill_classes_in_module(module):
                    name = self._derive_skill_name(cls.__name__)
                    if name in self._skills:
                        continue
                    instance = cls()
                    self.register(name, instance)
                    added.append(name)
                    logger.info("[skill_dynamically_registered] name=%s class=%s module=%s",
                                name, cls.__name__, dotted)
            except Exception as exc:
                logger.warning("[skill_discovery_module_failed] module=%s err=%s", dotted, exc)
        return added

    def register_module(self, module_path: str, class_name: Optional[str] = None,
                         name: Optional[str] = None) -> dict:
        """Explicitly import `module_path` (e.g. 'skills.scheduler_skill') and
        register the skill class found inside it. Intended for the
        Self-Evolution pipeline's deploy phase to call right after writing a
        new skill file, so the new capability is live without a restart.

        If class_name is omitted, the first class ending in 'Skill' with an
        `execute` method is used. If name is omitted, it's derived from the
        class name (see _derive_skill_name)."""
        try:
            module = importlib.import_module(module_path)
            importlib.reload(module)  # pick up the freshly-written file, not a stale cached one
        except Exception as exc:
            return {"success": False, "error": f"import_failed: {exc}"}

        classes = self._skill_classes_in_module(module)
        if class_name:
            classes = [c for c in classes if c.__name__ == class_name]
        if not classes:
            return {"success": False, "error": f"no skill class found in {module_path}"}

        cls = classes[0]
        resolved_name = name or self._derive_skill_name(cls.__name__)
        try:
            self.register(resolved_name, cls())
        except Exception as exc:
            return {"success": False, "error": f"instantiate_failed: {exc}"}
        return {"success": True, "name": resolved_name, "class": cls.__name__, "module": module_path}

    def reload(self) -> dict:
        """Re-run dynamic discovery now (no restart needed). Returns the
        skills added by this call plus the full current skill list."""
        self._ensure_loaded()
        added = self._discover_dynamic_skills()
        return {"added": added, "skills": self.list_skills()}


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
